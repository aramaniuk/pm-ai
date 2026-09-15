"""Composition root (AD-30).

The only module that may import every layer. Core services receive their
dependencies from here; they never construct or locate them — which is exactly
why this module has to exist and why the pipeline below had no legal home
before it did.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pm_ai.connectors.gitlab import GitLabConnectorAdapter
from pm_ai.connectors.graph import (
    CLIENT_ID_KEY,
    TENANT_KEY,
    GraphConnector,
    MissingGraphSetting,
    UnknownProject,
    graph_category_scopes,
    graph_window_policy,
)
from pm_ai.connectors.graph.auth import GraphDeviceCodeAuth, InMemoryRefreshTokenStore
from pm_ai.connectors.graph.calendar import GraphCalendarFetch
from pm_ai.connectors.graph.client import GraphClient
from pm_ai.connectors.registry import ConnectorRegistry, install as install_connectors
from pm_ai.connectors.transcripts.graph import GraphTranscriptAdapter
from pm_ai.connectors.transcripts.manual import ManualTranscriptAdapter
from pm_ai.core.config import Config
from pm_ai.core.project_registry import (
    DuplicateProject,
    ProjectEntry,
    ProjectPathUnusable,
    RegistryRefused,
    parse_registry,
    render_registry,
)
from pm_ai.core.project_scaffold import render_gitignore
from pm_ai.core.connector_enrolment import stored_credentials
from pm_ai.core.meeting_records import MeetingRecords
from pm_ai.domain.event_entries import DAEMON_ACTOR, EventEntry, SelfActionType
from pm_ai.domain.health import ArtifactState
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.scope_model import PROJECT_TREE
from pm_ai.ports import (
    MASTER_KEY_NAME,
    ConnectorPort,
    CryptoPort,
    KeychainPort,
    VcsPort,
)
from pm_ai.platform.environment import encryption_disabled as encryption_off
from pm_ai.platform.claims import exclusive
from pm_ai.platform.keychain import MacOSKeychainAdapter
from pm_ai.platform.paths import ScopePaths
from pm_ai.platform.vcs import GitVcs
from pm_ai.skills.gitlab import PostComment
from pm_ai.skills.registry import SkillRegistry
from pm_ai.storage.crypto import LazyKeyCrypto, PlaintextCrypto
from pm_ai.storage.service import StorageService

__all__ = [
    "Bootstrap",
    "CONFIG_ARTIFACT",
    "Daemon",
    "MASTER_KEY_NAME",
    "REGISTRY_ARTIFACT",
    "bootstrap",
    "build",
    "onboard_project",
    "Onboarded",
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
    # the four methods `ConnectorPort` declares.
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
    # Custody of the master key, held rather than reconstructed. `pm_ai.surfaces`
    # may not import `keyring` (`.importlinter`'s `os-behind-platform`), so a CLI
    # asked to enrol a key has no legal way to build an adapter — it has to be
    # handed one, and this is the layer permitted to build it. Declared *before*
    # `config` because that field carries a default: a non-default field after a
    # defaulted one raises `TypeError` at class creation.
    keychain: KeychainPort
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

    @property
    def pm_handle(self) -> str:
        """Who the daemon treats as the PM, per `config.toml`.

        A property rather than a field, so `Config` stays the single place the
        value lives. It was a literal default here — one developer's own email
        address, compiled into the package — until `pm_ai.core.config` existed
        to be asked. Unset by default now, and an unset handle matches no
        speaker, so nothing spoken auto-executes (AD-32).
        """
        return self.config.pm_handle


def build(
    root: Path | None,
    project: str,
    *,
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
    scope = DataScope(ScopeKind.PROJECT, project)
    # Eagerly, because every refusal below is about this scope and nothing else
    # resolves it until the first Tier-1 write: an id that cannot be a directory
    # name, or a project no registry knows, would otherwise surface mid-harvest
    # with a batch already in hand.
    resolver.scope_root(scope)
    # The cipher is chosen before storage, because storage performs every
    # encrypted read and write and therefore holds it. The *announcement* of a
    # disabled cipher needs storage, so it happens after — splitting the two is
    # what keeps this acyclic.
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
    storage = StorageService(resolver, now=clock, vcs=vcs or GitVcs(), crypto=crypto)
    if disabled:
        _announce_disabled_encryption(storage)
    skills = SkillRegistry(storage, scope=scope)
    skills.register(PostComment())  # credentials would be injected here, from storage
    connectors: dict[str, ConnectorPort] = {
        f"gitlab:{project}": GitLabConnectorAdapter(project=project, scope=scope, now=clock)
    }
    # The daemon holds the instances; `pm_ai.connectors.registry` enumerates
    # them. Two structures rather than one because the architecture gates and
    # `pm-ai connector check` have to ask "for every connector, ..." from
    # outside, and this dict is unreachable from anywhere but here. Registered
    # under the same key, so a cursor, a coverage window and a probe row all
    # name one instance. `install` replaces, so building a second daemon in one
    # process describes that daemon rather than accumulating both.
    # Enrolled connectors join the daemon's own dict *before* the registry is
    # built from it. Registering them only into the registry left the two
    # structures disagreeing — `pm-ai connector check` listed an instance that
    # `run_harvest` raised `KeyError` for — which is the divergence the comment
    # above says cannot happen and `test_composition_populates_the_registry`
    # asserts cannot.
    # The enrolled adapter *replaces* the built-in of the same name, and the
    # collision is the ordinary case rather than a corner: the built-in above is
    # keyed `gitlab:<project>`, and `gitlab.py`'s own ABSENT remediation tells the
    # operator to run `pm-ai connector add gitlab gitlab:<project>` — the exact
    # instance name that produces one. `setdefault` kept the credential-less
    # built-in and dropped the adapter holding the sealed credential, so the
    # connector the operator had just enrolled went on reporting ABSENT and the
    # remedy printed was the one they had already followed.
    for instance, enrolled in _enrolled_connectors(
        storage, scope=scope, clock=clock, registered=_registrar(resolver)
    ):
        connectors[instance] = enrolled
    enumerable = ConnectorRegistry()
    for instance, connector in connectors.items():
        enumerable.register(connector, instance=instance)
    install_connectors(enumerable)
    return Daemon(
        storage=storage,
        crypto=crypto,
        skills=skills,
        connectors=connectors,
        # AD-23 — both adapters wired from day one, so the pipeline is exercisable
        # without a live tenant.
        transcripts={"graph": GraphTranscriptAdapter(), "manual": ManualTranscriptAdapter()},
        meetings=MeetingRecords(storage),
        scope=scope,
        keychain=custody,
        # The same callable the single writer stamps from, so nothing downstream
        # has to build a second one.
        clock=clock,
        config=config if config is not None else Config(),
    )


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
        return PlaintextCrypto()
    return LazyKeyCrypto(keychain, MASTER_KEY_NAME)


def _announce_disabled_encryption(storage: StorageService) -> None:
    """Say so twice, because the two audiences are different.

    The console reaches whoever is running the daemon now. The event log reaches
    whoever reads the record later and would otherwise find plaintext credentials
    with no explanation — and a console warning is gone the moment the terminal
    scrolls. Only the composition root knows the flag exists, so only it can say.

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
    print(
        "WARNING: encryption is disabled by an explicit debug flag. "
        "Credentials and voice notes are being written in plaintext. This is "
        "never the default in a fresh installation.",
        file=sys.stderr,
    )
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
    scope: DataScope,
    clock: Callable[[], datetime],
    registered: Callable[[str], bool],
) -> tuple[tuple[str, ConnectorPort], ...]:
    """What `pm-ai connector add` wrote, as adapters, so enrolment survives a restart.

    Story 8b's success message tells the operator the connector becomes active
    at the next start. Nothing read `connectors/` until this function existed,
    so that sentence was false: an enrolment wrote two files and no later run
    looked at either. Registration stays construction-time per AD-9 and story
    8d — this is the start that "the next start" refers to.

    Returned rather than registered, so the caller can put these in
    `Daemon.connectors` *and* the registry. Registering them into the registry
    alone made `pm-ai connector check` list an instance `run_harvest` could not
    resolve.

    Failures are swallowed deliberately, and only here: an unreadable or
    malformed entry must not stop a daemon composing, because `doctor` is the
    command that diagnoses exactly that and it cannot run if `build()` raises.
    A connector that fails to load is simply absent, which `connector check`
    reports as a missing row.

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
    """
    # Fetched on first need, not up front. Opening the sealed store costs a
    # master-key fetch from the keychain, and a machine with no connectors
    # enrolled — every machine before the first `connector add` — paid it on
    # every `build()` to read a mapping nothing then consulted.
    credentials: dict[str, dict[str, str]] | None = None
    built: list[tuple[str, ConnectorPort]] = []
    for entry in _enrolled_configurations(storage):
        instance = entry.get("instance")
        system = entry.get("system")
        if not isinstance(instance, str) or not isinstance(system, str) or not instance:
            continue
        # Anything but an explicit `true` is off. The file is plaintext and
        # hand-editable on purpose, so `"false"`, `0` and `null` are all things
        # an operator will actually write meaning "not this one".
        if entry.get("enabled") is not True:
            continue
        if system not in ("gitlab", "graph"):
            # An enrolled system pm-ai has no adapter for is skipped rather than
            # guessed at. Two families exist: `gitlab` below, and `graph` since
            # story 33c, which is the connector `33a`'s token and `33b`'s
            # calendar fetch were built for.
            continue
        if system == "graph":
            if credentials is None:
                credentials = _stored_credentials(storage)
            graph = _graph_connector(
                entry,
                instance=instance,
                credential=_credential_for(credentials.get(instance), system=system),
                clock=clock,
                registered=registered,
            )
            if graph is not None:
                built.append((instance, graph))
            continue
        # The connector's own declared project, not one re-derived from its
        # name. The instance is a path component and may not contain `/`, while
        # a real GitLab project is `group/project` — deriving one from the other
        # built an adapter for the wrong path and said nothing. The fallback is
        # for entries written before 8b recorded it.
        declared = entry.get("project")
        project = (
            declared
            if isinstance(declared, str) and declared
            else (instance.split(":", 1)[1] if ":" in instance else instance)
        )
        if not project:
            continue
        # The credential the sealed store holds for *this* instance, or `None`
        # when there is none. `None`, absent and blank are one state to the
        # adapter's health probe — no usable credential — so a half-finished
        # enrolment still reports ABSENT rather than claiming to be configured.
        if credentials is None:
            credentials = _stored_credentials(storage)
        held = _credential_for(credentials.get(instance), system=system)
        try:
            built.append(
                (
                    instance,
                    GitLabConnectorAdapter(
                        project=project, scope=scope, now=clock, credential=held
                    ),
                )
            )
        except Exception:
            continue
    return tuple(built)


