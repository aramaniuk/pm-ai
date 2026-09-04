"""Protocol definitions, expressed in domain types (AD-30).

Imports nothing from `pm_ai` except `pm_ai.domain`; stdlib value types
(`pathlib.Path`, `datetime`) are permitted, because a protocol has to be able to
say what it returns. Adapters implement these; core depends on them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pm_ai.domain.disclosure import DisclosureRecord
from pm_ai.domain.event_entries import EventEntry
from pm_ai.domain.events import NormalizedEvent, ObservedEventType
from pm_ai.domain.harvest import Cursor, HarvestResult, PersistResult
from pm_ai.domain.health import Probe
from pm_ai.domain.identity import DataScope, SkillPermission, TargetRef
from pm_ai.domain.vcs import TrackingVerdict


@runtime_checkable
class ConnectorPort(Protocol):
    """AD-9 — one method, and no scheduling of its own."""

    name: str
    system: str

    def emits(self) -> frozenset[ObservedEventType]:
        """The subset of the core taxonomy this connector produces (AD-27)."""

    def harvest(self, since: Cursor) -> HarvestResult:
        """Auth, fetch, map-to-schema. Read-only — class H egress (AD-1)."""

    def sample_events(self) -> tuple[NormalizedEvent, ...]:
        """At least one event of the shape this connector produces, built offline.

        Declared on the port rather than left to convention because the AD-34
        gate calls `connector.sample_events()` on every registered connector: an
        `isinstance` conformance check cannot observe a method nothing declares,
        so the gate would have been reading an attribute no contract promised.

        Contacts nothing. It exists so an architecture check can inspect a
        connector's output without a credential, a network, or a fixture per
        connector — which is what lets "no connector mints an event id" be a
        property of the *set* of connectors rather than of the two somebody
        remembered to write a test for.

        Must return a non-empty tuple, and must build its events the same way
        `harvest` does. A separate hand-written sample would drift from the real
        mapping and the gate would then be checking a decoration.
        """

    def check_health(self) -> Probe:
        """Whether this connector can currently reach its provider.

        **Reports; never raises.** One broken connector must not hide three
        others — the rule `pm_ai.platform.doctor` states and this shares the type
        with.

        Three answers matter and the enum keeps them apart. `ABSENT` is a
        connector configured with no credential stored, which is an ordinary
        first-run state rather than a broken machine. `FAILING` is a provider
        that refused or would not answer. `OK` is a provider that answered.

        Implemented per connector, never by `doctor`: what "reachable" means is
        provider-specific, and `doctor` reports registry membership without
        contacting anything. The ten-second bound is not enforced here — a
        blocking call cannot cancel itself — but by
        `pm_ai.connectors.registry.ConnectorRegistry.check_health`, which waits
        for this and abandons it at the bound.
        """


@runtime_checkable
class ScopePathPort(Protocol):
    """AD-4/AD-26 — where a scope keeps a given artifact.

    `StorageService` writes through this instead of importing the resolver:
    `pm_ai.storage` and `pm_ai.platform` are independent siblings in the import
    graph, so the composition root builds `pm_ai.platform.paths.ScopePaths` and
    passes it in. Declaring the shape here is what lets the single writer name
    its dependency without reaching across that boundary.

    One method for artifacts, deliberately. A named accessor per store
    (`operational_store`, `event_index_store`, …) would put the artifact-to-scope
    mapping on both sides of the boundary; `resolve` keeps that mapping wholly
    inside the resolver, which is the table that decides whether a record may
    exist in a scope at all.

    `gitignore` is not an exception to that rule but a case outside it: the file
    it names belongs to the repository *containing* a project scope, so no scope
    tree declares it and `resolve` cannot address it. The alternative was for the
    single writer to compose `repository(project_id) / ".gitignore"` itself,
    which is a second copy of a layout fact (AD-4) in the layer least able to
    own one.
    """

    def resolve(self, scope: DataScope, artifact: str, *, create: bool = False) -> Path:
        """The absolute path of `artifact` in `scope`; `create` makes its directory.

        Never creates the file itself — content is the single writer's alone
        (AD-5).

        Refuses rather than guessing: an unknown artifact, an artifact that does
        not exist in this scope, an unregistered project, or a subject id that
        cannot be a directory name all raise. Every refusal is a
        `pm_ai.domain.ScopeResolutionError`, which is the only exception type a
        caller may rely on — the concrete classes live in the resolver's own
        module, which callers of this port are forbidden to import.
        """

    def gitignore(self, project_id: str) -> Path:
        """The `.gitignore` of the repository project `project_id` was enrolled from.

        Returned whether or not the file exists: an absent one is precisely the
        case `assert_capture_dir_ignored` must refuse, so "missing" has to be
        readable as "no rule" rather than arriving as a resolver refusal.

        Refuses an unregistered project or an unusable id, exactly as `resolve`
        does, and by the same exception type.
        """


@runtime_checkable
class VcsPort(Protocol):
    """AD-23/AD-38 — whether version control would carry a path into a commit.

    The single writer must not write a raw capture into a directory git tracks,
    and only git can answer whether it does. Text matching cannot: a negation
    line re-includes an excluded directory, a parent-directory exclude protects a
    child no rule names, and a directory already in the index is tracked whatever
    `.gitignore` says afterwards.

    A port rather than a direct call because answering means running `git`, and
    `.importlinter` forbids `subprocess` in `pm_ai.storage` — the adapter lives in
    `pm_ai.platform`, which is the layer AD-1 permits to shell out. That is the
    same boundary `ScopePathPort` exists for, and it lands the same way: the
    composition root builds the adapter and hands it to the writer.

    Implementations answer or raise. There is no third state and no default: an
    adapter that returned "probably fine" when git was missing would be the leak
    this port exists to prevent, arriving as a fallback.
    """

    def tracking(self, path: Path, *, repository: Path) -> TrackingVerdict:
        """Git's verdict on `path`, as seen from `repository`.

        `path` need not exist. The first capture write asks about a directory
        that is about to be created, and the answer must be the same one git
        would give afterwards.

        Raises `pm_ai.domain.VcsUnavailable` for every reason the question cannot
        be answered — no repository, no `git` binary, a path outside the
        repository, a timeout, an unrecognised failure. The caller refuses on it:
        unknown is not permission.
        """

    def working_tree(self, path: Path) -> Path | None:
        """The root of the git working tree containing `path`, or `None`.

        `None` is an *answer*, not a failure: this path is not inside a working
        tree, so there is nothing for git to carry into a commit and nothing to
        be excluded from. That distinction is what lets the capture guard cover
        every scope without refusing writes on a machine where the personal scope
        is an ordinary directory.

        Asked before `tracking`, because `tracking` needs a repository to be
        asked *from*, and which repository that is cannot be known from the scope
        — the project scope lives in the employer's checkout, the personal scope
        may be a private repository of its own, and either may be neither.

        `path` need not exist. Every first capture write concerns a directory
        about to be created, and the answer must be the one git would give
        afterwards.

        Raises `pm_ai.domain.VcsUnavailable` when the question cannot be
        answered at all — no `git` binary, a timeout, an exit code with no
        documented meaning. Not being in a repository is not one of those.
        """

    def repository_marker_above(self, path: Path) -> Path | None:
        """A `.git` at or above `path`, found without running anything.

        The fallback for when `working_tree` could not answer. git is *optional*:
        a machine without it, or a project that is not a checkout, must still be
        able to record a meeting. But "pm-ai cannot find git" is not the same
        fact as "no repository exists" — the daemon runs under `launchd` with a
        minimal PATH, so it can easily miss a `git` the developer's own shell
        uses. Captures would then land in a genuinely tracked directory.

        Answering "am I inside a repository at all" needs no binary; only "would
        git ignore this" does. So this narrows the refusal to the one case that
        can actually leak: a repository demonstrably present, and no way to ask
        it anything.

        Returns the `.git` itself, so a refusal can name what it found. Never
        raises: a directory walk has no failure mode worth propagating, and one
        that could not read a parent has already been reported by `working_tree`.
        """


class KeyNotFound(LookupError):
    """No secret is stored under that name.

    `LookupError`, and deliberately not a sibling of `KeychainUnavailable`: a
    caller has to be able to tell "there is no key" from "I could not ask". The
    first is an ordinary first-run state — nothing has been stored yet. The second
    means the daemon cannot decrypt anything and must say so rather than behave as
    though the store were empty, which would present as a fresh install.
    """


class KeychainUnavailable(RuntimeError):
    """The keychain could not be consulted, so nothing is known about the key.

    Covers every reason the question cannot be answered: the `keyring` package
    absent, the OS keychain unreachable, a backend that raised. Not a verdict —
    the same reading `VcsUnavailable` takes, for the same reason. Treating it as
    "no key" is how an encrypted store gets opened as a plaintext one.
    """


class KeychainBackendMissing(KeychainUnavailable):
    """The keychain library itself is not installed, so there is nothing to ask.

    A subclass rather than a sibling: it *is* a case of the keychain being
    unavailable, and every existing `except KeychainUnavailable` must keep
    catching it. What it adds is the one distinction that changes the repair —
    an incomplete installation, fixed with a package manager, versus a keychain
    that is present and refusing, fixed by unlocking or investigating it.

    Separated because the git probe already sets that bar: it reports the binary
    and the answer separately, on the grounds that telling an operator to install
    something they already have sends them in a circle. The keychain collapsed the
    same two cases into one result until 2026-08-26 and made the reader parse a
    message to tell them apart.
    """


class KeyAlreadyEnrolled(Exception):
    """A secret is already stored under that name, so nothing was written.

    Neither a `LookupError` nor a `KeychainUnavailable`: the keychain answered,
    and the answer was that something is there. Raised by
    `KeychainPort.store_if_absent`, the only operation that can observe that
    state and decline to change it in the same step.

    Deliberately not a subclass of anything a caller already catches. Minting a
    second key makes every artifact sealed under the first permanently
    unreadable, so this refusal must never be swallowed by an `except` written
    for a keychain that could not answer.
    """


# The name the master key is enrolled under. Spelled here because two callers
# that may not import each other both need it: the composition root builds the
# lazy cipher with it, and the doctor probes for it. Two independent literals
# was the previous shape (review 2026-08-28), and a rename in one left the
# other probing a key that no longer exists — ABSENT reported on a healthy
# machine.
MASTER_KEY_NAME = "master"

# How many bytes that key is. Here rather than in `pm_ai.storage.crypto`, which
# owned it until story 4b, for exactly the reason stated above the name: two
# callers that may not import each other both need it. `pm_ai.core.enrolment`
# mints a key of this length, and the layering contract forbids `pm_ai.core`
# importing `pm_ai.storage` at all — so the alternative was a second literal
# `32` in `core`, which is the shape that produced the ABSENT-on-a-healthy-
# machine defect when it happened to the *name*. `pm_ai.storage.crypto`
# re-exports it, so `crypto.AES_KEY_BYTES` and this are one object, and the
# cipher's 32-byte refusal and the minter's 32-byte key cannot drift apart.
AES_KEY_BYTES = 32


@runtime_checkable
class KeychainPort(Protocol):
    """AD-6 — where the master key lives, so the daemon can start unattended.

    Custody only. Nothing here encrypts, derives, or wraps anything; a caller
    that has the secret decides what to do with it. Keeping this narrow is what
    lets `pm_ai.storage` receive a key as a value and stay unable to reach the OS.

    Secrets are `bytes` because a master encryption key is arbitrary bytes, not
    text. Names are `str`, and an implementation maps a name onto whatever
    (service, account) pair its backend wants — the port does not spend that
    vocabulary, so a Linux Secret Service adapter can satisfy it unchanged.
    """

    def store(self, name: str, secret: bytes) -> None:
        """Store `secret` under `name`, replacing any previous value.

        Raises `KeychainUnavailable` if the keychain could not be reached.

        Not what enrolment uses: replacing the master key destroys every
        artifact sealed under the old one. See `store_if_absent`.
        """

    def store_if_absent(self, name: str, secret: bytes) -> None:
        """Store `secret` under `name` **only if nothing is stored there**.

        One operation, not a `fetch` followed by a `store`. Two enrolments that
        both read an empty keychain would both then write, and the second would
        replace the first's key — leaving every artifact sealed in between
        permanently unreadable. The condition and the write have to be the same
        step, or the refusal is advisory.

        Raises `KeyAlreadyEnrolled` when something is already stored under
        `name`, whatever that something is. An entry too short to be a key, or
        one that is not key material at all, is still an entry: reading it as
        absence is precisely how it gets minted over.

        Raises `KeychainUnavailable` if the keychain could not be reached, and
        `KeychainBackendMissing` if there is no keychain library to ask.
        """

    def fetch(self, name: str) -> bytes:
        """The secret stored under `name`.

        Raises `KeyNotFound` when nothing is stored, never returning `None`: a
        `None` return puts the burden of the distinction on every caller, and the
        one that forgets opens an encrypted store with no key rather than
        refusing. Raises `KeychainUnavailable` if the keychain could not be
        reached.
        """

    def delete(self, name: str) -> None:
        """Remove the secret stored under `name`.

        Raises `KeyNotFound` if there was nothing to remove, so a caller cannot
        read a silent success as proof the secret is gone.
        """


class DecryptionFailed(Exception):
    """The bytes did not decrypt under this key.

    One error for every cause — a wrong key, a truncated file, a plaintext file
    read as though it were encrypted, a tampered tag — because the caller's
    response is the same in all of them and the distinctions are exactly what an
    attacker would like reported back. What must never happen is returning
    plausible-looking garbage: AES-GCM authenticates, so a failure is knowable
    rather than guessable, and this is that knowledge made explicit.
    """


@runtime_checkable
class CryptoPort(Protocol):
    """AD-6 — the cipher over the encrypted set, holding its key.

    Two artifacts are encrypted: the API credential store and the PM's own voice
    notes. Both are files, so this is a bytes-in, bytes-out envelope rather than
    anything page-level — nothing encrypted is a database.

    The key is the implementation's, not the caller's. A method taking a key
    would put it in every call site's local scope and its traceback; holding it
    once means `pm_ai.app.wiring` is the only place that ever names it.
    """

    def encrypt(self, plaintext: bytes) -> bytes:
        """Seal `plaintext`, returning a self-describing envelope.

        Encrypting the same payload twice must not give the same bytes: a
        deterministic envelope over a small credential file lets an observer
        confirm a guess at its contents.
        """

    def decrypt(self, envelope: bytes) -> bytes:
        """Open an envelope, or raise `DecryptionFailed`. Never a partial read."""


@runtime_checkable
class StoragePort(Protocol):
    """AD-5 — the single writer, behind a port."""

    def persist_events(self, events: tuple[NormalizedEvent, ...], *, scope: DataScope) -> PersistResult: ...
    def load_cursor(self, instance: str) -> Cursor: ...
    def save_cursor(self, instance: str, cursor: Cursor, coverage: object) -> None: ...
    def was_executed(self, idempotency_key: str) -> bool: ...
    def append_event_log(self, entry: EventEntry, *, scope: DataScope) -> None: ...
    # Reads, added with story 2h. The port declared only writes, so an accessor
    # over `event_log/` had nothing to depend on and would have had to reach the
    # concrete service — or a path. Two methods rather than one because a
    # bounded read decides which segments to open and must see their names.
    def append_disclosure(self, record: DisclosureRecord, *, scope: DataScope | None = None) -> None: ...
    def read_disclosure(self) -> str: ...
    def event_log_segments(self, *, scope: DataScope) -> tuple[str, ...]: ...
    def read_event_log_segment(self, *, scope: DataScope, name: str) -> str: ...
    # AD-20 is two-phase, and this port declared only the one-shot form until
    # 2026-08-22. The key is *claimed* before the outbound call and *settled*
    # after, because recording only on success leaves a crash window in which
    # the mutation happened and the ledger does not know — the retry then acts
    # twice. `pm_ai.skills.registry` calls all three of these; a port that named
    # none of them left the class enforcing AD-18 and AD-20 typed as `object`,
    # which is how the security boundary became the least-checked code here.
    def begin_execution(self, idempotency_key: str, target: TargetRef) -> str: ...
    def settle_execution(self, idempotency_key: str, external_id: str) -> None: ...
    def executed_mutations(self) -> dict[str, tuple[str, str]]: ...
    # The single-phase convenience over the two above. Kept because it has a
    # caller; not what the registry uses.
    def record_execution(self, idempotency_key: str, target: TargetRef, external_id: str) -> None: ...

    # Artifacts, added with story 8f. This port declared nine methods and
    # neither the single writer nor the single reader, while `StorageService`
    # implemented both — so the Protocol under-declared its own implementation
    # and anything typed against it could reach every ledger and no artifact,
    # which is the whole of AD-3's tiering contract as far as a port consumer
    # can see. The same absence story 2h closed for the event log; filed as A1
    # by the wave-1 review and downgraded because `Daemon.storage` happens to be
    # the concrete class.

    def write_artifact(
        self, payload: bytes, *, scope: DataScope, artifact: str, name: str | None = None
    ) -> Path: ...

    def read_artifact(
        self, *, scope: DataScope, artifact: str, name: str | None = None
    ) -> bytes | None:
        """The artifact's bytes, or `None` when nothing has been written there.

        `bytes | None` rather than `bytes`, because absence is the ordinary
        state of every optional artifact on a clean machine and a first run is
        not a failure. The implementation ended in `path.read_bytes()` until
        story 8f, so each caller that needed "not there yet" wrote its own
        translation of `FileNotFoundError` — and the one that forgets aborts on
        a machine that is merely new.

        Absence alone is a value. A directory in the way, a permission refusal,
        an undecryptable file: those raise, because reporting them as "no file"
        is how a machine that cannot read its own configuration looks freshly
        installed.
        """

    def list_collection(self, *, scope: DataScope, artifact: str) -> tuple[str, ...]:
        """The member names a declared `Collection` currently holds.

        Names, not paths, and that is the load-bearing half. A caller reaching
        this port may not learn where an artifact lives — the resolver is the
        only thing that knows (story 1a) — and a listing of paths would be a way
        to open one directly, which is AD-5's single writer routed around.

        Empty when nothing has been written yet; asking must not create the
        directory. Raises `pm_ai.domain.ScopeResolutionError` when `artifact` is
        not a `Collection` at all: a `File` has no members, and a `Dir`'s are
        declared in the scope trees rather than discovered on disk.
        """


@runtime_checkable
class SkillPort(Protocol):
    """AD-1 class M — the only egress that mutates."""

    name: str
    system: str
    permission: SkillPermission

    def execute(self, target: TargetRef, payload: dict) -> str:
        """Perform the mutation, return the external id it produced."""


@runtime_checkable
class ConfigPort(Protocol):
    """The settings `config.toml` carries, named where `pm_ai.core` is unreachable.

    `pm_ai.core.config.Config` is the only implementation and always will be —
    this exists because `pm_ai.ports` may import nothing but `pm_ai.domain`
    (`.importlinter`'s `ports-depend-only-on-domain`), so `DaemonPort` below has
    no way to say `Config` and would otherwise say `Any`.

    Read-only members, deliberately: `Config` is frozen, and a protocol
    declaring settable attributes would claim a surface may write one.
    """

    @property
    def blended_hourly_rate(self) -> float: ...

    @property
    def pm_handle(self) -> str: ...

    @property
    def verbose_logging(self) -> bool: ...


@runtime_checkable
class DaemonPort(Protocol):
    """AD-30 — what a surface may see of the daemon the composition root built.

    `pm_ai.app.wiring.Daemon` is the implementation, and no surface may name it:
    `surfaces` sits *below* `app` in the enforced layer stack, so
    `pm_ai.surfaces.cli.dispatch` cannot import it and an unannotated parameter
    there would be implicitly `Any` — the defect story `1k` retired from
    `SkillRegistry`, reintroduced in the most branch-heavy module of the wave.

    Four members, and no more. The CLI reads settings, reaches the single writer,
    enrols the master key, and knows which scope it is acting in; everything else
    `Daemon` holds — the connectors, the skill registry, the cipher — is the
    daemon's own business, and naming it here would make it the CLI's.
    """

    @property
    def storage(self) -> StoragePort: ...

    @property
    def keychain(self) -> KeychainPort: ...

    @property
    def config(self) -> ConfigPort: ...

    @property
    def scope(self) -> DataScope: ...
