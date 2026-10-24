"""Approval writes the commitment (story 15a).

`CommitmentRecorder` is built in the composition root with the store, the
meeting records, `display_timezone`, the machine's zone and the clock. It has
two calls:

- **`record(proposal, scope)`**, for one root, after the approval is committed
  in `operational.db` — never inside the proposal's claimed step. It reads the
  ledger and the meeting record, then writes the `pending` line from the live
  approved proposal when the root has none, a `revised` line when this
  proposal is a newer replacement than the newest value line, and otherwise
  nothing. The approval stands whatever happens here: a line that could not
  be written is written by the same `approve` run again, or by the next
  daemon start.
- **`catch_up(...)`**, at daemon start only: every live approved proposal of a
  commitment type whose line is missing gets it, `written=catch_up`. The
  first start after 15a writes the items approved before it; later starts
  write nothing, because of the entry ids.

Written once, by the entry id: each writer reads and folds the ledger, leaves
out every line whose entry is already there, and appends the rest with the
size it read. On `LedgerChanged` it reads again and decides again, at most
three times, then reports `busy` (AD-37).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any, TypeVar

from pm_ai.app.concurrency import WriterBusy, WriterStopped
from pm_ai.app.proposal_reads import open_world
from pm_ai.core.commitment_log import (
    REASONS,
    CommitmentLedger,
    CommitmentLine,
    CommitmentLog,
    Head,
    NoApprovalInstant,
    Pending,
    Reason,
    Recording,
    TooLong,
    from_approved,
    preview_due,
    render_line,
    revised_from,
)
from pm_ai.core.meeting_listing import zone_of
from pm_ai.core.proposals import ProposalTypes
from pm_ai.domain.claims import ClaimReentered
from pm_ai.domain.commitments import COMMITMENTS_LOG, Commitment, LineKind, NotACommitmentScope
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.lifecycle import CommitmentState, ProposalState
from pm_ai.domain.proposals import Proposal, replacement_number, root_of
from pm_ai.domain.storage_tiers import UnprotectedCaptureDir
from pm_ai.platform.environment import machine_zone
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import ArtifactBusy, DecryptionFailed, LedgerChanged
from pm_ai.storage.service import (
    OperationalStoreUnavailable,
    ProposalsUnreadable,
    proposal_bodies_at,
    proposal_index_at,
)

__all__ = [
    "ATTEMPTS",
    "CATCH_UP_STEP",
    "CatchUp",
    "CommitmentCatchUp",
    "CommitmentRecorder",
    "ScopeCatchUp",
    "recorder_for",
]

ATTEMPTS = 3
"""How many times a writer reads the ledger again after `LedgerChanged`, then `busy`."""

CATCH_UP_STEP = "record the approved items as commitments"

_log = logging.getLogger("pm_ai.daemon")

_T = TypeVar("_T")


def _reason(code: str, detail: str | None = None) -> Reason:
    text = REASONS[code]
    return Reason(code, text if detail is None else f"{text}: {detail}")


def _via(proposal: Proposal) -> str:
    return proposal.changed_via or "unknown"


@dataclass
class CommitmentRecorder:
    """The approval's second write, after the first is committed (story 15a)."""

    storage: Any
    """The store this process writes through: the store itself, or a job's handed view."""
    meetings: Any
    """`MeetingRecords` over the same store: the meeting record is read here."""
    types: ProposalTypes
    now: Callable[[], datetime]
    display_zone: tzinfo | None = None
    """`display_timezone`, `None` when unset."""
    machine_zone: tzinfo | None = None
    """The machine's zone, used only when `display_timezone` is unset."""

    def records(self, kind: str) -> bool:
        """Whether approving a proposal of type `kind` records a commitment."""
        return self.types.rule(kind).records_commitment

    @property
    def log(self) -> CommitmentLog:
        return CommitmentLog(self.storage)

    def meeting_record(self, proposal: Proposal, scope: DataScope) -> object | None:
        """The meeting record `proposal` cites, or `None` when it cannot be read."""
        meeting_id = proposal.meeting_id
        if meeting_id is None:
            return None
        try:
            return self.meetings.get(meeting_id, scope=scope)
        except Exception:  # noqa: BLE001 — an unreadable record is no title, start or day
            return None

    def record(
        self, proposal: Proposal, scope: DataScope, *, repair: bool = False
    ) -> Recording | None:
        """The commitment for `proposal`'s root, written if it is missing (story 15a).

        `None` for a type that records no commitment. `repair` is true for an
        approve that found the item already approved: a line it writes is one
        an earlier approval left unwritten.
        """
        if not self.records(proposal.type):
            return None
        root = root_of(proposal.proposal_id)
        record = self.meeting_record(proposal, scope)
        day, day_from, due = preview_due(
            proposal, record, zone=self.display_zone, machine_zone=self.machine_zone
        )

        def answer(
            *,
            recorded: bool,
            reason: Reason | None = None,
            wrote: LineKind | None = None,
            held: Commitment | None = None,
        ) -> Recording:
            return Recording(
                commitment_id=root,
                scope=str(scope),
                state=CommitmentState.PENDING if held is None else held.state,
                recorded=recorded,
                repaired=repair and wrote is not None,
                reason=reason,
                day=day if held is None else held.day,
                day_from=day_from if held is None else held.day_from,
                due=due if held is None else held.due,
                wrote=wrote,
            )

        held: Commitment | None = None
        for _ in range(ATTEMPTS):
            try:
                ledger = self.log.read(scope)
            except NotACommitmentScope:
                return answer(recorded=False, reason=_reason("scope_refused", str(scope)))
            except (OSError, DecryptionFailed, UnicodeError) as unreadable:
                return answer(
                    recorded=False,
                    reason=_reason("ledger_unreadable", type(unreadable).__name__),
                )
            held = ledger.commitments.get(root)
            line: CommitmentLine
            after: Commitment | None
            try:
                if held is None:
                    if root in ledger.malformed_roots:
                        return answer(recorded=False, reason=_reason("ledger_malformed"))
                    made = from_approved(
                        proposal,
                        record=record,
                        zone=self.display_zone,
                        machine_zone=self.machine_zone,
                    )
                    line = render_line(Pending(made, via=_via(proposal), written="approval", at=self.now()))
                    after = made
                else:
                    head = ledger.heads[root]
                    if (replacement_number(proposal.proposal_id) or 0) <= head.number:
                        return answer(recorded=True, held=held)
                    revised = revised_from(
                        proposal, held, head, via=_via(proposal), written="approval", at=self.now()
                    )
                    line = render_line(revised)
                    after = _after_revision(held, revised.due)
            except TooLong as too_long:
                return answer(recorded=False, reason=_reason("too_long", str(too_long)), held=held)
            except NoApprovalInstant:
                return answer(recorded=False, reason=_reason("no_approval_time"), held=held)
            if line.entry in ledger.entries:
                return answer(recorded=True, held=held)
            try:
                self.log.append([line], scope=scope, after=ledger.size)
            except LedgerChanged:
                continue
            except NotACommitmentScope:
                return answer(recorded=False, reason=_reason("scope_refused", str(scope)))
            except UnprotectedCaptureDir:
                return answer(recorded=False, reason=_reason("git_not_excluded"), held=held)
            except (ArtifactBusy, WriterBusy, WriterStopped, OperationalStoreUnavailable, ClaimReentered):
                return answer(recorded=False, reason=_reason("busy"), held=held)
            except OSError as failed:
                return answer(
                    recorded=False,
                    reason=_reason("disk", failed.strerror or type(failed).__name__),
                    held=held,
                )
            return answer(recorded=True, wrote=line.kind, held=after)
        return answer(recorded=False, reason=_reason("busy", "the ledger kept changing"), held=held)


