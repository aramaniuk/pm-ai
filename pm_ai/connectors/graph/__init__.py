"""Microsoft Graph — the delegated auth this connector family runs on, and the wire.

Four modules now, in the order they were built and in the order they depend:

- `auth` (story 33a) — the device-code flow, the five refusals, and the silent
  refresh. Nothing here can ask Graph anything without it.
- `client` (story 33b) — one authenticated read-only `GET`, followed as far as
  the provider's own `@odata.nextLink` chain goes, under a page cap, a
  seen-link set, an origin check and a wall-clock budget.
- `calendar` (story 33b) — `calendarView` over a bounded window, returning rows
  whose instants are aware UTC and the coverage the fetch actually earned.
- this module (story 33c) — `GraphConnector`, the `ConnectorPort` that turns
  those rows into `Meeting` records and `CALENDAR_EVENT_HELD` events.
- `channels` (story 33d) — the Teams channels the PM listed by hand, walked
  newest first; this module turns each message and reply into one
  `MESSAGE_POSTED` event in the channel's project log.
- `chats` (story 33f) — the Teams chats the PM listed by hand, each with the
  scope its messages go to, read from a server-side position; each message
  becomes one `MESSAGE_POSTED` event through the channel mapping, in the
  chat's listed scope.

Transcripts are 33e.

## The one modelling decision, and why it could not be deferred

**A meeting that has already ended becomes a record and an event. A meeting that
has not becomes neither.** It is mapped to an in-memory `Meeting` and handed
back, and nothing is written.

Both halves are forced rather than chosen. The past half is forced by AD-27's
closed enumeration: `MeetingHeldPayload` carries a `meeting_id`, an attendee
*count* and a duration, and has no field for a title or a start — so an upcoming
meeting has no honest representation as an event, and a connector may not open
the enumeration to give it one. The future half was decided on 2026-09-07: the
calendar is the source of truth for a meeting that has not happened, and a local
copy of a row that can be moved or cancelled outside pm-ai cannot be kept
accurate. Persisting it would buy duplication plus the obligation to model
staleness, which is a cancellation-marking rewrite this connector would then owe
on every cycle.

That is what makes `meetings/` the dashboard's Time-Critical source rather than
`event_log/`, and why a surface asking for the day ahead reaches this connector's
live read instead.

## What a connector is allowed to assert

Nothing about provenance. Every event leaves here `Provenance.UNKNOWN` (AD-36)
and carries no minted id (AD-34) — `pm_ai.core.normalize` decides authorship,
because it is the only layer that can see the executed-mutation ledger, and
hard-coding `EXTERNAL` would make pm-ai's own writes admissible as evidence that
its own promises were kept. `gitlab.py` states the same rule at the same two
points.

Nothing about where a record lands, either, beyond reading the mapping it was
given: a Graph event carries no pm-ai scope, so `CategoryScopes` — per-machine
connector configuration, not `config.toml` — is the only honest source, and an
untagged row is the PM's own meeting rather than a row to drop.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from pm_ai.connectors.graph.calendar import (
    CalendarAttendee,
    CalendarFetch,
    CalendarRow,
    CalendarWindow,
    GraphCalendarFetch,
    WindowPolicy,
)
from pm_ai.connectors.graph.channels import (
    EXCERPT_LENGTH,
    MAX_LABEL_LENGTH,
    ChannelFetch,
    ChannelListing,
    GraphChannelFetch,
    MessageRow,
    display_name,
    label_problem,
    unusable_id_character,
)
from pm_ai.connectors.graph.chats import (
    MAX_CHAT_ID_LENGTH,
    ChatFetch,
    ChatListing,
    GraphChatFetch,
)
from pm_ai.domain.connector_rows import USER_ID_KEY
from pm_ai.domain.event_entries import MAX_ENTRY_LENGTH, render_value
from pm_ai.domain.events import (
    MeetingHeldPayload,
    MessagePayload,
    NormalizedEvent,
    ObservedEventType,
    Provenance,
)
from pm_ai.domain.handles import EMAIL, GRAPH, GRAPH_APP, NO_PERSON, usable_handle
from pm_ai.domain.harvest import (
    UNCLASSIFIED_FAULT_IS_RETRYABLE,
    Cursor,
    HarvestFailure,
    HarvestOutcome,
    HarvestResult,
    RowRefusal,
)
from pm_ai.domain.health import Health, Probe
from pm_ai.domain.identity import (
    Actor,
    DataScope,
    MalformedReference,
    ScopeKind,
    SourceRef,
)
from pm_ai.domain.lifecycle import CoverageWindow
from pm_ai.domain.meetings import Meeting

__all__ = [
    "ALL_DAY_MINUTES",
    "AuthProbe",
    "CATEGORIES_KEY",
    "CHANNELS_KEY",
    "CHATS_KEY",
    "CLIENT_ID_KEY",
    "MAX_CHAT_ID_LENGTH",
    "MAX_LABEL_LENGTH",
    "MAX_SETTING_MINUTES",
    "TENANT_KEY",
    "CategoryScopes",
    "ChannelListRefused",
    "ChannelListing",
    "ChatListRefused",
    "ChatListing",
    "GraphConnector",
    "GraphRowRefused",
    "MissingGraphSetting",
    "PERSONAL",
    "REACH_BACK_KEY",
    "SAMPLE_ROW",
    "UnknownProject",
    "USER_ID_KEY",
    "WIDTH_KEY",
    "graph_category_scopes",
    "graph_channel_listings",
    "graph_chat_listings",
    "graph_window_policy",
]


PERSONAL = DataScope(ScopeKind.PERSONAL)
"""Where an untagged meeting lands, spelled once.

Not a fallback that hides a gap: a meeting nobody categorised is the PM's own,
it belongs in the personal tree, and it still appears on the personal dashboard.
Dropping it instead would lose the half of the calendar the PM most needs to see,
and filing it under the project would put a private appointment in the team's
record, where AD-38's wall exists to keep it out.
"""

ALL_DAY_MINUTES = 0
"""What an all-day row records, and it is a decision rather than an arithmetic.

An all-day entry is a marker — a birthday, an OOO block, a sprint boundary — not
a meeting anybody sat through, so it contributes nothing to FR-03's Man-Hour
Cost. `1440` is the answer this is chosen against: five attendees at £100/h would
report £12,000 for a birthday, and a card showing that is worse than one showing
nothing. `duration_minutes` is an `int` on both `Meeting` and
`MeetingHeldPayload`, so the convention has to be a whole number, which `0` is.
"""


# ── Refusals ─────────────────────────────────────────────────────────────────


class GraphRowRefused(ValueError):
    """One calendar row this connector will not map, and the harvest continues.

    The third of the family — `gitlab.RowRefused` and
    `calendar.CalendarRowRefused` are the others — and it exists for the reason
    both of those do: a row that cannot be read must not discard the rows beside
    it, the coverage the fetch earned, or the outcome. `harvest` catches it,
    records a `RowRefusal`, and keeps mapping.

    Distinct from `CalendarRowRefused` because the two refuse at different
    depths. That one is about a row that cannot be placed in time at all; this
    one is about a row that converted cleanly and still cannot become a
    `Meeting` — an id that is not a legal `meeting:` reference, or a span that
    runs backwards and so has no duration to record.

    A `ValueError` because the provider's data is what is wrong, matching both
    siblings and `ImplausibleTimestamp` one layer down.
    """


class UnknownProject(LookupError):
    """A category maps to a project id no registry knows, so nothing was built.

    **Deliberately not `pm_ai.platform.paths.UnknownProject`, and the duplication
    is forced rather than careless.** `pm_ai.connectors` and `pm_ai.platform` are
    independent siblings in the enforced layer stack, so this package cannot
    import that class and cannot raise it. What it can do is refuse the same fact
    one layer earlier, before a harvest resolves anything: the resolver's version
    is raised when a path is asked for, and by then a batch is in hand.

    Raised at construction of the mapping and never during a harvest, which is
    what keeps `ConnectorPort.harvest`'s report-never-raise contract intact. A
    stale mapping entry is pm-ai's own misconfiguration rather than anything the
    provider said, so the right moment to refuse it is composition, and the right
    thing to name is both halves — the category the PM types in Outlook and the
    project id it points at — because only the pair says what to repair.

    A `LookupError` for the reason the resolver's is: a name was looked up and is
    not there.

    **It is also the refusal for a mapping entry that could never be looked up**
    — a key or a value that is not a usable string, and two keys that differ only
    in case. Those are not lookups that failed, and naming them here rather than
    inventing a second class is deliberate: every one of them is the same fact to
    the operator holding `connectors/<instance>.json` open, which is that this
    category-to-project mapping cannot be used and no connector was built from
    it. A second exception would split one repair into two.
    """


class MissingGraphSetting(LookupError):
    """A Graph enrolment row carries no usable value for a setting with no default.

    **Missing is the first case and not the only one**, and the docstring used to
    say otherwise while the code already refused three: the key absent, the value
    not a whole number of minutes, and the number outside
    `MAX_SETTING_MINUTES`. All three leave the same hole — this row names no
    harvest window — and all three are repaired in the same file by the same
    person, so they are one refusal naming one key rather than a taxonomy of how
    a hand-edited line went wrong.

    The two window widths are the case this exists for. `WindowPolicy` requires
    both with no defaults because story 33b's `Ask First` reserved them for the
    human, and supplying one here would put that answer back — invisibly, in
    every composition that accepted it by silence. So a row without either builds
    no connector, and the refusal names the key rather than the row: an operator
    editing `connectors/<instance>.json` by hand needs the word to type.

    `client_id` is refused the same way and for a sharper reason: it identifies
    the Entra application the PM consents to, and pm-ai inventing one would be
    pm-ai choosing whose app asks for their calendar.

    A `LookupError` for the case that names the class, and kept for the other two
    so a caller has one thing to catch.
    """


class ChannelListRefused(ValueError):
    """The row's `channels` list cannot be read, so no connector was built.

    A list that is not a list, an entry that is not an object, an entry missing
    its team or channel id, a label that is not text, and one channel id listed
    twice under any team or project. Each names the entry, because the operator
    repairing it is holding `connectors/<instance>.json` open. An entry naming a
    project no registry knows is `UnknownProject` instead, the refusal the
    category mapping already uses for the same fact.

    A `ValueError`, so `_graph_connector`'s handler reports it like every other
    row refusal and `build()` composes regardless.
    """


class ChatListRefused(ValueError):
    """The row's `chats` list cannot be read, so no connector was built (`33f`).

    A list that is not a list, an entry that is not an object, an entry
    missing its chat id or scope, a scope that is neither personal nor a
    project, a people scope, a label that is not text, and one chat id listed
    twice. Each names the entry. An entry naming a project no registry knows
    is `UnknownProject` instead, as for channels and categories.

    A `ValueError`, so `_graph_connector`'s handler reports it like every other
    row refusal and `build()` composes regardless.
    """


# ── Where a record lands ─────────────────────────────────────────────────────

WIDTH_KEY = "window_width_minutes"
REACH_BACK_KEY = "first_run_reach_back_minutes"
CLIENT_ID_KEY = "client_id"
TENANT_KEY = "tenant"
CATEGORIES_KEY = "categories"
CHANNELS_KEY = "channels"
CHATS_KEY = "chats"
"""The keys a Graph enrolment row carries, spelled once.

