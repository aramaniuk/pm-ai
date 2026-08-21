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
from pm_ai.domain.storage_tiers import EVENT_LOG, OPERATIONAL_DB, Tier
from pm_ai.ports import ScopePathPort

# The application scope owns Tier 2 (AD-3). Resolved rather than remembered, so
# the mapping from artifact to scope stays in the one table that owns it.
APPLICATION = DataScope(ScopeKind.APPLICATION)

# AD-20 — an execution is recorded *before* the call and settled after, so a
# crash in between is a reconciliation task rather than a silent second write.
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


class ReconciliationRequired(RuntimeError):
    """AD-20 — a prior attempt reached the provider and its outcome is unknown.

    Retrying is not safe and neither is assuming success. The operator, or a
    provider-side idempotency token, resolves it.
    """


class StorageService:
    """Owns every write. Nothing else opens a file for writing (AD-5)."""

    tier_of_operational = Tier.OPERATIONAL

    def __init__(self, paths: ScopePathPort, *, now: Callable[[], datetime]) -> None:
        """Both dependencies are injected, and neither is optional.

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
        """
        self._paths = paths
        self._now = now
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

    def _segment(self, scope: DataScope, artifact: str, at: datetime) -> Path:
        """The open monthly segment of the `artifact` ledger in `scope`.

        The directory comes from the resolver, so a scope's event log lands in
        that scope's own tree rather than in a sibling directory named by
        flattening the scope to a string.
        """
        return self._paths.resolve(scope, artifact, create=True) / f"{at:%Y-%m}.md"

    def append_event_log(self, entry: str, *, scope: DataScope) -> None:
        at = self._at()
        with self._segment(scope, EVENT_LOG, at).open("a", encoding="utf-8") as fh:
            fh.write(entry.rstrip("\n") + "\n")

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
