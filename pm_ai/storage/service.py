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
inside the team's working tree, so whether git would publish a capture is a
question about that repository rather than about a directory boundary — and
being the single writer is what makes this the one place it can be asked before
anything is on disk. Since story 1n the project scope's `memory/` is asked the
same question, for the same reason: it is machine-local by declaration and it
lives inside a repository.

Asked of git, through `VcsPort`. `.importlinter` forbids `subprocess` here, which
is not an obstacle but the design: this module states the policy — refuse unless
git says the directory is excluded and untracked, refuse when git cannot be
reached at all — and `pm_ai.platform.vcs` runs the commands.
"""

from __future__ import annotations

import contextlib
import copy
import errno
import json
import logging
import logging.handlers
import os
import re
import sqlite3
import stat
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from pm_ai.domain import clocks
from pm_ai.domain.api_token import MAX_TOKEN_FILE_BYTES, check_api_token, symlinked
from pm_ai.domain.claims import ClaimHeld, ClaimReentered
from pm_ai.domain.disclosure import (
    DISCLOSURE_LEDGER_ARTIFACT,
    DISCLOSURE_LEDGER_SCOPE,
    DisclosureRecord,
    assert_writable,
    render_disclosure,
)
from pm_ai.domain.event_entries import (
    IMPLAUSIBLE,
    OCCURRED_AT_FLAG,
    UNKNOWN_VALUE,
    EventEntry,
    render_entry,
)
from pm_ai.domain.events import NormalizedEvent
from pm_ai.domain.harvest import Cursor, HarvestFailure, HarvestRecord, PersistResult
from pm_ai.domain.identity import DataScope, ScopeKind, SourceRef, TargetRef
from pm_ai.domain.lifecycle import CoverageWindow, ProposalState
from pm_ai.domain.payload_fields import encode_payload
from pm_ai.domain.proposals import Proposal
from pm_ai.domain.renders import RenderRecord
from pm_ai.domain.scope_model import CLAIM_SIDECAR_SUFFIX, DAEMON_LOCK
from pm_ai.domain.meetings import MEETING_RECORD_NAME
from pm_ai.domain.transcripts import MAX_INGEST_ATTEMPTS, TranscriptState, TranscriptStateKind
from pm_ai.domain.storage_tiers import (
    API_TOKEN,
    CAPTURES,
    CAPTURE_STAGING,
    EVENT_LOG,
    GITIGNORE_FILENAME,
    OPERATIONAL_DB,
    Tier,
    UnprotectedCaptureDir,
    assert_capture_dir_untracked,
    assert_is_collection,
    gitignore_rule_for,
    is_append_only,
    requires_git_exclusion,
    restricted_mode,
)
from pm_ai.domain.vcs import VcsUnavailable
from pm_ai.ports import (
    ArtifactBusy,
    CaptureAlreadyExists,
    ClaimPort,
    CryptoPort,
    DecryptionFailed,
    EmptyCapture,
    MalformedCaptureName,
    ScopePathPort,
    VcsPort,
)
from pm_ai.storage.crypto import (
    ENCLAVE_DIR_MODE,
    ENCRYPTED_FILE_MODE,
    LazyKeyCrypto,
    is_encrypted,
)

# The application scope owns Tier 2 (AD-3). Resolved rather than remembered, so
# the mapping from artifact to scope stays in the one table that owns it.
APPLICATION = DataScope(ScopeKind.APPLICATION)

# A capture filename's ceiling, in bytes. Well under the 255 every filesystem
# this daemon runs on allows, because the name is one component of a path that
# also carries a repository root and a scope tree.
CAPTURE_NAME_LIMIT = 128

# AD-20 — an execution is recorded *before* the call and settled after, so a
# crash in between is a reconciliation task rather than a silent second write.
IN_FLIGHT = "in_flight"
SETTLED = "settled"
# A skill that raised (story 11b): the call was made and did not return, so the
# key is settled as failed rather than left in flight — the attempt is over.
# Like an in-flight key it is never claimed again: whether the provider applied
# the change is unknown, and a retry is reconciliation, not a second call.
FAILED = "failed"

# The collections a member may be removed from, each with its own name rule
# (story 11b). Only `meetings/`: adoption re-files a record under another day.
_REMOVABLE = {"meetings/": MEETING_RECORD_NAME}
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
CREATE TABLE IF NOT EXISTS harvest_failures (
    instance    TEXT PRIMARY KEY,
    reason      TEXT NOT NULL,
    retryable   INTEGER NOT NULL,
    retry_after REAL,
    at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS harvest_runs (
    instance            TEXT PRIMARY KEY,
    last_attempt        TEXT NOT NULL,
    last_success        TEXT,
    previous_success    TEXT,
    transcript_refusals TEXT
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
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS transcript_state (
    scope       TEXT NOT NULL,
    meeting_id  TEXT NOT NULL,
    state       TEXT NOT NULL,
    reason      TEXT NOT NULL,
    retryable   INTEGER NOT NULL,
    retry_after REAL,
    at          TEXT NOT NULL,
    PRIMARY KEY (scope, meeting_id)
);
CREATE TABLE IF NOT EXISTS transcript_ingest_attempts (
    scope       TEXT NOT NULL,
    meeting_id  TEXT NOT NULL,
    attempts    INTEGER NOT NULL,
    PRIMARY KEY (scope, meeting_id)
);
CREATE TABLE IF NOT EXISTS renders (
    scope        TEXT PRIMARY KEY,
    last_success TEXT,
    last_failure TEXT,
    reason       TEXT,
    retryable    INTEGER
);
CREATE TABLE IF NOT EXISTS requests (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    scope      TEXT NOT NULL,
    state      TEXT NOT NULL,
    result     TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# Every table `_SCHEMA` declares, read off it rather than listed beside it, so
# the check for a store that still needs preparing cannot disagree with what
# preparing creates (story 4r).
_SCHEMA_TABLES = frozenset(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", _SCHEMA))

# What this code expects the operational store to look like. Bumped in the same
# commit that appends to `MIGRATIONS`, never separately: a version with no
# migration behind it stamps a store that was never changed, and a migration
# with no version behind it runs on every open.
SCHEMA_VERSION = 1

# Not bumped by story `8a`, which added the `harvest_failures` table, and the
# reason is the same one `_migrate`'s docstring gives for why it exists at all.
# `CREATE TABLE IF NOT EXISTS` is a no-op on a store that has the table and
# *creates* it on one that does not, and `_SCHEMA` runs on every open — so a new
# table is picked up by a version-1 store without a migration step. What that
# statement cannot do is add a column to a table that already exists, which is
# the case migration 1 was written for. A version bump with no migration behind
# it would stamp a store nothing changed. Story `4r`'s `requests` table rode in
# the same way, and for the same reason the version did not move; so did story
# `9a`'s `harvest_runs`, and story `9b`'s `renders`.

# Version 0 is the *unversioned era* — every store written before this story.
# It is not a shape: two stores can both be at 0 and differ, because the era had
# no way to record which. That is why migration 1 is the one migration allowed
# to inspect the schema before acting, and the last: from 1 onward each step
# knows exactly what its predecessor left.
UNVERSIONED = 0


def _m1_settled_at(db: sqlite3.Connection) -> None:
    """Add `executed.settled_at` (AD-20 — an execution is recorded, then settled).

    Conditional, uniquely. This replaces the `PRAGMA table_info` sniff that used
    to run on every open, and it inherits that sniff's problem exactly once:
    a version-0 store may or may not already have the column, and nothing
    recorded which. Every later migration starts from a known version and must
    act unconditionally, or it is not a migration but a second sniff.
    """
    columns = {row[1] for row in db.execute("PRAGMA table_info(executed)")}
    if "settled_at" not in columns:
        db.execute("ALTER TABLE executed ADD COLUMN settled_at TEXT")


# Ordered, ascending, contiguous from 1. Append only: editing a shipped entry
# changes what a store that already ran it believes about itself.
#
# A migration MUST NOT commit. The runner wraps each step in a savepoint and
# stamps the version inside it, so the change and its version land together or
# neither does. A step that commits releases that savepoint early, and a failure
# after it leaves the store half-migrated with a stamp claiming success — in the
# one tier no rebuild can repair.
MIGRATIONS: tuple[tuple[int, str, Callable[[sqlite3.Connection], None]], ...] = (
    (1, "add executed.settled_at", _m1_settled_at),
)


# `link` is not a filesystem-independent operation. exFAT and some network
# mounts refuse it; the errno differs by platform, so both are named rather than
# one assumed.
_LINK_UNSUPPORTED = frozenset({errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS})


def _refusals(stored: str | None) -> tuple[str, ...]:
    """`harvest_runs.transcript_refusals` read back: a JSON list of sentences, or none.

    A value that is not a list of strings — a hand edit, a row from a build
    that wrote something else — reads as one sentence saying so, never as none.
    """
    if not stored:
        return ()
    try:
        decoded = json.loads(stored)
    except ValueError:
        decoded = None
    if isinstance(decoded, list) and all(isinstance(item, str) for item in decoded):
        return tuple(decoded)
    return ("the recorded transcript refusals could not be read back",)


def _write_all(descriptor: int, payload: bytes) -> None:
    """Write every byte, because `os.write` may write fewer than asked.

    Its return value used to be discarded. A short write is rare on a regular
    file and not impossible, and for a sealed artifact the consequence is not a
    truncated file but an unreadable one: AES-GCM fails its tag rather than
    degrading, so the loss is total and silent until the next read.
    """
    written = 0
    while written < len(payload):
        progressed = os.write(descriptor, payload[written:])
        if progressed == 0:
            # POSIX permits a zero-byte result; looping on it would hang the
            # single writer forever, which is the one failure mode that neither
            # refuses nor succeeds.
            raise OSError(errno.EIO, f"os.write made no progress at byte {written}")
        written += progressed


_APPEND_OPEN_ATTEMPTS = 3


def _open_for_append(path: Path, creation_mode: int) -> tuple[int, bool]:
    """Open `path` for append, creating it exclusively if absent.

    Returns the descriptor and whether this call created the file. Creation and
    append are told apart by the kernel (`O_CREAT|O_EXCL`), not by a prior
    existence check. If the file vanishes between a refused exclusive create
    and the plain reopen, the create is tried again — at most
    `_APPEND_OPEN_ATTEMPTS` times, after which the `FileNotFoundError`
    propagates rather than looping on a directory something keeps emptying.
    """
    for attempt in range(_APPEND_OPEN_ATTEMPTS):
        try:
            return (
                os.open(
                    path,
                    os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL,
                    creation_mode,
                ),
                True,
            )
        except FileExistsError:
            pass
        try:
            return os.open(path, os.O_WRONLY | os.O_APPEND), False
        except FileNotFoundError:
            if attempt == _APPEND_OPEN_ATTEMPTS - 1:
                raise
    raise AssertionError("unreachable: the last attempt returns or raises")


def _mkdir_enclave(directory: Path) -> None:
    """Create `directory` and any missing ancestors, each at `ENCLAVE_DIR_MODE`.

    `mkdir(parents=True, mode=...)` applies the mode only to the last component;
    the ancestors it creates get the umask default, which for an enclave means a
    world-readable `private/` around 0700 leaves. Walked explicitly so every
    directory this write brings into existence is 0700 (story 1f). Directories
    that already exist are left alone — their mode is their owner's decision.
    """
    missing: list[Path] = []
    for candidate in (directory, *directory.parents):
        if candidate.exists():
            break
        missing.append(candidate)
    for candidate in reversed(missing):
        candidate.mkdir(mode=ENCLAVE_DIR_MODE, exist_ok=True)


def _fsync_dir(directory: Path) -> None:
    """Make a newly-visible name durable, not just its content.

    Best-effort: a directory `fsync` is unsupported on some filesystems, and
    failing a write that already succeeded would be the worse outcome.
    """
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover — directory we just wrote into
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


_SEGMENT_NAME = re.compile(r"^\d{4}-\d{2}\.md$")


def _segment_names(directory: Path) -> list[str]:
    """Every dated segment in `directory`, and nothing else.

    Matched against the naming rule rather than globbed as `*.md`: an editor
    leftover, a hand-written note or a `.bak` in the log directory must not be
    able to seal a month, and a `*.md` glob cannot tell them apart.
    """
    return [p.name for p in directory.iterdir() if _SEGMENT_NAME.match(p.name)]


_WRITER_OWNED_FIELDS = frozenset({"ingested_at", OCCURRED_AT_FLAG})
"""Fields the single writer sets, which no caller may supply.

Both are stamped onto the entry on the way in, and `dict(entry.fields)` resolves
a duplicate key to the *last* occurrence — so a caller passing either one would
override the writer rather than be overridden by it.
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
            # `None` for a proposal with no work-item target — a promise, or a
            # spoken command whose target the reference grammar refused (11b).
            "target": None if p.target is None else str(p.target),
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
        target=None if d.get("target") is None else TargetRef.parse(d["target"]),
        cites=SourceRef.parse(d["cites"]),
        created_at=datetime.fromisoformat(d["created_at"]),
        state=ProposalState(d["state"]),
        version=d["version"],
        ttl=timedelta(seconds=d["ttl_seconds"]),
    )


class ProposalNotFound(KeyError):
    """No staged proposal carries that id.

    Named rather than a bare `KeyError`, which is what this raised until
    2026-08-24 — the only built-in exception left in a module where every other
    failure has a type. A caller that wants to tell "this proposal expired or was
    never staged" from a dictionary miss somewhere inside the writer had no way
    to, and the two call for different responses: the first is a surface telling
    the PM the card is gone, the second is a defect.

    Subclasses `KeyError` deliberately, following `UnknownVerb` in
    `pm_ai.domain.lifecycle`: a registry miss *is* a key error, and inheriting it
    means adding the type cannot break a caller that already catches the general
    case.
    """


class AppendToSealedArtifact(RuntimeError):
    """An artifact declared encrypted was appended to, which cannot be done.

    Not a caller error to be worked around: it means a declaration and a write
    shape disagree, and the resolution is to change one of them.
    """


class SealedSegment(RuntimeError):
    """A write targeted a segment that is no longer the open one.

    `storage-contract.md` makes the event log "a directory of dated segments,
    exactly one open and appended to, earlier segments sealed and immutable",
    and that immutability is what lets compaction replace a whole sealed segment
    rather than rewrite entries in place. A late append into a summarised month
    would be deleted by the next compaction with nothing recording that it
    existed.

    Refused rather than redirected into the open segment: redirecting silently
    re-dates the record, and the filename is not what carries when the thing
    happened — `occurred_at` is.
    """


class AppendOnlyArtifact(RuntimeError):
    """A ledger was asked to be rewritten whole, which destroys its history.

    The mirror of `AppendToSealedArtifact`, guarding the other direction: that
    one refuses appending to what only replacement can write, this one refuses
    replacing what only appending may touch. The membership comes from
    `storage-contract.md`'s append set, spelled once in
    `pm_ai.domain.storage_tiers.is_append_only` — AD-5's static scan cannot see
    an artifact name that arrives as a runtime parameter, so the check is here,
    at the moment the name is known.
    """


class OperationalStoreUnavailable(RuntimeError):
    """Tier 2 could not be opened, and the daemon has no state without it.

    Raised in place of a bare `sqlite3.OperationalError`, whose message ("unable
    to open database file") names neither the path nor the reason — and the path
    is now resolved rather than passed in, so the operator cannot read it off the
    call site either.
    """