They live in `connectors/<instance>.json` — application scope, Tier 1,
gitignored — and **not** in `config.toml`, whose vocabulary is closed at four
keys. These are per-machine connector settings: which Entra application this
laptop signs in through, how much calendar this PM wants read, and which of their
Outlook categories mean which project. None of that is a fact about pm-ai, and a
key in the closed vocabulary is a key every installation then has.
"""

# `USER_ID_KEY` ("user_id", story 8q) — the signed-in Microsoft user's Graph user
# id — is imported above from `pm_ai.domain.connector_rows` and re-exported. It
# is spelled there, once, because `pm_ai.core.connector_enrolment` writes it and
# applies its absent rule, and this package does not import `core`.


@dataclass(frozen=True)
class CategoryScopes:
    """Outlook category to project scope, and personal for everything else.

    The PM tags a meeting in a UI they already use and the tag decides the tree.
    That is the only honest source available: a Graph event carries no pm-ai
    scope, and inferring one from a subject line or an attendee list would be
    pm-ai guessing where a private appointment belongs.

    Matching is casefolded because a category is display text the PM types, and
    `Platform` and `platform` are one tag to everybody except a dict lookup.
    Order is the *row's*: `CalendarRow.categories` preserves what Graph sent, and
    the first tag that maps wins, so a meeting tagged for two projects lands in
    the one Outlook lists first rather than in whichever the dict happened to
    iterate to.

    **Two keys that casefold together are refused rather than merged**, and that
    is the other half of the same decision. `{"Platform": "alpha", "platform":
    "beta"}` is one tag with two answers: folding them silently files every
    Platform meeting under whichever entry the mapping happened to iterate to
    last, and the losing project's meetings land in the winner's committed tree
    — the AD-38 leak this class exists to prevent, arriving through the class
    itself. There is no correct guess between the two, so the operator makes it.
    Two spellings pointing at the *same* project are allowed through: that is a
    redundant line rather than an ambiguity, and there is nothing for a refusal
    to protect.
    """

    projects: Mapping[str, str]
    """Category (as the PM typed it) to project id. Empty is a real configuration
    — every meeting is then personal, which is the correct answer for a PM who
    has not tagged anything yet.
    """

    registered: Callable[[str], bool]
    """Whether a project id is one this machine's registry knows (AD-11).

    Injected rather than looked up, because `pm_ai.connectors` may not import the
    scope resolver — the composition root asks it and passes the answer as a
    predicate. Required, with no default: a default of "yes" would accept a stale
    mapping entry silently and surface it as a path refusal mid-harvest, with a
    batch already in hand.
    """

    _folded: Mapping[str, str] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        folded: dict[str, str] = {}
        spelled: dict[str, str] = {}
        for category, project in self.projects.items():
            if not isinstance(category, str) or not category.strip():
                raise UnknownProject(
                    f"a Graph category mapping entry has {category!r} where an "
                    f"Outlook category belongs. The key is the tag the PM types "
                    f"on the meeting, so an empty one matches every untagged row "
                    f"and would file the PM's private appointments into a "
                    f"project tree."
                )
            if not isinstance(project, str) or not project.strip():
                raise UnknownProject(
                    f"the Graph category {category!r} maps to {project!r}, which "
                    f"is not a project id. A category has to name the project "
                    f"whose `meetings/` the record lands in; there is no default."
                )
            if not self.registered(project):
                raise UnknownProject(
                    f"the Graph category {category!r} maps to project "
                    f"{project!r}, which no registry knows. Projects enter the "
                    f"system through `pm-ai project add` and never by being "
                    f"named in a connector's configuration (AD-11), so this is a "
                    f"mapping left behind by a project that was removed or a "
                    f"typo in an id. Refused here rather than mid-harvest: the "
                    f"resolver would raise on the first write, with a batch "
                    f"already in hand."
                )
            key = category.strip().casefold()
            if key in folded and folded[key] != project:
                raise UnknownProject(
                    f"the Graph categories {spelled[key]!r} and {category!r} are "
                    f"the same tag — matching is casefolded, because a category "
                    f"is display text the PM types — and they map to "
                    f"{folded[key]!r} and {project!r}. Refused rather than "
                    f"resolved by iteration order: one of the two projects would "
                    f"silently receive the other's meetings, and if either is a "
                    f"committed tree that is the AD-38 leak this mapping exists "
                    f"to prevent. Delete one entry."
                )
            folded[key] = project
            spelled[key] = category
        object.__setattr__(self, "_folded", folded)

    def scope_for(self, categories: tuple[str, ...]) -> DataScope:
        """The scope a row's categories name, or the personal scope.

        Never raises for an unmapped tag: an untagged or differently-tagged
        meeting is the PM's own, and nothing is dropped. The ids in the mapping
        were checked at construction, so nothing here can produce a scope the
        resolver will refuse.
        """
        for category in categories:
            project = self._folded.get(category.strip().casefold())
            if project is not None:
                return DataScope(ScopeKind.PROJECT, project_id=project)
        return PERSONAL


def graph_window_policy(entry: Mapping[str, object]) -> WindowPolicy:
    """The two harvest widths off one enrolment row, or a refusal naming the key.

    Minutes, as integers, because that is the unit CAP-2's cycle is stated in and
    the floor `WindowPolicy.__post_init__` enforces is compared against. A row
    written by hand says `1440` and `10080` for a day and a week.

    Neither is defaulted. `WindowPolicy` refuses to guess for the reason story
    33b's `Ask First` reserved the question, and answering it here would move the
    default one file along rather than remove it.
    """
    return WindowPolicy(
        width=_minutes(entry, WIDTH_KEY),
        first_run_reach_back=_minutes(entry, REACH_BACK_KEY),
    )


def graph_category_scopes(
    entry: Mapping[str, object], *, registered: Callable[[str], bool]
) -> CategoryScopes:
    """The category-to-project mapping off one enrolment row.

    An absent `categories` key is an empty mapping rather than a refusal, and
    that is the one Graph setting with a safe absence: no mapping means every
    meeting is personal, which is exactly right for a PM who has not tagged
    anything and is what an unmapped row gets anyway.

    The decoded mapping is handed to `CategoryScopes` **as it was decoded**.
    This used to coerce every key with `str(key)`, which made
    `__post_init__`'s `isinstance(category, str)` branch unreachable and turned a
    JSON `null` key into the literal Outlook category `"None"` — a tag no PM can
    type, silently filing nothing while looking like a mapping that works.
    """
    raw = entry.get(CATEGORIES_KEY)
    if raw is None:
        return CategoryScopes(projects={}, registered=registered)
    if not isinstance(raw, Mapping):
        raise UnknownProject(
            f"{CATEGORIES_KEY!r} in this Graph enrolment row is a "
            f"{type(raw).__name__} rather than an object mapping an Outlook "
            f"category to a project id. Refused rather than ignored: a mapping "
            f"pm-ai cannot read would file every tagged meeting as personal and "
            f"say nothing about having done so."
        )
    return CategoryScopes(projects=raw, registered=registered)


def graph_channel_listings(
    entry: Mapping[str, object], *, registered: Callable[[str], bool]
) -> tuple[ChannelListing, ...]:
    """The channels this row lists by hand, or a refusal naming the entry (`33d`).

    Each entry is `{"team": …, "channel": …, "project": …, "label": …}`, the
    label optional. pm-ai never lists teams or channels: what is read is what
    the PM wrote here. No list means calendar only, as before this slice.

    Refused, and the connector not built: a value that is not a list; an entry
    that is not an object; a team or channel id missing, not text, padded, or
    holding whitespace or a `/` — the ids become a request path and a
    reference; a label that is present and not text; a project no registry
    knows (`UnknownProject`); and a channel id listed twice under any team or
    project, compared ignoring case, because one channel cannot belong to two
    projects' logs.
    """
    raw = entry.get(CHANNELS_KEY)
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ChannelListRefused(
            f"{CHANNELS_KEY!r} in this Graph enrolment row is a "
            f"{type(raw).__name__}, not a list of channel entries. Each entry is "
            f'an object: {{"team": …, "channel": …, "project": …, "label": …}}.'
        )
    listings: list[ChannelListing] = []
    first: dict[str, int] = {}
    for position, item in enumerate(raw, start=1):
        where = f"channel entry {position} in {CHANNELS_KEY!r}"
        if not isinstance(item, Mapping):
            raise ChannelListRefused(
                f"{where} is a {type(item).__name__}, not an object with a "
                f"team id, a channel id and a project."
            )
        team = _listed_id(item, "team", where=where)
        channel = _listed_id(item, "channel", where=where)
        project = item.get("project")
        if not isinstance(project, str) or not project.strip():
            raise UnknownProject(
                f"{where} (channel {channel!r}) names no project. A channel's "
                f"messages belong to one enrolled project's event log, and there "
                f"is no default."
            )
        project = project.strip()
        if not registered(project):
            raise UnknownProject(
                f"{where} (channel {channel!r}) belongs to project {project!r}, "
                f"which no registry knows. Projects enter the system through "
                f"`pm-ai project add` (AD-11); this is a removed project or a "
                f"typo in an id."
            )
        label = item.get("label")
        problem = None if label is None else label_problem(label)
        if problem is not None:
            raise ChannelListRefused(
                f"{where} (channel {channel!r}) has a label that {problem}. "
                f"Leave the label out to show the channel id."
            )
        folded = channel.casefold()
        if folded in first:
            raise ChannelListRefused(
                f"the channel id {channel!r} is listed twice in "
                f"{CHANNELS_KEY!r}, in entries {first[folded]} and {position}. "
                f"One channel's messages belong to one project's log; delete "
                f"one entry."
            )
        first[folded] = position
        listings.append(
            ChannelListing(
                team=team,
                channel=channel,
                project=project,
                label=label.strip() if isinstance(label, str) else None,
            )
        )
    return tuple(listings)


def _listed_id(item: Mapping[object, object], key: str, *, where: str) -> str:
    """One team or channel id off a channel entry, or the refusal naming it."""
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ChannelListRefused(f"{where} has no {key} id ({value!r}).")
    if value != value.strip() or any(unusable_id_character(c) for c in value):
        raise ChannelListRefused(
            f"{where} has the {key} id {value!r}, which holds whitespace, a "
            f"control or format character or one of `/`, `\\`, `?`, `#`. The id becomes "
            f"part of a request path and of every message's reference, so it is "
            f"refused rather than repaired."
        )
    return value


def graph_chat_listings(
    entry: Mapping[str, object], *, registered: Callable[[str], bool]
) -> tuple[ChatListing, ...]:
    """The chats this row lists by hand, or a refusal naming the entry (`33f`).

    Each entry is `{"chat": …, "scope": …, "label": …}`, the label optional.
    The scope is `personal` or `project:<id>` naming an enrolled project — the
    spelling a scope gives itself. pm-ai never lists chats: what is read is
    what the PM wrote here. No list means no chat is read.

    Refused, and the connector not built: a value that is not a list; an entry
    that is not an object; a chat id missing, not text, padded, or holding
    whitespace, a control or format character or one of `/`, `\\`, `?`, `#`,
    `.` or `..`, or longer than `MAX_CHAT_ID_LENGTH`; a scope missing, padded,
    or neither personal nor a project; a people scope in any case, with the
    sentence that says why; a project no registry knows (`UnknownProject`); a
    project named `personal` in any case, whose references would read as
    personal ones; a project id the reference grammar cannot carry; a label
    failing `label_problem`; and a chat id listed twice, compared ignoring
    case, because one chat's messages go to one scope.
    """
    raw = entry.get(CHATS_KEY)
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ChatListRefused(
            f"{CHATS_KEY!r} in this Graph enrolment row is a "
            f"{type(raw).__name__}, not a list of chat entries. Each entry is an "
            f'object: {{"chat": …, "scope": "personal" or "project:<id>", '
            f'"label": …}}.'
        )
    listings: list[ChatListing] = []
    first: dict[str, int] = {}
    for position, item in enumerate(raw, start=1):
        where = f"chat entry {position} in {CHATS_KEY!r}"
        if not isinstance(item, Mapping):
            raise ChatListRefused(
                f"{where} is a {type(item).__name__}, not an object with a chat "
                f"id and a scope."
            )
        chat = _listed_chat(item.get("chat"), where=where)
        scope = _chat_scope(
            item.get("scope"), where=f"{where} (chat {chat!r})", registered=registered
        )
        label = item.get("label")
        problem = None if label is None else label_problem(label)
        if problem is not None:
            raise ChatListRefused(
                f"{where} (chat {chat!r}) has a label that {problem}. Leave the "
                f"label out to show the chat's topic or kind."
            )
        folded = chat.casefold()
        if folded in first:
            raise ChatListRefused(
                f"the chat id {chat!r} is listed twice in {CHATS_KEY!r}, in "
                f"entries {first[folded]} and {position}. One chat's messages go "
                f"to one scope; delete one entry."
            )
        first[folded] = position
        listings.append(
            ChatListing(
                chat=chat,
                scope=scope,
                label=label.strip() if isinstance(label, str) else None,
            )
        )
    return tuple(listings)


def _citable_scope(project: str) -> bool:
    """Whether a project id survives as the scope segment of a reference."""
    try:
        parsed = SourceRef.parse(f"graph:{project}:message:x")
    except MalformedReference:
        return False
    return parsed.scope == project


NO_TEAM_MEMBER_REGISTRY = (
    "pm-ai has no team-member registry yet, so a chat with a direct report "
    "cannot be filed in that person's scope, where AD-4 says it belongs. Do not "
    "list it until that registry exists"
)
"""Why a chat scoped to a person is refused (`33f`), spelled once."""


def _listed_chat(value: object, *, where: str) -> str:
    """One chat id off a chat entry, or the refusal naming the entry."""
    if not isinstance(value, str) or not value.strip():
        raise ChatListRefused(f"{where} has no chat id ({value!r}).")
    if value != value.strip() or any(unusable_id_character(c) for c in value):
        raise ChatListRefused(
            f"{where} has the chat id {value!r}, which holds whitespace, a "
            f"control or format character or one of `/`, `\\`, `?`, `#`. "
            f"The id becomes part of a request path and of every message's "
            f"reference, so it is refused rather than repaired."
        )
    if value in (".", ".."):
        raise ChatListRefused(
            f"{where} has the chat id {value!r}, which a request path reads as "
            f"a directory step rather than a chat."
        )
    if len(value) > MAX_CHAT_ID_LENGTH:
        raise ChatListRefused(
            f"{where} has a chat id of {len(value)} characters, past the "
            f"{MAX_CHAT_ID_LENGTH} a chat id may be (`MAX_CHAT_ID_LENGTH`). A "
            f"chat id is about a hundred characters; this is likely a paste "
            f"gone wrong."
        )
    return value


def _chat_scope(
    value: object, *, where: str, registered: Callable[[str], bool]
) -> DataScope:
    """A chat entry's scope: personal, or an enrolled project. Refused otherwise."""
    if not isinstance(value, str) or not value.strip():
        raise ChatListRefused(
            f"{where} names no scope ({value!r}). A chat's messages go to "
            f"`personal` or to `project:<id>`, and there is no default."
        )
    if value != value.strip():
        raise ChatListRefused(
            f"{where} has the scope {value!r}, padded with whitespace. Refused "
            f"rather than trimmed, as a padded chat id is."
        )
    text = value
    kind, _, subject = text.partition(":")
    if kind.casefold() == ScopeKind.PEOPLE.value:
        raise ChatListRefused(f"{where} is scoped {text!r}: {NO_TEAM_MEMBER_REGISTRY}.")
    if text == ScopeKind.PERSONAL.value:
        return PERSONAL
    if kind == ScopeKind.PROJECT.value and subject.strip():
        project = subject
        if project.casefold() == ScopeKind.PERSONAL.value:
            raise ChatListRefused(
                f"{where} is scoped {text!r}. A project named 'personal' cites "
                f"its messages exactly as the personal scope does, so the two "
                f"could not be told apart; enrol the project under another id."
            )
        if not _citable_scope(project):
            raise ChatListRefused(
                f"{where} is scoped to project {project!r}, an id the message "
                f"reference grammar `graph:<scope>:message:<id>` cannot carry "
                f"(AD-34), so no message from it could be cited."
            )
        if not registered(project):
            raise UnknownProject(
                f"{where} is scoped to project {project!r}, which no registry "
                f"knows. Projects enter the system through `pm-ai project add` "
                f"(AD-11); this is a removed project or a typo in an id."
            )
        return DataScope(ScopeKind.PROJECT, project_id=project)
    raise ChatListRefused(
        f"{where} is scoped {text!r}, which is neither `personal` nor "
        f"`project:<id>` naming an enrolled project."
    )