def _graph_connector(
    entry: Mapping[str, object],
    *,
    instance: str,
    credential: str | None,
    clock: Callable[[], datetime],
    registered: Callable[[str], bool],
) -> GraphConnector | None:
    """One Graph enrolment row as a connector, or `None` with the reason said out loud.

    **Five settings come off the row and four of them are undefaulted**: the
    Entra `client_id` this laptop signs in through, the two harvest widths, and
    the Outlook-category-to-project mapping — which defaults to empty, and that
    absence is safe because it means every meeting is personal, exactly what an
    untagged row gets anyway. The fifth is `tenant`, which *is* defaulted, to
    `GraphDeviceCodeAuth`'s own value rather than to a copy of it. This docstring
    said "four settings and none of them is defaulted" while the code beside it
    defaulted `tenant` to a literal, which is two claims one file apart that
    could not both be true.

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

    Returns `None` rather than raising, because `build()` must compose on a
    misconfigured machine — that is the whole reason the diagnostics can run at
    all.
    """
    client_id = entry.get(CLIENT_ID_KEY)
    if not isinstance(client_id, str) or not client_id.strip():
        _unbuilt(
            instance,
            f"it carries no usable {CLIENT_ID_KEY!r}. That is the Entra "
            f"application the PM consents to, and pm-ai does not supply one: "
            f"inventing it would be pm-ai choosing whose app asks for their "
            f"calendar.",
        )
        return None
    try:
        policy = graph_window_policy(entry)
        categories = graph_category_scopes(entry, registered=registered)
    except (MissingGraphSetting, UnknownProject, ValueError) as refused:
        # The refusals this row is *expected* to produce, each of which composed
        # a sentence naming what to change. `ValueError` is
        # `WindowPolicy.__post_init__`'s: CAP-2's 240-minute floor, and a
        # reach-back shorter than the width every later run covers anyway.
        _unbuilt(instance, str(refused))
        return None
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
        _unbuilt(
            instance,
            f"reading it raised {type(unexpected).__name__}: {unexpected}. That "
            f"is a row pm-ai could not classify rather than a setting it can "
            f"name — report it.",
        )
        return None

    tenant = entry.get(TENANT_KEY)
    named_tenant = tenant.strip() if isinstance(tenant, str) and tenant.strip() else None
    auth = GraphDeviceCodeAuth(
        client_id=client_id.strip(),
        # The fifth setting, and the one that *is* defaulted — to the adapter's
        # own value, read off the field rather than copied. A literal here would
        # be a second place `organizations` is decided, and the two would agree
        # until the day the adapter's changed. `organizations` is any work or
        # school tenant; the row names one when this PM's Entra application is
        # single-tenant.
        tenant=named_tenant if named_tenant is not None else GraphDeviceCodeAuth.tenant,
        # Custody, and the honest limit of this slice. The sealed store is read
        # at composition and the adapter is handed what it held; a refresh token
        # the provider rotates mid-run is kept for the life of this process and
        # not written back, so the next start signs in from the enrolled one
        # again. Writing back needs an update path through `8b`'s sealed store
        # that does not exist, and a store that silently lost the rotation would
        # be worse than one that visibly never had it.
        store=InMemoryRefreshTokenStore(credential=credential),
        instance=instance,
        now=clock,
    )
    return GraphConnector(
        auth=auth,
        calendar=GraphCalendarFetch(
            client=GraphClient(
                auth=auth,
                now=clock,
                # The real one, supplied here because `GraphClient` defaults to a
                # wait that does not wait — a blocking sleep as the default is
                # what makes a forgotten injection cost ten minutes inside a test
                # suite. Its 429 branch compares this against that default by
                # identity and refuses rather than spinning, so the composition
                # root is the only place the honoured hint can come from.
                wait=time.sleep,
            ),
            instance=instance,
            windows=policy,
            now=clock,
        ),
        categories=categories,
        now=clock,
    )


