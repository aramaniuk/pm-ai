"""Composition root (AD-30).

The only module that may import every layer. Core services receive their
dependencies from here; they never construct or locate them — which is exactly
why this module has to exist and why the pipeline below had no legal home
before it did.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pm_ai.connectors.gitlab import GitLabConnectorAdapter
from pm_ai.connectors.transcripts.graph import GraphTranscriptAdapter
from pm_ai.connectors.transcripts.manual import ManualTranscriptAdapter
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.ports import CryptoPort, KeychainPort, VcsPort
from pm_ai.platform.keychain import MacOSKeychainAdapter
from pm_ai.platform.paths import ScopePaths
from pm_ai.platform.vcs import GitVcs
from pm_ai.skills.gitlab import PostComment
from pm_ai.skills.registry import SkillRegistry
from pm_ai.storage.crypto import LazyKeyCrypto, PlaintextCrypto
from pm_ai.storage.service import StorageService


MASTER_KEY_NAME = "master"


@dataclass
class Daemon:
    storage: StorageService
    crypto: CryptoPort
    skills: SkillRegistry
    connectors: dict[str, GitLabConnectorAdapter]
    transcripts: dict[str, object]
    meetings: dict[str, object]
    scope: DataScope
    pm_handle: str = "andrei@example.com"


def build(
    root: Path | None,
    project: str,
    *,
    paths: ScopePaths | None = None,
    now: Callable[[], datetime] | None = None,
    vcs: VcsPort | None = None,
    keychain: KeychainPort | None = None,
    encryption_disabled: bool = False,
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
    storage = StorageService(resolver, now=clock, vcs=vcs or GitVcs())
    crypto = _build_crypto(
        keychain or MacOSKeychainAdapter(),
        storage=storage,
        scope=scope,
        encryption_disabled=encryption_disabled,
    )
    skills = SkillRegistry(storage, scope=scope)
    skills.register(PostComment())  # credentials would be injected here, from storage
    return Daemon(
        storage=storage,
        crypto=crypto,
        skills=skills,
        connectors={
            f"gitlab:{project}": GitLabConnectorAdapter(
                project=project, scope=scope, now=clock
            )
        },
        # AD-23 — both adapters wired from day one, so the pipeline is exercisable
        # without a live tenant.
        transcripts={"graph": GraphTranscriptAdapter(), "manual": ManualTranscriptAdapter()},
        meetings={},
        scope=scope,
    )


def _build_crypto(
    keychain: KeychainPort,
    *,
    storage: StorageService,
    scope: DataScope,
    encryption_disabled: bool,
) -> CryptoPort:
    """The cipher for the encrypted set, or the pass-through the debug flag asks for.

    The key is fetched *here* and nowhere else. `pm_ai.storage` may not import
    `pm_ai.platform`, so the single writer cannot reach a keychain — which is the
    property that keeps a key out of every module that merely writes files.

    `KeyNotFound` is deliberately **not** caught. A first run has no key and must
    mint one, and that is a decision with consequences — a new key makes every
    previously sealed artifact unreadable — so it belongs to whatever owns
    installation rather than to a constructor that would silently do it. Refusing
    here means the daemon says what is wrong instead of quietly writing a second
    key over a store it could still have opened.
    """
    if encryption_disabled:
        # Announced twice on purpose: the console reaches whoever is running it
        # now, the event log reaches whoever reads the record later and would
        # otherwise find plaintext with no explanation. Only this module knows the
        # flag exists, so only this module can say so.
        print(
            "WARNING: encryption is disabled by an explicit debug flag. "
            "Credentials and voice notes are being written in plaintext. This is "
            "never the default in a fresh installation.",
            file=sys.stderr,
        )
        storage.append_event_log(
            "- [security] encryption disabled by debug flag; the encrypted set is "
            "being written in plaintext",
            scope=scope,
        )
        return PlaintextCrypto()
    return LazyKeyCrypto(keychain, MASTER_KEY_NAME)
