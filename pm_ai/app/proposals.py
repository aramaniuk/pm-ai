"""The proposal engine: one function per verb, for the terminal, the daemon and `review` (13a).

`ProposalDesk` lists, shows, approves, rejects and edits staged proposals.
Every surface calls it: the CLI at a terminal, the daemon for a line typed at
the prompt, and `proposal review`. Nothing here says a word of its own: the
cards and their words are `pm_ai.core.proposal_cards`'.

**Reads** go by path, on read-only connections (`pm_ai.app.proposal_reads`):
the operational store's indexed columns first — for one proposal only its
row and its meeting's rows — then only the bodies of the cards shown and the
meeting records they name. No master key, keychain or model is asked for,
and `list` and `show` build no `StorageService`.

**Changes** are one claimed step each through the writer the desk was given
(`StorageService.change_proposal` and `edit_proposal`), checked by version.
On a lost compare-and-swap the desk reloads and re-evaluates, never retrying
blindly (AD-37): already in the state asked for is `already`; moved somewhere
else is refused naming where; the same state at another version is refused
under `--if-version`, and otherwise tried once more at the version just read.

**Scopes** fail closed (AD-4): a `people` scope is refused and never listed;
a proposal whose scope cannot be told is counted in `unscoped`, never listed
or offered, and refused by every verb.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone

from pm_ai.app.commitments import (
    LEDGER_READ_FAILURES,
    CommitmentRecorder,
    read_ledger,
    recorder_for,
)
from pm_ai.app.concurrency import WRITER_START_WAIT_SECONDS, WriterBusy
from pm_ai.app.meeting_listing import MeetingLister, resolve_title
from pm_ai.app.proposal_reads import ProposalWorld, open_world, seen_state
from pm_ai.app.scope_reads import read_display_config
from pm_ai.app.wiring import REGISTRY_ARTIFACT, registered_proposal_types
from pm_ai.core.commitment_log import Recording
from pm_ai.core.due_dates import due_words
from pm_ai.core.meeting_records import MeetingRecords
from pm_ai.core.meeting_summary import action_item_id
from pm_ai.core.proposal_cards import (
    LIST_STATES,
    Card,
    CardContext,
    CardMeeting,
    ProposalListing,
    ProposalMisused,
    ProposalOutcome,
    ProposalRefused,
    build_card,
    command_line,
    day_words,
    via_words,
)
from pm_ai.core.proposals import (
    ApprovalKind,
    EditMisused,
    EditRefused,
    ProposalType,
    ProposalTypes,
    SUMMARY_LIMIT,
    edited_values,
)
from pm_ai.domain.commitments import LineKind, Mark, MarkAnswer, mark_answer
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.lifecycle import ProposalState
from pm_ai.domain.meeting_listing import RecordReading
from pm_ai.domain.proposals import (
    IllegalTransition,
    Proposal,
    TerminalState,
    TypeRule,
    UNKNOWN_TYPE,
    VersionConflict,
    root_of,
)
from pm_ai.domain.storage_tiers import OPERATIONAL_DB
from pm_ai.platform.paths import ScopePaths
from pm_ai.storage.service import (
    OperationalStoreUnavailable,
    ProposalIndexRow,
    ProposalNotFound,
    ProposalsUnreadable,
    ProposalUnreadable,
    ReplacementExists,
    proposal_bodies_at,
    proposal_index_at,
)

__all__ = [
    "DEFAULT_LIMIT",
    "LIMITS",
    "MeetingChoiceRefused",
    "choose_meeting",
    "PROPOSAL_WRITE_BOUND_SECONDS",
    "ProposalDesk",
    "TERMINAL_WRITE_WAIT_SECONDS",
    "build_registry",
    "completion_choices",
]

PROPOSAL_WRITE_BOUND_SECONDS = 2.0
"""`approve`, `reject` and `edit`'s bound at the prompt (AD-21): at most 1 s for the
daemon's writer thread and 100 ms of SQLite's busy wait, with room to spare."""

TERMINAL_WRITE_WAIT_SECONDS = 1.0
"""Their busy wait at a terminal: 1 s, not the standard library's 5, so they too
answer within 2 seconds."""

LIMITS = (1, 200)
DEFAULT_LIMIT = 20
COMPLETION_CANDIDATES = 100

_LIVE = (ProposalState.STAGED, ProposalState.APPROVED)


def build_registry(*extra: ProposalType) -> ProposalTypes:
    """The registry the composition root registers (`registered_proposal_types`), then `extra`.

    A second registration of one name is refused here (AD-13, AD-30).
    """
    registry = registered_proposal_types()
    for each in extra:
        registry.register(each)
    return registry


class MeetingChoiceRefused(ProposalRefused):
    """`--meeting` or `--title [--on]` named no one meeting (11f, 13a; 15c's code).

    `kind` is `ambiguous` (the meeting has records in several scopes, or
    several meetings match the title) or `not_found` (no meeting matches), so
    a caller maps it to its own code without reading the sentence.
    """

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind


def choose_meeting(
    world: ProposalWorld,
    scope: DataScope | None,
    meeting_id: str | None,
    title: str | None,
    on: date | None,
    *,
    now: Callable[[], datetime],
    paths: ScopePaths | None,
    reachable: Callable[[], bool],
) -> tuple[DataScope, str] | None:
    """The one meeting `--meeting` or `--title [--on]` names, as `proposal list` chooses it.

    `MeetingChoiceRefused` when it names none or several. Story 15c's
    `commitment list` and `review` choose a meeting by the same rule.
    """
    desk = ProposalDesk(now=now, paths=paths, reachable=reachable)
    return desk._chosen_meeting(world, scope, meeting_id, title, on)


@dataclass(frozen=True, slots=True)
class Located:
    """One proposal found by its index row, with where it belongs."""

    row: ProposalIndexRow
    scope: DataScope


@dataclass
class ProposalDesk:
    """The engine (13a). `storage` is the writer changes go through; `via` where they come from.

    `paths` is the resolver whose application and personal trees are read —
    the daemon's own in the daemon, the process owner's home when `None`; the
    projects are those `projects.toml` enrols now. `types` is the registry the
    composition root built. `reachable` answers whether a daemon answers, for
    `--title`'s listing.
    """

    now: Callable[[], datetime]
    paths: ScopePaths | None = None
    storage: object | None = None
    via: str = "terminal"
    types: ProposalTypes = field(default_factory=lambda: registered_proposal_types())
    reachable: Callable[[], bool] = lambda: False
    commitments: CommitmentRecorder | None = None
    """What records an approved item's commitment (story 15a), built in the
    composition root; when `None`, one is built over `storage` as it is needed."""

    # ── reading ──────────────────────────────────────────────────────────

    def world(self) -> ProposalWorld:
        return open_world(self.paths, self.now())

    def rule(self, kind: str | None) -> TypeRule:
        return UNKNOWN_TYPE if kind is None else self.types.rule(kind)

    def seen(self, row: ProposalIndexRow, now: datetime) -> ProposalState:
        return seen_state(row, self.types, now)

    def _rows_around(self, world: ProposalWorld, proposal_id: str) -> tuple[ProposalIndexRow, ...]:
        """The proposal's row and its meeting's rows — replacements share the meeting."""
        try:
            own = proposal_index_at(world.store, ids=[proposal_id])
            meetings = [row.meeting_id for row in own if row.meeting_id is not None]
            if not meetings:
                return own
            return proposal_index_at(world.store, ids=[proposal_id], meetings=meetings)
        except ProposalsUnreadable as unreadable:
            raise ProposalRefused(
                f"the operational store could not be read: {unreadable}. Nothing was changed."
            ) from unreadable

    def _card(
        self,
        proposal: Proposal,
        scope: DataScope,
        world: ProposalWorld,
        rows: Sequence[ProposalIndexRow],
    ) -> Card:
        meeting = None
        meeting_id = proposal.meeting_id
        if meeting_id is not None:
            record = world.record(scope, meeting_id)
            meeting = (
                CardMeeting(meeting_id)
                if record is None
                else CardMeeting(meeting_id, record.meeting.title, record.meeting.start, True)
            )
        replacing = sorted(
            (row for row in rows if row.replaces == proposal.proposal_id),
            key=lambda row: (row.staged_at or world.now, row.proposal_id),
        )
        live = _live_in_chain(proposal.proposal_id, rows)
        return build_card(
            proposal,
            self.types.get(proposal.type),
            CardContext(
                scope=str(scope),
                now=world.now,
                zone=world.zone,
                meeting=meeting,
                replaced_by=replacing[-1].proposal_id if replacing else None,
                may_repeat=self._may_repeat(proposal, rows, world),
                live_replacement=live,
            ),
        )

    def _may_repeat(
        self, proposal: Proposal, rows: Sequence[ProposalIndexRow], world: ProposalWorld
    ) -> str | None:
        """A live replacement of the meeting staged before this root item was (13a)."""
        if (
            proposal.replaces is not None
            or root_of(proposal.proposal_id) != proposal.proposal_id
            or proposal.seen_state(self.rule(proposal.type), world.now) is not ProposalState.STAGED
            or proposal.meeting_id is None
        ):
            return None
        head = proposal.proposal_id[: proposal.proposal_id.rfind("_ai_") + 4]
        if not head.endswith("_ai_"):
            return None
        earlier = [
            row
            for row in rows
            if row.replaces is not None
            and row.proposal_id.startswith(head)
            and root_of(row.proposal_id) != proposal.proposal_id
            and self.seen(row, world.now) in _LIVE
            and row.staged_at is not None
            and row.staged_at < proposal.staged_instant
        ]
        if not earlier:
            return None
        return max(earlier, key=lambda row: (row.staged_at, row.proposal_id)).proposal_id

    def _body(self, world: ProposalWorld, proposal_id: str) -> Proposal | None:
        try:
            return proposal_bodies_at(world.store, [proposal_id]).get(proposal_id)
        except ProposalsUnreadable as unreadable:
            raise ProposalRefused(
                f"the operational store could not be read: {unreadable}."
            ) from unreadable

    # ── scope rules ──────────────────────────────────────────────────────

    def _checked_scope(
        self, scope: DataScope | None, world: ProposalWorld
    ) -> DataScope | None:
        if scope is None:
            return None
        if scope.kind not in (ScopeKind.PERSONAL, ScopeKind.PROJECT):
            raise ProposalRefused(
                f"proposals in {scope} are not read here: only `personal` and "
                f"`project:<id>` hold the proposals pm-ai lists (AD-4). Nothing was read."
            )
        if scope.kind is ScopeKind.PROJECT and scope.project_id not in world.projects:
            if world.registry_fault is not None:
                raise ProposalRefused(
                    f"{REGISTRY_ARTIFACT} could not be read ({world.registry_fault}), so "
                    f"whether {scope} is enrolled cannot be told. Nothing was read."
                )
            enrolled = ", ".join(f"project:{pid}" for pid in world.projects) or "none"
            raise ProposalRefused(
                f"{scope} is not a project projects.toml enrols (enrolled: {enrolled}). "
                f"Nothing was read."
            )
        return scope

    def _locate(
        self, proposal_id: str, scope: DataScope | None, world: ProposalWorld
    ) -> tuple[Located, Proposal, tuple[ProposalIndexRow, ...]]:
        """The proposal under `proposal_id`, refused when it cannot be acted on here."""
        self._checked_scope(scope, world)
        rows = self._rows_around(world, proposal_id)
        row = next((row for row in rows if row.proposal_id == proposal_id), None)
        if row is None:
            raise ProposalRefused(
                f"no proposal is staged under {proposal_id!r}; `pm-ai proposal list` "
                f"lists the proposals waiting for you."
            )
        body = self._body(world, proposal_id) if row.type is not None else None
        if body is None:
            raise ProposalRefused(
                f"the stored body of {proposal_id} could not be read, so it is not shown "
                f"or changed."
            )
        found = world.scope_of(row)
        if found is None:
            raise ProposalRefused(
                f"{proposal_id}'s scope cannot be told, so it is not shown or changed "
                f"(AD-4)."
            )
        if found.kind is ScopeKind.PEOPLE:
            raise ProposalRefused(
                f"{proposal_id} is in a `people` scope, which is not read here (AD-4)."
            )
        if scope is not None and found != scope:
            raise ProposalRefused(f"{proposal_id} is not in {scope}; it is in {found}.")
        if found.kind is ScopeKind.PROJECT and found.project_id not in world.projects:
            raise ProposalRefused(
                f"{proposal_id} is in {found}, which projects.toml no longer enrols."
            )
        return Located(row, found), body, rows

    # ── list ─────────────────────────────────────────────────────────────

    def listing(
        self,
        *,
        scope: DataScope | None = None,
        meeting_id: str | None = None,
        title: str | None = None,
        on: date | None = None,
        type_name: str | None = None,
        state: str = "staged",
        limit: int = DEFAULT_LIMIT,
    ) -> ProposalListing:
        """`proposal list`: the cards in `list`'s order, and what was left out (13a)."""
        if state not in LIST_STATES:
            raise ProposalMisused(
                f"`--state` takes {', '.join(LIST_STATES)}; `{state}` is none of them."
            )
        low, high = LIMITS
        if not low <= limit <= high:
            raise ProposalMisused(f"`--limit` takes a whole number, {low} to {high}.")
        world = self.world()
        scope = self._checked_scope(scope, world)
        chosen = self._chosen_meeting(world, scope, meeting_id, title, on)
        if chosen is not None:
            scope, meeting_id = chosen
        covered = [scope] if scope is not None else world.scopes
        unreadable_sources: list[tuple[str, str]] = []
        if world.registry_fault is not None and scope is None:
            unreadable_sources.append((REGISTRY_ARTIFACT, world.registry_fault))
        try:
            rows = proposal_index_at(
                world.store, meetings=None if meeting_id is None else [meeting_id]
            )
        except ProposalsUnreadable as failed_read:
            unreadable_sources.append((OPERATIONAL_DB, str(failed_read)))
            rows = ()
        for listed_scope in covered:
            view = world.view(listed_scope)
            if isinstance(view, str):
                unreadable_sources.append((str(listed_scope), view))
        covered_keys = {str(listed_scope) for listed_scope in covered}
        unreadable = 0
        unscoped = 0
        picked: list[Located] = []
        for row in rows:
            if row.type is None:
                unreadable += 1
                continue
            seen = self.seen(row, world.now)
            if not _passes(seen, state):
                continue
            if type_name is not None and row.type != type_name:
                continue
            if meeting_id is not None and row.meeting_id != meeting_id:
                continue
            found = world.scope_of(row)
            if found is None:
                unscoped += 1
                continue
            if str(found) not in covered_keys:
                continue
            picked.append(Located(row, found))
        ordered, more = self.ordered(picked, world, limit)
        bodies: dict[str, Proposal | None] = {}
        if ordered:
            try:
                bodies = proposal_bodies_at(world.store, [each.row.proposal_id for each in ordered])
            except ProposalsUnreadable as failed:
                unreadable_sources.append((OPERATIONAL_DB, str(failed)))
        cards: list[Card] = []
        for each in ordered:
            body = bodies.get(each.row.proposal_id)
            if body is None:
                unreadable += 1
                continue
            cards.append(self._card(body, each.scope, world, rows))
        filters = _filter_words(scope, meeting_id, title, on, chosen is not None)
        # Story 13a: every printed command carries `--scope` — one per scope
        # involved, the newest meeting's scope first.
        waiting_scopes = _in_order(card.scope for card in cards if card.state == "staged")
        reviews = tuple(
            ("proposal", "review", *_filter_args(DataScope.parse(waiting_scope), meeting_id))
            for waiting_scope in waiting_scopes
        )
        shown_scopes = _in_order(
            [*(card.scope for card in cards), *(str(looked) for looked in covered)]
        )
        widens: list[tuple[str, ...]] = []
        for shown_scope in shown_scopes:
            base = ("proposal", "list", *_filter_args(DataScope.parse(shown_scope), meeting_id))
            if type_name is not None:
                base += ("--type", type_name)
            if more:
                widens.append((*base, "--state", state, "--limit", str(high)))
            elif state != "all":
                widens.append((*base, "--state", "all"))
        return ProposalListing(
            scopes=tuple(sorted(covered_keys, key=lambda key: (key != "personal", key))),
            filter_words=f"--state {state}" + (f" {filters}" if filters else "")
            + (f" --type {type_name}" if type_name else ""),
            cards=tuple(cards),
            unreadable=tuple(unreadable_sources),
            unreadable_proposals=unreadable,
            unscoped=unscoped,
            more=more,
            review=reviews[0] if reviews else None,
            widen=None if reviews or not widens else widens[0],
            reviews=reviews,
            widens=() if reviews else tuple(widens),
        )

    def ordered(
        self, picked: Sequence[Located], world: ProposalWorld, limit: int | None
    ) -> tuple[list[Located], int]:
        """`list`'s order, reading only the records the shown window needs (13a).

        By meeting, newest start first; within a meeting, the recorded
        summary's order with a replacement in its root's place, then the
        others by staging time; meetings with no readable record and proposals
        with no meeting last, by staging time; ties by staging time, then id.
        Records are found by name and ordered by the day in the name; only the
        meetings in the window, and the others on its last day, are parsed.
        """
        groups: dict[tuple[str, str | None], list[Located]] = {}
        for located in picked:
            groups.setdefault((str(located.scope), located.row.meeting_id), []).append(located)
        dated: list[tuple[str, tuple[str, str | None]]] = []
        tail: list[Located] = []
        for key, members in groups.items():
            scope_text, meeting = key
            day = None if meeting is None else world.record_day(DataScope.parse(scope_text), meeting)
            if day is None:
                tail.extend(members)
            else:
                dated.append((day, key))
        dated.sort(key=lambda entry: entry[0], reverse=True)
        window: list[tuple[datetime, tuple[str, str | None]]] = []
        counted = 0
        index = 0
        beyond = 0
        while index < len(dated):
            day = dated[index][0]
            filled = limit is not None and counted >= limit
            while index < len(dated) and dated[index][0] == day:
                key = dated[index][1]
                index += 1
                if filled:
                    beyond += len(groups[key])
                    continue
                record = world.record(DataScope.parse(key[0]), str(key[1]))
                if record is None:
                    tail.extend(groups[key])
                    continue
                window.append((record.meeting.start, key))
                counted += len(groups[key])
        window.sort(key=lambda entry: (-entry[0].timestamp(), entry[1][0], entry[1][1] or ""))
        ordered: list[Located] = []
        for _, key in window:
            ordered.extend(self._within(groups[key], world))
        tail.sort(key=lambda each: _staging_key(each.row))
        ordered.extend(tail)
        if limit is None:
            return ordered, beyond
        return ordered[:limit], beyond + max(0, len(ordered) - limit)

    def _within(self, members: Sequence[Located], world: ProposalWorld) -> list[Located]:
        scope = members[0].scope
        meeting = members[0].row.meeting_id
        order: dict[str, int] = {}
        if meeting is not None:
            record = world.record(scope, meeting)
            if record is not None and record.summary is not None:
                for position, item in enumerate(record.summary.answer.action_items):
                    order.setdefault(action_item_id(scope, meeting, item), position)
        last = len(order)
        return sorted(
            members,
            key=lambda each: (
                order.get(root_of(each.row.proposal_id), last),
                _staging_key(each.row),
            ),
        )

    def _chosen_meeting(
        self,
        world: ProposalWorld,
        scope: DataScope | None,
        meeting_id: str | None,
        title: str | None,
        on: date | None,
    ) -> tuple[DataScope, str] | None:
        """The meeting `--meeting` or `--title [--on]` names, with its scope (11f, 13a)."""
        if meeting_id is not None:
            if scope is not None:
                return scope, meeting_id
            holding = [each for each in world.scopes if world.holds(each, meeting_id)]
            if len(holding) > 1:
                raise MeetingChoiceRefused(
                    "ambiguous",
                    f"meeting {meeting_id} has records in "
                    f"{', '.join(str(each) for each in holding)}; give `--scope` to say "
                    f"which. Nothing was read."
                )
            return (holding[0], meeting_id) if holding else None
        if title is None:
            return None
        lister = MeetingLister(reachable=self.reachable, now=self.now, paths=world.resolver)
        found = []
        for each in [scope] if scope is not None else world.scopes:
            try:
                _, matches = resolve_title(lister, scope=each, title=title, on=on)
            except Exception:  # noqa: BLE001 — a scope that cannot be read offers no match
                continue
            found += [(each, listed) for listed in matches if listed.record is RecordReading.READABLE]
        if not found:
            raise MeetingChoiceRefused(
                "not_found",
                f"no meeting{'' if scope is None else f' in {scope}'} has a title matching "
                f"{title!r}{'' if on is None else f' on {on.isoformat()}'}; `pm-ai transcript "
                f"list` lists the meetings."
            )
        if len(found) > 1:
            named = "; ".join(
                f"{each} {listed.meeting_id} {listed.title!r}"
                + (
                    ""
                    if listed.start is None
                    else f" {day_words(listed.start, world.zone, world.now, time=True)}"
                )
                for each, listed in found[:10]
            )
            raise MeetingChoiceRefused(
                "ambiguous",
                f"{len(found)} meetings match {title!r}: {named}. Give `--scope`, `--on` "
                f"or `--meeting` to choose one. Nothing was read."
            )
        chosen_scope, listed = found[0]
        return chosen_scope, listed.meeting_id

    # ── show, the walk and completion ────────────────────────────────────

    def show(self, proposal_id: str, *, scope: DataScope | None = None) -> ProposalOutcome:
        world = self.world()
        located, body, rows = self._locate(proposal_id, scope, world)
        card = self._card(body, located.scope, world, rows)
        return ProposalOutcome("shown", card, f"{proposal_id} is {card.heading.split(', ', 1)[-1]}.")

    def waiting(
        self,
        *,
        scope: DataScope | None = None,
        meeting_id: str | None = None,
        title: str | None = None,
        on: date | None = None,
    ) -> ProposalListing:
        """The waiting proposals in `list`'s order, at most 200: `review`'s walk.

        `more` counts the waiting ones beyond them.
        """
        return self.listing(
            scope=scope, meeting_id=meeting_id, title=title, on=on, state="staged", limit=LIMITS[1]
        )

    def choices(self, verb: str) -> tuple[tuple[str, str, str, datetime | None], ...]:
        """What `<id>` completes from for `verb`, newest meeting first, at most 100 (13a).

        `(id, "owner: what", meeting title, meeting start)`. Only proposals the
        verb can act on by the change table and the type's registration: never
        an unknown type or one whose approval is unavailable for `approve`,
        never an approved one of a type with an executor for `reject`, never a
        type with no editable field for `edit`. A proposal with no known scope,
        in a `people` scope, or with a body that cannot be read is never offered.
        """
        if verb not in _OFFERED_STATES:
            return ()
        world = self.world()
        try:
            rows = proposal_index_at(world.store)
        except ProposalsUnreadable:
            return ()
        picked: list[Located] = []
        for row in rows:
            if row.type is None:
                continue
            seen = self.seen(row, world.now)
            if not self._offered(verb, row.type, seen):
                continue
            scope = world.scope_of(row)
            if scope is None or scope.kind not in (ScopeKind.PERSONAL, ScopeKind.PROJECT):
                continue
            picked.append(Located(row, scope))
        ordered, _ = self.ordered(picked, world, COMPLETION_CANDIDATES)
        try:
            bodies = proposal_bodies_at(world.store, [each.row.proposal_id for each in ordered])
        except ProposalsUnreadable:
            return ()
        found: list[tuple[str, str, str, datetime | None]] = []
        for each in ordered:
            body = bodies.get(each.row.proposal_id)
            if body is None:
                continue
            proposal_type = self.types.get(body.type)
            values = {} if proposal_type is None else proposal_type.values(body.payload)
            what = values.get("what") or body.summary
            owner = values.get("owner")
            said = f"{owner}: {what}" if owner else what
            title, start = "", None
            if body.meeting_id is not None:
                record = world.record(each.scope, body.meeting_id)
                if record is not None:
                    title = record.meeting.title
                    start = record.meeting.start.astimezone(world.zone)
            found.append((body.proposal_id, " ".join(said.split()), title, start))
        return tuple(found)

    def _offered(self, verb: str, kind: str, seen: ProposalState) -> bool:
        if seen not in _OFFERED_STATES[verb]:
            return False
        proposal_type = self.types.get(kind)
        if verb == "approve":
            return (
                proposal_type is not None
                and proposal_type.approval.kind is ApprovalKind.AGREED
            )
        if verb == "reject":
            if seen is ProposalState.APPROVED:
                # Story 15a: an approved item whose type records a commitment
                # is not rejected.
                return (
                    proposal_type is not None
                    and not proposal_type.rule.has_executor
                    and not proposal_type.rule.records_commitment
                )
            return True
        if verb == "edit":
            return proposal_type is not None and bool(proposal_type.editable)
        return True

    # ── changes ──────────────────────────────────────────────────────────

    @contextlib.contextmanager
    def _writing(self) -> Iterator[object]:
        storage = self.storage
        if storage is None:
            raise ProposalRefused(
                "no writer was supplied for this command, so nothing was changed. That is "
                "a wiring fault in pm-ai."
            )
        wait = getattr(storage, "proposal_write_wait", None) if self.via == "terminal" else None
        try:
            if wait is None:
                yield storage
            else:
                with wait(TERMINAL_WRITE_WAIT_SECONDS):
                    yield storage
        except OperationalStoreUnavailable as busy:
            raise ProposalRefused(str(busy)) from busy
        except WriterBusy as busy:
            raise ProposalRefused(
                f"the daemon's writer was busy and did not start this change within "
                f"{WRITER_START_WAIT_SECONDS:g} s ({busy}); nothing was written. Run "
                f"the command again."
            ) from busy

    def approve(
        self, proposal_id: str, *, scope: DataScope | None = None, if_version: int | None = None
    ) -> ProposalOutcome:
        return self._change(proposal_id, ProposalState.APPROVED, scope, if_version)

    def reject(
        self, proposal_id: str, *, scope: DataScope | None = None, if_version: int | None = None
    ) -> ProposalOutcome:
        return self._change(proposal_id, ProposalState.REJECTED, scope, if_version)

    def _refuse_approval(
        self, proposal_id: str, body: Proposal, card: Card, world: ProposalWorld
    ) -> None:
        """Refuse approving a proposal its type does not let a person approve now."""
        proposal_type = self.types.get(body.type)
        if proposal_type is None:
            raise ProposalRefused(
                f"{proposal_id} cannot be approved: pm-ai does not know its type "
                f"{body.type!r}. Reject it instead.",
                state=card.state,
                card=card,
            )
        kind = proposal_type.approval.kind
        if kind is ApprovalKind.UNAVAILABLE:
            raise ProposalRefused(
                f"{proposal_id} cannot be approved: "
                f"{proposal_type.approval.reason or 'approval is unavailable'}.",
                state=card.state,
                card=card,
            )
        if kind is ApprovalKind.EXECUTOR:
            raise ProposalRefused(
                f"{proposal_id} cannot be approved here: approving it runs a change, which "
                f"this pm-ai does not do yet.",
                state=card.state,
                card=card,
            )

    def _change(
        self,
        proposal_id: str,
        to: ProposalState,
        scope: DataScope | None,
        if_version: int | None,
    ) -> ProposalOutcome:
        world = self.world()
        located, body, rows = self._locate(proposal_id, scope, world)
        proposal_type = self.types.get(body.type)
        rule = self.rule(body.type)
        verb = "approved" if to is ProposalState.APPROVED else "rejected"
        if body.state is to:
            return self._already(body, located.scope, world, rows, verb)
        if (
            to is ProposalState.REJECTED
            and body.state is ProposalState.APPROVED
            and rule.records_commitment
        ):
            raise self._not_rejected(body, located.scope, world, rows)
        if to is ProposalState.APPROVED and body.seen_state(rule, world.now) is ProposalState.STAGED:
            self._refuse_approval(proposal_id, body, self._card(body, located.scope, world, rows), world)
        expected = body.version if if_version is None else if_version
        retried = False
        while True:
            try:
                with self._writing() as storage:
                    moved: Proposal = storage.change_proposal(  # type: ignore[attr-defined]
                        proposal_id, to, expected_version=expected, rule=rule, via=self.via
                    )
            except ProposalNotFound as gone:
                raise ProposalRefused(f"no proposal is staged under {proposal_id!r}.") from gone
            except ProposalUnreadable as unreadable:
                raise ProposalRefused(str(unreadable)) from unreadable
            except (TerminalState, VersionConflict, IllegalTransition) as lost:
                world = self.world()
                rows = self._rows_around(world, proposal_id)
                current = self._body(world, proposal_id)
                if current is None:
                    raise ProposalRefused(
                        f"the stored body of {proposal_id} could not be read after a "
                        f"change was refused, so nothing more was tried."
                    ) from lost
                if current.state is to:
                    return self._already(current, located.scope, world, rows, verb)
                seen = current.seen_state(rule, world.now)
                if seen is not body.state or isinstance(lost, (TerminalState, IllegalTransition)):
                    raise self._moved(current, located.scope, world, rows, seen) from lost
                card = self._card(current, located.scope, world, rows)
                if if_version is not None or retried:
                    raise ProposalRefused(
                        f"{proposal_id} changed since version {expected} was shown: it is at "
                        f"version {current.version} now. Nothing was changed; this is the "
                        f"current card.",
                        state=card.state,
                        changed_at=current.changed_at,
                        changed_via=current.changed_via,
                        card=card,
                    ) from lost
                expected, retried = current.version, True
                continue
            break
        # Story 15a: the approval is committed; its commitment's line follows,
        # in its own claimed step, never inside the proposal's.
        recording = (
            self._record(moved, located.scope, world, repair=False)
            if to is ProposalState.APPROVED
            else None
        )
        world = self.world()
        rows = self._rows_around(world, proposal_id)
        card = self._card(moved, located.scope, world, rows)
        sentence = f"{proposal_id} {verb}."
        if to is ProposalState.APPROVED and proposal_type is not None:
            sentence = _approved_sentence(proposal_id, recording, rerun=None)
        return ProposalOutcome(
            verb,
            card,
            sentence,
            next=self._next(moved, located.scope, world, rows),
            commitment=recording,
        )

    def _recorder(self, world: ProposalWorld) -> CommitmentRecorder | None:
        """The composition root's recorder, else one over this desk's writer (15a)."""
        if self.commitments is not None:
            return self.commitments
        if self.storage is None:
            return None
        zone_name, _, _ = read_display_config(world.resolver)
        return recorder_for(
            self.storage,
            MeetingRecords(self.storage),  # type: ignore[arg-type]
            types=self.types,
            now=self.now,
            display_timezone=zone_name,
        )

    def _record(
        self, proposal: Proposal, scope: DataScope, world: ProposalWorld, *, repair: bool
    ) -> Recording | None:
        """The commitment of an approved `proposal`, recorded after its change (15a)."""
        if not self.rule(proposal.type).records_commitment:
            return None
        recorder = self._recorder(world)
        if recorder is None:
            return None
        return recorder.record(proposal, scope, repair=repair)

    def _not_rejected(
        self,
        body: Proposal,
        scope: DataScope,
        world: ProposalWorld,
        rows: Sequence[ProposalIndexRow],
    ) -> ProposalRefused:
        """The refusal of `reject` on an approved item that is a commitment now (15a, 15c).

        It names the commitment commands `mark_answer` allows from the state in
        force, read once from the ledger: `withdrawn` and `altered`, then `proposal
        edit` always. With no line in the ledger for the root, the repair instead;
        with the ledger unreadable, both marks, unchecked.
        """
        card = self._card(body, scope, world, rows)
        when = (
            ""
            if body.changed_at is None
            else f" on {day_words(body.changed_at, world.zone, world.now)}"
        )
        pid = body.proposal_id
        root = root_of(pid)
        scope_words = ("--scope", str(scope))
        withdrawn = ("commitment", "withdrawn", root, *scope_words)
        altered = ("commitment", "altered", root, *scope_words, "--note", "…")
        edit = ("proposal", "edit", pid, *scope_words, "--what", "…")
        approve = ("proposal", "approve", pid, *scope_words)
        sentences = [
            f"{pid} was approved{when} and is a commitment now, so it is not rejected; "
            f"nothing was changed."
        ]
        commands: dict[str, list[str]] = {}
        recorded: bool | None
        held = None
        details = None
        try:
            ledger = read_ledger(self.storage, world.resolver, scope)
        except LEDGER_READ_FAILURES:  # an unreadable ledger offers both marks, unchecked
            recorded = None
            sentences.append("The ledger could not be read, so these may be refused:")
            sentences.append(f"If approving it was a mistake: {command_line(withdrawn)}.")
            sentences.append(f"If what was agreed has changed: {command_line(altered)}.")
            commands["withdrawn"] = list(withdrawn)
            commands["altered"] = list(altered)
        else:
            held = ledger.commitments.get(root)
            details = ledger.details.get(root)
            recorded = held is not None
            if held is None:
                sentences.append(
                    f"It is not recorded in the ledger yet: {command_line(approve)} records it."
                )
                commands["approve"] = list(approve)
            else:
                if mark_answer(held.state, held.state_source, Mark.WITHDRAWN) is MarkAnswer.YES:
                    sentences.append(f"If approving it was a mistake: {command_line(withdrawn)}.")
                    commands["withdrawn"] = list(withdrawn)
                if mark_answer(held.state, held.state_source, Mark.ALTERED) is MarkAnswer.YES:
                    sentences.append(f"If what was agreed has changed: {command_line(altered)}.")
                    commands["altered"] = list(altered)
        sentences.append(f"To correct what was recorded: {command_line(edit)}.")
        commands["edit"] = list(edit)
        return ProposalRefused(
            " ".join(sentences),
            state=ProposalState.APPROVED.value,
            changed_at=body.changed_at,
            changed_via=body.changed_via,
            card=card,
            commitment={
                "commitment_id": root,
                "scope": str(scope),
                "recorded": recorded,
                "show": None
                if held is None or details is None
                else ["commitment", "show", root, *scope_words],
                "commands": commands,
            },
        )

    def _already(
        self,
        current: Proposal,
        scope: DataScope,
        world: ProposalWorld,
        rows: Sequence[ProposalIndexRow],
        verb: str,
    ) -> ProposalOutcome:
        # Story 15a: an approve that finds the item approved writes its
        # commitment's line if an earlier approval left it unwritten.
        recording = (
            self._record(current, scope, world, repair=True)
            if verb == "approved" and current.state is ProposalState.APPROVED
            else None
        )
        card = self._card(current, scope, world, rows)
        clauses = [f"already {verb}"]
        if current.changed_at is not None:
            clauses.append(day_words(current.changed_at, world.zone, world.now, time=True))
        where = via_words(current.changed_via)
        if where is not None:
            clauses.append(where)
        head = f"{current.proposal_id}: " + ", ".join(clauses)
        if recording is not None and recording.wrote is not None:
            sentence = f"{head}; now recorded as a commitment, {_due(recording)}."
        elif recording is not None and not recording.recorded:
            sentence = f"{head}; {_unrecorded(recording, rerun=None)}"
        else:
            sentence = f"{head}; nothing changed."
        return ProposalOutcome(
            "already",
            card,
            sentence,
            next=self._next(current, scope, world, rows),
            commitment=recording,
        )

    def _moved(
        self,
        current: Proposal,
        scope: DataScope,
        world: ProposalWorld,
        rows: Sequence[ProposalIndexRow],
        seen: ProposalState,
    ) -> ProposalRefused:
        """The refusal for a proposal that moved somewhere else: where, when, from where."""
        card = self._card(current, scope, world, rows)
        rule = self.rule(current.type)
        clauses = [f"{current.proposal_id} is {card.heading.split(', ', 1)[-1]}"]
        if seen is ProposalState.EXPIRED and current.state is ProposalState.STAGED:
            clauses = [
                f"{current.proposal_id} expired on "
                f"{day_words(current.expires_under(rule), world.zone, world.now)}, not reviewed"
            ]
        elif current.changed_at is not None and seen is current.state:
            clauses.append(day_words(current.changed_at, world.zone, world.now, time=True))
        where = via_words(current.changed_via)
        if where is not None and seen is current.state:
            clauses.append(where)
        sentence = ", ".join(clauses) + "; nothing was changed."
        if seen is ProposalState.SUPERSEDED and card.replaced_by is None:
            meeting = current.meeting_id
            if meeting is not None:
                sentence += (
                    f" A re-summary replaced it; `pm-ai proposal list --scope {scope} "
                    f"--meeting {meeting}` lists the meeting's items now."
                )
        elif card.replaced_by is not None and seen is ProposalState.SUPERSEDED:
            sentence += f" Its replacement is {card.replaced_by}."
        if seen is ProposalState.EXPIRED:
            edit = card.action("edit")
            if edit is not None and edit.available and edit.command is not None:
                sentence += " Editing it stages it again: `pm-ai " + " ".join(edit.command[:5]) + " …`."
        return ProposalRefused(
            sentence,
            state=seen.value,
            changed_at=current.changed_at,
            changed_via=current.changed_via,
            replaced_by=card.replaced_by,
            card=card,
        )

    def _next(
        self,
        changed: Proposal,
        scope: DataScope,
        world: ProposalWorld,
        rows: Sequence[ProposalIndexRow],
    ) -> tuple[str, ...]:
        """The next waiting proposal of the same meeting, else the scope's listing."""
        meeting = changed.meeting_id
        if meeting is not None:
            candidates = [
                Located(row, scope)
                for row in rows
                if row.meeting_id == meeting
                and row.proposal_id != changed.proposal_id
                and row.type is not None
                and self.seen(row, world.now) is ProposalState.STAGED
                and world.scope_of(row) == scope
            ]
            if candidates:
                first, _ = self.ordered(candidates, world, 1)
                if first:
                    return ("proposal", "show", first[0].row.proposal_id, "--scope", str(scope))
        return ("proposal", "list", "--scope", str(scope))

    def edit(
        self,
        proposal_id: str,
        *,
        changes: Mapping[str, str],
        approve: bool = False,
        scope: DataScope | None = None,
        if_version: int | None = None,
    ) -> ProposalOutcome:
        """`proposal edit`: supersede with a replacement, or stage an expired item again (13a)."""
        return self._edit(proposal_id, changes, approve, scope, if_version, write=True)

    def preview_edit(
        self, proposal_id: str, *, changes: Mapping[str, str], scope: DataScope | None = None
    ) -> ProposalOutcome:
        """The replacement's card as an edit would stage it, nothing written (`review`)."""
        return self._edit(proposal_id, changes, False, scope, None, write=False)

    def _edited(
        self, proposal_type: ProposalType, body: Proposal, changes: Mapping[str, str], card: Card
    ) -> tuple[dict[str, str | None], dict[str, str | None], dict[str, object], str]:
        """The values before and after the PM's `changes`, the new payload and summary.

        Rebuilt from `body` each time, so a retry after another writer's change
        keeps that change and re-applies only the fields the PM gave.
        """
        current = proposal_type.values(body.payload)
        try:
            values = edited_values(proposal_type, current, changes)
        except EditMisused as misused:
            raise ProposalMisused(str(misused)) from misused
        except EditRefused as refused:
            raise ProposalRefused(str(refused), state=card.state, card=card) from refused
        payload: dict[str, object] = dict(body.payload) if isinstance(body.payload, Mapping) else {}
        payload.update(values)
        summary = (values.get("what") or body.summary)[:SUMMARY_LIMIT]
        return current, values, payload, summary

    def _edit(
        self,
        proposal_id: str,
        changes: Mapping[str, str],
        approve: bool,
        scope: DataScope | None,
        if_version: int | None,
        *,
        write: bool,
    ) -> ProposalOutcome:
        world = self.world()
        located, body, rows = self._locate(proposal_id, scope, world)
        proposal_type = self.types.get(body.type)
        rule = self.rule(body.type)
        card = self._card(body, located.scope, world, rows)
        if proposal_type is None:
            raise ProposalRefused(
                f"{proposal_id} cannot be edited: pm-ai does not know its type {body.type!r}.",
                state=card.state,
                card=card,
            )
        if not proposal_type.editable:
            raise ProposalRefused(
                f"{proposal_id} cannot be edited: a {proposal_type.label.lower()} has no "
                f"field a person may edit. Reject it, or make the change yourself.",
                state=card.state,
                card=card,
            )
        seen = body.seen_state(rule, world.now)
        if seen not in (*_LIVE, ProposalState.EXPIRED):
            raise self._moved(body, located.scope, world, rows, seen)
        if seen is ProposalState.EXPIRED:
            edit_action = card.action("edit")
            if edit_action is not None and not edit_action.available:
                raise ProposalRefused(
                    f"{proposal_id} cannot be staged again: {edit_action.reason}.",
                    state=seen.value,
                    replaced_by=card.replaced_by,
                    card=card,
                )
        if approve:
            self._refuse_approval(proposal_id, body, card, world)
        current, values, payload, summary = self._edited(proposal_type, body, changes, card)
        if values == current:
            if approve and write and seen is ProposalState.STAGED:
                # The values are already right, and `--approve` was asked for:
                # approve it as it stands, rather than dropping the request.
                return self._change(proposal_id, ProposalState.APPROVED, located.scope, if_version)
            return ProposalOutcome(
                "unchanged",
                card,
                f"{proposal_id} already holds those values; nothing was written.",
                next=self._next(body, located.scope, world, rows),
            )
        if not write:
            preview = replace(
                body,
                proposal_id=f"{root_of(proposal_id)}_e…",
                payload=payload,
                summary=summary,
                state=ProposalState.STAGED,
                version=1,
                staged_at=world.now,
                replaces=proposal_id,
            )
            return ProposalOutcome(
                "unchanged",
                self._card(preview, located.scope, world, rows),
                "the replacement as it would be staged; nothing was written.",
            )
        expected = body.version if if_version is None else if_version
        retried = False
        while True:
            try:
                with self._writing() as storage:
                    edited = storage.edit_proposal(  # type: ignore[attr-defined]
                        proposal_id,
                        expected_version=expected,
                        payload=payload,
                        summary=summary,
                        approve=approve,
                        rule=rule,
                        via=self.via,
                    )
            except ProposalNotFound as gone:
                raise ProposalRefused(f"no proposal is staged under {proposal_id!r}.") from gone
            except ProposalUnreadable as unreadable:
                raise ProposalRefused(str(unreadable)) from unreadable
            except ReplacementExists as exists:
                world = self.world()
                rows = self._rows_around(world, proposal_id)
                raise ProposalRefused(
                    f"{proposal_id} expired and was already staged again as "
                    f"{exists.replacement}; nothing was written.",
                    state=ProposalState.EXPIRED.value,
                    replaced_by=exists.replacement,
                    card=self._card(body, located.scope, world, rows),
                ) from exists
            except (TerminalState, VersionConflict, IllegalTransition) as lost:
                world = self.world()
                rows = self._rows_around(world, proposal_id)
                now_body = self._body(world, proposal_id)
                if now_body is None:
                    raise ProposalRefused(
                        f"the stored body of {proposal_id} could not be read after the edit "
                        f"was refused, so nothing more was tried."
                    ) from lost
                now_seen = now_body.seen_state(rule, world.now)
                if now_seen is not seen or isinstance(lost, (TerminalState, IllegalTransition)):
                    raise self._moved(now_body, located.scope, world, rows, now_seen) from lost
                now_card = self._card(now_body, located.scope, world, rows)
                if if_version is not None or retried:
                    raise ProposalRefused(
                        f"{proposal_id} changed since version {expected} was shown: it is at "
                        f"version {now_body.version} now. Nothing was written; this is the "
                        f"current card.",
                        state=now_card.state,
                        changed_at=now_body.changed_at,
                        changed_via=now_body.changed_via,
                        card=now_card,
                    ) from lost
                # Rebuilt from the row just read: another writer's change to a
                # field the PM did not give is kept.
                _, values, payload, summary = self._edited(proposal_type, now_body, changes, now_card)
                expected, retried = now_body.version, True
                continue
            break
        # Story 15a: an approved replacement — by `--approve`, or because an
        # approved item was edited — records its commitment's line.
        recording = (
            self._record(edited.new, located.scope, world, repair=False)
            if edited.new.state is ProposalState.APPROVED
            else None
        )
        world = self.world()
        rows = self._rows_around(world, proposal_id)
        new_card = self._card(edited.new, located.scope, world, rows)
        old_card = self._card(edited.old, located.scope, world, rows)
        outcome = "edited_and_approved" if approve else "edited"
        if seen is ProposalState.EXPIRED:
            sentence = (
                f"{proposal_id} expired and is staged again as {edited.new.proposal_id}"
                + (", approved" if approve else "")
                + "; the expired one is left as it was."
            )
        else:
            sentence = f"{proposal_id} replaced by {edited.new.proposal_id}" + (
                ", approved." if approve else "."
            )
        if recording is not None:
            rerun = ("proposal", "approve", edited.new.proposal_id, "--scope", str(located.scope))
            sentence += " " + _edited_commitment(edited.new.proposal_id, recording, rerun=rerun)
        return ProposalOutcome(
            outcome,
            new_card,
            sentence,
            replaced=old_card,
            next=self._next(edited.new, located.scope, world, rows),
            commitment=recording,
        )


def _due(recording: Recording) -> str:
    return due_words(recording.due, day=recording.day)


def _unrecorded(recording: Recording, *, rerun: tuple[str, ...] | None) -> str:
    """`it could not be recorded as a commitment yet (…)`, and what to do (15a)."""
    reason = recording.reason
    text = "" if reason is None else f" ({reason.text})"
    if reason is not None and reason.permanent:
        return f"it could not be recorded as a commitment{text}. pm-ai cannot record it."
    again = "Run the same command again" if rerun is None else f"Run `{command_line(rerun)}`"
    return (
        f"it could not be recorded as a commitment yet{text}. {again}, or pm-ai records it "
        f"when the daemon next starts."
    )


def _approved_sentence(
    proposal_id: str, recording: Recording | None, *, rerun: tuple[str, ...] | None
) -> str:
    """What an approval says (story 15a)."""
    if recording is None:
        return f"{proposal_id} approved: you agreed to it; nothing is carried out."
    if recording.recorded:
        return (
            f"{proposal_id} approved: you agreed to it, and it is recorded as a commitment, "
            f"{_due(recording)}."
        )
    return f"{proposal_id} approved; {_unrecorded(recording, rerun=rerun)}"


def _edited_commitment(replacement: str, recording: Recording, *, rerun: tuple[str, ...]) -> str:
    """The sentence an approved replacement adds about its commitment (story 15a)."""
    if not recording.recorded:
        found = _unrecorded(recording, rerun=rerun)
        return found[0].upper() + found[1:]
    if recording.wrote is LineKind.REVISED:
        return f"Its commitment now holds the edited values, {_due(recording)}."
    return f"It is recorded as a commitment, {_due(recording)}."


def _live_in_chain(proposal_id: str, rows: Sequence[ProposalIndexRow]) -> str | None:
    """The newest waiting or approved proposal down the replacement chain from one (13a).

    The store's own rule (`edit_proposal`): `e1` superseded by a waiting `e2`
    still counts, so an expired item with one is not offered for staging again.
    """
    children: dict[str, list[ProposalIndexRow]] = {}
    for row in rows:
        if row.replaces is not None:
            children.setdefault(row.replaces, []).append(row)
    live: list[ProposalIndexRow] = []
    frontier = [proposal_id]
    seen: set[str] = set()
    while frontier:
        parent = frontier.pop()
        for child in children.get(parent, ()):
            if child.proposal_id in seen:
                continue
            seen.add(child.proposal_id)
            frontier.append(child.proposal_id)
            if child.state in (ProposalState.STAGED.value, ProposalState.APPROVED.value):
                live.append(child)
    if not live:
        return None
    return max(live, key=lambda row: (row.staged_at or datetime.min.replace(tzinfo=timezone.utc), row.proposal_id)).proposal_id


def _in_order(scopes: Sequence[str] | Iterator[str]) -> list[str]:
    found: list[str] = []
    for each in scopes:
        if each not in found:
            found.append(each)
    return found


def _passes(seen: ProposalState, state: str) -> bool:
    if state == "all":
        return True
    if state == "open":
        return seen in (ProposalState.STAGED, ProposalState.APPROVED)
    return seen.value == state


def _staging_key(row: ProposalIndexRow) -> tuple[str, str]:
    staged = row.staged_at.astimezone(timezone.utc).isoformat() if row.staged_at else ""
    return (staged, row.proposal_id)


def _filter_args(scope: DataScope | None, meeting_id: str | None) -> tuple[str, ...]:
    words: tuple[str, ...] = ()
    if scope is not None:
        words += ("--scope", str(scope))
    if meeting_id is not None:
        words += ("--meeting", meeting_id)
    return words


def _filter_words(
    scope: DataScope | None,
    meeting_id: str | None,
    title: str | None,
    on: date | None,
    chosen: bool,
) -> str:
    words = list(_filter_args(scope, meeting_id))
    if title is not None and not chosen:
        words += ["--title", title]
    if on is not None and not chosen:
        words += ["--on", on.isoformat()]
    return " ".join(words)


_OFFERED_STATES: Mapping[str, tuple[ProposalState, ...]] = {
    "approve": (ProposalState.STAGED,),
    "reject": (ProposalState.STAGED, ProposalState.APPROVED),
    "edit": (ProposalState.STAGED, ProposalState.APPROVED, ProposalState.EXPIRED),
    "show": tuple(ProposalState),
}
"""The proposals each verb's `<id>` completes from, before the type's own rules (13a)."""


def completion_choices(
    verb: str,
    *,
    now: datetime,
    paths: ScopePaths | None = None,
    types: ProposalTypes | None = None,
) -> tuple[tuple[str, str, str, datetime | None], ...]:
    """`ProposalDesk.choices` over the registry the composition root registers (13a)."""
    desk = ProposalDesk(
        now=lambda: now,
        paths=paths,
        types=types if types is not None else registered_proposal_types(),
    )
    return desk.choices(verb)
