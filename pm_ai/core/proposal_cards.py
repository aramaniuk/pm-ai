"""One card for every surface (AD-13, story 13a).

`build_card` turns a proposal and its registered type into a `Card`: the
heading, the field lines, where it came from, what it replaced, and its
actions — each with whether it is available, why not, and the exact command.
It does no I/O: the meeting's title and start, the clock and the display zone
are handed in. A surface renders a card and never decides availability itself:
the CLI's text and JSON here, and story 5's Telegram keyboard from the same
actions.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo

from pm_ai.core.proposals import ApprovalKind, ProposalType
from pm_ai.domain.lifecycle import ProposalState
from pm_ai.domain.proposals import Proposal, TypeRule, UNKNOWN_TYPE, fold_typed

__all__ = [
    "CARD_FORMAT",
    "Card",
    "CardAction",
    "CardContext",
    "CardMeeting",
    "UNKNOWN_TYPE_SENTENCE",
    "build_card",
    "card_json",
    "card_text",
    "command_line",
    "day_words",
    "instant_json",
]

CARD_FORMAT = 1
"""The `format` of every `proposal` JSON object. A field added keeps it; one removed
or renamed changes it."""

UNKNOWN_TYPE_SENTENCE = "pm-ai does not know this type"

STATE_WORDS = {
    ProposalState.STAGED: "waiting for you",
    ProposalState.APPROVED: "approved",
    ProposalState.REJECTED: "rejected",
    ProposalState.EXPIRED: "expired, not reviewed",
    ProposalState.SUPERSEDED: "replaced",
    ProposalState.EXECUTING: "being carried out",
    ProposalState.EXECUTED: "carried out",
}
"""The state in plain words, as a card's heading says it."""

ACTIONS = ("approve", "edit", "reject", "show")


@dataclass(frozen=True, slots=True)
class CardMeeting:
    """The meeting a proposal came from, as its record was read (or not)."""

    meeting_id: str
    title: str | None = None
    start: datetime | None = None
    readable: bool = False


@dataclass(frozen=True, slots=True)
class CardContext:
    """What the card needs besides the proposal: read by the caller, never here."""

    scope: str
    now: datetime
    zone: tzinfo = timezone.utc
    meeting: CardMeeting | None = None
    replaced_by: str | None = None
    """The proposal that replaced this one by an edit, when there is one."""
    may_repeat: str | None = None
    """A live replacement in the same meeting that this item may repeat."""
    live_replacement: str | None = None
    """A waiting or approved proposal replacing this one: an expired item with
    one is not staged again."""


@dataclass(frozen=True, slots=True)
class CardAction:
    name: str
    available: bool
    reason: str | None = None
    command: tuple[str, ...] | None = None
    """The words after `pm-ai`, `--scope` and `--if-version` included."""


@dataclass(frozen=True, slots=True)
class Card:
    proposal_id: str
    type: str
    label: str
    state: str
    """The store's word, a waiting proposal past its window given as `expired`."""
    heading: str
    version: int
    scope: str
    fields: Mapping[str, str | None]
    field_lines: tuple[str, ...]
    origin: str
    meeting: CardMeeting | None
    created_at: datetime
    staged_at: datetime
    expires_at: datetime | None
    changed_at: datetime | None
    changed_via: str | None
    replaces: str | None
    replaced_by: str | None
    may_repeat: str | None
    actions: tuple[CardAction, ...] = field(default=())
    known: bool = True

    def action(self, name: str) -> CardAction | None:
        return next((action for action in self.actions if action.name == name), None)


def day_words(at: datetime, zone: tzinfo, now: datetime, *, time: bool = False) -> str:
    """`Tue 4 Nov` (the year added when it is not this year's), `10:00` with `time`."""
    local = at.astimezone(zone)
    words = f"{local:%a} {local.day} {local:%b}"
    if local.year != now.astimezone(zone).year:
        words += f" {local.year}"
    if time:
        words += f" {local:%H:%M}"
    return words


