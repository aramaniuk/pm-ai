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

Chat and channel messages are 33d and transcripts are 33e.

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

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from pm_ai.connectors.graph.calendar import (
    CalendarAttendee,
    CalendarFetch,
    CalendarRow,
    GraphCalendarFetch,
    WindowPolicy,
)
from pm_ai.domain.event_entries import MAX_ENTRY_LENGTH, render_value
from pm_ai.domain.events import (
    MeetingHeldPayload,
    NormalizedEvent,
    ObservedEventType,
    Provenance,
)
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
    resolve_actor,
)
from pm_ai.domain.meetings import Meeting

__all__ = [
    "ALL_DAY_MINUTES",
    "AuthProbe",
    "CATEGORIES_KEY",
    "CLIENT_ID_KEY",
    "MAX_SETTING_MINUTES",
    "TENANT_KEY",
    "CategoryScopes",
    "GraphConnector",
    "GraphRowRefused",
    "MissingGraphSetting",
    "PERSONAL",
    "REACH_BACK_KEY",
    "SAMPLE_ROW",
    "UnknownProject",
    "WIDTH_KEY",
    "graph_category_scopes",
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


# ── Where a record lands ─────────────────────────────────────────────────────

WIDTH_KEY = "window_width_minutes"
REACH_BACK_KEY = "first_run_reach_back_minutes"
CLIENT_ID_KEY = "client_id"
TENANT_KEY = "tenant"
CATEGORIES_KEY = "categories"
"""The keys a Graph enrolment row carries, spelled once.

They live in `connectors/<instance>.json` — application scope, Tier 1,
gitignored — and **not** in `config.toml`, whose vocabulary is closed at four
keys. These are per-machine connector settings: which Entra application this
laptop signs in through, how much calendar this PM wants read, and which of their
Outlook categories mean which project. None of that is a fact about pm-ai, and a
key in the closed vocabulary is a key every installation then has.
"""


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
    """`ConnectorPort` over `calendarView` — one method, and no cadence (AD-9).

    Holds no schedule, no cursor of its own and no HTTP: `33b`'s
    `GraphCalendarFetch` owns the wire, this owns the modelling. What it decides
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
        """Exactly one member of the core taxonomy (AD-27).

        `MESSAGE_POSTED` joins it in `33d`, which is a deliberate change here
        rather than something a connector may mint.
        """
        return frozenset({ObservedEventType.CALENDAR_EVENT_HELD})

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
        return (self._to_event(meeting, SAMPLE_ROW),)

    # ── The harvest ──────────────────────────────────────────────────────────

    def harvest(self, since: Cursor) -> HarvestResult:
        """Fetch the window, map every row, and report what actually happened.

        The order of the two halves of the result is the whole contract:
        `records` are the meetings `pm_ai.app.pipelines` writes through `11a`
        **before** it persists `events`, because every event here cites
        `meeting:<id>` and one emitted ahead of a failed record write leaves an
        unresolvable AD-33 citation.

        Coverage is passed through from the fetch and then withheld unless
        something was actually mapped. A window whose every row was cancelled or
        declined reached the provider and produced no meeting, and coverage arms
        AD-35's fail-closed reading — within a covered window, absence of
        evidence is evidence of absence. Claiming it there would let a mapping
        decision read as a kept-or-broken promise.
        """
        try:
            walked_from = _calendar_position(since)
        except ValueError:
            return HarvestResult(
                events=(),
                cursor=since,
                outcome=HarvestOutcome.FAILED,
                failure=HarvestFailure(
                    reason=(
                        f"{self.instance} was handed a stored cursor of "
                        f"{len(since.token)} byte(s) that is not an aware-UTC "
                        f"calendar instant, so there is no position to resume "
                        f"from. Nothing was fetched and the cursor is unchanged; "
                        f"a cursor this connector did not write needs clearing "
                        f"rather than waiting."
                    ),
                    retryable=False,
                ),
            )

        try:
            fetched = self.calendar.fetch(since=walked_from)
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
                    ),
                    retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
                ),
            )

        try:
            return self._result(fetched, since=since)
        except Exception as unexpected:  # noqa: BLE001 — the port promises a value
            # Everything `_result` does can fault: `resolve_actor` reads the
            # alias table, `NormalizedEvent`'s constructor validates, the clock
            # is injected and the cursor is an encode. A raise here would leave
            # `run_harvest` — whose only call has no `except` — propagating out
            # of the scheduler with the fetched page, its coverage and its
            # cursor all lost, which is exactly the shape `HarvestOutcome.FAILED`
            # exists to make expressible. The rows are not carried out with it:
            # whatever broke, this run cannot say which half of them it mapped.
            return HarvestResult(
                events=(),
                cursor=since,
                outcome=HarvestOutcome.FAILED,
                failure=HarvestFailure(
                    reason=(
                        f"{self.instance} fetched a calendar page and then "
                        f"raised {type(unexpected).__name__} while mapping it, "
                        f"which pm-ai cannot classify. Nothing was recorded and "
                        f"the cursor is unchanged, so the same window is walked "
                        f"again next run."
                    ),
                    retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
                ),
            )

    def _result(self, fetched: CalendarFetch, *, since: Cursor) -> HarvestResult:
        """One `CalendarFetch` as the `HarvestResult` the port promises.

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
        takes.
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
            # port says never raises. It resolves an actor, parses a reference
            # and runs `NormalizedEvent`'s validation — all of it on data the
            # provider sent — so it can refuse the same row `_meeting` accepted,
            # and a refusal there must cost the row rather than the batch.
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

        mapped = bool(events or records or live)
        outcome = HarvestOutcome.of(
            mapped=mapped, refused=bool(refusals), failed=fetched.failure is not None
        )

        return HarvestResult(
            events=tuple(events),
            cursor=(
                Cursor(fetched.walked_through.isoformat().encode("utf-8"))
                if fetched.walked_through is not None
                else since
            ),
            outcome=outcome,
            # Two ways a window is earned, and they are the two `HarvestResult`
            # admits: something was mapped, or the fetch completed and the
            # calendar held nothing for us — `8i`, without which a quiet
            # calendar recorded no coverage ever and no promise against it could
            # be reported broken. Spelled against `outcome`, which is
            # `HarvestOutcome.of`'s answer here, in `gitlab.harvest` and in
            # `GraphCalendarFetch.fetch`, so the three cannot drift: a FAILED
            # walk whose every row we filtered out keeps claiming nothing.
            coverage=(
                fetched.coverage if mapped or outcome is HarvestOutcome.EMPTY else None
            ),
            failure=fetched.failure,
            refusals=tuple(refusals),
            records=tuple(records),
            live=tuple(live),
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
        )

    def _to_event(self, meeting: Meeting, row: CalendarRow) -> NormalizedEvent:
        """The `CALENDAR_EVENT_HELD` an ended meeting emits.

        `attendee_count` is the number of attendee entries the provider sent,
        which is deliberately not `len(meeting.attendees)`. The record holds
        *distinct actors* and AD-34 collapses every handle pm-ai cannot resolve
        onto one `UNRESOLVED`, so a five-person meeting whose attendees are all
        strangers has one attendee in the record and five on the wire. The event
        carries the measured number; the record carries who could be named. The
        consequence for FR-03's Man-Hour Cost is real and is recorded in the
        story rather than papered over here.
        """
        return NormalizedEvent(
            scope=meeting.scope,
            type=ObservedEventType.CALENDAR_EVENT_HELD,
            # AD-34's scopeless grammar — a meeting belongs to no project, and
            # `_assert_citable` has already proved this parses.
            source_ref=meeting.source_ref,
            # AD-34 — a native handle resolves to an Actor or to UNRESOLVED,
            # never to itself. The organizer, because they are the one person a
            # calendar row always attributes; an absent one is what UNRESOLVED
            # is for, and a meeting whose organizer pm-ai cannot name is still a
            # meeting that happened.
            actor=resolve_actor(
                system="graph",
                handle=row.organizer.email if row.organizer is not None else None,
            ),
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


def _calendar_position(since: Cursor) -> datetime | None:
    """The calendar instant a previous run walked through, or `None` for a first run.

    Calendar time, not an `ingested_at` watermark — the two clocks AD-35 keeps
    apart, and `GraphCalendarFetch` takes the first. Encoded as the ISO string of
    `CalendarFetch.walked_through`, so the cursor a run writes is the position
    the next one resumes from with nothing in between to interpret it.

    Raises `ValueError` for a token that is not one. An empty token is a first
    run and is the ordinary state of a clean machine.
    """
    token = since.token
    if not token:
        return None
    parsed = datetime.fromisoformat(token.decode("utf-8"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("a calendar cursor is aware UTC or it is not a position")
    return parsed


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
    """The row's attendees as resolved actors, distinct, in the order Graph sent.

    Three matrix rows meet here and each keeps its answer:

    - a null `emailAddress` resolves to `UNRESOLVED` (AD-34) rather than to the
      raw handle, because one engineer arriving as a commit email and a calendar
      address must not become two people in a metric that feeds a review;
    - a distribution list arrives as one attendee entry and is counted as one.
      Graph does not expand a group in `calendarView`, so pm-ai cannot know how
      many people it names, and inventing a number is worse than under-counting
      one that is visibly a list;
    - zero attendees is recorded as zero. A room booking really has none, and an
      invented organizer-as-attendee would price it at one.

    **Distinct, and that is a loss worth stating.** `11a` refuses a repeated
    handle in a record — one person listed twice is two people in the Man-Hour
    Cost — and every attendee pm-ai cannot resolve collapses onto the single
    `UNRESOLVED` actor, so a meeting of five strangers has one attendee here. The
    alternative was refusing every such meeting, which on a machine with an empty
    alias table is every meeting. The event carries the provider's own count
    beside it, and the divergence is recorded rather than hidden.
    """
    resolved: list[Actor] = []
    seen: set[str] = set()
    for attendee in row.attendees:
        actor = resolve_actor(system="graph", handle=attendee.email)
        if actor.actor_id in seen:
            continue
        seen.add(actor.actor_id)
        # Handle only. `11a` stores `actor_id` and never a display name — AD-34
        # makes a display name an alias rather than an identity, and a record
        # carrying one would be a second place it could be wrong.
        resolved.append(Actor(actor_id=actor.actor_id))
    return tuple(resolved)