def _unbuilt(instance: str, reason: str) -> None:
    """Say why a connector this machine is configured for was not constructed.

    stderr, and nothing else: the daemon has not composed yet, so there is no
    event log to write into and no storage to reach that has not already been
    the thing that failed.
    """
    print(
        f"WARNING: the Graph connector {instance!r} was not built — {reason}",
        file=sys.stderr,
    )


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
    if not isinstance(sealed, Mapping):
        return None
    recorded = sealed.get("system")
    if isinstance(recorded, str) and recorded and recorded != system:
        return None
    credential = sealed.get("credential")
    return credential if isinstance(credential, str) else None


def _stored_credentials(storage: StorageService) -> dict[str, dict[str, str]]:
    """The sealed store's credentials, or nothing when it cannot be opened.

    Swallowed for the reason every failure in `_enrolled_connectors` is: the
    daemon must compose on a broken machine so `pm-ai doctor` can run and say
    what is broken. The sealed store needs the master key, so a keyless or
    locked machine raises here — and a `build()` that raised would take the
    diagnostics down with it.

    The cost is stated rather than hidden: a connector whose credential could
    not be *read* reports `ABSENT`, the same as one that has none. The keychain
    probe is what tells those two apart, and it reports the cause directly
    instead of through a connector row.
    """
    try:
        return stored_credentials(storage)
    except Exception:
        return {}