MAX_SETTING_MINUTES = 10 * 366 * 24 * 60
"""The far end of a harvest width, and it is a guard rather than a preference.

`timedelta(minutes=value)` raises `OverflowError` past roughly 1.5e12 minutes,
and `OverflowError` is not a `ValueError`: it escaped `_graph_connector`'s
handler and took `build()` down with it, so a single mistyped digit in a
hand-edited `connectors/` row stopped `pm-ai doctor` — the one command that
diagnoses a mistyped `connectors/` row — from running at all.

Ten years of minutes rather than the arithmetic limit, because the value is a
*calendar* reach: a first run wanting a decade of history is already far past
anything a PM has, and a number above it is a typo rather than a request. Wide
enough that no real answer meets it, narrow enough that the arithmetic below
cannot overflow.
"""


def _minutes(entry: Mapping[str, object], key: str) -> timedelta:
    """One integer minute count off the row, refusing everything that is not one.

    `bool` before `int`, as everywhere else that reads hand-editable
    configuration: `True` is an `int` in Python and would arrive as a one-minute
    harvest window, which `WindowPolicy` then refuses with a message about CAP-2
    rather than about the file somebody typed.

    Bounded before it becomes a `timedelta`, not after — see
    `MAX_SETTING_MINUTES`. The lower end is here for the same arithmetic reason
    as the upper one and not because `WindowPolicy` would miss it: a large
    negative overflows exactly as a large positive does, before any policy sees
    it.
    """
    value = entry.get(key)
    if value is None:
        raise MissingGraphSetting(
            f"this Graph enrolment row carries no {key!r}, so there is no "
            f"harvest window to build and no connector was constructed. Add "
            f"{key!r} to the row as a whole number of minutes — there is no "
            f"default, because a window width accepted by silence is invisible "
            f"in every composition that took it."
        )
    if isinstance(value, bool) or not isinstance(value, int):
        raise MissingGraphSetting(
            f"{key!r} in this Graph enrolment row is {value!r}, which is not a "
            f"whole number of minutes. The row is plaintext and hand-editable, "
            f"so a string or a float is an ordinary typo — and refused rather "
            f"than coerced, because rounding somebody's window silently is how a "
            f"harvest ends up covering a range nobody chose."
        )
    if not 0 < value <= MAX_SETTING_MINUTES:
        raise MissingGraphSetting(
            f"{key!r} in this Graph enrolment row is {value!r} minutes, which is "
            f"outside the 1 to {MAX_SETTING_MINUTES} a harvest width may be. Ten "
            f"years is not a bound anybody's calendar meets, so a number past it "
            f"is a mistyped digit — and the arithmetic that turns it into a "
            f"window raises `OverflowError` rather than a refusal, which is a "
            f"fault `build()` does not survive and `pm-ai doctor` cannot then be "
            f"run to explain."
        )
    return timedelta(minutes=value)


# ── The sample every architecture gate inspects ──────────────────────────────