def _after_revision(held: Commitment, due: Any) -> Commitment:
    return held if due is None else replace(held, due=due)


def recorder_for(
    storage: Any,
    meetings: Any,
    *,
    types: ProposalTypes,
    now: Callable[[], datetime],
    display_timezone: str,
) -> CommitmentRecorder:
    """The recorder the composition root builds, over `storage` (story 15a)."""
    zone = zone_of(display_timezone)
    return CommitmentRecorder(
        storage=storage,
        meetings=meetings,
        types=types,
        now=now,
        display_zone=zone,
        machine_zone=None if zone is not None else machine_zone(),
    )


# ── The catch-up at daemon start ─────────────────────────────────────────────


@dataclass
class ScopeCatchUp:
    """What the catch-up did in one scope, as counts only (story 15a)."""

    scope: str
    written: int = 0
    no_change_time: int = 0
    unreadable: int = 0
    too_long: int = 0
    scope_refused: int = 0
    malformed_root: int = 0
    left_out: int = 0
    """Proposals that changed between the read and the claimed step."""
    unvisited: int = 0
    """Approved rows here, in a scope it could not visit."""
    unvisited_why: str | None = None
    flagged: tuple[str, ...] = ()
    """Roots with a `pending` line whose proposal is not approved in `operational.db`."""
    malformed: int = 0
    orphans: int = 0
    duplicates: int = 0
    conflicts: int = 0
    failed: str | None = None
    """Why this scope's lines could not be appended, when they could not."""


