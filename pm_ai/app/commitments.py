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
import re
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, TypeVar

from pm_ai.app.concurrency import WRITER_START_WAIT_SECONDS, WriterBusy, WriterStopped
from pm_ai.app.proposal_reads import ProposalWorld, open_world
from pm_ai.app.scope_reads import read_display_config
from pm_ai.app.wiring import REGISTRY_ARTIFACT
from pm_ai.core.commitment_cards import (
    LIST_STATES,
    MARK_VERBS,
    Card,
    CommitmentListing,
    CommitmentMisused,
    CommitmentOutcome,
    CommitmentRefused,
    LedgerCounts,
    build_card,
    change_words,
    closed_reason,
    date_words,
    undo_until,
    valid_version,
)
from pm_ai.core.commitment_log import (
    NOTE_LIMIT,
    REASONS,
    CommitmentLedger,
    CommitmentLine,
    CommitmentLog,
    Details,
    DueSet,
    Head,
    Marked,
    NoApprovalInstant,
    OwnerSet,
    Pending,
    Reason,
    Recording,
    TooLong,
    Undone,
    fold,
    from_approved,
    parse_ledger,
    preview_due,
    render_line,
    revised_from,
)
from pm_ai.core.due_dates import (
    FORM_DATED,
    FORM_END_OF_WEEK,
    FORM_THIS_WEEKDAY,
    FORM_WEEKDAY,
    due_form,
    resolve_date,
)
from pm_ai.core.meeting_listing import zone_of
from pm_ai.core.proposal_cards import (
    ProposalMisused,
    ProposalRefused,
    command_line,
    day_words,
    via_words,
)
from pm_ai.core.proposals import IMPLICIT_COMMITMENT, OWNER_LIMIT, ProposalTypes
from pm_ai.domain.claims import ClaimReentered
from pm_ai.domain.commitments import (
    COMMITMENTS_LOG,
    DUE_NONE,
    DUE_UNRESOLVED,
    UNDO_WINDOW,
    Commitment,
    LineKind,
    Mark,
    MarkAnswer,
    NotACommitmentScope,
    StateSource,
    mark_answer,
)
from pm_ai.domain.handles import reads_as_handle
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.lifecycle import CommitmentState, ProposalState
from pm_ai.domain.meeting_summaries import WHAT_LIMIT
from pm_ai.domain.proposals import Proposal, fold_typed, replacement_number, root_of
from pm_ai.domain.scope_model import ScopeResolutionError
from pm_ai.domain.storage_tiers import UnprotectedCaptureDir
from pm_ai.platform.environment import machine_zone
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import ArtifactBusy, DecryptionFailed, LedgerChanged
from pm_ai.storage.service import (
    DAEMON_BUSY_WAIT_SECONDS,
    OperationalStoreUnavailable,
    ProposalIndexRow,
    ProposalsUnreadable,
    proposal_bodies_at,
    proposal_index_at,
)

