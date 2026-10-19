"""Protocol definitions, expressed in domain types (AD-30).

Imports nothing from `pm_ai` except `pm_ai.domain`; stdlib value types
(`pathlib.Path`, `datetime`) are permitted, because a protocol has to be able to
say what it returns. Adapters implement these; core depends on them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

from pm_ai.domain.disclosure import DisclosureRecord
from pm_ai.domain.event_entries import EventEntry
from pm_ai.domain.events import NormalizedEvent, ObservedEventType
from pm_ai.domain.harvest import (
    Cursor,
    HarvestFailure,
    HarvestRecord,
    HarvestResult,
    PersistResult,
)
from pm_ai.domain.health import Probe
from pm_ai.domain.identity import DataScope, SkillPermission, TargetRef
from pm_ai.domain.lifecycle import CoverageWindow
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.model_calls import ModelAnswer, ScopeFlow
from pm_ai.domain.renders import RenderRecord
from pm_ai.domain.sanitize import Sanitized
from pm_ai.domain.task_classes import TaskClass
from pm_ai.domain.transcripts import (
    StoredPart,
    TranscriptOutcome,
    TranscriptPart,
    TranscriptState,
    TranscriptStateKind,
)
from pm_ai.domain.vcs import TrackingVerdict
from pm_ai.domain.work import ProjectWork, WorkChange


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
        gate calls `connector.sample_events()` on every connector a composed
        daemon holds: an `isinstance` conformance check cannot observe a method
        nothing declares, so the gate would have been reading an attribute no
        contract promised.

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
        provider-specific, and `doctor` contacts no provider. The ten-second
        bound is not enforced here — a
        blocking call cannot cancel itself — but by
        `pm_ai.connectors.registry.ConnectorRegistry.check_health`, which waits
        for this and abandons it at the bound.
        """


@runtime_checkable
class CalendarReadPort(Protocol):
    """A connector's calendar alone, for a live read of the day (2026-10-12).

    Optional beside `ConnectorPort`, never part of it: AD-9's harvest surface
    stays closed, and a connector without this method keeps being asked through
    `harvest`. The dashboard's live calendar read calls this when a connector
    has it, so a connector that also harvests other resources — Graph's listed
    Teams channels (`33d`) — reads only the calendar on every render, and a
    failure in a resource the dashboard did not need cannot mark its calendar
    unread.
    """

    def read_calendar(self, start: datetime, end: datetime) -> HarvestResult:
        """The calendar alone over `start` to `end`, two aware UTC instants (`23e`).

        The render names its day: local midnight to the next local midnight in
        `display_timezone`, so a daylight-saving day is 23 or 25 hours. The
        harvest's window, cursor and coverage play no part — the read persists
        nothing, and its caller saves no cursor and records no coverage from it.
        """


class DuplicateConnector(Exception):
    """Two connectors under one instance name, so one of them would be lost.

    Declared here rather than in `pm_ai.connectors.registry`, where story `8d`
    first wrote it, because story `8b` raises the same refusal from
    `pm_ai.core.connector_enrolment` — and `core` sits *below* `connectors` in
    the enforced layer stack and may not import it. The alternatives were two
    classes with one name in two packages that cannot see each other, so a
    caller's `except` would catch one of them and let the other through, or a
    second spelling of a refusal an operator has to recognise once. `ports` is
    the layer both may reach, and the exception cluster below already lives
    here for the same reason.

    `pm_ai.connectors.registry` re-exports it, so `from
    pm_ai.connectors.registry import DuplicateConnector` still names this class
    and there is nothing to keep in step.

    Refused rather than resolved by last-write-wins. The instance name is what a
    cursor, a coverage window and a stored credential are all keyed by, so two
    connectors sharing one would interleave their cursors — each re-harvesting
    from where the other left off — and the symptom would be missing events,
    weeks later, with nothing in the logs.

    Not a `KeyError` or a `ValueError`: a caller catching either of those is
    catching something else, and this is a refusal that should stop a daemon
    starting rather than be absorbed by a broad `except`.
    """


