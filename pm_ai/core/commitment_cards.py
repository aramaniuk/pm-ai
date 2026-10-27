"""One card for a commitment, for every surface (story 15c, AD-13's rule for cards).

`build_card` turns a `Commitment` and its `Details` (the fold's version, the
PM's mark in force and where the due and owner came from) into a `Card`: the
heading, the field line, where it came from, the PM's note, and its actions —
each with whether it is available, why not, and the exact command. It does
no I/O: the scope, the clock and the display zone are handed in.
Availability is decided here alone, from `mark_answer`, so no surface
decides it itself.

The full text card is what `show`, `list`, every verb, every refusal and
every `--json` object carry; the compact form — the heading, the field line,
the origin and the note — is for `commitment review`'s walk only (13d's
lesson).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, tzinfo

from pm_ai.core.commitment_log import CommitmentLedger, Details, MarkInForce
from pm_ai.core.proposal_cards import (
    CardAction,
    CardCommitment,
    Fill,
    FilledCommand,
    command_line,
    day_words,
    fill_json,
    filled_json,
    instant_json,
    via_words,
)
from pm_ai.core.proposals import IMPLICIT_COMMITMENT
from pm_ai.domain.commitments import (
    DUE_NONE,
    DUE_UNRESOLVED,
    UNDO_WINDOW,
    Commitment,
    LineKind,
    Mark,
    MarkAnswer,
    StateSource,
    mark_answer,
)
from pm_ai.domain.lifecycle import CommitmentState
from pm_ai.domain.proposals import fold_typed

__all__ = [
    "ACTIONS",
    "CARD_FORMAT",
    "CLOSED_STATES",
    "Card",
    "CommitmentListing",
    "CommitmentMisused",
    "CommitmentOutcome",
    "CommitmentRefused",
    "LIST_STATES",
    "LedgerCounts",
    "MARK_VERBS",
    "OUTCOMES",
    "PLACEHOLDERS",
    "PROJECT_PAGE_PAST_DUE",
    "PageParts",
    "REFUSAL_CODES",
    "TEAM_ROW_PAST_DUE",
    "TEAM_ROW_STATES",
    "bad_lines_words",
    "build_card",
    "card_json",
    "card_text",
    "change_words",
    "closed_reason",
    "closed_state_word",
    "closed_words",
    "date_words",
    "due_text",
    "is_placeholder",
    "listing_json",
    "listing_text",
    "mark_words",
    "open_order",
    "outcome_json",
    "outcome_text",
    "page_due",
    "page_parts",
    "past_due",
    "placeholder_words",
    "proposal_commitment",
    "refusal_json",
    "refusal_text",
    "reopen_command",
    "said_due_words",
    "team_row_words",
    "undo_until",
    "valid_version",
]

CARD_FORMAT = 1
"""The `format` of every `commitment` JSON object. A field added keeps it; one
removed or renamed changes it."""

ACTIONS = ("done", "altered", "broken", "withdrawn", "due", "owner", "undo", "show")

MARK_VERBS: dict[str, Mark] = {
    "done": Mark.FULFILLED,
    "altered": Mark.ALTERED,
    "broken": Mark.BROKEN,
    "withdrawn": Mark.WITHDRAWN,
}
"""The verbs that mark: the words the PM says about a commitment, one `marked` state each."""

LIST_STATES = ("open", "done", "broken", "withdrawn", "all")
"""What `commitment list --state` takes; `open` (not closed) is the default."""

OUTCOMES = ("shown", "marked", "already", "due_set", "owner_set", "altered", "undone", "unchanged")

REFUSAL_CODES = (
    "not_found",
    "ambiguous",
    "scope_refused",
    "not_enrolled",
    "ledger_unreadable",
    "malformed",
    "owner_is_handle",
    "mark_refused",
    "changed",
    "due_unresolved",
    "what_not_editable",
    "edit_refused",
    "busy",
    "git_not_excluded",
    "disk",
    "commitment_closed",
    "undo_expired",
    "undo_superseded",
    "nothing_to_undo",
    "no_terminal",
)

_MARK_WORD = {
    Mark.FULFILLED: "done",
    Mark.ALTERED: "altered",
    Mark.BROKEN: "broken",
    Mark.WITHDRAWN: "withdrawn",
}

_STATE_WORD = {
    CommitmentState.FULFILLED: "done",
    CommitmentState.BROKEN: "broken",
    CommitmentState.WITHDRAWN: "withdrawn",
    CommitmentState.ALTERED: "altered",
    CommitmentState.PENDING: "open",
    CommitmentState.UNKNOWN: "unknown",
    CommitmentState.ERROR: "in error",
}


_VERSION = re.compile(r"cml_[0-9a-f]{12}")


def valid_version(text: str) -> bool:
    """`cml_` plus 12 lower-case hex characters: a commitment's version (15c).

    Anything else given as `--if-version` is a usage error, never `changed`.
    """
    return _VERSION.fullmatch(text) is not None


def mark_words(mark: Mark) -> str:
    """The PM's word for a mark: `done` for `fulfilled`."""
    return _MARK_WORD[mark]