@dataclass
class CatchUp:
    """The whole pass: one entry per scope, and rows no scope could be told for."""

    scopes: dict[str, ScopeCatchUp] = field(default_factory=dict)
    unscoped: int = 0
    unreadable: int = 0

    def of(self, scope: str) -> ScopeCatchUp:
        if scope not in self.scopes:
            self.scopes[scope] = ScopeCatchUp(scope)
        return self.scopes[scope]

    @property
    def written(self) -> int:
        return sum(each.written for each in self.scopes.values())


def _direct(work: Callable[[], _T]) -> _T:
    return work()


def catch_up(
    recorder: CommitmentRecorder,
    *,
    paths: ScopePaths | None,
    step: Callable[[Callable[[], Any]], Any] = _direct,
) -> CatchUp:
    """Write the missing lines of every live approved commitment-type proposal (15a).

    1. For each scope — personal and every enrolled project — the ledger is
       read first.
    2. Then, once, the approved rows; each of a commitment type is placed in
       its scope by its stored scope, else by `ProposalWorld.scope_of`.
    3. A live approved proposal whose root has no `pending` line gets one; one
       newer than the newest value line gets a `revised` line.
    4. Each scope's lines go in one append, handed to `step` (the writer
       thread in the daemon); inside it, every proposal is checked to be still
       approved at the version read, and any that is not is left out.

    `recorder.storage` is the unhanded store: its reads run where this runs,
    and its appends only inside `step`.
    """
    now = recorder.now()
    world = open_world(paths, now)
    result = CatchUp()
    log = recorder.log
    ledgers: dict[str, CommitmentLedger | str] = {}
    for scope in world.scopes:
        try:
            ledgers[str(scope)] = log.read(scope)
        except (OSError, DecryptionFailed, UnicodeError, NotACommitmentScope) as unreadable:
            ledgers[str(scope)] = f"its ledger could not be read ({type(unreadable).__name__})"
    try:
        rows = proposal_index_at(world.store, states=[ProposalState.APPROVED.value])
    except ProposalsUnreadable as unreadable:
        _log.warning(
            "the commitment catch-up could not read the approved proposals (%s); nothing was written",
            type(unreadable).__name__,
        )
        return result
    placed: dict[str, list[tuple[str, int]]] = {}
    approved_roots: set[str] = set()
    for row in rows:
        if row.type is None:
            # Unreadable, so its type cannot be told; its root is still live, and
            # a `pending` line for it is not "store lost".
            result.unreadable += 1
            approved_roots.add(root_of(row.proposal_id))
            continue
        if not recorder.records(row.type):
            continue
        approved_roots.add(root_of(row.proposal_id))
        found = world.scope_of(row)
        if found is None:
            result.unscoped += 1
            continue
        key = str(found)
        if found.kind not in (ScopeKind.PERSONAL, ScopeKind.PROJECT):
            result.of(key).scope_refused += 1
            continue
        held = ledgers.get(key)
        if held is None or isinstance(held, str):
            counts = result.of(key)
            counts.unvisited += 1
            counts.unvisited_why = held if isinstance(held, str) else "it is not enrolled now"
            continue
        placed.setdefault(key, []).append((row.proposal_id, row.version))
    for key, held in ledgers.items():
        if isinstance(held, str):
            continue
        counts = result.of(key)
        counts.flagged = tuple(sorted(root for root in held.commitments if root not in approved_roots))
        candidates = placed.get(key, [])
        if candidates:
            try:
                _catch_up_scope(recorder, DataScope.parse(key), held, candidates, world.store, counts, step)
            except Exception as failed:  # noqa: BLE001 — one scope's failure stops no other
                counts.failed = type(failed).__name__
        else:
            _counted(counts, held)
    for each in result.scopes.values():
        _log.info(
            "commitment catch-up in %s: %d written, %d without an approval instant, %d "
            "unreadable, %d too long, %d refused scope, %d named by a malformed line, %d "
            "changed meanwhile, %d in a scope not visited%s; ledger: %d malformed, %d "
            "orphans, %d duplicates, %d conflicts; %d pending without an approved proposal%s%s",
            each.scope,
            each.written,
            each.no_change_time,
            each.unreadable,
            each.too_long,
            each.scope_refused,
            each.malformed_root,
            each.left_out,
            each.unvisited,
            f" ({each.unvisited_why})" if each.unvisited_why else "",
            each.malformed,
            each.orphans,
            each.duplicates,
            each.conflicts,
            len(each.flagged),
            f": {', '.join(each.flagged)}" if each.flagged else "",
            f"; not appended ({each.failed}), the next start tries again" if each.failed else "",
        )
    if result.unscoped or result.unreadable:
        _log.info(
            "commitment catch-up: %d approved row(s) whose scope cannot be told, %d unreadable",
            result.unscoped,
            result.unreadable,
        )
    return result


