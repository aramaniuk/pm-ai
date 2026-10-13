"""Composition root (AD-30).

The only module that may import every layer. Core services receive their
dependencies from here; they never construct or locate them — which is exactly
why this module has to exist and why the pipeline below had no legal home
before it did.
"""

from __future__ import annotations

import json
import math
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pm_ai.connectors.gitlab import (
    DEFAULT_HOST as GITLAB_DEFAULT_HOST,
    HOST_KEY as GITLAB_HOST_KEY,
    PROJECT_KEY as GITLAB_PROJECT_KEY,
    MAX_REACH_BACK_MINUTES as GITLAB_MAX_REACH_BACK_MINUTES,
    REACH_BACK_KEY as GITLAB_REACH_BACK_KEY,
    GitLabConnectorAdapter,
    host_refusal,
    project_refusal,
)
from pm_ai.connectors.gitlab.client import (
    API_PATH,
    GitLabClient,
    GitLabRequest,
    GitLabResponse,
    PageUnavailable,
    token_refusal,
)
from pm_ai.connectors.graph import (
    CLIENT_ID_KEY,
    MAX_SETTING_MINUTES,
    REACH_BACK_KEY,
    TENANT_KEY,
    USER_ID_KEY,
    WIDTH_KEY,
    CategoryScopes,
    GraphConnector,
    MissingGraphSetting,
    UnknownProject,
    graph_category_scopes,
    graph_channel_listings,
    graph_window_policy,
)
from pm_ai.connectors.graph.auth import (
    GraphAuthError,
    GraphDeviceCodeAuth,
    GraphUnreachable,
    InMemoryRefreshTokenStore,
    SealedCredential,
    StoreBusy,
    StoreEntryMissing,
    StoreUnavailable,
    require_msal,
    tenant_refusal,
)
from pm_ai.connectors.graph.calendar import (
    HARVEST_CYCLE,
    GraphCalendarFetch,
    WindowPolicy,
)
from pm_ai.connectors.graph.channels import GraphChannelFetch
from pm_ai.connectors.graph.client import GraphClient
from pm_ai.connectors.probe import CREDENTIAL_PROBE_SECONDS
from pm_ai.connectors.registry import run_bounded
from pm_ai.connectors.transcripts.graph_transcripts import (
    GraphTranscriptAdapter,
    UnenrolledGraphTranscripts,
)
from pm_ai.connectors.transcripts.manual import ManualTranscriptAdapter
from pm_ai.core.config import Config
from pm_ai.core.connector_enrolment import (
    CONNECTORS,
    CREDENTIAL_STORE,
    ConnectorNotConfigured,
    ConnectorRowChanged,
    CredentialNotHeld,
    CredentialStoreUnclaimable,
    MalformedSettings,
    NotAGraphRow,
    UnplacedConnector,
    UserNotRecorded,
    assert_enrollable,
    assert_replaceable,
    connector_row,
    credential_for,
    record_user_id,
    redacted,
    replace_credential,
    stored_credentials,
)
from pm_ai.core.meeting_records import MeetingRecords
from pm_ai.core.project_registry import (
    DuplicateProject,
    ProjectEntry,
    ProjectPathUnusable,
    RegistryRefused,
    parse_registry,
    render_registry,
)
from pm_ai.core.project_scaffold import render_gitignore
from pm_ai.domain.claims import ClaimReentered
from pm_ai.domain.event_entries import DAEMON_ACTOR, EventEntry, SelfActionType
from pm_ai.domain.handles import (
    GRAPH,
    PersonHandle,
    own_handle_set,
    pm_identity,
    usable_user_id,
)
from pm_ai.domain.health import ArtifactState, Health, Presence, UnbuiltConnector
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.scope_model import PROJECT_DIRNAME, PROJECT_TREE
from pm_ai.domain.storage_tiers import GITIGNORE_FILENAME
from pm_ai.platform.claims import FlockClaims
from pm_ai.platform.environment import encryption_disabled as encryption_off
from pm_ai.platform.keychain import MacOSKeychainAdapter
from pm_ai.platform.paths import ScopePaths
from pm_ai.platform.vcs import GitVcs
from pm_ai.ports import (
    MASTER_KEY_NAME,
    ArtifactBusy,
    ConnectorPort,
    CryptoPort,
    DecryptionFailed,
    KeychainPort,
    ProbeFailed,
    ProbeUnreachable,
    TranscriptSourcePort,
    VcsPort,
)
from pm_ai.skills.gitlab import PostComment
from pm_ai.skills.registry import SkillRegistry
from pm_ai.storage.crypto import LazyKeyCrypto, PlaintextCrypto
from pm_ai.storage.service import StorageService

__all__ = [
    "Bootstrap",
    "CONFIG_ARTIFACT",
    "Daemon",
    "GitLabChecked",
    "GitLabEnrolment",
    "GraphEnrolment",
    "GraphSignInReplaced",
    "GraphSignedIn",
    "MASTER_KEY_NAME",
    "REGISTRY_ARTIFACT",
    "application_storage",
    "artifact_state",
    "bootstrap",
    "build",
    "onboard_project",
    "Onboarded",
    "warn_if_encryption_disabled",
    "PmIdentityReading",
    "UnusableRow",
    "read_pm_identity",
]

REGISTRY_ARTIFACT = "projects.toml"
CONFIG_ARTIFACT = "config.toml"


@dataclass
class Daemon:
    storage: StorageService
    crypto: CryptoPort
    skills: SkillRegistry
    # Typed against the port rather than one adapter, since story 33c. The
    # annotation said `dict[str, GitLabConnectorAdapter]` while the registry beside
    # it held `ConnectorPort`s, so the moment a second connector family existed
    # mypy — gated inside pytest since story 1k — refused the assignment. The port
    # is also the honest type: `run_harvest` reaches this dict and calls exactly
    # the four methods `ConnectorPort` declares. Since story 8k this dict is the
    # only connector list pm-ai keeps: `pm-ai connector check` probes it too.
    connectors: dict[str, ConnectorPort]
    transcripts: dict[str, object]
    # The Tier-1 accessor over `meetings/`, not a mapping. This was
    # `dict[str, object]` until story 11a, which meant every citation
    # `run_transcript_ingestion` minted resolved against process memory and died
    # with the process — while `Meeting` is declared Tier-1 in three scope trees
    # and is the citation root AD-33 makes durable. The accessor is the third of
    # the three `derivation-services.md` rule 3 names, and it writes to the scope
    # each meeting itself declares.
    meetings: MeetingRecords
    scope: DataScope
    """The project **this command is about** — not the set the daemon watches.

    One value used to answer both questions, and that is what made a second
    enrolled project a composition failure (story 4l). They are different
    questions with different answers: every enrolled project is watched (AD-10),
    while a command acts in exactly one of them — the project whose directory
    contains the working directory (AD-11). `watched` below carries the other
    half, so neither has to be re-derived from the other.
    """
    # Custody of the master key, held rather than reconstructed. `pm_ai.surfaces`
    # may not import `keyring` (`.importlinter`'s `os-behind-platform`), so a CLI
    # asked to enrol a key has no legal way to build an adapter — it has to be
    # handed one, and this is the layer permitted to build it. Declared *before*
    # `config` because that field carries a default: a non-default field after a
    # defaulted one raises `TypeError` at class creation.
    keychain: KeychainPort
    watched: tuple[str, ...]
    """Every project this daemon watches, `scope`'s project among them (AD-10).

    Held rather than re-derived from `connectors`, because a connector instance
    is named after what it covers and not every connector family is keyed by
    project — deriving the set back out of the keys would be reading a decision
    out of a string.

    **Required, and `__post_init__` enforces the invariant the sentence above
    states.** It carried a `()` default until the `4l` review, which let a
    direct construction produce a daemon acting in a project it did not
    watch — the exact pair of values this field exists to keep consistent — and
    a `tuple` annotation rather than `Sequence` so a bare `"alpha"` cannot be
    accepted as five single-character projects.
    """
    own_handles: frozenset[PersonHandle]
    """pm-ai's own bot accounts, as handles (AD-36 mechanism 3).

    `run_harvest` hands it to `attribute_all`, which marks an event `pm_ai`
    when its actor is in the set — an exact handle match, no alias lookup.
    Required, so a daemon cannot be built without saying whether it has any.
    Checked in `__post_init__`, **at construction only**: every member a
    `PersonHandle`, none `none:`, and a bare string refused. `Daemon` is not
    frozen — `connectors` and `config` are reassigned after construction by
    the CLI and its tests — so an assignment to this field afterwards is not
    re-checked. Change it through `dataclasses.replace`, which constructs anew.
    """
    # The clock the whole daemon was built with, held rather than re-read. A
    # pipeline that needs an instant — `23b`'s render takes one — would otherwise
    # compose a second `datetime.now()` in whatever layer happened to call it,
    # and a process with two clocks is the thing AD-5's single writer exists to
    # avoid: the storage service stamps from this one, so a dashboard whose
    # `now` came from anywhere else could date a file against a clock nothing
    # else in the process reads.
    clock: Callable[[], datetime] = field(
        default_factory=lambda: lambda: datetime.now(timezone.utc)
    )
    # Every setting `config.toml` carries, held once. Defaults when the caller
    # supplied none, which is a first run rather than an error.
    config: Config = field(default_factory=Config)
    transcript_sources: dict[str, TranscriptSourcePort] = field(default_factory=dict)
    """Each calendar connector's transcript source, keyed by connector instance.

    What `run_harvest` acquires through after that instance's harvest (story
    33e). Separate from `transcripts`, whose keys are source families
    (`"graph"`, `"manual"`), so no instance name can shadow one. A Graph
    connector with no entry here gets a named refusal, never another
    account's source.
    """
    unbuilt: tuple[UnbuiltConnector, ...] = ()
    """Every enabled connector row this build did not construct, with why (4o).

    The sentence each one printed on stderr at composition — no project enrolled
    to file a GitLab harvest into, a Graph row a settings key short, a row with
    no usable instance or system, a system with no adapter, an adapter that
    would not construct — kept so `pm-ai connector check` can list the row
    rather than omit it (AD-39). A **disabled** row is not here: it is skipped
    without a word, because the operator turned it off. A row here is not a
    connector: nothing harvests it and nothing probes it.
    """

    def __post_init__(self) -> None:
        """Refuse a daemon whose acting project is not one it watches.

        Two fields that must agree, checked where they are set rather than
        where they are read: a `scope` outside `watched` writes into a project
        whose telemetry nothing collects, and that is invisible until somebody
        goes looking for a harvest that never ran. A bare string is refused by
        name because `"alpha"` is a perfectly good `Sequence[str]` of five
        one-character ids, which would pass every later check.
        """
        self.own_handles = own_handle_set(self.own_handles)
        if isinstance(self.watched, str):
            raise TypeError(
                f"Daemon.watched is every project this daemon watches, not one "
                f"project's name: {self.watched!r} is a string, and a string of "
                f"ids is a sequence of its characters. Pass a tuple, e.g. "
                f"({self.watched!r},)."
            )
        if self.scope.kind is ScopeKind.PROJECT and self.scope.project_id not in self.watched:
            raise ValueError(
                f"this daemon acts in {self.scope.project_id!r} and watches "
                f"{sorted(self.watched)}, which does not include it. A command "
                f"acting in a project nothing watches writes where no harvest "
                f"ever runs."
            )