def date_words(day: date, now: datetime, zone: tzinfo) -> str:
    """`Fri 30 Oct`, the year added when it is not this year's."""
    words = f"{day:%a} {day.day} {day:%b}"
    if day.year != now.astimezone(zone).year:
        words += f" {day.year}"
    return words


def past_due(commitment: Commitment, today: date) -> bool:
    """Past due only when open, dated, not estimated, and before `today` (15c).

    A commitment due today is not past due; an estimated, `unresolved` or
    `none` due never is, and a closed commitment never is. Shown, never acted on.
    """
    found = commitment.due.date
    return (
        not commitment.state.is_terminal
        and found is not None
        and not commitment.due.estimated
        and found < today
    )


# ── The card ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Card:
    """One commitment as every surface shows it (story 15c)."""

    commitment: Commitment
    details: Details
    scope: str
    also_in: tuple[str, ...]
    heading: str
    field_line: str
    origin: str
    note: str | None
    past_due: bool
    due_said: str | None
    changed: str
    """One sentence for the newest line: what changed it last (`change_words`)."""
    due_words: str = ""
    """The due in force in words (`due_text`): what a prompt shows as its current value."""
    actions: tuple[CardAction, ...] = field(default=())

    @property
    def commitment_id(self) -> str:
        return self.commitment.commitment_id

    @property
    def version(self) -> str:
        return self.details.version

    @property
    def state(self) -> CommitmentState:
        return self.commitment.state

    @property
    def is_open(self) -> bool:
        return not self.commitment.state.is_terminal

    def action(self, name: str) -> CardAction | None:
        return next((action for action in self.actions if action.name == name), None)


def _when(at: datetime, now: datetime, zone: tzinfo, *, time: bool = False) -> str:
    return day_words(at, zone, now, time=time)


def _state_words(commitment: Commitment, details: Details, now: datetime, zone: tzinfo) -> str:
    state, source = commitment.state, commitment.state_source
    mark = details.mark
    at = mark.occurred if mark is not None and source is StateSource.PM else details.state_at
    on = "" if at is None else f" on {_when(at, now, zone)}"
    if state is CommitmentState.PENDING:
        words = "open"
    elif state is CommitmentState.ALTERED:
        words = f"open, altered{on}"
    elif state is CommitmentState.UNKNOWN:
        words = "open, pm-ai could not check"
    elif state is CommitmentState.ERROR:
        words = "open, a check failed"
    else:
        words = f"{_STATE_WORD[state]}{on}"
    if source is StateSource.TELEMETRY:
        words += " by telemetry"
    return words


def _due_heading(commitment: Commitment, details: Details, said: str | None, overdue: bool, now: datetime, zone: tzinfo) -> str:
    due = commitment.due
    found = due.date
    if found is None:
        if due.value == DUE_UNRESOLVED and said is not None:
            return f'no due date worked out from "{fold_typed(said)}"'
        if due.value == DUE_UNRESOLVED:
            return "no due date worked out"
        return "no due date said"
    words = f"due {date_words(found, now, zone)}"
    if due.estimated:
        words += " (meeting day estimated)"
    if details.due_by_pm:
        words += ", set by you"
    if overdue:
        words += ", past due"
    return words


def due_text(commitment: Commitment, said: str | None, now: datetime, zone: tzinfo) -> str:
    """The due in force in words, from the commitment, never parsed from a heading (15c).

    `Fri 30 Oct`, else `no due date worked out from "…"`, else `no due date said`.
    """
    found = commitment.due.date
    if found is not None:
        return date_words(found, now, zone)
    if commitment.due.value == DUE_UNRESOLVED:
        return f'no due date worked out from "{fold_typed(said)}"' if said else "no due date worked out"
    return "no due date said"


def _field_line(commitment: Commitment, said: str | None, scope: str) -> str:
    what = fold_typed(commitment.what)
    if commitment.what_cut:
        what += (
            f"… (cut; the full text: pm-ai proposal show {commitment.proposal_id} "
            f"--scope {scope})"
        )
    owner = None if commitment.owner is None else fold_typed(commitment.owner)
    speaker = None if commitment.speaker is None else fold_typed(commitment.speaker)
    if commitment.type == IMPLICIT_COMMITMENT:
        who = []
        if speaker:
            who.append(f"said by {speaker}")
        if owner:
            who.append(f"owner {owner}")
        line = f"{', '.join(who)}: {what}" if who else what
    else:
        line = f"{owner}: {what}" if owner else what
    if said:
        line += f' ("{fold_typed(said)}")'
    return line


