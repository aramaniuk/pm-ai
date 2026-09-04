"""Composition root (AD-30).

The only module that may import every layer. Core services receive their
dependencies from here; they never construct or locate them — which is exactly
why this module has to exist and why the pipeline below had no legal home
before it did.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pm_ai.connectors.gitlab import GitLabConnectorAdapter
from pm_ai.connectors.registry import ConnectorRegistry, install as install_connectors
from pm_ai.connectors.transcripts.graph import GraphTranscriptAdapter
from pm_ai.connectors.transcripts.manual import ManualTranscriptAdapter
from pm_ai.core.config import Config
from pm_ai.domain.event_entries import DAEMON_ACTOR, EventEntry, SelfActionType
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.ports import (
    MASTER_KEY_NAME,
    CryptoPort,
    KeychainPort,
    VcsPort,
)
from pm_ai.platform.environment import encryption_disabled as encryption_off
from pm_ai.platform.keychain import MacOSKeychainAdapter
from pm_ai.platform.paths import ScopePaths
from pm_ai.platform.vcs import GitVcs
from pm_ai.skills.gitlab import PostComment
from pm_ai.skills.registry import SkillRegistry
from pm_ai.storage.crypto import LazyKeyCrypto, PlaintextCrypto
from pm_ai.storage.service import StorageService

__all__ = ["Daemon", "MASTER_KEY_NAME", "build"]


@dataclass
class Daemon:
    storage: StorageService
    crypto: CryptoPort
    skills: SkillRegistry
    connectors: dict[str, GitLabConnectorAdapter]
    transcripts: dict[str, object]
    meetings: dict[str, object]
    scope: DataScope
    # Custody of the master key, held rather than reconstructed. `pm_ai.surfaces`
    # may not import `keyring` (`.importlinter`'s `os-behind-platform`), so a CLI
    # asked to enrol a key has no legal way to build an adapter — it has to be
    # handed one, and this is the layer permitted to build it. Declared *before*
    # `config` because that field carries a default: a non-default field after a
    # defaulted one raises `TypeError` at class creation.
    keychain: KeychainPort
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
    itself: whether git would commit a raw capture. It defaults to the real `git`
    adapter and is overridable so a test can supply a verdict, because this is
    also the one module that may import both `pm_ai.storage` and
    `pm_ai.platform`. A writer built without it refuses every capture into a
    committed scope, which is the safe direction but not a useful one.
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
    connectors: dict[str, GitLabConnectorAdapter] = {
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
    for instance, enrolled in _enrolled_connectors(storage, scope=scope, clock=clock):
        connectors.setdefault(instance, enrolled)
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
        meetings={},
        scope=scope,
        keychain=custody,
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
    and until 2026-08-28 this wrote into the daemon's project scope, whose
    `event_log/` is committed: the fact that the operator ran with encryption
    off landed in the employer's repository, the exact misfiling-by-convenience
    the scope model exists to refuse (and the reason AD-38 homes the disclosure
    ledger the same way).
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


def _enrolled_connectors(
    storage: StorageService,
    *,
    scope: DataScope,
    clock: Callable[[], datetime],
) -> tuple[tuple[str, GitLabConnectorAdapter], ...]:
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

    Credentials are *not* read. They live in the sealed store, and constructing
    an adapter needs none — the harvest that needs one fetches it when it runs.
    """
    built: list[tuple[str, GitLabConnectorAdapter]] = []
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
        if system != "gitlab":
            # The only adapter that exists. An enrolled system pm-ai cannot
            # build is skipped rather than guessed at; 33a adds Graph.
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
        try:
            built.append(
                (instance, GitLabConnectorAdapter(project=project, scope=scope, now=clock))
            )
        except Exception:
            continue
    return tuple(built)


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