def _counted(counts: ScopeCatchUp, ledger: CommitmentLedger) -> None:
    counts.malformed = ledger.malformed
    counts.orphans = ledger.orphans
    counts.duplicates = ledger.duplicates
    counts.conflicts = ledger.conflicts


def _catch_up_scope(
    recorder: CommitmentRecorder,
    scope: DataScope,
    ledger: CommitmentLedger,
    candidates: Sequence[tuple[str, int]],
    store: Path,
    counts: ScopeCatchUp,
    step: Callable[[Callable[[], Any]], Any],
) -> None:
    storage = recorder.storage
    if scope.kind is ScopeKind.PROJECT:
        try:
            storage.assert_writable(scope=scope, artifact=COMMITMENTS_LOG)
        except UnprotectedCaptureDir:
            counts.unvisited += len(candidates)
            counts.unvisited_why = "git does not exclude the project's memory/"
            _counted(counts, ledger)
            return
    versions = dict(candidates)
    for _ in range(ATTEMPTS):
        try:
            bodies = proposal_bodies_at(store, list(versions))
        except ProposalsUnreadable:
            counts.unreadable += len(versions)
            counts.failed = "the approved proposals' bodies could not be read"
            return
        lines, sources, tallies = _lines_for(recorder, scope, ledger, bodies, versions)
        counts.no_change_time, counts.too_long, counts.malformed_root, counts.unreadable = tallies
        _counted(counts, ledger)
        if not lines:
            return

        def work(lines: list[CommitmentLine] = lines, sources: list[tuple[str, int]] = sources) -> int:
            kept: list[CommitmentLine] = []
            for line, (proposal_id, version) in zip(lines, sources, strict=True):
                try:
                    now_row = storage.load_proposal(proposal_id)
                except Exception:  # noqa: BLE001 — gone or unreadable: left out
                    continue
                if now_row.state is ProposalState.APPROVED and now_row.version == version:
                    kept.append(line)
            if kept:
                CommitmentLog(storage).append(kept, scope=scope, after=ledger.size)
            return len(kept)

        try:
            written = step(work)
        except LedgerChanged:
            try:
                ledger = recorder.log.read(scope)
            except (OSError, DecryptionFailed, UnicodeError) as unreadable:
                counts.failed = f"its ledger could not be read again ({type(unreadable).__name__})"
                return
            continue
        except (ArtifactBusy, WriterBusy, WriterStopped, OperationalStoreUnavailable, OSError) as failed:
            counts.failed = type(failed).__name__
            return
        counts.written += int(written)
        counts.left_out += len(lines) - int(written)
        return
    counts.failed = "busy: the ledger kept changing"