SAMPLE_ROW = CalendarRow(
    event_id="AAMkAGsample",
    start=datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc),
    end=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
    subject="a sample meeting, mapped by the same code path as a real one",
    organizer=CalendarAttendee(email="sample@example.invalid", name="A Person"),
    attendees=(CalendarAttendee(email="other@example.invalid", name="B Person"),),
    response="accepted",
)
"""The row `sample_events` maps. Fixed values, and no categories.

No categories on purpose: the sample must not depend on this machine's mapping,
and an untagged row is the case every installation has. It maps to the personal
scope, which is a scope every machine holds.

**No clock is involved, and there used to be a `SAMPLE_NOW` claiming otherwise.**
`sample_events` maps this row and emits its event directly rather than going
through the past/future split, so nothing here is compared against an instant —
the constant was defined, exported and described in two docstrings as standing
in for a clock it was never passed to. Deleted rather than wired: giving the
sample a clock would make it exercise a branch the gates do not ask about, and a
fixed row with fixed values is already the determinism the constant was said to
buy.
"""


SAMPLE_CHANNEL = ChannelListing(
    team="00000000-0000-0000-0000-00000000000a",
    channel="19:sample@thread.tacv2",
    project="sample",
    label="a sample channel",
)
SAMPLE_MESSAGE = MessageRow(
    message_id="1600000000000",
    created_at=datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc),
    text="a sample message, mapped by the same code path as a real one",
    sender_user="00000000-0000-0000-0000-00000000000b",
    mentions=("00000000-0000-0000-0000-00000000000c",),
)
"""The message `sample_events` maps, for the AD-34 minted-id gate (`33d`).

Fixed values and no clock, as `SAMPLE_ROW`. Its project is a name and not a
lookup: nothing is written, and the gate asks only that no id is minted.
"""

SAMPLE_CHAT = ChatListing(
    chat="19:sample_chat@unq.gbl.spaces",
    scope=PERSONAL,
    label="a sample chat",
)
"""The chat `sample_events` maps `SAMPLE_MESSAGE` from (`33f`). Personal, the
scope every machine holds."""


# ── The connector ────────────────────────────────────────────────────────────


@runtime_checkable
class AuthProbe(Protocol):
    """The one thing this connector asks of `33a`'s adapter: can it get a token?

    Declared here, narrow, for the reason `client.TokenSource` is declared beside
    the client: the connector needs one method and naming the whole
    `GraphAuthPort` would claim a dependency on six.

    It is also the only shape that type-checks. `GraphAuthPort.scopes` is an
    instance variable in the protocol and a `ClassVar` on
    `GraphDeviceCodeAuth` — deliberately, so a caller cannot pass `scopes=` and
    override the very set the partial-consent refusal compares a grant against —
    and a `ClassVar` does not satisfy a protocol's instance attribute. Widening
    the port or reopening that constructor argument to make the annotation fit
    would be changing a security decision to please a type; asking for less is
    not.
    """

    def check_health(self) -> Probe:
        """Reports; never raises. See `GraphAuthPort.check_health`."""