def _origin(commitment: Commitment, scope: str, also_in: Sequence[str], now: datetime, zone: tzinfo) -> str:
    approved = f"approved {_when(commitment.approved_at, now, zone)}"
    if commitment.title is not None and commitment.start is not None:
        title = fold_typed(commitment.title) or "(untitled)"
        origin = f"from {title}, {_when(commitment.start, now, zone, time=True)}, {scope}; {approved}"
    elif commitment.meeting_id is not None:
        origin = (
            f"from meeting {commitment.meeting_id} (meeting record unavailable when it was "
            f"approved), {scope}; {approved}"
        )
    else:
        origin = f"{scope}; {approved}"
    if also_in:
        origin += (
            f"; ambiguous: the same id is also in {', '.join(also_in)}'s ledger, and one "
            f"line must be removed by hand"
        )
    return origin


def change_words(details: Details, now: datetime, zone: tzinfo) -> str:
    """One sentence built from the kind of the commitment's newest line (15c's `changed`)."""
    newest = details.newest
    fields = newest.fields
    on = _when(newest.occurred, now, zone)
    kind = newest.kind
    if kind is LineKind.MARKED:
        state = Mark(fields["state"])
        where = via_words(fields.get("via"))
        return f"marked {mark_words(state)} on {on}" + (f", {where}" if where else "")
    if kind is LineKind.DUE_SET:
        value = fields.get("due", DUE_NONE)
        if value == DUE_NONE:
            return f"its due was cleared on {on}"
        return f"its due was set to {date_words(date.fromisoformat(value), now, zone)} on {on}"
    if kind is LineKind.OWNER_SET:
        owner = fields.get("owner")
        return f"its owner was set to {fold_typed(owner)}" if owner else "its owner was cleared"
    if kind is LineKind.REVISED:
        return f"its wording was edited on {on}"
    if kind is LineKind.VERDICT:
        verdict = fields.get("state", "")
        word = "done" if verdict == CommitmentState.FULFILLED.value else verdict
        return f"telemetry recorded it {word} on {on}"
    if kind is LineKind.UNDONE:
        return f"your done mark was undone on {on}"
    return f"it was recorded as a commitment on {on}"


def _words(verb: str, commitment_id: str, scope: str, *rest: str) -> tuple[str, ...]:
    return ("commitment", verb, commitment_id, "--scope", scope, *rest)


def undo_until(details: Details) -> datetime | None:
    """When the PM's done mark stops being undoable, if it is the newest line (15c)."""
    mark = details.mark
    if mark is None or mark.mark is not Mark.FULFILLED or details.newest.entry != mark.entry:
        return None
    return mark.occurred + UNDO_WINDOW


def _undo_action(
    commitment: Commitment, details: Details, scope: str, now: datetime, zone: tzinfo
) -> CardAction:
    mark = details.mark
    if commitment.state is CommitmentState.FULFILLED and commitment.state_source is StateSource.TELEMETRY:
        return CardAction(
            "undo", False, "telemetry recorded it done; pm-ai undoes only your own done mark"
        )
    if mark is None or mark.mark is not Mark.FULFILLED or commitment.state is not CommitmentState.FULFILLED:
        return CardAction("undo", False, "nothing to undo: it is not marked done by you")
    if details.newest.entry != mark.entry:
        return CardAction(
            "undo",
            False,
            f"a later line follows your done mark ({change_words(details, now, zone)})",
        )
    until = mark.occurred + UNDO_WINDOW
    if now > until:
        return CardAction(
            "undo",
            False,
            f"marked done at {_when(mark.occurred, now, zone, time=True)}, more than 4 hours "
            f"ago; mark it broken if it was not done",
        )
    return CardAction(
        "undo",
        True,
        f"until {_when(until, now, zone, time=True)}",
        _words("undo", commitment.commitment_id, scope, "--if-version", details.version),
    )


def _mark_reason(
    verb: str, answer: MarkAnswer, commitment: Commitment, details: Details, now: datetime, zone: tzinfo
) -> str:
    mark = details.mark
    on = "" if mark is None else f" on {_when(mark.occurred, now, zone)}"
    if answer is MarkAnswer.ALREADY:
        return f"you already marked it {verb}{on}"
    if commitment.state is CommitmentState.FULFILLED and commitment.state_source is StateSource.PM:
        return (
            f"you marked it done{on}, so it is not altered or withdrawn; mark it broken if it "
            f"was not done after all"
        )
    return f"it is {_STATE_WORD[commitment.state]}, and pm-ai's mark table does not allow {verb} from it"