__all__ = [
    "ATTEMPTS",
    "CATCH_UP_STEP",
    "COMMITMENT_WRITE_BOUND_SECONDS",
    "CatchUp",
    "CommitmentCatchUp",
    "CommitmentDesk",
    "CommitmentRecorder",
    "EDIT_NEEDS_SECONDS",
    "ScopeCatchUp",
    "commitment_choices",
    "commitment_owners",
    "pm_due",
    "pm_handle",
    "read_ledger",
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


# ── The PM's commitment commands (story 15c) ─────────────────────────────────

COMMITMENT_WRITE_BOUND_SECONDS = 2.0
"""`commitment done|altered|broken|withdrawn|due|owner|undo`'s bound at the prompt
(AD-21, story 15c): one ledger read, the git check for a project, the PM's
handle, one append — and, for `altered --what`, 13a's edit within the same 2 s."""

EDIT_NEEDS_SECONDS = WRITER_START_WAIT_SECONDS + DAEMON_BUSY_WAIT_SECONDS
"""What 13a's edit needs of the bound: its 1-second wait for the writer and one
claimed step's busy wait. With less left, `altered --what` does not start it."""

COMMITMENT_LIMITS = (1, 200)
COMMITMENT_DEFAULT_LIMIT = 20
COMMITMENT_CANDIDATES = 100

_BUSY = (ArtifactBusy, WriterBusy, WriterStopped, OperationalStoreUnavailable, ClaimReentered)
_UNREADABLE = (OSError, DecryptionFailed, UnicodeError, ScopeResolutionError)
_HANDLE_FAILURES = (OSError, DecryptionFailed, UnicodeError, ValueError)
"""What reading the `connectors/` rows for the PM's handle may meet (`read_pm_identity`)."""
_ADDRESS = re.compile(r"[^\s@]+@[^\s@]+")
_ONE_MICROSECOND = timedelta(microseconds=1)

DUE_FORMS = (
    "a weekday (`friday`, `до пятницы`), `today`, `tomorrow`, `in 3 days`, `in 2 weeks`, "
    "`end of week`, `end of month`, `3 Nov`, `30.10`, or a date with its year (`2026-11-06`, "
    "`6.11.2026`, `6 Nov 2026`, `6 ноября 2026`)"
)
"""The forms the PM's due resolves from, named when his words name no one date."""


def typed_name(text: str) -> str:
    """The PM's typed name: NFC-normalised and folded to one line (15c)."""
    return fold_typed(unicodedata.normalize("NFC", text))


def reads_as_handle_or_address(name: str) -> bool:
    """Whether a typed owner reads as a person handle or an address (AD-34, 15c).

    `reads_as_handle` answers `<system>:…`; an address (`alex@example.com`)
    has no system prefix, so it is matched here as one word around one `@`.
    """
    return reads_as_handle(name) or _ADDRESS.fullmatch(name.strip()) is not None


def pm_due(words: str, today: date) -> date | None:
    """The date the PM's typed words name, against `today`; `None` for no one date (15c).

    - A date with its year written is taken as typed, a past one included.
    - A plain weekday that is today's means that weekday next week, 7 days out.
    - The end of the week typed on a Friday, Saturday or Sunday means next
      week's Friday; Monday to Thursday it is this week's, as `resolve` gives.
    - `this <weekday>` on that same weekday stays unresolved.
    - Anything else is `due_dates.resolve_date(words, today)`.

    These rules are for the PM's own words only: `resolve` keeps a meeting's
    "by Friday" said on a Friday unresolved (15a).
    """
    form = due_form(words)
    weekday = today.weekday()
    if form.kind == FORM_DATED:
        return resolve_date(words, date.min)
    if form.kind == FORM_WEEKDAY and form.weekday == weekday:
        return today + timedelta(days=7)
    if form.kind == FORM_THIS_WEEKDAY and form.weekday == weekday:
        return None
    if form.kind == FORM_END_OF_WEEK and weekday >= 4:
        return today + timedelta(days=4 - weekday + 7)
    return resolve_date(words, today)


def read_ledger(storage: Any, resolver: ScopePaths | None, scope: DataScope) -> CommitmentLedger:
    """`scope`'s ledger folded: through `CommitmentLog` when a store is held, else by path.

    By path is completion's and the terminal's `list` and `show`: no
    `StorageService`, no key, the same `parse_ledger` and `fold` (15c).
    """
    return read_ledger_with_bytes(storage, resolver, scope)[0]


def read_ledger_with_bytes(
    storage: Any, resolver: ScopePaths | None, scope: DataScope
) -> tuple[CommitmentLedger, bytes]:
    """`read_ledger`, with the bytes it folded (15c)."""
    if storage is not None:
        return CommitmentLog(storage).read_with_bytes(scope)
    if scope.kind not in (ScopeKind.PERSONAL, ScopeKind.PROJECT):
        raise NotACommitmentScope(f"{scope} keeps no commitment ledger (AD-4, AD-28).")
    base = resolver if resolver is not None else ScopePaths.production(projects={})
    try:
        data = Path(base.resolve(scope, COMMITMENTS_LOG)).read_bytes()
    except FileNotFoundError:
        data = b""
    return fold(parse_ledger(data)), data


LEDGER_READ_FAILURES = (OSError, DecryptionFailed, UnicodeError, ScopeResolutionError, NotACommitmentScope)
"""What reading a commitment ledger may meet that is not a defect in pm-ai (15c)."""


def _unreadable_words(failed: BaseException) -> str:
    return f"its ledger could not be read ({type(failed).__name__})"


@dataclass(frozen=True, slots=True)
class _Found:
    scope: DataScope
    ledger: CommitmentLedger
    commitment: Commitment
    details: Details
    also_in: tuple[str, ...] = ()
    data: bytes = b""
    """The ledger's bytes as read: what was folded."""


def _refound(found: _Found, ledger: CommitmentLedger, data: bytes) -> _Found:
    """`found`'s commitment in a ledger read again: `malformed` or `not_found` when it is not there."""
    root = found.commitment.commitment_id
    if root in ledger.malformed_roots:
        raise CommitmentRefused(
            "malformed",
            f"a line of commitments_log.md in {found.scope} that names {root} does not "
            f"parse, so nothing is decided about it; fix or remove that line.",
        )
    if root not in ledger.commitments:
        listing = ("commitment", "list", "--scope", str(found.scope))
        raise CommitmentRefused(
            "not_found",
            f"{root} is no longer in commitments_log.md in {found.scope}; "
            f"`{command_line(listing)}` lists what is.",
            next=listing,
        )
    return _Found(
        found.scope, ledger, ledger.commitments[root], ledger.details[root], found.also_in, data
    )


@dataclass
class _Plan:
    """The lines a change will append, and what it says once they are in."""

    records: list[Any]
    finish: Callable[[_Found, CommitmentLedger], CommitmentOutcome]
    lines: list[CommitmentLine] = field(default_factory=list)


@dataclass
class CommitmentDesk:
    """The PM's commitment commands (story 15c): one function per verb, every surface.

    `storage` is the writer appends go through — the composed store at a
    terminal, the daemon's handed store for a line typed at the prompt — or
    `None` for `list` and `show` at a terminal and completion, which read the
    ledgers by path. `via` is where a change comes from: `prompt` whenever
    the command was typed at the prompt, `terminal` otherwise. `handle` reads
    the PM's handle (`read_pm_identity`) when a line is written: `(handle,
    readable)`. `proposals` is 13a's desk, for `altered --what`'s edit.
    """

    now: Callable[[], datetime]
    paths: ScopePaths | None = None
    storage: Any = None
    via: str = "terminal"
    types: ProposalTypes | None = None
    reachable: Callable[[], bool] = lambda: False
    zone: tzinfo | None = None
    """The display zone when the composition root knows it; else `config.toml`'s
    `display_timezone`, else the machine's zone."""
    handle: Callable[[], tuple[str | None, bool]] | None = None
    proposals: Any = None
    monotonic: Callable[[], float] = time.monotonic
    bound: float = COMMITMENT_WRITE_BOUND_SECONDS

    # ── reading ──────────────────────────────────────────────────────────

    def world(self) -> ProposalWorld:
        return open_world(self.paths, self.now())

    def _zone(self, world: ProposalWorld) -> tzinfo:
        if self.zone is not None:
            return self.zone
        name, _, _ = read_display_config(world.resolver)
        return zone_of(name) or machine_zone() or timezone.utc

    def _read(self, world: ProposalWorld, scope: DataScope) -> CommitmentLedger:
        return read_ledger(self.storage, world.resolver, scope)

    def _card(self, found: _Found, world: ProposalWorld, zone: tzinfo) -> Card:
        return build_card(
            found.commitment,
            found.details,
            scope=str(found.scope),
            now=world.now,
            zone=zone,
            also_in=found.also_in,
        )

    def _checked_scope(self, scope: DataScope | None, world: ProposalWorld) -> DataScope | None:
        if scope is None:
            return None
        if scope.kind not in (ScopeKind.PERSONAL, ScopeKind.PROJECT):
            raise CommitmentRefused(
                "scope_refused",
                f"{scope} keeps no commitment ledger: only `personal` and `project:<id>` do "
                f"(AD-4, AD-28). Nothing was read.",
            )
        if scope.kind is ScopeKind.PROJECT and scope.project_id not in world.projects:
            if world.registry_fault is not None:
                raise CommitmentRefused(
                    "not_enrolled",
                    f"{REGISTRY_ARTIFACT} could not be read ({world.registry_fault}), so "
                    f"whether {scope} is enrolled cannot be told. Nothing was read.",
                )
            enrolled = ", ".join(f"project:{pid}" for pid in world.projects) or "none"
            raise CommitmentRefused(
                "not_enrolled",
                f"{scope} is not a project projects.toml enrols (enrolled: {enrolled}). "
                f"Nothing was read.",
            )
        return scope

    def _store_row(self, world: ProposalWorld, proposal_id: str) -> ProposalIndexRow | None:
        try:
            rows = proposal_index_at(world.store, ids=[proposal_id])
        except ProposalsUnreadable:
            return None
        return next((row for row in rows if row.proposal_id == proposal_id), None)

    def _find(
        self, commitment_id: str, scope: DataScope | None, world: ProposalWorld, zone: tzinfo, *, verb: bool
    ) -> _Found:
        """The commitment under `commitment_id`, in the finding order 15c gives (15c)."""
        self._checked_scope(scope, world)
        root = root_of(commitment_id)
        looked = [scope] if scope is not None else world.scopes
        ledgers: list[tuple[DataScope, CommitmentLedger]] = []
        read: dict[str, bytes] = {}
        unreadable: list[tuple[DataScope, str]] = []
        for each in looked:
            try:
                ledger, data = read_ledger_with_bytes(self.storage, world.resolver, each)
            except NotACommitmentScope:
                continue
            except _UNREADABLE as failed:
                unreadable.append((each, _unreadable_words(failed)))
                continue
            ledgers.append((each, ledger))
            read[str(each)] = data
        for each, ledger in ledgers:
            if root in ledger.malformed_roots:
                raise CommitmentRefused(
                    "malformed",
                    f"a line of commitments_log.md in {each} that names {root} does not parse, "
                    f"so nothing is decided about it; fix or remove that line.",
                )
        holding = [(each, ledger) for each, ledger in ledgers if root in ledger.commitments]
        if root != commitment_id:
            known = any(
                commitment_id in ledger.details[root].proposals for _, ledger in holding
            ) or self._store_row(world, commitment_id) is not None
            if not known:
                raise CommitmentRefused(
                    "not_found",
                    f"no commitment is recorded under {commitment_id!r}: neither the proposals "
                    f"nor the ledgers know that replacement id.",
                    next=("commitment", "list"),
                )
        if holding:
            first_scope, ledger = holding[0]
            others = tuple(str(each) for each, _ in holding[1:])
            found = _Found(
                first_scope, ledger, ledger.commitments[root], ledger.details[root], others,
                read[str(first_scope)],
            )
            if others:
                if verb:
                    named = ", ".join(str(each) for each, _ in holding)
                    raise CommitmentRefused(
                        "ambiguous",
                        f"{root} is in the ledgers of {named}: the same id is in both, which only "
                        f"a hand copy makes. Nothing was written; remove one of the lines by hand.",
                        card=self._card(found, world, zone),
                    )
            return found
        if unreadable:
            named = "; ".join(f"{each}: {reason}" for each, reason in unreadable)
            raise CommitmentRefused(
                "ledger_unreadable",
                f"{root} is in no ledger that could be read, and it may be in one that could "
                f"not ({named}). Nothing was written.",
            )
        row = self._store_row(world, commitment_id)
        if row is not None:
            where = world.scope_of(row) or scope
            scope_words = ("--scope", str(where)) if where is not None else ()
            records = (
                self.types is not None
                and row.type is not None
                and self.types.rule(row.type).records_commitment
            )
            if records and row.state == ProposalState.APPROVED.value:
                repair = ("proposal", "approve", commitment_id, *scope_words)
                raise CommitmentRefused(
                    "not_found",
                    f"{commitment_id} was approved, but the approval is not recorded in the "
                    f"ledger yet; `{command_line(repair)}` writes it, and so does the next "
                    f"daemon start.",
                    next=repair,
                )
            show = ("proposal", "show", commitment_id, *scope_words)
            raise CommitmentRefused(
                "not_found",
                f"{commitment_id} is a proposal ({row.state}), not a commitment: only an approved "
                f"action item or spoken promise is one. `{command_line(show)}` shows it.",
                next=show,
            )
        listing = ("commitment", "list")
        raise CommitmentRefused(
            "not_found",
            f"no commitment is recorded under {commitment_id!r}; `{command_line(listing)}` "
            f"lists them.",
            next=listing,
        )

    # ── list, show, the walk ─────────────────────────────────────────────

    def listing(
        self,
        *,
        scope: DataScope | None = None,
        meeting_id: str | None = None,
        title: str | None = None,
        on: date | None = None,
        state: str = "open",
        limit: int = COMMITMENT_DEFAULT_LIMIT,
    ) -> CommitmentListing:
        """`commitment list`: open commitments by default, soonest due first (15c)."""
        if state not in LIST_STATES:
            raise CommitmentMisused(
                f"`--state` takes {', '.join(LIST_STATES)}; `{state}` is none of them."
            )
        low, high = COMMITMENT_LIMITS
        if not low <= limit <= high:
            raise CommitmentMisused(f"`--limit` takes a whole number, {low} to {high}.")
        return self._listing(scope=scope, meeting_id=meeting_id, title=title, on=on, state=state, limit=limit)

    def _listing(
        self,
        *,
        scope: DataScope | None,
        meeting_id: str | None,
        title: str | None,
        on: date | None,
        state: str,
        limit: int | None,
    ) -> CommitmentListing:
        """`list`'s cards; `limit=None` is `review`'s walk, which has no limit (15c)."""
        high = COMMITMENT_LIMITS[1]
        world = self.world()
        zone = self._zone(world)
        scope = self._checked_scope(scope, world)
        chosen = self._chosen_meeting(world, scope, meeting_id, title, on)
        if chosen is not None:
            scope, meeting_id = chosen
        covered = [scope] if scope is not None else world.scopes
        unreadable: list[tuple[str, str]] = []
        counts: list[LedgerCounts] = []
        picked: list[_Found] = []
        for each in covered:
            try:
                ledger = self._read(world, each)
            except NotACommitmentScope:
                continue
            except _UNREADABLE as failed:
                if scope is not None:
                    raise CommitmentRefused(
                        "ledger_unreadable",
                        f"commitments_log.md in {each} could not be read "
                        f"({type(failed).__name__}), so nothing could be listed.",
                    ) from failed
                unreadable.append((str(each), _unreadable_words(failed)))
                continue
            counts.append(
                LedgerCounts(
                    str(each), ledger.malformed, ledger.orphans, ledger.duplicates,
                    ledger.conflicts, ledger.refused,
                )
            )
            for root, commitment in ledger.commitments.items():
                if meeting_id is not None and commitment.meeting_id != meeting_id:
                    continue
                if not _passes(commitment, state):
                    continue
                picked.append(_Found(each, ledger, commitment, ledger.details[root]))
        ordered = _ordered(picked)
        # `counts` counts what matches the filters, before `--limit` (15c).
        by_state: dict[str, int] = {}
        by_source = {source.value: 0 for source in StateSource}
        for held in ordered:
            by_state[held.commitment.state.value] = by_state.get(held.commitment.state.value, 0) + 1
            by_source[held.commitment.state_source.value] += 1
        shown = ordered if limit is None else ordered[:limit]
        more = len(ordered) - len(shown)
        cards = tuple(self._card(held, world, zone) for held in shown)
        filters = _filter_args(meeting_id)
        open_scopes = _in_order(
            [str(held.scope) for held in ordered if not held.commitment.state.is_terminal]
        )
        reviews = tuple(("commitment", "review", "--scope", key, *filters) for key in open_scopes)
        looked = _in_order([*(str(held.scope) for held in ordered), *(str(key) for key in covered)])
        widens: list[tuple[str, ...]] = []
        for key in looked:
            base = ("commitment", "list", "--scope", key, *filters)
            if more:
                widens.append((*base, "--state", state, "--limit", str(high)))
            elif state != "all":
                widens.append((*base, "--state", "all"))
        words = f"--state {state}" + (f" --meeting {meeting_id}" if meeting_id else "")
        return CommitmentListing(
            state=state,
            scopes=tuple(sorted({str(each) for each in covered}, key=lambda key: (key != "personal", key))),
            filter_words=words,
            cards=cards,
            unreadable=tuple(unreadable),
            ledgers=tuple(counts),
            by_state=tuple(sorted(by_state.items())),
            by_source=tuple(by_source.items()),
            more=more,
            review=reviews[0] if reviews else None,
            widen=None if reviews or not widens else widens[0],
            reviews=reviews,
            widens=() if reviews else tuple(widens),
        )

    def _chosen_meeting(
        self,
        world: ProposalWorld,
        scope: DataScope | None,
        meeting_id: str | None,
        title: str | None,
        on: date | None,
    ) -> tuple[DataScope, str] | None:
        """`--meeting` or `--title [--on]` choosing one meeting, as 13a's listing does."""
        if meeting_id is None and title is None:
            return None
        from pm_ai.app.proposals import MeetingChoiceRefused, choose_meeting

        try:
            return choose_meeting(
                world, scope, meeting_id, title, on, now=self.now, paths=self.paths, reachable=self.reachable
            )
        except MeetingChoiceRefused as refused:
            raise CommitmentRefused(refused.kind, refused.reason) from refused

    def walk(
        self,
        *,
        scope: DataScope | None = None,
        meeting_id: str | None = None,
        title: str | None = None,
        on: date | None = None,
    ) -> CommitmentListing:
        """Every open commitment in `list`'s order: `review`'s walk, which has no limit (15c)."""
        return self._listing(
            scope=scope, meeting_id=meeting_id, title=title, on=on, state="open", limit=None
        )

    def current(self, commitment_id: str, scope: DataScope) -> Card:
        """The commitment's card as the ledger holds it now: `review` reads it before each card."""
        world = self.world()
        zone = self._zone(world)
        return self._card(self._find(commitment_id, scope, world, zone, verb=False), world, zone)

    def show(self, commitment_id: str, *, scope: DataScope | None = None) -> CommitmentOutcome:
        world = self.world()
        zone = self._zone(world)
        found = self._find(commitment_id, scope, world, zone, verb=False)
        card = self._card(found, world, zone)
        sentence = f"{found.commitment.commitment_id} is {card.heading.split(', ', 1)[-1]}."
        if found.also_in:
            sentence += (
                f" It is ambiguous: the same id is also in {', '.join(found.also_in)}'s ledger, so "
                f"no verb acts on it until one line is removed by hand."
            )
        return CommitmentOutcome("shown", card, sentence, next=self._next(found, world))

    def _next(self, found: _Found, world: ProposalWorld) -> tuple[str, ...]:
        """The next open commitment of the same meeting, else the scope's listing."""
        meeting = found.commitment.meeting_id
        ledger = found.ledger
        if meeting is not None:
            others = [
                _Found(found.scope, ledger, commitment, ledger.details[root])
                for root, commitment in ledger.commitments.items()
                if commitment.meeting_id == meeting
                and root != found.commitment.commitment_id
                and not commitment.state.is_terminal
            ]
            ordered = _ordered(others)
            if ordered:
                return (
                    "commitment", "show", ordered[0].commitment.commitment_id,
                    "--scope", str(found.scope),
                )
        return ("commitment", "list", "--scope", str(found.scope))

    # ── completion ───────────────────────────────────────────────────────

    def choices(self, verb: str) -> tuple[tuple[str, str, str, str], ...]:
        """What `<id>` completes from for `verb`, in `list`'s order, at most 100 (15c).

        `(id, "owner: what" or "said by …: what", meeting title, due)`. For the
        marks, those whose `mark_answer` is `yes`; for `due` and `owner`, the
        open ones; for `undo`, those whose undo is available now; for `show`,
        any. A ledger that cannot be read offers nothing.
        """
        world = self.world()
        zone = self._zone(world)
        picked: list[_Found] = []
        for each in world.scopes:
            try:
                ledger = self._read(world, each)
            except LEDGER_READ_FAILURES:  # a ledger that cannot be read offers nothing
                continue
            for root, commitment in ledger.commitments.items():
                picked.append(_Found(each, ledger, commitment, ledger.details[root]))
        found: list[tuple[str, str, str, str]] = []
        for held in _ordered(picked):
            card = self._card(held, world, zone)
            if not _offered(verb, card):
                continue
            found.append(
                (
                    held.commitment.commitment_id,
                    card.field_line,
                    fold_typed(held.commitment.title or ""),
                    card.due_words,
                )
            )
            if len(found) >= COMMITMENT_CANDIDATES:
                break
        return tuple(found)

    def owner_names(self, commitment_id: str | None, scope: DataScope | None) -> tuple[str, ...]:
        """The owners and speakers already in that scope's ledger, the commitment's speaker first."""
        world = self.world()
        scopes = [scope] if scope is not None else world.scopes
        first: list[str] = []
        names: list[str] = []
        root = None if commitment_id is None else root_of(commitment_id)
        for each in scopes:
            try:
                ledger = self._read(world, each)
            except LEDGER_READ_FAILURES:  # a ledger that cannot be read offers nothing
                continue
            for key, commitment in ledger.commitments.items():
                for name in (commitment.speaker, commitment.owner):
                    if not name:
                        continue
                    target = first if key == root and name == commitment.speaker else names
                    # The same normalisation as `owner`'s compare: NFC and NFD are one name.
                    target.append(typed_name(name))
        return tuple(_in_order([*first, *sorted(set(names))]))

    # ── changes ──────────────────────────────────────────────────────────

    def _handle(self) -> tuple[str | None, bool]:
        if self.handle is not None:
            try:
                return self.handle()
            except _HANDLE_FAILURES:  # an unreadable handle never stops the mark
                return None, False
        if self.storage is None:
            # No store, so no connector rows to read: no handle, and nothing unread.
            return None, True
        return pm_handle(self.storage)

    def _writing_storage(self) -> Any:
        if self.storage is None:
            raise RuntimeError(
                "CommitmentDesk was built without a writer and asked to write: a wiring fault "
                "in pm-ai's composition root."
            )
        return self.storage

    def _occurred(self, found: _Found) -> datetime:
        """The later of now and the newest line's `occurred` plus 1 µs (15c)."""
        return max(self.now(), found.details.changed_at + _ONE_MICROSECOND)

    def _changed(self, found: _Found, if_version: str | None, world: ProposalWorld, zone: tzinfo) -> None:
        if if_version is not None and if_version != found.details.version:
            raise CommitmentRefused(
                "changed",
                f"{found.commitment.commitment_id} changed since version {if_version} was shown: "
                f"{change_words(found.details, world.now, zone)}. Nothing was written; this is "
                f"the current card.",
                card=self._card(found, world, zone),
            )

    def _reread(self, world: ProposalWorld, found: _Found, zone: tzinfo) -> _Found:
        """The commitment read again from its ledger. Raises the ledger's read failure as it came."""
        ledger, data = read_ledger_with_bytes(self.storage, world.resolver, found.scope)
        return _refound(found, ledger, data)

    def _reread_before_writing(self, world: ProposalWorld, found: _Found, zone: tzinfo) -> _Found:
        """`_reread` before anything is written: an unreadable ledger is `ledger_unreadable`."""
        try:
            return self._reread(world, found, zone)
        except _UNREADABLE as failed:
            raise CommitmentRefused(
                "ledger_unreadable",
                f"commitments_log.md in {found.scope} could not be read again "
                f"({type(failed).__name__}); nothing was written.",
            ) from failed

    def _read_or(self, found: _Found, zone: tzinfo) -> _Found:
        """The commitment read again, or `found` as it is when the read fails (after a write)."""
        try:
            return self._reread(self.world(), found, zone)
        except (CommitmentRefused, *_UNREADABLE):
            return found

    def _after_writing(self, found: _Found, lines: Sequence[CommitmentLine], zone: tzinfo) -> _Found:
        """The commitment after this call's append: read again, else folded from what was read
        plus what was appended. The lines are written, so this never reports a refusal."""
        try:
            return self._reread(self.world(), found, zone)
        except (CommitmentRefused, *_UNREADABLE):
            data = found.data + "".join(line.text + "\n" for line in lines).encode("utf-8")
            return _refound(found, fold(parse_ledger(data)), data)

    def _write(
        self,
        commitment_id: str,
        scope: DataScope | None,
        decide: Callable[[_Found, ProposalWorld, tzinfo], CommitmentOutcome | _Plan],
    ) -> CommitmentOutcome:
        """The writer's steps (15c, AD-37): read, decide, compare-and-append, read again."""
        world = self.world()
        zone = self._zone(world)
        found = self._find(commitment_id, scope, world, zone, verb=True)
        for attempt in range(ATTEMPTS):
            if attempt:
                # `LedgerChanged`: read again and decide again; `occurred` is recomputed.
                world = self.world()
                found = self._reread_before_writing(world, found, zone)
            decided = decide(found, world, zone)
            if isinstance(decided, CommitmentOutcome):
                return decided
            try:
                decided.lines = [render_line(record) for record in decided.records]
            except TooLong as too_long:
                raise CommitmentMisused(str(too_long)) from too_long
            fresh = [line for line in decided.lines if line.entry not in found.ledger.entries]
            if not fresh:
                return decided.finish(found, found.ledger)
            storage = self._writing_storage()
            try:
                CommitmentLog(storage).append(fresh, scope=found.scope, after=found.ledger.size)
            except LedgerChanged:
                continue
            except NotACommitmentScope as refused:
                raise CommitmentRefused("scope_refused", f"{refused} Nothing was written.") from refused
            except UnprotectedCaptureDir as refused:
                raise CommitmentRefused(
                    "git_not_excluded",
                    f"{REASONS['git_not_excluded']} ({refused}); nothing was written.",
                ) from refused
            except _BUSY as busy:
                raise CommitmentRefused(
                    "busy",
                    f"{REASONS['busy']} ({type(busy).__name__}); nothing was written. Run the "
                    f"command again.",
                ) from busy
            except OSError as failed:
                # An append that raised after its bytes reached the file is
                # found on the re-read by this call's own entry ids.
                why = failed.strerror or type(failed).__name__
                try:
                    again: _Found | None = self._reread(self.world(), found, zone)
                except (CommitmentRefused, *_UNREADABLE):
                    again = None
                if again is None:
                    raise CommitmentRefused(
                        "disk",
                        f"{REASONS['disk']} ({why}), and the ledger could not be read again, so "
                        f"whether anything was written could not be checked.",
                    ) from failed
                landed = [line for line in fresh if line.entry in again.ledger.entries]
                if len(landed) == len(fresh):
                    return decided.finish(again, again.ledger)
                if landed:
                    kinds = [line.kind.value for line in landed]
                    raise CommitmentRefused(
                        "disk",
                        f"{REASONS['disk']} ({why}); only part of it was written: "
                        f"{', '.join(kinds)}.",
                        card=self._card(again, self.world(), zone),
                        wrote=kinds,
                    ) from failed
                raise CommitmentRefused(
                    "disk", f"{REASONS['disk']} ({why}); nothing was written."
                ) from failed
            after = self._after_writing(found, fresh, zone)
            return decided.finish(after, after.ledger)
        raise CommitmentRefused(
            "busy",
            f"{REASONS['busy']}: the ledger kept changing while this was written, so nothing "
            f"was written. Run the command again.",
        )

    def _outcome(
        self,
        outcome: str,
        found: _Found,
        sentence: str,
        *,
        wrote: Sequence[str] = (),
        note_recorded: bool = False,
        handle: tuple[str | None, bool] | None = None,
        edited: str | None = None,
        undo: bool = False,
    ) -> CommitmentOutcome:
        world = self.world()
        zone = self._zone(world)
        card = self._card(found, world, zone)
        handle_recorded: bool | None = None
        if wrote:
            handle_recorded = handle is not None and handle[0] is not None
            if handle is not None and not handle[1]:
                sentence += " It was recorded without your handle: the connector rows could not be read."
        offered: tuple[tuple[str, ...], datetime] | None = None
        shown: str | None = None
        if undo:
            action = card.action("undo")
            until = undo_until(found.details)
            if action is not None and action.available and action.command is not None and until is not None:
                offered = (action.command, until)
                shown = day_words(until, zone, world.now, time=True)
        return CommitmentOutcome(
            outcome,
            card,
            sentence,
            wrote=tuple(wrote),
            edited=edited,
            note_recorded=note_recorded,
            handle_recorded=handle_recorded,
            next=self._next(found, world),
            undo=offered,
            undo_shown=shown,
        )

    def mark(
        self,
        verb: str,
        commitment_id: str,
        *,
        note: str | None = None,
        scope: DataScope | None = None,
        if_version: str | None = None,
    ) -> CommitmentOutcome:
        """`commitment done|broken|withdrawn <id>`: the PM's own mark, by the table (15c)."""
        if verb not in ("done", "broken", "withdrawn"):
            raise CommitmentMisused(f"`{verb}` is not one of done, broken, withdrawn.")
        typed = _note(note)
        _version(if_version)
        mark = MARK_VERBS[verb]
        handle: list[tuple[str | None, bool]] = []

        def decide(found: _Found, world: ProposalWorld, zone: tzinfo) -> CommitmentOutcome | _Plan:
            commitment, details = found.commitment, found.details
            answer = mark_answer(commitment.state, commitment.state_source, mark)
            if answer is MarkAnswer.ALREADY:
                held = details.mark
                clauses = [f"{commitment.commitment_id}: already marked {verb}"]
                if held is not None:
                    clauses.append(day_words(held.occurred, zone, world.now, time=True))
                    where = via_words(held.via)
                    if where:
                        clauses.append(where)
                sentence = ", ".join(clauses) + "; nothing was written."
                if typed is not None:
                    sentence += " The note was not recorded."
                return self._outcome("already", found, sentence)
            self._changed(found, if_version, world, zone)
            if answer is MarkAnswer.REFUSED:
                card = self._card(found, world, zone)
                action = card.action(verb)
                reason = action.reason if action is not None and action.reason else "the mark table refuses it"
                raise CommitmentRefused(
                    "mark_refused",
                    f"{commitment.commitment_id} is not marked {verb}: {reason}. Nothing was written.",
                    card=card,
                )
            if not handle:
                handle.append(self._handle())
            occurred = self._occurred(found)
            record = Marked(
                commitment.commitment_id, mark, occurred=occurred, via=self.via, at=self.now(),
                handle=handle[0][0], note=typed,
            )
            source = commitment.state_source
            sentence = f"{commitment.commitment_id} marked {verb}."
            if source is StateSource.TELEMETRY:
                if commitment.state is mark.state:
                    sentence = f"{commitment.commitment_id} marked {verb}; it confirms telemetry's verdict."
                else:
                    sentence = (
                        f"{commitment.commitment_id} marked {verb}; it overrides telemetry's verdict "
                        f"({_TELEMETRY_WORDS.get(commitment.state, commitment.state.value)})."
                    )

            def finish(after: _Found, _: CommitmentLedger) -> CommitmentOutcome:
                return self._outcome(
                    "marked", after, sentence, wrote=(LineKind.MARKED.value,),
                    note_recorded=typed is not None, handle=handle[0], undo=verb == "done",
                )

            return _Plan([record], finish)

        return self._write(commitment_id, scope, decide)

    def altered(
        self,
        commitment_id: str,
        *,
        what: str | None = None,
        owner: str | None = None,
        due: str | None = None,
        note: str | None = None,
        scope: DataScope | None = None,
        if_version: str | None = None,
    ) -> CommitmentOutcome:
        """`commitment altered <id>`: what was agreed has changed (15c).

        Every input is checked before anything is written. One append holds
        the `altered` mark and, when they differ from what is in force, an
        `owner_set` and a `due_set` line, sharing one `occurred`; then, for an
        action item's new wording, 13a's edit of its approved proposal.
        """
        started = self.monotonic()
        typed_note = _note(note)
        _version(if_version)
        wording: str | None = None
        if what is not None:
            wording = fold_typed(what)
            if not wording:
                raise CommitmentMisused("`--what` takes the new wording; it cannot be empty.")
            if len(wording) > WHAT_LIMIT:
                raise CommitmentMisused(f"`--what` is at most {WHAT_LIMIT} characters.")
        new_owner: str | None = None
        if owner is not None:
            new_owner = _owner(owner, allow_clear=True)
        handle: list[tuple[str | None, bool]] = []

        def decide(found: _Found, world: ProposalWorld, zone: tzinfo) -> CommitmentOutcome | _Plan:
            commitment, details = found.commitment, found.details
            root = commitment.commitment_id
            if wording is not None and commitment.type == IMPLICIT_COMMITMENT:
                raise CommitmentRefused(
                    "what_not_editable",
                    "a spoken promise keeps the words that were said; record how it changed with "
                    "--note. Nothing was written.",
                    card=self._card(found, world, zone),
                )
            today = world.now.astimezone(zone).date()
            due_date: date | None = None
            clearing_due = False
            if due is not None:
                if not due.strip():
                    clearing_due = True
                else:
                    due_date = pm_due(due, today)
                    if due_date is None:
                        raise CommitmentRefused(
                            "due_unresolved",
                            _unresolved_words(due) + " Nothing was written, the mark included.",
                            card=self._card(found, world, zone),
                        )
            if new_owner is not None and new_owner and reads_as_handle_or_address(new_owner):
                raise CommitmentRefused(
                    "owner_is_handle",
                    _handle_words(new_owner) + " Nothing was written, the mark included.",
                    card=self._card(found, world, zone),
                )
            self._changed(found, if_version, world, zone)
            answer = mark_answer(commitment.state, commitment.state_source, Mark.ALTERED)
            if answer is not MarkAnswer.YES:
                card = self._card(found, world, zone)
                action = card.action("altered")
                reason = action.reason if action is not None and action.reason else "the mark table refuses it"
                raise CommitmentRefused(
                    "mark_refused",
                    f"{root} is not marked altered: {reason}. Nothing was written.",
                    card=card,
                )
            if not handle:
                handle.append(self._handle())
            occurred = self._occurred(found)
            at = self.now()
            who = handle[0][0]
            records: list[Any] = [
                Marked(root, Mark.ALTERED, occurred=occurred, via=self.via, at=at, handle=who, note=typed_note)
            ]
            wrote = [LineKind.MARKED.value]
            if new_owner is not None and not _same_owner(commitment.owner, new_owner or None):
                records.append(OwnerSet(root, new_owner or None, occurred=occurred, via=self.via, at=at, handle=who))
                wrote.append(LineKind.OWNER_SET.value)
            if due is not None and not _same_due(commitment, details, due_date, clearing_due):
                records.append(DueSet(root, due_date, occurred=occurred, via=self.via, at=at, handle=who))
                wrote.append(LineKind.DUE_SET.value)
            edit_to = wording if wording is not None and wording != fold_typed(commitment.what) else None
            past = due_date is not None and due_date < today

            def finish(after: _Found, _: CommitmentLedger) -> CommitmentOutcome:
                sentence = f"{root} marked altered."
                if past and due_date is not None:
                    sentence += f" Due {date_words(due_date, world.now, zone)} (already past)."
                if edit_to is None:
                    return self._outcome(
                        "altered", after, sentence, wrote=wrote,
                        note_recorded=typed_note is not None, handle=handle[0],
                    )
                return self._edit_wording(after, edit_to, sentence, wrote, typed_note, handle[0], started)

            return _Plan(records, finish)

        return self._write(commitment_id, scope, decide)

    def _edit_wording(
        self,
        found: _Found,
        wording: str,
        sentence: str,
        wrote: Sequence[str],
        note: str | None,
        handle: tuple[str | None, bool],
        started: float,
    ) -> CommitmentOutcome:
        """`altered --what`'s second write: 13a's edit, after the mark is recorded (15c)."""
        commitment = found.commitment
        proposal_id = commitment.proposal_id
        again = ("proposal", "edit", proposal_id, "--scope", str(found.scope), "--what", wording)
        world = self.world()
        zone = self._zone(world)
        left = self.bound - (self.monotonic() - started)
        if left < EDIT_NEEDS_SECONDS:
            raise CommitmentRefused(
                "edit_refused",
                f"{commitment.commitment_id}: the altered mark is recorded, and the wording is "
                f"not: out of time ({left:.2f} s of {self.bound:g} s left, and the edit needs "
                f"{EDIT_NEEDS_SECONDS:g} s). Run `{command_line(again)}`.",
                card=self._card(found, world, zone),
                wrote=wrote,
                next=again,
            )
        desk = self._proposal_desk()
        try:
            edited = desk.edit(proposal_id, changes={"what": wording}, scope=found.scope)
        except (ProposalRefused, ProposalMisused) as refused:
            reason = refused.reason if isinstance(refused, ProposalRefused) else str(refused)
            raise CommitmentRefused(
                "edit_refused",
                f"{commitment.commitment_id}: the altered mark is recorded, and the wording is "
                f"not: {reason} Run `{command_line(again)}`.",
                card=self._card(self._read_or(found, zone), world, zone),
                wrote=wrote,
                next=again,
            ) from refused
        after = self._read_or(found, zone)
        replacement = None if edited.outcome == "unchanged" else edited.card.proposal_id
        if replacement is not None:
            sentence += f" Its wording is edited: {replacement} now holds it."
        return self._outcome(
            "altered", after, sentence, wrote=wrote, note_recorded=note is not None,
            handle=handle, edited=replacement,
        )

    def _proposal_desk(self) -> Any:
        if self.proposals is not None:
            return self.proposals
        from pm_ai.app.proposals import ProposalDesk

        kwargs: dict[str, Any] = {}
        if self.types is not None:
            kwargs["types"] = self.types
        return ProposalDesk(
            now=self.now,
            paths=self.paths,
            storage=self.storage,
            via=self.via,
            reachable=self.reachable,
            **kwargs,
        )

    def set_due(
        self,
        commitment_id: str,
        *,
        words: str | None = None,
        clear: bool = False,
        scope: DataScope | None = None,
        if_version: str | None = None,
    ) -> CommitmentOutcome:
        """`commitment due <id> (<words> | --clear)`: one `due_set` line, open commitments only (15c)."""
        if clear == (words is not None):
            raise CommitmentMisused("`commitment due` takes the due's words or `--clear`, one of them.")
        if words is not None and not words.strip():
            raise CommitmentMisused(
                "the due's words are empty; write when it is due, or `--clear` to clear it."
            )
        _version(if_version)
        handle: list[tuple[str | None, bool]] = []

        def decide(found: _Found, world: ProposalWorld, zone: tzinfo) -> CommitmentOutcome | _Plan:
            commitment, details = found.commitment, found.details
            root = commitment.commitment_id
            today = world.now.astimezone(zone).date()
            found_date: date | None = None
            if words is not None:
                found_date = pm_due(words, today)
                if found_date is None:
                    raise CommitmentRefused(
                        "due_unresolved",
                        _unresolved_words(words) + " Nothing was written.",
                        card=self._card(found, world, zone),
                    )
            self._closed(found, world, zone, "due")
            if _same_due(commitment, details, found_date, clear):
                return self._outcome(
                    "unchanged", found, f"{root} already has that due; nothing was written."
                )
            self._changed(found, if_version, world, zone)
            if not handle:
                handle.append(self._handle())
            record = DueSet(
                root, found_date, occurred=self._occurred(found), via=self.via, at=self.now(),
                handle=handle[0][0],
            )
            if found_date is None:
                sentence = f"{root}: its due date is cleared."
            else:
                sentence = f"{root}: due {date_words(found_date, world.now, zone)}, set by you"
                sentence += " (already past)." if found_date < today else "."

            def finish(after: _Found, _: CommitmentLedger) -> CommitmentOutcome:
                return self._outcome(
                    "due_set", after, sentence, wrote=(LineKind.DUE_SET.value,), handle=handle[0]
                )

            return _Plan([record], finish)

        return self._write(commitment_id, scope, decide)

    def set_owner(
        self,
        commitment_id: str,
        *,
        name: str | None = None,
        clear: bool = False,
        scope: DataScope | None = None,
        if_version: str | None = None,
    ) -> CommitmentOutcome:
        """`commitment owner <id> (<name> | --clear)`: one `owner_set` line, open only (15c)."""
        if clear == (name is not None):
            raise CommitmentMisused("`commitment owner` takes a name or `--clear`, one of them.")
        typed = None if name is None else _owner(name, allow_clear=False)
        _version(if_version)
        handle: list[tuple[str | None, bool]] = []

        def decide(found: _Found, world: ProposalWorld, zone: tzinfo) -> CommitmentOutcome | _Plan:
            commitment = found.commitment
            root = commitment.commitment_id
            if typed is not None and reads_as_handle_or_address(typed):
                raise CommitmentRefused(
                    "owner_is_handle",
                    _handle_words(typed) + " Nothing was written.",
                    card=self._card(found, world, zone),
                )
            self._closed(found, world, zone, "owner")
            if _same_owner(commitment.owner, typed):
                return self._outcome(
                    "unchanged", found, f"{root} already has that owner; nothing was written."
                )
            self._changed(found, if_version, world, zone)
            if not handle:
                handle.append(self._handle())
            record = OwnerSet(
                root, typed, occurred=self._occurred(found), via=self.via, at=self.now(),
                handle=handle[0][0],
            )
            sentence = f"{root}: owner {typed}." if typed else f"{root}: its owner is cleared."

            def finish(after: _Found, _: CommitmentLedger) -> CommitmentOutcome:
                return self._outcome(
                    "owner_set", after, sentence, wrote=(LineKind.OWNER_SET.value,), handle=handle[0]
                )

            return _Plan([record], finish)

        return self._write(commitment_id, scope, decide)

    def _closed(self, found: _Found, world: ProposalWorld, zone: tzinfo, field_name: str) -> None:
        commitment = found.commitment
        if not commitment.state.is_terminal:
            return
        reason = closed_reason(
            commitment, found.details, str(found.scope), world.now, zone, field_name=field_name
        )
        raise CommitmentRefused(
            "commitment_closed",
            f"{commitment.commitment_id}'s {field_name} is set only while it is open: {reason}. "
            f"Nothing was written.",
            card=self._card(found, world, zone),
        )

    def undo(
        self, commitment_id: str, *, scope: DataScope | None = None, if_version: str | None = None
    ) -> CommitmentOutcome:
        """`commitment undo <id>`: the PM's own done mark taken back, within 4 hours (15c)."""
        _version(if_version)
        handle: list[tuple[str | None, bool]] = []

        def decide(found: _Found, world: ProposalWorld, zone: tzinfo) -> CommitmentOutcome | _Plan:
            commitment, details = found.commitment, found.details
            root = commitment.commitment_id
            if details.undone is not None and details.undone_mark_at is not None:
                when = day_words(details.undone_mark_at, zone, world.now, time=True)
                return self._outcome(
                    "already", found, f"{root}: your done mark of {when} is already undone; nothing was written."
                )
            mark = details.mark
            card = self._card(found, world, zone)
            if (
                if_version is not None
                and if_version != details.version
                and if_version in details.done_marks
            ):
                # The version shown was a done mark, and a later line follows it.
                raise CommitmentRefused(
                    "undo_superseded",
                    f"{root}'s done mark is not undone: a later line follows it "
                    f"({change_words(details, world.now, zone)}). Nothing was written.",
                    card=card,
                )
            if (
                commitment.state is not CommitmentState.FULFILLED
                or commitment.state_source is not StateSource.PM
                or mark is None
                or mark.mark is not Mark.FULFILLED
            ):
                action = card.action("undo")
                reason = action.reason if action is not None and action.reason else "nothing to undo"
                raise CommitmentRefused(
                    "nothing_to_undo", f"{root}: {reason}. Nothing was written.", card=card
                )
            if details.newest.entry != mark.entry:
                raise CommitmentRefused(
                    "undo_superseded",
                    f"{root}'s done mark is not undone: a later line follows it "
                    f"({change_words(details, world.now, zone)}). Nothing was written.",
                    card=card,
                )
            self._changed(found, if_version, world, zone)
            occurred = self._occurred(found)
            if occurred - mark.occurred > UNDO_WINDOW:
                raise CommitmentRefused(
                    "undo_expired",
                    f"{root}: marked done at {day_words(mark.occurred, zone, world.now, time=True)}, "
                    f"more than 4 hours ago; mark it broken if it was not done. Nothing was written.",
                    card=card,
                )
            if not handle:
                handle.append(self._handle())
            record = Undone(
                root, mark.entry, occurred=occurred, via=self.via, at=self.now(), handle=handle[0][0]
            )
            when = day_words(mark.occurred, zone, world.now, time=True)

            def finish(after: _Found, _: CommitmentLedger) -> CommitmentOutcome:
                state = after.commitment.state
                words = "open" if not state.is_terminal else _TELEMETRY_WORDS.get(state, state.value)
                if state is CommitmentState.ALTERED:
                    words = "open, altered"
                return self._outcome(
                    "undone", after, f"{root}: your done mark of {when} is undone; it is {words} again.",
                    wrote=(LineKind.UNDONE.value,), handle=handle[0],
                )

            return _Plan([record], finish)

        return self._write(commitment_id, scope, decide)


_TELEMETRY_WORDS = {
    CommitmentState.FULFILLED: "done",
    CommitmentState.BROKEN: "broken",
    CommitmentState.WITHDRAWN: "withdrawn",
    CommitmentState.UNKNOWN: "unknown",
    CommitmentState.ERROR: "in error",
}


def pm_handle(storage: Any) -> tuple[str | None, bool]:
    """The PM's handle for a mark: the lowest `pm_identity` gives, and whether the rows read (15c).

    When `read_pm_identity` fails or lists rows as unusable, the mark is still
    written, without a handle, and the outcome says so.
    """
    from pm_ai.app.wiring import read_pm_identity

    try:
        reading = read_pm_identity(storage)
    except _HANDLE_FAILURES:  # an unreadable handle never stops the mark
        return None, False
    if reading.unusable:
        return None, False
    handles = sorted(str(handle) for handle in reading.identity)
    return (handles[0] if handles else None), True


def _note(note: str | None) -> str | None:
    if note is None:
        return None
    folded = fold_typed(note)
    if len(folded) > NOTE_LIMIT:
        raise CommitmentMisused(f"`--note` is at most {NOTE_LIMIT} characters; it is {len(folded)}.")
    return folded or None


def _owner(name: str, *, allow_clear: bool) -> str:
    folded = typed_name(name)
    if not folded and not allow_clear:
        raise CommitmentMisused("the owner's name is empty; write a name, or `--clear` to clear it.")
    if len(folded) > OWNER_LIMIT:
        raise CommitmentMisused(f"an owner's name is at most {OWNER_LIMIT} characters; it is {len(folded)}.")
    return folded


def _version(if_version: str | None) -> None:
    if if_version is not None and not valid_version(if_version):
        raise CommitmentMisused(
            f"`--if-version` takes a commitment's version, `cml_` and 12 lower-case hex "
            f"characters, as its card shows it; {if_version!r} is not one."
        )


def _same_owner(current: str | None, typed: str | None) -> bool:
    held = None if current is None else typed_name(current) or None
    return held == (typed or None)


def _same_due(commitment: Commitment, details: Details, found: date | None, clear: bool) -> bool:
    """The `unchanged` rule (15c): the PM's own exact date already in force, or no due to clear."""
    due = commitment.due
    if clear:
        return due.value == DUE_NONE
    return (
        found is not None
        and due.date == found
        and not due.estimated
        and details.due_by_pm
    )


def _unresolved_words(words: str) -> str:
    return (
        f'"{fold_typed(words)}" names no one date. pm-ai reads {DUE_FORMS}; "this <weekday>" '
        f"typed on that weekday may mean today, so it names no date."
    )


def _handle_words(name: str) -> str:
    return (
        f"{name!r} reads as a handle or an address, and an owner is a name, never a handle "
        f"(AD-34); write the person's name."
    )


def _passes(commitment: Commitment, state: str) -> bool:
    if state == "all":
        return True
    current = commitment.state
    if state == "open":
        return not current.is_terminal
    if state == "done":
        return current is CommitmentState.FULFILLED
    return current.value == state


def _due_key(commitment: Commitment) -> tuple[int, date]:
    found = commitment.due.date
    if found is not None:
        return (0, found)
    return (1 if commitment.due.value == DUE_UNRESOLVED else 2, date.max)


def _ordered(picked: Sequence[_Found]) -> list[_Found]:
    """`list`'s order (15c): open ones by due, then closed ones by `changed_at`, newest first."""
    opened = [each for each in picked if not each.commitment.state.is_terminal]
    closed = [each for each in picked if each.commitment.state.is_terminal]

    def open_key(each: _Found) -> tuple[int, date, int, float, str]:
        commitment = each.commitment
        rank, found = _due_key(commitment)
        start = commitment.start
        return (
            rank,
            found,
            0 if start is not None else 1,
            -start.timestamp() if start is not None else 0.0,
            commitment.commitment_id,
        )

    opened.sort(key=open_key)
    closed.sort(key=lambda each: (-each.details.changed_at.timestamp(), each.commitment.commitment_id))
    return [*opened, *closed]


def _offered(verb: str, card: Card) -> bool:
    if verb == "show":
        return True
    action = card.action(verb)
    return action is not None and action.available


def _filter_args(meeting_id: str | None) -> tuple[str, ...]:
    return () if meeting_id is None else ("--meeting", meeting_id)


def _in_order(values: Sequence[str] | Any) -> list[str]:
    found: list[str] = []
    for each in values:
        if each not in found:
            found.append(each)
    return found


def commitment_choices(
    verb: str, *, now: datetime, paths: ScopePaths | None = None
) -> tuple[tuple[str, str, str, str], ...]:
    """`CommitmentDesk.choices` read by path: completion's reader (15c, 4u's rules)."""
    return CommitmentDesk(now=lambda: now, paths=paths).choices(verb)


def commitment_owners(
    commitment_id: str | None, scope: str | None, *, now: datetime, paths: ScopePaths | None = None
) -> tuple[str, ...]:
    """`CommitmentDesk.owner_names` read by path: completion's reader for `owner`'s name (15c)."""
    chosen: DataScope | None = None
    if scope is not None:
        try:
            chosen = DataScope.parse(scope)
        except ValueError:
            return ()
        if chosen.kind not in (ScopeKind.PERSONAL, ScopeKind.PROJECT):
            return ()
    return CommitmentDesk(now=lambda: now, paths=paths).owner_names(commitment_id, chosen)
