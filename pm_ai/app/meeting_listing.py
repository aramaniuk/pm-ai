"""`pm-ai transcript list`'s reads, by path, for the terminal, the daemon and the drop (11f).

Nothing here decides a state or says a word: `pm_ai.core.meeting_listing`
does both. This reads — `projects.toml` and `config.toml` by their paths, the
operational store's transcript, parts and summary rows in one read-only
transaction, each scope's `meetings/` and `transcripts/` names — and hands
the decision what it found. No master key, keychain or model is asked for,
nothing is written, and no `StorageService` is built: meeting records and
`operational.db` are not encrypted.

**Reading no more than it shows.** The rows are read first. A meeting's
record is found by its id's digest at the end of the file name, without
parsing it; the UTC day at the start of the name orders by day. Only the
records shown are parsed, and the others on the oldest day shown, to order
within that day. `--state summarized` and `--state not-summarized` turn on
whether the record holds a summary, so under those two the candidates whose
row cannot say are parsed too.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from pm_ai.core.config import load_config
from pm_ai.core.meeting_listing import (
    LISTED_TRANSCRIPT_STATES,
    summary_state_word,
    decide,
    passes,
    row_passes,
    title_matches,
    day_in,
)
from pm_ai.core.meeting_records import (
    MEETINGS,
    MeetingRecord,
    capture_kept,
    is_record_name,
    parse_record,
    record_digest_suffix,
)
from pm_ai.core.project_registry import parse_registry
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.app.wiring import CONFIG_ARTIFACT, REGISTRY_ARTIFACT
from pm_ai.domain.meeting_listing import (
    PEOPLE_REFUSAL,
    ListedMeeting,
    Listing,
    ListingRefused,
    RecordReading,
    Unreadable,
)
from pm_ai.domain.storage_tiers import CAPTURES, OPERATIONAL_DB
from pm_ai.platform.paths import ScopePaths
from pm_ai.app.scope_reads import (
    Resolver as _Resolver,
    ScopeView as _ScopeView,
    read_display_config as _config,
    read_or_none as _read_or_none,
    scope_view as _view,
)
from pm_ai.storage.service import (
    MeetingRows,
    MeetingRowsUnreadable,
    is_collection_member,
    meeting_rows_at,
)
from pm_ai.app.proposal_reads import waiting_by_meeting

__all__ = [
    "MeetingLister",
    "listed_after_drop",
    "resolve_title",
]

CONFIG_FILE = CONFIG_ARTIFACT
REGISTRY_FILE = REGISTRY_ARTIFACT
OPERATIONAL_SOURCE = "operational.db"

APPLICATION = DataScope(ScopeKind.APPLICATION)
PERSONAL = DataScope(ScopeKind.PERSONAL)

_LISTABLE = frozenset({ScopeKind.PERSONAL, ScopeKind.PROJECT})



@dataclass(frozen=True, slots=True)
class _Candidate:
    scope: str
    meeting_id: str | None
    name: str | None
    """The record's file name; `None` when no record carries the id's digest."""


@dataclass
class _Reading:
    """Everything one call read, handed to the decision."""

    rows: MeetingRows | None
    zone: str
    local_model: bool
    reachable: bool
    now: datetime
    zone_note: str = ""
    waiting: Mapping[tuple[str, str], int] = field(default_factory=dict)
    """Story 13a: proposals waiting for review, per `(scope, meeting_id)`."""

    def decide(self, view: _ScopeView, candidate: _Candidate) -> ListedMeeting:
        scope, name = candidate.scope, candidate.name
        if name is None:
            return decide(
                scope=scope,
                meeting_id=candidate.meeting_id or "",
                record=RecordReading.MISSING,
                record_reason="no record of this meeting is on file",
                **self._rows_of(scope, candidate.meeting_id or "", None, view),
            )
        found = view.record(name)
        if isinstance(found, str):
            return decide(
                scope=scope,
                meeting_id=candidate.meeting_id or name,
                record=RecordReading.UNREADABLE,
                record_reason=found,
                **self._rows_of(scope, candidate.meeting_id or "", name[:-3], view),
            )
        meeting_id = found.meeting.meeting_id
        return decide(
            scope=scope,
            meeting_id=meeting_id,
            record=RecordReading.READABLE,
            meeting=found.meeting,
            held=found.summary,
            **self._rows_of(scope, meeting_id, name[:-3], view),
        )

    def _rows_of(
        self, scope: str, meeting_id: str, stem: str | None, view: _ScopeView
    ) -> dict[str, Any]:
        rows = self.rows
        key = (scope, meeting_id)
        return {
            "stem": stem,
            "store_readable": rows is not None,
            "transcript": None if rows is None else rows.transcripts.get(key),
            "parts": () if rows is None else rows.parts.get(key, ()),
            "captures": view.captures,
            "summary": None if rows is None else rows.summaries.get(key),
            "local_model": self.local_model,
            "daemon_reachable": self.reachable,
            "now": self.now,
            "waiting": self.waiting.get(key, 0),
        }

    def pre_filter(self, state: str, view: _ScopeView, candidate: _Candidate) -> bool | None:
        """`--state` from the rows and names alone; `None` when only the record can say."""
        if self.rows is None:
            return state == "all"
        key = (candidate.scope, candidate.meeting_id or "")
        row = self.rows.transcripts.get(key)
        summary = self.rows.summaries.get(key)
        transcript_state = "none" if row is None else row.state
        kept = None
        if candidate.name is not None and transcript_state == "ingested":
            kept = capture_kept(candidate.name[:-3], self.rows.parts.get(key, ()), view.captures)
        summary_state = "none" if summary is None else summary_state_word(summary.state)
        return row_passes(
            state,
            transcript_state=transcript_state,
            kept=kept,
            summary_state=summary_state,
            held=None,
        )


@dataclass
class MeetingLister:
    """`transcript list`'s reader, built from paths (story 11f).

    `paths` is the resolver whose application and personal trees are read —
    the daemon's own in the daemon, the process owner's home when `None`; the
    projects are always those `projects.toml` enrols now, read at each call.
    `reachable` answers whether a daemon answers on the loopback port; `now` is
    the composition root's clock.
    """

    reachable: Callable[[], bool]
    now: Callable[[], datetime]
    paths: ScopePaths | None = None

    def _resolver(self, projects: Mapping[str, Path]) -> ScopePaths:
        if self.paths is None:
            return ScopePaths.production(projects=projects)
        return replace(self.paths, project_roots=dict(projects))

    def list(self, *, scope: DataScope | None, state: str, limit: int | None) -> Listing:
        """The listing for `scope`, or every listable scope when `None`.

        `ListingRefused` for `people:` (before anything is read) and for a
        project `projects.toml` does not enrol.
        """
        if scope is not None and scope.kind not in _LISTABLE:
            raise ListingRefused(PEOPLE_REFUSAL.format(scope=scope))
        base = self._resolver({})
        unreadable: list[Unreadable] = []
        registry: Mapping[str, Any] = {}
        registry_fault: str | None = None
        try:
            raw = _read_or_none(base.resolve(APPLICATION, REGISTRY_FILE))
            registry = parse_registry(raw)
        except (OSError, ValueError) as refused:
            registry_fault = str(refused) or type(refused).__name__
        if scope is not None and scope.kind is ScopeKind.PROJECT:
            if registry_fault is not None:
                unreadable.append(Unreadable(REGISTRY_FILE, registry_fault))
                scopes: list[DataScope] = []
                named: tuple[str, ...] = (str(scope),)
            elif scope.project_id not in registry:
                raise ListingRefused(
                    f"{scope} is not a project projects.toml enrols, so it has no meetings "
                    f"to list. `pm-ai project add <path> {scope.project_id}` enrols it."
                )
            else:
                scopes = [scope]
                named = (str(scope),)
        elif scope is not None:
            scopes, named = [scope], (str(scope),)
        else:
            if registry_fault is not None:
                unreadable.append(Unreadable(REGISTRY_FILE, registry_fault))
            scopes = [PERSONAL, *(DataScope(ScopeKind.PROJECT, pid) for pid in registry)]
            named = tuple(str(each) for each in scopes)
        resolver = self._resolver(
            {pid: Path(entry.path) for pid, entry in registry.items()}
        )
        reading, store_fault = self._reading(resolver)
        if store_fault is not None:
            unreadable.append(Unreadable(OPERATIONAL_SOURCE, store_fault))
        views: dict[str, _ScopeView] = {}
        for each in scopes:
            root = None
            if each.kind is ScopeKind.PROJECT:
                root = Path(registry[str(each.project_id)].path)
            view = _view(resolver, each, root=root)
            if isinstance(view, str):
                unreadable.append(Unreadable(str(each), view))
            else:
                views[str(each)] = view
        zone_note = "" if reading.zone else reading.zone_note
        if reading.rows is None and state != "all":
            return Listing(
                zone=reading.zone, scopes=named, daemon_reachable=reading.reachable,
                state=state, limit=limit or 0, unreadable=tuple(unreadable),
                store_readable=False, zone_note=zone_note,
            )
        shown, more = _select(reading, views, state=state, limit=limit)
        return Listing(
            zone=reading.zone,
            scopes=named,
            daemon_reachable=reading.reachable,
            state=state,
            limit=limit or 0,
            meetings=tuple(shown),
            unreadable=tuple(unreadable),
            more=more,
            store_readable=reading.rows is not None,
            zone_note=zone_note,
        )

    def _reading(self, resolver: ScopePaths) -> tuple[_Reading, str | None]:
        zone, local, note = _config(resolver)
        store = Path(resolver.resolve(APPLICATION, OPERATIONAL_DB))
        try:
            rows: MeetingRows | None = meeting_rows_at(store)
            fault = None
        except MeetingRowsUnreadable as unreadable:
            rows, fault = None, str(unreadable)
        try:
            reachable = bool(self.reachable())
        except Exception:  # noqa: BLE001 — unknown reads as not reachable
            reachable = False
        now = self.now()
        return (
            _Reading(rows, zone, local, reachable, now, note, waiting_by_meeting(resolver, now=now)),
            fault,
        )

    def summarizable(self, scope: str) -> tuple[tuple[str, datetime, str, bool], ...]:
        """Completion's `summarize --meeting`: ingested and kept, newest first (story 11f).

        Each with whether the record holds a summary, so completion can say
        that summarizing it again needs `--again`.
        """
        parsed = DataScope.parse(scope)
        listing = self.list(scope=parsed, state="all", limit=None)
        return tuple(
            (listed.meeting_id, listed.start, listed.title or "", bool(listed.summary.held))
            for listed in listing.meetings
            if listed.start is not None
            and listed.transcript.state == "ingested"
            and listed.transcript.kept
        )


def _candidates(reading: _Reading, views: Mapping[str, _ScopeView]) -> list[_Candidate]:
    """Each (scope, meeting id) once: from the rows, or with no store from capture names."""
    found: list[_Candidate] = []
    rows = reading.rows
    if rows is None:
        for scope, view in views.items():
            for name in view.names:
                if view.has_capture(name[:-3]):
                    found.append(_Candidate(scope, None, name))
        return found
    keys = {
        key
        for key, row in rows.transcripts.items()
        if row.state in LISTED_TRANSCRIPT_STATES
    } | set(rows.summaries)
    for scope, meeting_id in sorted(keys):
        holder = views.get(scope)
        if holder is None:
            continue
        found.append(_Candidate(scope, meeting_id, holder.record_name(meeting_id)))
    return found


def _select(
    reading: _Reading,
    views: Mapping[str, _ScopeView],
    *,
    state: str,
    limit: int | None,
) -> tuple[list[ListedMeeting], int]:
    """The meetings shown, newest first, how many more match, and every record not read.

    A candidate with no record file, or whose record cannot be parsed, goes to
    the tail after the dated meetings and is never counted in `more`. The
    shown window is parsed day by day; the candidates beyond it are parsed too,
    so an unreadable one among them reaches the tail rather than `more`.
    """
    dated: list[tuple[str, _Candidate]] = []
    tail: list[ListedMeeting] = []
    for candidate in _candidates(reading, views):
        view = views[candidate.scope]
        verdict = reading.pre_filter(state, view, candidate)
        if verdict is False:
            continue
        if candidate.name is None:
            tail.append(reading.decide(view, candidate))
            continue
        if verdict is None:
            # Only the record says whether it holds a summary.
            listed = reading.decide(view, candidate)
            if listed.record is not RecordReading.READABLE:
                tail.append(listed)
                continue
            if not passes(listed, state):
                continue
        dated.append((candidate.name[:10], candidate))
    dated.sort(key=lambda entry: entry[0], reverse=True)
    window: list[ListedMeeting] = []
    beyond = 0
    index = 0
    while index < len(dated):
        day = dated[index][0]
        filled = limit is not None and len(window) >= limit
        while index < len(dated) and dated[index][0] == day:
            candidate = dated[index][1]
            listed = reading.decide(views[candidate.scope], candidate)
            index += 1
            if listed.record is not RecordReading.READABLE:
                tail.append(listed)
            elif filled:
                beyond += 1
            else:
                window.append(listed)
    window.sort(
        key=lambda listed: (
            -(listed.start or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
            listed.scope,
            listed.meeting_id,
        )
    )
    shown = window if limit is None else window[:limit]
    readable_left_out = beyond + (len(window) - len(shown))
    tail.sort(key=lambda listed: (listed.scope, listed.meeting_id))
    return [*shown, *tail], readable_left_out


def resolve_title(
    lister: MeetingLister, *, scope: DataScope, title: str, on: date | None
) -> tuple[str, tuple[ListedMeeting, ...]]:
    """The listing's zone, and the meetings in `scope` `--title` (and `--on`) match.

    Among those the listing lists there; `--on`'s day is read in the zone.
    """
    listing = lister.list(scope=scope, state="all", limit=None)
    return listing.zone, tuple(
        listed
        for listed in listing.meetings
        if listed.record is RecordReading.READABLE
        and listed.title is not None
        and title_matches(listed.title, title)
        and (on is None or (listed.start is not None and day_in(listed.start, listing.zone) == on))
    )


def listed_after_drop(
    *,
    resolver: _Resolver,
    scope: DataScope,
    meeting_id: str,
    zone: str,
    local_model: bool,
    reachable: bool,
    now: datetime,
) -> ListedMeeting | None:
    """One meeting as the listing would show it, read after a drop commits (story 11f).

    The same reads and the same decision as the listing, over the daemon's
    own resolver; `None` when anything about it cannot be read, so the drop's
    closing line goes without it rather than failing a drop that succeeded.
    """
    try:
        store = Path(resolver.resolve(APPLICATION, OPERATIONAL_DB))
        try:
            rows: MeetingRows | None = meeting_rows_at(store)
        except MeetingRowsUnreadable:
            rows = None
        view = _view(resolver, scope, root=None)
        if isinstance(view, str):
            return None
        waiting = (
            waiting_by_meeting(resolver, now=now) if isinstance(resolver, ScopePaths) else {}
        )
        reading = _Reading(rows, zone, local_model, reachable, now, waiting=waiting)
        return reading.decide(
            view, _Candidate(str(scope), meeting_id, view.record_name(meeting_id))
        )
    except Exception:  # noqa: BLE001 — the drop succeeded; its line goes without this
        return None