def command_line(words: Sequence[str]) -> str:
    """The words as the line a person types: `pm-ai …`, quoted to split back."""
    return "pm-ai " + shlex.join(words)


def _words(verb: str, proposal_id: str, scope: str, *rest: str) -> tuple[str, ...]:
    return ("proposal", verb, proposal_id, "--scope", scope, *rest)


def build_card(
    proposal: Proposal, proposal_type: ProposalType | None, context: CardContext
) -> Card:
    """The card for `proposal` under its registered type (or none), at `context.now`."""
    rule: TypeRule = UNKNOWN_TYPE if proposal_type is None else proposal_type.rule
    now = context.now
    seen = proposal.seen_state(rule, now)
    expires = proposal.expires_under(rule)
    waiting = proposal.waiting(rule) and seen is not ProposalState.EXPIRED
    scope = context.scope
    version = str(proposal.version)
    if proposal_type is None:
        label = "Proposal"
        fields: dict[str, str | None] = {}
        lines: tuple[str, ...] = (fold_typed(proposal.summary) or "(no summary recorded)", UNKNOWN_TYPE_SENTENCE)
    else:
        label = proposal_type.label
        fields = {
            name: None if value is None else fold_typed(value)
            for name, value in proposal_type.values(proposal.payload).items()
        }
        lines = tuple(fold_typed(line) for line in proposal_type.card(fields))
    if context.may_repeat is not None:
        lines = (*lines, f"may repeat an item you edited: {context.may_repeat}")
    heading = f"{label}, {_state_words(proposal, seen, expires, context)}"
    origin = _origin(proposal, context)
    actions = _actions(proposal, proposal_type, seen, expires, fields, context)
    return Card(
        proposal_id=proposal.proposal_id,
        type=proposal.type,
        label=label,
        state=seen.value,
        heading=heading,
        version=proposal.version,
        scope=scope,
        fields=fields,
        field_lines=lines,
        origin=origin,
        meeting=context.meeting,
        created_at=proposal.created_at,
        staged_at=proposal.staged_instant,
        expires_at=expires if waiting else None,
        changed_at=proposal.changed_at,
        changed_via=proposal.changed_via,
        replaces=proposal.replaces,
        replaced_by=context.replaced_by,
        may_repeat=context.may_repeat,
        actions=actions,
        known=proposal_type is not None,
    )


def _state_words(
    proposal: Proposal, seen: ProposalState, expires: datetime, context: CardContext
) -> str:
    now, zone = context.now, context.zone
    if seen is ProposalState.STAGED:
        return f"waiting for you until {day_words(expires, zone, now)}"
    if seen is ProposalState.EXPIRED:
        when = expires if proposal.state is ProposalState.STAGED else (proposal.changed_at or expires)
        return f"expired on {day_words(when, zone, now)}, not reviewed"
    words = STATE_WORDS[seen]
    if seen is ProposalState.SUPERSEDED and context.replaced_by:
        return f"{words} by {context.replaced_by}"
    if seen in (ProposalState.APPROVED, ProposalState.REJECTED) and proposal.changed_at:
        return f"{words} on {day_words(proposal.changed_at, zone, now)}"
    return words


def _origin(proposal: Proposal, context: CardContext) -> str:
    meeting = context.meeting
    if meeting is None:
        return f"cites {proposal.cites}, {context.scope}"
    if not meeting.readable or meeting.start is None:
        return f"from meeting {meeting.meeting_id} (meeting record unavailable), {context.scope}"
    title = fold_typed(meeting.title or "") or "(untitled)"
    when = day_words(meeting.start, context.zone, context.now, time=True)
    return f"from {title}, {when}, {context.scope}"