class OperationalStoreBusy(OperationalStoreUnavailable, sqlite3.OperationalError):
    """Another connection held `operational.db`'s write lock past the busy wait (4e).

    The daemon and a thin client can meet on the store: in this wave the CLI
    still writes directly rather than through the daemon (AD-5). The connection
    waits for a busy database for the standard library's default — pm-ai sets
    no timeout of its own — and WAL mode means readers never wait for the
    writer. A write refused as busy — after the wait, or at once because its
    snapshot went stale, which WAL refuses without waiting — is refused with
    this, saying which of the two was measured, naming the store and the wait
    that was configured, after its open transaction is rolled back — rather than surfacing as a bare "database is locked" that
    names nothing, and rather than waiting again.

    A subclass of `OperationalStoreUnavailable`, so every caller that already
    catches an unusable store sees it, and of `sqlite3.OperationalError`, so
    every caller that catches the library's error does too.
    """


_BUSY = "database is locked"  # SQLITE_BUSY: the busy wait ran out
_LOCKED = "database table is locked"  # SQLITE_LOCKED: no wait applies
_NO_WAIT_MS = 10  # below this a busy refusal did not wait, whatever was configured


class _OperationalConnection(sqlite3.Connection):
    """The operational store's connection, with a busy refusal that names the file.

    Built by `sqlite3.connect(..., factory=...)` and otherwise the library's own
    connection: the busy wait is the library's default, untouched. The three
    methods the writer calls are the ones overridden, because they are where the
    lock is taken — a cursor's `fetchone` reads rows a statement already ran.
    """

    def __init__(self, database: str | os.PathLike[str], *args: object, **kwargs: object) -> None:
        super().__init__(database, *args, **kwargs)  # type: ignore[arg-type]
        self.store = os.fspath(database)
        # The daemon's writer connection (story 4r, AD-5): one rule for every
        # write on it. The first statement a claimed step sends opens
        # `BEGIN IMMEDIATE` with the schema-version check first; `commit()`
        # inside a step is deferred to the step's end, where the writer
        # commits what returned and rolls back what raised.
        self.daemon_mode = False
        self.in_step = False

    def _open_for_step(self, sql: object) -> None:
        """`BEGIN IMMEDIATE` and the version check, before a statement that needs them."""
        if not self.daemon_mode or self.in_transaction:
            return
        if isinstance(sql, str) and sql.lstrip().upper().startswith(_CONTROL):
            return
        started = time.monotonic()
        try:
            super().execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            refused = self._busy(exc, started)
            if refused is exc:
                raise
            raise refused from exc
        row = super().execute("SELECT version FROM schema_version").fetchone()
        found = UNVERSIONED if row is None else int(row[0])
        if found != SCHEMA_VERSION:
            super().execute("ROLLBACK")
            raise schema_moved(found)

    def _busy(self, exc: sqlite3.OperationalError, started: float) -> sqlite3.OperationalError:
        message = str(exc).lower()
        if _BUSY not in message and _LOCKED not in message:
            return exc
        waited = (time.monotonic() - started) * 1000
        rolled_back = self._roll_back()
        undone = (
            "its open transaction was rolled back, so nothing from it was kept"
            if rolled_back
            else "the statement was not applied"
        )
        if _LOCKED in message:
            return OperationalStoreBusy(
                f"a table in the operational store at {self.store} is locked by "
                f"another statement on the same connection ({exc}); {undone}. "
                f"This is not a busy wait running out — no wait applies to it."
            )
        # Said as measured. Under WAL a transaction whose snapshot went stale
        # is refused busy at once, with no wait at all, so the wait is never
        # assumed from the setting.
        when = (
            "at once, without waiting"
            if waited < _NO_WAIT_MS
            else f"after waiting {waited:.0f} ms"
        )
        return OperationalStoreBusy(
            f"the operational store at {self.store} was refused as busy {when} "
            f"— another connection holds its write lock ({exc}; busy wait "
            f"configured: {self._wait()}); {undone}. Run the command again "
            f"once the other one finishes."
        )

    def _wait(self) -> str:
        try:
            millis = int(super().execute("PRAGMA busy_timeout").fetchone()[0])
        except sqlite3.Error:
            return "the configured busy wait"
        return f"{millis} ms"

    def _roll_back(self) -> bool:
        if not self.in_transaction:
            return False
        try:
            if self.autocommit is True:
                # The daemon's writer connection (story 4r): `rollback()` does
                # nothing in autocommit mode, so the transaction a claimed step
                # opened with `BEGIN IMMEDIATE` is ended by statement.
                super().execute("ROLLBACK")
            else:
                super().rollback()
        except sqlite3.Error:
            return False
        return True

    def execute(self, *args: object, **kwargs: object) -> sqlite3.Cursor:  # type: ignore[override]
        self._open_for_step(args[0] if args else kwargs.get("sql"))
        started = time.monotonic()
        try:
            return super().execute(*args, **kwargs)  # type: ignore[arg-type]
        except sqlite3.OperationalError as exc:
            refused = self._busy(exc, started)
            if refused is exc:
                raise
            raise refused from exc

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        started = time.monotonic()
        try:
            return super().executescript(sql_script)
        except sqlite3.OperationalError as exc:
            refused = self._busy(exc, started)
            if refused is exc:
                raise
            raise refused from exc

    def commit(self) -> None:
        if self.daemon_mode:
            # Deferred inside a claimed step: the step's rows are saved
            # together when it returns. Outside one, ended here.
            if not self.in_step and self.in_transaction:
                self.execute("COMMIT")
            return
        started = time.monotonic()
        try:
            super().commit()
        except sqlite3.OperationalError as exc:
            refused = self._busy(exc, started)
            if refused is exc:
                raise
            raise refused from exc


    def rollback(self) -> None:
        if self.daemon_mode:
            # `rollback()` does nothing on an autocommit connection, so the
            # step's transaction is ended by statement — the whole step.
            if self.in_transaction:
                super().execute("ROLLBACK")
            return
        super().rollback()


_CONTROL = ("BEGIN", "COMMIT", "ROLLBACK", "END", "SAVEPOINT", "RELEASE", "PRAGMA")
"""Statements that manage a transaction themselves, and so never open one."""


class SchemaVersionTooNew(RuntimeError):
    """The store was written by a later version of pm-ai than this one.

    Refusing is the only response that cannot make things worse. A later version
    may have added columns this code does not know about; opening the store and
    proceeding writes rows it will then misread, and the corruption surfaces long
    after the mistake — in Tier 2, which no rebuild reconstructs (AD-3). There is
    no automatic downgrade, because a downgrade would mean deciding what to do
    with data this code cannot interpret.
    """


class SchemaVersionChanged(RuntimeError):
    """`operational.db`'s stamp is no longer the version the daemon opened it at (4r).

    The family the daemon halts on. `found` is the version read; it is moved on
    (`SchemaVersionMoved`) or older or gone (`SchemaVersionRegressed`).
    """

    found: int


class SchemaVersionMoved(SchemaVersionTooNew, SchemaVersionChanged):
    """Another process moved `operational.db` past the version this daemon knows (4r).

    Raised by the schema-version check that is the first statement inside every
    claimed step's `BEGIN IMMEDIATE`, after the step's transaction is rolled
    back — so nothing of the step was written. The daemon stops on it and
    writes nothing more to the store (AD-5). `found` is the version read.
    """

    def __init__(self, found: int) -> None:
        super().__init__(
            f"the operational store is now stamped schema version {found} and "
            f"this daemon knows version {SCHEMA_VERSION}: another pm-ai moved it "
            f"while the daemon ran. The daemon stopped and wrote nothing more to "
            f"it; upgrade pm-ai and start it again."
        )
        self.found = found


class SchemaVersionRegressed(SchemaVersionChanged):
    """`operational.db` is now *older* than the version the daemon opened (4r).

    Its stamp is lower, or gone: the store was replaced or rewritten under the
    running daemon — a restored copy, a hand edit. Not a reason to upgrade
    pm-ai, so it says so in its own words.
    """

    def __init__(self, found: int) -> None:
        super().__init__(
            f"the operational store is now stamped schema version {found} — "
            f"older than the version {SCHEMA_VERSION} this daemon opened it at, "
            f"or with no stamp at all — so it was replaced or rewritten while "
            f"the daemon ran (a restored copy, a hand edit). The daemon stopped "
            f"and wrote nothing more to it; check ~/.pm-ai/private/operational.db "
            f"and start the daemon again.",
        )
        self.found = found


class OperationalStoreStuck(OperationalStoreUnavailable):
    """A claimed step's transaction could not be rolled back (story 4r).

    The writer's connection is left inside a transaction nothing can end, so
    every later step would fail too; the writer halts on this, naming it.
    """


def schema_moved(found: int) -> SchemaVersionChanged:
    """The refusal for a store whose stamp is no longer `SCHEMA_VERSION`."""
    if found < SCHEMA_VERSION:
        return SchemaVersionRegressed(found)
    return SchemaVersionMoved(found)


DAEMON_BUSY_WAIT_SECONDS = 0.1
"""The daemon's own busy wait on `operational.db` (AD-5, story 4r).

At most 100 ms: a claimed step is short and never waits, so the writer thread
meets a write lock another process holds with a refusal rather than a stall.
The commands that still write for themselves keep the standard library's five
seconds.
"""

REQUEST_UNFINISHED = ("queued", "running")
"""The `requests` states a restart or a shutdown still has to settle (story 4r)."""


@dataclass(frozen=True, slots=True)
class RequestRow:
    """One row of `requests`: a job the daemon acknowledged (AD-20, story 4r).

    `result` is the JSON text the job settled with — the command's output and
    exit code, or pm-ai's own sentence and the error's type name — or `None`
    while it is unsettled.
    """

    id: str
    kind: str
    scope: str
    state: str
    result: str | None
    created_at: str
    updated_at: str


_REQUEST_COLUMNS = "id, kind, scope, state, result, created_at, updated_at"

MIGRATION_CLAIM_ATTEMPTS = 40
"""How many times `operational.db`'s own claim is tried before opening is refused."""

MIGRATION_CLAIM_INTERVAL = 0.05
"""Seconds between tries: about two seconds in all, the sealed store's budget."""


class NonUtcClock(ValueError):
    """The injected clock returned a naive or non-UTC datetime.

    Both are silent corruption rather than an error: the monthly segment
    filename is formatted from this value, so an offset clock files an entry
    under the wrong month at a boundary, and a naive one raises `TypeError` later
    when it is compared against the aware timestamps every other producer emits.
    """


@dataclass(frozen=True, slots=True)
class GitExclusion:
    """Git's answer that a write to `artifact` in `scope` is excluded (AD-43, 9b).

    Asked in a bounded step on a worker and handed with the bytes to the
    claimed step, which then never calls `git` itself. Minted only by
    `StorageService.ask_git_exclusion`; one built anywhere else is refused.
    """

    scope: DataScope
    artifact: str
    _asked_by: object = field(default=None, repr=False, compare=False)
    """The `StorageService` that asked, which alone accepts it."""
    _target: Path | None = field(default=None, repr=False, compare=False)
    """The path git was asked about, under that service's root."""


class GitNotAsked(RuntimeError):
    """A write handed an exclusion answer that `ask_git_exclusion` did not give."""


def _assert_aware_utc(at: datetime, *, name: str) -> None:
    """Refuse an instant to be stored that is not aware UTC: stored text compares
    in time order only when every row is written in one offset."""
    if at.tzinfo is None or at.utcoffset() != timedelta(0):
        raise NonUtcClock(f"{name}={at!r} is not aware UTC, so it was not recorded.")


class ReconciliationRequired(RuntimeError):
    """AD-20 — a prior attempt reached the provider and its outcome is unknown.

    Retrying is not safe and neither is assuming success. The operator, or a
    provider-side idempotency token, resolves it.
    """


class CoverageInstanceMismatch(ValueError):
    """A coverage window was offered under one instance and saved under another.

    Coverage rows are keyed on `CoverageWindow.connector_instance` and read back
    by instance, so the two disagreeing is not a cosmetic slip: the window lands
    where nothing looks for it, the instance that harvested reads as uncovered,
    and an absence assertion over the intended key passes with a real row on
    disk. Refused at the write, where both names are still in scope.
    """


# AD-24's rotating diagnostic log: the `logs/` collection in the application
# scope, a few files of a few megabytes, oldest dropped first.
DIAGNOSTIC_LOGS = "logs/"
_DIAGNOSTIC_LOG_BYTES = 5 * 1024 * 1024
_DIAGNOSTIC_LOG_BACKUPS = 3


# The artifacts this thread holds a claim on, by canonical path, across every
# writer in the process (AD-5, story 8q). Per thread: it cannot see asyncio
# tasks that share one thread. Checked before the port is asked, so
# a re-entry is a programming error whichever `ClaimPort` is wired — the kernel
# lock refuses it too, and an in-memory one would otherwise answer "busy".
_CLAIMED_HERE = threading.local()


_RECORD_RENDER = (
    "INSERT INTO renders (scope, last_success, last_failure, reason) "
    "VALUES (?, ?, NULL, NULL) "
    "ON CONFLICT(scope) DO UPDATE SET "
    "  last_success = excluded.last_success, "
    "  last_failure = NULL, reason = NULL, retryable = NULL"
)
"""A successful render: its instant, and no failure. One statement (AD-5, 9b)."""

_RECORD_FAILURE = (
    "INSERT INTO renders (scope, last_success, last_failure, reason, retryable) "
    "VALUES (?, NULL, ?, ?, ?) "
    "ON CONFLICT(scope) DO UPDATE SET "
    "  last_failure = excluded.last_failure, reason = excluded.reason, "
    "  retryable = excluded.retryable"
)
"""A failed render: when, why, whether waiting clears it. One statement (9b)."""

_storage_log = logging.getLogger(__name__)


def _instant_or_none(raw: object, *, scope: str, column: str) -> datetime | None:
    """A stored instant, or `None` (logged) when it cannot be read as an aware one."""
    try:
        at: datetime | None = datetime.fromisoformat(str(raw))
    except ValueError:
        at = None
    if at is None or at.tzinfo is None:
        _storage_log.warning(
            "the renders row for %s holds a %s that is not an instant; it is skipped",
            scope,
            column,
        )
        return None
    return at


def render_records_at(store: Path) -> tuple[RenderRecord, ...]:
    """Every `renders` row in the operational store at `store`, read-only (9b).

    No store, or no table yet, is no records. A row whose scope does not parse,
    or whose instant cannot be read, is skipped and logged, so one bad row
    never fails a render or `doctor`'s line.
    """
    if not store.exists():
        return ()
    reader = sqlite3.connect(f"{store.as_uri()}?mode=ro", uri=True)
    try:
        try:
            rows = reader.execute(
                "SELECT scope, last_success, last_failure, reason, retryable "
                "FROM renders ORDER BY scope"
            ).fetchall()
        except sqlite3.OperationalError as missing:
            if "no such table" in str(missing):
                return ()
            raise
    finally:
        reader.close()
    records = []
    for scope, success, failure, reason, retryable in rows:
        try:
            parsed = DataScope.parse(str(scope))
        except ValueError:
            _storage_log.warning("a renders row names no scope pm-ai knows; it is skipped")
            continue
        # An unreadable instant drops that column alone (logged), so a valid
        # failure and its reason still show beside a broken success.
        last_success = (
            _instant_or_none(success, scope=scope, column="last_success") if success else None
        )
        last_failure = (
            _instant_or_none(failure, scope=scope, column="last_failure") if failure else None
        )
        records.append(
            RenderRecord(
                parsed,
                last_success,
                last_failure,
                reason if last_failure is not None else None,
                retryable=bool(retryable) and last_failure is not None,
            )
        )
    return tuple(records)