@dataclass
class GraphConnector:
    """`ConnectorPort` over `calendarView` and the listed Teams channels, with no
    cadence (AD-9).

    Holds no schedule, no cursor of its own and no HTTP: `33b`'s
    `GraphCalendarFetch` and `33d`'s `GraphChannelFetch` own the wire, this owns
    the modelling. What it decides
    is which rows become Tier-1 records, which become events, and what scope each
    lands in — and it decides nothing about provenance, which is AD-36's and
    `pm_ai.core.normalize`'s.

    Reports; never raises. Everything the fetch or the mapping can throw becomes
    a `HarvestFailure` or a `RowRefusal` in the returned value, because a raise
    cannot carry the coverage a partial walk earned.
    """

    auth: AuthProbe
    """`33a`'s adapter, held for `check_health` alone.

    The same object the fetch's client draws tokens from, but reached separately:
    `GraphClient.auth` is typed as a `TokenSource`, which declares
    `access_token` and nothing else, so a health probe routed through it would be
    reading an attribute no contract promises. Naming a shape here is what lets
    this connector answer the registry's question without guessing.
    """

    calendar: GraphCalendarFetch
    categories: CategoryScopes
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    """This machine's clock, injected (AD-30).

    It decides one thing and it is the load-bearing one: whether a row has ended,
    and therefore whether it becomes a durable record or a live read. A guard
    that cannot be exercised deterministically is a guard nobody can trust, and
    the past/future split is exactly that guard.
    """

    name: str = "graph"
    system: str = "graph"

    channels: tuple[ChannelListing, ...] = ()
    """The Teams channels the PM listed by hand (`33d`). Empty: calendar only."""

    messages: GraphChannelFetch | None = None
    """The channel fetcher. `None` builds one over the calendar's client."""

    chats: tuple[ChatListing, ...] = ()
    """The Teams chats the PM listed by hand (`33f`). Empty: no chat is read."""

    chat_messages: GraphChatFetch | None = None
    """The chat fetcher. `None` builds one over the calendar's client."""

    @property
    def instance(self) -> str:
        """This connector's registry identity, taken from the fetch it owns.

        Not a field of its own. The same string keys the cursor, the coverage
        window and the probe row, and `GraphCalendarFetch` already holds it —
        two spellings is how a coverage window gets stored under one name and
        read back under another.
        """
        return self.calendar.instance

    def emits(self) -> frozenset[ObservedEventType]:
        """Exactly two members of the core taxonomy (AD-27).

        `MESSAGE_POSTED` joined `CALENDAR_EVENT_HELD` in `33d`, a deliberate
        change here rather than something a connector may mint. Declared
        whether or not this row lists a channel: the declaration is what the
        connector can produce, and a row edited to list one is the same
        connector.
        """
        return frozenset(
            {ObservedEventType.CALENDAR_EVENT_HELD, ObservedEventType.MESSAGE_POSTED}
        )

    def check_health(self) -> Probe:
        """Whether this machine can currently obtain a Graph token.

        Delegated to `33a`'s adapter, which is where the four answers live, and
        relabelled with this connector's instance so one screen can show it
        beside every other row. Wrapped in a `try` even though the port says a
        probe never raises: this method is the one the registry calls, and a
        breach of that contract one layer down must not take the report with it.
        """
        try:
            probe = self.auth.check_health()
        except Exception as raised:  # noqa: BLE001 — a probe reports, never raises
            return Probe(
                self.instance,
                Health.FAILING,
                f"{self.instance}'s auth probe raised "
                f"{type(raised).__name__} instead of reporting. That is a bug in "
                f"pm-ai rather than a verdict about Microsoft Graph.",
                "Report this: a probe is required to return a Probe.",
            )
        return Probe(self.instance, probe.health, probe.detail, probe.remediation)

    def sample_events(self) -> tuple[NormalizedEvent, ...]:
        """One representative event, built offline through the harvest mapping.

        Contacts nothing and reads no clock — not by standing a fixed instant in,
        but because no clock is consulted: `SAMPLE_ROW` is mapped and its event
        built directly, so the same tuple comes back on every run whatever the
        calendar date the suite runs on. The past/future split is `_result`'s and
        is exercised by the harvest tests, where it can be moved.

        Non-empty by construction, because the AD-34 gate asserts over every
        event a connector samples and an empty tuple would pass it without
        executing one assertion.
        """
        meeting = self._meeting(SAMPLE_ROW)
        return (
            self._to_event(meeting, SAMPLE_ROW),
            self._message_event(SAMPLE_CHANNEL, SAMPLE_MESSAGE),
            self._chat_message_event(SAMPLE_CHAT, SAMPLE_CHAT.name, SAMPLE_MESSAGE),
        )

    # ── The harvest ──────────────────────────────────────────────────────────

    def harvest(self, since: Cursor) -> HarvestResult:
        """The calendar, every listed channel, then every listed chat, under one
        budget (`33d`, `33f`).

        The order of the two halves of the result is the whole contract for the
        calendar's part: `records` are the meetings `pm_ai.app.pipelines` writes
        through `11a` **before** it persists `events`, because every held event
        cites `meeting:<id>` and one emitted ahead of a failed record write
        leaves an unresolvable AD-33 citation. A message event cites the
        channel message itself and needs no record.

        **One time budget per harvest**, read once here and shared by the
        calendar and every channel. **Failures are per resource**: a channel
        that cannot be read costs that channel and nothing else — except a
        calendar sign-in failure, which ends the run before any channel is
        attempted, because every channel would spend the same credential.

        Chats follow the channels (`33f`) with the same rules, and one more: a
        throttle or a server error on a chat skips the run's remaining chats,
        each named, carrying the provider's hint.

        Coverage is one window for the whole run and is withheld when any listed
        resource failed — see `_result`.
        """
        return self._read(since, channels=self.channels, chats=self.chats)

    def read_calendar(self, start: datetime, end: datetime) -> HarvestResult:
        """The calendar alone over `start` to `end` (`CalendarReadPort`, `23e`).

        What the dashboard's live read calls (Andrei, 2026-10-12): meetings not
        yet held are never stored, so the day ahead is a live read — and a live
        read must not walk every listed channel on each render, nor let a
        channel it did not need mark the calendar unread. No channel or chat is
        attempted or named. The scheduled harvest (`harvest`) still reads the
        calendar and every listed channel and chat.

        The range is the render's display day, never the harvest's window: the
        window reaches back only as far as the first-run reach-back and forward
        only as far as its width, which at 07:00 missed the afternoon. The split
        between ended and not-yet-ended meetings is the harvest's.
        """
        return self._read(Cursor(), channels=(), chats=(), span=(start, end))

    def _read(
        self,
        since: Cursor,
        *,
        channels: tuple[ChannelListing, ...],
        chats: tuple[ChatListing, ...],
        span: tuple[datetime, datetime] | None = None,
    ) -> HarvestResult:
        """The calendar, then `channels`, then `chats`, as one result. See `harvest`."""
        try:
            positions = _Positions.read(since)
        except (ValueError, TypeError):
            return HarvestResult(
                events=(),
                cursor=since,
                outcome=HarvestOutcome.FAILED,
                failure=HarvestFailure(
                    reason=(
                        f"{self.instance} was handed a stored cursor of "
                        f"{len(since.token)} byte(s) that is neither an aware-UTC "
                        f"calendar instant nor this connector's per-resource "
                        f"positions, so there is no position to resume from. "
                        f"Nothing was fetched and the cursor is unchanged; a "
                        f"cursor this connector did not write needs clearing "
                        f"rather than waiting."
                    ),
                    retryable=False,
                ),
            )

        try:
            # The run's one budget is read by the calendar fetch when it starts,
            # and handed back on `fetched.deadline` for every channel to share.
            # A named range (`read_calendar`, `23e`) or the cursor, never both:
            # `fetch` refuses the mix, and that refusal is caught just below.
            fetched = self.calendar.fetch(
                since=positions.calendar,
                window=None if span is None else CalendarWindow(start=span[0], end=span[1]),
            )
        except Exception as unexpected:  # noqa: BLE001 — a failure is a value here
            # `GraphCalendarFetch.fetch` converts every transport and auth
            # refusal into a `CalendarFetch.failure` itself, so this catches what
            # is left: a clock that is not aware UTC, a policy that cannot build
            # a window. The exception's **type** and not its message, because
            # this sentence is written to Tier 2, never rebuilt, and read back
            # into reports.
            return HarvestResult(
                events=(),
                cursor=since,
                outcome=HarvestOutcome.FAILED,
                failure=HarvestFailure(
                    reason=(
                        f"{self.instance} could not start a calendar fetch: the "
                        f"fetcher raised {type(unexpected).__name__}, which "
                        f"pm-ai cannot classify"
                        f"{_not_attempted(channels, chats)}"
                    ),
                    retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
                ),
            )

        walked: list[ChannelFetch] = []
        chatted: list[ChatFetch] = []
        blocked: HarvestFailure | None = None
        try:
            if (channels or chats) and not fetched.signed_out:
                deadline = fetched.deadline
                if deadline is None:
                    # A fetch that did not say (one built by hand): the run's
                    # budget from now, rather than no channel read at all.
                    deadline = self.now() + self.calendar.client.budget
                signed_out = False
                for listing in channels:
                    walked.append(
                        self._walk_channel(listing, positions, deadline=deadline)
                    )
                    if walked[-1].signed_out:
                        # The credential failed mid-run: every later channel
                        # would spend the same one.
                        signed_out = True
                        break
                busy = any(channel.busy for channel in walked)
                if chats and not signed_out and not busy:
                    if self.now() >= deadline:
                        # Spent before any chat: one failure names them all.
                        blocked = HarvestFailure(
                            reason=(
                                f"{self.instance} spent the run's time budget "
                                f"before reading any chat"
                            ),
                            retryable=True,
                        )
                    else:
                        for chat in chats:
                            chatted.append(
                                self._walk_chat(chat, positions, deadline=deadline)
                            )
                            if chatted[-1].signed_out or chatted[-1].stops_run:
                                # The same credential, or a provider asking
                                # pm-ai to wait: every later chat would meet
                                # the same answer.
                                break
            return self._result(
                fetched,
                tuple(walked),
                tuple(chatted),
                since=since,
                positions=positions,
                skipped=channels[len(walked) :],
                skipped_chats=chats[len(chatted) :],
                blocked=blocked,
            )
        except Exception as unexpected:  # noqa: BLE001 — the port promises a value
            # Everything `_result` does can fault: `NormalizedEvent`'s
            # constructor validates, the clock is injected and the cursor is an
            # encode. (The actor handle cannot: `usable_handle` never raises.)
            # A raise here would leave `run_harvest` — whose only call has no
            # `except` — propagating out of the scheduler with the fetched page,
            # its coverage and its cursor all lost, which is exactly the shape `HarvestOutcome.FAILED`
            # exists to make expressible. The rows are not carried out with it:
            # whatever broke, this run cannot say which half of them it mapped.
            #
            # The reason says what actually happened (`8p`): with no page read
            # there was no page to map, and a walk that ended without raising
            # and without a body reaches here as an EMPTY with no instant.
            read = _first_answer(fetched, tuple(walked), tuple(chatted))
            happened = (
                f"fetched {_first_answering(fetched, tuple(walked), tuple(chatted))} "
                f"and then raised {type(unexpected).__name__} while mapping the run"
                if read is not None
                else f"read no calendar page — the walk ended without one and "
                f"without a fault — and then raised "
                f"{type(unexpected).__name__} building the result"
            )
            return HarvestResult(
                events=(),
                cursor=since,
                outcome=HarvestOutcome.FAILED,
                failure=HarvestFailure(
                    reason=(
                        f"{self.instance} {happened}, which pm-ai cannot "
                        f"classify. Nothing was recorded and the cursor is "
                        f"unchanged, so the same window is walked again next run."
                    ),
                    retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
                ),
                # The page was read, so the provider did answer, and when is a
                # fact the mapping fault does not undo (`8p`). No window rides
                # with it: this run cannot say what it learned. Carried only
                # when aware UTC: an instant `HarvestResult` refuses may be the
                # very fault that landed here, and reusing it would raise out of
                # a method whose contract is that it reports.
                answered_at=(
                    read
                    if read is not None
                    and read.tzinfo is not None
                    and read.utcoffset() == timedelta(0)
                    else None
                ),
            )

    def _walk_channel(
        self, listing: ChannelListing, positions: _Positions, *, deadline: datetime
    ) -> ChannelFetch:
        """One channel's walk, with a contract breach costing only that channel."""
        try:
            return self._messages().fetch(
                listing,
                since=positions.channels.get(listing.channel.casefold()),
                deadline=deadline,
            )
        except Exception as unexpected:  # noqa: BLE001 — a failure is a value here
            return ChannelFetch(
                listing=listing,
                failure=HarvestFailure(
                    reason=(
                        f"{self.instance} could not start walking channel "
                        f"{listing.name!r}: the fetcher raised "
                        f"{type(unexpected).__name__}, which pm-ai cannot classify"
                    ),
                    retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
                ),
            )

    def _walk_chat(
        self, listing: ChatListing, positions: _Positions, *, deadline: datetime
    ) -> ChatFetch:
        """One chat's walk, with a contract breach costing only that chat."""
        try:
            return self._chat_messages().fetch(
                listing,
                since=positions.chats.get(listing.chat.casefold()),
                deadline=deadline,
            )
        except Exception as unexpected:  # noqa: BLE001 — a failure is a value here
            return ChatFetch(
                listing=listing,
                failure=HarvestFailure(
                    reason=(
                        f"{self.instance} could not start walking chat "
                        f"{listing.name!r}: the fetcher raised "
                        f"{type(unexpected).__name__}, which pm-ai cannot classify"
                    ),
                    retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
                ),
                finished=self.now(),
            )

    def _chat_messages(self) -> GraphChatFetch:
        """The chat fetcher: the one composed, or one over the calendar's client."""
        if self.chat_messages is not None:
            return self.chat_messages
        return GraphChatFetch(
            client=self.calendar.client,
            instance=self.instance,
            reach_back=self.calendar.windows.first_run_reach_back,
            now=self.now,
        )

    def _messages(self) -> GraphChannelFetch:
        """The channel fetcher: the one composed, or one over the calendar's client.

        Over the calendar's client by default because the two share one
        credential, one host check and one budget; a separate client would be a
        second place any of those is decided.
        """
        if self.messages is not None:
            return self.messages
        return GraphChannelFetch(
            client=self.calendar.client,
            instance=self.instance,
            reach_back=self.calendar.windows.first_run_reach_back,
            now=self.now,
        )

    def _result(
        self,
        fetched: CalendarFetch,
        walked: tuple[ChannelFetch, ...],
        chatted: tuple[ChatFetch, ...] = (),
        *,
        since: Cursor,
        positions: _Positions,
        skipped: tuple[ChannelListing, ...] = (),
        skipped_chats: tuple[ChatListing, ...] = (),
        blocked: HarvestFailure | None = None,
    ) -> HarvestResult:
        """Every resource's answer as the one `HarvestResult` the port promises.

        **A FAILED fetch still carries the rows it did reach, and `run_harvest`
        writes them.** A partial walk is page one succeeded and page two did not:
        those meetings happened, their records are real, and discarding them
        would mean a connector that fails on its last page never records
        anything. What keeps that safe is `33b`'s cursor rule: `walked_through`
        advances only past spans walked to completion, so the span that failed
        is asked for again next run and the records it did produce are rewritten
        in place under the same name — `record_name` is derived from the id and
        the start day, and `put` replaces the fields while carrying `## Notes`
        through. The trade is a record written twice rather than a meeting lost,
        which is the same direction every other fail-closed reading in AD-35
        takes. A channel that failed likewise hands over the messages it read,
        and its position rule is `channels.py`'s; a chat is read the same way,
        and its position rule is `chats.py`'s (`33f`).

        **One coverage window for the whole run**: from the first resource's
        answer instant to the end of the last resource's walk, and withheld
        entirely when any listed resource failed — or when any resource's own
        reading would have withheld it (a calendar whose rows had no credible
        clock, a resource whose every row was refused). Stricter on purpose than
        "a partial walk keeps its window": the connector may not vouch for a
        resource it could not read, and the calendar's window is not separable
        while the result carries one.
        """
        now = self.now()
        records: list[Meeting] = []
        live: list[Meeting] = []
        events: list[NormalizedEvent] = []
        # The fetch's own refusals travel on: a row that could not be placed in
        # time and a row that could not be modelled are both rows somebody has to
        # look at, and dropping the first half here would make a calendar pm-ai
        # cannot parse indistinguishable from an empty one.
        refusals: list[RowRefusal] = [
            RowRefusal(identifier=refused.event_id, reason=refused.reason)
            for refused in fetched.refusals
        ]

        for row in fetched.rows:
            if not self._is_the_pms_meeting(row, now=now):
                continue
            # `_to_event` is inside this `try` and not after it, which is the
            # difference between one refused row and a raise out of a method the
            # port says never raises. It parses a reference and runs
            # `NormalizedEvent`'s validation — on data the provider sent — so it
            # can refuse the same row `_meeting` accepted, and a refusal there
            # must cost the row rather than the batch. The actor handle is not
            # among the faults: `usable_handle` answers `None` for an unusable
            # address and never raises, so a bad address costs only the person.
            try:
                meeting = self._meeting(row)
                held = row.end <= now
                # Ended. The bound is inclusive on this side only, so `end`
                # exactly equal to `now` is held rather than in progress, and the
                # two states cannot both be true of one row.
                event = self._to_event(meeting, row) if held else None
            except GraphRowRefused as refused:
                refusals.append(RowRefusal(identifier=row.event_id, reason=str(refused)))
                continue
            if event is not None:
                records.append(meeting)
                events.append(event)
            else:
                live.append(meeting)

        calendar_mapped = bool(events or records or live)
        calendar_outcome = HarvestOutcome.of(
            mapped=calendar_mapped,
            refused=bool(refusals),
            failed=fetched.failure is not None,
        )
        # The calendar's own reading of whether it earned its window, exactly as
        # it stood before channels joined the run.
        vouched = fetched.coverage is not None and (
            calendar_mapped or calendar_outcome is HarvestOutcome.EMPTY
        )

        for channel in walked:
            emitted = 0
            for message in channel.rows:
                try:
                    events.append(self._message_event(channel.listing, message))
                    emitted += 1
                except GraphRowRefused as refused:
                    refusals.append(
                        RowRefusal(
                            identifier=f"{channel.listing.channel}/{message.message_id}",
                            reason=str(refused),
                        )
                    )
                except Exception as unexpected:  # noqa: BLE001 — costs the row only
                    # Event validation on provider data (a `ValueError`, a
                    # `TypeError`) costs this message, never the run. The type,
                    # not the message: the reason reaches Tier 2.
                    refusals.append(
                        RowRefusal(
                            identifier=f"{channel.listing.channel}/{message.message_id}",
                            reason=(
                                f"channel {channel.listing.name!r} message "
                                f"{message.message_id!r} could not become an "
                                f"event: {type(unexpected).__name__} was raised "
                                f"building it."
                            ),
                        )
                    )
            refusals.extend(channel.refusals)
            if (
                channel.failure is not None
                or channel.unread_pages
                or channel.unread_replies
                or (channel.refusals and not emitted)
            ):
                vouched = False

        for chat in chatted:
            emitted = 0
            name = chat.name if chat.name is not None else chat.listing.name
            for message in chat.rows:
                try:
                    events.append(
                        self._chat_message_event(chat.listing, name, message)
                    )
                    emitted += 1
                except Exception as unexpected:  # noqa: BLE001 — costs the row only
                    refusals.append(
                        RowRefusal(
                            identifier=f"{chat.listing.chat}/{message.message_id}",
                            reason=(
                                str(unexpected)
                                if isinstance(unexpected, GraphRowRefused)
                                else f"chat {chat.listing.name!r} message "
                                f"{message.message_id!r} could not become an "
                                f"event: {type(unexpected).__name__} was raised "
                                f"building it."
                            ),
                        )
                    )
            refusals.extend(chat.refusals)
            if (
                chat.failure is not None
                or chat.unread_pages
                or (chat.refusals and not emitted)
            ):
                vouched = False

        failures = tuple(
            failure
            for failure in (
                fetched.failure,
                *(channel.failure for channel in walked),
                *(chat.failure for chat in chatted),
                blocked,
            )
            if failure is not None
        )
        mapped = bool(events or records or live)
        outcome = HarvestOutcome.of(
            mapped=mapped, refused=bool(refusals), failed=bool(failures)
        )
        answered_at = _first_answer(fetched, walked, chatted)
        # The last resource that finished, so a walk that broke before it
        # could read the clock does not erase the end the others earned.
        finishes = [
            finished
            for finished in (
                *(channel.finished for channel in walked),
                *(chat.finished for chat in chatted),
            )
            if finished is not None
        ]
        ended = (
            finishes[-1]
            if finishes
            else (fetched.coverage.end if fetched.coverage is not None else None)
        )
        coverage = (
            CoverageWindow(connector_instance=self.instance, start=answered_at, end=ended)
            if vouched
            and not failures
            and (mapped or outcome is HarvestOutcome.EMPTY)
            and answered_at is not None
            and ended is not None
            # A clock that stepped backwards between the first answer and the
            # last walk's end leaves no honest interval (`CoverageWindow`
            # refuses a reversed one).
            and ended >= answered_at
            else None
        )

        return HarvestResult(
            events=tuple(events),
            cursor=positions.advanced(
                since,
                calendar=fetched.walked_through,
                channels={
                    channel.listing.channel.casefold(): channel.position
                    for channel in walked
                    if channel.position is not None
                },
                chats={
                    chat.listing.chat.casefold(): chat.position
                    for chat in chatted
                    if chat.position is not None
                },
            ),
            outcome=outcome,
            coverage=coverage,
            failure=_with_skipped(_one_failure(failures), skipped, skipped_chats),
            refusals=tuple(refusals),
            records=tuple(records),
            live=tuple(live),
            # Passed through, never re-read here (`8p`): each resource took its
            # one reading when its first page arrived, and the result's is the
            # first resource that answered.
            answered_at=answered_at,
        )

    # ── One message ──────────────────────────────────────────────────────────

    def _message_event(self, listing: ChannelListing, row: MessageRow) -> NormalizedEvent:
        """The `MESSAGE_POSTED` one channel message or reply emits.

        The sender is a person handle around Microsoft's own user id —
        `graph:<user id>`, never resolved through the alias table here (AD-34):
        a person is matched only when read. A bot sender is
        `graph_app:<application id>`; a sender Microsoft names neither way is
        `none:`. Each mention with a user id is a `graph:` handle, never a
        display name. The one display name recorded is the sender's (`23f`),
        beside the handle and for display only: taken from the same branch of
        `from` that produced the actor, and never for `none:`.
        """
        return self._posted(
            scope=listing.scope,
            reference=(
                f"graph:{listing.project}:message:{listing.channel}/{row.message_id}"
            ),
            what=f"channel {listing.name!r}",
            name=listing.name,
            row=row,
        )

    def _chat_message_event(
        self, listing: ChatListing, name: str, row: MessageRow
    ) -> NormalizedEvent:
        """The `MESSAGE_POSTED` one chat message emits (`33f`).

        The channel mapping unchanged, in the chat's listed scope: the reference
        is `graph:<scope>:message:<chat id>/<message id>`, the scope spelled
        `personal` or as the project's id, and `channel` is `name` — the label,
        the topic, or the chat kind's word (`chats.chat_name`).
        """
        return self._posted(
            scope=listing.scope,
            reference=(
                f"graph:{listing.reference_scope}:message:"
                f"{listing.chat}/{row.message_id}"
            ),
            what=f"chat {listing.name!r}",
            name=name,
            row=row,
        )

    def _posted(
        self, *, scope: DataScope, reference: str, what: str, name: str, row: MessageRow
    ) -> NormalizedEvent:
        """One message as its event: the mapping channels and chats share."""
        try:
            source = SourceRef.parse(reference)
        except MalformedReference as unparsed:
            raise GraphRowRefused(
                f"{what} message {row.message_id!r} cannot be "
                f"cited as {reference!r} under AD-34's grammar ({unparsed})."
            ) from unparsed
        mentions: tuple[str, ...] | None = None
        if row.mentions is not None:
            handles: list[str] = []
            for user_id in row.mentions:
                handle = usable_handle(GRAPH, user_id)
                if handle is not None and str(handle) not in handles:
                    handles.append(str(handle))
            mentions = tuple(handles)
        # The actor first, then the name from the same branch of `from` (`23f`):
        # a user's message never shows an application's name, and `none:` shows
        # no name even when Microsoft sent one.
        user = usable_handle(GRAPH, row.sender_user)
        application = (
            None if user is not None else usable_handle(GRAPH_APP, row.sender_application)
        )
        if user is not None:
            sender_name = display_name(row.sender_user_name)
        elif application is not None:
            sender_name = display_name(row.sender_application_name)
        else:
            sender_name = None
        return NormalizedEvent(
            # AD-4 — a channel's project, or the scope a chat was listed under.
            scope=scope,
            type=ObservedEventType.MESSAGE_POSTED,
            source_ref=source,
            actor=user or application or NO_PERSON,
            # The provider clock (AD-35), carried as sent: implausible is
            # flagged at persist, never replaced.
            occurred_at=row.created_at,
            payload=MessagePayload(
                channel=name,
                # Cut, never cleaned (AD-29): sanitized at the model boundary
                # (AD-12), not here.
                excerpt=row.text[:EXCERPT_LENGTH] or None,
                mentions=mentions,
                # Display only, never compared or matched (AD-34, `23f`).
                sender_name=sender_name,
            ),
            # AD-36 — a connector may never assert `external`.
            authored_by=Provenance.UNKNOWN,
            # no `id`: the storage service mints the surrogate (AD-34)
        )

    # ── One row ──────────────────────────────────────────────────────────────

    def _is_the_pms_meeting(self, row: CalendarRow, *, now: datetime) -> bool:
        """Whether this row is a meeting the PM actually has.

        Two rows of the matrix, and they resolve in opposite directions:

        - **Declined.** Not the PM's meeting, whenever it falls. It is not
          written, no event is emitted for it, and it is not handed back either
          — a dashboard showing a meeting its reader has declined is showing
          them somebody else's calendar.
        - **Cancelled.** Suppressed only while it has not ended. An upcoming
          cancelled row is not a meeting the PM has; an *ended* one is a meeting
          that happened and was tidied up afterwards, and a meeting that happened
          is not undone by a later calendar edit. Its record stands and keeps
          resolving, which is what AD-33's citations rest on — and it is why
          there is no cancellation-marking rewrite here to get wrong.

        **The whole of `responseStatus.response`, since only one member of it is
        named above.** Graph sends one of six: `none`, `organizer`,
        `tentativelyAccepted`, `accepted`, `declined`, `notResponded`. Exactly
        one — `declined` — suppresses the row here. The other five are all
        meetings the PM has:

        - `organizer` is their own meeting and the strongest case of all;
        - `accepted` is the ordinary one;
        - `tentativelyAccepted` is kept and carried as `Meeting.tentative`,
          because a maybe is still an entry on the day;
        - `notResponded` and `none` are silence, and silence is not a refusal.
          A PM who never answered an invitation still attended the meeting more
          often than not, and dropping the row would hide it from the dashboard
          *and* from the Man-Hour Cost of a meeting that happened.

        An unrecognised string — a value Graph adds later, or an absent field
        arriving as `None` — falls through with the five, which is the safe
        direction: the failure mode of keeping a row is a meeting the PM can see
        and correct, and the failure mode of dropping one is a meeting nobody
        knows is missing.
        """
        if (row.response or "").strip().casefold() == "declined":
            return False
        return not (row.is_cancelled and row.end > now)

    def _meeting(self, row: CalendarRow) -> Meeting:
        """One converted row as the domain entity, or the refusal that names it.

        The only place this connector builds a `Meeting`, and `sample_events`
        goes through it too — a separate hand-written sample would drift from the
        real mapping and every gate reading it would be checking a decoration.
        """
        meeting_id = _assert_citable(row.event_id)
        return Meeting(
            meeting_id=meeting_id,
            # An untitled meeting really has no title: `_text` collapses a blank
            # subject to `None` upstream, and the empty string round-trips
            # through `11a`'s grammar unchanged. A substituted "(no subject)"
            # would be indistinguishable from a real one somebody typed.
            title=_assert_recordable_title(row.subject or "", event_id=meeting_id),
            start=row.start,
            duration_minutes=_minutes_of(row),
            attendees=_attendees_of(row),
            # Every attendee entry the provider sent, named or not — what the
            # Man-Hour Cost multiplies (story 2n). `attendees` above holds only
            # the ones with a usable address, de-duplicated.
            attendee_count=len(row.attendees),
            # The only honest source of a pm-ai scope for a Graph event.
            scope=self.categories.scope_for(row.categories),
            # The Graph event id, which `33e` resolves a join URL from when it
            # needs one. `Meeting` has no `join_url` field and this slice adds
            # none: a URL is a lookup, and storing one is storing an answer that
            # can go stale beside the id that cannot.
            calendar_event_ref=row.event_id,
            # In memory only. `render_record` writes the keys in `_FIELDS` and
            # this is not one of them, so a tentatively-accepted meeting is a
            # question the dashboard can show and no file can hold.
            tentative=(row.response or "").strip().casefold() == "tentativelyaccepted",
            # In memory only, exactly as `tentative` is. `calendar_event_ref`
            # above and `meeting_id` are both Graph's per-mailbox `id`, so a
            # meeting cross-invited to two tenants arrives under two of them;
            # this is the one identifier a surface reading both mailboxes can
            # match the two copies on.
            ical_uid=row.ical_uid,
            # In memory only, as `tentative` is (`23e`, finding 7). Only an
            # ended cancelled row gets this far — `_is_the_pms_meeting` leaves
            # out one not yet ended — and the dashboard marks it cancelled.
            cancelled=row.is_cancelled,
            # In memory only (`23e`): the date Graph gave an all-day row, by
            # which the dashboard places the marker.
            all_day_date=row.all_day_date,
            # In memory only (`23h`): the end date Graph gave it, so a
            # multi-day entry is on every date it spans.
            all_day_end_date=row.all_day_end_date,
        )

    def _to_event(self, meeting: Meeting, row: CalendarRow) -> NormalizedEvent:
        """The `CALENDAR_EVENT_HELD` an ended meeting emits.

        `attendee_count` is the number of attendee entries the provider sent,
        which is deliberately not `len(meeting.attendees)`: the list holds
        *distinct handles*, and an attendee with no usable address is left out
        of it. The meeting record stores the same count as `attendee_count`,
        and the Man-Hour Cost multiplies by it.

        The actor is the organizer's address as an `email:` handle (AD-34), or
        `none:` when the organizer has none pm-ai can use. The organizer is not
        added to the attendees.
        """
        return NormalizedEvent(
            scope=meeting.scope,
            type=ObservedEventType.CALENDAR_EVENT_HELD,
            # AD-34's scopeless grammar — a meeting belongs to no project, and
            # `_assert_citable` has already proved this parses.
            source_ref=meeting.source_ref,
            # AD-34 — what the provider said, as a person handle. The
            # organizer, because they are the one person a calendar row always
            # attributes; an absent or unusable address is `none:`, and a meeting
            # whose organizer pm-ai cannot name is still a meeting that happened.
            actor=usable_handle(
                EMAIL, row.organizer.email if row.organizer is not None else None
            )
            or NO_PERSON,
            # The provider clock (AD-35), carried as sent. A row `33b` flagged as
            # implausible is emitted anyway and `persist_events` counts it in
            # `PersistResult.flagged`: a timestamp replaced from the local clock
            # is well-formed, plausible and wrong.
            occurred_at=meeting.start,
            payload=MeetingHeldPayload(
                meeting_id=meeting.meeting_id,
                attendee_count=len(row.attendees),
                duration_minutes=meeting.duration_minutes,
            ),
            # AD-36 — a connector may NEVER assert `external`. It cannot see the
            # executed-mutation ledger, so it cannot know whether this is pm-ai's
            # own write coming back. `pm_ai.core.normalize` decides.
            authored_by=Provenance.UNKNOWN,
            # no `id`: the storage service mints the surrogate (AD-34)
        )


