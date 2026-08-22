"""The single writer (AD-5), with tiers physically separated (AD-3).

Tier 1 is markdown segments on disk. Tier 2 is `operational.db`, a separate
SQLite file, so `reindex` cannot reach operational state by construction rather
than by careful coding.

Tier 2 was four in-memory dicts until 2026-08-19. That made AD-3's "durable and
NOT derivable from Tier 1" and AD-20's "every deferred unit of work is a
persisted row" false in the same breath: a restart silently emptied the job
state, the connector cursors, the executed-key ledger, and the dedup set — the
last of which turns AD-34's "re-harvesting is idempotent" into a promise that
holds only within one process lifetime.

Raw captures are neither tier and are written here anyway. `transcripts/` sits
inside the one scope that is committed to the employer's repository, so whether
git would publish a capture is a question about that repository rather than about
a directory boundary — and being the single writer is what makes this the one
place it can be asked before anything is on disk.

Asked of git, through `VcsPort`. `.importlinter` forbids `subprocess` here, which
is not an obstacle but the design: this module states the policy — refuse unless
git says the directory is excluded and untracked, refuse when git cannot be
reached at all — and `pm_ai.platform.vcs` runs the commands.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from pm_ai.domain.disclosure import assert_writable
from pm_ai.domain.events import NormalizedEvent
from pm_ai.domain.harvest import Cursor, PersistResult
from pm_ai.domain.identity import DataScope, ScopeKind, SourceRef, TargetRef
from pm_ai.domain.lifecycle import ProposalState
from pm_ai.domain.proposals import Proposal
from pm_ai.domain.storage_tiers import (
    CAPTURES,
    EVENT_LOG,
    GITIGNORE_REQUIRED,
    OPERATIONAL_DB,
    Tier,
    UnprotectedCaptureDir,
    assert_capture_dir_untracked,
)
from pm_ai.domain.vcs import VcsUnavailable
from pm_ai.ports import ScopePathPort, VcsPort

# The application scope owns Tier 2 (AD-3). Resolved rather than remembered, so
# the mapping from artifact to scope stays in the one table that owns it.
APPLICATION = DataScope(ScopeKind.APPLICATION)

# AD-20 — an execution is recorded *before* the call and settled after, so a
# crash in between is a reconciliation task rather than a silent second write.
# A capture filename's ceiling, in bytes. Well under the 255 every filesystem
# this daemon runs on allows, because the name is one component of a path that
# also carries a repository root and a scope tree.
CAPTURE_NAME_LIMIT = 128

IN_FLIGHT = "in_flight"
SETTLED = "settled"
NO_EXTERNAL_ID = ""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cursors (
    instance TEXT PRIMARY KEY,
    token    BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS coverage (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    instance TEXT NOT NULL,
    start    TEXT NOT NULL,
    end      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS executed (
    key         TEXT PRIMARY KEY,
    lock_key    TEXT NOT NULL,
    external_id TEXT,
    state       TEXT NOT NULL,
    at          TEXT NOT NULL,
    settled_at  TEXT
);
CREATE TABLE IF NOT EXISTS seen (
    natural_key TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    body        TEXT NOT NULL,
    version     INTEGER NOT NULL,
    state       TEXT NOT NULL
);
"""


def _ulid() -> str:
    """Surrogate id, minted here and nowhere else (AD-34)."""
    import secrets

    return "evt_" + secrets.token_hex(10)