def _actions(
    proposal: Proposal,
    proposal_type: ProposalType | None,
    seen: ProposalState,
    expires: datetime,
    fields: Mapping[str, str | None],
    context: CardContext,
) -> tuple[CardAction, ...]:
    scope, pid, version = context.scope, proposal.proposal_id, str(proposal.version)
    show = CardAction("show", True, None, _words("show", pid, scope))
    if seen in (ProposalState.EXECUTING, ProposalState.EXECUTED):
        return (show,)
    label = "this proposal" if proposal_type is None else f"a {proposal_type.label.lower()}"
    approve_words = _words("approve", pid, scope, "--if-version", version)
    reject_words = _words("reject", pid, scope, "--if-version", version)
    edit_rest: list[str] = []
    editable = () if proposal_type is None else proposal_type.editable
    for name in editable:
        value = fields.get(name)
        if value is not None:
            edit_rest += [f"--{name}", value]
    edit_words = _words("edit", pid, scope, *edit_rest, "--if-version", version)
    no_edit = (
        f"{UNKNOWN_TYPE_SENTENCE}, so it has no field a person may edit"
        if proposal_type is None
        else f"{label} has no field a person may edit"
    )

    def unavailable(name: str, reason: str) -> CardAction:
        return CardAction(name, False, reason, None)

    if seen is ProposalState.STAGED:
        if proposal_type is None:
            approve = unavailable("approve", UNKNOWN_TYPE_SENTENCE)
        elif proposal_type.approval.kind is ApprovalKind.UNAVAILABLE:
            approve = unavailable("approve", proposal_type.approval.reason or "approval is unavailable")
        elif proposal_type.approval.kind is ApprovalKind.EXECUTOR:
            approve = unavailable(
                "approve", "approving it runs a change, which this pm-ai does not do yet"
            )
        else:
            approve = CardAction("approve", True, None, approve_words)
        edit = CardAction("edit", True, None, edit_words) if editable else unavailable("edit", no_edit)
        reject = CardAction("reject", True, None, reject_words)
        return (approve, edit, reject, show)
    if seen is ProposalState.APPROVED:
        approve = unavailable("approve", "already approved")
        edit = CardAction("edit", True, None, edit_words) if editable else unavailable("edit", no_edit)
        if proposal_type is not None and not proposal_type.rule.has_executor:
            reject = CardAction("reject", True, None, reject_words)
        else:
            reject = unavailable("reject", "an approved change is not rejected once approved")
        return (approve, edit, reject, show)
    if seen is ProposalState.EXPIRED:
        when = expires if proposal.state is ProposalState.STAGED else (proposal.changed_at or expires)
        reason = f"expired on {day_words(when, context.zone, context.now)}, not reviewed"
        if not editable:
            edit = unavailable("edit", no_edit)
        elif context.live_replacement is not None:
            edit = unavailable("edit", f"already staged again as {context.live_replacement}")
        else:
            edit = CardAction("edit", True, None, edit_words)
        return (unavailable("approve", reason), edit, unavailable("reject", reason), show)
    reason = STATE_WORDS[seen]
    if seen is ProposalState.SUPERSEDED and context.replaced_by:
        reason = f"replaced by {context.replaced_by}"
    return (
        unavailable("approve", reason),
        unavailable("edit", reason),
        unavailable("reject", reason),
        show,
    )


def card_text(card: Card, *, compact: bool = False) -> str:
    """The text card: heading, field lines, origin, what it replaced, the commands.

    `compact=True` is for the interactive review walk only (13d): the heading,
    the field lines and the origin, which are the full form's first lines,
    without the `replaces`, `staged again as` and action lines. `show`, `list`,
    the verbs' outcomes and `refusal_text` print the full form.
    """
    lines = [card.heading]
    lines += [f"  {line}" for line in card.field_lines]
    lines.append(f"  {card.origin}")
    if not compact:
        lines += _detail_lines(card)
    return "\n".join(lines) + "\n"