def build(
    root: Path | None,
    acting: DataScope | str,
    *,
    watched: Sequence[str] | None = None,
    paths: ScopePaths | None = None,
    now: Callable[[], datetime] | None = None,
    vcs: VcsPort | None = None,
    keychain: KeychainPort | None = None,
    encryption_disabled: bool | None = None,
    config: Config | None = None,
) -> Daemon:
    """Wire the daemon against one resolver, which owns all four scopes (AD-4).

    This is the one module that may import both `pm_ai.storage` and
    `pm_ai.platform` — they are independent siblings everywhere else — so the
    path resolver is built here and handed to the single writer.

    ## `acting` and `watched` answer two different questions

    `acting` is the scope **this command acts in**: it becomes `Daemon.scope`,
    which is where a command writes and what AD-38's citation guard reads. It
    is the command's own target (story 4m, AD-11's selection order): a named or
    selected project, or the personal or application scope for a command aimed
    at one of those — which is what lets `pm-ai dashboard` and `goal set` build
    a daemon on a machine where no folder names a project. A bare `str` is
    shorthand for that project's scope, kept because every test that wires a
    single-project daemon spells it that way. `watched` is **every project
    pm-ai watches** — AD-10 gives each registered project
    independently-scheduled harvesting, so a connector instance is constructed
    for each, with its own scope and therefore its own cursor, whatever the
    command targets.

    Until story 4l one argument answered both, and the consequence was not a
    narrow one: `entry._compose` refused to assemble at all when a second
    project was enrolled, and reported that refusal as a `FAILING` probe — a
    machine in the arrangement the architecture describes, told it was broken.

    `watched` defaults to the acting project alone (nothing, for a
    non-project scope), which is exactly the daemon this function built before
    the argument existed. It is a `Sequence` rather than a
    set because a caller has a registry in hand, not a set; duplicates and order
    are normalised here so two callers cannot produce two different daemons from
    the same registry.

    Exactly one of `root` and `paths`:

    - `root` builds `ScopePaths.rooted(root)`, which puts all four scopes beneath
      one directory *and* invents a repository path for any project id it is
      given. That second property is what makes it the test factory, so it must
      not be the only way in.
    - `paths` takes a resolver the caller built — `ScopePaths.production(...)`
      from the registry `pm-ai project add` writes, for a real daemon.

    `now` stays optional and defaults to a system-clock read. It is the default
    for the whole daemon: `StorageService` requires a clock and reads none of its
    own, so every timestamp it writes comes from here.

    `config` is `config.toml`, already read and interpreted — passed in rather
    than loaded here for the same reason `vcs` is: `pm_ai.core.config` parses
    bytes and opens nothing, and the read belongs to the single reader. Taking
    it as an argument is also what will let `4c` decide what an unparseable
    config does to a `pm-ai doctor` run, rather than having composition raise
    before any probe executes; there is no such subcommand and no such probe
    today. `None` means no file was found, which is a first run and not an
    error.

    `vcs` is the same arrangement for the other question the writer cannot answer
    itself: whether git would commit what is about to be written. It defaults to
    the real `git` adapter and is overridable so a test can supply a verdict,
    because this is also the one module that may import both `pm_ai.storage` and
    `pm_ai.platform`. A writer built without it refuses every declared-excluded
    write inside a working tree, which is the safe direction but not a useful
    one — and since story 1n that includes the project event log, not just
    captures.
    """
    # Written as three branches rather than an XOR check followed by a ternary,
    # so the exclusivity is what narrows the types instead of something a reader
    # (or a checker) has to infer two statements later. Not an `assert`
    # deliberately: those vanish under `python -O`, and nothing that decides
    # which resolver the daemon gets should depend on an interpreter flag.
    if root is not None and paths is None:
        resolver = ScopePaths.rooted(root)
    elif paths is not None and root is None:
        resolver = paths
    else:
        raise ValueError(
            "build() needs exactly one of `root` (a rooted layout beneath one "
            "directory, for tests) and `paths` (a resolver you built, which is "
            "how ScopePaths.production() reaches the daemon)."
        )
    clock = now or (lambda: datetime.now(timezone.utc))
    scope = (
        acting
        if isinstance(acting, DataScope)
        else DataScope(ScopeKind.PROJECT, acting)
    )
    spans = _watched(scope, watched)
    # Eagerly, because nothing else resolves these until the first Tier-1 write:
    # an id that cannot be a directory name, or a project no registry knows,
    # would otherwise surface mid-harvest with a batch already in hand. Every
    # watched project and not only the acting one, since 4l — a connector is
    # constructed for each below, and a resolver that will not answer for one of
    # them is a fact to meet here rather than on its first harvest.
    for watched_scope in spans.values():
        resolver.scope_root(watched_scope)
    # The cipher is chosen before storage, because storage performs every
    # encrypted read and write and therefore holds it. The *record* of a
    # disabled cipher needs storage, so `_writer` binds it after — splitting the
    # two is what keeps this acyclic.
    # `None` means consult the environment, which is the only way a user may
    # disable encryption — no config key, no stored profile, nothing that
    # survives a restart. Reading ambient state is the composition root's job and
    # nobody else's; an explicit `True`/`False` overrides it, which is how tests
    # state their intent instead of mutating the environment.
    disabled = encryption_disabled if encryption_disabled is not None else encryption_off()
    # Hoisted out of the `_choose_crypto` argument it used to be, because the
    # daemon now carries it: the cipher is not the only consumer, and building a
    # second adapter for the CLI would put key custody in two places.
    custody = keychain or MacOSKeychainAdapter()
    crypto = _choose_crypto(custody, encryption_disabled=disabled)
    storage = _writer(resolver, now=clock, vcs=vcs or GitVcs(), crypto=crypto)
    if disabled:
        _warn_disabled_encryption()
    skills = SkillRegistry(storage, scope=scope)
    skills.register(PostComment())  # credentials would be injected here, from storage
    # One built-in per **watched** project, each bound to its own scope. AD-10
    # gives every registered project independently-scheduled harvesting, and the
    # instance name is what `save_cursor` keys on — so a per-project instance is
    # also what keeps each project's place in the queue its own, rather than one
    # cursor advancing for whichever project happened to be asked.
    #
    # Keyed by `adapter.instance` rather than by an f-string spelled here: the
    # adapter already owns the `gitlab:<project>` form, and a second copy of it
    # is what makes a registry key, a coverage window and a probe row drift.
    connectors: dict[str, ConnectorPort] = {}
    builtin_scopes: dict[str, DataScope] = {}
    for watched_id, watched_scope in spans.items():
        # Named `gitlab:<project id>` by composition, not by the adapter, and
        # holding no credential: it is not harvested and records nothing until
        # an enrolment replaces it (story 8n).
        builtin = GitLabConnectorAdapter(
            instance=_builtin_instance(watched_id),
            project=watched_id,
            scope=watched_scope,
            now=clock,
            wait=time.sleep,
        )
        connectors[builtin.instance] = builtin
        builtin_scopes[builtin.instance] = watched_scope
    # This dict is the one inventory (story 8k, AD-30): `run_harvest` reads it,
    # and `pm-ai connector check` probes it through the daemon `entry` is handed.
    # Until 8k it was also copied into a process-wide registry that `connector
    # check` read instead, and the copy diverged once — `connector check` listed
    # an instance `run_harvest` raised `KeyError` for, because enrolled
    # connectors had been registered into the copy alone. One structure cannot
    # disagree with itself, and a second daemon built in one process no longer
    # replaces the first one's list. Keyed by instance, so a cursor, a coverage
    # window and a probe row all name one connector.
    # The enrolled adapter *replaces* the built-in of the same name, and the
    # collision is the ordinary case rather than a corner: the built-in above is
    # keyed `gitlab:<project>`, and `gitlab.py`'s own ABSENT remediation tells the
    # operator to run `pm-ai connector add gitlab gitlab:<project>` — the exact
    # instance name that produces one. `setdefault` kept the credential-less
    # built-in and dropped the adapter holding the sealed credential, so the
    # connector the operator had just enrolled went on reporting ABSENT and the
    # remedy printed was the one they had already followed.
    enrolled_rows, unbuilt = _enrolled_connectors(
        storage,
        scopes=builtin_scopes,
        clock=clock,
        registered=_registrar(resolver),
    )
    for instance, enrolled in enrolled_rows:
        connectors[instance] = enrolled
    sources = _transcript_sources(connectors)
    return Daemon(
        storage=storage,
        crypto=crypto,
        skills=skills,
        connectors=connectors,
        # AD-23 — both adapters wired from day one, so the pipeline is exercisable
        # without a live tenant.
        transcripts=_transcript_families(sources),
        transcript_sources=sources,
        meetings=MeetingRecords(storage),
        scope=scope,
        keychain=custody,
        watched=tuple(spans),
        # Empty, and that is accurate: no enrolment records a bot account
        # today. Graph signs in as the user (`8m`) and GitLab uses the user's
        # personal access token (`8o`), so every event they harvest is the
        # user's own account, never a distinct pm-ai bot. Where bot handles are
        # stored is decided by the first enrolment that records one.
        own_handles=frozenset(),
        # The same callable the single writer stamps from, so nothing downstream
        # has to build a second one.
        clock=clock,
        config=config if config is not None else Config(),
        unbuilt=unbuilt,
    )


def _transcript_sources(
    connectors: Mapping[str, ConnectorPort],
) -> dict[str, TranscriptSourcePort]:
    """Each Graph connector's transcript source, keyed by its instance (story 33e).

    Built from that connector's own client — the same credential, origin check,
    page cap, throttle handling and budget — so a harvest of one Microsoft
    account asks that account, and never another, about the meetings it
    recorded. A mapping of its own rather than keys beside `"graph"` and
    `"manual"` in `Daemon.transcripts`, so an instance named either cannot
    collide with a family key.
    """
    return {
        instance: GraphTranscriptAdapter(client=connector.calendar.client, instance=instance)
        for instance, connector in connectors.items()
        if isinstance(connector, GraphConnector)
    }


def _transcript_families(sources: Mapping[str, TranscriptSourcePort]) -> dict[str, object]:
    """`Daemon.transcripts`: one source per family (AD-23).

    `"graph"` is the first enrolled Graph connector's adapter or, with none
    enrolled, the stand-in that refuses by name. `"manual"` is the watched-folder
    fallback, which joins the port with `11c`.
    """
    graph: TranscriptSourcePort = next(iter(sources.values()), UnenrolledGraphTranscripts())
    return {"graph": graph, "manual": ManualTranscriptAdapter()}


def _watched(
    acting: DataScope, watched: Sequence[str] | None
) -> dict[str, DataScope]:
    """Every project this daemon watches, as ids mapped to their scopes.

    Sorted and de-duplicated, so the same registry always produces the same
    daemon: the connector dict's iteration order reaches `pm-ai connector check`
    and the dashboard's merge order, and a set would have made both depend on
    hash ordering.

    **The acting project is always among them**, whatever the caller passed. A
    daemon that acted in a project it did not watch would write into a scope
    whose telemetry nothing collects, and the caller with the registry in hand
    is not always the caller that chose the working directory. An acting scope
    that is not a project adds nothing: personal and application scope are not
    harvested per project (story 4m).

    `None` means "just the acting project", which is the daemon `build()`
    produced before the argument existed. A bare **string** is refused rather
    than accepted:
    `watched="alpha"` is a valid `Sequence[str]` whose elements are five
    one-character project ids, and every check downstream would pass.
    """
    if isinstance(watched, str):
        raise TypeError(
            f"build(watched=...) is every project to watch, not one project's "
            f"name: {watched!r} is a string, and a string of ids is a sequence "
            f"of its characters. Pass a tuple, e.g. ({watched!r},)."
        )
    own = (acting.project_id,) if acting.kind is ScopeKind.PROJECT else ()
    ids = (*own, *(watched or ()))
    return {
        project_id: DataScope(ScopeKind.PROJECT, project_id)
        for project_id in sorted(set(ids))
    }


def _choose_crypto(keychain: KeychainPort, *, encryption_disabled: bool) -> CryptoPort:
    """The cipher for the encrypted set, or the pass-through the debug flag asks for.

    The keychain is reached *here* and nowhere else. `pm_ai.storage` may not
    import `pm_ai.platform`, so the single writer cannot fetch a key itself —
    which is the property that keeps one out of every module that merely writes
    files.

    `KeyNotFound` is deliberately not caught, and not raised here either: the
    cipher returned is lazy, so a machine with no key enrolled still boots and the
    refusal lands when an encrypted artifact is actually touched. A first run must
    mint a key, and that is a decision with consequences — a new key makes every
    previously sealed artifact unreadable — so it belongs to whatever owns
    installation, not to a constructor that would quietly do it.
    """
    if encryption_disabled:
        return _RecordedPlaintext()
    return LazyKeyCrypto(keychain, MASTER_KEY_NAME)


class _RecordedPlaintext(PlaintextCrypto):
    """The pass-through, recording itself once before its first protected write.

    Storage calls `encrypt` only for a path in the encrypted set and only just
    before publishing it, so the first `encrypt` is exactly "a protected file is
    about to reach the disk in plaintext" — the moment the event-log line
    explains. Recording at start-up instead put a line in a never-trimmed log for
    every `--help` and mistyped command, burying the one that mattered (1p).

    The record runs *before* the bytes are returned, so a failed append
    propagates and the file is never written: a plaintext credential with no
    line explaining it is the state this exists to make impossible. `decrypt`
    records nothing — reading writes nothing unprotected.

    The "already recorded" flag lives here, on the one cipher each writer owns,
    rather than in a module variable (AD-30). A `PlaintextCrypto` still, so every
    check for the debug cipher keeps recognising it.

    The recorder arrives after construction because the writer holds the cipher
    and the recorder needs the writer. An `encrypt` before `bind` refuses rather
    than writing unrecorded.

    The check, the record and the flag are held under one lock, because
    connector work runs on threads and two first protected writes through one
    writer would otherwise both see "not yet recorded" and append twice.

    Setting attributes here works only because `PlaintextCrypto` is a frozen
    dataclass *with no fields*: the frozen `__setattr__` refuses field names and
    exact-class instances, and lets a subclass set anything else. Giving
    `PlaintextCrypto` a field named like one of these slots, or replacing its
    frozen check, would break this class.
    """

    __slots__ = ("_lock", "_record", "_recorded")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._record: Callable[[], None] | None = None
        self._recorded = False

    def bind(self, record: Callable[[], None]) -> None:
        self._record = record

    def encrypt(self, plaintext: bytes) -> bytes:
        with self._lock:
            if not self._recorded:
                if self._record is None:
                    raise RuntimeError(
                        "a protected file was about to be written in plaintext "
                        "before anything could record why; nothing was written."
                    )
                self._record()
                self._recorded = True
        return plaintext


def _writer(
    resolver: ScopePaths,
    *,
    now: Callable[[], datetime],
    vcs: VcsPort,
    crypto: CryptoPort,
) -> StorageService:
    """A `StorageService`, with a disabled cipher bound to record through it.

    Every writer pm-ai builds goes through here — the daemon's, `pm-ai setup`'s
    and project enrolment's — so none of them can write a protected file in
    plaintext without the event-log line first.
    """
    storage = StorageService(
        resolver, now=now, vcs=vcs, crypto=crypto, claims=FlockClaims()
    )
    if isinstance(crypto, _RecordedPlaintext):
        crypto.bind(lambda: _record_disabled_encryption(storage))
    return storage


def warn_if_encryption_disabled() -> None:
    """`build()`'s console warning, for a writer composed without a daemon (story 8m)."""
    if encryption_off():
        _warn_disabled_encryption()