def closed_reason(
    commitment: Commitment, details: Details, scope: str, now: datetime, zone: tzinfo, *, field_name: str
) -> str:
    """Why `due` and `owner` act on an open commitment only, and how to reopen it (15c).

    Prose only (15d): when `altered` reopens it, the reason names "the reopen
    command", which `reopen_command` gives with its placeholder and `fill`.
    The undo hint stays a complete command with nothing to fill.
    """
    state = commitment.state
    mark = details.mark
    if commitment.state_source is StateSource.PM and mark is not None:
        said = f"you marked it {_STATE_WORD[state]} on {_when(mark.occurred, now, zone)}"
    elif details.state_at is not None:
        said = f"telemetry recorded it {_STATE_WORD[state]} on {_when(details.state_at, now, zone)}"
    else:
        said = f"it is {_STATE_WORD[state]}"
    if reopen_command(commitment, scope, field_name=field_name) is not None:
        noun = "a due" if field_name == "due" else "an owner"
        return f"{said}; the reopen command reopens it with {noun}"
    undo = _undo_action(commitment, details, scope, now, zone)
    if undo.available and undo.command is not None:
        # The same words as the card's `undo` action, `--if-version` included.
        return f"{said}; `{command_line(undo.command)}` reopens it {undo.reason}"
    return f"{said}; it is closed"


def reopen_command(commitment: Commitment, scope: str, *, field_name: str) -> FilledCommand | None:
    """`commitment altered <id> --due <when>` (or `--owner <name>`) when `altered` reopens it (15d)."""
    if mark_answer(commitment.state, commitment.state_source, Mark.ALTERED) is not MarkAnswer.YES:
        return None
    placeholder = "<when>" if field_name == "due" else "<name>"
    words = _words("altered", commitment.commitment_id, scope, f"--{field_name}", placeholder)
    return FilledCommand(words, (Fill(len(words) - 1, placeholder[1:-1], True),))


def build_card(
    commitment: Commitment,
    details: Details,
    *,
    scope: str,
    now: datetime,
    zone: tzinfo = timezone.utc,
    also_in: Sequence[str] = (),
) -> Card:
    """The card for `commitment` in `scope` at `now`, its actions from `mark_answer` (15c)."""
    today = now.astimezone(zone).date()
    said = details.due_said if details.due_said is not None else commitment.due.said
    overdue = past_due(commitment, today)
    heading = (
        f"Commitment, {_state_words(commitment, details, now, zone)}, "
        f"{_due_heading(commitment, details, said, overdue, now, zone)}"
    )
    root, version = commitment.commitment_id, details.version
    actions: list[CardAction] = []
    for verb, mark in MARK_VERBS.items():
        answer = mark_answer(commitment.state, commitment.state_source, mark)
        if answer is MarkAnswer.YES:
            rest = ("--note", "<note>") if verb == "altered" else ()
            words = _words(verb, root, scope, *rest, "--if-version", version)
            # Story 15d: the note is optional; a wrapper drops it with `--note`.
            fill = (Fill(words.index("<note>"), "note", False),) if rest else ()
            actions.append(CardAction(verb, True, None, words, fill))
        else:
            actions.append(CardAction(verb, False, _mark_reason(verb, answer, commitment, details, now, zone)))
    for verb in ("due", "owner"):
        if commitment.state.is_terminal:
            actions.append(
                CardAction(
                    verb,
                    False,
                    closed_reason(commitment, details, scope, now, zone, field_name=verb),
                    reopen=reopen_command(commitment, scope, field_name=verb),
                )
            )
        else:
            placeholder = "<when>" if verb == "due" else "<name>"
            actions.append(
                CardAction(
                    verb,
                    True,
                    None,
                    ("commitment", verb, root, placeholder, "--scope", scope, "--if-version", version),
                    (Fill(3, placeholder[1:-1], True),),
                )
            )
    actions.append(_undo_action(commitment, details, scope, now, zone))
    actions.append(CardAction("show", True, None, _words("show", root, scope)))
    in_force = details.mark
    return Card(
        commitment=commitment,
        details=details,
        scope=scope,
        also_in=tuple(also_in),
        heading=heading,
        field_line=_field_line(commitment, said, scope),
        origin=_origin(commitment, scope, also_in, now, zone),
        note=None if in_force is None or in_force.note is None else fold_typed(in_force.note),
        past_due=overdue,
        due_said=said,
        changed=change_words(details, now, zone),
        due_words=due_text(commitment, said, now, zone),
        actions=tuple(actions),
    )


def card_text(card: Card, *, compact: bool = False) -> str:
    """The text card: heading, field line, origin, the PM's note, then the commands.

    `compact=True` is for `commitment review`'s walk only (13d's lesson): the
    full form's first lines, without the command lines.
    """
    lines = [card.heading, f"  {card.field_line}", f"  {card.origin}"]
    if card.note is not None:
        lines.append(f"  note: {card.note}")
    if not compact:
        for action in card.actions:
            if action.name == "show":
                continue
            name = f"{action.name}:".ljust(10)
            if action.available and action.command is not None:
                line = f"  {name} {command_line(action.command)}"
                if action.reason:
                    line += f"  ({action.reason})"
                lines.append(line)
            else:
                lines.append(f"  {name} unavailable: {action.reason}")
                if action.reopen is not None:
                    lines.append(f"  {'':10} reopen: {command_line(action.reopen.command)}")
    return "\n".join(lines) + "\n"