class ProbeFailed(Exception):
    """A credential was offered to its provider and the provider said no.

    Raised by whatever performs the live check story `8b` runs *before* anything
    is stored, so a bad token is refused while the human who typed it is still
    present rather than discovered by a silent harvest at 03:00.

    Declared here for `DuplicateConnector`'s reason, one layer along: the probe
    is an adapter in `pm_ai.connectors` because `core-is-io-free` forbids every
    HTTP client in `pm_ai.core`, and the core service that catches this may not
    import that package. A parameter carries the probe in; this carries its
    refusal back out.

    Carries no credential material, ever — not in its message, not in its
    `args`, not in the traceback of whatever it was raised from. A refusal that
    quotes the token puts it in the operator's scrollback and in any bug report
    that follows.
    """


class ProbeUnreachable(ProbeFailed):
    """The provider never answered, so nothing is known about the credential.

    A subclass, so `except ProbeFailed` keeps refusing both — the enrolment
    declines either way, and it declines for the same reason: an unverified
    credential must not be stored. What the subclass adds is the distinction
    that changes the repair. A rejected token is fixed by issuing another one;
    a provider that went silent past CAP-35's ten-second bound is fixed by
    looking at the network, and telling an operator to re-issue a token that is
    perfectly good sends them in a circle.

    The same reading `KeychainUnavailable` takes against `KeyNotFound`, and the
    same one `VcsUnavailable` takes: unknown is not a verdict.
    """


class UnknownConnectorSystem(LookupError):
    """pm-ai has no probe for that provider, so it cannot verify a credential.

    Not a `ProbeFailed`: nothing was asked and nothing refused. Storing a
    credential nobody can check would be enrolling a connector that cannot
    harvest, which is the state `8b` exists to keep off the disk.
    """


class ArtifactBusy(RuntimeError):
    """Another process holds the exclusive claim on this artifact.

    `private/config.json` is one sealed file holding every connector's
    credential, and it is rewritten whole. Two enrolments that both read it and
    both write it destroy one credential — the read-modify-write rule failing
    one level down — so the read and the write are held under a claim, and a
    second attempt refuses rather than waiting.

    Refusing rather than blocking is deliberate: enrolment is a command a human
    is waiting on, at a prompt, and a second one is a mistake rather than a
    queue. The refusal names the artifact and its scope, says nothing was
    changed and asks for the command to be run again. It names no file to
    remove, because there is none: the claim is a kernel lock, released with
    its holder however that holder died (story `1r`).
    """


@runtime_checkable
class CredentialProbePort(Protocol):
    """A live check that a credential is one the provider accepts (CAP-35).

    A Protocol over a call rather than an object with a method, because the one
    implementation is a function in `pm_ai.connectors` and the one consumer is
    `pm_ai.core.connector_enrolment`, which receives it as a parameter. Naming
    the shape here is what lets that parameter be typed at all: `core` may not
    import the adapter, and an unannotated parameter is implicitly `Any` — the
    defect story `1k` retired.

    Returns a short sentence naming what answered, for the operator. It must
    never contain the credential.

    Raises `ProbeFailed` when the provider refused, `ProbeUnreachable` when it
    did not answer within the bound, and `UnknownConnectorSystem` when pm-ai has
    no probe for that provider at all.
    """

    def __call__(self, system: str, credential: str) -> str: ...