def _warn_disabled_encryption() -> None:
    """The console half: whoever is running pm-ai now, on every command.

    Printed at start rather than on the first protected write, because it costs
    nothing permanent and the person at the keyboard should know straight away.
    The durable half is `_record_disabled_encryption`, written only when a
    protected file is actually about to be written in plaintext.
    """
    print(
        "WARNING: encryption is disabled by an explicit debug flag. "
        "Credentials and voice notes are being written in plaintext. This is "
        "never the default in a fresh installation.",
        file=sys.stderr,
    )


def _record_disabled_encryption(storage: StorageService) -> None:
    """The event-log half: whoever reads the record later.

    Someone who finds a credential file readable in plain text needs to find out
    why, and a console warning is gone the moment the terminal scrolls. Only the
    composition root knows the flag exists, so only it can say. Called by
    `_RecordedPlaintext` once per writer, just before the first protected write.

    Into the *application* scope's event log, always. The flag describes the
    daemon's own posture on this machine — application-scope subject matter —
    and until 2026-08-28 this wrote into the daemon's project scope, where the
    fact that the operator ran with encryption off became part of the team's
    record — the exact misfiling-by-convenience the scope model exists to refuse
    (and the reason AD-38 homes the disclosure ledger the same way). That scope's
    `event_log/` was also committed at the time; story 1n made it machine-local,
    which narrows the old consequence without touching the reason this writes
    where it does — the subject matter is the daemon's, not the team's.
    """
    storage.append_event_log(
        EventEntry(
            category=SelfActionType.SECURITY,
            actor=DAEMON_ACTOR,
            fields=(
                ("protection", "encryption-at-rest"),
                ("disabled_by", "environment variable"),
            ),
        ),
        scope=DataScope(ScopeKind.APPLICATION),
    )


def _registrar(resolver: ScopePaths) -> Callable[[str], bool]:
    """Whether a project id is one this machine's registry knows (AD-11).

    A predicate rather than a set, because "registered" is the resolver's
    question and the two resolvers answer it differently on purpose: a
    production one knows only what `pm-ai project add` wrote, while a rooted one
    invents a repository beneath its root for any id — which is what makes it the
    test factory. Reading `project_roots` directly would have called every id in
    a test unregistered and refused every category mapping in the suite.

    Handed to `pm_ai.connectors`, which may not import this resolver: the layer
    stack makes them independent siblings, so the composition root asks and
    passes the answer.
    """

    def registered(project_id: str) -> bool:
        try:
            resolver.repository(project_id)
        except Exception:
            # `ScopeResolutionError` is the refusal family `ScopePathPort`
            # promises — an unregistered project, and an id that cannot be a
            # directory name — and it is not the only thing a resolver can
            # raise: `repository` touches the filesystem, so a permission error
            # or an `OSError` from a broken registry reaches here too.
            #
            # Every one of them is "no" to the question this predicate asks, and
            # none of them is a reason to stop composing. Narrower, an
            # unexpected fault propagated through `CategoryScopes.__post_init__`
            # and out of `build()`, taking down `pm-ai doctor` — the command
            # that diagnoses a machine whose project registry is broken.
            return False
        return True

    return registered


def _enrolled_connectors(
    storage: StorageService,
    *,
    scopes: Mapping[str, DataScope],
    clock: Callable[[], datetime],
    registered: Callable[[str], bool],
) -> tuple[tuple[tuple[str, ConnectorPort], ...], tuple[UnbuiltConnector, ...]]:
    """What `pm-ai connector add` wrote, as adapters, so enrolment survives a restart.

    Story 8b's success message tells the operator the connector becomes active
    at the next start. Nothing read `connectors/` until this function existed,
    so that sentence was false: an enrolment wrote two files and no later run
    looked at either. Registration stays construction-time per AD-9 and story
    8d — this is the start that "the next start" refers to.

    Returned rather than registered, so the caller puts these in
    `Daemon.connectors` — the one inventory a harvest and `pm-ai connector
    check` both read (story 8k). Registering them into a separate list once made
    `connector check` list an instance `run_harvest` could not resolve.

    Failures are swallowed deliberately, and only here: an unreadable or
    malformed entry must not stop a daemon composing, because `doctor` is the
    command that diagnoses exactly that and it cannot run if `build()` raises.
    An enabled row that fails to build is not raised but kept, with its reason,
    in the second half of the pair (story 4o). A file that cannot be read or
    decoded at all is still skipped: it has no `enabled` to read, so nothing
    says it was meant to run.

    Credentials **are** read, since story 33a. They were not until then, and the
    consequence was live rather than theoretical: `8b`'s success message tells
    the operator the connector becomes active at the next start, and the next
    start built every adapter with `credential=None`, so `pm-ai connector check`
    reported a connector enrolled ten seconds ago as `ABSENT` — a fresh install
    on a machine somebody had just finished setting up. `stored_credentials` was
    built by `8b` and, outside enrolment's own duplicate check, called by
    nothing. This is the only layer that can call it: `app` may import both
    `core` and `storage`, and nothing lower may import both.

    A sealed-store read is not a resource fetch, which is what keeps this inside
    `33a`'s boundaries: no provider is contacted here, and the adapter is handed
    a string.

    **`scopes` is which scope an enrolled row writes into.** An enrolment keyed
    `gitlab:<project>` is the ordinary case rather than a corner — `gitlab.py`'s
    own remediation tells the operator to type exactly that name — so it
    replaces the built-in for that project and must inherit the project's scope,
    not the acting command's. Without it, a credentialled connector enrolled for
    `beta` would file `beta`'s commits into whichever project the operator
    happened to be standing in. `_enrolment_scope` decides, and a row it cannot
    place on a multi-project machine is **skipped and said out loud** rather
    than defaulted.

    **The second half of the pair is every enabled row skipped out loud** (story
    4o): the instance and the sentence printed for it, so `pm-ai connector
    check` lists the row as enrolled and not built instead of omitting it
    (AD-39). A disabled row is skipped silently and is in neither half.
    """
    # Fetched on first need, not up front. Opening the sealed store costs a
    # master-key fetch from the keychain, and a machine with no connectors
    # enrolled — every machine before the first `connector add` — paid it on
    # every `build()` to read a mapping nothing then consulted.
    credentials: dict[str, dict[str, str]] | None = None
    built: list[tuple[str, ConnectorPort]] = []
    unbuilt: list[UnbuiltConnector] = []
    for name, entry in _named_configurations(storage):
        # Anything but an explicit `true` is off. The file is plaintext and
        # hand-editable on purpose, so `"false"`, `0` and `null` are all things
        # an operator will actually write meaning "not this one". A disabled
        # row is the one enrolled row skipped without a word: the operator
        # turned it off, and listing it as "not built" would report their own
        # choice back to them as a fault.
        if entry.get("enabled") is not True:
            continue
        instance = entry.get("instance")
        system = entry.get("system")
        if not isinstance(instance, str) or not instance:
            # Named by its file, since the row cannot name itself (story 4o:
            # an enabled row is listed, never dropped).
            unbuilt.append(
                _unplaced(
                    f"connectors/{name}",
                    "its 'instance' is missing or not text, so pm-ai cannot tell "
                    "which connector it is. Repair the row or remove it.",
                )
            )
            continue
        if not isinstance(system, str) or not system:
            unbuilt.append(
                _unplaced(
                    instance,
                    "its 'system' is missing or not text, so pm-ai cannot tell "
                    "which adapter to build. Repair the row or remove it.",
                )
            )
            continue
        if system not in ("gitlab", "graph"):
            # An enrolled system pm-ai has no adapter for is not guessed at.
            # Two families exist: `gitlab` below, and `graph` since story 33c,
            # which is the connector `33a`'s token and `33b`'s calendar fetch
            # were built for.
            unbuilt.append(
                _unplaced(
                    instance,
                    f"pm-ai has no adapter for the system {system!r}; the "
                    f"systems it builds are 'gitlab' and 'graph'.",
                )
            )
            continue
        if system == "graph":
            # No credential is read here: the store reads the sealed file each
            # time the adapter asks, so a token another run rotated is the one
            # this run refreshes from (story 8j).
            graph = _graph_connector(
                entry,
                instance=instance,
                store=SealedRefreshTokenStore(
                    storage=storage, instance=instance, system=system
                ),
                clock=clock,
                registered=registered,
            )
            if isinstance(graph, UnbuiltConnector):
                unbuilt.append(graph)
            else:
                built.append((instance, graph))
            continue
        # Placed before the credential is read, so a row that will not be built
        # — on a machine with no project enrolled, every GitLab row — costs no
        # master-key fetch. The project path itself is the row's own `project`
        # setting, checked by `_gitlab_connector` (story 8n).
        filed = _enrolment_scope(instance, scopes=scopes)
        if isinstance(filed, UnbuiltConnector):
            unbuilt.append(filed)
            continue
        # The credential the sealed store holds for *this* instance, or `None`
        # when there is none. `None`, absent and blank are one state to the
        # adapter's health probe — no usable credential — so a half-finished
        # enrolment still reports ABSENT rather than claiming to be configured.
        if credentials is None:
            credentials = _stored_credentials(storage)
        held = _credential_for(credentials.get(instance), system=system)
        gitlab = _gitlab_connector(
            entry, instance=instance, scope=filed, credential=held, clock=clock
        )
        if isinstance(gitlab, UnbuiltConnector):
            unbuilt.append(gitlab)
        else:
            built.append((instance, gitlab))
    return tuple(built), tuple(unbuilt)


def _gitlab_connector(
    entry: Mapping[str, object],
    *,
    instance: str,
    scope: DataScope,
    credential: str | None,
    clock: Callable[[], datetime],
) -> GitLabConnectorAdapter | UnbuiltConnector:
    """One GitLab enrolment row as a connector, or why it was not built (story 8n).

    Three settings come off the row. `project` is the GitLab path and is
    required — read from the row and never derived from the instance name,
    which cannot hold the `/` a grouped project has. `host` defaults to
    `https://gitlab.com`, the one defaulted key, and may carry a path for a
    self-managed instance. `first_run_reach_back_minutes` is optional and
    checked as Graph's minute settings are.

    A value that cannot be used builds nothing: the key is named on stderr
    and returned for `connector check` to list (4o), as `_graph_connector` does: a connector that silently fails to appear is
    indistinguishable from one that was never enrolled. `time.sleep` is
    injected as the throttle wait, because the client's default does not wait.
    """
    project = entry.get(GITLAB_PROJECT_KEY)
    unusable = project_refusal(project)
    if unusable is not None:
        return _unbuilt(
            instance,
            f"its {GITLAB_PROJECT_KEY!r} cannot be used: {unusable}.",
            family="GitLab",
        )
    host = entry.get(GITLAB_HOST_KEY, GITLAB_DEFAULT_HOST)
    unusable = host_refusal(host)
    if unusable is not None:
        return _unbuilt(
            instance,
            f"its {GITLAB_HOST_KEY!r} cannot be used: {unusable}.",
            family="GitLab",
        )
    reach_back = entry.get(GITLAB_REACH_BACK_KEY)
    extra: dict[str, Any] = {}
    if reach_back is not None:
        if (
            isinstance(reach_back, bool)
            or not isinstance(reach_back, int)
            or not 0 < reach_back <= GITLAB_MAX_REACH_BACK_MINUTES
        ):
            return _unbuilt(
                instance,
                f"its {GITLAB_REACH_BACK_KEY!r} is {reach_back!r}, which is not a "
                f"whole number of minutes from 1 to {GITLAB_MAX_REACH_BACK_MINUTES}.",
                family="GitLab",
            )
        extra["first_run_reach_back"] = timedelta(minutes=reach_back)
    if not isinstance(project, str) or not isinstance(host, str):
        # Unreachable: both refusals above answer for anything but a string.
        # Said rather than asserted, so `python -O` cannot remove it.
        return _unbuilt(
            instance,
            "its settings were not read as strings, which is a fault in pm-ai — report it.",
            family="GitLab",
        )
    try:
        return GitLabConnectorAdapter(
            instance=instance,
            project=project,
            scope=scope,
            host=host,
            now=clock,
            credential=credential,
            wait=time.sleep,
            **extra,
        )
    except Exception as unexpected:  # noqa: BLE001 — `build()` composes regardless
        return _unbuilt(
            instance,
            f"building it raised {type(unexpected).__name__}, which pm-ai could "
            f"not classify — report it.",
            family="GitLab",
        )


def _enrolment_scope(
    instance: str, *, scopes: Mapping[str, DataScope]
) -> DataScope | UnbuiltConnector:
    """Which project's tree one enrolled connector files into, or why it is skipped.

    Four answers; the third is the one the `4l` review asked for, the fourth
    story 4o's:

    - **The row replaces a built-in.** `scopes` is keyed by built-in instance
      name (`gitlab:<enrolled-id>`), and the enrolment that collides with one is
      the documented ordinary case, so it inherits that project's scope.
    - **Exactly one project is watched.** There is nothing to be ambiguous
      *between*, so a row named anything at all (`gitlab:acme/web`, a provider
      path rather than a pm-ai id) files into that project, as it always did.
      Single-project machines are every machine that exists today, and
      skipping their connectors would be this slice breaking them to close a
      leak they cannot have. **The only watched project, and never the acting
      scope** (story 4m): a command aimed at personal or application scope
      builds a daemon too, and a project's harvest filed into the PM's
      personal tree is the leak AD-38 refuses.
    - **Neither.** Several projects are watched and the row's name matches no
      enrolled id — a renamed project, a removed one, or a provider-shaped
      instance. Defaulting to the acting command's scope is precisely the leak
      `scopes` exists to close, only reached by a different route: the row would
      file into whichever project the operator happened to be standing in, and
      differently on the next invocation. Refused, and said out loud, because a
      connector that silently vanishes is indistinguishable from one that was
      never enrolled.
    - **No project is watched at all** (story 4o). A command aimed at the
      person or at pm-ai itself runs on a machine with nothing enrolled, and
      its daemon watches nothing — so there is no project's tree to file into,
      and none is invented. Said out loud on every start until a project is
      enrolled. With exactly one enrolled the row becomes the second answer
      above; with more, it must be named for its project (the first answer).

    The row's own `project` key is deliberately **not** consulted: for GitLab it
    holds the *provider's* path (`group/project`), not a pm-ai project id, and
    reading a pm-ai id out of it is the same mistake in a new place.
    """
    placed = _placement(instance, scopes=scopes)
    return _unplaced(instance, placed) if isinstance(placed, str) else placed


