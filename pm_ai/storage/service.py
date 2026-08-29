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

import errno
import json
import os
import re
import sqlite3
import stat
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from pm_ai.domain import clocks
from pm_ai.domain.disclosure import assert_writable
from pm_ai.domain.event_entries import EventEntry, render_entry
from pm_ai.domain.events import NormalizedEvent
from pm_ai.domain.harvest import Cursor, PersistResult
from pm_ai.domain.identity import DataScope, ScopeKind, SourceRef, TargetRef
from pm_ai.domain.lifecycle import ProposalState
from pm_ai.domain.proposals import Proposal
from pm_ai.domain.storage_tiers import (
    CAPTURES,
    CAPTURE_STAGING,
    EVENT_LOG,
    GITIGNORE_FILENAME,
    OPERATIONAL_DB,
    Tier,
    UnprotectedCaptureDir,
    assert_capture_dir_untracked,
    gitignore_rule_for,
    is_append_only,
    requires_git_exclusion,
)
from pm_ai.domain.vcs import VcsUnavailable
from pm_ai.ports import CryptoPort, ScopePathPort, VcsPort
from pm_ai.storage.crypto import (
    ENCLAVE_DIR_MODE,
    ENCRYPTED_FILE_MODE,
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
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""

# What this code expects the operational store to look like. Bumped in the same
# commit that appends to `MIGRATIONS`, never separately: a version with no
# migration behind it stamps a store that was never changed, and a migration
# with no version behind it runs on every open.
SCHEMA_VERSION = 1

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


class SchemaVersionTooNew(RuntimeError):
    """The store was written by a later version of pm-ai than this one.

    Refusing is the only response that cannot make things worse. A later version
    may have added columns this code does not know about; opening the store and
    proceeding writes rows it will then misread, and the corruption surfaces long
    after the mistake — in Tier 2, which no rebuild reconstructs (AD-3). There is
    no automatic downgrade, because a downgrade would mean deciding what to do
    with data this code cannot interpret.
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
        crypto: CryptoPort,
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
        """
        self._paths = paths
        self._crypto = crypto
        self._git_checked: set[tuple[object, ...]] = set()
        self._now = now
        self._vcs = vcs
        # Tier 2 is its own file, in the application scope's enclave and outside
        # every scope's Markdown tree. `reindex` targets Tier 3 and therefore
        # cannot reach this, which is the structural guarantee AD-3 asks for.
        store = paths.resolve(APPLICATION, OPERATIONAL_DB, create=True)
        try:
            self._db = sqlite3.connect(store, check_same_thread=False)
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
            self._db.executescript(_SCHEMA)
            self._db.commit()
            self._migrate(preexisting=preexisting)
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

    def _assert_git_excludes(self, scope: DataScope, artifact: str) -> None:
        """Refuse a raw capture that git would carry into a commit (AD-23, AD-43).

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
        if seen in self._git_checked:
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
                f"it ({unanswered}). A capture written here may be committed and "
                f"a verbatim transcript in a repository is not recoverable. "
                f"Install git, or put it on the daemon's PATH."
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

    def _append(self, path: Path, text: str) -> None:
        """Append to a plaintext artifact, or refuse if it is declared encrypted.

        There is no such thing as appending to a sealed file. AES-GCM covers the
        whole payload, so an append would mean read, decrypt, concatenate,
        re-seal, rewrite — and rewriting a ledger in place is precisely what AD-5
        forbids and what segmented compaction exists to avoid.

        So declaring a ledger encrypted is refused loudly here rather than
        honoured by quietly rewriting the file. No ledger is encrypted today; this
        is the guard that makes changing that a decision rather than an accident.
        """
        if is_encrypted(str(path)):
            raise AppendToSealedArtifact(
                f"{path} is declared encrypted, and appending to a sealed file "
                f"means rewriting it whole — which AD-5 forbids for a ledger and "
                f"segmented compaction exists to avoid. Either the declaration is "
                f"wrong, or this artifact needs a segment-per-write shape."
            )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text)

    def _create_exclusively(self, path: Path, text: str, *, staging: Path) -> None:
        """Create an artifact that must not exist yet, sealing it if declared.

        Exclusive creation is the capture path's guarantee: two recordings must
        never splice into one file. Sealing does not change that — the name is
        claimed the same way whether the bytes are ciphertext or not.

        Published with `os.link`, so the name appears only once the content is
        complete *and* a name already taken is refused. `os.replace` would give
        the first property and destroy the second, silently splicing the two
        recordings this refusal exists to keep apart.
        """
        payload = text.encode("utf-8")
        sealed = is_encrypted(str(path))
        self._publish(
            path,
            self._crypto.encrypt(payload) if sealed else payload,
            staging=staging,
            mode=ENCRYPTED_FILE_MODE if sealed else None,
            exclusive=True,
        )

    def _replace(self, path: Path, payload: bytes) -> None:
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
        """
        sealed = is_encrypted(str(path))
        self._publish(
            path,
            self._crypto.encrypt(payload) if sealed else payload,
            staging=path.parent,
            mode=ENCRYPTED_FILE_MODE if sealed else None,
            exclusive=False,
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

        `fsync` before publishing and on the directory after: without the first,
        a crash can leave a visible, complete-looking name whose content never
        reached stable storage, which is the same problem one layer down.
        """
        parent = target.parent
        enclave = mode is not None
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

    def write_artifact(
        self, payload: bytes, *, scope: DataScope, artifact: str, name: str | None = None
    ) -> Path:
        """Write a declared artifact whole, sealed or not as the model says.

        The entry point for the encrypted set — the credential store and the PM's
        voice notes — and the reason no caller decides whether to encrypt. Callers
        name *what* they are writing; the declaration in the scope trees decides
        *how*.

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
        self._assert_git_excludes(scope, artifact)
        target = self._paths.resolve(scope, artifact)
        if name is not None:
            target = target / name
        self._replace(target, payload)
        return target

    def read_artifact(
        self, *, scope: DataScope, artifact: str, name: str | None = None
    ) -> bytes:
        """Read a declared artifact, unsealing it if the model says it is sealed.

        `name` is validated as `write_artifact` validates it — this writer only
        ever mints single-component names, so a traversal here is a request to
        read something the layout never placed in this directory.
        """
        if name is not None:
            name = _capture_name(name)
        target = self._paths.resolve(scope, artifact)
        if name is not None:
            target = target / name
        return self._read(target)

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
        self._append(self._segment(scope, EVENT_LOG, at), render_entry(named) + "\n")

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
                suspect = (("occurred_at_flag", "implausible"),)
                flagged += 1
            # The format lives in `render_entry` (story 2d), not here. A writer
            # that formats its own line is a second grammar in the ledger, which
            # is what left four of them reaching one file.
            lines.append(
                render_entry(
                    EventEntry(
                        entry_id=_ulid(),
                        category=stamped.type,
                        actor=stamped.actor.actor_id,
                        fields=(
                            ("ingested_at", at.isoformat()),
                            ("src", str(stamped.source_ref)),
                            (
                                "occurred_at",
                                stamped.occurred_at.isoformat()
                                if stamped.occurred_at
                                else "unknown",
                            ),
                            ("authored_by", stamped.authored_by.value),
                        )
                        + suspect,
                    )
                )
            )
            persisted += 1
        if lines:
            self._append(self._segment(scope, EVENT_LOG, at), "\n".join(lines) + "\n")
        return PersistResult(
            persisted=persisted, duplicates=duplicates, at=at, flagged=flagged
        )

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