# ── Reading one row's fields ─────────────────────────────────────────────────


@dataclass(frozen=True)
class _Positions:
    """Where each resource's last walk got to: the cursor, read (`33d`).

    The cursor is one JSON object — `calendar` (an ISO instant, or absent),
    `channels` (channel id to ISO instant) and, from `33f`, `chats` (chat id to
    ISO instant). A stored bare ISO string is a cursor written before `33d` and
    reads as calendar only, so every channel and chat starts from a first run;
    a cursor `33d` wrote has no `chats` and reads with every chat first-run. An
    empty token is a first run for everything. Channel and chat keys are
    case-folded ids, read back case-insensitively.

    **Anything else is refused, never partly read**: a token that is not
    UTF-8, JSON that is not an object, `channels` or `chats` that is not an
    object, `calendar` or a stamp that is not an ISO string, or an instant
    that is naive or not UTC. The harvest is then FAILED, not retryable, with
    the cursor unchanged — a cursor this connector did not write needs
    clearing, and guessing which half of it to trust would be guessing where
    to resume. A `channels` or `chats` of `null` reads as no positions.

    Calendar time and channel creation time, never an `ingested_at` watermark —
    the two clocks AD-35 keeps apart.
    """

    calendar: datetime | None = None
    channels: Mapping[str, datetime] = field(default_factory=dict)
    chats: Mapping[str, datetime] = field(default_factory=dict)

    @classmethod
    def read(cls, since: Cursor) -> _Positions:
        """Raises `ValueError` for a token this connector did not write."""
        token = since.token
        if not token:
            return cls()
        text = token.decode("utf-8")
        if not text.lstrip().startswith("{"):
            return cls(calendar=_aware(datetime.fromisoformat(text)))
        document = json.loads(text)
        if not isinstance(document, dict):
            raise ValueError("a Graph cursor is an object of positions")
        calendar = document.get("calendar")
        channels = document.get("channels", {})
        if channels is None:
            channels = {}
        if not isinstance(channels, dict):
            raise ValueError("a Graph cursor's channels are an object")
        chats = document.get("chats", {})
        if chats is None:
            chats = {}
        if not isinstance(chats, dict):
            raise ValueError("a Graph cursor's chats are an object")
        return cls(
            calendar=(
                None if calendar is None else _aware(datetime.fromisoformat(calendar))
            ),
            # Keyed by the case-folded channel id, the comparison the duplicate
            # check makes; a key written in any case reads back the same.
            channels={
                str(channel).casefold(): _aware(datetime.fromisoformat(stamp))
                for channel, stamp in channels.items()
            },
            chats={
                str(chat).casefold(): _aware(datetime.fromisoformat(stamp))
                for chat, stamp in chats.items()
            },
        )

    def advanced(
        self,
        since: Cursor,
        *,
        calendar: datetime | None,
        channels: Mapping[str, datetime],
        chats: Mapping[str, datetime],
    ) -> Cursor:
        """The next cursor: each resource moved where it moved, kept where not.

        When nothing moved the stored cursor comes back byte for byte, so "the
        position does not move" stays observable as an unchanged cursor. A
        channel no longer listed keeps its position: listing it again resumes
        rather than reaching back, and the natural key drops anything twice. A
        chat is kept the same way (`33f`).
        """
        if calendar is None and not channels and not chats:
            return since
        moved_calendar = calendar if calendar is not None else self.calendar
        merged = {**self.channels, **channels}
        merged_chats = {**self.chats, **chats}
        document: dict[str, object] = {
            "calendar": None if moved_calendar is None else moved_calendar.isoformat(),
            "channels": {
                channel: stamp.isoformat() for channel, stamp in sorted(merged.items())
            },
        }
        if merged_chats:
            # Only when a chat has a position: a connector with no chats keeps
            # the bytes it wrote before `33f`.
            document["chats"] = {
                chat: stamp.isoformat() for chat, stamp in sorted(merged_chats.items())
            }
        return Cursor(json.dumps(document, sort_keys=True).encode("utf-8"))