def _placement(instance: str, *, scopes: Mapping[str, DataScope]) -> DataScope | str:
    """`_enrolment_scope`'s rule, saying nothing: the scope, or why there is none.

    Pure, so `pm-ai connector add gitlab` can ask it before anything is asked
    of the operator (story 8o) without printing the start-up warning.
    """
    placed = scopes.get(instance)
    if placed is not None:
        return placed
    if len(scopes) == 1:
        (only,) = scopes.values()
        return only
    if not scopes:
        return (
            "no project is enrolled, so there is no project's tree for its "
            "harvest to file into. It is saved, and it is built once a single "
            "project is enrolled with `pm-ai project add <path>`; with more "
            "than one enrolled, its instance name must name its project."
        )
    return (
        f"it is not one of the enrolled projects' connectors "
        f"({', '.join(sorted(scopes))}), and this daemon watches "
        f"{len(scopes)} projects — so nothing says which project's tree its "
        f"harvest belongs in. Enrol it afresh under the instance name of the "
        f"project it covers, or remove the row."
    )


def _builtin_instance(project_id: str) -> str:
    """The built-in GitLab connector's name for one watched project: `gitlab:<id>`.

    One spelling for `build()`, which keys the built-in by it, and for the
    placement check `pm-ai connector add gitlab` makes up front (story 8o).
    """
    return f"gitlab:{project_id}"


def _unplaced(instance: str, reason: str) -> UnbuiltConnector:
    """Say why an enrolled connector was not built, and keep the sentence.

    stderr, because the daemon has not composed yet and there is no event log to
    write into; and returned, so the daemon carries it for `pm-ai connector
    check` to list (story 4o).
    """
    print(
        f"WARNING: the enrolled connector {instance!r} was not built — {reason}",
        file=sys.stderr,
    )
    return UnbuiltConnector(instance, reason)


class _RowRefused(Exception):
    """A Graph row that would not build a connector, with the sentence naming the key."""


@dataclass(frozen=True)
class _JudgedGraphRow:
    """What `_judged_graph_row` read off a Graph row that would build."""

    client_id: str
    tenant: str | None
    """The row's own tenant, trimmed, or `None` for the adapter's default."""
    policy: WindowPolicy
    width: int
    reach_back: int
    categories: CategoryScopes | None
    """`None` when the caller did not ask for them (no `registered`)."""


def _judged_graph_row(
    entry: Mapping[str, object],
    *,
    registered: Callable[[str], bool] | None = None,
) -> _JudgedGraphRow:
    """The checks a start applies to one Graph row, or `_RowRefused` naming the key.

    One place for them, applied at every start by `_graph_connector` and before
    the sign-in by `pm-ai connector sign-in` (story 8m): the app id present,
    both harvest widths present and valid, the category mapping when
    `registered` is given, and a named tenant well-formed. A sign-in judges no
    categories — it writes no mapping, keeps the row's as it is, and runs on a
    machine with no project enrolled, where every mapping would name an unknown
    project.
    """
    client_id = entry.get(CLIENT_ID_KEY)
    if not isinstance(client_id, str) or not client_id.strip():
        raise _RowRefused(f"it carries no usable {CLIENT_ID_KEY!r}. {_CLIENT_ID_REASON}")
    try:
        policy = graph_window_policy(entry)
        categories = (
            None
            if registered is None
            else graph_category_scopes(entry, registered=registered)
        )
    except (MissingGraphSetting, UnknownProject, ValueError) as refused:
        # The refusals this row is *expected* to produce, each of which composed
        # a sentence naming what to change. `ValueError` is
        # `WindowPolicy.__post_init__`'s: CAP-2's 240-minute floor, and a
        # reach-back shorter than the width every later run covers anyway.
        raise _RowRefused(str(refused)) from None
    except Exception as unexpected:  # noqa: BLE001 — `build()` composes regardless
        # And everything else, for the reason the rest of this module swallows:
        # the row is plaintext and hand-editable, and no hand edit may stop
        # `build()` composing — `pm-ai doctor` is the command that diagnoses a
        # bad row, and it cannot run if the daemon will not compose. The live
        # instance was `OverflowError` from an absurd minute count, which is
        # bounded in `_minutes` now; this clause is what stops the next one
        # being found the same way. The **type** and not just the message,
        # because an unclassified fault's message rarely says what file it is
        # about.
        raise _RowRefused(
            f"reading it raised {type(unexpected).__name__}: {unexpected}. That "
            f"is a row pm-ai could not classify rather than a setting it can "
            f"name — report it."
        ) from None

    tenant = entry.get(TENANT_KEY)
    named_tenant = tenant.strip() if isinstance(tenant, str) and tenant.strip() else None
    if named_tenant is not None:
        # The check `pm-ai connector add graph` applies to the typed answer
        # (story 8l), applied to the row as well: the value becomes the last
        # segment of the sign-in address, so a hand edit holding a space or a
        # `/` would ask somewhere else.
        unusable = tenant_refusal(named_tenant)
        if unusable is not None:
            raise _RowRefused(f"its {TENANT_KEY!r} cannot be used: {unusable}")
    width, reach_back = entry.get(WIDTH_KEY), entry.get(REACH_BACK_KEY)
    if not isinstance(width, int) or not isinstance(reach_back, int):
        # Unreachable: `graph_window_policy` accepted both as whole minutes.
        # Said rather than asserted, so `python -O` cannot remove it.
        raise _RowRefused(
            f"its {WIDTH_KEY!r} and {REACH_BACK_KEY!r} passed the window check and "
            f"are not whole numbers, which is a fault in pm-ai — report it."
        )
    return _JudgedGraphRow(
        client_id=client_id.strip(),
        tenant=named_tenant,
        policy=policy,
        width=width,
        reach_back=reach_back,
        categories=categories,
    )


def _graph_connector(
    entry: Mapping[str, object],
    *,
    instance: str,
    store: SealedRefreshTokenStore,
    clock: Callable[[], datetime],
    registered: Callable[[str], bool],
) -> GraphConnector | UnbuiltConnector:
    """One Graph enrolment row as a connector, or the reason said out loud.

    **Five settings come off the row and four of them are undefaulted**: the
    Entra `client_id` this laptop signs in through, the two harvest widths, and
    the Outlook-category-to-project mapping — which defaults to empty, and that
    absence is safe because it means every meeting is personal, exactly what an
    untagged row gets anyway. The fifth is `tenant`, which *is* defaulted, to
    `GraphDeviceCodeAuth`'s own value rather than to a copy of it. This docstring
    said "four settings and none of them is defaulted" while the code beside it
    defaulted `tenant` to a literal, which is two claims one file apart that
    could not both be true. A sixth, `channels` (story 33d), lists the Teams
    channels read; absent, the connector reads the calendar only.

    They live in `connectors/<instance>.json` — per-machine, gitignored,
    hand-editable — rather than in `config.toml`, whose vocabulary stays closed
    at four keys: which app asks for a PM's calendar and how much of it to read
    are facts about this machine, not about pm-ai.

    **A row missing a setting builds nothing and the missing key is named**, on
    stderr, rather than being skipped silently the way every other failure here
    is. The difference is what the operator can do about it: an unreadable
    credential store is diagnosed by `pm-ai doctor`, while a `connectors/` row
    one key short is a file they are holding open, and a connector that quietly
    fails to appear is indistinguishable from one that was never enrolled.

    Returns the reason (`_unbuilt`'s, kept for `pm-ai connector check`, story
    4o) rather than raising, because `build()` must compose on a
    misconfigured machine — that is the whole reason the diagnostics can run at
    all.

    The row's checks are `_judged_graph_row`'s, which `pm-ai connector sign-in`
    applies before its sign-in (story 8m), so the two cannot disagree about
    whether a row would build.
    """
    try:
        judged = _judged_graph_row(entry, registered=registered)
        # The channels the PM listed by hand (`33d`). No list is calendar only;
        # a list that cannot be read builds nothing and names the entry. Read
        # here, at the start, and not in `_judged_graph_row`: a sign-in judges
        # nothing that names a project, as with the category mapping.
        channels = graph_channel_listings(entry, registered=registered)
    except (_RowRefused, UnknownProject, ValueError) as refused:
        # `ChannelListRefused` is a `ValueError`; `UnknownProject` a channel
        # mapped to a project no registry knows (`33d`).
        return _unbuilt(instance, str(refused), family="Graph")
    client_id, named_tenant, policy = judged.client_id, judged.tenant, judged.policy
    categories = judged.categories
    if categories is None:
        # Unreachable: judged with `registered`, so the mapping was read. Said
        # rather than asserted, so `python -O` cannot remove it.
        return _unbuilt(
            instance,
            "its category mapping was not read, which is a fault in pm-ai — report it.",
            family="Graph",
        )
    auth = GraphDeviceCodeAuth(
        client_id=client_id,
        # The fifth setting, and the one that *is* defaulted — to the adapter's
        # own value, read off the field rather than copied. A literal here would
        # be a second place `organizations` is decided, and the two would agree
        # until the day the adapter's changed. `organizations` is any work or
        # school tenant; the row names one when this PM's Entra application is
        # single-tenant.
        tenant=named_tenant if named_tenant is not None else GraphDeviceCodeAuth.tenant,
        # Custody: this instance's entry in the sealed store, read live and
        # written back under enrolment's claim (story 8j). The adapter hands a
        # rotated token to what it was given and never chooses where it goes.
        store=store,
        instance=instance,
        now=clock,
    )
    client = GraphClient(
        auth=auth,
        now=clock,
        # The real one, supplied here because `GraphClient` defaults to a
        # wait that does not wait — a blocking sleep as the default is
        # what makes a forgotten injection cost ten minutes inside a test
        # suite. Its 429 branch compares this against that default by
        # identity and refuses rather than spinning, so the composition
        # root is the only place the honoured hint can come from.
        wait=time.sleep,
    )
    return GraphConnector(
        auth=auth,
        calendar=GraphCalendarFetch(
            client=client,
            instance=instance,
            windows=policy,
            now=clock,
        ),
        categories=categories,
        now=clock,
        channels=channels,
        # One client for the calendar and every channel: one credential, one
        # host check, and the run's one budget (`33d`). A channel with no
        # stored position reaches back as far as the calendar's first run.
        messages=GraphChannelFetch(
            client=client,
            instance=instance,
            reach_back=policy.first_run_reach_back,
            now=clock,
        ),
    )


_CLIENT_ID_REASON = (
    "That is the Entra application the PM consents to, and pm-ai does not supply "
    "one: inventing it would be pm-ai choosing whose app asks for their calendar."
)


@dataclass(frozen=True)
class GraphSignedIn:
    """What a completed Graph sign-in hands to enrolment (story 8l).

    The credential to seal, the health check's sentence for the operator, and
    the row settings `_graph_connector` reads back at the next start — spelled
    with the connector's own key names, so the CLI never learns them. The
    settings carry the signed-in user's Graph user id under `USER_ID_KEY`
    (story 8q), which the row records and nothing at the start reads.
    """

    credential: str = field(repr=False)
    answer: str
    healthy: bool
    settings: Mapping[str, object]
    secrets: frozenset[str] = field(default=frozenset(), repr=False)
    """Every refresh token the sign-in and its check saw (story 8m).

    For redacting a message composed later — from a failed write, say — the
    way the check's own sentences already are.
    """


@dataclass(frozen=True)
class GraphSignInReplaced:
    """What `pm-ai connector sign-in` replaced (story 8m): no credential, ever.

    The health check's sentence, redacted, and whether it answered `OK` or
    only warned. The new sign-in itself stays in the sealed store.
    """

    answer: str
    healthy: bool