def _detail_lines(card: Card) -> list[str]:
    """The full form's lines after the origin: what it replaced, then the commands."""
    lines: list[str] = []
    if card.replaces:
        lines.append(f"  replaces {card.replaces}")
    if card.replaced_by and card.state != ProposalState.SUPERSEDED.value:
        lines.append(f"  staged again as {card.replaced_by}")
    waiting = card.state == ProposalState.STAGED.value
    for action in card.actions:
        if action.name == "show":
            continue
        name = f"{action.name}:".ljust(8)
        if action.available and action.command is not None:
            lines.append(f"  {name} {command_line(action.command)}")
        elif waiting and action.reason:
            lines.append(f"  {name} unavailable: {action.reason}")
    return lines


def instant_json(at: datetime | None) -> str | None:
    """An instant in ISO 8601 UTC, or `None`."""
    return None if at is None else at.astimezone(timezone.utc).isoformat()


def card_json(card: Card) -> dict[str, object]:
    """The card as the machine form's object (13a)."""
    meeting = card.meeting
    return {
        "proposal_id": card.proposal_id,
        "type": card.type,
        "label": card.label,
        "state": card.state,
        "version": card.version,
        "scope": card.scope,
        "meeting": None
        if meeting is None
        else {
            "meeting_id": meeting.meeting_id,
            "title": meeting.title if meeting.readable else None,
            "start": instant_json(meeting.start) if meeting.readable else None,
            "record": "read" if meeting.readable else "unavailable",
        },
        "fields": dict(card.fields),
        "created_at": instant_json(card.created_at),
        "staged_at": instant_json(card.staged_at),
        "expires_at": instant_json(card.expires_at),
        "changed_at": instant_json(card.changed_at),
        "changed_via": card.changed_via,
        "replaces": card.replaces,
        "replaced_by": card.replaced_by,
        "may_repeat": card.may_repeat,
        "actions": {
            action.name: {
                "available": action.available,
                "reason": action.reason,
                "command": None if action.command is None else list(action.command),
            }
            for action in card.actions
        },
    }


# ── What the queue's commands answer (13a) ───────────────────────────────────

OUTCOMES = (
    "shown",
    "approved",
    "rejected",
    "edited",
    "edited_and_approved",
    "unchanged",
    "already",
)

LIST_STATES = ("staged", "approved", "open", "rejected", "expired", "superseded", "all")
"""What `proposal list --state` takes; `staged` (waiting for the PM) is the default.

`open` is waiting or approved, the dashboard's own meaning of an open action
item, so its overflow line lists exactly what it counted (Andrei, 2026-10-23).
"""

VIA_WORDS = {
    "terminal": "from the terminal",
    "prompt": "from the prompt",
    "sweep": "by the expiry sweep",
    "summary": "by a re-summary",
    "ingestion": "by a re-ingestion",
    "telegram": "from Telegram",
}


def via_words(via: str | None) -> str | None:
    """Where a change came from, as a clause; `None` when nothing was recorded."""
    return None if via is None else VIA_WORDS.get(via, f"from {via}")


class ProposalMisused(ValueError):
    """A `proposal` command line pm-ai cannot act on: a usage error (exit 2)."""