def _lines_for(
    recorder: CommitmentRecorder,
    scope: DataScope,
    ledger: CommitmentLedger,
    bodies: Mapping[str, Proposal | None],
    versions: Mapping[str, int],
) -> tuple[list[CommitmentLine], list[tuple[str, int]], tuple[int, int, int, int]]:
    """The lines missing for these approved proposals, read against `ledger`."""
    no_time = too_long = malformed = unreadable = 0
    lines: list[CommitmentLine] = []
    sources: list[tuple[str, int]] = []
    made: dict[str, tuple[Commitment, Any]] = {}
    found = [body for body in bodies.values() if body is not None]
    unreadable += sum(1 for proposal_id in versions if bodies.get(proposal_id) is None)
    found.sort(key=lambda body: (root_of(body.proposal_id), replacement_number(body.proposal_id) or 0))
    now = recorder.now()
    for body in found:
        if body.state is not ProposalState.APPROVED:
            continue
        if body.changed_at is None:
            no_time += 1
            continue
        root = root_of(body.proposal_id)
        held = ledger.commitments.get(root)
        head = ledger.heads.get(root)
        if root in made:
            held, head = made[root]
        try:
            if held is None or head is None:
                if root in ledger.malformed_roots:
                    malformed += 1
                    continue
                record = recorder.meeting_record(body, scope)
                commitment = from_approved(
                    body,
                    record=record,
                    zone=recorder.display_zone,
                    machine_zone=recorder.machine_zone,
                )
                line = render_line(Pending(commitment, via=_via(body), written="catch_up", at=now))
                made[root] = (
                    commitment,
                    Head(body.proposal_id, replacement_number(body.proposal_id) or 0, commitment.due.said),
                )
            else:
                number = replacement_number(body.proposal_id) or 0
                if number <= head.number:
                    continue
                revised = revised_from(body, held, head, via=_via(body), written="catch_up", at=now)
                line = render_line(revised)
                made[root] = (
                    _after_revision(held, revised.due),
                    Head(body.proposal_id, number, head.due_said if revised.due is None else revised.due.said),
                )
        except TooLong:
            too_long += 1
            continue
        except NoApprovalInstant:
            no_time += 1
            continue
        if line.entry in ledger.entries:
            continue
        lines.append(line)
        sources.append((body.proposal_id, versions[body.proposal_id]))
    return lines, sources, (no_time, too_long, malformed, unreadable)


class CommitmentCatchUp:
    """The one-shot catch-up task `_lifespan` creates at daemon start (story 15a).

    Its reads run on the I/O pool and its appends on the writer thread
    (AD-19), off the request path. It never stops the daemon starting: a
    failure is logged, and the next start tries again.
    """

    def __init__(self, *, requests: Any, now: Callable[[], datetime]) -> None:
        self.requests = requests
        self.now = now
        self.last: CatchUp | None = None
        self.ran = 0

    async def run(self) -> None:
        try:
            self.last = await self.requests.pool.run(self._pass)
        except asyncio.CancelledError:
            raise
        except Exception as unexpected:  # noqa: BLE001 — logged; the next start tries again
            _log.error(
                "the commitment catch-up stopped on an unexpected %s; the next start tries again",
                type(unexpected).__name__,
            )
        finally:
            self.ran += 1

    def _pass(self) -> CatchUp:
        daemon = self.requests.daemon
        paths = daemon.storage.paths if isinstance(daemon.storage.paths, ScopePaths) else None
        recorder = recorder_for(
            daemon.storage,
            daemon.meetings,
            types=daemon.proposal_types,
            now=self.now,
            display_timezone=daemon.config.display_timezone,
        )
        writer = self.requests.writer
        return catch_up(
            recorder,
            paths=paths,
            step=lambda work: writer.call(work, label=CATCH_UP_STEP),
        )