@runtime_checkable
class GraphAuthPort(Protocol):
    """Delegated Microsoft Graph auth, as the rest of pm-ai may see it (story 33a).

    Declared here for the reason every port is: the adapter lives in
    `pm_ai.connectors` because the device-code flow speaks HTTP to the token
    endpoint (`.importlinter`'s `http-confined-to-adapters`), and nothing below
    `connectors` may import it. Token **acquisition** is an adapter's; token
    **custody** is `pm_ai.platform`'s keychain and the sealed store behind it.

    The three refusals this protocol's implementations raise are deliberately
    distinct, because their remedies are: a stale credential needs a new
    sign-in (`pm-ai connector sign-in <instance>`, story 8m), an unreachable
    endpoint needs waiting, and conditional access needs an interactive sign-in
    that neither of the other two would produce.
    The concrete classes live beside the adapter — a caller that needs to tell
    them apart imports them from there, exactly as `ScopePathPort`'s callers
    catch `pm_ai.domain.ScopeResolutionError` rather than the resolver's own
    classes.

    Nothing here returns, logs or formats a refresh token except `sign_in`,
    whose whole purpose is to hand one to enrolment for sealing.
    """

    instance: str
    """The connector instance this auth signs in for (story 8m).

    What a remedy names: `pm-ai connector sign-in <instance>`. Declared here so
    the port still satisfies the Graph client's narrower `TokenSource`.
    """

    scopes: frozenset[str]
    """Every permission this connector will ever ask consent for, in one place.

    Compared against what the provider actually granted on **every**
    acquisition, not only at enrolment: an administrator who revokes one
    permission afterwards must stop the connector rather than leave it failing
    at whichever resource happens to need that scope.
    """

    def sign_in(self, present: Callable[[str], None]) -> str:
        """Run the interactive flow and return the credential to seal.

        `present` receives the sentence the human acts on — a code and a URL.
        The implementation opens no browser and handles no password: the PM
        signs in themselves, wherever they already are.

        Returns the credential enrolment stores, which is the only value in this
        protocol that carries secret material. It is never logged, never
        returned by anything else here, and never written to a connector's
        unencrypted configuration.
        """

    def access_token(self, *, force_refresh: bool = False) -> str:
        """A bearer token for Graph, refreshed silently and without prompting.

        `force_refresh` discards a cached access token and asks the provider
        again. It is what a harvester calls after a page returns 401 on a
        credential that was valid when the walk started — the alternative is
        reporting a long harvest as stale on a perfectly good credential.
        """

    def check_health(self) -> Probe:
        """Whether this machine can currently obtain a Graph token.

        **Reports; never raises**, like every other probe. `ABSENT` when nothing
        has been enrolled — a fresh install is not a broken machine — and
        `FAILING` with a detail that says *which* refusal it was, because the
        three remedies are three different pieces of advice.
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


@runtime_checkable
class ClaimPort(Protocol):
    """An exclusive, cross-process claim on one file (story `1r`).

    Until the daemon exists every `pm-ai` command is the writer, and what stops
    two of them losing each other's read-modify-write over a file rewritten
    whole is a claim held across the read and the write. The mechanism is a
    kernel lock — released with the process however it dies — and that is an
    OS API, so it sits behind this port with its adapter in `pm_ai.platform`
    (AD-26), handed to the single writer by the composition root exactly as
    `VcsPort` is. The shape is what makes a Windows adapter possible later.
    """

    def exclusive(self, guarded: Path) -> AbstractContextManager[None]:
        """Hold the claim on `guarded` for the body of a `with`, or refuse at once.

        Keyed on the file, not on its spelling: two paths reaching one file
        share one claim. Raises `pm_ai.domain.claims.ClaimHeld` on entry when
        another holder has it — never waits — and `OSError` for anything that
        is not "busy": a filesystem with no lock semantics, a permission fault,
        a sidecar that is not a plain file. Released however the body leaves.
        Not re-entrant: the thread holding the claim that asks again gets
        `pm_ai.domain.claims.ClaimReentered`, a programming error naming the
        path — never `ClaimHeld`, which would blame another holder that does
        not exist (AD-5, story 8q). Another thread holding it is `ClaimHeld`.
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