def _mark_json(mark: MarkInForce | None) -> dict[str, object] | None:
    if mark is None:
        return None
    return {
        "state": mark.mark.value,
        "at": instant_json(mark.occurred),
        "via": mark.via,
        "note": mark.note,
    }


def card_json(card: Card) -> dict[str, object]:
    """The card as the machine form's object (15c)."""
    commitment, details = card.commitment, card.details
    due = commitment.due
    found = due.date
    return {
        "commitment_id": commitment.commitment_id,
        "version": details.version,
        "scope": card.scope,
        "also_in": list(card.also_in),
        "type": commitment.type,
        "proposal_id": commitment.proposal_id,
        "state": commitment.state.value,
        "state_source": commitment.state_source.value,
        "what": commitment.what,
        "what_cut": commitment.what_cut,
        "owner": commitment.owner,
        "owner_by_pm": details.owner_by_pm,
        "speaker": commitment.speaker,
        "due": {
            "said": card.due_said,
            "value": due.value,
            "date": None if found is None else found.isoformat(),
            "estimated": due.estimated,
            "by_pm": details.due_by_pm,
        },
        "past_due": card.past_due,
        "day": None if commitment.day is None else commitment.day.isoformat(),
        "day_from": commitment.day_from,
        "meeting": None
        if commitment.meeting_id is None
        else {
            "meeting_id": commitment.meeting_id,
            "title": commitment.title,
            "start": instant_json(commitment.start),
        },
        "approved_at": instant_json(commitment.approved_at),
        "changed_at": instant_json(details.changed_at),
        "mark": _mark_json(details.mark),
        "actions": {action.name: _action_json(action) for action in card.actions},
    }


def _action_json(action: CardAction) -> dict[str, object]:
    """One action: `command` and `fill` side by side; `reopen` on `due` and `owner` (15d)."""
    found: dict[str, object] = {
        "available": action.available,
        "reason": action.reason,
        "command": None if action.command is None else list(action.command),
        "fill": fill_json(action.fill),
    }
    if action.name in ("due", "owner"):
        found["reopen"] = filled_json(action.reopen)
    return found


# ── What the commands answer ─────────────────────────────────────────────────


class CommitmentMisused(ValueError):
    """A `commitment` command line pm-ai cannot act on: a usage error (exit 2)."""


class CommitmentRefused(Exception):
    """A `commitment` command refused (exit 3), with what the refusal object carries."""

    def __init__(
        self,
        code: str,
        reason: str,
        *,
        card: Card | None = None,
        wrote: Sequence[str] = (),
        next: Sequence[str] | None = None,
    ) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.card = card
        self.wrote = tuple(wrote)
        self.next = None if next is None else tuple(next)


@dataclass(frozen=True, slots=True)
class CommitmentOutcome:
    """What `show` and every verb did: the outcome, the card, the next steps (15c)."""

    outcome: str
    card: Card
    sentence: str
    wrote: tuple[str, ...] = ()
    edited: str | None = None
    note_recorded: bool = False
    handle_recorded: bool | None = None
    next: tuple[str, ...] | None = None
    undo: tuple[tuple[str, ...], datetime] | None = None
    """`undo`'s command and the instant its window closes, after a done mark."""
    undo_shown: str | None = None
    """That instant as the text says it, in the display zone."""


@dataclass(frozen=True, slots=True)
class LedgerCounts:
    """One scope's lines that never failed the read (15a's counts), as `list` shows them."""

    scope: str
    malformed: int = 0
    orphans: int = 0
    duplicates: int = 0
    conflicts: int = 0
    refused: int = 0

    @property
    def any(self) -> bool:
        return bool(self.malformed or self.orphans or self.duplicates or self.conflicts or self.refused)


@dataclass(frozen=True, slots=True)
class CommitmentListing:
    """What `commitment list` found (15c)."""

    state: str
    scopes: tuple[str, ...]
    filter_words: str
    cards: tuple[Card, ...] = ()
    unreadable: tuple[tuple[str, str], ...] = ()
    ledgers: tuple[LedgerCounts, ...] = ()
    by_state: tuple[tuple[str, int], ...] = ()
    by_source: tuple[tuple[str, int], ...] = ()
    more: int = 0
    review: tuple[str, ...] | None = None
    widen: tuple[str, ...] | None = None
    reviews: tuple[tuple[str, ...], ...] = ()
    """`commitment review` per scope with open commitments; `review` is the first.
    Every printed command carries `--scope`."""
    widens: tuple[tuple[str, ...], ...] = ()
    """The listing that would show more, per scope looked at; `widen` is the first."""


def refusal_json(refused: CommitmentRefused) -> dict[str, object]:
    return {
        "format": CARD_FORMAT,
        "outcome": "refused",
        "code": refused.code,
        "reason": refused.reason,
        "commitment": None if refused.card is None else card_json(refused.card),
        "wrote": list(refused.wrote),
        "next": None if refused.next is None else list(refused.next),
    }