@dataclass(frozen=True)
class GraphEnrolment:
    """`pm-ai connector add graph`'s sign-in, behind the composition root (story 8l).

    The CLI asks the questions and enrols; this does the things it may not,
    because they reach `pm_ai.connectors`: checking up front that a sign-in
    could run at all, judging each answer with the checks a composition applies
    to the row, and running Microsoft's sign-in followed by CAP-35's bounded
    health check.

    The sign-in writes into an `InMemoryRefreshTokenStore`, never the sealed
    one: nothing is sealed until the health check has passed, and enrolment
    seals it then, under the shared claim, beside the settings. What is sealed
    is what the store holds *after* the check, because the forced refresh the
    check performs may have rotated the token the sign-in returned.

    Every refusal leaves as one of the two `pm_ai.ports` declares for a
    credential check (AD-49): `ProbeUnreachable` when Microsoft never answered
    or a bounded step overran, `ProbeFailed` for everything else — Microsoft
    refusing the app id or tenant, a declined, partial or expired sign-in, a
    failed check, a missing `msal`. Each carries the adapter's own sentence,
    with every refresh token this sign-in saw withheld from it.

    `pm-ai connector sign-in` (story 8m) uses the same sign-in for a connector
    already enrolled: `enrolled` judges its row before anything is asked, and
    `replace` signs in with the row's app id and tenant, checks the result, and
    replaces that connector's stored sign-in and nothing else.
    """

    storage: StorageService
    client_factory: Callable[[str, str], Any] | None = None
    """MSAL's client, or `None` for the adapter's own (a real `msal` import)."""

    timeout: float = CREDENTIAL_PROBE_SECONDS
    """The bound on each step nobody is acting on: asking for a code, and the check."""

    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    claim_sleep: Callable[[float], None] = time.sleep
    """How `replace` waits between tries for the credentials file's claim.

    The wait itself is `SealedRefreshTokenStore`'s — the one wait a rotation
    write gets (AD-5) — and this is only its sleep, injectable for a test.
    """

    announce: Callable[[], None] = lambda: None
    """Said once before a sign-in touches the sealed store (story 8m).

    The composition root passes `warn_if_encryption_disabled` when it built no
    daemon: `build()` prints that warning, and with no daemon nothing else did.
    """

    @property
    def minimum_window_minutes(self) -> int:
        """CAP-2's floor, read off the constant `WindowPolicy` enforces.

        Rounded up, so the minimum the question states is never below the one
        the check applies.
        """
        return math.ceil(HARVEST_CYCLE / timedelta(minutes=1))

    @property
    def default_tenant(self) -> str:
        """The adapter's own default, read off its field rather than copied."""
        return GraphDeviceCodeAuth.tenant

    def settings_file(self, instance: str) -> Path:
        """Where `instance`'s row lands — the file categories are added to by hand."""
        return (
            self.storage.paths.resolve(DataScope(ScopeKind.APPLICATION), "connectors/")
            / f"{instance}.json"
        )

    def ready(self) -> None:
        """Refuse now if no sign-in could run on this machine: `msal` is missing.

        Asked before any question, so nobody answers four of them to learn the
        installation was incomplete. Skipped when a client was injected.
        """
        if self.client_factory is not None:
            return
        try:
            require_msal()
        except GraphAuthError as missing:
            raise ProbeFailed(
                f"{missing} Nothing was asked and nothing was saved."
            ) from missing

    def refusal(
        self,
        *,
        client_id: str | None = None,
        tenant: str | None = None,
        width: int | None = None,
        reach_back: int | None = None,
    ) -> str | None:
        """Why the answers given so far would not build a connector, or `None`.

        Only what was given is judged, so each answer can be checked as it is
        typed. The checks are the ones composition applies to the row:
        `tenant_refusal`, `_minutes`' bounds, `WindowPolicy`'s floor and
        ordering. A width judged alone is paired with a reach-back equal to it,
        which `WindowPolicy` accepts, so only the width's own faults show.

        Minutes arrive as whole numbers — the CLI accepts digits only — so the
        one refusal `_minutes` can still make is its range, and it is reworded
        here for a typed answer: its own sentence is about a hand-edited row.
        """
        if client_id is not None and not client_id.strip():
            return f"no app (client) id was given. {_CLIENT_ID_REASON}"
        if tenant is not None:
            refused = tenant_refusal(tenant)
            if refused is not None:
                return refused
        if width is None:
            return None
        for typed in (width, reach_back):
            if typed is not None and not 0 < typed <= MAX_SETTING_MINUTES:
                return (
                    f"{typed} minutes is outside the 1 to {MAX_SETTING_MINUTES} "
                    f"minutes (ten years) a window may be; a number past that "
                    f"is a mistyped digit."
                )
        try:
            graph_window_policy(
                {
                    WIDTH_KEY: width,
                    REACH_BACK_KEY: width if reach_back is None else reach_back,
                }
            )
        except (MissingGraphSetting, ValueError) as refused_window:
            return str(refused_window)
        return None

    def sign_in(
        self,
        *,
        instance: str,
        client_id: str,
        tenant: str,
        width: int,
        reach_back: int,
        present: Callable[[str], None],
    ) -> GraphSignedIn:
        """Sign in through `present`, check the result, and say what to save.

        Asking Microsoft for a code is bounded at `timeout`, because nothing is
        on screen yet. The wait after the code is shown is not — a human is in
        a browser, for minutes. The health check after it is bounded again.
        """
        tenant = tenant.strip() or self.default_tenant
        refused = self.refusal(
            client_id=client_id, tenant=tenant, width=width, reach_back=reach_back
        )
        if refused is not None:
            raise ProbeFailed(f"{refused} Nothing was saved.")
        store = InMemoryRefreshTokenStore()
        auth = GraphDeviceCodeAuth(
            client_id=client_id.strip(),
            store=store,
            tenant=tenant,
            instance=instance,
            now=self.now,
            **(
                {}
                if self.client_factory is None
                else {"client_factory": self.client_factory}
            ),
        )
        try:
            auth.sign_in(
                present,
                starting=lambda start: run_bounded(
                    start, timeout=self.timeout, label=f"{instance} sign-in start"
                ),
            )
        except TimeoutError as stalled:
            raise ProbeUnreachable(
                f"Microsoft did not issue a sign-in code within {self.timeout:g}s, "
                f"so no code was shown. Nothing was saved."
            ) from stalled
        except GraphUnreachable as silent:
            raise ProbeUnreachable(_nothing_saved(str(silent))) from silent
        except GraphAuthError as refused_sign_in:
            raise ProbeFailed(_nothing_saved(str(refused_sign_in))) from refused_sign_in
        signed_in = _refresh_token(store.read())
        try:
            probe = run_bounded(
                auth.check_health,
                timeout=self.timeout,
                label=f"{instance} health check",
            )
        except TimeoutError as overran:
            raise ProbeUnreachable(
                f"the sign-in completed, and the health check that follows it "
                f"did not answer within {self.timeout:g}s, so whether this "
                f"machine can obtain a token is unknown. Nothing was saved."
            ) from overran
        credential = store.read()
        secrets = {t for t in (signed_in, _refresh_token(credential)) if t}

        def withheld(text: str, instead: str) -> str:
            for secret in secrets:
                text = redacted(text, secret, instead=instead)
            return text

        if probe.health not in (Health.OK, Health.WARNING):
            remedy = f" {probe.remediation}" if probe.remediation else ""
            raise ProbeFailed(
                withheld(
                    f"the sign-in completed and the health check failed: "
                    f"{probe.detail.rstrip('.')}.{remedy} Nothing was saved.",
                    instead=(
                        "the sign-in completed and the health check failed; its "
                        "message is withheld because it quoted the sign-in "
                        "token. Nothing was saved."
                    ),
                )
            )
        if not credential:
            raise ProbeFailed(
                "the sign-in completed and left no credential to save, which is "
                "a fault in pm-ai rather than anything Microsoft said. Nothing "
                "was saved."
            )
        # Read back from what is sealed, which carries the account the sign-in
        # named (story 8q). The adapter refused before sealing an account it
        # could not read a user id from, so `None` here is a pm-ai fault. A
        # credential that does not decode is its own failure and is not folded
        # into that one: it propagates as the adapter's refusal.
        try:
            user_id = SealedCredential.decode(credential, instance=instance).user_id
        except GraphAuthError as undecodable:
            raise ProbeFailed(_nothing_saved(str(undecodable))) from undecodable
        if user_id is None or not user_id.strip():
            raise ProbeFailed(
                "the sign-in completed and the credential it left does not name "
                "the signed-in user's Graph user id, which is a fault in pm-ai "
                "rather than anything Microsoft said. Nothing was saved."
            )
        answer = probe.detail
        if probe.health is Health.WARNING and probe.remediation:
            answer = f"{answer.rstrip('.')}. {probe.remediation}"
        return GraphSignedIn(
            credential=credential,
            answer=withheld(answer, instead=f"{instance} obtained a token"),
            healthy=probe.health is Health.OK,
            settings={
                CLIENT_ID_KEY: client_id.strip(),
                TENANT_KEY: tenant,
                WIDTH_KEY: width,
                REACH_BACK_KEY: reach_back,
                USER_ID_KEY: user_id,
            },
            secrets=frozenset(secrets),
        )


    # ── `pm-ai connector sign-in` (story 8m) ─────────────────────────────────

    def enrolled(self, instance: str) -> None:
        """Refuse, before anything is asked, an instance a new sign-in cannot replace.

        In the order an operator can act on: a name that cannot be a row
        (`MalformedInstanceName`); no row of that name
        (`ConnectorNotConfigured`); a row that is not JSON or not an object
        (`MalformedSettings`); a row whose `system` is missing, not text or not
        `graph` (`NotAGraphRow`); a row that would not build — the checks every
        start applies — (`MalformedSettings`, naming the key); then the sealed
        store, whose entry under another system is `CredentialNotHeld`, whose
        malformed document is `MalformedSettings` naming `config.json`, and
        whose master key, fetched here, is `KeyNotFound` on a machine with
        none. An `enabled` switched off is not refused: it governs harvesting,
        not enrolment. Nothing is written.

        `announce` runs first: with no daemon, nothing else said that
        encryption is off.
        """
        self.announce()
        self._judged_row(instance)
        assert_replaceable(self.storage, system="graph", instance=instance)

    def replace(
        self, *, instance: str, present: Callable[[str], None]
    ) -> GraphSignInReplaced:
        """Redo `instance`'s sign-in and replace its stored one, and nothing else.

        The row is read and judged once, and its app id, tenant and widths are
        what is signed in with, through `sign_in`'s bounded steps and its
        health check. Any authenticated account is accepted (Andrei,
        2026-10-12): identity and access are Microsoft's to guard, so neither
        the user nor the tenant is compared with what the row recorded.

        Then, under the credentials file's claim with the wait a rotation write
        gets, the row is read and judged **again**: a row removed, switched to
        another system, or naming another app id, tenant or width than the
        sign-in used is refused (`ConnectorRowChanged` names what changed) —
        that is about the settings the sign-in was made with. Only then is the
        credential replaced — written when the entry or the file is absent —
        and the row's `user_id` set to the signed-in user through
        `record_user_id`, which replaces whatever was recorded and keeps every
        other key, a `categories` mapping hand-added during the sign-in
        included.

        A file still busy after the wait is `ArtifactBusy`; one whose claim
        cannot be taken at all is `CredentialStoreUnclaimable`; both name
        `config.json` and write nothing. A row write that fails after the
        credential landed is `UserNotRecorded`, carrying the check's answer.
        Every message composed here from another exception is redacted against
        the refresh tokens the sign-in saw.
        """
        judged = self._judged_row(instance)
        signed = self.sign_in(
            instance=instance,
            client_id=judged.client_id,
            tenant=judged.tenant or "",
            width=judged.width,
            reach_back=judged.reach_back,
            present=present,
        )

        def withheld(text: str, instead: str) -> str:
            for secret in signed.secrets:
                text = redacted(text, secret, instead=instead)
            return text

        # Read through the one rule every reader of the key applies (story
        # 2o): blank, non-text or not a Graph user id is absent, refused here
        # before anything is saved rather than by `record_user_id` after the
        # credential has landed.
        user_id = usable_user_id(signed.settings)
        if user_id is None:
            raise ProbeFailed(
                "the sign-in completed and does not name a usable Graph user id "
                "for the signed-in user, which is a fault in pm-ai rather than "
                "anything Microsoft said. Nothing was saved."
            )

        store = SealedRefreshTokenStore(
            storage=self.storage, instance=instance, system="graph", sleep=self.claim_sleep
        )
        untouched = f"The new sign-in was not saved and {instance}'s stored one is untouched."
        try:
            with store.exclusive():
                self._assert_unchanged(instance, judged)
                replace_credential(
                    self.storage,
                    system="graph",
                    instance=instance,
                    credential=signed.credential,
                    claimed=True,
                    configured=True,
                )
                try:
                    record_user_id(
                        self.storage, instance=instance, user_id=user_id, claimed=True
                    )
                except Exception as unwritten:  # noqa: BLE001 — reported, never silent
                    raise UserNotRecorded(
                        withheld(
                            _joined(
                                f"{instance}: sign-in replaced; the user id was not "
                                f"recorded — writing {CONNECTORS}{instance}.json "
                                f"failed ({type(unwritten).__name__}: {unwritten})",
                                "The new sign-in is the one in use from now on",
                                f"Run `pm-ai connector sign-in {instance}` again to "
                                f"record the user id",
                            ),
                            instead=(
                                f"{instance}: sign-in replaced; the user id was not "
                                f"recorded — writing {CONNECTORS}{instance}.json "
                                f"failed, and its message is withheld because it "
                                f"quoted the sign-in token. Run `pm-ai connector "
                                f"sign-in {instance}` again to record the user id."
                            ),
                        ),
                        answer=signed.answer,
                    ) from unwritten
        except StoreBusy as busy:
            raise ArtifactBusy(
                withheld(
                    _joined(
                        f"the credentials file {CREDENTIAL_STORE} was busy past the "
                        f"wait: {busy}",
                        untouched,
                    ),
                    instead=f"the credentials file {CREDENTIAL_STORE} was busy past "
                    f"the wait. {untouched}",
                )
            ) from None
        except StoreUnavailable as unavailable:
            raise CredentialStoreUnclaimable(
                withheld(
                    _joined(
                        f"the claim on the credentials file {CREDENTIAL_STORE} could "
                        f"not be taken — this is not another command holding it: "
                        f"{unavailable}",
                        untouched,
                    ),
                    instead=f"the claim on the credentials file {CREDENTIAL_STORE} "
                    f"could not be taken. {untouched}",
                )
            ) from None
        return GraphSignInReplaced(answer=signed.answer, healthy=signed.healthy)

    def _row(self, instance: str) -> dict[str, object]:
        """`instance`'s row, with a missing or malformed one worded for a sign-in."""
        try:
            return connector_row(
                self.storage, instance=instance, purpose="its sign-in redone"
            )
        except ConnectorNotConfigured:
            raise ConnectorNotConfigured(
                f"no connector named {instance!r} is configured — {CONNECTORS} has "
                f"no row for it — so there is no sign-in to redo. Nothing was "
                f"asked and nothing was saved."
            ) from None
        except MalformedSettings as malformed:
            raise MalformedSettings(
                f"{_sentence(str(malformed).removesuffix(' Nothing was written.'))} "
                f"Nothing was asked and nothing was saved."
            ) from None

    def _judged_row(self, instance: str) -> _JudgedGraphRow:
        """`instance`'s row, judged by every start's checks, or the refusal naming why."""
        row = self._row(instance)
        system = row.get("system")
        if system is None:
            reason = "names no `system`"
        elif not isinstance(system, str):
            reason = f"has a `system` that is a {type(system).__name__}, not text"
        elif system != "graph":
            reason = f"is configured as a {system!r} connector, not a Graph one"
        else:
            reason = None
        if reason is not None:
            raise NotAGraphRow(
                f"the row for {instance!r} in {CONNECTORS} {reason}, so there is no "
                f"Microsoft sign-in to redo. Nothing was asked and nothing was "
                f"saved."
            )
        try:
            return _judged_graph_row(row)
        except _RowRefused as refused:
            raise MalformedSettings(
                f"the row for {instance!r} in {CONNECTORS} would not build a "
                f"connector: {_sentence(str(refused))} Nothing was asked and "
                f"nothing was saved."
            ) from None

    def _assert_unchanged(self, instance: str, signed_with: _JudgedGraphRow) -> None:
        """Refuse when the row no longer says what the sign-in was made with."""
        try:
            now = self._judged_row(instance)
        except (ConnectorNotConfigured, NotAGraphRow, MalformedSettings) as changed:
            raise ConnectorRowChanged(
                f"the row for {instance!r} changed during the sign-in: "
                f"{_sentence(str(changed).removesuffix(' Nothing was asked and nothing was saved.'))} "
                f"The sign-in was not saved and {instance}'s stored one is "
                f"untouched."
            ) from None
        differs = [
            f"{key} {before!r} is now {after!r}"
            for key, before, after in (
                (CLIENT_ID_KEY, signed_with.client_id, now.client_id),
                (TENANT_KEY, signed_with.tenant, now.tenant),
                (WIDTH_KEY, signed_with.width, now.width),
                (REACH_BACK_KEY, signed_with.reach_back, now.reach_back),
            )
            if before != after
        ]
        if differs:
            raise ConnectorRowChanged(
                f"the row for {instance!r} changed during the sign-in — "
                f"{'; '.join(differs)} — so a sign-in made with the earlier "
                f"settings was not saved and {instance}'s stored one is "
                f"untouched. Run `pm-ai connector sign-in {instance}` again."
            )


