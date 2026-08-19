"""Composition root (AD-30).

The only module that may import every layer. Core services receive their
dependencies from here; they never construct or locate them — which is exactly
why this module has to exist and why the pipeline below had no legal home
before it did.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pm_ai.connectors.gitlab import GitLabConnector
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.skills.gitlab import PostComment
from pm_ai.skills.registry import SkillRegistry
from pm_ai.storage.service import StorageService


@dataclass
class Daemon:
    storage: StorageService
    skills: SkillRegistry
    connectors: dict[str, GitLabConnector]
    scope: DataScope


def build(root: Path, project: str, *, now: Callable[[], datetime] | None = None) -> Daemon:
    clock = now or (lambda: datetime.now(timezone.utc))
    scope = DataScope(ScopeKind.PROJECT, project)
    storage = StorageService(root)
    skills = SkillRegistry(storage, scope=scope)
    skills.register(PostComment())  # credentials would be injected here, from storage
    return Daemon(
        storage=storage,
        skills=skills,
        connectors={f"gitlab:{project}": GitLabConnector(project=project, scope=scope, now=clock)},
        scope=scope,
    )