def refusal_text(refused: CommitmentRefused) -> str:
    """The refusal's sentence, the current card when there is one, and what repairs it."""
    text = refused.reason.rstrip()
    if refused.card is not None:
        text += "\n" + card_text(refused.card).rstrip("\n")
    if refused.next is not None:
        text += f"\nnext: {command_line(refused.next)}"
    return text


def outcome_json(answer: CommitmentOutcome) -> dict[str, object]:
    return {
        "format": CARD_FORMAT,
        "outcome": answer.outcome,
        "commitment": card_json(answer.card),
        "wrote": list(answer.wrote),
        "edited": None if answer.edited is None else {"proposal_id": answer.edited},
        "note_recorded": answer.note_recorded,
        "handle_recorded": answer.handle_recorded,
        "next": None if answer.next is None else list(answer.next),
        "undo": None
        if answer.undo is None
        else {"command": list(answer.undo[0]), "until": instant_json(answer.undo[1])},
    }


def outcome_text(answer: CommitmentOutcome) -> str:
    """The card after the change, the outcome, then the next steps — `undo` first while open."""
    lines = [card_text(answer.card).rstrip("\n"), answer.sentence]
    if answer.undo is not None:
        words, until = answer.undo
        shown = answer.undo_shown or instant_json(until)
        lines.append(f"undo until {shown}: {command_line(words)}")
    if answer.next is not None:
        lines.append(f"next: {command_line(answer.next)}")
    return "\n".join(lines) + "\n"


def listing_json(listing: CommitmentListing) -> dict[str, object]:
    return {
        "format": CARD_FORMAT,
        "state": listing.state,
        "scopes": list(listing.scopes),
        "unreadable": [{"scope": scope, "reason": reason} for scope, reason in listing.unreadable],
        "ledgers": [
            {
                "scope": each.scope,
                "malformed": each.malformed,
                "orphans": each.orphans,
                "duplicates": each.duplicates,
                "conflicts": each.conflicts,
                "refused": each.refused,
            }
            for each in listing.ledgers
        ],
        "counts": {
            "by_state": dict(listing.by_state),
            "by_source": dict(listing.by_source),
        },
        "more": listing.more,
        "next": {
            "review": None if listing.review is None else list(listing.review),
            "more": None if listing.widen is None else list(listing.widen),
        },
        "commitments": [card_json(card) for card in listing.cards],
    }


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def bad_lines_words(counts: LedgerCounts) -> str:
    """`commitments_log.md in personal: 1 line could not be read, 1 mark was not applied`."""
    parts: list[str] = []
    if counts.malformed:
        parts.append(_plural(counts.malformed, "line could not be read", "lines could not be read"))
    if counts.orphans:
        parts.append(
            _plural(counts.orphans, "line names no recorded commitment", "lines name no recorded commitment")
        )
    if counts.duplicates:
        parts.append(_plural(counts.duplicates, "line repeats an earlier one", "lines repeat an earlier one"))
    if counts.conflicts:
        parts.append(
            _plural(
                counts.conflicts,
                "line conflicts with an earlier one",
                "lines conflict with an earlier one",
            )
        )
    if counts.refused:
        parts.append(_plural(counts.refused, "mark was not applied", "marks were not applied"))
    return f"commitments_log.md in {counts.scope}: {', '.join(parts)}"


def listing_text(listing: CommitmentListing) -> str:
    """One card per commitment, the bad-line counts per scope, then the next command."""
    lines: list[str] = [
        f"pm-ai could not read commitments_log.md in {scope}: {reason}"
        for scope, reason in listing.unreadable
    ]
    scopes = ", ".join(listing.scopes) or "no scope"
    if not listing.cards:
        lines.append(f"No commitment matches {listing.filter_words} in {scopes}.")
    for card in listing.cards:
        lines.append(card_text(card).rstrip("\n"))
    if listing.more:
        lines.append(f"and {listing.more} more not shown.")
    lines += [bad_lines_words(each) for each in listing.ledgers if each.any]
    reviews = listing.reviews or (() if listing.review is None else (listing.review,))
    widens = listing.widens or (() if listing.widen is None else (listing.widen,))
    if reviews:
        lines += [f"next: {command_line(words)}" for words in reviews]
    else:
        lines += [f"to see more: {command_line(words)}" for words in widens]
    return "\n".join(lines) + "\n"


# ── Story 15d: the words the pages and the proposal cards show ───────────────

TEAM_ROW_STATES = frozenset({"done", "altered"})
"""The states a Team Activity row prints (Andrei, 2026-10-27). Rows are grouped by
member, so a row's "broken" would read as a per-person failure record (AD-25)."""

TEAM_ROW_PAST_DUE = False
"""A Team Activity row never says a date has passed (Andrei, 2026-10-27, AD-25)."""

PROJECT_PAGE_PAST_DUE = False
"""A project page's block of open commitments never says a date has passed, and its
closed line leaves the broken count out (Andrei, 2026-10-27, AD-25)."""