# ── `pm-ai connector add gitlab` (story 8o) ──────────────────────────────────

GITLAB_PATH_FORM = "group/project"
"""How the project question names the form it wants — the path in the
project's GitLab address."""


@dataclass(frozen=True)
class GitLabChecked:
    """What a GitLab check that passed hands to enrolment (story 8o).

    No token: the CLI already holds the one it typed, and enrolment seals that.
    `project` travels apart from `settings` because `project` is a key
    enrolment writes itself (`RESERVED_ROW_KEYS`); `settings` carries the
    row's other GitLab key, the host, spelled by the connector.
    """

    answer: str
    project: str
    settings: Mapping[str, object]


@dataclass(frozen=True)
class GitLabEnrolment:
    """`pm-ai connector add gitlab`'s checks, behind the composition root (story 8o).

    The CLI asks the questions and enrols; this does what it may not, because
    it reaches `pm_ai.connectors`: judging the host, the project path and the
    typed token with the transport's own rules, placing the instance name
    among the watched projects, and asking GitLab for one commit of that
    project — the transport's health check (`GitLabClient.check`) — bounded at
    CAP-35's ten seconds, with the transport's thirty-second socket timeout
    inside it.

    Every refusal leaves as one of the two `pm_ai.ports` declares for a
    credential check (AD-49): `ProbeUnreachable` only when GitLab could not be
    heard from or asked to wait — an `OSError` on the wire, no answer within
    the bound, 429 or a 5xx — so a good token is not reissued for a host that
    was down; `ProbeFailed` for everything else — 401, 403 (no `read_api`),
    404, a redirect, any other status, a 2xx whose body is not a list, and an
    exception pm-ai cannot classify, named by type. No message carries the
    token.
    """

    storage: StorageService
    watching: Callable[[], Sequence[str]] = lambda: ()
    """Every project watched *now* — read again each time a name is placed.

    A callable rather than a value, because projects can be enrolled while
    the questions are answered, and the placement is checked again just before
    the save (`assert_placed`).
    """

    transport: Callable[[GitLabRequest], GitLabResponse] | None = None
    """The wire, or `None` for the transport's own (a real `urllib` `GET`)."""

    timeout: float = CREDENTIAL_PROBE_SECONDS
    """CAP-35's bound on the check: whatever has not answered by then is unreachable."""

    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    @property
    def default_host(self) -> str:
        """What Enter keeps at the host question — the transport's own default."""
        return GITLAB_DEFAULT_HOST

    def settings_file(self, instance: str) -> Path:
        """Where `instance`'s row lands, named in the closing message."""
        return (
            self.storage.paths.resolve(DataScope(ScopeKind.APPLICATION), "connectors/")
            / f"{instance}.json"
        )

    def ready(self, instance: str) -> None:
        """Refuse now what no answer could fix: the keychain, and a name no start builds.

        `assert_enrollable` reads the sealed store and fetches the master key —
        `KeyNotFound` with none, `KeychainUnavailable` for one that cannot be
        read, `DuplicateConnector` for a credential sealed with no row (the
        half-enrolled state). Then `assert_placed`.
        """
        assert_enrollable(self.storage, system="gitlab", instance=instance)
        self.assert_placed(instance)

    def assert_placed(self, instance: str) -> None:
        """Refuse a name no start would build, against the projects watched now.

        The placement rule every start applies (`_placement`): on a machine
        watching several projects, a name that is not `gitlab:<enrolled
        project id>` is never built, so it is refused (`UnplacedConnector`).
        With one project any name builds into it; with none, nothing is built
        until one is enrolled, and the add is not refused.
        """
        scopes = {
            _builtin_instance(project_id): DataScope(ScopeKind.PROJECT, project_id)
            for project_id in sorted(set(self.watching()))
        }
        if scopes and isinstance(_placement(instance, scopes=scopes), str):
            names = ", ".join(sorted(scopes))
            raise UnplacedConnector(
                f"pm-ai watches {len(scopes)} projects, and {instance!r} names "
                f"none of them, so a GitLab connector enrolled under it would "
                f"never be built: nothing says which project's tree its harvest "
                f"files into. Name it gitlab:<enrolled project id> — one of "
                f"{names}. Nothing was saved."
            )

    def refusal(self, *, host: str | None = None, project: str | None = None) -> str | None:
        """Why a typed host or project path would not build a connector, or `None`.

        The host is judged by the transport's `host_refusal`, plus one rule a
        typed host adds: its path may not end in `/api/v4`, which the API is
        reached under. The path is judged by the transport's
        `project_refusal`, plus what a typed `group/project` must also be:
        at least one `/`, no `://` (a pasted address), no trailing `/` and no
        empty segment.
        """
        if host is not None:
            refused = host_refusal(host)
            if refused is None:
                path = urlsplit(host).path.rstrip("/").casefold()
                if path == API_PATH or path.endswith(API_PATH):
                    refused = (
                        f"it ends in {API_PATH!r}, which pm-ai adds itself; type "
                        f"the address without it"
                    )
            if refused is not None:
                return (
                    f"{host!r} cannot be the GitLab host: {refused}. Type an "
                    f"https address, optionally with a path "
                    f"(https://git.internal/gitlab), or press Enter for "
                    f"{self.default_host}."
                )
        if project is not None:
            refused = project_refusal(project)
            if refused is None:
                if "://" in project:
                    refused = "it is an address, not a path — type only the part after the host"
                elif "/" not in project:
                    refused = "it holds no '/'"
                elif project.endswith("/"):
                    refused = "it ends in '/'"
                elif "" in project.split("/"):
                    refused = "it has an empty segment ('//')"
            if refused is not None:
                return (
                    f"{project!r} cannot be the project path: {refused}. Type "
                    f"it as {GITLAB_PATH_FORM} — the path in the project's "
                    f"GitLab address."
                )
        return None

    def check(
        self, *, instance: str, host: str, project: str, credential: str
    ) -> GitLabChecked:
        """Ask GitLab for one commit of `project` with the token, and judge the answer.

        The host is saved and asked with its scheme and host name lower-cased.
        The token is judged first by the transport's `token_refusal`, so a
        token that could never travel in a header is refused without a request.

        Raises `ProbeFailed` or `ProbeUnreachable`; returns what to save.

        The ten-second bound abandons the *wait*, not the request: a socket
        read cannot be cancelled, so the request may still be in flight when
        this refuses. Nothing is saved either way — whatever it answers later
        is discarded.
        """
        token = credential.strip()

        def withheld(text: str) -> str:
            return redacted(
                text,
                token,
                instead=(
                    "GitLab's answer is withheld because it quoted the token. "
                    "Nothing was saved."
                ),
            )

        refused = self.refusal(host=host, project=project)
        if refused is not None:
            raise ProbeFailed(f"{refused} Nothing was saved.")
        if not token:
            raise ProbeFailed("no token was typed, so nothing was saved.")
        if token_refusal(token) is not None:
            raise ProbeFailed(
                "the token you typed holds a space, a line break, a control "
                "character or a non-ASCII character, so it cannot travel in a "
                "request header and is not a GitLab token; nothing was sent or "
                "saved."
            )
        host = _lowered_origin(host)
        client = GitLabClient(host=host, project=project, token=token, now=self.now)
        heard: list[GitLabResponse | BaseException] = []
        wire = client.transport if self.transport is None else self.transport

        def recorded(request: GitLabRequest) -> GitLabResponse:
            try:
                answered = wire(request)
            except BaseException as raised:
                heard.append(raised)
                raise
            heard.append(answered)
            return answered

        client.transport = recorded
        try:
            run_bounded(client.check, timeout=self.timeout, label=f"{instance} health check")
        except TimeoutError as silent:
            raise ProbeUnreachable(
                f"GitLab at {host} is unreachable: it did not answer within "
                f"{self.timeout:g}s; try later; the token was not judged. "
                f"Nothing was saved."
            ) from silent
        except PageUnavailable as answer:
            raise _gitlab_verdict(
                answer,
                host=host,
                project=project,
                raw=heard[-1] if heard else None,
                withheld=withheld,
            ) from None
        except Exception as unexpected:  # noqa: BLE001 — refused by type, never raised raw
            raise ProbeFailed(
                f"the check of {project!r} on {host} raised "
                f"{type(unexpected).__name__}, which pm-ai cannot classify — "
                f"report it. Nothing was saved."
            ) from None
        last = heard[-1] if heard else None
        if not isinstance(last, GitLabResponse) or not isinstance(last.body, list):
            status = last.status if isinstance(last, GitLabResponse) else "nothing"
            raise ProbeFailed(
                withheld(
                    f"GitLab at {host} answered {status} for {project!r} with a "
                    f"body that is not a list of commits, so it is not a GitLab "
                    f"API answer. Check that the host is the GitLab instance "
                    f"itself, with its path if it is served under one. Nothing "
                    f"was saved."
                )
            )
        return GitLabChecked(
            answer=withheld(f"{host} answered for {project}"),
            project=project,
            settings={GITLAB_HOST_KEY: host},
        )


def _lowered_origin(host: str) -> str:
    """`host` with its scheme and host name lower-cased, its path as typed.

    Only called on a host `host_refusal` accepted, so there is no user info
    to keep the case of.
    """
    parts = urlsplit(host)
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, parts.fragment)
    )


def _no_answer(raw: GitLabResponse | BaseException | None) -> BaseException | None:
    """The `OSError` behind a request that got no answer, or `None`.

    Raised by an injected transport itself, or carried as the cause of the
    real transport's `PageUnavailable` — which is how `urllib`'s connection
    and socket errors reach the client.
    """
    if isinstance(raw, OSError):
        return raw
    if isinstance(raw, PageUnavailable) and isinstance(raw.__cause__, OSError):
        return raw.__cause__
    return None