class MalformedCaptureName(ValueError):
    """A capture filename that is not a single name inside the capture directory.

    The name reaches this service from a meeting id, a dropped filename, or a
    connector handle, so none of it is the daemon's own string — and it is
    interpolated into a path. `pm_ai.platform.paths` validates the subject ids it
    interpolates for the same reason; this is the one path component that module
    never sees, because it names a record rather than a scope.

    Declared here with `StoragePort.write_capture` since story 33e (AD-49): the
    acquisition in `pm_ai.core` catches it, and `core` may not import the
    storage adapter. `pm_ai.storage.service` re-exports it.
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


class TranscriptSourceUnavailable(RuntimeError):
    """A transcript source that cannot be asked anything at all.

    The daemon's Graph source when no Graph connector is enrolled: a stand-in
    that refuses by name rather than a missing key, so a caller holding the
    port has one thing to catch. About the port's contract, so declared beside
    it (AD-49). Nothing about any meeting is learned from it, so no meeting's
    state is recorded.
    """


@runtime_checkable
class TranscriptSourcePort(Protocol):
    """AD-23 — where a meeting's transcript is acquired from (story 33e).

    Implemented by the Graph adapter here and by the manual adapter (`11c`'s
    dropped file); both yield captures. Reading a capture's text is
    `pm_ai.core.transcripts`' separate contract (story 11b).

    Read-only and class H egress (AD-1) for a networked source.
    """

    name: str
    requires_network: bool

    def acquire(
        self,
        meetings: Sequence[Meeting],
        *,
        since: datetime,
        stored: Mapping[str, Sequence[StoredPart]],
    ) -> tuple[TranscriptOutcome, ...]:
        """One outcome per meeting reached, in the order asked; never raises per meeting.

        `since` is the start of the harvest's reach-back: no meeting asked about
        starts before it, and a source that lists transcripts may stop listing
        once it reaches ones created well before it.

        `stored` holds, by meeting id, the parts of each meeting's transcript
        already stored (story 33g); a meeting absent from it holds none. A
        source returns only new parts, in time order, in one
        `TranscriptCaptured`, and reads the stored parts' instants from here,
        not from its listing, when it tests a new part against them.

        A meeting missing from the answer was not reached: a provider fault
        (a throttle, a server error, no answer) stops the run at the meeting it
        struck, which is refused retryably with the provider's hint, and the
        meetings after it wait for the next run. Raises only
        `TranscriptSourceUnavailable`, when nothing can be asked at all.
        """


@runtime_checkable
class StoragePort(Protocol):
    """AD-5 — the single writer, behind a port."""

    def persist_events(self, events: tuple[NormalizedEvent, ...], *, scope: DataScope) -> PersistResult: ...
    def load_cursor(self, instance: str) -> Cursor: ...

    def save_cursor(
        self,
        instance: str,
        cursor: Cursor,
        coverage: CoverageWindow | None,
        failure: HarvestFailure | None,
    ) -> None:
        """Where a harvest got to, what it covered, whether it failed (AD-35).

        `coverage` was typed `object` here and read through three `getattr` calls
        in the single writer, so "accepts an absent window explicitly" was a
        claim no type carried: any object at all satisfied it, and a caller
        handing over the wrong thing was a runtime discovery rather than a mypy
        error. `CoverageWindow | None` is the rule — absence is the ordinary
        answer for a provider that returned nothing, and it is now the *only*
        other thing this parameter takes.

        **`failure` has no default, and that is the same rule one field over.**
        Passing `None` *deletes* the `harvest_failures` row — the write that says
        "this connector is working again" — so a default would let any
        three-argument call silently repair a dead connector, and
        `evaluate_commitment` would read a broken machine as patience. It is
        stated for the reason `harvest_failed`, `HarvestFailure.retryable` and
        `WindowPolicy`'s two widths are all required: the caller knows which of
        the three outcomes it had, and a default here would answer on its behalf.
        """

    def harvest_failure(self, instance: str) -> HarvestFailure | None:
        """The last harvest failure for `instance`, or `None` if the last one worked.

        Declared here because `evaluate_commitment` requires `harvest_failed` and
        had no source for it: a failed harvest and an empty one both persisted as
        the absence of a coverage window, so after a restart they were one state
        and a dead credential resolved to `UNKNOWN` — patience, forever.
        """
    def record_harvest_failure(
        self, instance: str, failure: HarvestFailure, *, after_save: bool = False
    ) -> None:
        """A failed attempt, position and coverage unchanged (story 9a).

        `after_save`: the same attempt saved its position first, so the
        success that save stamped is taken back.
        """
    def record_transcript_refusals(self, instance: str, refusals: Sequence[str]) -> None:
        """What the last attempt's transcript fetch and ingestion refused (9a)."""
    def prune_coverage(self, *, before: datetime) -> int:
        """Remove coverage records ending before `before`; how many went (9a)."""
    def harvest_records(self) -> tuple[HarvestRecord, ...]:
        """Every instance's attempts and current failure, a plain read (9a)."""
    def record_project_work(self, scope: DataScope, change: WorkChange) -> int:
        """Merge one GitLab harvest's members, issues and merge requests (story 8t).

        One claimed step, asking git nothing: the current rows of `(scope,
        change.instance)` are read, merged and written in one transaction.
        How many members lists, issues and merge requests changed.
        """
    def project_work(self, scope: DataScope) -> ProjectWork:
        """A project's GitLab members, issues and merge requests — the one reader (8t).

        `23g` and `7e` read nothing else of it. A plain read. Refuses a scope
        that is not a project (`ValueError`), as `record_project_work` does.
        """
    def record_render(self, scope: DataScope, *, at: datetime) -> None:
        """A successful render of `scope` at `at`, in one statement (9b)."""
    def record_render_failure(
        self, scope: DataScope, reason: str, *, at: datetime, retryable: bool = False
    ) -> None:
        """A failed render of `scope` at `at`, last success unchanged, one statement (9b)."""
    def render_records(self) -> tuple[RenderRecord, ...]:
        """Every dashboard scope's render record, a plain read (9b)."""
    def last_render(self, scope: DataScope) -> datetime | None:
        """`scope`'s last successful render instant, a keyed plain read (9b)."""
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
    # Story 11b: a skill that raises settles its key as failed rather than
    # leaving it in flight, so ingestion can stage the extraction and go on.
    def fail_execution(self, idempotency_key: str) -> None: ...

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

    # Two more, added with story 8b. Both exist because that slice writes *two*
    # artifacts in a mandated order and must leave nothing behind if either
    # refuses — a guarantee neither of the methods above can give on its own.

    def assert_writable(self, *, scope: DataScope, artifact: str) -> None:
        """Ask now every question a write to `artifact` would ask, and write nothing.

        The pre-flight. A declared-gitignored artifact's write is refused when
        git cannot say whether the directory would be committed, and for an
        enrolment that refusal lands on the *second* write, after the credential
        is already sealed — so every attempt on a machine whose `$HOME` is a
        repository without the rule would orphan a credential. Asking first is
        what makes "nothing written" true rather than aspirational.

        Answers by returning. Raises exactly what the corresponding write would
        raise, and creates neither the file nor its directory.
        """

    def exclusive(self, *, scope: DataScope, artifact: str) -> AbstractContextManager[None]:
        """Hold an exclusive claim on `artifact` for the body of a `with`.

        For a read-modify-write over a whole file. `write_artifact` publishes by
        `os.replace`, which overwrites deliberately and coordinates nothing, so
        two processes that both read `private/config.json`, both add a
        credential and both write it back leave one credential behind — the
        exact loss the read-modify-write rule was written to prevent, one level
        down.

        Refuses rather than waits: `ArtifactBusy` when the claim is already
        held, naming the artifact and its scope. Enrolment is a command a human
        is standing at, so a second one concurrently is a mistake and not a
        queue. A holder that was killed holds nothing — the claim is a kernel
        lock on a sidecar beside the file, released with the process — so there
        is no orphan to remove and the refusal names none.

        An `ArtifactBusy` raised *inside* the body is the body's own refusal
        (an inner file that was busy), never re-reported as this artifact.
        A fault that is not "busy" — no lock support on the filesystem, a
        permission error on the sidecar — is an `OSError`, reported as such.

        The claim is released however the body leaves.

        Not re-entrant: the thread already holding it that asks again gets
        `pm_ai.domain.claims.ClaimReentered`, never `ArtifactBusy` (AD-5,
        story 8q).
        """

    def holds_claim(self, *, scope: DataScope, artifact: str) -> bool:
        """Whether *this thread* holds `exclusive`'s claim on `artifact` now (story 8q).

        For a writer told `claimed=True` to check it rather than trust it: a
        caller that said so without holding the claim would write unserialized.
        Per thread, as the re-entry guard is; it cannot see asyncio tasks that
        share one thread.
        """

    # Captures and their acquisition state, added with story 33e. `write_capture`
    # existed on the single writer and not here — the under-declaration `8f`
    # closed for artifacts — so the acquisition in `core` had nothing to write
    # a transcript through.

    def write_capture(self, body: str, *, scope: DataScope, name: str) -> Path:
        """Write one raw capture into `scope`'s `transcripts/`, exclusively.

        Asks git about the final directory first (AD-43), stages in
        `transcripts/temp/` and links into place (AD-47). Refuses with
        `pm_ai.domain.UnprotectedCaptureDir`, `MalformedCaptureName`,
        `EmptyCapture` or `CaptureAlreadyExists` — never overwrites, never
        appends.
        """

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
        """Where one meeting's transcript acquisition got to (Tier 2, AD-33).

        One row per meeting in a scope, replaced on every write and stamped
        from the storage clock. Never on the meeting record.
        """

    def transcript_states(self, scope: DataScope) -> dict[str, TranscriptState]:
        """Every recorded acquisition and ingestion state in `scope`, by meeting id."""

    def record_pending_part(
        self,
        scope: DataScope,
        meeting_id: str,
        part: int,
        *,
        transcript_id: str,
        created: datetime,
        ended: datetime,
        reason: str | None = None,
    ) -> None:
        """Claim a part of a meeting's transcript before its capture is written (33g).

        A pending row under the same number is replaced; a stored one refuses.
        """

    def mark_part_stored(self, scope: DataScope, meeting_id: str, part: int) -> None:
        """A pending part's capture is on disk; resets the meeting's ingestion attempts."""

    def drop_pending_part(self, scope: DataScope, meeting_id: str, part: int) -> bool:
        """Remove a pending part whose write was refused for good."""

    def mark_part_ingested(self, scope: DataScope, meeting_id: str, part: int) -> None:
        """A stored part was ingested and is never extracted again."""

    def transcript_parts(self, scope: DataScope) -> dict[str, tuple[TranscriptPart, ...]]:
        """Every part row in `scope`, by meeting id, in part order (Tier 2, 33g)."""

    def record_part_ask(self, scope: DataScope, meeting_id: str, *, asked_at: datetime) -> None:
        """When a run that asked the source about this meeting started (33g)."""

    def transcript_part_asks(self, scope: DataScope) -> dict[str, datetime]:
        """When each meeting in `scope` was last asked about, by meeting id (33g)."""

    def fetched_not_ingested(self, scope: DataScope) -> tuple[str, ...]:
        """The meetings in `scope` whose capture still awaits ingestion (story 11b).

        Fetched with no ingestion row, or whose ingestion failed fewer than
        `pm_ai.domain.transcripts.MAX_INGEST_ATTEMPTS` times.
        """

    def ingestion_exhausted(self) -> tuple[tuple[DataScope, str, str], ...]:
        """Every capture whose ingestion failed `MAX_INGEST_ATTEMPTS` times.

        `(scope, meeting_id, last reason)`, across every scope. `pm-ai doctor`
        names each (story 11b).
        """

    def remove_member(self, *, scope: DataScope, artifact: str, name: str) -> bool:
        """Remove one member of a declared `Collection`; `True` if it was there.

        For the one legitimate move of a meeting record between day files —
        adoption by a calendar arrival (story 11b). Ledgers are refused.
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
class ModelPort(Protocol):
    """AD-12's consumer clause and AD-15's declared task class, at one boundary.

    AD-12 asks for sanitization "at the consumer, not only at the producer",
    through a port that "accepts only that type for externally-sourced text".
    Until story 8e neither half existed: `pipelines.run_harvest` called
    `sanitize(...)` and discarded the result under a comment claiming the
    protection, and nothing in `pm_ai/` named `Sanitized` at all. A producer-side
    rule is one forgotten call site away from being false, which is exactly what
    that line was.

    **`external` is `Sanitized`, never `str`.** That is the whole mechanism:
    omitting sanitization stops being a review catch and becomes a type error at
    the only door a prompt can leave by. It works because `Sanitized` cannot be
    forged — `__post_init__` requires `for_model` to be a fixed point of the
    sanitizer, so `Sanitized(raw=t, for_model=t)` over provider prose raises.

    **Fragments, never a pre-joined string.** A port taking one assembled
    `prompt: str` would be defeated by the ordinary way prompts are written:
    interpolating `for_model` into a template yields a plain `str`, and the port
    would have to accept it. Keeping the two sources apart in the signature is
    what stops the type being flattened away one line before the call. The
    transcript path keeps the pair whole since story `11b`: every text field of
    an `Extraction` is a `Sanitized`, and no model is on that path.

    Internally-sourced text may be `str`: `instructions` is prose pm-ai wrote
    about itself, and there is no outside author to distrust.

    **That is also one bound on what this port can promise.** `complete(
    instructions=f"Summarise: {provider_text}", external=())` type-checks, and
    no signature can refuse it while internal prose stays a `str`. The
    chokepoint makes the honest call easy and the omission loud; it does not
    make the dishonest one impossible. Closing it would take a distinct type for
    internal prose, which is a different design from the one approved here.

    **The second bound was this signature's, and story `7a` narrowed it.**
    Until then `complete` returned `-> str`, which could not feed the
    disclosure ledger: AD-31 requires a frontier call to record its
    contributing scopes, task class, model, token counts and destination, and
    a bare string carries none of them. The port is widened once, here, for
    both adapters:

    - `flow` (required) names the scopes the fragments came from and the
      destination the answer is bound for — a scope or `EXTERNAL`. Every
      adapter checks it against `domain`'s closed table
      (`pm_ai.domain.model_calls.assert_flow_legal`) before anything is sent,
      local calls included (AD-31 obligation 3);
    - `shape` (optional) is a JSON schema the answer must follow — sent to the
      model; validating the answer against it stays the caller's;
    - `ModelAnswer` carries the text, the model's name, whether it ran
      locally, and the token counts and durations the server reported — what a
      ledger line needs *from the model*.

    What is still open. Nothing writes a ledger line yet: the `sent` and
    `completed` lines are `7c`'s, around the frontier adapter. And the scopes
    a line would record are the flow **the caller declares**: `Sanitized`
    holds two strings and no `DataScope`, so nothing checks that each fragment
    really came from a scope the flow names. The flow table refuses a declared
    flow that may not happen; it cannot catch a caller that declares the wrong
    one.

    `external` stays `Sequence[Sanitized]`: what a fragment is and where it
    came from are separate facts, and the flow carries the second.

    **`task_class` is required, and typed.** AD-15's invariant row makes "a call
    without a declared task class" a defect, and its Rule routes by "task class
    declared at the call site" — so the declaration belongs in the signature,
    where omitting it is the same kind of construction error as omitting
    sanitization. Keyword-only and un-defaulted: a default would answer for the
    caller, which is the one thing a call site declaring its own purpose must
    not have done for it. `TaskClass` rather than `str` so a misspelling is
    caught here rather than by whichever router first fails to match it. The
    vocabulary is open — see `pm_ai.domain.task_classes`.

    **Adapters.** The local adapter (`pm_ai.models.local.ollama`, story `7a`)
    implements this port for the local-only classes; the router and the
    frontier adapter are story `7c`'s, behind the same signature. The refusals
    a caller must catch about the port's transport are declared below, beside
    it (AD-49): `LocalModelUnavailable` (and `RemoteModelRefused`, one kind of
    it), `NoLocalModel`, `PossiblyTruncated`, `ModelTimedOut`. `ScopeFlowRefused`
    is about the flow table, a rule `domain` owns, and lives beside it.
    """

    def complete(
        self,
        *,
        task_class: TaskClass,
        instructions: str,
        external: Sequence[Sanitized],
        flow: ScopeFlow,
        shape: Mapping[str, object] | None = None,
    ) -> ModelAnswer:
        """Run `instructions` over `external` as `task_class`, and return the answer.

        Implementations put `fragment.for_model` in the prompt and never
        `fragment.raw` — the raw travels so a citation the answer carries can be
        resolved against the evidence (AD-29), not so it can be sent.

        `flow` is checked against the closed table before anything is sent
        (`ScopeFlowRefused`). An adapter serves the task classes its side
        declares (AD-15) and refuses any other by name, before any request.
        """


class LocalModelUnavailable(RuntimeError):
    """The local model server could not answer this call, and it was not retried.

    Nothing on 127.0.0.1:11434, the named model not pulled, an overloaded
    server (503), a redirect (never followed), an error or a malformed answer —
    each named in the sentence with its remedy (AD-1 class LM, story 7a).
    """


class RemoteModelRefused(LocalModelUnavailable):
    """The named model runs off this machine, so no prompt was sent to it (AD-1 class LM).

    A `:cloud` or `-cloud` tag, or the server's own `/api/show` answer naming
    `remote_host` or `remote_model`. A kind of `LocalModelUnavailable`, so a
    caller that catches the one catches this.
    """


class NoLocalModel(LookupError):
    """`config.toml` names no `local_model`, so no local adapter is wired (story 7a)."""


class PossiblyTruncated(RuntimeError):
    """The answer may be cut short, so it was discarded (story 7a).

    The prompt and output tokens together reached the context window, the
    server stopped for a reason other than `stop`, a token count is missing,
    or the server refused a prompt longer than the window. Names the counts or
    the field.
    """


class ModelTimedOut(TimeoutError):
    """The call outlasted its deadline; the connection was closed and nothing written."""


@runtime_checkable
class ConfigPort(Protocol):
    """The settings `config.toml` carries, named where `pm_ai.core` is unreachable.

    `pm_ai.core.config.Config` is the only implementation and always will be —
    this exists because `pm_ai.ports` may import nothing but `pm_ai.domain`
    (`.importlinter`'s `ports-depend-only-on-domain`), so `DaemonPort` below has
    no way to say `Config` and would otherwise say `Any`.

    Read-only members, deliberately: `Config` is frozen, and a protocol
    declaring settable attributes would claim a surface may write one.

    **One member per field of `Config`, and the parity is tested.** A field
    added to the dataclass and not declared here is not a type error anywhere —
    it is simply unreachable through the only settings interface a surface is
    typed against, so the setting exists, loads, renders, and cannot be read by
    the code that needs it. `display_timezone` spent story 4g in exactly that
    state. `tests/core/test_config.py::test_the_port_declares_every_field_config_carries`
    is what fails now instead of a future story's mypy run.
    """

    @property
    def blended_hourly_rate(self) -> float: ...

    @property
    def verbose_logging(self) -> bool: ...

    @property
    def display_timezone(self) -> str: ...

    @property
    def daemon_port(self) -> int: ...

    @property
    def local_model(self) -> str: ...


class DaemonRefused(RuntimeError):
    """`pm-ai daemon run` declined to start, and says why (story 4e).

    Raised by the composition root's daemon lifecycle, each time with a
    sentence naming its cause. Every cause it is raised for:

    - the `runtime` extra (`fastapi`, `uvicorn`) is not installed;
    - `config.toml` could not be read, or the loader refused it (a bad
      `daemon_port` included) — on a machine with no project enrolled, where
      the daemon is composed at run time;
    - the operational store could not be opened or prepared, or stayed busy
      past the busy wait, or the keychain could not be reached, while composing
      that daemon;
    - the port is taken — something accepts connections on it, or another
      socket is bound to it — or the daemon could not listen on it;
    - the token file is unusable: a symlink, not a regular file, readable by
      group or others, over 4 KiB, not ASCII, empty, whitespace or control
      characters, shorter than 32 characters; or it could not be read or
      written;
    - the request log under `~/.pm-ai/logs/` could not be opened;
    - the server bound the port but failed to start serving.

    A machine where no daemon could be composed for any other reason is refused
    by the CLI's `require_daemon()` instead. The CLI maps this onto exit 3 with
    the sentence unedited. Declared
    with the port (AD-49) because the layer that raises it, `pm_ai.app`, sits
    above the layer that catches it, `pm_ai.surfaces.cli`, and neither may
    name the other's types.
    """


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