PLACEHOLDERS = ("<note>", "<when>", "<name>", "<what>")
"""The words a command offers where the PM types a value (15d). Each command
word list that holds one carries a `fill` entry for it."""

_PLACEHOLDER_OF = {
    "note": "<note>",
    "when": "<when>",
    "due": "<when>",
    "name": "<name>",
    "owner": "<name>",
    "what": "<what>",
}
_PLACEHOLDER_ASK = {
    "<note>": "the note itself",
    "<when>": "when it is due",
    "<name>": "the name itself",
    "<what>": "the wording itself",
}


def is_placeholder(value: str) -> bool:
    """Whether a typed value is a placeholder a wrapper failed to fill (15d).

    The value is folded to one line (multi-word words joined with one space),
    stripped and case-folded; one pair of surrounding `'` or `"` is removed and
    it is stripped again. It is a placeholder when it contains any of
    `PLACEHOLDERS`, or is `…` or `...`.
    """
    folded = " ".join(fold_typed(value).split()).strip().casefold()
    if len(folded) >= 2 and folded[0] == folded[-1] and folded[0] in "'\"":
        folded = folded[1:-1].strip()
    if folded in ("…", "..."):
        return True
    return any(word in folded for word in PLACEHOLDERS)


def placeholder_words(field_name: str) -> str:
    """`type the note itself, not <note>`: what a refused placeholder says (15d)."""
    placeholder = _PLACEHOLDER_OF[field_name]
    return f"type {_PLACEHOLDER_ASK[placeholder]}, not {placeholder}"


def open_order(commitment: Commitment) -> tuple[int, date, int, float, str]:
    """`commitment list`'s order for open ones, and every page's (15c, 15d).

    A date first, earliest first; then `unresolved`; then `none`. Ties by the
    meeting's start, newest first, a line with no start after those with one,
    then the commitment id. No missing value is ever compared with a date.
    """
    found = commitment.due.date
    if found is not None:
        rank, day = 0, found
    else:
        rank, day = (1 if commitment.due.value == DUE_UNRESOLVED else 2), date.max
    start = commitment.start
    return (
        rank,
        day,
        0 if start is not None else 1,
        -start.timestamp() if start is not None else 0.0,
        commitment.commitment_id,
    )


def said_due_words(day: date, estimated: bool) -> str:
    """A waiting item's worked-out date on its Team Activity row: ` (2026-10-22)` (15a, 7e)."""
    words = f" ({day.isoformat()})"
    if estimated:
        words += " (meeting day estimated)"
    return words


@dataclass(frozen=True, slots=True)
class PageParts:
    """Every word a page shows about one commitment's state and due (story 15d).

    Built here alone, from the card's own functions, so `rendering` and
    `member_updates` only compose and escape.
    """

    block_state: str
    """The block line's state: `""` for `pending`, `altered on <date>`, …"""
    block_due: str
    """The block line's bold due."""
    block_due_unsaid: str
    """`block_due` without the words said: what a line whose text is withheld shows."""
    row_state: str
    """A Team Activity row's state, appended as `; <row_state>`."""
    row_due: str
    """What follows `due as said in the meeting: <words>` on a row, or stands alone."""
    past_due: bool
    said: str | None
    quote_said: bool
    """The due as said is quoted exactly when a date was worked out from it."""
    origin_day: str | None
    """The meeting's start as the block's origin says it: `Mon 19 Oct`."""


def _page_state(
    commitment: Commitment, details: Details, now: datetime, zone: tzinfo, *, row: bool
) -> str:
    state, source = commitment.state, commitment.state_source
    mark = details.mark
    at = mark.occurred if mark is not None and source is StateSource.PM else details.state_at
    on = "" if at is None else f" on {_when(at, now, zone)}"
    if state is CommitmentState.PENDING:
        return ""
    if state is CommitmentState.UNKNOWN:
        words = "commitment: pm-ai could not check" if row else "pm-ai could not check"
    elif state is CommitmentState.ERROR:
        words = "commitment: a check failed" if row else "a check failed"
    else:
        words = f"{_STATE_WORD[state]}{on}"
        if row:
            words = f"commitment {words}"
    if source is StateSource.TELEMETRY:
        words += " by telemetry"
    return words


def _page_due(
    commitment: Commitment, details: Details, said: str | None, overdue: bool, now: datetime, zone: tzinfo
) -> str:
    due = commitment.due
    if due.date is None and due.value == DUE_NONE and details.due_by_pm:
        return "due cleared by you"
    return _due_heading(commitment, details, said, overdue, now, zone)


def _row_due(
    commitment: Commitment, details: Details, said: str | None, overdue: bool, now: datetime, zone: tzinfo
) -> str:
    due = commitment.due
    found = due.date
    if found is None:
        return "due cleared by you" if due.value == DUE_NONE and details.due_by_pm else ""
    day = date_words(found, now, zone)
    if details.due_by_pm:
        words = f"(set by you: {day})" if said else f"due set by you: {day}"
    elif due.estimated:
        words = f"({day}, meeting day estimated)"
    else:
        words = f"({day})"
    if overdue:
        words += ", past due"
    return words