def _gitlab_verdict(
    answer: PageUnavailable,
    *,
    host: str,
    project: str,
    raw: GitLabResponse | BaseException | None,
    withheld: Callable[[str], str],
) -> ProbeFailed:
    """The refusal one failed GitLab check is reported as (story 8o, AD-49).

    By status and by what the wire raised, never by parsing the client's
    sentence. Unreachable is exactly: 429, a 5xx, or an `OSError` on the
    wire — a good token is not reissued for a bad hour or a typo in the
    host. Everything else refuses, for its own reason.
    """
    status = answer.status
    nothing = "Nothing was saved."
    later = "try later; the token was not judged."
    if status is not None and 300 <= status < 400:
        return ProbeFailed(
            withheld(
                f"GitLab at {host} answered {status} with a redirect, and pm-ai "
                f"does not follow one: the token would travel to whatever the "
                f"redirect names. Give the final address — the one the redirect "
                f"leads to — as the host. {nothing}"
            )
        )
    if status == 401:
        return ProbeFailed(
            withheld(
                f"GitLab refused the token (401): it is expired, revoked or "
                f"mistyped. Create a personal access token with the `read_api` "
                f"scope and run this again. {nothing}"
            )
        )
    if status == 403:
        return ProbeFailed(
            withheld(
                f"GitLab answered 403 for {project!r} on {host}: the token lacks "
                f"the `read_api` scope, so a connector holding it could not "
                f"harvest, and it is not enrolled. Give the token `read_api`, or "
                f"create one that has it. {nothing}"
            )
        )
    if status == 404:
        return ProbeFailed(
            withheld(
                f"GitLab answered 404: no project is visible at {project!r} on "
                f"{host}. The path, not the token, is the likely mistake — "
                f"GitLab answers 404 for a project the token cannot see as well "
                f"as for one that does not exist. {nothing}"
            )
        )
    if status == 429:
        return ProbeUnreachable(
            withheld(f"GitLab answered 429 (it is rate-limiting this machine); {later} {nothing}")
        )
    if status is not None and status >= 500:
        return ProbeUnreachable(
            withheld(f"GitLab answered {status} (a server error); {later} {nothing}")
        )
    silent = _no_answer(raw)
    if silent is not None:
        return ProbeUnreachable(
            withheld(
                f"GitLab at {host} is unreachable: no answer came back "
                f"({type(silent).__name__}); {later} {nothing}"
            )
        )
    return ProbeFailed(withheld(f"{_sentence(answer.reason)} {nothing}"))


def _sentence(text: str) -> str:
    """`text` as a whole sentence, ending in one full stop or other end mark."""
    stripped = text.strip()
    if not stripped:
        return ""
    return stripped if stripped[-1] in ".!?" else f"{stripped}."


def _joined(*parts: str) -> str:
    """Sentences joined with one space, each ended once, so none run together."""
    return " ".join(sentence for sentence in map(_sentence, parts) if sentence)


def _refresh_token(credential: str | None) -> str | None:
    """The raw refresh token inside a sealed Graph credential, or `None`."""
    if not credential:
        return None
    try:
        return SealedCredential.decode(credential).refresh_token
    except GraphAuthError:
        return None


def _nothing_saved(sentence: str) -> str:
    """`sentence`, ending by saying nothing was saved unless it already does."""
    folded = sentence.casefold()
    if "nothing was stored" in folded or "nothing was saved" in folded:
        return sentence
    return f"{sentence} Nothing was saved."


CLAIM_WAIT_ATTEMPTS = 40
"""How many times the sealed store's claim is tried before reporting it busy."""

CLAIM_WAIT_INTERVAL = 0.05
"""Seconds between tries. With the attempts above, about two seconds in all."""


@dataclass
class SealedRefreshTokenStore:
    """One Graph instance's refresh token, in the sealed credentials file (story 8j).

    The file holds every connector's credential keyed by instance name, so this
    store reads and writes one entry and nothing else. It reads the file each
    time it is asked rather than a copy taken at composition, so a token
    another run rotated is the one this run refreshes from. That is a
    deliberate cost: every read fetches the master key and decrypts the whole
    file. It writes through `replace_credential`, under the same claim
    enrolment takes, so neither can lose the other's write.

    `exclusive()` is that claim, plus a lock for threads sharing this object.
    The storage service refuses at once when the claim is held, which is right
    for enrolment and wrong here: two `pm-ai` commands refreshing at the same
    moment are ordinary, not a mistake. So both are retried within one budget
    of about two seconds before `StoreBusy` is raised.

    `write()` is accepted only from the thread inside `exclusive()`. Taking the
    claim is the caller's job (the adapter takes it around every write), and a
    write from anywhere else would be a read-modify-write without the lock.

    The storage service's own refusals are translated into the protocol's
    vocabulary — `StoreBusy`, `StoreUnavailable`, `StoreEntryMissing` — so the
    connector never learns what storage raises.
    """

    storage: StorageService
    instance: str
    system: str
    sleep: Callable[[float], None] = time.sleep
    attempts: int = CLAIM_WAIT_ATTEMPTS
    interval: float = CLAIM_WAIT_INTERVAL
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _holder: int | None = field(default=None, init=False, repr=False)

    def read(self) -> str | None:
        """This instance's sealed credential, or `None` when it has none.

        An unreadable file raises `StoreUnavailable`, so a locked keychain does
        not present as a machine nobody enrolled.
        """
        try:
            held = stored_credentials(self.storage).get(self.instance)
        except Exception as unreadable:  # noqa: BLE001 — translated, not swallowed
            raise StoreUnavailable(
                f"{CREDENTIAL_STORE} could not be read: "
                f"{type(unreadable).__name__}: {unreadable}"
            ) from None
        return credential_for(held, system=self.system)

    def write(self, credential: str) -> None:
        """Replace this instance's credential. Only from inside `exclusive()`."""
        if self._holder != threading.get_ident():
            raise RuntimeError(
                f"a write to {self.instance}'s entry in {CREDENTIAL_STORE} was "
                f"made outside this thread's claim on it, so it was refused: "
                f"without the claim it could lose another run's write."
            )
        try:
            replace_credential(
                self.storage,
                system=self.system,
                instance=self.instance,
                credential=credential,
                claimed=True,
            )
        except CredentialNotHeld as missing:
            raise StoreEntryMissing(str(missing)) from None

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """The sealed store's claim, waited for briefly. Not re-entrant."""
        me = threading.get_ident()
        if self._holder == me:
            # A programming error, raised before any attempt and never retried
            # as busy (AD-5, story 8q): waiting would wait on this thread itself.
            raise ClaimReentered(
                f"the claim on {CREDENTIAL_STORE} is already held by this thread "
                f"for {self.instance}; it is not re-entrant."
            )
        tries = max(self.attempts, 1)
        locked = False
        busy: str | None = None
        with ExitStack() as claimed:
            for attempt in range(tries):
                if not locked:
                    locked = self._lock.acquire(blocking=False)
                    if locked:
                        # Registered first, so it runs last: the file's claim is
                        # given up before another thread may try for it.
                        claimed.callback(self._lock.release)
                if locked:
                    try:
                        claimed.enter_context(
                            self.storage.exclusive(
                                scope=DataScope(ScopeKind.APPLICATION),
                                artifact=CREDENTIAL_STORE,
                            )
                        )
                        busy = None
                        break
                    except ArtifactBusy as held:
                        busy = str(held)
                    except OSError as unclaimable:
                        raise StoreUnavailable(
                            f"the claim on {CREDENTIAL_STORE} could not be taken: "
                            f"{type(unclaimable).__name__}: {unclaimable}"
                        ) from None
                else:
                    busy = (
                        f"another caller in this process held the claim on "
                        f"{CREDENTIAL_STORE} for {self.instance}."
                    )
                if attempt + 1 < tries:
                    self.sleep(self.interval)
            if busy is not None:
                raise StoreBusy(busy)
            self._holder = me
            claimed.callback(self._release_holder)
            yield

    def _release_holder(self) -> None:
        self._holder = None


def _unbuilt(instance: str, reason: str, *, family: str) -> UnbuiltConnector:
    """Say why a connector this machine is configured for was not constructed.

    stderr, because the daemon has not composed yet, so there is no event log to
    write into and no storage to reach that has not already been the thing that
    failed; and returned, so the daemon carries the sentence for `pm-ai
    connector check` to list (story 4o).
    """
    print(
        f"WARNING: the {family} connector {instance!r} was not built — {reason}",
        file=sys.stderr,
    )
    return UnbuiltConnector(instance, reason)


def _credential_for(sealed: object, *, system: str) -> str | None:
    """The sealed credential for one instance, or `None` when there is none to use.

    Two refusals, both of which used to hand something through:

    - **Not a string.** `private/config.json` is decrypted JSON, so a
      hand-repaired or half-written entry can hold a number, a list or a nested
      object. `GitLabConnectorAdapter.check_health` calls `.strip()` on the value
      and would have raised inside a probe that is required never to raise, which
      takes the whole `connector check` report down with it.
    - **Sealed under a different system.** An entry recording `system: "graph"`
      against a configuration that says `gitlab` is two different connectors
      wearing one instance name, and handing a Microsoft refresh token to the
      GitLab adapter would send it to the wrong provider. Reported as `ABSENT`,
      which is the honest answer: this instance has no credential *for this
      system*.
    """
    # The rule lives in core so the Graph write-back applies the same one: an
    # entry readable as this system's is one `replace_credential` will replace.
    return credential_for(sealed, system=system)


def _stored_credentials(storage: StorageService) -> dict[str, dict[str, str]]:
    """The sealed store's credentials, or nothing when it cannot be opened.

    Swallowed for the reason every failure in `_enrolled_connectors` is: the
    daemon must compose on a broken machine so `pm-ai doctor` can run and say
    what is broken. The sealed store needs the master key, so a keyless or
    locked machine raises here — and a `build()` that raised would take the
    diagnostics down with it.

    The cost is stated rather than hidden: a GitLab connector whose credential
    could not be *read* reports `ABSENT`, the same as one that has none. The
    keychain probe is what tells those two apart, and it reports the cause
    directly instead of through a connector row. A Graph connector does not go
    through here: its store reads the file live (story 8j), so an unreadable
    file reports as a custody failure rather than `ABSENT`.
    """
    try:
        return stored_credentials(storage)
    except Exception:
        return {}


def _named_configurations(
    storage: StorageService,
) -> tuple[tuple[str, Mapping[str, object]], ...]:
    """Every readable `connectors/<name>.json`, with the member name it was read from.

    The name is what a row that cannot name itself — no usable `instance` — is
    listed under by `pm-ai connector check` (story 4o).
    """
    application = DataScope(ScopeKind.APPLICATION)
    try:
        names = storage.list_collection(scope=application, artifact="connectors/")
    except Exception:
        return ()
    entries: list[tuple[str, Mapping[str, object]]] = []
    for name in names:
        try:
            raw = storage.read_artifact(
                scope=application, artifact="connectors/", name=name
            )
        except Exception:
            continue
        if raw is None:
            continue
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(decoded, dict):
            entries.append((name, decoded))
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class UnusableRow:
    """A connector row `read_pm_identity` could not use, and why."""

    name: str
    reason: str


@dataclass(frozen=True, slots=True)
class PmIdentityReading:
    """Who the PM is, and which rows could not say, from one read at one instant.

    `identity` is what `pm_identity` answered; `unusable` lists every row that
    could not be read, and every enabled Graph row with no usable user id, so
    `23c`'s sign-in line and its marking come from the same read.
    """

    identity: frozenset[PersonHandle]
    unusable: tuple[UnusableRow, ...]


def read_pm_identity(storage: StorageService) -> PmIdentityReading:
    """Read every `connectors/` row once, now, and ask `pm_identity` (story 2o).

    The one caller of `pm_identity`: `23c` and the transcript pipeline call
    this, never `pm_identity`. Nothing is cached, so a row added while the
    daemon runs is in the next call's answer. A row is replaced whole by
    `write_artifact`, so a read during `8q`'s write sees a whole row.

    A row that cannot be used is listed, never raised and never dropped:
    a storage-read failure (`OSError`, `DecryptionFailed`), bytes that are not
    UTF-8 JSON, a document that is not an object, a row removed between the
    listing and its read, and a Graph row with no usable user id that is
    enabled — or whose `enabled` is neither `true` nor `false`, which is named.
    A failure to list `connectors/` is one entry for the collection. Anything
    else is a defect in pm-ai and propagates.

    `aliased` is empty: no row names the PM's person, and a test-filled alias
    table is not a record. The slice that saves the table fills it here and
    changes no caller.
    """
    application = DataScope(ScopeKind.APPLICATION)
    try:
        names = storage.list_collection(scope=application, artifact="connectors/")
    except (OSError, DecryptionFailed) as unlisted:
        return PmIdentityReading(
            identity=pm_identity((), aliased=frozenset()),
            unusable=(
                UnusableRow("connectors/", f"could not be listed ({unlisted})"),
            ),
        )
    rows: list[Mapping[str, object]] = []
    unusable: list[UnusableRow] = []
    for name in names:
        try:
            raw = storage.read_artifact(
                scope=application, artifact="connectors/", name=name
            )
        except (OSError, DecryptionFailed) as unreadable:
            unusable.append(UnusableRow(name, f"could not be read ({unreadable})"))
            continue
        if raw is None:
            unusable.append(UnusableRow(name, "was removed while it was read"))
            continue
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            unusable.append(UnusableRow(name, "is not UTF-8"))
            continue
        except json.JSONDecodeError:
            unusable.append(UnusableRow(name, "is not valid JSON"))
            continue
        if not isinstance(decoded, dict):
            unusable.append(
                UnusableRow(name, f"holds a {type(decoded).__name__}, not an object")
            )
            continue
        rows.append(decoded)
        if decoded.get("system") != GRAPH or usable_user_id(decoded) is not None:
            continue
        enabled = decoded.get("enabled")
        if enabled is True:
            unusable.append(
                UnusableRow(name, "is an enabled Graph row with no usable user id")
            )
        elif enabled is not False:
            unusable.append(
                UnusableRow(
                    name,
                    f"is a Graph row with no usable user id whose `enabled` is "
                    f"{enabled!r}, neither true nor false",
                )
            )
    return PmIdentityReading(
        identity=pm_identity(rows, aliased=frozenset()),
        unusable=tuple(unusable),
    )


@dataclass(frozen=True, slots=True)
class Bootstrap:
    """The two application artifacts `doctor` reports on, read before any daemon.

    One read each, from one `StorageService`, so `doctor` and the daemon cannot
    end up holding two different answers about the same file.
    """

    projects: Mapping[str, ProjectEntry]
    registry: ArtifactState
    config: ArtifactState