def _capture_name(name: str) -> str:
    """Validate a capture filename as the single path component it becomes.

    The refusals, in the order they matter:

    - empty, whitespace-only, or padded — two names differing only in spacing are
      one name in every log that reports them, and the padding is invisible;
    - a control character. A newline is the case that makes the whitespace check
      above a half-measure: a name with one embedded is neither empty nor padded,
      and it turns one filename into two lines everywhere the daemon reports it;
    - a path separator, forward or back. This is the one that defeats the git
      check rather than tripping it: `../memory/leak.md` is written *outside* the
      directory git was asked about, so the verdict was about somewhere else.
      Both separators, because a name reaching a Linux daemon from a Windows
      client is still a traversal there;
    - a leading dot. `.` and `..` are directories, and a dotfile hides a capture
      from the operator who has to purge it at thirty days (NFR-09);
    - length. Past the filesystem's limit the write fails with a bare `OSError`
      naming neither the capture nor the limit, which is a worse report than this
      one.
    """
    if not name.strip():
        raise MalformedCaptureName(
            f"a capture needs a name and {name!r} is empty or only whitespace. "
            f"The name is the only handle the purge at thirty days has on it."
        )
    if name != name.strip():
        raise MalformedCaptureName(
            f"{name!r} is padded with whitespace, which makes two distinguishable "
            f"captures indistinguishable in every log and directory listing."
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise MalformedCaptureName(
            f"{name!r} contains a control character. A newline in particular "
            f"splits one filename across two lines in every message that reports "
            f"it, including the refusals in this module."
        )
    if "/" in name or "\\" in name:
        raise MalformedCaptureName(
            f"{name!r} contains a path separator. A capture is one file in the "
            f"capture directory: a nested or absolute path is written somewhere "
            f"git was never asked about, so the check that just passed was about "
            f"a different directory."
        )
    if name.startswith("."):
        raise MalformedCaptureName(
            f"{name!r} starts with a dot. `.` and `..` are directories rather "
            f"than captures, and a dotfile hides a capture from the operator who "
            f"has to purge it at thirty days (NFR-09)."
        )
    if len(name.encode("utf-8")) > CAPTURE_NAME_LIMIT:
        raise MalformedCaptureName(
            f"{name!r} is {len(name.encode('utf-8'))} bytes; the limit is "
            f"{CAPTURE_NAME_LIMIT}. Past the filesystem's own limit the write "
            f"fails with an `OSError` that names neither the capture nor why."
        )
    return name


def _dump_proposal(p: Proposal) -> str:
    return json.dumps(
        {
            "proposal_id": p.proposal_id,
            "type": p.type,
            "summary": p.summary,
            "payload": p.payload,
            "target": str(p.target),
            "cites": str(p.cites),
            "created_at": p.created_at.isoformat(),
            "state": p.state.value,
            "version": p.version,
            "ttl_seconds": p.ttl.total_seconds(),
        },
        sort_keys=True,
    )


def _load_proposal(body: str) -> Proposal:
    d = json.loads(body)
    return Proposal(
        proposal_id=d["proposal_id"],
        type=d["type"],
        summary=d["summary"],
        payload=d["payload"],
        target=TargetRef.parse(d["target"]),
        cites=SourceRef.parse(d["cites"]),
        created_at=datetime.fromisoformat(d["created_at"]),
        state=ProposalState(d["state"]),
        version=d["version"],
        ttl=timedelta(seconds=d["ttl_seconds"]),
    )


class OperationalStoreUnavailable(RuntimeError):
    """Tier 2 could not be opened, and the daemon has no state without it.

    Raised in place of a bare `sqlite3.OperationalError`, whose message ("unable
    to open database file") names neither the path nor the reason — and the path
    is now resolved rather than passed in, so the operator cannot read it off the
    call site either.
    """


class NonUtcClock(ValueError):
    """The injected clock returned a naive or non-UTC datetime.

    Both are silent corruption rather than an error: the monthly segment
    filename is formatted from this value, so an offset clock files an entry
    under the wrong month at a boundary, and a naive one raises `TypeError` later
    when it is compared against the aware timestamps every other producer emits.
    """


class MalformedCaptureName(ValueError):
    """A capture filename that is not a single name inside the capture directory.

    The name reaches this service from a meeting id, a dropped filename, or a
    connector handle, so none of it is the daemon's own string — and it is
    interpolated into a path. `pm_ai.platform.paths` validates the subject ids it
    interpolates for the same reason; this is the one path component that module
    never sees, because it names a record rather than a scope.
    """


class EmptyCapture(ValueError):
    """A capture with no content, which would consume a name and say nothing.

    A zero-length transcript reads downstream exactly like a real one — a meeting
    that happened and in which nobody spoke — and it occupies the name the real
    capture would have used, so the retry that carries the content is refused as
    a duplicate. Refusing here keeps the failure attached to its cause.
    """


class CaptureAlreadyExists(FileExistsError):
    """A capture already occupies this name, and neither outcome is acceptable.

    Appending would splice two recordings into one file that reads as a single
    meeting; truncating would destroy the first. Subclasses `FileExistsError`
    because that is what the exclusive open raises and a caller may reasonably
    already catch it — the name and the message are what this adds.
    """


class ReconciliationRequired(RuntimeError):
    """AD-20 — a prior attempt reached the provider and its outcome is unknown.

    Retrying is not safe and neither is assuming success. The operator, or a
    provider-side idempotency token, resolves it.
    """


class StorageService:
    """Owns every write. Nothing else opens a file for writing (AD-5)."""

    tier_of_operational = Tier.OPERATIONAL

    def __init__(
        self,
        paths: ScopePathPort,
        *,
        now: Callable[[], datetime],
        vcs: VcsPort,
    ) -> None:
        """Every dependency is injected, and none is optional.

        `paths` arrives from the composition root because `pm_ai.storage` and
        `pm_ai.platform` are independent siblings in the import graph — the
        single writer cannot locate a scope itself. One instance serves every
        scope: the layout is a property of the resolver, not of the service.

        `now` is a required keyword rather than one defaulting to a system-clock
        read, so a caller that forgets it gets an error instead of a hidden one.
        This service reads no clock of its own — `pm_ai.app.wiring` supplies the
        default the daemon shares — though it is not the only default in the
        process (`GitLabConnector.now` has one too). The monthly segment filename
        derives from this clock, which is what made the three internal reads this
        replaces untestable: a test could not name the file it was about to
        assert on.

        `vcs` is required for the same reason as `now`, and more sharply: a
        default would have to be either the real adapter — which this package may
        not import, `pm_ai.platform` being an independent sibling — or a stand-in
        that answers without asking git. The second is the leak this dependency
        exists to prevent, so there is no default and a caller that forgets it
        gets a `TypeError` at construction rather than an unprotected write later.
        """
        self._paths = paths
        self._now = now
        self._vcs = vcs
        # Tier 2 is its own file, in the application scope's enclave and outside
        # every scope's Markdown tree. `reindex` targets Tier 3 and therefore
        # cannot reach this, which is the structural guarantee AD-3 asks for.
        store = paths.resolve(APPLICATION, OPERATIONAL_DB, create=True)
        try:
            self._db = sqlite3.connect(store, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")  # AD-5 — sole writer, WAL
            self._db.executescript(_SCHEMA)
            self._migrate()
            self._db.commit()
        except sqlite3.Error as exc:
            raise OperationalStoreUnavailable(
                f"could not open the operational store at {store}: {exc}. Tier 2 "
                f"holds the job queue, the connector cursors, the executed-key "
                f"ledger, and the dedup set, and none of it is rebuildable "
                f"(AD-3) — so the daemon must not start without it."
            ) from exc

    def _migrate(self) -> None:
        """Add columns a store created by an earlier version does not have.

        `CREATE TABLE IF NOT EXISTS` is a no-op on an existing store, so a schema
        that grows a column would otherwise fail on the first write against a
        Tier-2 file that predates it — and Tier 2 is never rebuilt, so
        "delete it and start again" is not the fix.
        """
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(executed)")}
        if "settled_at" not in columns:
            self._db.execute("ALTER TABLE executed ADD COLUMN settled_at TEXT")

    @property
    def paths(self) -> ScopePathPort:
        """The resolver this service writes through.

        Exposed because a caller holding the writer often needs the location of
        what it just wrote, and re-deriving that location is how a second,
        divergent copy of the layout appears (AD-4).
        """
        return self._paths

    def _at(self) -> datetime:
        """The current instant, from the injected clock, checked before use.

        Every timestamp this service writes goes through here, so a clock that
        would file an entry under the wrong month is refused once rather than
        trusted in three places.
        """
        at = self._now()
        if at.tzinfo is None or at.utcoffset() != timedelta(0):
            raise NonUtcClock(
                f"the injected clock returned {at!r}, which is not UTC. Segment "
                f"filenames are formatted from it and every other producer emits "
                f"aware UTC, so this silently misfiles entries at a month "
                f"boundary and raises on comparison afterwards."
            )
        return at

    # ── Tier 1: append-only markdown segments (AD-5) ─────────────────────────

    def _writable_dir(self, scope: DataScope, artifact: str) -> Path:
        """Resolve a directory artifact for writing, having first earned the right.

        Every write that needs a directory goes through here — the event-log
        segments and the raw captures alike — so the git check cannot be bypassed
        by adding a write path that resolves for itself. It is a no-op for an
        artifact no rule covers, which is all of them but one.

        The refusal happens *before* `create=True`, so a refused write does not
        even leave behind the directory it was about to fill.
        """
        self._assert_git_excludes(scope, artifact)
        return self._paths.resolve(scope, artifact, create=True)

    def _assert_git_excludes(self, scope: DataScope, artifact: str) -> None:
        """Refuse a raw capture that git would carry into a commit (AD-23, AD-38).

        Two conditions gate the question, and both are the scope model's:
        `GITIGNORE_REQUIRED` names the artifacts whose exclusion is a repository
        rule rather than a directory boundary, and `is_git_committed` names the
        one scope that has a repository to ask. A capture in the personal or
        team-member scope is excluded by *where it is*; there is no repository,
        and git is not consulted at all.

        Git answers, not this process. A `.gitignore` containing the rule can
        still leave the directory tracked — a later negation line re-includes it,
        or it was committed before the rule existed — and a `.gitignore` that
        never names the directory can still exclude it through its parent. All
        three are ordinary repository states, and text matching gets two of them
        wrong in the direction that publishes a transcript.

        `VcsUnavailable` is a refusal, not an exception to it. Unknown is not
        permission: a machine with no `git`, a project whose registered
        repository has been moved away, a repository that was never initialised —
        each leaves the question unanswered, and the only safe answer to an
        unanswered question here is no.

        `scope.project_id` is passed as-is rather than cast. `DataScope` refuses
        a PROJECT scope without one and `is_git_committed` is true for PROJECT
        alone, so it is a non-empty string here by construction — and if that
        ever stops being true, the resolver refuses `None` loudly instead of
        looking up a project named "None".
        """
        if not scope.is_git_committed or artifact not in GITIGNORE_REQUIRED:
            return
        repository = self._paths.repository(scope.project_id)
        # Resolved without `create`: asking git about a directory is not a reason
        # to bring it into existence, and git answers the same either way.
        target = self._paths.resolve(scope, artifact)
        try:
            verdict = self._vcs.tracking(target, repository=repository)
        except VcsUnavailable as unanswered:
            raise UnprotectedCaptureDir(
                f"refusing to write {artifact} into {scope}, because git could "
                f"not be consulted about it: {unanswered}"
            ) from unanswered
        assert_capture_dir_untracked(
            artifact, verdict, gitignore=str(self._paths.gitignore(scope.project_id))
        )

    def _segment(self, scope: DataScope, artifact: str, at: datetime) -> Path:
        """The open monthly segment of the `artifact` ledger in `scope`.

        The directory comes from the resolver, so a scope's event log lands in
        that scope's own tree rather than in a sibling directory named by
        flattening the scope to a string.
        """
        return self._writable_dir(scope, artifact) / f"{at:%Y-%m}.md"

    def append_event_log(self, entry: str, *, scope: DataScope) -> None:
        at = self._at()
        with self._segment(scope, EVENT_LOG, at).open("a", encoding="utf-8") as fh:
            fh.write(entry.rstrip("\n") + "\n")

    # ── Raw captures: outside the tier model, inside a committed scope ───────
    # Not Tier 1 — no rebuild reconstructs a recording and nothing may depend on
    # one (AD-33), which is why `RETENTION_MANAGED` holds them instead of
    # `ARTIFACT_TIER`. They still pass through the single writer, because asking
    # git first has to happen somewhere no caller can skip.

    def write_capture(self, body: str, *, scope: DataScope, name: str) -> Path:
        """Write one raw capture into `scope`, or refuse to write it at all.

        The capture lives in the scope that owns the meeting it records, so a
        team meeting's transcript lands in a committed repository and a 1:1's
        does not. That is why the refusal is here and not in the caller: the
        writer is the only component that knows a write is about to happen.

        Refuses, and each refusal is a different repair:

        - `UnprotectedCaptureDir` — git would commit the capture directory, or
          could not be asked. The message says which, and what to change.
        - `MalformedCaptureName` — the name is not a single component of a path,
          or not one this daemon will report legibly.
        - `EmptyCapture` — there is no content, so the name would be spent on
          nothing and the retry that carries the real transcript refused.
        - `CaptureAlreadyExists` — the name is taken. A capture is verbatim
          input, never amended.
        - `pm_ai.domain.ScopeResolutionError` — the scope holds no capture
          directory (the application scope), its subject id cannot be a
          directory name, or its project is not registered.

        Returns the path written, because the caller that has just produced a
        capture is the one that has to purge it at thirty days (NFR-09) and
        re-deriving the path is how a second copy of the layout appears (AD-4).
        """
        if not body.strip():
            raise EmptyCapture(
                f"refusing to write an empty capture as {name!r}. A zero-length "
                f"transcript reads downstream as a meeting in which nobody spoke, "
                f"and it takes the name the real capture would have used."
            )
        # The name is checked before the directory is resolved, so a malformed
        # one is reported as itself rather than as whatever the resolver or the
        # git check happens to say first — and creates nothing.
        filename = _capture_name(name)
        capture = self._writable_dir(scope, CAPTURES) / filename
        try:
            with capture.open("x", encoding="utf-8") as fh:
                fh.write(body)
        except FileExistsError as taken:
            raise CaptureAlreadyExists(
                f"{capture} already exists. Appending would splice two recordings "
                f"into one transcript and truncating would destroy the first, so "
                f"a second capture needs its own name."
            ) from taken
        except BaseException:
            # Exclusive creation has already claimed the name at this point. A
            # failure mid-write would otherwise leave a zero-length file owning
            # it permanently, and every retry — including the one carrying the
            # content — would then be refused as a duplicate.
            capture.unlink(missing_ok=True)
            raise
        return capture

    def persist_events(
        self, events: tuple[NormalizedEvent, ...], *, scope: DataScope
    ) -> PersistResult:
        """Record a batch, or record none of it (AD-34).

        The `seen` insert and the segment append are one unit. Without the
        rollback, a refusal between them — AD-38's leak guard, or any of the
        resolver's refusals, which a scope with a malformed subject id reaches on
        the very first write — left the dedup rows pending in the implicit
        transaction, where the next unrelated `commit()` persisted them. Every
        event in that batch was then a permanent duplicate that had never been
        written anywhere.

        A crash between the file append and the commit is the remaining window,
        and it is deliberately the safe side of the trade: a replay then appends
        a duplicate line, which is visible in the segment and reconcilable,
        rather than dropping an event, which is not.
        """
        at = self._at()
        try:
            result = self._append_batch(events, at, scope=scope)
        except BaseException:
            self._db.rollback()
            raise
        self._db.commit()
        return result

    def _append_batch(
        self, events: tuple[NormalizedEvent, ...], at: datetime, *, scope: DataScope
    ) -> PersistResult:
        persisted = duplicates = 0
        lines: list[str] = []
        for ev in events:
            key = json.dumps(ev.natural_key)  # AD-34 — includes scope
            if self._db.execute(
                "SELECT 1 FROM seen WHERE natural_key = ?", (key,)
            ).fetchone():
                duplicates += 1
                continue
            self._db.execute("INSERT INTO seen (natural_key) VALUES (?)", (key,))
            stamped = replace(ev, ingested_at=at)  # AD-35 — local clock, assigned here
            assert_writable(stamped, scope=scope)  # AD-38
            lines.append(
                f"- [{_ulid()}] {stamped.type.value} "
                f"actor={stamped.actor.actor_id} src={stamped.source_ref} "
                f"occurred_at={stamped.occurred_at.isoformat() if stamped.occurred_at else 'unknown'} "
                f"ingested_at={at.isoformat()} authored_by={stamped.authored_by.value}"
            )
            persisted += 1
        if lines:
            with self._segment(scope, EVENT_LOG, at).open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        return PersistResult(persisted=persisted, duplicates=duplicates, at=at)

    # ── Tier 2: operational, never rebuilt (AD-3) ────────────────────────────

    def load_cursor(self, instance: str) -> Cursor:
        row = self._db.execute(
            "SELECT token FROM cursors WHERE instance = ?", (instance,)
        ).fetchone()
        return Cursor(row[0]) if row else Cursor()

    def save_cursor(self, instance: str, cursor: Cursor, coverage: object) -> None:
        self._db.execute(
            "INSERT INTO cursors (instance, token) VALUES (?, ?) "
            "ON CONFLICT(instance) DO UPDATE SET token = excluded.token",
            (instance, cursor.token),
        )
        start = getattr(coverage, "start", None)
        end = getattr(coverage, "end", None)
        if start is not None and end is not None:
            self._db.execute(
                "INSERT INTO coverage (instance, start, end) VALUES (?, ?, ?)",
                (getattr(coverage, "connector_instance", instance), start.isoformat(), end.isoformat()),
            )
        self._db.commit()

    def coverage_windows(self, instance: str) -> list[tuple[datetime, datetime]]:
        """AD-35 — what the sweeper consults before it may say BROKEN."""
        return [
            (datetime.fromisoformat(s), datetime.fromisoformat(e))
            for s, e in self._db.execute(
                "SELECT start, end FROM coverage WHERE instance = ? ORDER BY id", (instance,)
            )
        ]

    def was_executed(self, idempotency_key: str) -> bool:
        """True only for a *settled* execution — an in-flight one proves nothing."""
        row = self._db.execute(
            "SELECT state FROM executed WHERE key = ?", (idempotency_key,)
        ).fetchone()
        return bool(row) and row[0] == SETTLED

    def begin_execution(self, idempotency_key: str, target: TargetRef) -> str:
        """AD-20 — claim the key BEFORE the provider is called.

        Returns "new" when the claim is ours to act on. Raises when a previous
        attempt claimed it and never settled: that attempt may have reached the
        provider, so re-executing is exactly the duplicate this rule exists to
        prevent.
        """
        row = self._db.execute(
            "SELECT state FROM executed WHERE key = ?", (idempotency_key,)
        ).fetchone()
        if row and row[0] == IN_FLIGHT:
            raise ReconciliationRequired(
                f"{idempotency_key} was claimed by an attempt that never settled. "
                f"Its outcome at the provider is unknown, so a retry is not safe "
                f"(AD-20). Reconcile against {target.lock_key} before proceeding."
            )
        self._db.execute(
            "INSERT INTO executed (key, lock_key, external_id, state, at) VALUES (?, ?, ?, ?, ?)",
            (idempotency_key, target.lock_key, None, IN_FLIGHT, self._at().isoformat()),
        )
        self._db.commit()
        return "new"

    def settle_execution(self, idempotency_key: str, external_id: str) -> None:
        """AD-20/AD-36 — record the outcome, which is what makes it recognisable.

        The settle time is stamped as well as the claim time. With one timestamp
        only, an execution that reached the provider and returned in a
        millisecond and one that hung for an hour before settling are the same
        row, so neither an operator reconciling AD-20's in-flight window nor a
        test can tell how long the claim was open.
        """
        self._db.execute(
            "UPDATE executed SET external_id = ?, state = ?, settled_at = ? WHERE key = ?",
            (external_id, SETTLED, self._at().isoformat(), idempotency_key),
        )
        self._db.commit()

    def record_execution(self, idempotency_key: str, target: TargetRef, external_id: str) -> None:
        """Claim and settle in one step — for callers with no crash window."""
        self.begin_execution(idempotency_key, target)
        self.settle_execution(idempotency_key, external_id)

    def stage_proposal(self, proposal: Proposal) -> None:
        self._db.execute(
            "INSERT INTO proposals (proposal_id, body, version, state) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(proposal_id) DO UPDATE SET body = excluded.body, "
            "version = excluded.version, state = excluded.state",
            (proposal.proposal_id, _dump_proposal(proposal), proposal.version, proposal.state.value),
        )
        self._db.commit()

    def load_proposal(self, proposal_id: str) -> Proposal:
        row = self._db.execute(
            "SELECT body FROM proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return _load_proposal(row[0])

    def transition_proposal(self, proposal_id: str, to: ProposalState, *, expected_version: int) -> Proposal:
        """CAS through the single writer, so two surfaces cannot both win (AD-37)."""
        current = self.load_proposal(proposal_id)
        moved = current.transition(to, expected_version=expected_version)
        self.stage_proposal(moved)
        return moved

    def executed_mutations(self) -> dict[str, tuple[str, str]]:
        """Read by normalization to mark harvested events as pm-ai's own (AD-36).

        An in-flight row is reported with no external id, which normalization
        reads as "we mutated here and cannot recognise the artifact" — so events
        in that scope resolve to UNKNOWN rather than being cleared as external.
        """
        return {
            key: (lock_key, external_id if external_id is not None else NO_EXTERNAL_ID)
            for key, lock_key, external_id in self._db.execute(
                "SELECT key, lock_key, external_id FROM executed"
            )
        }