def _enrolled_configurations(
    storage: StorageService,
) -> tuple[Mapping[str, object], ...]:
    """Every readable `connectors/<name>.json`, as decoded mappings."""
    application = DataScope(ScopeKind.APPLICATION)
    try:
        names = storage.list_collection(scope=application, artifact="connectors/")
    except Exception:
        return ()
    entries: list[Mapping[str, object]] = []
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
            entries.append(decoded)
    return tuple(entries)


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
    resolver = paths if paths is not None else ScopePaths.production()
    storage = StorageService(
        resolver,
        now=lambda: datetime.now(timezone.utc),
        vcs=GitVcs(),
        crypto=_choose_crypto(keychain, encryption_disabled=encryption_off()),
    )
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
    present afterwards" true rather than usually true.

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
    # Through the resolver rather than `_directory_name` directly: that helper is
    # private to `pm_ai.platform.paths`, and `scope_root` applies exactly the
    # same standard and raises the same `MalformedSubjectId`. Asked against a
    # resolver that knows this project, so an unusable *id* is what refuses here
    # rather than an unregistered one.
    known = ScopePaths.production(
        home=None, projects={**_registered_paths(resolver), project_id: repository}
    )
    scope = DataScope(ScopeKind.PROJECT, project_id)
    known.scope_root(scope)
    _assert_usable(repository)
    storage = StorageService(
        known,
        now=lambda: datetime.now(timezone.utc),
        vcs=GitVcs(),
        crypto=_choose_crypto(keychain, encryption_disabled=encryption_off()),
    )
    with exclusive(known.project_registry):
        held = parse_registry(
            _artifact_state(storage, REGISTRY_ARTIFACT).raw
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
        gitignore = storage.write_project_gitignore(
            project_id, render_gitignore(storage.read_project_gitignore(project_id))
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


def _registered_paths(resolver: ScopePaths) -> Mapping[str, Path]:
    """What the resolver already knows, so composing a wider one loses nothing."""
    return dict(resolver.project_roots)


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