def bootstrap(keychain: KeychainPort, *, paths: ScopePaths | None = None) -> Bootstrap:
    """`projects.toml` and `config.toml`, read before the daemon exists.

    ## Why the read happens here at all

    The registry has to be read *before* the resolver exists — `production()`
    takes the mapping as an argument (AD-11) — but `StorageService` is the
    single reader (AD-5) and is constructed *on* a resolver. `config.toml` used
    to sidestep the circle by being read after `build()` returned; the registry
    cannot, because `build()` is what needs it.

    So the read happens against a **bootstrap resolver**: `production()` over an
    empty mapping, which resolves the application scope perfectly well and knows
    no projects. That costs nothing here — both files are application-scope, and
    nothing in this function touches a project tree. `pm_ai.app` is the one
    layer permitted to import both `pm_ai.storage` and `pm_ai.platform`, which
    is why this lives in `wiring` and not in `entry` beside its caller.

    ## `config.toml` is read here too, and that is the point

    It could be read after `build()`, and was until 2026-09-15. Then `doctor`
    grew a probe for it and the arrangement produced a lie on the most ordinary
    machine there is: a first run has no project, so composition stopped before
    the config was ever read, and the probe reported `FAILING` —
    "could not reach the file to find out" — about a file that was simply not
    there yet. Read here, that machine gets `ABSENT` and the command that fixes
    it. `UNOBTAINABLE` is left for what it actually describes: a run where even
    this could not happen.

    The cipher is the ordinary one and costs nothing: `LazyKeyCrypto` reaches
    the keychain when an encrypted artifact is touched, and neither of these is.

    ## A refusal is not an absence

    The mapping is empty in three different situations — no file, an empty file,
    and a file that would not parse — and only the first two mean "no projects
    are enrolled". The `ArtifactState` carries the difference to `doctor`, which
    is the surface that has somewhere to say it. Nothing here raises on a
    malformed registry: `4c` requires `pm-ai doctor` to survive a machine that
    is broken, and a registry nobody can parse is exactly that machine.
    """
    storage = application_storage(keychain, paths=paths)
    registry = _artifact_state(storage, REGISTRY_ARTIFACT)
    config = _artifact_state(storage, CONFIG_ARTIFACT)
    if registry.raw is None:
        return Bootstrap({}, registry, config)
    try:
        return Bootstrap(parse_registry(registry.raw), registry, config)
    except RegistryRefused:
        # Carried, not raised, and not logged here either: the state holds the
        # bytes, so `registry_readable` re-reads them and reports the parser's
        # own message — which names the project or the line, and is the only
        # form of this an operator can act on.
        return Bootstrap({}, registry, config)


def application_storage(
    keychain: KeychainPort, *, paths: ScopePaths | None = None
) -> StorageService:
    """The single reader and writer over a resolver that knows no project.

    `bootstrap`'s writer, made public for `4h`: `pm-ai setup` writes
    `config.toml` on a machine where no daemon exists yet — composition stops
    at "no project enrolled" until the step before it has run — so the write
    cannot go through `daemon.storage`. Both artifacts it serves are
    application-scope, which a resolver over an empty project map resolves
    perfectly well.
    """
    resolver = paths if paths is not None else ScopePaths.production()
    return _writer(
        resolver,
        now=lambda: datetime.now(timezone.utc),
        vcs=GitVcs(),
        crypto=_choose_crypto(keychain, encryption_disabled=encryption_off()),
    )


def artifact_state(storage: StorageService, artifact: str) -> ArtifactState:
    """`_artifact_state`, for the daemon's status endpoint (story 4e).

    The daemon re-reads `config.toml` and `projects.toml` through its own one
    writer on every status request, so a hand-edit shows up without a restart
    and nothing is composed per request.
    """
    return _artifact_state(storage, artifact)


def _artifact_state(storage: StorageService, artifact: str) -> ArtifactState:
    """One application-scope artifact as the reader found it.

    The distinction `4i` needs and `bytes | None` cannot express: absence is a
    first run with a command to fix it, while a permission error is a machine
    with a file it cannot open. Folded together, an operator is told to create a
    file they already have — and for the registry that advice is destructive,
    since `projects.toml` is rebuildable from nothing and the command they would
    run writes over it.
    """
    try:
        raw = storage.read_artifact(scope=DataScope(ScopeKind.APPLICATION), artifact=artifact)
    except FileNotFoundError:
        # Unreachable through the real service since `8f` gave `read_artifact`
        # its `bytes | None` form, and kept for a fake that has not caught up —
        # the same arrangement, and the same reason, as `entry.read_optional`.
        return ArtifactState.absent()
    except OSError as unreadable:
        return ArtifactState.unreadable(str(unreadable))
    return ArtifactState.absent() if raw is None else ArtifactState.read(raw)


@dataclass(frozen=True, slots=True)
class Onboarded:
    """What `onboard_project` did, in enough detail for the CLI to say so."""

    project_id: str
    repository: Path
    gitignore: Path
    already_registered: bool
    """True when the registry already held this id at this path.

    An ordinary outcome and not a failure (the human's Q5): re-running
    `project add` on an onboarded project reports and changes nothing. It is a
    separate field rather than an exception because the *command* succeeds —
    exit 0 — and only the sentence printed differs.
    """


def onboard_project(
    keychain: KeychainPort,
    raw_path: str,
    alias: str | None = None,
    *,
    paths: ScopePaths | None = None,
) -> Onboarded:
    """`pm-ai project add <path> [alias]` — everything but the printing.

    ## Why the sequence lives in `app`

    It needs three things no other layer may hold at once: the filesystem, which
    `core` may not touch; `pm_ai.storage`, which `surfaces` may not reach; and
    `_directory_name`'s standard for an id, which lives in `pm_ai.platform` and
    which `core` may not import. `app` is the only layer permitted all three.

    ## The order, and why every step is where it is

    Validation first, and entirely: a path that is a file, or a name that cannot
    be a directory, is refused before anything is created. A refusal after a
    partial create leaves a project half-onboarded and the operator with no way
    to tell how far it got.

    Then the exclusive claim, held across read, decide and write. `write_artifact`
    publishes with `os.replace`, so two concurrent runs that both read a
    one-entry registry both render a two-entry one and the second silently
    discards the first's project. The claim is what makes "both entries are
    present afterwards" true rather than usually true. Taken through
    `storage.exclusive`, the one door every whole-file claim uses (story `1r`),
    so a busy registry is `ArtifactBusy` naming `projects.toml` and its scope.

    Inside the claim: `.gitignore` before the structure, and the registry last.

    - **`.gitignore` first** because `_assert_git_excludes` refuses every write
      to a `GITIGNORED` artifact that git would commit, and since `1n` that is
      the project's whole `memory/` tree. A project onboarded without the rule
      looks onboarded and fails on its first harvest.
    - **The registry last** so the failure mode is the recoverable one. If the
      registry write is refused, the directory and the rule remain and re-running
      completes; the reverse order would leave a registered project with no home.

    ## What it does not do

    No `git init`, and no check for a repository: `service.py:714-717` already
    treats "no working tree" as an answer rather than an unanswered question, so
    a plain directory onboards and gets its rule anyway. No removal, and no
    rename of an id — the id is the scope directory name, so changing it moves
    every artifact and stales every `SourceRef`.
    """
    repository = _resolved(raw_path)
    project_id = alias if alias is not None else repository.name
    resolver = paths if paths is not None else ScopePaths.production()
    # `replace` rather than a second `production()` call. Rebuilding the resolver
    # from scratch here discarded everything `paths` said about the *layout* and
    # kept only its project map, so a caller that handed in `ScopePaths.rooted()`
    # — the documented way to get a temporary layout, and the one `bootstrap`
    # above honours — had its registry written under the real `$HOME` anyway.
    # Caught in review on 2026-09-15; the tests missed it because they redirect
    # `HOME` instead of passing a resolver.
    #
    # Through the resolver rather than `_directory_name` directly: that helper is
    # private to `pm_ai.platform.paths`, and `scope_root` applies exactly the
    # same standard and raises the same `MalformedSubjectId`. Asked against a
    # resolver that knows this project, so an unusable *id* is what refuses here
    # rather than an unregistered one.
    known = replace(
        resolver, project_roots={**resolver.project_roots, project_id: repository}
    )
    scope = DataScope(ScopeKind.PROJECT, project_id)
    known.scope_root(scope)
    _assert_usable(repository)
    storage = _writer(
        known,
        now=lambda: datetime.now(timezone.utc),
        vcs=GitVcs(),
        crypto=_choose_crypto(keychain, encryption_disabled=encryption_off()),
    )
    with storage.exclusive(
        scope=DataScope(ScopeKind.APPLICATION), artifact=REGISTRY_ARTIFACT
    ):
        state = _artifact_state(storage, REGISTRY_ARTIFACT)
        if state.presence is Presence.UNREADABLE:
            # The registry exists and could not be opened. `parse_registry(None)`
            # means *absent*, so falling through would render a registry holding
            # this project alone and `os.replace` it over the one that could not
            # be read — and `projects.toml` is Tier 1, rebuildable from nothing,
            # so every other enrolled project would be gone with the command
            # reporting success. Measured doing exactly that in review on
            # 2026-09-15: two projects enrolled, the file chmod 000, and the
            # registry afterwards held only the third.
            raise ProjectPathUnusable(
                f"{known.project_registry} exists and could not be read: "
                f"{state.detail}. Nothing was onboarded. pm-ai will not write a "
                f"new registry over one it cannot read — that would forget every "
                f"project already enrolled, and nothing else records them. Fix "
                f"the file's permissions and run this again."
            )
        held = parse_registry(state.raw)
        claimed = {e.path: i for i, e in held.items()}
        registered_as = claimed.get(repository)
        if registered_as is not None and registered_as != project_id:
            # Two ids for one repository is not a second project: both resolve to
            # the same `<repo>/.project-ai`, so they would share one event log,
            # one meeting set and one dashboard while every `SourceRef` disagreed
            # about which project owned them — and `_compose` would refuse every
            # subcommand but `doctor` as an ambiguous registry, on a machine with
            # exactly one repository.
            raise DuplicateProject(
                f"{repository} is already onboarded as {registered_as!r}, so it "
                f"cannot also be onboarded as {project_id!r}. One directory is "
                f"one project: both ids would resolve to the same "
                f"{PROJECT_DIRNAME} tree and pm-ai would then have no way to "
                f"choose between them. To rename the project, edit its entry in "
                f"{known.project_registry}."
            )
        entry = held.get(project_id)
        if entry is not None and entry.path != repository:
            raise DuplicateProject(
                f"project {project_id!r} is already registered at {entry.path}, "
                f"and this would point it at {repository}. Artifacts have "
                f"already been written and referenced under the old path, so "
                f"re-pointing the id would leave its event log and meetings "
                f"invisible — that is a migration, not a registration. Onboard "
                f"the new path under a different alias."
            )
        already = entry is not None
        _create(repository)
        try:
            existing = storage.read_project_gitignore(project_id)
        except OSError as unreadable:
            # `read_project_gitignore` propagates every `OSError` but absence,
            # deliberately — "cannot open" must not read as "no rules yet". Here
            # it becomes a refusal rather than reaching `main`'s generic handler,
            # which exits 1 with a traceback. Exit 1 means "pm-ai broke" in the
            # one table that decides these, and this is an ordinary thing to be
            # wrong with a machine.
            raise ProjectPathUnusable(
                f"{repository / GITIGNORE_FILENAME} exists and could not be "
                f"read: {unreadable}. Nothing was onboarded. pm-ai appends its "
                f"rules to what is already there, so it will not write this "
                f"file without first reading it."
            ) from unreadable
        gitignore = storage.write_project_gitignore(
            project_id, render_gitignore(existing)
        )
        for node in PROJECT_TREE:
            if node.is_dir:
                known.resolve(scope, node.key, create=True)
        if not already:
            storage.write_artifact(
                scope=DataScope(ScopeKind.APPLICATION),
                artifact=REGISTRY_ARTIFACT,
                payload=render_registry(
                    {**held, project_id: ProjectEntry(path=repository, alias=alias)}
                ),
            )
    return Onboarded(project_id, repository, gitignore, already)


def _resolved(raw_path: str) -> Path:
    """The path as an absolute one, `~` expanded and the working directory applied.

    Only absolute is stored (the human's Q4): a relative path in `projects.toml`
    means a different directory to every process that reads it, and the daemon is
    not started from the shell the operator typed this in.

    Resolved rather than merely joined, so `..` and a symlinked parent settle
    here instead of in `_absolute_map`, which takes what it is given. `strict` is
    off because the directory legitimately may not exist yet — creating it is
    this command's job.
    """
    expanded = Path(raw_path).expanduser()
    return (expanded if expanded.is_absolute() else Path.cwd() / expanded).resolve()


def _assert_usable(repository: Path) -> None:
    """Refuse a path that cannot become a project directory, before anything is made."""
    if repository.exists() and not repository.is_dir():
        raise ProjectPathUnusable(
            f"{repository} is not a directory. A project is onboarded at a "
            f"directory — pm-ai creates one when it is missing, and will not "
            f"replace a file that is already there."
        )


def _create(repository: Path) -> None:
    """Make the project directory if it is absent, or say why that cannot happen."""
    try:
        repository.mkdir(parents=True, exist_ok=True)
    except OSError as refused:
        raise ProjectPathUnusable(
            f"{repository} could not be created: {refused}. Nothing was "
            f"onboarded — check the permissions on the parent directory."
        ) from refused