def _aware(parsed: datetime) -> datetime:
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("a Graph position is aware UTC or it is not a position")
    return parsed


def _first_answer(
    fetched: CalendarFetch,
    walked: tuple[ChannelFetch, ...],
    chatted: tuple[ChatFetch, ...] = (),
) -> datetime | None:
    """The answer instant of the first resource that answered (AD-9, `8p`).

    The calendar is asked first, so its reading wins when it has one; a run
    whose calendar answered only with an error takes the first channel's, and
    then the first chat's.
    """
    if fetched.answered_at is not None:
        return fetched.answered_at
    for answered in (
        *(channel.answered_at for channel in walked),
        *(chat.answered_at for chat in chatted),
    ):
        if answered is not None:
            return answered
    return None


def _first_answering(
    fetched: CalendarFetch,
    walked: tuple[ChannelFetch, ...],
    chatted: tuple[ChatFetch, ...],
) -> str:
    """Which resource answered first, named for a mapping fault's reason."""
    if fetched.answered_at is not None:
        return "a calendar page"
    for channel in walked:
        if channel.answered_at is not None:
            return f"a page of channel {channel.listing.name!r}"
    for chat in chatted:
        if chat.answered_at is not None:
            return f"a page of chat {chat.listing.name!r}"
    return "a page"


def _not_attempted(
    channels: tuple[ChannelListing, ...], chats: tuple[ChatListing, ...] = ()
) -> str:
    """The sentence naming the listed channels and chats this run did not attempt."""
    parts = []
    if channels:
        named = ", ".join(repr(channel.name) for channel in channels)
        parts.append(f"channel(s) {named}")
    if chats:
        parts.append("chat(s) " + ", ".join(repr(chat.name) for chat in chats))
    if not parts:
        return ""
    return f". Not attempted this run: {'; '.join(parts)}"