class ProposalRefused(Exception):
    """A `proposal` command refused (exit 3), with what the refusal object carries."""

    def __init__(
        self,
        reason: str,
        *,
        state: str | None = None,
        changed_at: datetime | None = None,
        changed_via: str | None = None,
        replaced_by: str | None = None,
        card: Card | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.state = state
        self.changed_at = changed_at
        self.changed_via = changed_via
        self.replaced_by = replaced_by
        self.card = card


@dataclass(frozen=True, slots=True)
class ProposalOutcome:
    """What `show`, `approve`, `reject` and `edit` did: the outcome, the cards, the next step."""

    outcome: str
    card: Card
    sentence: str
    replaced: Card | None = None
    next: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class ProposalListing:
    """What `proposal list` found (13a)."""

    scopes: tuple[str, ...]
    filter_words: str
    cards: tuple[Card, ...] = ()
    unreadable: tuple[tuple[str, str], ...] = ()
    """Sources that could not be read: `(scope or file, reason)`."""
    unreadable_proposals: int = 0
    unscoped: int = 0
    more: int = 0
    review: tuple[str, ...] | None = None
    widen: tuple[str, ...] | None = None
    reviews: tuple[tuple[str, ...], ...] = ()
    """`proposal review` per scope with waiting cards, newest meeting's scope first;
    `review` is the first. Every printed command carries `--scope`."""
    widens: tuple[tuple[str, ...], ...] = ()
    """The listing that would show more, per scope looked at; `widen` is the first."""


def refusal_json(refused: ProposalRefused) -> dict[str, object]:
    return {
        "format": CARD_FORMAT,
        "outcome": "refused",
        "reason": refused.reason,
        "state": refused.state,
        "changed_at": instant_json(refused.changed_at),
        "changed_via": refused.changed_via,
        "replaced_by": refused.replaced_by,
        "proposal": None if refused.card is None else card_json(refused.card),
    }


def refusal_text(refused: ProposalRefused) -> str:
    """The refusal's sentence, then the current card when there is one."""
    text = refused.reason.rstrip()
    if refused.card is not None:
        text += "\n" + card_text(refused.card).rstrip("\n")
    return text


def outcome_json(answer: ProposalOutcome) -> dict[str, object]:
    return {
        "format": CARD_FORMAT,
        "outcome": answer.outcome,
        "proposal": card_json(answer.card),
        "replaced": None if answer.replaced is None else card_json(answer.replaced),
        "next": None if answer.next is None else list(answer.next),
    }


def outcome_text(answer: ProposalOutcome) -> str:
    """The card after the change, the outcome in one sentence, and the next step."""
    lines = [card_text(answer.card).rstrip("\n"), answer.sentence]
    if answer.next is not None:
        lines.append(f"next: {command_line(answer.next)}")
    return "\n".join(lines) + "\n"


def listing_json(listing: ProposalListing) -> dict[str, object]:
    return {
        "format": CARD_FORMAT,
        "scopes": list(listing.scopes),
        "unreadable": [
            {"scope": source, "reason": reason} for source, reason in listing.unreadable
        ],
        "unreadable_proposals": listing.unreadable_proposals,
        "unscoped": listing.unscoped,
        "more": listing.more,
        "next": {
            "review": None if listing.review is None else list(listing.review),
            "more": None if listing.widen is None else list(listing.widen),
        },
        "proposals": [card_json(card) for card in listing.cards],
    }


def listing_text(listing: ProposalListing) -> str:
    """One card per proposal, the counts of what was left out, then the next command."""
    lines: list[str] = [
        f"pm-ai could not read {source}: {reason}" for source, reason in listing.unreadable
    ]
    scopes = ", ".join(listing.scopes) or "no scope"
    if not listing.cards:
        lines.append(f"No proposal matches {listing.filter_words} in {scopes}.")
    for card in listing.cards:
        lines.append(card_text(card).rstrip("\n"))
    if listing.more:
        lines.append(f"and {listing.more} more not shown.")
    if listing.unreadable_proposals:
        n = listing.unreadable_proposals
        lines.append(
            f"{n} {'proposal' if n == 1 else 'proposals'} could not be read, so "
            f"{'it is' if n == 1 else 'they are'} not listed."
        )
    if listing.unscoped:
        n = listing.unscoped
        lines.append(
            f"{n} {'proposal has' if n == 1 else 'proposals have'} no known scope, so "
            f"{'it is' if n == 1 else 'they are'} not listed."
        )
    reviews = listing.reviews or (() if listing.review is None else (listing.review,))
    widens = listing.widens or (() if listing.widen is None else (listing.widen,))
    if reviews:
        lines += [f"next: {command_line(words)}" for words in reviews]
    else:
        lines += [f"to see more: {command_line(words)}" for words in widens]
    return "\n".join(lines) + "\n"