def _canonical(target: Path) -> Path:
    """`target` keyed as the platform lock keys it: its directory resolved."""
    return target.parent.resolve() / target.name


def _claimed_here() -> set[Path]:
    held: set[Path] | None = getattr(_CLAIMED_HERE, "paths", None)
    if held is None:
        held = set()
        _CLAIMED_HERE.paths = held
    return held


class StorageService:
    """Owns every write. Nothing else opens a file for writing (AD-5)."""

    tier_of_operational = Tier.OPERATIONAL

    def __init__(
        self,
        paths: ScopePathPort,
        *,
        now: Callable[[], datetime],
        vcs: VcsPort,
        crypto: CryptoPort,
        claims: ClaimPort,
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
        process (`GitLabConnectorAdapter.now` has one too). The monthly segment filename
        derives from this clock, which is what made the three internal reads this
        replaces untestable: a test could not name the file it was about to
        assert on.

        `vcs` is required for the same reason as `now`, and more sharply: a
        default would have to be either the real adapter — which this package may
        not import, `pm_ai.platform` being an independent sibling — or a stand-in
        that answers without asking git. The second is the leak this dependency
        exists to prevent, so there is no default and a caller that forgets it
        gets a `TypeError` at construction rather than an unprotected write later.

        `claims` likewise (story `1r`): the cross-process lock `exclusive` takes
        is an OS API, so its adapter lives in `pm_ai.platform` and arrives from
        the composition root. A default that did not lock would make every
        read-modify-write decorative.
        """
        self._paths = paths
        self._claims = claims
        self._markers_reported: set[Path] = set()
        self._crypto = crypto
        self._git_checked: set[tuple[object, ...]] = set()
        self._now = now
        self._vcs = vcs
        # Tier 2 is its own file, in the application scope's enclave and outside
        # every scope's Markdown tree. `reindex` targets Tier 3 and therefore
        # cannot reach this, which is the structural guarantee AD-3 asks for.
        # The enclave first, by story 1f's rule: on a fresh home this is the
        # first write under `~/.pm-ai`, and `resolve(create=True)` would make
        # `~/.pm-ai` and `private/` at the umask's mode (story 9a).
        _mkdir_enclave(Path(paths.resolve(APPLICATION, OPERATIONAL_DB)).parent)
        store = paths.resolve(APPLICATION, OPERATIONAL_DB, create=True)
        self._store = Path(store)
        try:
            # No `timeout=`: the standard library's five-second busy wait is
            # the one pm-ai relies on when the daemon and a thin client meet on
            # this file (story 4e), and `_OperationalConnection` is what turns
            # waiting it out into a refusal that names the store.
            self._db = sqlite3.connect(
                store, check_same_thread=False, factory=_OperationalConnection
            )
        except sqlite3.Error as exc:
            raise OperationalStoreUnavailable(
                f"could not open the operational store at {store}: {exc}. Tier 2 "
                f"holds the job queue, the connector cursors, the executed-key "
                f"ledger, and the dedup set, and none of it is rebuildable "
                f"(AD-3) — so the daemon must not start without it."
            ) from exc
        # From here the connection exists, so every refusal below — a schema
        # from a later version, a failing migration — closes it before leaving.
        # A refused open used to leak the handle and its WAL sidecars: the
        # process exit reclaimed them eventually, but a caller that catches the
        # refusal and retries held one leaked connection per attempt.
        try:
            self._db.execute("PRAGMA journal_mode=WAL")  # AD-5 — sole writer, WAL
            # Asked BEFORE `_SCHEMA` runs, because afterwards every store looks
            # created-by-us. It is the only way to tell a brand-new file from one
            # written in the unversioned era, and the two need opposite handling:
            # a new store is stamped current and migrates nothing, an old one is
            # version 0 and migrates from there.
            #
            # "Any table at all", not a named one. Naming `cursors` looked
            # equivalent and was not: a store from the unversioned era holds
            # whatever subset of tables the code of its day created, and the
            # pre-written legacy fixture has `executed` alone. That store would
            # have been read as brand new, stamped current, and never migrated —
            # the first settle failing on a missing column, which is the exact
            # failure this story exists to prevent.
            preexisting = self._db.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone() is not None
            if self._needs_preparing():
                # Migrated only under the store's own claim (AD-5, story 4r), in
                # every process that opens it: two processes preparing one
                # store at once must not interleave a schema step, and the
                # daemon's version check inside each claimed step is what
                # keeps a migration from landing under a daemon that writes
                # the old shape.
                with self._migration_claim():
                    self._db.executescript(_SCHEMA)
                    self._db.commit()
                    self._migrate(preexisting=preexisting)
            else:
                # Nothing to change, so nothing to claim: this only reads the
                # stamp, and refuses a store a later pm-ai wrote.
                self._migrate(preexisting=preexisting)
        except OperationalStoreBusy:
            # Busy is not "could not prepare": the store is fine and another
            # connection is writing to it. Its own sentence already names the file.
            self._db.close()
            raise
        except sqlite3.Error as exc:
            self._db.close()
            # Worded as what failed — preparing, not opening. A migration that
            # raises `sqlite3.Error` used to be reported as "could not open",
            # sending the operator to permissions and paths when the store
            # opened fine and a schema step is what refused.
            raise OperationalStoreUnavailable(
                f"opened the operational store at {store} but could not prepare "
                f"its schema: {exc}. Tier 2 is never rebuilt (AD-3), so the "
                f"daemon must not start against a store it could not bring to "
                f"the expected version."
            ) from exc
        except BaseException:
            self._db.close()
            raise

    def _migrate(self, *, preexisting: bool) -> None:
        """Bring the store forward to `SCHEMA_VERSION`, or refuse to open it.

        Runs at construction, after the connection is open and before any other
        statement, so nothing can read a half-migrated schema.

        Replaces a `PRAGMA table_info` sniff that worked exactly once per column,
        could not express an ordered sequence, and could not detect a store
        written by a *later* version at all. `CREATE TABLE IF NOT EXISTS` is a
        no-op on an existing store, so a schema that grows a column still fails
        on the first write against a Tier-2 file that predates it — and Tier 2 is
        never rebuilt, so "delete it and start again" is not the fix.
        """
        row = self._db.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            # A store with no stamp is either brand new — created by `_SCHEMA`
            # moments ago, therefore already current — or one from the
            # unversioned era, which is version 0 and has migrations to run.
            current = UNVERSIONED if preexisting else SCHEMA_VERSION
            self._db.execute("INSERT INTO schema_version (version) VALUES (?)", (current,))
            self._db.commit()
        else:
            current = int(row[0])

        if current > SCHEMA_VERSION:
            raise SchemaVersionTooNew(
                f"the operational store is stamped schema version {current} and "
                f"this pm-ai expects {SCHEMA_VERSION}. It was written by a later "
                f"version, which may have added columns this code would misread. "
                f"Refusing to open it: upgrade pm-ai, or restore a Tier-2 backup "
                f"written by this version — and note that restoring an older copy "
                f"opens a re-execution window, because mutations performed after "
                f"the backup are missing from the executed-key ledger."
            )

        for version, name, apply in MIGRATIONS:
            if version <= current:
                continue
            # One savepoint per migration, so a failure leaves the store at the
            # version before *that* step rather than partly through it. Steps
            # that already succeeded are committed and stay: forward-only means
            # the next open resumes rather than restarts.
            self._db.execute("SAVEPOINT pm_ai_migration")
            try:
                apply(self._db)
                self._db.execute("UPDATE schema_version SET version = ?", (version,))
            except BaseException:
                self._db.execute("ROLLBACK TO pm_ai_migration")
                self._db.execute("RELEASE pm_ai_migration")
                raise
            self._db.execute("RELEASE pm_ai_migration")
            self._db.commit()

    def _needs_preparing(self) -> bool:
        """Whether opening this store would change it: a table missing, or a stamp behind.

        Read without a claim, because it only reads; a store that needs nothing
        is opened without one, so two commands opening a prepared store never
        meet on its claim.
        """
        tables = {
            row[0]
            for row in self._db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not _SCHEMA_TABLES <= tables:
            return True
        row = self._db.execute("SELECT version FROM schema_version").fetchone()
        return row is None or int(row[0]) < SCHEMA_VERSION

    @contextlib.contextmanager
    def _migration_claim(self) -> Iterator[None]:
        """`operational.db`'s own claim, for preparing it (AD-5, story 4r).

        Only at open: a connection the daemon has adopted is refused, so the
        retry below can never run inside a claimed step on the running writer.
        Tried briefly rather than once — another process holding it is
        preparing the same store, which takes milliseconds, and refusing to
        open over it would fail a command for having started at the same
        moment as another. Only taking the claim is retried; past the attempts
        the open is refused by name, with nothing changed.
        """
        if getattr(self._db, "daemon_mode", False):
            raise RuntimeError(
                "operational.db is prepared only when it is opened, never on the "
                "daemon's writer connection; that is a bug in pm-ai."
            )
        held = contextlib.ExitStack()
        for attempt in range(MIGRATION_CLAIM_ATTEMPTS):
            try:
                held.enter_context(self.exclusive(scope=APPLICATION, artifact=OPERATIONAL_DB))
                break
            except ArtifactBusy:
                if attempt + 1 < MIGRATION_CLAIM_ATTEMPTS:
                    time.sleep(MIGRATION_CLAIM_INTERVAL)
        else:
            raise OperationalStoreUnavailable(
                f"the operational store at {self._store} is being prepared by "
                f"another pm-ai process, which kept its claim for "
                f"{MIGRATION_CLAIM_ATTEMPTS * MIGRATION_CLAIM_INTERVAL:g} s; nothing "
                f"was changed. Run the command again."
            )
        with held:
            yield

    # ── The daemon's writer connection (AD-5, story 4r) ──────────────────────

    def adopt_daemon_connection(self) -> None:
        """Reopen `operational.db` as the daemon's writer connection.

        Called once, on the daemon's writer thread, before anything else is
        written. The connection runs with `autocommit=True` and a 100 ms busy
        wait, and one rule holds for every write on it, the older writers'
        included: the first statement of a claimed step opens `BEGIN IMMEDIATE`
        with the schema-version check first, and the step's end
        (`claimed_step`) commits it when the step returned and rolls it back
        when it raised. So a busy refusal undoes that step and nothing else,
        and a store another process migrated is never written. The schema was
        prepared when this writer was built.
        """
        replacement = sqlite3.connect(
            self._store,
            check_same_thread=False,
            factory=_OperationalConnection,
            autocommit=True,
            timeout=DAEMON_BUSY_WAIT_SECONDS,
        )
        replacement.daemon_mode = True
        previous, self._db = self._db, replacement
        previous.close()

    @contextlib.contextmanager
    def claimed_step(self) -> Iterator[None]:
        """One claimed step's bounds on `operational.db` (AD-5, story 4r).

        The daemon's writer thread runs every step inside this. Whatever the
        step wrote is committed when it returns and rolled back when it raises,
        as one transaction; a step that never touched the store opens none. A
        rollback that fails leaves the connection inside a transaction nothing
        can end, which is raised as `OperationalStoreStuck` for the writer to
        halt on. Outside the daemon's connection, or nested, it does nothing.
        """
        db = self._db
        if not isinstance(db, _OperationalConnection) or not db.daemon_mode or db.in_step:
            yield
            return
        db.in_step = True
        returned = False
        try:
            yield
            returned = True
        finally:
            db.in_step = False
            if db.in_transaction:
                if returned:
                    # Busy here is refused by name, with the step undone; any
                    # failed commit is rolled back, so the next step starts
                    # with its own `BEGIN IMMEDIATE` and version check rather
                    # than joining these writes.
                    try:
                        db.execute("COMMIT")
                    except BaseException:
                        try:
                            db.rollback()
                        except sqlite3.Error as stuck:
                            raise OperationalStoreStuck(
                                f"a claimed step's commit on the operational store "
                                f"at {self._store} failed and its transaction could "
                                f"not be rolled back ({stuck}); the writer stops."
                            ) from stuck
                        if db.in_transaction:
                            raise OperationalStoreStuck(
                                f"a claimed step's commit on the operational store "
                                f"at {self._store} failed and left a transaction "
                                f"that would not end; the writer stops."
                            ) from None
                        raise
                else:
                    try:
                        db.rollback()
                    except sqlite3.Error as stuck:
                        raise OperationalStoreStuck(
                            f"a claimed step on the operational store at "
                            f"{self._store} failed and its transaction could not "
                            f"be rolled back ({stuck}); every later step would "
                            f"fail the same way, so the writer stops."
                        ) from stuck
            if db.in_transaction:
                raise OperationalStoreStuck(
                    f"a claimed step on the operational store at {self._store} "
                    f"left a transaction that would not end; the writer stops."
                )

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """One writer call's statements: all of them, or none (AD-5).

        On the daemon's connection the first statement opens the step's
        `BEGIN IMMEDIATE` with the version check first; a raise rolls back,
        and a return commits — at once outside a claimed step, at the step's
        end inside one. On a command's own connection the library's implicit
        transaction does the same job, with the version checked first.
        """
        db = self._db
        try:
            if not getattr(db, "daemon_mode", False):
                self._check_version(db)
            yield db
        except BaseException:
            db.rollback()
            raise
        db.commit()

    @staticmethod
    def _check_version(db: sqlite3.Connection) -> None:
        row = db.execute("SELECT version FROM schema_version").fetchone()
        found = UNVERSIONED if row is None else int(row[0])
        if found != SCHEMA_VERSION:
            raise schema_moved(found)

    @contextlib.contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection]:
        """A read-only connection of its own, for a plain read off any thread.

        A plain read is not a writer call (AD-5): it never shares the writer's
        connection, so it can neither see a step's uncommitted rows nor be
        undone by a step's rollback.
        """
        reader = sqlite3.connect(f"{self._store.as_uri()}?mode=ro", uri=True)
        try:
            yield reader
        finally:
            reader.close()

    # ── `requests`: the jobs the daemon acknowledged (AD-20, story 4r) ───────

    def create_request(
        self, *, job_id: str, kind: str, scope: str, deduplicate: bool = True
    ) -> tuple[RequestRow, bool]:
        """The job's row, written `queued` — or the unfinished one it duplicates.

        One job per kind and scope at a time: when a row of this kind and scope
        is still `queued` or `running`, that row is returned with `False` and
        nothing is written — unless `deduplicate` is off, for a job whose second
        run is not the same job (`project add`, `transcript drop`). Otherwise the
        new row is inserted and returned with `True`. Both in one transaction, so no second row can land between the
        look and the insert. `ArtifactBusy` and `OperationalStoreBusy` pass
        through: the caller answers busy and no row exists.
        """
        with self._transaction() as db:
            existing = None if not deduplicate else db.execute(
                f"SELECT {_REQUEST_COLUMNS} FROM requests "
                f"WHERE kind = ? AND scope = ? AND state IN (?, ?) "
                f"ORDER BY created_at, id LIMIT 1",
                (kind, scope, *REQUEST_UNFINISHED),
            ).fetchone()
            if existing is not None:
                return RequestRow(*existing), False
            at = self._at().isoformat()
            db.execute(
                f"INSERT INTO requests ({_REQUEST_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, kind, scope, REQUEST_UNFINISHED[0], None, at, at),
            )
        return RequestRow(job_id, kind, scope, REQUEST_UNFINISHED[0], None, at, at), True

    def move_request(
        self, job_id: str, *, expected: str, to: str, result: str | None = None
    ) -> RequestRow | None:
        """Move one job from `expected` to `to`, in one statement; the row it wrote,
        or `None` when overtaken.

        Applies only to the state it expects to find, and its count of changed
        rows is checked: zero means another step got there first — a job
        finishing late never overwrites `failed`.
        """
        with self._transaction() as db:
            moved = db.execute(
                "UPDATE requests SET state = ?, result = COALESCE(?, result), "
                f"updated_at = ? WHERE id = ? AND state = ? RETURNING {_REQUEST_COLUMNS}",
                (to, result, self._at().isoformat(), job_id, expected),
            ).fetchone()
            return None if moved is None else RequestRow(*moved)

    def fail_unfinished_requests(
        self, reason: Callable[[RequestRow], str]
    ) -> tuple[RequestRow, ...]:
        """Mark every `queued` or `running` row `failed`, with `reason(row)` as its result.

        The restart pass and the shutdown pass. Each row moves by its own
        conditional statement, inside one transaction; the rows it wrote are
        returned.
        """
        failed: list[RequestRow] = []
        with self._transaction() as db:
            rows = [
                RequestRow(*row)
                for row in db.execute(
                    f"SELECT {_REQUEST_COLUMNS} FROM requests WHERE state IN (?, ?) "
                    f"ORDER BY created_at, id",
                    REQUEST_UNFINISHED,
                ).fetchall()
            ]
            at = self._at().isoformat()
            for row in rows:
                settled = db.execute(
                    "UPDATE requests SET state = ?, result = ?, updated_at = ? "
                    f"WHERE id = ? AND state = ? RETURNING {_REQUEST_COLUMNS}",
                    ("failed", reason(row), at, row.id, row.state),
                ).fetchone()
                if settled is not None:
                    failed.append(RequestRow(*settled))
        return tuple(failed)

    def unfinished_request_ids(self) -> tuple[str, ...]:
        """Every job still `queued` or `running`, on a read-only connection of its own."""
        with self._reader() as reader:
            return tuple(
                row[0]
                for row in reader.execute(
                    "SELECT id FROM requests WHERE state IN (?, ?) ORDER BY created_at, id",
                    REQUEST_UNFINISHED,
                )
            )

    def prune_requests(self, *, older_than: timedelta) -> int:
        """Remove every row created more than `older_than` ago; how many went."""
        cutoff = (self._at() - older_than).isoformat()
        with self._transaction() as db:
            return db.execute(
                "DELETE FROM requests WHERE created_at < ?", (cutoff,)
            ).rowcount

    def read_request(self, job_id: str) -> RequestRow | None:
        """One job's row, on a read-only connection of its own; `None` when unknown."""
        with self._reader() as reader:
            row = reader.execute(
                f"SELECT {_REQUEST_COLUMNS} FROM requests WHERE id = ?", (job_id,)
            ).fetchone()
        return None if row is None else RequestRow(*row)

    def diagnostic_lines(self, name: str) -> tuple[str, ...]:
        """Every line of `~/.pm-ai/logs/<name>` and its rotations, oldest file first.

        A plain read (AD-24's diagnostic log is outside the tier model): the
        daemon's restart pass asks it which job a schema-version stop named.
        Absent files contribute nothing.
        """
        directory = self._paths.resolve(APPLICATION, DIAGNOSTIC_LOGS)
        base = directory / _capture_name(name)
        files = [
            base.with_name(f"{base.name}.{n}")
            for n in range(_DIAGNOSTIC_LOG_BACKUPS, 0, -1)
        ] + [base]
        lines: list[str] = []
        for path in files:
            try:
                lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
            except FileNotFoundError:
                continue
        return tuple(lines)

    def seeing(self, paths: ScopePathPort) -> StorageService:
        """This writer, resolving through `paths` — the same store, claims and cipher.

        For the daemon's `project add` (story 4r): the project being enrolled is
        one the daemon's resolver does not know, and a second writer with its
        own store connection is what the daemon may not open. The view shares
        everything but the resolver, so its writes are still the one writer's,
        made on the writer thread.
        """
        view = copy.copy(self)
        view._paths = paths
        return view

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
        artifact whose node declares `gitignored=False`; `GITIGNORED` derives
        several members per scope, so the guard is live for more than captures.

        The refusal happens *before* `create=True`, so a refused write does not
        even leave behind the directory it was about to fill.
        """
        self._assert_git_excludes(scope, artifact)
        return self._paths.resolve(scope, artifact, create=True)

    def _assert_git_excludes(
        self, scope: DataScope, artifact: str, *, cached: bool = True
    ) -> None:
        """Refuse a write that git would carry into a commit (AD-23, AD-43).

        Raw captures were the whole subject until story 1n (2026-09-15) flipped
        the project scope's `memory/` — its event log, meeting summaries,
        commitments and dashboard — to gitignored, because a `git pull` rewrites
        Tier 1 underneath everything derived from it. So this now guards the
        ledger the daemon appends to on every harvest, not just the directory a
        transcript lands in. Nothing about the mechanism changed: the artifact's
        own declaration decides, which is why the reach could grow without a line
        here moving.

        One condition gates the question, and it is the scope model's:
        `GITIGNORED` names, per scope, the artifacts whose exclusion rests on a
        repository rule rather than on a directory boundary. **Which scope owns
        the capture is not a condition.** It was until 2026-08-22, gated on
        `is_git_committed`, and that was wrong in the direction that leaks:
        `is_git_committed` answers "is this scope pushed to the employer", a
        question about who may read the material, while the guard needs "can git
        reach this directory", a question about the filesystem. The two agree
        everywhere except the case Deployment itself creates — keep the personal
        scope as a private git repository — where `transcripts/` sits at that
        scope's root, outside the one `private/` rule that scope is told to add.
        A verbatim coaching transcript was therefore committable, and git was
        never asked.

        So the working tree decides. `working_tree` returning `None` is an
        *answer*: this path is in no repository, nothing can carry it into a
        commit, and the write proceeds. That is not the same as an unanswered
        question, which refuses.

        Git answers, not this process. A `.gitignore` containing the rule can
        still leave the directory tracked — a later negation line re-includes it,
        or it was committed before the rule existed — and a `.gitignore` that
        never names the directory can still exclude it through its parent. All
        three are ordinary repository states, and text matching gets two of them
        wrong in the direction that publishes a transcript.

        `VcsUnavailable` is a refusal, not an exception to it. Unknown is not
        permission: no `git` on PATH, a timeout, an exit code with no documented
        meaning — each leaves the question unanswered, and the only safe answer
        to an unanswered question here is no.

        The rule and the `.gitignore` path in the refusal are both derived from
        the root git reports, never from a table. A message naming
        `/.project-ai/transcripts/` while the operator is looking at
        `~/.manager-ai` sends them to edit a file that is already correct, in a
        repository that is not the one at fault — and a per-scope table cannot
        hold the alternative, since a basename is spelled the same way by every
        scope that declares it.

        The verdict is cached per `(scope, artifact)` for the daemon's lifetime.
        Without it this would spawn `git` on every append to a team-member or
        personal event log, both of which sit inside a gitignored enclave — the
        write-in-a-loop case AD-43 named as the condition to revisit on. One
        subprocess per artifact per run keeps the check at the moment of writing,
        which a startup probe cannot do. The cost is a staleness window: a rule
        deleted while the daemon runs is not noticed until it restarts.
        """
        if not requires_git_exclusion(scope.kind, artifact):
            return
        seen = (scope.kind, scope.project_id, scope.person_id, artifact)
        if cached and seen in self._git_checked:
            return
        # Resolved without `create`: asking git about a directory is not a reason
        # to bring it into existence, and git answers the same either way.
        target = self._paths.resolve(scope, artifact)
        try:
            repository = self._vcs.working_tree(target)
        except VcsUnavailable as unanswered:
            # git is optional. A machine without it, or a scope that is not a
            # checkout, must still be able to record a meeting — so the fallback
            # asks the only question that needs no binary: does a repository
            # exist here at all? If none does, nothing can commit this capture
            # and the write proceeds. If one does, pm-ai has a real repository it
            # cannot interrogate, which is the single case that leaks: `launchd`
            # gives the daemon a minimal PATH, so missing `git` here does not
            # mean missing `git` on the machine.
            marker = self._vcs.repository_marker_above(target)
            if marker is None:
                self._git_checked.add(seen)
                return
            raise UnprotectedCaptureDir(
                f"refusing to write {artifact} into {scope}: {marker} exists, so "
                f"a repository is present, but git could not be consulted about "
                f"it ({unanswered}). What is written here may be committed, and "
                f"neither a verbatim transcript nor a machine-local ledger is "
                f"recoverable once it is in a repository. Install git, or put it "
                f"on the daemon's PATH."
            ) from unanswered
        if repository is None:
            self._git_checked.add(seen)
            return
        try:
            verdict = self._vcs.tracking(target, repository=repository)
        except VcsUnavailable as unanswered:
            raise UnprotectedCaptureDir(
                f"refusing to write {artifact} into {scope}, because git could "
                f"not be consulted about it: {unanswered}"
            ) from unanswered
        assert_capture_dir_untracked(
            artifact,
            verdict,
            # Realpathed for the rule derivation only: `repository` comes from
            # `git rev-parse`, which resolves symlinks, and `target` from the
            # resolver, which does not — on a symlinked root (macOS `/tmp`, a
            # symlinked home) the two spell one directory two ways and the rule
            # cannot be derived from the unresolved form.
            rule=gitignore_rule_for(Path(os.path.realpath(target)), repository=repository),
            gitignore=str(repository / GITIGNORE_FILENAME),
        )
        # Recorded only on success: a refusal must fire again on the next attempt,
        # or an operator who fixes nothing sees the write succeed on retry.
        self._git_checked.add(seen)

    def _segment(self, scope: DataScope, artifact: str, at: datetime) -> Path:
        """The open monthly segment of the `artifact` ledger in `scope`.

        The directory comes from the resolver, so a scope's event log lands in
        that scope's own tree rather than in a sibling directory named by
        flattening the scope to a string.

        **Which segment is open is derived, never stored.** A stored flag is a
        second structure describing one fact, and the way it fails here is the
        one state compaction cannot survive: a sealed segment the writer believes
        is open. The filenames already carry the answer — the newest is open and
        every earlier one is sealed — so the directory listing *is* the record.

        Refuses a target older than the newest segment present. Nothing reaches
        that today, because both append paths pass the current instant; it is the
        clock moving backwards across a month boundary that gets here, and then
        the refusal is the point.
        """
        directory = self._writable_dir(scope, artifact)
        target = directory / f"{at:%Y-%m}.md"
        newest = max(_segment_names(directory), default=None)
        if newest is not None and target.name < newest:
            raise SealedSegment(
                f"{target.name} is sealed: {newest} is the open segment of "
                f"{scope}'s {artifact}. An entry appended to a sealed month is "
                f"deleted by the next compaction with nothing recording that it "
                f"existed. It is not re-dated into the open segment — the "
                f"filename does not carry when the thing happened."
            )
        return target

    # ── Every file operation asks the model first (AD-5, AD-6) ──────────────
    #
    # `is_encrypted` is consulted here rather than by callers, so which artifacts
    # are sealed is decided by their declaration in the scope trees and not by a
    # caller remembering which helper to reach for. The classifier knew the answer
    # before this; nothing asked it at the moment it mattered.

    def _append(self, path: Path, text: str, *, declared_mode: int | None) -> None:
        """Append to a plaintext artifact, or refuse if it is declared encrypted.

        There is no such thing as appending to a sealed file. AES-GCM covers the
        whole payload, so an append would mean read, decrypt, concatenate,
        re-seal, rewrite — and rewriting a ledger in place is precisely what AD-5
        forbids and what segmented compaction exists to avoid.

        So declaring a ledger encrypted is refused loudly here rather than
        honoured by quietly rewriting the file. No ledger is encrypted today; this
        is the guard that makes changing that a decision rather than an accident.

        `declared_mode` is `restricted_mode`'s answer for the ledger, computed by
        the caller that knows the scope and the artifact — the same rule the two
        whole-file writers follow (story 1q). It applies **when the file is
        created, and only then**: creation and append are told apart by the
        kernel (`O_CREAT|O_EXCL`), not by an existence check another process
        could race. The creation mode is the declared one, so the umask can only
        strip bits and the file never exists wider than declared; `fchmod` then
        restores owner bits an owner-stripping umask removed, or pm-ai could not
        append to its own ledger. An existing segment's mode is never touched,
        including one created at the umask before this rule — and a ledger
        declared without a mode (`None`) stays at the umask's answer.

        A segment removed between a refused exclusive create and the reopen
        (`FileNotFoundError` on the reopen) is created again, a bounded number of
        times, rather than refused — the old `path.open("a")` created it too.

        **An empty segment left by a failure between the create and the write is
        not always harmless.** Under an ordinary umask it already carries the
        declared mode, and the next append treats it as existing. Under an
        owner-stripping umask it does not: the create yields `declared_mode &
        ~umask` (`0400` at umask `200`), and a kill between `os.open` and
        `fchmod` leaves that file behind. The next append takes the
        existing-file branch, which never `fchmod`s, so pm-ai is locked out of
        that segment with the operating system's permission error until someone
        restores owner-write by hand. Changing an existing file's mode is not
        this writer's call (story 1q), so it is stated here rather than repaired.
        """
        if is_encrypted(str(path)):
            raise AppendToSealedArtifact(
                f"{path} is declared encrypted, and appending to a sealed file "
                f"means rewriting it whole — which AD-5 forbids for a ledger and "
                f"segmented compaction exists to avoid. Either the declaration is "
                f"wrong, or this artifact needs a segment-per-write shape."
            )
        descriptor, created = _open_for_append(
            path, 0o666 if declared_mode is None else declared_mode
        )
        try:
            if created and declared_mode is not None:
                os.fchmod(descriptor, declared_mode)
            # One record is one `write(2)` on an `O_APPEND` descriptor, and that
            # is what keeps a concurrent appender from splicing into it. The
            # loop in `_write_all` exists for short writes: a short write sends
            # the rest as a second `write(2)`, which gives up that guarantee for
            # this record. The measured bound on concurrent appenders sits
            # beside `MAX_ENTRY_LENGTH`.
            _write_all(descriptor, text.encode("utf-8"))
        finally:
            os.close(descriptor)

    def _create_exclusively(
        self, path: Path, text: str, *, staging: Path, declared_mode: int | None
    ) -> None:
        """Create an artifact that must not exist yet, sealing it if declared.

        Exclusive creation is the capture path's guarantee: two recordings must
        never splice into one file. Sealing does not change that — the name is
        claimed the same way whether the bytes are ciphertext or not.

        Published with `os.link`, so the name appears only once the content is
        complete *and* a name already taken is refused. `os.replace` would give
        the first property and destroy the second, silently splicing the two
        recordings this refusal exists to keep apart.

        `declared_mode` is `restricted_mode`'s answer for what is being written,
        computed by the caller that knows the scope and the artifact. It is used
        only when the artifact is not sealed: a sealed one already gets the same
        number *and* the enclave around it.
        """
        payload = text.encode("utf-8")
        sealed = is_encrypted(str(path))
        self._publish(
            path,
            self._crypto.encrypt(payload) if sealed else payload,
            staging=staging,
            mode=ENCRYPTED_FILE_MODE if sealed else declared_mode,
            enclave=sealed,
            exclusive=True,
        )

    def _replace(
        self,
        path: Path,
        payload: bytes,
        *,
        declared_mode: int | None,
        exclusive: bool = False,
    ) -> None:
        """Write an artifact whole, sealing it if declared. Credentials live here.

        Distinct from `_append` because a credential store *is* replaced — it has
        no history and no segments — and from `_create_exclusively` because
        rotating a token must overwrite rather than refuse.

        Published with `os.replace`, and here overwriting is the point: refusing
        a taken name would make token rotation impossible. `O_TRUNC` used to
        destroy the old bytes *before* writing the new ones, so a crash between
        the two left `config.json` empty — and an AES-GCM file cut part-way does
        not degrade, it fails its tag and becomes unreadable. Every connector
        credential, lost, with the daemon having done nothing wrong.

        `declared_mode` carries the trees' answer for an artifact that is *not*
        encrypted — `connectors/`, whose members used to land at the umask.
        `enclave=sealed` is the whole of story 8f's separation: the mode reaches
        the file either way, and only the encrypted set takes its parents with it.
        """
        sealed = is_encrypted(str(path))
        self._publish(
            path,
            self._crypto.encrypt(payload) if sealed else payload,
            staging=path.parent,
            mode=ENCRYPTED_FILE_MODE if sealed else declared_mode,
            enclave=sealed,
            # `write_artifact(exclusive=True)` (story 4e): publish by link, so a
            # name already taken raises `FileExistsError` and is left alone.
            exclusive=exclusive,
        )

    def _read(self, path: Path) -> bytes:
        """Read an artifact, unsealing it if declared encrypted."""
        raw = path.read_bytes()
        return self._crypto.decrypt(raw) if is_encrypted(str(path)) else raw

    def _publish(
        self,
        target: Path,
        payload: bytes,
        *,
        staging: Path,
        mode: int | None,
        enclave: bool,
        exclusive: bool,
    ) -> None:
        """Write `payload` where nobody can see it, then make it visible at once.

        The one primitive behind every whole-file write. Before a filesystem
        watcher existed (AD-46) a half-written file had no observer and only
        crash durability mattered; now the moment a name appears is the moment a
        job starts, so *when* a write becomes visible is part of the contract.

        Publication is the only difference between the two callers, and it is the
        interesting one:

        - `exclusive` — `os.link`, which fails `EEXIST` on a taken name. Atomic
          *and* exclusive, exactly as `O_CREAT|O_EXCL` was, so the capture
          refusal stays kernel-enforced rather than becoming a check.
        - otherwise — `os.replace`, which overwrites deliberately, because a
          rotated credential must land on top of the old one.

        `mode` and `enclave` are two answers, not one. Until story 8f this
        function inferred the second from the first — `enclave = mode is not
        None` — so the only way to ask for a restricted *file* was to also chmod
        every directory above it to 0700, `~/.pm-ai` included. That is right for
        the encrypted set, where a listable directory publishes the names, sizes
        and mtimes the enclave hides, and wrong for a declared-gitignored
        plaintext artifact: one connector file would have tightened the
        application root, which nobody asked for and nobody would notice.

        `fsync` before publishing and on the directory after: without the first,
        a crash can leave a visible, complete-looking name whose content never
        reached stable storage, which is the same problem one layer down.
        """
        parent = target.parent
        if enclave:
            # Every directory created along the way is 0700, not just the
            # immediate parent (story 1f): `mkdir(parents=True, mode=...)`
            # applies the mode to the final directory only, so a missing
            # `private/` above `telegram_cache/` used to land at umask default —
            # publishing the names, sizes and mtimes the enclave exists to hide.
            _mkdir_enclave(parent)
            if stat.S_IMODE(parent.stat().st_mode) != ENCLAVE_DIR_MODE:
                parent.chmod(ENCLAVE_DIR_MODE)
            _mkdir_enclave(staging)
        else:
            staging.mkdir(parents=True, exist_ok=True)

        staged = staging / f".{target.name}.{_ulid()}.part"
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode or 0o666)
        # One cleanup for every failure past this point: the staged name is
        # dot-prefixed — invisible to the operator NFR-09's purge rule serves —
        # so a failure that leaves it behind (ENOSPC mid-write as much as a
        # refused publish) must remove it. Only a kill can orphan one now.
        try:
            try:
                if mode is not None:
                    # `os.open`'s mode is masked by the process umask, so it is
                    # set again — on the STAGED file, before anything is visible
                    # under the final name, which is what makes the window zero
                    # rather than short.
                    #
                    # The rationale this replaces was wrong and worth correcting:
                    # it said `umask 000` would leave a credential "briefly
                    # world-readable". Measured 2026-08-28, it does not — umask
                    # only *removes* bits, and `0o600` requests none for group or
                    # other, so `umask 000` yields exactly `0o600`. What the mask
                    # can do is strip owner bits: at `umask 200` the same open
                    # yields `0o400`, and pm-ai could not finish writing its own
                    # credential store. The set is for determinism, never for
                    # confidentiality.
                    os.fchmod(descriptor, mode)
                _write_all(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._make_visible(staged, target, exclusive=exclusive)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
        _fsync_dir(parent)

    def _make_visible(self, staged: Path, target: Path, *, exclusive: bool) -> None:
        """The publish step, and the fallback for filesystems without hardlinks.

        `os.link` is unsupported on exFAT and on some network mounts. Those are
        reachable only through an *enrolled project repository* — never through
        `~/.pm-ai` or `~/.manager-ai`, which sit on the home filesystem — so the
        case is real but narrow. Declining to record a meeting because a
        repository lives on a USB stick is the wrong trade, so the fallback is
        check-then-rename.

        That is weaker, and precisely how much weaker is worth stating: the race
        it opens needs two *concurrent* writers, while AD-5 makes one component
        the sole writer and AD-19 puts it on a single loop. The real duplicate
        arrives as a later retry, which the check catches. Detected by attempting
        the link and reading the error, never by inspecting filesystem type.
        """
        if not exclusive:
            os.replace(staged, target)
            return
        try:
            os.link(staged, target)
        except FileExistsError:
            raise
        except OSError as unsupported:
            if unsupported.errno not in _LINK_UNSUPPORTED:
                raise
            if target.exists():
                raise FileExistsError(errno.EEXIST, "target exists", str(target))
            os.replace(staged, target)
            return
        staged.unlink(missing_ok=True)

    def read_project_gitignore(self, project_id: str) -> bytes | None:
        """A repository's `.gitignore`, or `None` when it has none.

        The read half of the pair below, here for the reason the write half is:
        no scope tree declares this file, so `read_artifact` cannot address it,
        and the single reader is the single reader (AD-5) whether or not an
        artifact happens to fit the table.

        Absence is a value rather than an exception — an unversioned directory
        genuinely has no `.gitignore`, and that is the ordinary first-onboarding
        state, not a fault. Every other `OSError` propagates: a directory in the
        way or a permission refusal must not read as "no rules yet", because the
        caller's next move is to write a file it believes is absent.
        """
        target = self._paths.gitignore(project_id)
        try:
            return target.read_bytes()
        except FileNotFoundError:
            return None

    def write_project_gitignore(self, project_id: str, payload: bytes) -> Path:
        """Publish a repository's `.gitignore`. Story 4k's one write outside a scope.

        It needs its own method because it is the one file pm-ai writes that no
        scope tree declares: `.gitignore` belongs to the *repository*, not to the
        `.project-ai/` scope inside it, so `ScopePathPort.gitignore()` resolves
        it and `resolve()` deliberately cannot (`paths.py:540-543`). Without this
        the onboarding sequence would have had to open it itself, and AD-5's
        static sweep covers `pm_ai.app` precisely so that does not happen
        quietly.

        No git check, and that is not an oversight: `_assert_git_excludes` asks
        whether git would commit an artifact, and this file is the *answer* to
        that question. Guarding it with itself would refuse to write the rule
        that makes every subsequent write legal — a project could then never be
        onboarded inside a repository at all.

        Whole-file replacement, so the caller renders the complete content. It is
        a file a team owns and hand-edits, and `pm_ai.core.project_scaffold`
        renders it by *appending* what is missing to what was read, which is what
        keeps adoption from being replacement. An append-only writer here would
        have been the wrong shape for the same reason `render_registry` takes the
        whole mapping: the caller must have read before it can write.

        `_publish` directly rather than `_replace`, and this is the one place
        that is correct. `_replace` asks `is_encrypted`, which answers `True` for
        any path it cannot locate inside a scope — a fail-closed default that is
        right everywhere else and wrong here, because this file is *deliberately*
        outside every scope. Measured 2026-09-15: routed through `_replace`, the
        first `pm-ai project add` inside a repository wrote an AES-GCM blob over
        the team's `.gitignore` — or, on a machine with no key enrolled, refused
        with a keychain error while onboarding a plain directory. Plaintext is
        not a default here; it is the artifact's nature.
        """
        target = self._paths.gitignore(project_id)
        self._publish(
            target,
            payload,
            staging=target.parent,
            # Not a restricted mode and not an enclave: a `.gitignore` is
            # committed, read by git and by every member of the team, and 0600
            # on it would be a file the operator's own tooling cannot read on a
            # shared checkout.
            mode=None,
            enclave=False,
            # Overwriting is the point — the payload is the old content plus
            # what was missing, so refusing a taken name would make onboarding
            # work exactly once per repository.
            exclusive=False,
        )
        return target

    def write_artifact(
        self,
        payload: bytes,
        *,
        scope: DataScope,
        artifact: str,
        name: str | None = None,
        exclusive: bool = False,
        exclusion: GitExclusion | None = None,
    ) -> Path:
        """Write a declared artifact whole, sealed or not as the model says.

        The entry point for the encrypted set — the credential store and the PM's
        voice notes — and the reason no caller decides whether to encrypt. Callers
        name *what* they are writing; the declaration in the scope trees decides
        *how*. Since story 8f that covers the file mode as well: an artifact the
        trees declare gitignored lands at 0600 whether or not it is encrypted,
        and no signature grew a `mode` argument to say so.

        `name` is for an artifact whose members are created at runtime: a
        `Collection` resolves to its directory, so the file inside it has to be
        named by the caller. A `File` resolves to itself and takes none. The name
        is validated exactly as a capture's is — it is interpolated into a path,
        and `../` or an absolute name would write outside the directory every
        guard above just answered for.

        Ledgers are refused. This path replaces the file whole, and replacing a
        ledger keeps the last write and destroys every entry before it — the
        rewrite-in-place AD-5 forbids and `append_event_log` exists to avoid.

        Resolved *without* `create`, deliberately: the cipher runs before the
        first directory is made, so a refused write — no key enrolled, a sealed
        artifact on a machine that cannot seal — leaves neither the file nor the
        directory behind (story 1f). `_publish` creates what the successful
        write actually needs.

        `exclusive` (story 4e) publishes with `O_EXCL` semantics instead of
        replacing: a name already taken raises `FileExistsError` and the file
        there is left exactly as it was. The API token is the caller — two
        daemons starting at once must never replace each other's token.

        `exclusion` (story 9b) is git's answer asked in a bounded step before
        this write (`ask_git_exclusion`): given, this write asks no `git` of its
        own and refuses an answer it was not handed for this artifact.
        """
        if is_append_only(scope.kind, artifact):
            raise AppendOnlyArtifact(
                f"{artifact} in {scope} is append-only, and write_artifact "
                f"replaces a file whole — which keeps this write and destroys "
                f"every entry before it. Append through the writer's append "
                f"path instead; a ledger's history is the artifact."
            )
        if name is not None:
            name = _capture_name(name)
        if exclusion is None:
            self._assert_git_excludes(scope, artifact)
        elif (
            exclusion._asked_by is not self
            or exclusion.scope != scope
            or exclusion.artifact != artifact
            or exclusion._target != self._paths.resolve(scope, artifact)
        ):
            raise GitNotAsked(
                f"the write of {artifact} in {scope} was handed a git answer that "
                f"was not asked for it, so nothing was written."
            )
        target = self._paths.resolve(scope, artifact)
        if name is not None:
            target = target / name
        self._replace(
            target,
            payload,
            declared_mode=restricted_mode(scope.kind, artifact),
            exclusive=exclusive,
        )
        return target

    def read_api_token(self) -> str | None:
        """The loopback API's token, `None` when no file exists (story 4e).

        `read_artifact`'s resolution and its one translation — absence is a
        value — plus the check `pm_ai.domain.api_token` states: a symlink, a
        file that is not regular, one readable by group or others, one over
        4 KiB, empty, carrying whitespace or control characters, or shorter
        than 32 characters is `ApiTokenRefused`, naming the file and never the
        token. The mode is read
        off the descriptor the bytes come from, so the answer describes the file
        that was read rather than whatever is at the name a moment later.
        """
        target = self._paths.resolve(APPLICATION, API_TOKEN)
        try:
            # `O_NOFOLLOW` refuses a symlink (`ELOOP`); `O_NONBLOCK` keeps a
            # FIFO planted at the name from hanging the open.
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        except OSError as refused:
            if refused.errno == errno.ELOOP:
                raise symlinked(str(target)) from refused
            raise
        try:
            mode = os.fstat(descriptor).st_mode
            raw = b""
            if stat.S_ISREG(mode):
                # One byte past the cap, so an oversized file is detected
                # without reading it whole.
                limit = MAX_TOKEN_FILE_BYTES + 1
                while len(raw) < limit and (chunk := os.read(descriptor, limit - len(raw))):
                    raw += chunk
        finally:
            os.close(descriptor)
        return check_api_token(raw, mode=mode, where=str(target))

    def api_token_path(self) -> Path:
        """Where the token file is, for a refusal that has to name it (4e)."""
        return self._paths.resolve(APPLICATION, API_TOKEN)

    def daemon_lock_path(self) -> Path:
        """Where the daemon's lifetime lock is: beside the token (AD-7, story 9a)."""
        return self.api_token_path().with_name(DAEMON_LOCK)

    def prepare_private_dir(self) -> Path:
        """The application scope's `private/`, every missing directory made at 0700.

        By story 1f's rule (`_mkdir_enclave`): the daemon's lifetime lock is
        the first thing a fresh home may write there (story 9a), and its
        directories must not come out at the umask's mode.
        """
        directory = self.daemon_lock_path().parent
        _mkdir_enclave(directory)
        return directory

    def create_api_token(self, token: str) -> Path:
        """Publish a freshly minted token, refusing a name already taken (4e).

        `write_artifact(..., exclusive=True)`: a token file found already
        present raises `FileExistsError` and is left untouched, so the caller
        reads it instead of replacing it.
        """
        return self.write_artifact(
            token.encode("ascii"), scope=APPLICATION, artifact=API_TOKEN, exclusive=True
        )

    def diagnostic_log(self, name: str) -> logging.Handler:
        """A rotating handler on `~/.pm-ai/logs/<name>`, AD-24's diagnostic log.

        Outside the tier model and never the event log: what goes here is
        operational noise — the daemon's request log first (story 4e) — and the
        file rotates rather than growing for ever. Built here because this
        service owns every write; the caller attaches it to a logger and
        removes and closes it when it stops. `name` is validated as a capture's
        is, since it becomes a path component.
        """
        directory = self._paths.resolve(APPLICATION, DIAGNOSTIC_LOGS, create=True)
        target = directory / _capture_name(name)
        return logging.handlers.RotatingFileHandler(
            target, maxBytes=_DIAGNOSTIC_LOG_BYTES, backupCount=_DIAGNOSTIC_LOG_BACKUPS,
            encoding="utf-8",
        )

    def close(self) -> None:
        """Close the operational store's connection — the daemon's last act (4e).

        A writer is otherwise closed by the process exiting. The daemon is the
        one owner that outlives its commands, so its shutdown closes the writer
        explicitly once the listener has stopped. Idempotent.
        """
        self._db.close()

    def read_artifact(
        self, *, scope: DataScope, artifact: str, name: str | None = None
    ) -> bytes | None:
        """Read a declared artifact, unsealing it if the model says it is sealed.

        `None` means *nothing has been written there yet*, which is the ordinary
        state of every optional artifact on a clean machine and not a failure.
        This ended in `path.read_bytes()` until story 8f, so a first run raised
        `FileNotFoundError` out of whatever asked, and every caller that needed
        "not there yet" had to wrap the call in its own translation — `4c` wrote
        one in `pm_ai.app.entry` for exactly this reason.

        Only absence becomes a value. A directory in the way, a permission
        refusal, an unreadable device — all `OSError`s that are *not* absence —
        propagate, because reporting them as "no file" is how a machine that
        cannot read its own configuration looks freshly installed. So does a
        `DecryptionFailed` or a missing key: the file is there and unopenable,
        which is a different sentence.

        `name` is validated as `write_artifact` validates it — this writer only
        ever mints single-component names, so a traversal here is a request to
        read something the layout never placed in this directory.
        """
        if name is not None:
            name = _capture_name(name)
        target = self._paths.resolve(scope, artifact)
        if name is not None:
            target = target / name
        try:
            return self._read(target)
        except FileNotFoundError:
            return None

    def list_collection(self, *, scope: DataScope, artifact: str) -> tuple[str, ...]:
        """The names a declared `Collection` currently holds, sorted.

        Names, never paths. A caller in `core` may not learn where an artifact
        lives — story `1a` made the resolver the only thing that knows — and a
        listing that handed out paths would be a way to route around it and open
        a file directly, which is also AD-5's single writer gone.

        Only a `Collection` has a listing, and `assert_is_collection` is what
        refuses the rest: a `File` key is a caller error, and a `Dir`'s members
        are declared in the trees rather than discovered on disk.

        Resolved *without* `create`, for `event_log_segments`' reason: asking
        what a collection holds must not bring its directory into being. A
        collection nothing has been written to answers with an empty tuple, not
        a new empty directory for the next reader to find and believe in.

        Dot-prefixed entries are omitted. The only ones this writer produces are
        the `.part` files `_publish` stages and unlinks, which are deliberately
        invisible to the operator NFR-09's purge rule serves — reporting one as
        a member would name a file that is about to stop existing.

        Claim sidecars (`<name>.lock`, story `1r`) are omitted too: a claim on a
        member leaves one beside it for good, and it is structure, not a member.
        The scope model refuses to declare any name with that suffix, so nothing
        real is hidden by this.
        """
        directory = self._paths.resolve(scope, artifact)
        assert_is_collection(scope.kind, artifact)
        if not directory.is_dir():
            return ()
        return tuple(
            sorted(
                entry.name
                for entry in directory.iterdir()
                if not entry.name.startswith(".")
                and not entry.name.endswith(CLAIM_SIDECAR_SUFFIX)
            )
        )

    def remove_member(self, *, scope: DataScope, artifact: str, name: str) -> bool:
        """Remove one member of a declared `Collection`; `True` if it was there.

        Added with story 11b for the one legitimate move of a meeting record
        between day files: a calendar arrival that adopts a manual-only record
        re-files it under the calendar start's day, and the old name must not
        stay behind as a second record of the same id. A ledger is refused —
        its history is the artifact — and so is anything that is not a
        collection member.
        """
        if is_append_only(scope.kind, artifact):
            raise AppendOnlyArtifact(
                f"{artifact} in {scope} is append-only; a member of it is never removed."
            )
        assert_is_collection(scope.kind, artifact)
        rule = _REMOVABLE.get(artifact)
        if rule is None:
            raise MalformedCaptureName(
                f"{artifact} declares no rule for removing a member, so {name!r} "
                f"was not removed. Only a meeting record is ever re-filed."
            )
        if not rule.fullmatch(name):
            raise MalformedCaptureName(
                f"{name!r} is not a member name {artifact} holds, so it was not "
                f"removed: a record there is `<day>-<slug>-<sha256>.md`."
            )
        target = self._paths.resolve(scope, artifact) / name
        try:
            target.unlink()
        except FileNotFoundError:
            return False
        return True

    def assert_writable(self, *, scope: DataScope, artifact: str) -> None:
        """Ask now what a write to `artifact` would ask, and write nothing (story 8b).

        The only question a write asks before touching anything is git's, and it
        is asked of a *declared-gitignored* artifact only. For a caller that
        writes one artifact this is redundant — `write_artifact` asks it anyway.
        For a caller that writes two in a mandated order it is the difference
        between a refusal and a half-finished enrolment: `connectors/` is
        gitignored, so on a machine whose `$HOME` is a git repository without
        the rule the refusal lands on the *second* write, with the credential
        already sealed. Every attempt would orphan one.

        Deliberately not `_writable_dir`, which resolves with `create=True`. A
        question is not a reason to bring a directory into existence, and a
        pre-flight that left one behind would be answering by writing.

        The verdict is cached exactly as the write path caches it, because it is
        the same call — so the pre-flight costs one `git` invocation and the
        write that follows costs none.

        A declared-encrypted artifact also has its master key fetched (story
        8l), because sealing is the other question its write asks: on a machine
        with no key enrolled the refusal is `KeyNotFound` now, before a caller
        has spent minutes on a sign-in whose result could never be sealed.
        Nothing is encrypted to ask it, so the plaintext debug cipher records
        nothing either.
        """
        self._assert_git_excludes(scope, artifact)
        if isinstance(self._crypto, LazyKeyCrypto) and is_encrypted(
            str(self._paths.resolve(scope, artifact))
        ):
            self._crypto.ready()

    def ask_git_exclusion(self, *, scope: DataScope, artifact: str) -> GitExclusion:
        """Ask git now, never from the cache, whether `artifact` is excluded (9b).

        A bounded step: `git` runs here, on the caller's thread, and the answer
        is handed to `write_artifact(exclusion=...)`, which then asks nothing.
        Refuses as the write would (`UnprotectedCaptureDir`) when git cannot
        confirm the exclusion. An artifact git need not be asked about gets an
        answer without asking.
        """
        self._assert_git_excludes(scope, artifact, cached=False)
        return GitExclusion(scope, artifact, self, Path(self._paths.resolve(scope, artifact)))

    @contextlib.contextmanager
    def exclusive(self, *, scope: DataScope, artifact: str) -> Iterator[None]:
        """An exclusive claim on `artifact`, for a read-modify-write (stories 8b, 1r).

        `write_artifact` replaces a file whole and publishes by `os.replace`,
        which overwrites deliberately and coordinates nothing. That is right for
        a rotated credential and wrong for `private/config.json`, which is one
        sealed file holding *every* connector's credential: two enrolments that
        both read it, both add an entry and both write it back keep one entry
        and lose the other. The read and the write have to be one step, and this
        is what makes them one.

        The claim is the platform's kernel lock (`ClaimPort`), taken on an empty
        `<name>.lock` sidecar beside the artifact, keyed on the resolved path —
        so a caller holding the platform lock on the same file directly, or
        through another spelling of it, holds the same claim. A kill releases
        it. The sidecar is never deleted, and is declared as structure outside
        the tier model (`scope_model.CLAIM_SIDECAR_SUFFIX`).

        **Never re-entrant** (AD-5, story 8q): the thread already holding this
        claim that asks for it again gets `ClaimReentered`, a programming error
        naming the path, before anything is created or the port is asked —
        never `ArtifactBusy`, which would blame a process that does not exist
        and send a retry into itself. The guard is **per thread**: another
        thread holding the claim is ordinary contention and answers busy, and
        asyncio tasks sharing one thread are one holder to it — it cannot tell
        them apart.

        **Refuses rather than waits**, as `ArtifactBusy` naming the artifact and
        its scope. The platform's `ClaimHeld` is translated here and only for
        this claim's own entry: a refusal raised inside the body is the inner
        file's, and passes through untouched. Any other `OSError` from taking
        the claim is the operating system's and propagates as itself.

        An older build's marker file (`.<name>.claim`) is reported once per
        writer and per path on the error stream, and otherwise ignored: nothing
        holds it, and obeying it would wedge every later run.
        """
        target = self._paths.resolve(scope, artifact)
        # Checked first, before any directory is created, so "nothing was
        # changed" is true. The set is captured here and the same set is the
        # one discarded from on the way out.
        canonical = _canonical(target)
        claimed = _claimed_here()
        if canonical in claimed:
            raise ClaimReentered(
                f"this pm-ai thread already holds the claim on {artifact} in "
                f"{scope} ({canonical}) and asked for it again. That is a bug in "
                f"pm-ai, not a busy file: a caller inside the claim writes under "
                f"it rather than taking it twice. Nothing was changed."
            )
        self._report_older_build_marker(target)
        # The parent may not exist yet on a first run, and the claim has to live
        # beside what it claims. Created by the same rule the artifact itself
        # would be created under, so claiming a sealed artifact does not open a
        # world-listable `private/` a moment before the enclave would have been —
        # the platform lock's own `mkdir` runs at the umask.
        if is_encrypted(str(target)):
            _mkdir_enclave(target.parent)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.ExitStack() as held:
            try:
                held.enter_context(self._claims.exclusive(target))
            except ClaimHeld as busy:
                raise ArtifactBusy(
                    f"{artifact} in {scope} is claimed by another pm-ai process; "
                    f"nothing was changed. Run the command again."
                ) from busy
            claimed.add(canonical)
            held.callback(claimed.discard, canonical)
            yield

    def holds_claim(self, *, scope: DataScope, artifact: str) -> bool:
        """Whether this thread holds `exclusive`'s claim on `artifact` (story 8q).

        Read off the same per-thread set the re-entry guard keeps, so it sees a
        claim taken through any writer in this process, and none taken by
        another thread or process.
        """
        return _canonical(self._paths.resolve(scope, artifact)) in _claimed_here()

    def _report_older_build_marker(self, target: Path) -> None:
        """Say so, once per writer, when an older build's marker sits beside `target`."""
        marker = target.parent / f".{target.name}.claim"
        if marker in self._markers_reported or not os.path.lexists(marker):
            return
        self._markers_reported.add(marker)
        print(
            f"pm-ai: {marker} was left by an older pm-ai build; delete it — "
            f"nothing holds it.",
            file=sys.stderr,
        )

    def append_event_log(self, entry: EventEntry, *, scope: DataScope) -> None:
        """Append one typed record to `scope`'s open segment, naming it here.

        Took a free string until story 2e, which is how four grammars reached one
        ledger and why CAP-10's guarantee — an id, a timestamp, an actor and a
        category on every entry — held only on the harvest path.

        The id is minted here rather than accepted, per AD-34: the surrogate
        belongs to the writer, and a caller that could supply one could also
        reuse one. Refused rather than overwritten, because silently discarding
        a caller's id would leave them believing the ledger holds it.
        """
        supplied = {key for key, _ in entry.fields} & _WRITER_OWNED_FIELDS
        if supplied:
            raise ValueError(
                f"entry already carries {sorted(supplied)}, which the writer "
                f"stamps itself. A duplicate key wins under `dict(fields)`, so a "
                f"caller's value would silently replace the local clock — the "
                f"substitution AD-35 forbids."
            )
        if entry.entry_id is not None:
            raise ValueError(
                f"entry already carries id {entry.entry_id!r}. The `evt_` "
                f"surrogate is minted by the storage service at persist time "
                f"(AD-34) — build the entry without one."
            )
        at = self._at()
        named = replace(
            entry,
            entry_id=_ulid(),
            # CAP-10 wants a timestamp on *every* entry, and until this was added
            # the clock read below named the segment file and nothing else — so an
            # entry arriving here carried an id, a category and an actor, and no
            # time. `ingested_at` rather than `occurred_at`: the writer knows when
            # the record reached it and nothing about when the thing happened
            # (AD-35). It leads the fields on both write paths so a reader finds
            # it without knowing which one produced the line.
            fields=(("ingested_at", at.isoformat()),) + entry.fields,
        )
        self._append(
            self._segment(scope, EVENT_LOG, at),
            render_entry(named) + "\n",
            declared_mode=restricted_mode(scope.kind, EVENT_LOG),
        )

    def append_disclosure(
        self, record: DisclosureRecord, *, scope: DataScope | None = None
    ) -> None:
        """Append one frontier call to the application-scoped ledger (AD-17, AD-38).

        `scope` exists so the refusal is reachable, not so a caller can choose:
        the record's only home is the application scope, and omitting it takes
        that. `assert_writable` is what enforces it — a `DisclosureRecord` routed
        anywhere else raises `CommittedScopeLeak`, because a per-scope ledger is
        readable by whoever that scope is shared with — and a record naming
        personal material in the project's log reaches the team. The mechanism
        built to prove nothing leaked would have been the leak.

        Both refusals run before the directory is resolved with `create`, so a
        refused write leaves neither a file nor a directory behind.
        """
        destination = record.home if scope is None else scope
        assert_writable(record, scope=destination)  # AD-38
        # No git check: `assert_writable` above refuses every scope but the
        # application one, and that scope is outside every working tree. A guard
        # whose refusal has no reachable path reads as protection and is not.
        target = self._paths.resolve(
            destination, DISCLOSURE_LEDGER_ARTIFACT, create=True
        )
        self._append(
            target,
            render_disclosure(record) + "\n",
            declared_mode=restricted_mode(destination.kind, DISCLOSURE_LEDGER_ARTIFACT),
        )

    def read_disclosure(self) -> str:
        """The application ledger's text, or empty if nothing has left the machine.

        Absent is not an error: a machine that has made no frontier call has an
        honest answer, and creating the file to report it would make "nothing yet"
        indistinguishable from "the ledger was deleted".
        """
        target = self._paths.resolve(DISCLOSURE_LEDGER_SCOPE, DISCLOSURE_LEDGER_ARTIFACT)
        if not target.exists():
            return ""
        return self._read(target).decode("utf-8")

    def event_log_segments(self, *, scope: DataScope) -> tuple[str, ...]:
        """Every dated segment in `scope`'s event log, oldest first.

        Resolved *without* `create`: asking which segments exist must not bring
        the directory into being. A scope that has never been written to answers
        "none", and an empty tuple is that answer — not a new empty directory for
        the next reader to find and believe in.
        """
        directory = self._paths.resolve(scope, EVENT_LOG)
        if not directory.is_dir():
            return ()
        return tuple(sorted(_segment_names(directory)))

    def read_event_log_segment(self, *, scope: DataScope, name: str) -> str:
        """One segment's text, by the name `event_log_segments` returned.

        Goes through `read_artifact`, so the name is validated as the single path
        component it becomes — a caller cannot read its way out of the directory
        with a name this writer never minted.

        Absence stays an exception here, alone among the readers. The name comes
        from `event_log_segments`, which said the segment exists; returning `""`
        for one that has since vanished would fold a deleted month into "that
        month was empty", and a fold over an audit trail cannot tell those apart.
        """
        try:
            text = self.read_artifact(scope=scope, artifact=EVENT_LOG, name=name)
        except DecryptionFailed as unopenable:
            # The same type, with the segment and the log named (story `23c`):
            # the dashboard states this refusal's text as a segment that could
            # not be read from disk, and the crypto's own words name neither.
            raise DecryptionFailed(
                f"{name} in {scope}'s event log could not be decrypted: {unopenable}"
            ) from unopenable
        if text is None:
            raise FileNotFoundError(
                errno.ENOENT,
                f"{name} is not a segment of {scope}'s event log; "
                f"event_log_segments() is what names the ones that are",
                name,
            )
        try:
            return text.decode("utf-8")
        except UnicodeDecodeError as undecodable:
            # Re-raised as the same type with the segment named (story `23c`):
            # the dashboard states this refusal's text, and the codec's own
            # words say which byte but never which file.
            raise UnicodeDecodeError(
                undecodable.encoding,
                undecodable.object,
                undecodable.start,
                undecodable.end,
                f"{undecodable.reason}; {name} in {scope}'s event log is not "
                f"valid UTF-8 text",
            ) from undecodable

    # ── Raw captures: outside the tier model, inside a working tree ──────────
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
            # Staged in the scope's declared `transcripts/temp/`, resolved
            # rather than composed: two packages need that name — this one and
            # AD-46's watcher, which must exclude it — and re-deriving a path is
            # how a second copy of the layout appears (AD-4).
            #
            # Resolved WITHOUT `_writable_dir`, deliberately. That helper asks
            # git, and the question was already asked above about the capture
            # directory this one sits inside — the answer is a directory rule
            # covering both. Asking twice made the guard's own pre-written test
            # fail on a duplicate question, which is the cheap version of the
            # expensive failure: a second question can get a second answer, and
            # then "which directory was the verdict about" has no fixed answer.
            self._create_exclusively(
                capture,
                body,
                staging=self._paths.resolve(scope, CAPTURE_STAGING, create=True),
                # The same declaration `write_artifact` reads, asked here too so
                # the trees' answer is honoured by both writers rather than by
                # whichever one somebody remembered. `transcripts/` is declared
                # gitignored in every scope that holds it, so a verbatim
                # recording lands owner-only — with its directories untouched,
                # since a capture is not encrypted and gets no enclave.
                declared_mode=restricted_mode(scope.kind, CAPTURES),
            )
        except FileExistsError as taken:
            raise CaptureAlreadyExists(
                f"{capture} already exists. Appending would splice two recordings "
                f"into one transcript and truncating would destroy the first, so "
                f"a second capture needs its own name."
            ) from taken
        except BaseException:
            # The final name is never claimed until the content is complete, so
            # there is nothing here to clean up. The unlink this replaces ran
            # only for *exceptions* and could do nothing for `SIGKILL` or a power
            # loss — after which a zero-length file owned the name permanently
            # and every retry, including the one carrying the content, was
            # refused as a duplicate. Staging closes that unconditionally.
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
        persisted = duplicates = flagged = 0
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

            # AD-35 — an implausible provider clock is *flagged*, never
            # backfilled from `ingested_at`: a substituted timestamp is
            # well-formed, plausible and wrong, and nothing downstream can tell
            # it from a real one. The refusal is caught rather than propagated
            # because this batch is all-or-nothing, and one skewed provider must
            # not discard every other event harvested with it.
            suspect: tuple[tuple[str, str], ...]
            try:
                clocks.validate_occurred_at(stamped.occurred_at, now=at)
                suspect = ()
            except clocks.ImplausibleTimestamp:
                suspect = ((OCCURRED_AT_FLAG, IMPLAUSIBLE),)
                flagged += 1
            # The format lives in `render_entry` (story 2d), not here. A writer
            # that formats its own line is a second grammar in the ledger, which
            # is what left four of them reaching one file.
            lines.append(
                render_entry(
                    EventEntry(
                        entry_id=_ulid(),
                        category=stamped.type,
                        actor=str(stamped.actor),
                        fields=(
                            ("ingested_at", at.isoformat()),
                            ("src", str(stamped.source_ref)),
                            (
                                "occurred_at",
                                stamped.occurred_at.isoformat()
                                if stamped.occurred_at
                                else UNKNOWN_VALUE,
                            ),
                            ("authored_by", stamped.authored_by.value),
                        )
                        + suspect
                        # Through the one encoder (AD-27), so the writer and
                        # every reader agree on each field's line name and form.
                        + encode_payload(stamped.payload),
                    )
                )
            )
            persisted += 1
        if lines:
            self._append(
                self._segment(scope, EVENT_LOG, at),
                "\n".join(lines) + "\n",
                declared_mode=restricted_mode(scope.kind, EVENT_LOG),
            )
        return PersistResult(
            persisted=persisted, duplicates=duplicates, at=at, flagged=flagged
        )

    # ── Tier 2: operational, never rebuilt (AD-3) ────────────────────────────

    def load_cursor(self, instance: str) -> Cursor:
        row = self._db.execute(
            "SELECT token FROM cursors WHERE instance = ?", (instance,)
        ).fetchone()
        return Cursor(row[0]) if row else Cursor()

    def save_cursor(
        self,
        instance: str,
        cursor: Cursor,
        coverage: CoverageWindow | None,
        failure: HarvestFailure | None,
    ) -> None:
        """Record where a harvest got to, what it covered, and whether it failed.

        One statement group and one `commit`, because these three are one fact
        about one attempt. Split across two writes, a crash in between leaves a
        cursor that advanced past events whose coverage was never recorded.

        `coverage: CoverageWindow | None` **by signature**, not by duck-typing.
        It was `object`, read through three `getattr` calls, which meant "accepts
        an absent window explicitly" was aspirational: any object at all was
        accepted, a misspelled attribute silently became "no coverage", and mypy
        could not see a caller passing the wrong thing. Absence is an ordinary
        answer, so the type says so — but it means "did not check", not "found
        nothing". Since `8i` a provider that answered an empty `200` *did*
        check, and arrives here with the window measured by the connector's own
        clock either side of that request. `None` arrives when the request
        failed, nothing that came back had a readable time, everything that
        came back was rejected as unreadable, or this machine's clock stepped
        backwards during the request, leaving no honest interval to record.

        `failure=None` records "the last attempt did not fail", and that is a
        write rather than a no-op: a failure that outlived its repair reads as
        `ERROR` forever, so a successful attempt clears the row. The row is
        current state, not history — `evaluate_commitment` asks whether this
        connector is broken *now*.

        Which is exactly why `failure` has **no default**. Because passing `None`
        deletes a durable row, a default would have made every three-argument
        call — a cursor restore, a test replay, a caller written before this
        parameter existed — report a dead connector as repaired, silently. The
        caller always knows which of `8a`'s three outcomes it had, so it says.

        **Validated before the first `execute`, and rolled back if anything
        after it raises.** The refusal below used to fire *after* the cursor
        insert, on a connection with no `rollback`, so a refused save left the
        cursor advance pending — and the next unrelated `commit()` on this
        shared connection promoted it. A refused write that durably advances a
        cursor is the same both-or-neither property the matrix states one method
        up for a persist that raises after page one, and it is stated here
        rather than left to statement order.
        """
        if coverage is not None and coverage.connector_instance != instance:
            # Refused rather than silently stored under the window's own key.
            # `coverage_windows(instance)` reads back by this column, so a
            # window whose `connector_instance` disagrees with the instance
            # being saved lands where nothing looks for it — and an absence
            # assertion over the intended key passes while the row exists.
            raise CoverageInstanceMismatch(
                f"coverage for {coverage.connector_instance!r} was offered "
                f"while saving the cursor for {instance!r}. The window is "
                f"stored under its own `connector_instance` and read back by "
                f"instance, so storing this would file real coverage where "
                f"nothing reads it and leave {instance!r} looking uncovered."
            )
        try:
            self._write_cursor(instance, cursor, coverage, failure)
        except Exception:
            # Nothing this method wrote may survive a refusal it raised. The
            # connection is shared, so a pending statement is not discarded by
            # leaving this frame — it waits for whoever commits next.
            self._db.rollback()
            raise
        self._db.commit()

    def _write_cursor(
        self,
        instance: str,
        cursor: Cursor,
        coverage: CoverageWindow | None,
        failure: HarvestFailure | None,
    ) -> None:
        """The three statements `save_cursor` commits, with nothing decided here.

        Split out so the caller owns the transaction — validate, write, commit
        or roll back — rather than interleaving a raise with half the writes.
        """
        self._db.execute(
            "INSERT INTO cursors (instance, token) VALUES (?, ?) "
            "ON CONFLICT(instance) DO UPDATE SET token = excluded.token",
            (instance, cursor.token),
        )
        if coverage is not None:
            # `WHERE NOT EXISTS` rather than a plain insert, and what it buys
            # is replay idempotence, not range deduplication. A window is
            # recorded in `ingested_at` terms, so two harvests of one calendar
            # range happen at two different instants and are two real windows —
            # there is nothing to collapse. What this stops is a *retried* save
            # of one `HarvestResult`, whose bounds are byte-identical. The
            # constraint is in the statement rather than in a `UNIQUE` index
            # because Tier 2 is never rebuilt (AD-3) — creating a unique index
            # on every open would refuse to start against a store that already
            # holds a duplicate from the era that inserted unconditionally.
            self._db.execute(
                "INSERT INTO coverage (instance, start, end) "
                "SELECT ?, ?, ? WHERE NOT EXISTS ("
                "  SELECT 1 FROM coverage WHERE instance = ? AND start = ? AND end = ?"
                ")",
                (
                    coverage.connector_instance,
                    coverage.start.isoformat(),
                    coverage.end.isoformat(),
                    coverage.connector_instance,
                    coverage.start.isoformat(),
                    coverage.end.isoformat(),
                ),
            )
        self._write_failure(instance, failure)

    def _write_failure(
        self, instance: str, failure: HarvestFailure | None, *, after_save: bool = False
    ) -> None:
        """The failure row and the attempt it belongs to, on the caller's transaction.

        `None` clears the failure and stamps the attempt a success, keeping the
        success before it; a failure replaces the row and leaves the last
        success as it was — or, `after_save`, puts back the one before this
        attempt's own save, which a later step of the same attempt undid
        (story 9a).
        """
        at = self._at().isoformat()
        if failure is None:
            self._db.execute("DELETE FROM harvest_failures WHERE instance = ?", (instance,))
        else:
            self._db.execute(
                "INSERT INTO harvest_failures (instance, reason, retryable, retry_after, at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(instance) DO UPDATE SET "
                "  reason = excluded.reason, retryable = excluded.retryable, "
                "  retry_after = excluded.retry_after, at = excluded.at",
                (
                    instance,
                    failure.reason,
                    int(failure.retryable),
                    failure.retry_after.total_seconds()
                    if failure.retry_after is not None
                    else None,
                    # This service's clock, not `failure.at`. The connector reads
                    # its own clock for coverage bounds; when a row was written is
                    # the single writer's fact (AD-5, AD-30), and taking it from
                    # the value would let a connector with a skewed clock date a
                    # failure into next week. `harvest_failure()` reads it back.
                    at,
                ),
            )
        # The attempt itself (story 9a): when it was, and when one last worked.
        # A new attempt starts with no transcript refusals; the transcript
        # fetch after the save records its own.
        if failure is None:
            self._db.execute(
                "INSERT INTO harvest_runs (instance, last_attempt, last_success, "
                "previous_success, transcript_refusals) VALUES (?, ?, ?, NULL, NULL) "
                "ON CONFLICT(instance) DO UPDATE SET "
                "  last_attempt = excluded.last_attempt, "
                "  previous_success = harvest_runs.last_success, "
                "  last_success = excluded.last_success, "
                "  transcript_refusals = NULL",
                (instance, at, at),
            )
        elif after_save:
            self._db.execute(
                "UPDATE harvest_runs SET last_attempt = ?, last_success = previous_success "
                "WHERE instance = ?",
                (at, instance),
            )
        else:
            self._db.execute(
                "INSERT INTO harvest_runs (instance, last_attempt, transcript_refusals) "
                "VALUES (?, ?, NULL) "
                "ON CONFLICT(instance) DO UPDATE SET "
                "  last_attempt = excluded.last_attempt, transcript_refusals = NULL",
                (instance, at),
            )

    def record_harvest_failure(
        self, instance: str, failure: HarvestFailure, *, after_save: bool = False
    ) -> None:
        """Record a failed attempt with the position and coverage left as they are (9a).

        For an attempt that never reached the save: a connector that raised
        instead of reporting, a fetch abandoned at its budget or by the
        shutdown, a write the harvest could not classify. One transaction:
        the failure row and the attempt together. `after_save`: the same
        attempt saved its position first, so the success that save stamped is
        taken back — the record never shows a success at an attempt that failed.
        """
        with self._transaction():
            self._write_failure(instance, failure, after_save=after_save)

    def record_transcript_refusals(self, instance: str, refusals: Sequence[str]) -> None:
        """What the last attempt's transcript fetch and ingestion refused (story 9a).

        pm-ai's own sentences, stored for `doctor`'s `harvests` line. An
        instance with no attempt recorded gets none: the refusals belong to an
        attempt, and there is none to hang them on.
        """
        with self._transaction() as db:
            db.execute(
                "UPDATE harvest_runs SET transcript_refusals = ? WHERE instance = ?",
                (json.dumps(list(refusals), ensure_ascii=False) if refusals else None, instance),
            )

    def prune_coverage(self, *, before: datetime) -> int:
        """Remove every coverage record ending before `before`; how many went (9a)."""
        if before.tzinfo is None or before.utcoffset() != timedelta(0):
            raise ValueError(f"before={before!r} is not aware UTC")
        # One statement. Bounds are written by `_write_cursor` as ISO-8601
        # strings of one aware-UTC clock (a window starts at `answered_at`,
        # which `HarvestResult` refuses unless aware UTC), and those compare as
        # text in time order. A row in any other shape — naive, hand-edited,
        # unparseable — is compared as text too and never aborts the prune.
        with self._transaction() as db:
            return db.execute(
                "DELETE FROM coverage WHERE end < ?", (before.isoformat(),)
            ).rowcount

    def harvest_records(self) -> tuple[HarvestRecord, ...]:
        """Every instance's attempts and current failure, by instance (story 9a).

        A plain read on a read-only connection of its own, so `doctor` sent
        through the daemon and the timers' start read off a worker and never
        the writer's connection.
        """
        with self._reader() as reader:
            runs = {
                instance: (attempt, success, refusals)
                for instance, attempt, success, refusals in reader.execute(
                    "SELECT instance, last_attempt, last_success, transcript_refusals "
                    "FROM harvest_runs"
                )
            }
            failures = {
                instance: HarvestFailure(
                    reason=reason,
                    retryable=bool(retryable),
                    retry_after=(
                        timedelta(seconds=retry_after) if retry_after is not None else None
                    ),
                    at=datetime.fromisoformat(at) if at is not None else None,
                )
                for instance, reason, retryable, retry_after, at in reader.execute(
                    "SELECT instance, reason, retryable, retry_after, at FROM harvest_failures"
                )
            }
        records = []
        for instance in sorted({*runs, *failures}):
            attempt, success, refusals = runs.get(instance, (None, None, None))
            records.append(
                HarvestRecord(
                    instance=instance,
                    last_attempt=datetime.fromisoformat(attempt) if attempt else None,
                    last_success=datetime.fromisoformat(success) if success else None,
                    failure=failures.get(instance),
                    transcript_refusals=_refusals(refusals),
                )
            )
        return tuple(records)

    # ── `renders`: each dashboard scope's last render (story 9b) ────────────
    # Tier 2. A hand-run `pm-ai dashboard` and the daemon both write a row, so
    # it changes only through one statement — an insert-or-update keyed by
    # scope, setting only the columns that attempt records — never read in one
    # statement and written in another; the new value never depends on the old
    # (AD-5).

    def record_render(self, scope: DataScope, *, at: datetime) -> None:
        """A successful render of `scope` at `at`: its instant, and no failure."""
        _assert_aware_utc(at, name="at")
        with self._transaction() as db:
            db.execute(_RECORD_RENDER, (str(scope), at.isoformat()))

    @contextlib.contextmanager
    def recording_render(self, scope: DataScope, *, at: datetime) -> Iterator[None]:
        """`record_render`'s one statement, made first and committed last (9b).

        The statement opens the transaction before the body runs, so a busy
        store refuses the render before its file is published; the body (the
        publish) runs inside it, and the record is committed only after the
        body returns — rolled back if it raises. In the daemon the commit is
        the claimed step's end.
        """
        _assert_aware_utc(at, name="at")
        with self._transaction() as db:
            db.execute(_RECORD_RENDER, (str(scope), at.isoformat()))
            yield

    def record_render_failure(
        self, scope: DataScope, reason: str, *, at: datetime, retryable: bool = False
    ) -> None:
        """A failed render of `scope` at `at`, and why; its last success unchanged.

        `retryable`: waiting can clear it (a held claim, a busy store), so the
        daemon retries it on the waits whoever recorded it.
        """
        _assert_aware_utc(at, name="at")
        with self._transaction() as db:
            db.execute(_RECORD_FAILURE, (str(scope), at.isoformat(), reason, int(retryable)))

    def record_render_failures(
        self, failures: Sequence[tuple[DataScope, str]], *, at: datetime
    ) -> tuple[DataScope, ...]:
        """Several failed renders in one step, each its own statement (9b).

        For the shutdown's own step: each record is made under a savepoint of
        its own, so one that fails is undone alone, logged, and the others are
        kept. Returns the scopes recorded.
        """
        _assert_aware_utc(at, name="at")
        recorded: list[DataScope] = []
        with self._transaction() as db:
            # A plain statement first, so the daemon's step opens its
            # `BEGIN IMMEDIATE` with the version check before any savepoint.
            db.execute("SELECT 1 FROM renders LIMIT 0")
            for index, (scope, reason) in enumerate(failures):
                point = f"render_failure_{index}"
                db.execute(f"SAVEPOINT {point}")
                try:
                    db.execute(_RECORD_FAILURE, (str(scope), at.isoformat(), reason, 0))
                except sqlite3.Error as failed:
                    db.execute(f"ROLLBACK TO {point}")
                    _storage_log.warning(
                        "the %s render's failure was not recorded (%s)",
                        scope,
                        type(failed).__name__,
                    )
                else:
                    recorded.append(scope)
                db.execute(f"RELEASE {point}")
        return tuple(recorded)

    def render_records(self) -> tuple[RenderRecord, ...]:
        """Every dashboard scope's render record, by scope — a plain read (9b).

        On a read-only connection of its own, so `doctor`, a render's look-back
        and the render timer read it off a worker, never the writer's
        connection. A row whose scope no longer parses is skipped.
        """
        return render_records_at(self._store)

    def last_render(self, scope: DataScope) -> datetime | None:
        """The instant of `scope`'s last successful render, keyed — a plain read (9b).

        `None` when none is recorded, or when the recorded instant cannot be
        read as one (logged, never raised).
        """
        try:
            with self._reader() as reader:
                row = reader.execute(
                    "SELECT last_success FROM renders WHERE scope = ?", (str(scope),)
                ).fetchone()
        except sqlite3.OperationalError as unread:
            if not self._store.exists() or "no such table" in str(unread):
                return None
            raise OperationalStoreBusy(
                f"the operational store at {self._store} could not be read for the "
                f"{scope} render's last instant ({unread}); waiting may clear it."
            ) from unread
        if row is None or row[0] is None:
            return None
        return _instant_or_none(row[0], scope=str(scope), column="last_success")

    def harvest_failure(self, instance: str) -> HarvestFailure | None:
        """The last harvest failure for `instance`, or `None` if the last one worked.

        The source `evaluate_commitment`'s required `harvest_failed` never had.
        Both "ran and learned nothing" and "ran and failed" used to persist as
        the absence of a coverage window, so once the process exited they were
        one state — and a connector with a dead token read as a sleeping laptop,
        which resolves to `UNKNOWN` and waits forever.

        `at` comes back with it. The column was written from the first commit and
        read by nothing, which made the age of a failure unknowable: "this token
        is dead" recorded three minutes ago and the same sentence recorded in
        March are one row to a reader that cannot see when it was stamped, and
        Tier 2 is never rebuilt, so there is no second place to recover it from.
        """
        row = self._db.execute(
            "SELECT reason, retryable, retry_after, at FROM harvest_failures "
            "WHERE instance = ?",
            (instance,),
        ).fetchone()
        if row is None:
            return None
        reason, retryable, retry_after, at = row
        return HarvestFailure(
            reason=reason,
            retryable=bool(retryable),
            retry_after=timedelta(seconds=retry_after) if retry_after is not None else None,
            # Stored as an ISO string by `_write_cursor`; `None` only for a row
            # written before this column was read, which no shipped version
            # produced — Tier 2 is never rebuilt, so tolerated rather than
            # assumed absent.
            at=datetime.fromisoformat(at) if at is not None else None,
        )

    def coverage_windows(self, instance: str) -> list[tuple[datetime, datetime]]:
        """AD-35 — what the sweeper consults before it may say BROKEN."""
        return [
            (datetime.fromisoformat(s), datetime.fromisoformat(e))
            for s, e in self._db.execute(
                "SELECT start, end FROM coverage WHERE instance = ? ORDER BY id", (instance,)
            )
        ]

    # ── Transcript acquisition state (story 33e): Tier 2, never the record ──
    # One row per meeting in a scope. On the record it would be a processing
    # status, which AD-33 forbids; here it is operational state like a cursor —
    # where a meeting's acquisition got to, and whether to ask again. The table
    # is created by `_SCHEMA` on open, so `SCHEMA_VERSION` does not move (see
    # the note beside it).

    def record_transcript_state(
        self,
        scope: DataScope,
        meeting_id: str,
        state: TranscriptStateKind,
        reason: str,
        *,
        retryable: bool,
        retry_after: timedelta | None = None,
    ) -> None:
        """Replace this meeting's row, stamped from the storage clock."""
        if not isinstance(state, TranscriptStateKind):
            raise TypeError(
                f"record_transcript_state was given {state!r}; a state is a "
                f"TranscriptStateKind, so a misspelled one cannot reach Tier 2"
            )
        self._db.execute(
            "INSERT INTO transcript_state "
            "(scope, meeting_id, state, reason, retryable, retry_after, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(scope, meeting_id) DO UPDATE SET state = excluded.state, "
            "reason = excluded.reason, retryable = excluded.retryable, "
            "retry_after = excluded.retry_after, at = excluded.at",
            (
                str(scope),
                meeting_id,
                state.value,
                reason,
                int(bool(retryable)),
                retry_after.total_seconds() if retry_after is not None else None,
                self._at().isoformat(),
            ),
        )
        if state is TranscriptStateKind.INGEST_FAILED:
            # The attempt count lives beside the row rather than in it: the
            # table shipped with `33e`, and `CREATE TABLE IF NOT EXISTS` cannot
            # add a column to a store that already has it (see `SCHEMA_VERSION`).
            self._db.execute(
                "INSERT INTO transcript_ingest_attempts (scope, meeting_id, attempts) "
                "VALUES (?, ?, 1) ON CONFLICT(scope, meeting_id) DO UPDATE SET "
                "attempts = attempts + 1",
                (str(scope), meeting_id),
            )
        elif state is TranscriptStateKind.INGESTED:
            # Ingested: the count of failures before it is spent, and a later
            # capture of the same meeting starts from none.
            self._db.execute(
                "DELETE FROM transcript_ingest_attempts WHERE scope = ? AND meeting_id = ?",
                (str(scope), meeting_id),
            )
        self._db.commit()

    def transcript_states(self, scope: DataScope) -> dict[str, TranscriptState]:
        """Every acquisition and ingestion row in `scope`, keyed by meeting id."""
        return {
            meeting_id: TranscriptState(
                state=state,
                reason=reason,
                retryable=bool(retryable),
                retry_after=timedelta(seconds=retry_after) if retry_after is not None else None,
                at=datetime.fromisoformat(at),
                attempts=attempts or 0,
            )
            for meeting_id, state, reason, retryable, retry_after, at, attempts in self._db.execute(
                "SELECT t.meeting_id, t.state, t.reason, t.retryable, t.retry_after, "
                "t.at, a.attempts FROM transcript_state t "
                "LEFT JOIN transcript_ingest_attempts a "
                "ON a.scope = t.scope AND a.meeting_id = t.meeting_id "
                "WHERE t.scope = ? ORDER BY t.meeting_id",
                (str(scope),),
            )
        }

    def fetched_not_ingested(self, scope: DataScope) -> tuple[str, ...]:
        """Every meeting in `scope` whose capture still awaits ingestion (story 11b).

        Only rows in `fetched` or `ingest_failed`, and only while their capture
        has failed fewer than `MAX_INGEST_ATTEMPTS` times. Never an absent,
        refused, skipped or ingested meeting, so nothing is ingested twice.
        """
        return tuple(
            meeting_id
            for meeting_id, state in self.transcript_states(scope).items()
            if state.awaits_ingestion
        )

    def ingestion_exhausted(self) -> tuple[tuple[DataScope, str, str], ...]:
        """Every capture, in any scope, whose ingestion failed too often to retry.

        `(scope, meeting_id, reason)`, the reason being the last failure's.
        Read by `pm-ai doctor`, which names each one (story 11b). A plain read
        on a read-only connection of its own (story 4r), so `doctor` sent
        through the daemon reads off its worker and never the writer's
        connection.
        """
        with self._reader() as reader:
            rows = reader.execute(
                "SELECT t.scope, t.meeting_id, t.reason FROM transcript_state t "
                "JOIN transcript_ingest_attempts a "
                "ON a.scope = t.scope AND a.meeting_id = t.meeting_id "
                "WHERE t.state IN (?, ?) AND a.attempts >= ? ORDER BY t.scope, t.meeting_id",
                (
                    TranscriptStateKind.FETCHED.value,
                    TranscriptStateKind.INGEST_FAILED.value,
                    MAX_INGEST_ATTEMPTS,
                ),
            ).fetchall()
        return tuple(
            (DataScope.parse(scope), meeting_id, reason)
            for scope, meeting_id, reason in rows
        )

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
        if row and row[0] == FAILED:
            # The skill raised: the call was made and did not return, so whether
            # the provider applied it is unknown — the same reason an in-flight
            # key is not retried. Settling it failed records that it ended; it
            # is not a licence to call again (AD-20).
            raise ReconciliationRequired(
                f"{idempotency_key} was settled as failed: its skill raised, and "
                f"whether the provider applied the change is unknown, so a retry "
                f"is not safe (AD-20). Reconcile against {target.lock_key} first."
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

        Settling a key that was never claimed is refused, not ignored. An UPDATE
        matching zero rows used to succeed silently, so a caller that settled
        the wrong key — a typo, a key minted twice — believed the outcome was
        recorded while the real claim stayed in flight and blocked every retry.
        """
        settled = self._db.execute(
            "UPDATE executed SET external_id = ?, state = ?, settled_at = ? WHERE key = ?",
            (external_id, SETTLED, self._at().isoformat(), idempotency_key),
        )
        if settled.rowcount == 0:
            self._db.rollback()
            raise ReconciliationRequired(
                f"{idempotency_key} has no claim to settle — begin_execution "
                f"never recorded it. The outcome being reported belongs to some "
                f"claim, and it is not this one: reconcile before retrying, "
                f"because whichever key was actually claimed is still in flight."
            )
        self._db.commit()

    def fail_execution(self, idempotency_key: str) -> None:
        """Settle a claimed key as failed: the skill raised (story 11b).

        Refused for a key nothing claimed, for `settle_execution`'s reason.
        """
        failed = self._db.execute(
            "UPDATE executed SET state = ?, settled_at = ? WHERE key = ? AND state = ?",
            (FAILED, self._at().isoformat(), idempotency_key, IN_FLIGHT),
        )
        if failed.rowcount == 0:
            self._db.rollback()
            raise ReconciliationRequired(
                f"{idempotency_key} has no in-flight claim to settle as failed — "
                f"begin_execution never recorded it, or it was already settled."
            )
        self._db.commit()

    def record_execution(self, idempotency_key: str, target: TargetRef, external_id: str) -> None:
        """Claim and settle in one step — for callers with no crash window."""
        self.begin_execution(idempotency_key, target)
        self.settle_execution(idempotency_key, external_id)

    def stage_unless_acted(self, proposal: Proposal) -> bool:
        """Stage `proposal` unless one under its id has left `staged` (story 11b).

        For re-ingestion. `stage_proposal` writes over by id, so a second
        ingestion of the same transcript would put a proposal the PM approved,
        rejected or let expire back to `staged`. Here a row already past
        `staged` is left exactly as it is; a `staged` one is replaced, which for
        an identical proposal changes nothing. One statement, so no transition
        can land between the check and the write. `True` when it was staged.
        """
        written = self._db.execute(
            "INSERT INTO proposals (proposal_id, body, version, state) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(proposal_id) DO UPDATE SET body = excluded.body, "
            "version = excluded.version, state = excluded.state "
            "WHERE proposals.state = ?",
            (
                proposal.proposal_id,
                _dump_proposal(proposal),
                proposal.version,
                proposal.state.value,
                ProposalState.STAGED.value,
            ),
        )
        self._db.commit()
        return written.rowcount == 1

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
            raise ProposalNotFound(
                f"no proposal is staged under {proposal_id!r}. A proposal that "
                f"expired, was executed, or was never staged is absent rather "
                f"than empty, so this is not a state to read through."
            )
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