def _with_skipped(
    failure: HarvestFailure | None,
    skipped: tuple[ChannelListing, ...],
    skipped_chats: tuple[ChatListing, ...] = (),
) -> HarvestFailure | None:
    """The run's failure, naming each listed channel and chat the run skipped —
    after a sign-in failure, or a chat throttled or answered with a server error."""
    if failure is None or not (skipped or skipped_chats):
        return failure
    return HarvestFailure(
        reason=f"{failure.reason.rstrip('.')}{_not_attempted(skipped, skipped_chats)}.",
        retryable=failure.retryable,
        retry_after=failure.retry_after,
        walk_cut_short=failure.walk_cut_short,
    )


def _one_failure(failures: tuple[HarvestFailure, ...]) -> HarvestFailure | None:
    """Every resource's failure as the run's one, honest about all of them.

    One passes through verbatim. Several are joined in the order the resources
    were walked — each reason names its resource — and are retryable only if
    every one is, carrying the largest retry hint any of them was given. The
    join is a channel walk cut short (`23h`) only when it is not retryable and
    every non-retryable part is one: a cut beside a throttled calendar waits
    for the next cycle, a cut beside an expired sign-in needs a person.
    """
    if not failures:
        return None
    if len(failures) == 1:
        return failures[0]
    hints = [failure.retry_after for failure in failures if failure.retry_after is not None]
    retryable = all(failure.retryable for failure in failures)
    return HarvestFailure(
        reason="; ".join(failure.reason for failure in failures),
        retryable=retryable,
        retry_after=max(hints) if hints else None,
        walk_cut_short=not retryable
        and all(failure.walk_cut_short for failure in failures if not failure.retryable),
    )


def _assert_citable(event_id: str) -> str:
    """A Graph event id that can be both a `meeting:` reference and a filename.

    Two guards rather than one, because the two consumers refuse different
    things and both refusals would otherwise land far from the row:

    - `SourceRef.parse` reads `meeting:<id>` as exactly two colon-separated
      parts, so an id carrying a colon parses as a malformed reference — out of
      `NormalizedEvent`'s constructor, past every handler in `harvest`.
    - `11a`'s `record_name` interpolates the id into a filename and refuses a
      path separator or a control character, so such a row would raise inside
      `MeetingRecords.put` — in `pm_ai.app`, with the whole batch in hand.

    Refused here as the one row it is, by name, with the walk continuing. A
    substituted id is worse than a refusal: `33c` writes a record under it and
    every extracted fact then cites a meeting that is not the one that happened.

    The length bound is the fourth guard and the one that is about neither the
    filename nor the reference. `record_name` caps the slug at 32 characters and
    appends a digest, so the *name* is bounded whatever the id is — but the id is
    also written into the record as `meeting_id=<id>`, and `11a` refuses a
    rendered field line past `MAX_ENTRY_LENGTH` for the reason a ledger line is
    bounded: a record is plaintext Markdown a PM reads, greps and diffs by hand.
    Unbounded here, a single absurd provider id raised `MalformedMeeting` inside
    `MeetingRecords.put`, in `pm_ai.app`, with the whole batch in hand.
    """
    identifier = event_id.strip()
    if not identifier or identifier != event_id:
        raise GraphRowRefused(
            f"a calendar row carries {event_id!r} as its id, which is empty or "
            f"padded. The id is a filename component and the referent of every "
            f"fact extracted from the meeting (AD-33), and two spellings of one "
            f"id are two meetings in every listing that reports them."
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in identifier):
        raise GraphRowRefused(
            f"calendar row {identifier!r} carries a control character in its id. "
            f"A newline in particular splits one record's name across two lines "
            f"in every message that reports it, including this one."
        )
    if "/" in identifier or "\\" in identifier:
        raise GraphRowRefused(
            f"calendar row {identifier!r} carries a path separator in its id. A "
            f"record is one file in `meetings/`: a nested or absolute name is "
            f"written somewhere the scope resolver was never asked about."
        )
    rendered = _field_line("meeting_id", identifier)
    if len(rendered) > MAX_ENTRY_LENGTH:
        raise GraphRowRefused(
            f"a calendar row carries an id of {len(identifier)} characters, "
            f"which renders as a {len(rendered)}-character `meeting_id=` line — "
            f"past the {MAX_ENTRY_LENGTH} a record's field line may be. A "
            f"meeting record is plaintext Markdown meant to be read, grepped and "
            f"diffed by hand, and `MeetingRecords.put` refuses the line. Refused "
            f"here as one row, because there it is raised with the whole batch "
            f"in hand and no event of any row persisted."
        )
    try:
        SourceRef.parse(f"meeting:{identifier}")
    except MalformedReference as unparsed:
        raise GraphRowRefused(
            f"calendar row {identifier!r} cannot be cited as `meeting:<id>` "
            f"under AD-34's grammar ({unparsed}). Refused rather than emitted "
            f"under a rewritten identifier: the reference is what every fact "
            f"extracted from this meeting resolves against."
        ) from unparsed
    return identifier


def _field_line(key: str, value: str) -> str:
    """One record field line as `11a` will render it, for measuring it here.

    The same two pieces `render_record` composes — the key, and `render_value`'s
    bare-or-quoted form — so the length this connector checks is the length that
    module will check, rather than an approximation that drifts the first time
    the escaping changes. `pm_ai.connectors` may not import `pm_ai.core`, so the
    shared thing is `pm_ai.domain.event_entries`, which is where both the escaper
    and the bound already live.
    """
    return f"{key}={render_value(value, where=f'field {key!r}')}"


def _assert_recordable_title(title: str, *, event_id: str) -> str:
    """A subject `11a`'s record grammar can hold, or the refusal that names the row.

    **The same guard `_assert_citable` is, for the same reason, on the other
    field a provider fills with arbitrary text.** The id was guarded and the
    title was not, which left one hole with the whole of a harvest behind it: a
    subject carrying a NUL or a bell, or a subject long enough to blow the field
    line's bound, raises `MalformedMeeting` out of `MeetingRecords.put` — in
    `pm_ai.app`, inside `run_harvest`, with the batch in hand. No record is
    written, no event is persisted and the cursor does not advance.

    And it does not clear. The window reaches *backward* on every run, so the
    same row is fetched, mapped and refused again next cycle: one meeting with a
    control character in its subject stops every harvest on the machine until the
    PM edits the calendar in Outlook, with nothing anywhere saying which row to
    edit. As one `RowRefusal` it costs that meeting and names it.

    Not the whole of C0. `render_value` escapes `\\`, `"`, `\n` and `\r`, so a
    real subject carrying a newline survives one line and round-trips — refusing
    those would refuse ordinary Graph data. Tab is ordinary in prose. What is
    left reaches the file unescaped, where it is invisible to the reader the
    plaintext record exists for and active in the terminal that prints it back.
    """
    illegal = sorted(
        {
            character
            for character in title
            if (ord(character) < 32 and character not in "\t\n\r")
            or ord(character) == 127
        }
    )
    if illegal:
        raise GraphRowRefused(
            f"calendar row {event_id!r} has a subject carrying the control "
            f"character(s) {illegal!r}. A record's field grammar escapes a "
            f"backslash, a quote, a newline and a carriage return; the rest of "
            f"C0 and DEL survive nothing a human reads and reach the terminal "
            f"that reports the record, so `MeetingRecords.put` refuses them — "
            f"and it refuses them with the whole harvest in hand."
        )
    rendered = _field_line("title", title)
    if len(rendered) > MAX_ENTRY_LENGTH:
        raise GraphRowRefused(
            f"calendar row {event_id!r} has a subject of {len(title)} "
            f"characters, which renders as a {len(rendered)}-character `title=` "
            f"line — past the {MAX_ENTRY_LENGTH} a record's field line may be. "
            f"Refused as one row rather than left to `MeetingRecords.put`, "
            f"which raises there with every other meeting in the batch and "
            f"refuses again on every run, because the harvest window reaches "
            f"backward and fetches this row again."
        )
    return title


def _minutes_of(row: CalendarRow) -> int:
    """A row's duration in whole minutes, with the all-day convention applied.

    Truncated rather than rounded, because Graph's calendar is minute-aligned and
    a rounding rule would only ever act on a row nothing produced — while
    `round` would additionally pick the even minute on a half-minute, which is a
    surprise nobody asked for in a number the Man-Hour Cost multiplies.

    A span that runs backwards is refused rather than clamped. `33b` already
    flags it — the pair is carried as the provider sent it, never reordered —
    and `Meeting`'s record refuses a negative duration for exactly the reason
    this refuses the row: a cost that subtracts is not a number any card should
    show, and a clamped zero would be indistinguishable from an all-day marker.
    """
    if row.is_all_day:
        return ALL_DAY_MINUTES
    span = row.duration
    if span < timedelta(0):
        raise GraphRowRefused(
            f"calendar row {row.event_id!r} ends at {row.end.isoformat()}, "
            f"before it starts at {row.start.isoformat()}, so it has no duration "
            f"to record. Refused rather than clamped to zero: zero is the "
            f"all-day convention and means a marker nobody sat through, which "
            f"this is not."
        )
    return int(span.total_seconds()) // 60


def _attendees_of(row: CalendarRow) -> tuple[Actor, ...]:
    """The row's attendees as `email:` handles, distinct, in the order Graph sent.

    Each `Actor` holds the handle's text (`email:<address>`, AD-34) and no
    display name — a reader decides who a handle is (`2o`). Four matrix rows
    meet here and each keeps its answer:

    - an attendee with no usable address — a null `emailAddress`, or one the
      producer refuses — is left out of the list. It is still counted:
      `Meeting.attendee_count` is every entry the provider sent;
    - duplicates are removed on the handle text after lowercasing, so
      `Dana@x.com` and `dana@x.com` are one handle — and two in the count;
    - a distribution list arrives as one attendee entry and is counted as one.
      Graph does not expand a group in `calendarView`, so pm-ai cannot know how
      many people it names;
    - zero attendees is recorded as zero. A room booking really has none, and an
      invented organizer-as-attendee would price it at one.
    """
    named: list[Actor] = []
    seen: set[str] = set()
    for attendee in row.attendees:
        handle = usable_handle(EMAIL, attendee.email)
        if handle is None:
            continue
        text = str(handle)
        if text in seen:
            continue
        seen.add(text)
        named.append(Actor(actor_id=text))
    return tuple(named)
