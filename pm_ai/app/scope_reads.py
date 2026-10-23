"""What a scope's `meetings/` and `transcripts/` hold, read by path (stories 11f, 13a).

Shared by `transcript list` (`pm_ai.app.meeting_listing`) and the proposal
queue (`pm_ai.app.proposals`): one scope's record and capture names, listed
once; a record parsed once, when asked for; `config.toml`'s display zone.
Nothing here writes, and no master key, keychain or `StorageService` is used.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pm_ai.app.wiring import CONFIG_ARTIFACT
from pm_ai.core.config import load_config
from pm_ai.core.meeting_records import (
    MEETINGS,
    MeetingRecord,
    is_record_name,
    parse_record,
    record_digest_suffix,
)
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.storage_tiers import CAPTURES
from pm_ai.platform.paths import ScopePaths
from pm_ai.storage.service import is_collection_member

__all__ = ["Resolver", "ScopeView", "read_display_config", "read_or_none", "scope_view"]

APPLICATION = DataScope(ScopeKind.APPLICATION)

_DIGEST_TAIL = len(record_digest_suffix("x"))
"""`-<sha256>.md`: the tail of a record's name its id owns."""


class Resolver(Protocol):
    def resolve(self, scope: DataScope, artifact: str, *, create: bool = False) -> Path: ...


@dataclass
class ScopeView:
    """One scope's `meetings/` and `transcripts/` names, listed once."""

    scope: str
    meetings_dir: Path
    by_digest: dict[str, list[str]] = field(default_factory=dict)
    names: list[str] = field(default_factory=list)
    captures: list[str] = field(default_factory=list)
    parsed: dict[str, MeetingRecord | str] = field(default_factory=dict)

    def record_name(self, meeting_id: str) -> str | None:
        found = self.by_digest.get(record_digest_suffix(meeting_id))
        return found[0] if found else None

    def record(self, name: str) -> MeetingRecord | str:
        """The parsed record, or why it could not be read; parsed once."""
        if name not in self.parsed:
            path = self.meetings_dir / name
            try:
                self.parsed[name] = parse_record(path.read_text(encoding="utf-8"), source=name)
            except FileNotFoundError:
                self.parsed[name] = "its record file is gone"
            except (OSError, UnicodeDecodeError, ValueError) as unreadable:
                self.parsed[name] = f"its record could not be read: {unreadable}"
        return self.parsed[name]

    def has_capture(self, stem: str) -> bool:
        at = bisect.bisect_left(self.captures, stem)
        return at < len(self.captures) and self.captures[at].startswith(stem)


def scope_view(resolver: Resolver, scope: DataScope, *, root: Path | None) -> ScopeView | str:
    """The scope's names, or why its folder or records cannot be read."""
    if root is not None and not root.is_dir():
        return f"its folder {root} is missing or is not a folder"
    try:
        meetings = Path(resolver.resolve(scope, MEETINGS))
        captures = Path(resolver.resolve(scope, CAPTURES))
        view = ScopeView(str(scope), meetings)
        if meetings.is_dir():
            for name in sorted((entry.name for entry in meetings.iterdir()), reverse=True):
                if not is_collection_member(name) or not is_record_name(name):
                    continue
                view.names.append(name)
                view.by_digest.setdefault(name[-_DIGEST_TAIL:], []).append(name)
        if captures.is_dir():
            view.captures = sorted(
                entry.name for entry in captures.iterdir() if is_collection_member(entry.name)
            )
    except (OSError, ValueError) as unreadable:
        return f"its meetings could not be listed: {unreadable}"
    return view


def read_display_config(resolver: ScopePaths) -> tuple[str, bool, str]:
    """`display_timezone`, whether `local_model` is set, and why UTC is used if it is."""
    try:
        raw = read_or_none(resolver.resolve(APPLICATION, CONFIG_ARTIFACT))
        config = load_config(raw)
    except (OSError, ValueError):
        return "", False, "config.toml could not be read, so no display_timezone is used"
    return config.display_timezone, bool(config.local_model), ""


def read_or_none(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