def page_parts(commitment: Commitment, details: Details, *, now: datetime, zone: tzinfo) -> PageParts:
    """The words of one commitment on a page, at `now` in `zone` (story 15d)."""
    today = now.astimezone(zone).date()
    said = details.due_said if details.due_said is not None else commitment.due.said
    overdue = past_due(commitment, today)
    return PageParts(
        block_state=_page_state(commitment, details, now, zone, row=False),
        block_due=_page_due(commitment, details, said, overdue, now, zone),
        block_due_unsaid=_page_due(commitment, details, None, overdue, now, zone),
        row_state=_page_state(commitment, details, now, zone, row=True),
        row_due=_row_due(commitment, details, said, overdue, now, zone),
        past_due=overdue,
        said=said,
        quote_said=commitment.due.date is not None and not details.due_by_pm and bool(said),
        origin_day=None if commitment.start is None else _when(commitment.start, now, zone),
    )


def page_due(parts: PageParts, *, project: bool, withheld: bool = False) -> str:
    """The block line's bold due: a project page drops `, past due` (`PROJECT_PAGE_PAST_DUE`).

    `withheld` gives the due without the words said, for a line whose text is withheld.
    """
    shown = parts.block_due_unsaid if withheld else parts.block_due
    if project and parts.past_due and not PROJECT_PAGE_PAST_DUE:
        shown = shown.removesuffix(", past due")
    return shown


def team_row_words(
    commitment: Commitment, details: Details, *, now: datetime, zone: tzinfo
) -> tuple[str, str]:
    """A Team Activity row's `(row_due, row_state)`, under `TEAM_ROW_STATES` and `TEAM_ROW_PAST_DUE`."""
    words = page_parts(commitment, details, now=now, zone=zone)
    row_due = words.row_due
    if words.past_due and not TEAM_ROW_PAST_DUE:
        row_due = row_due.removesuffix(", past due")
    state = _STATE_WORD.get(commitment.state, "")
    row_state = words.row_state if state in TEAM_ROW_STATES else ""
    return row_due, row_state


CLOSED_STATES = ("done", "broken", "withdrawn")
"""The closed line's states, in its order (15d)."""


def closed_state_word(state: CommitmentState) -> str | None:
    """`done`, `broken` or `withdrawn` for a closed state; `None` for an open one."""
    word = _STATE_WORD.get(state)
    return word if word in CLOSED_STATES else None


def closed_words(counts: Mapping[str, tuple[int, int]]) -> str:
    """`2 done (1 marked by you, 1 by telemetry), 1 broken (marked by you)` (15d).

    `counts` maps `done`, `broken`, `withdrawn` to `(by the PM, by telemetry)`.
    A state with 0 is left out; each count names its source.
    """
    parts: list[str] = []
    for state in CLOSED_STATES:
        pm, telemetry = counts.get(state, (0, 0))
        if not pm + telemetry:
            continue
        if pm and telemetry:
            source = f"{pm} marked by you, {telemetry} by telemetry"
        elif pm:
            source = "marked by you"
        else:
            source = "by telemetry"
        parts.append(f"{pm + telemetry} {state} ({source})")
    return ", ".join(parts)


def proposal_commitment(
    root: str,
    proposal_id: str,
    scope: str,
    ledger: CommitmentLedger | str,
    *,
    now: datetime,
    zone: tzinfo,
) -> CardCommitment:
    """An approved item's commitment line and JSON on its proposal card (15d).

    `ledger` is the scope's folded ledger, or the sentence saying why it was
    not read (`the ledger could not be read (OSError)`, `not read within the
    bound`).
    """
    if isinstance(ledger, str):
        return CardCommitment(root, f"commitment: {ledger}", reason=ledger)
    if root in ledger.malformed_roots:
        sentence = f"a line of commitments_log.md naming {root} does not parse; fix or remove it"
        return CardCommitment(
            root, f"commitment: {sentence}", recorded=root in ledger.commitments, reason=sentence
        )
    held = ledger.commitments.get(root)
    if held is None:
        repair = ("proposal", "approve", proposal_id, "--scope", scope)
        return CardCommitment(
            root,
            f"commitment: not recorded yet: {command_line(repair)} records it",
            recorded=False,
            past_due=False,
            repair=repair,
        )
    words = page_parts(held, ledger.details[root], now=now, zone=zone)
    show = ("commitment", "show", root, "--scope", scope)
    return CardCommitment(
        root,
        f"commitment: {words.block_state or 'open'}, {words.block_due}: {command_line(show)}",
        recorded=True,
        state=held.state.value,
        state_source=held.state_source.value,
        past_due=words.past_due,
        show=show,
    )
