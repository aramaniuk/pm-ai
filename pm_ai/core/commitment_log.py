"""`commitments_log.md`: its lines, its reader and its one accessor (story 15a).

The ledger starts at approval (AD-14): one `pending` line when the PM approves
an `action_item` or an `implicit_commitment`, in the `commitments_log.md` of
the scope that owns the meeting — a project's, or the personal scope's
(AD-28). An edit after approval appends a `revised` line; the PM's own marks,
the due and owner he sets (15c) and telemetry verdicts (story 16) are lines of
their own kinds, and 15a's reader folds every kind.

**The line.** One record per line, through the ledgers' one encoding
(`render_value` and `scan_fields`). Every line starts `- entry=<id>
commitment=<root> kind=<kind>` and ends `occurred=<instant> … at=<instant>`.
`entry` is `cml_` plus 12 hex characters of SHA-256 over the line's identity,
so a line repeated, retried or caught up has the id it had: each writer reads
and folds the ledger first and leaves out every line whose entry is there.

**Reading.** A final line without its newline is a write in progress and is
dropped silently, as `pm_ai.core.ledger` does. A complete line that will not
parse is skipped and counted — not refused for the whole file, as the event
log's reader does — because this file is also the PM's to read and edit, and
one bad line must not stop every later approval from being recorded. The
fold orders lines by their own fields, never by file order (AD-35).

`CommitmentLog` is the one accessor (`derivation-services.md` rule 3): a
vocabulary over `StoragePort`, holding no path and opening no file.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from types import MappingProxyType

from pm_ai.core.due_dates import meeting_day, resolve
from pm_ai.core.proposals import IMPLICIT_COMMITMENT, IMPLICIT_COMMITMENT_TYPE, OWNER_LIMIT
from pm_ai.domain.commitments import (
    UNDO_WINDOW,
    Cites,
    Commitment,
    DUE_NONE,
    DUE_UNRESOLVED,
    Due,
    LineKind,
    Mark,
    NotACommitmentLine,
    StateSource,
    can_mark,
)
from pm_ai.domain.event_entries import MAX_ENTRY_LENGTH, MalformedEntry, render_value, scan_fields
from pm_ai.domain.identity import DataScope
from pm_ai.domain.lifecycle import CommitmentState
from pm_ai.domain.meeting_summaries import ACTION_ITEM, DUE_LIMIT, WHAT_LIMIT
from pm_ai.domain.proposals import Proposal, fold_typed, replacement_number, root_of
from pm_ai.ports import StoragePort

__all__ = [
    "COMMITMENT_TYPES",
    "CommitmentLedger",
    "CommitmentLine",
    "CommitmentLog",
    "Details",
    "DueSet",
    "FOLD_VERSION",
    "Head",
    "MarkInForce",
    "Marked",
    "NOTE_LIMIT",
    "NewestLine",
    "NoApprovalInstant",
    "OwnerSet",
    "Pending",
    "REASONS",
    "Reason",
    "Recording",
    "Revised",
    "TITLE_LIMIT",
    "TooLong",
    "Undone",
    "day_of",
    "entry_id",
    "fold",
    "from_approved",
    "parse_ledger",
    "preview_due",
    "recording_json",
    "render_line",
    "revised_from",
]

COMMITMENT_TYPES = (ACTION_ITEM, IMPLICIT_COMMITMENT)
"""The proposal types whose approval records a commitment today."""

TITLE_LIMIT = 200
"""A meeting title on a line: cut at a word, and marked `title_cut=yes`."""

NOTE_LIMIT = 200

_ENTRY = re.compile(r"cml_[0-9a-f]{12}")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
"""A date on a line: `YYYY-MM-DD` only. `date.fromisoformat` alone takes `2026-W43-1` too."""
_BOM = b"\xef\xbb\xbf"
_NAMES_A_ROOT = re.compile(rb"(?:^|\s)commitment=\"?([^\s\"]+)")
_VERDICT_STATES = frozenset({"fulfilled", "broken", "unknown", "error"})
_WRITTEN = frozenset({"approval", "catch_up"})


class TooLong(ValueError):
    """A stored value is over its field's limit: only a hand edit of the store makes one.

    Not cut — a shortened name is a different name — so the line is not
    written (reason `too_long`), and nothing retrying will change it.
    """

    def __init__(self, field_name: str, length: int, limit: int) -> None:
        super().__init__(f"the {field_name} is {length} characters, over its limit of {limit}")
        self.field_name = field_name


class NoApprovalInstant(ValueError):
    """An approved proposal with no `changed_at`: no approval instant is known, so no line."""


# ── The lines ────────────────────────────────────────────────────────────────

_TOKEN = object()


@dataclass(frozen=True, slots=True)
class CommitmentLine:
    """One rendered line, without its newline. Only `render_line` builds one (AD-28)."""

    entry: str
    commitment: str
    kind: LineKind
    text: str
    _made_by: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._made_by is not _TOKEN:
            raise NotACommitmentLine(
                "a commitment line is built only by `render_line`, from a commitment or a mark."
            )


@dataclass(frozen=True, slots=True)
class Pending:
    """The `pending` line: the commitment as approved."""

    commitment: Commitment
    via: str
    written: str
    at: datetime


@dataclass(frozen=True, slots=True)
class Revised:
    """The `revised` line: the whole set of values after an edit of an approved item.

    `due` is set only when the due as said changed; otherwise the due in force
    stands, one the PM set included.
    """

    commitment: str
    proposal: str
    replaces: str
    what: str
    owner: str | None
    due: Due | None
    occurred: datetime
    via: str
    written: str
    at: datetime


@dataclass(frozen=True, slots=True)
class Marked:
    """The PM's own mark (15c writes it)."""

    commitment: str
    mark: Mark
    occurred: datetime
    via: str
    at: datetime
    handle: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class DueSet:
    """A due date the PM set, or cleared with `None` (15c)."""

    commitment: str
    due: date | None
    occurred: datetime
    via: str
    at: datetime
    handle: str | None = None


@dataclass(frozen=True, slots=True)
class OwnerSet:
    """An owner the PM set, or cleared with `None` (15c)."""

    commitment: str
    owner: str | None
    occurred: datetime
    via: str
    at: datetime
    handle: str | None = None


@dataclass(frozen=True, slots=True)
class Undone:
    """The PM's undo of his own done mark (story 15c, Andrei 2026-10-26).

    `undone_entry` is the `entry` of the `marked state=fulfilled` line it
    undoes. Its identity is `(commitment, "undone", undone_entry)`, so one mark
    is undone at most once.
    """

    commitment: str
    undone_entry: str
    occurred: datetime
    via: str
    at: datetime
    handle: str | None = None


Record = Pending | Revised | Marked | DueSet | OwnerSet | Undone


def entry_id(identity: Sequence[str]) -> str:
    """`cml_` plus the first 12 hex characters of SHA-256 over the line's identity."""
    canonical = json.dumps(list(identity), ensure_ascii=False, separators=(",", ":"))
    return "cml_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _instant(at: datetime) -> str:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("an instant on a commitment line carries a timezone")
    return at.astimezone(timezone.utc).isoformat()


def _bounded(name: str, value: str, limit: int) -> str:
    if len(value) > limit:
        raise TooLong(name, len(value), limit)
    return render_value(value, where=f"the {name}")


def _due_value(due: Due) -> str:
    return due.value


def _head(record_entry: str, commitment: str, kind: LineKind) -> list[str]:
    return [f"entry={record_entry}", f"commitment={render_value(commitment, where='a commitment id')}", f"kind={kind.value}"]


def render_line(record: Record) -> CommitmentLine:
    """One line for `record`, built only here: a commitment or a mark (story 15a).

    `TooLong` when a value is over its field's limit; nothing is cut.
    """
    if isinstance(record, Pending):
        c = record.commitment
        entry = entry_id((c.commitment_id, LineKind.PENDING.value))
        parts = _head(entry, c.commitment_id, LineKind.PENDING)
        parts += [
            f"proposal={render_value(c.proposal_id, where='a proposal id')}",
            f"type={c.type}",
        ]
        if c.meeting_id is not None:
            parts.append(f"meeting={render_value(c.meeting_id, where='a meeting id')}")
        if c.title is not None and c.start is not None:
            parts.append(f"title={_bounded('title', c.title, TITLE_LIMIT)}")
            if c.title_cut:
                parts.append("title_cut=yes")
            parts.append(f"start={_instant(c.start)}")
        if c.day is not None:
            parts.append(f"day={c.day.isoformat()}")
            if c.day_from is not None:
                parts.append(f"day_from={render_value(c.day_from, where='an estimate source')}")
        parts.append(f"what={_bounded('what', c.what, WHAT_LIMIT)}")
        if c.what_cut:
            parts.append("what_cut=yes")
        if c.owner is not None:
            parts.append(f"owner={_bounded('owner', c.owner, OWNER_LIMIT)}")
        if c.speaker is not None:
            parts.append(f"speaker={_bounded('speaker', c.speaker, OWNER_LIMIT)}")
        if c.due.said is not None:
            parts.append(f"due_said={_bounded('due as said', c.due.said, DUE_LIMIT)}")
        parts += [
            f"due={_due_value(c.due)}",
            f"occurred={_instant(c.approved_at)}",
            f"via={render_value(record.via, where='a surface')}",
            f"written={record.written}",
            f"at={_instant(record.at)}",
        ]
        return _line(entry, c.commitment_id, LineKind.PENDING, parts)
    if isinstance(record, Revised):
        entry = entry_id((record.commitment, LineKind.REVISED.value, record.proposal))
        parts = _head(entry, record.commitment, LineKind.REVISED)
        parts += [
            f"proposal={render_value(record.proposal, where='a proposal id')}",
            f"replaces={render_value(record.replaces, where='a proposal id')}",
            f"what={_bounded('what', record.what, WHAT_LIMIT)}",
        ]
        if record.owner is not None:
            parts.append(f"owner={_bounded('owner', record.owner, OWNER_LIMIT)}")
        if record.due is not None:
            if record.due.said is not None:
                parts.append(f"due_said={_bounded('due as said', record.due.said, DUE_LIMIT)}")
            parts.append(f"due={_due_value(record.due)}")
        parts += [
            f"occurred={_instant(record.occurred)}",
            f"via={render_value(record.via, where='a surface')}",
            f"written={record.written}",
            f"at={_instant(record.at)}",
        ]
        return _line(entry, record.commitment, LineKind.REVISED, parts)
    if isinstance(record, Marked):
        occurred = _instant(record.occurred)
        entry = entry_id((record.commitment, LineKind.MARKED.value, record.mark.value, occurred))
        parts = _head(entry, record.commitment, LineKind.MARKED)
        parts += [f"state={record.mark.value}", "by=pm"]
        if record.handle is not None:
            parts.append(f"handle={render_value(record.handle, where='a person handle')}")
        if record.note is not None:
            parts.append(f"note={_bounded('note', record.note, NOTE_LIMIT)}")
        parts += [
            f"occurred={occurred}",
            f"via={render_value(record.via, where='a surface')}",
            f"at={_instant(record.at)}",
        ]
        return _line(entry, record.commitment, LineKind.MARKED, parts)
    if isinstance(record, DueSet):
        occurred = _instant(record.occurred)
        value = DUE_NONE if record.due is None else record.due.isoformat()
        entry = entry_id((record.commitment, LineKind.DUE_SET.value, value, occurred))
        parts = _head(entry, record.commitment, LineKind.DUE_SET)
        parts += [f"due={value}", "by=pm"]
        if record.handle is not None:
            parts.append(f"handle={render_value(record.handle, where='a person handle')}")
        parts += [
            f"occurred={occurred}",
            f"via={render_value(record.via, where='a surface')}",
            f"at={_instant(record.at)}",
        ]
        return _line(entry, record.commitment, LineKind.DUE_SET, parts)
    if isinstance(record, OwnerSet):
        occurred = _instant(record.occurred)
        entry = entry_id((record.commitment, LineKind.OWNER_SET.value, record.owner or "", occurred))
        parts = _head(entry, record.commitment, LineKind.OWNER_SET)
        if record.owner is not None:
            parts.append(f"owner={_bounded('owner', record.owner, OWNER_LIMIT)}")
        parts.append("by=pm")
        if record.handle is not None:
            parts.append(f"handle={render_value(record.handle, where='a person handle')}")
        parts += [
            f"occurred={occurred}",
            f"via={render_value(record.via, where='a surface')}",
            f"at={_instant(record.at)}",
        ]
        return _line(entry, record.commitment, LineKind.OWNER_SET, parts)
    if isinstance(record, Undone):
        if not _ENTRY.fullmatch(record.undone_entry):
            raise ValueError("an undone line names a mark's entry: `cml_` and 12 hex characters")
        entry = entry_id((record.commitment, LineKind.UNDONE.value, record.undone_entry))
        parts = _head(entry, record.commitment, LineKind.UNDONE)
        parts += [f"undone_entry={record.undone_entry}", "by=pm"]
        if record.handle is not None:
            parts.append(f"handle={render_value(record.handle, where='a person handle')}")
        parts += [
            f"occurred={_instant(record.occurred)}",
            f"via={render_value(record.via, where='a surface')}",
            f"at={_instant(record.at)}",
        ]
        return _line(entry, record.commitment, LineKind.UNDONE, parts)
    raise NotACommitmentLine(
        f"{type(record).__name__} is not a commitment or a mark, so no commitment line is "
        f"built from it (AD-28)."
    )


def _line(entry: str, commitment: str, kind: LineKind, parts: list[str]) -> CommitmentLine:
    text = "- " + " ".join(parts)
    if len(text) > MAX_ENTRY_LENGTH or "\n" in text or "\r" in text:
        raise ValueError("a commitment line must be one line within the ledgers' bound.")
    return CommitmentLine(entry, commitment, kind, text, _TOKEN)


# ── Building a commitment from an approved proposal ─────────────────────────


def _text(value: object) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("for_model")
    if isinstance(value, str) and value.strip():
        return value
    return None


def _values(proposal: Proposal) -> tuple[str, str | None, str | None, str | None]:
    """`what`, `owner`, `speaker` and the due as said, from the live approved proposal."""
    payload = proposal.payload if isinstance(proposal.payload, Mapping) else {}
    if proposal.type == IMPLICIT_COMMITMENT:
        values = IMPLICIT_COMMITMENT_TYPE.values(payload)
        what = values.get("what") or proposal.summary
        return what, None, values.get("speaker"), values.get("when")
    what = _text(payload.get("what")) or proposal.summary
    return what, _text(payload.get("owner")), None, _text(payload.get("due"))


def day_of(
    record: object | None, *, zone: tzinfo | None, machine_zone: tzinfo | None
) -> tuple[date | None, str | None]:
    """The meeting's day the due is worked out against, and `day_from` when estimated.

    No day for a record that could not be read (`None`), a start from a
    file's modification time, or a source pm-ai does not know.
    """
    meeting = getattr(record, "meeting", None)
    if meeting is None:
        return None, None
    estimated = meeting.start_estimated_from
    day = meeting_day(meeting.start, estimated, zone=zone, machine_zone=machine_zone)
    if day is None:
        return None, None
    return day, estimated


def preview_due(
    proposal: Proposal, record: object | None, *, zone: tzinfo | None, machine_zone: tzinfo | None
) -> tuple[date | None, str | None, Due]:
    """The day, its source and the due an approval would record, limits unchecked."""
    _, _, _, said = _values(proposal)
    day, day_from = day_of(record, zone=zone, machine_zone=machine_zone)
    return day, day_from, resolve(said, day, estimated=day_from is not None)


def _cut_at_word(text: str, limit: int) -> tuple[str, bool]:
    """`text` folded to one line, cut at a word to at most `limit` characters."""
    folded = fold_typed(text)
    if len(folded) <= limit:
        return folded, False
    cut = folded[:limit]
    if folded[limit] != " " and " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(), True


def _cut_title(title: str) -> tuple[str, bool]:
    """The title folded to one line, cut at a word to at most `TITLE_LIMIT`."""
    return _cut_at_word(title, TITLE_LIMIT)


def _check(name: str, value: str | None, limit: int) -> None:
    if value is not None and len(value) > limit:
        raise TooLong(name, len(value), limit)


def from_approved(
    proposal: Proposal,
    *,
    record: object | None,
    zone: tzinfo | None,
    machine_zone: tzinfo | None = None,
) -> Commitment:
    """The commitment an approved proposal records: its `pending` line's values (15a).

    `record` is the meeting record (`MeetingRecord`) or `None` when it could
    not be read, in which case the line has no title, start or day and the due
    is `unresolved`. `zone` is `display_timezone`, `None` when unset.
    `NoApprovalInstant` when the proposal records no `changed_at`; `TooLong`
    when a stored value is over its limit.
    """
    if proposal.changed_at is None:
        raise NoApprovalInstant(f"{proposal.proposal_id} records no approval instant.")
    what, owner, speaker, said = _values(proposal)
    what_cut = False
    if proposal.type == IMPLICIT_COMMITMENT and len(what) > WHAT_LIMIT:
        # A spoken promise is never refused for its length (Andrei,
        # 2026-10-24): `11b`'s pattern bounds nothing, so its `what` is cut
        # at a word and marked; the full text stays in the proposal body.
        what, what_cut = _cut_at_word(what, WHAT_LIMIT)
    _check("what", what, WHAT_LIMIT)
    _check("owner", owner, OWNER_LIMIT)
    _check("speaker", speaker, OWNER_LIMIT)
    _check("due as said", said, DUE_LIMIT)
    title: str | None = None
    start: datetime | None = None
    title_cut = False
    meeting = getattr(record, "meeting", None)
    if meeting is not None:
        title, title_cut = _cut_title(meeting.title)
        start = meeting.start
    day, day_from = day_of(record, zone=zone, machine_zone=machine_zone)
    due = resolve(said, day, estimated=day_from is not None)
    meeting_id = proposal.meeting_id
    return Commitment(
        commitment_id=root_of(proposal.proposal_id),
        proposal_id=proposal.proposal_id,
        type=proposal.type,
        meeting_id=meeting_id,
        title=title,
        start=start,
        day=day,
        day_from=day_from,
        what=what,
        owner=owner,
        speaker=speaker,
        due=due,
        state=CommitmentState.PENDING,
        state_source=StateSource.NONE,
        approved_at=proposal.changed_at,
        cites=Cites(
            meeting=None if meeting_id is None else f"meeting:{meeting_id}",
            person=speaker if proposal.type == IMPLICIT_COMMITMENT else owner,
        ),
        title_cut=title_cut,
        what_cut=what_cut,
    )


def revised_from(
    proposal: Proposal,
    current: Commitment,
    head: Head,
    *,
    via: str,
    written: str,
    at: datetime,
) -> Revised:
    """The `revised` line for an approved replacement newer than the newest value line (15a).

    It holds the whole set of values after the edit, and the due only when the
    due as said changed — resolved against the day the `pending` line stored,
    never against the zone or clock of the moment.
    """
    if proposal.changed_at is None:
        raise NoApprovalInstant(f"{proposal.proposal_id} records no change instant.")
    what, owner, _, said = _values(proposal)
    _check("what", what, WHAT_LIMIT)
    _check("owner", owner, OWNER_LIMIT)
    _check("due as said", said, DUE_LIMIT)
    said = said if said is not None and said.strip() else None
    due: Due | None = None
    if said != (head.due_said or None):
        due = resolve(said, current.day, estimated=current.day_from is not None)
    return Revised(
        commitment=current.commitment_id,
        proposal=proposal.proposal_id,
        replaces=head.proposal,
        what=what,
        owner=owner,
        due=due,
        occurred=proposal.changed_at,
        via=via,
        written=written,
        at=at,
    )


# ── Reading ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Read:
    """One complete line that parsed: its kind's fields."""

    entry: str
    commitment: str
    kind: LineKind
    fields: Mapping[str, str]
    occurred: datetime
    text: str


@dataclass(frozen=True, slots=True)
class ParsedLedger:
    """The ledger's lines in file order, first-wins by entry, and what was skipped."""

    lines: tuple[_Read, ...]
    malformed: int
    malformed_roots: frozenset[str]
    duplicates: int
    conflicts: int
    size: int


@dataclass(frozen=True, slots=True)
class Head:
    """A commitment's newest value line: what a `revised` line replaces."""

    proposal: str
    number: int
    due_said: str | None
    """The due as said in force from the value lines."""


@dataclass(frozen=True, slots=True)
class MarkInForce:
    """The PM's own mark the state comes from (story 15c)."""

    mark: Mark
    entry: str
    occurred: datetime
    via: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class NewestLine:
    """A commitment's newest line in file order: what its version names (15c)."""

    entry: str
    kind: LineKind
    fields: Mapping[str, str]
    occurred: datetime


@dataclass(frozen=True, slots=True)
class Details:
    """What 15c's commands need of one commitment besides its values (story 15c).

    `version` is the `entry` of its newest line in file order: any line
    appended for it changes it, a line of another commitment never does, and
    it is compared only for equality (entry ids are hashes, not counters).
    `mark` is the PM's own mark the state comes from, or `None`; `due_by_pm`
    and `owner_by_pm` say whether the due and the owner in force came from
    his `due_set` or `owner_set`; `changed_at` is the newest `occurred` among
    its lines; `due_said` the due as said from the value lines, kept beside
    a date the PM set. `undone` is the mark entry an applied `undone` line
    undid when that line is the newest, with the mark's `occurred` in
    `undone_mark_at`. `proposals` are the proposal ids its value lines name.
    """

    version: str
    newest: NewestLine
    mark: MarkInForce | None
    due_by_pm: bool
    owner_by_pm: bool
    changed_at: datetime
    due_said: str | None = None
    undone: str | None = None
    undone_mark_at: datetime | None = None
    """The `occurred` of the done mark that was undone — not the undo's own time."""
    proposals: frozenset[str] = frozenset()
    state_at: datetime | None = None
    """The `occurred` of the line the state in force came from; `None` for `PENDING`."""
    done_marks: frozenset[str] = frozenset()
    """The entries of the PM's `marked state=fulfilled` lines: a version a surface
    showed after its done mark, which `undo` checks against."""
    closed_at: datetime | None = None
    """Story 15d: the `occurred` of the first line, in fold order, of the run of
    lines since which the state has been terminal without a break; `None` while
    it is open. A second verdict, or the PM's mark confirming telemetry's, does
    not move it, so a closure is counted in one window only."""


@dataclass(frozen=True, slots=True)
class CommitmentLedger:
    """The ledger folded (story 15a): the commitments by id, and what was ignored.

    `malformed`, `orphans`, `duplicates`, `conflicts` and `refused` (marks the
    table did not allow, and `undone` lines that did not apply) count lines
    that never fail the read. `malformed_roots` are the roots a malformed line
    names, for which no `pending` line is written until the PM fixes or
    removes it. `size` is the bytes read, the `after` of the next append.
    `details` is 15c's per-commitment version, mark in force and sources.
    """

    commitments: Mapping[str, Commitment] = field(default_factory=lambda: MappingProxyType({}))
    heads: Mapping[str, Head] = field(default_factory=lambda: MappingProxyType({}))
    details: Mapping[str, Details] = field(default_factory=lambda: MappingProxyType({}))
    entries: frozenset[str] = frozenset()
    malformed: int = 0
    orphans: int = 0
    duplicates: int = 0
    conflicts: int = 0
    refused: int = 0
    malformed_roots: frozenset[str] = frozenset()
    size: int = 0


_REQUIRED: Mapping[LineKind, frozenset[str]] = {
    LineKind.PENDING: frozenset({"proposal", "type", "what", "due", "via", "written", "at"}),
    LineKind.REVISED: frozenset({"proposal", "replaces", "what", "via", "written", "at"}),
    LineKind.MARKED: frozenset({"state", "by", "via", "at"}),
    LineKind.DUE_SET: frozenset({"due", "by", "via", "at"}),
    LineKind.OWNER_SET: frozenset({"by", "via", "at"}),
    LineKind.VERDICT: frozenset({"state"}),
    LineKind.UNDONE: frozenset({"undone_entry", "by", "via", "at"}),
}


def _aware(text: str) -> datetime:
    at = datetime.fromisoformat(text)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("no timezone")
    return at


def _day_ok(value: str) -> bool:
    if not _DAY.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _due_ok(value: str) -> bool:
    return value in (DUE_NONE, DUE_UNRESOLVED) or _day_ok(value)


def _parse(line: str) -> _Read | None:
    """One complete line read, or `None` when it is not a line of the grammar."""
    if not line.startswith("- "):
        return None
    try:
        pairs = scan_fields(line[2:], line=line)
    except MalformedEntry:
        return None
    if len(pairs) < 3 or [key for key, _ in pairs[:3]] != ["entry", "commitment", "kind"]:
        return None
    found: dict[str, str] = {}
    for key, value in pairs:
        if not key or key in found:
            return None
        found[key] = value
    try:
        kind = LineKind(found["kind"])
    except ValueError:
        return None
    if not _ENTRY.fullmatch(found["entry"]) or not found["commitment"]:
        return None
    if not _REQUIRED[kind] <= found.keys() or "occurred" not in found:
        return None
    try:
        occurred = _aware(found["occurred"])
        if "at" in found:
            _aware(found["at"])
        if "start" in found:
            _aware(found["start"])
        if "day" in found and not _day_ok(found["day"]):
            return None
    except ValueError:
        return None
    if kind is LineKind.PENDING:
        if found["type"] not in COMMITMENT_TYPES or found["written"] not in _WRITTEN:
            return None
        if not _due_ok(found["due"]) or ("title" in found) != ("start" in found):
            return None
        if "day_from" in found and "day" not in found:
            return None
    elif kind is LineKind.REVISED:
        if found["written"] not in _WRITTEN:
            return None
        if "due_said" in found and "due" not in found:
            return None
        if "due" in found and not _due_ok(found["due"]):
            return None
    elif kind is LineKind.MARKED:
        if found["by"] != "pm" or found["state"] not in {mark.value for mark in Mark}:
            return None
    elif kind is LineKind.DUE_SET:
        if found["by"] != "pm" or found["due"] == DUE_UNRESOLVED or not _due_ok(found["due"]):
            return None
    elif kind is LineKind.OWNER_SET:
        if found["by"] != "pm":
            return None
    elif kind is LineKind.VERDICT and found["state"] not in _VERDICT_STATES:
        return None
    elif kind is LineKind.UNDONE:
        if found["by"] != "pm" or not _ENTRY.fullmatch(found["undone_entry"]):
            return None
    return _Read(found["entry"], found["commitment"], kind, MappingProxyType(found), occurred, line)


def parse_ledger(data: bytes) -> ParsedLedger:
    """The ledger's complete lines, each decoded on its own (story 15a).

    A final line without its newline is a write in progress: dropped, not
    counted. Blank lines and lines starting `#` (a heading the PM wrote) are
    not records and not counted. A line that is not valid UTF-8, does not
    parse, has an unknown kind or a bad state is skipped and counted
    `malformed`. A later line equal to an earlier one is a duplicate; one with
    an earlier one's entry and other content is a conflict, and the first in
    file order wins.
    """
    # A byte-order mark an editor may add is not part of the first line; the
    # size read stays the file's own, the next append's `after`.
    body = data[len(_BOM):] if data.startswith(_BOM) else data
    pieces = body.split(b"\n")
    complete = pieces[:-1]  # the last piece is empty, or a torn line
    lines: list[_Read] = []
    by_entry: dict[str, str] = {}
    malformed = duplicates = conflicts = 0
    roots: set[str] = set()
    for raw in complete:
        raw = raw.rstrip(b"\r")
        if not raw.strip() or raw.lstrip().startswith(b"#"):
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        parsed = None if text is None else _parse(text)
        if parsed is None:
            malformed += 1
            named = _NAMES_A_ROOT.search(raw)
            if named is not None:
                roots.add(named.group(1).decode("utf-8", errors="replace"))
            continue
        seen = by_entry.get(parsed.entry)
        if seen is not None:
            if seen == parsed.text:
                duplicates += 1
            else:
                conflicts += 1
            continue
        by_entry[parsed.entry] = parsed.text
        lines.append(parsed)
    return ParsedLedger(tuple(lines), malformed, frozenset(roots), duplicates, conflicts, len(data))


def _number(proposal_id: str) -> int:
    return replacement_number(proposal_id) or 0


def _optional(fields: Mapping[str, str], key: str) -> str | None:
    value = fields.get(key)
    return value if value is not None and value != "" else None


def _due_of(fields: Mapping[str, str], *, estimated: bool) -> Due:
    value = fields["due"]
    said = _optional(fields, "due_said")
    if value in (DUE_NONE, DUE_UNRESOLVED):
        return Due(said, value)
    return Due(said, value, estimated)


def _order(line: _Read) -> tuple[datetime, str]:
    return (line.occurred, line.entry)


def _value_order(line: _Read) -> tuple[int, datetime, str]:
    return (_number(line.fields["proposal"]), line.occurred, line.entry)


def _undone_marks(lines: Sequence[_Read]) -> tuple[set[str], int]:
    """The done marks the `undone` lines undo, and how many `undone` lines did not apply (15c).

    An `undone` line applies only when it names a `marked state=fulfilled
    by=pm` line of the same commitment, its `occurred` is within
    `UNDO_WINDOW` of that mark's, and no other line of the commitment sorts
    between the two by `(occurred, entry)`. The fold stays deterministic
    (AD-35): every condition is read off the lines' own fields.
    """
    by_entry = {line.entry: line for line in lines}
    dropped: set[str] = set()
    refused = 0
    for undo in (line for line in lines if line.kind is LineKind.UNDONE):
        mark = by_entry.get(undo.fields["undone_entry"])
        applies = (
            mark is not None
            and mark.kind is LineKind.MARKED
            and mark.fields.get("state") == Mark.FULFILLED.value
            and mark.fields.get("by") == "pm"
            and timedelta(0) <= undo.occurred - mark.occurred <= UNDO_WINDOW
            and not any(
                _order(mark) < _order(other) < _order(undo)
                for other in lines
                if other.entry not in (mark.entry, undo.entry)
            )
        )
        if applies and mark is not None:
            dropped.add(mark.entry)
        else:
            refused += 1
    return dropped, refused


def _owner_line(pending: _Read, values: Sequence[_Read], lines: Sequence[_Read]) -> _Read:
    """The line the owner in force comes from (story 15c's fix to 15a's fold).

    The newest by `(occurred, entry)` of the `pending` line, each `revised`
    line that changed the owner — compared with the value line before it in
    value order, no owner counted as a value — and the `owner_set` lines. A
    `revised` line always carries the proposal's owner, so one that did not
    change it never brings back an owner the PM replaced.
    """
    ordered = sorted(values, key=_value_order)
    candidates = [pending]
    for previous, line in zip(ordered, ordered[1:]):
        if line.kind is LineKind.REVISED and _optional(previous.fields, "owner") != _optional(
            line.fields, "owner"
        ):
            candidates.append(line)
    candidates += [line for line in lines if line.kind is LineKind.OWNER_SET]
    return max(candidates, key=_order)


FOLD_VERSION = 2
"""What `fold` computes from a ledger, as a number (story 15b; 2 since 15d added `closed_at`).

`commitment_index.db` stores it beside its own format: an index derived by
an older fold is dropped and rebuilt, never served as current. Bump it in the
same change as any change to what `fold` computes (a new line kind, a changed
rule)."""


def _closed_since(
    closed_at: datetime | None, before: CommitmentState, after: CommitmentState, at: datetime
) -> datetime | None:
    """Story 15d: when the run of terminal states began, after one applied line.

    A line that leaves the state terminal keeps the run's start; one that
    makes it terminal from open starts it; one that reopens it ends it.
    """
    if not after.is_terminal:
        return None
    if before.is_terminal and closed_at is not None:
        return closed_at
    return at


def fold(parsed: ParsedLedger) -> CommitmentLedger:
    """Each commitment from its lines, in an order of their own fields (AD-35, 15a).

    Values (`proposal`, `what`) from the newest `pending` or `revised` line
    by `(replacement number, occurred, entry)`; the owner from the newest by
    `(occurred, entry)` of the `pending` line, each `revised` line that
    changed the owner, and the `owner_set` lines (15c); the due from the
    newest by `(occurred, entry)` of the `pending` line, `revised` lines that
    carry one, and `due_set` lines; the state from the `marked` and `verdict`
    lines by `(occurred, entry)`: a `marked` line where `can_mark` allows it
    and no applied `undone` line undid it (15c), a `verdict` unless the state
    in force is the PM's own mark other than `altered` (after `altered` the
    commitment is still tracked, Andrei, 2026-10-24); else `PENDING`. A line
    for a commitment with no `pending` line is an orphan.

    A change to what this computes bumps `FOLD_VERSION` in the same change
    (story 15b), so the commitment index it was derived into is rebuilt.
    """
    groups: dict[str, list[_Read]] = {}
    for line in parsed.lines:
        groups.setdefault(line.commitment, []).append(line)
    commitments: dict[str, Commitment] = {}
    heads: dict[str, Head] = {}
    details: dict[str, Details] = {}
    orphans = refused = 0
    for root, lines in groups.items():
        pendings = [line for line in lines if line.kind is LineKind.PENDING]
        if not pendings:
            orphans += len(lines)
            continue
        pending = min(pendings, key=_order)
        fields = pending.fields
        estimated = "day_from" in fields
        values = [line for line in lines if line.kind in (LineKind.PENDING, LineKind.REVISED)]
        newest = max(values, key=_value_order)
        said_lines = [line for line in values if line.kind is LineKind.PENDING or "due" in line.fields]
        said_in_force = max(said_lines, key=_value_order)
        owner_line = _owner_line(pending, values, lines)
        due_lines = [
            line
            for line in lines
            if line.kind is LineKind.PENDING
            or (line.kind is LineKind.REVISED and "due" in line.fields)
            or line.kind is LineKind.DUE_SET
        ]
        due_line = max(due_lines, key=_order)
        if due_line.kind is LineKind.DUE_SET:
            due = Due(None, due_line.fields["due"])
        else:
            due = _due_of(due_line.fields, estimated=estimated)
        dropped, not_applied = _undone_marks(lines)
        refused += not_applied
        state, source = CommitmentState.PENDING, StateSource.NONE
        in_force: MarkInForce | None = None
        state_at: datetime | None = None
        closed_at: datetime | None = None
        for line in sorted(
            (line for line in lines if line.kind in (LineKind.MARKED, LineKind.VERDICT)),
            key=_order,
        ):
            if line.entry in dropped:
                # Story 15c: the PM undid this done mark; the state is the one before it.
                continue
            if line.kind is LineKind.MARKED:
                mark = Mark(line.fields["state"])
                if can_mark(state, source, mark):
                    closed_at = _closed_since(closed_at, state, mark.state, line.occurred)
                    state, source = mark.state, StateSource.PM
                    in_force = MarkInForce(
                        mark, line.entry, line.occurred, line.fields["via"], _optional(line.fields, "note")
                    )
                    state_at = line.occurred
                else:
                    refused += 1
            elif source is not StateSource.PM or state is CommitmentState.ALTERED:
                verdict = CommitmentState(line.fields["state"])
                closed_at = _closed_since(closed_at, state, verdict, line.occurred)
                state, source = verdict, StateSource.TELEMETRY
                in_force = None
                state_at = line.occurred
        last = lines[-1]
        undone_mark: str | None = None
        undone_mark_at: datetime | None = None
        if last.kind is LineKind.UNDONE and last.fields["undone_entry"] in dropped:
            undone_mark = last.fields["undone_entry"]
            undone_mark_at = next(line.occurred for line in lines if line.entry == undone_mark)
        details[root] = Details(
            version=last.entry,
            newest=NewestLine(last.entry, last.kind, last.fields, last.occurred),
            mark=in_force,
            due_by_pm=due_line.kind is LineKind.DUE_SET,
            owner_by_pm=owner_line.kind is LineKind.OWNER_SET,
            changed_at=max(line.occurred for line in lines),
            due_said=_optional(said_in_force.fields, "due_said"),
            undone=undone_mark,
            undone_mark_at=undone_mark_at,
            proposals=frozenset(line.fields["proposal"] for line in values),
            state_at=state_at,
            done_marks=frozenset(
                line.entry
                for line in lines
                if line.kind is LineKind.MARKED and line.fields["state"] == Mark.FULFILLED.value
            ),
            closed_at=closed_at,
        )
        speaker = _optional(fields, "speaker")
        owner = _optional(owner_line.fields, "owner")
        meeting_id = _optional(fields, "meeting")
        kind = fields["type"]
        commitments[root] = Commitment(
            commitment_id=root,
            proposal_id=newest.fields["proposal"],
            type=kind,
            meeting_id=meeting_id,
            title=_optional(fields, "title"),
            start=_aware(fields["start"]) if "start" in fields else None,
            day=date.fromisoformat(fields["day"]) if "day" in fields else None,
            day_from=_optional(fields, "day_from"),
            what=newest.fields["what"],
            owner=owner,
            speaker=speaker,
            due=due,
            state=state,
            state_source=source,
            approved_at=pending.occurred,
            cites=Cites(
                meeting=None if meeting_id is None else f"meeting:{meeting_id}",
                person=speaker if kind == IMPLICIT_COMMITMENT else owner,
            ),
            title_cut=fields.get("title_cut") == "yes",
            what_cut=newest.fields.get("what_cut") == "yes",
        )
        heads[root] = Head(
            proposal=newest.fields["proposal"],
            number=_number(newest.fields["proposal"]),
            due_said=_optional(said_in_force.fields, "due_said"),
        )
    return CommitmentLedger(
        commitments=MappingProxyType(commitments),
        heads=MappingProxyType(heads),
        details=MappingProxyType(details),
        entries=frozenset(line.entry for line in parsed.lines),
        malformed=parsed.malformed,
        orphans=orphans,
        duplicates=parsed.duplicates,
        conflicts=parsed.conflicts,
        refused=refused,
        malformed_roots=parsed.malformed_roots,
        size=parsed.size,
    )


# ── The accessor ─────────────────────────────────────────────────────────────


class CommitmentLog:
    """The one accessor for `commitments_log.md` (`derivation-services.md` rule 3).

    A vocabulary over `StoragePort`, as `MeetingRecords` and `EventLog` are: it
    holds no path and opens no file. Every writer uses it — the approval and
    the catch-up (15a), the PM's marks (15c), the verdicts (16) — and the
    indexer (15b) and `10a`'s job read through it.
    """

    def __init__(self, storage: StoragePort) -> None:
        self._storage = storage

    def read(self, scope: DataScope) -> CommitmentLedger:
        """The scope's ledger folded. `NotACommitmentScope` for a scope that keeps none."""
        return self.read_with_bytes(scope)[0]

    def read_with_bytes(self, scope: DataScope) -> tuple[CommitmentLedger, bytes]:
        """The scope's ledger folded, and the bytes it was folded from (15c).

        15c's desk keeps the bytes so that, when an append succeeded and the
        read after it fails, it can fold what it read plus what it appended.
        """
        data = self._storage.read_commitments(scope=scope)
        return fold(parse_ledger(data)), data

    def append(self, lines: Sequence[CommitmentLine], *, scope: DataScope, after: int) -> int:
        """Append `lines` in one write if the ledger is still `after` bytes; the new size.

        Only `CommitmentLine` values, which only `render_line` builds:
        anything else is refused with `NotACommitmentLine` before the store is
        called (AD-28). `LedgerChanged` when the ledger grew since it was read.
        """
        texts: list[str] = []
        for line in lines:
            if not isinstance(line, CommitmentLine):
                raise NotACommitmentLine(
                    f"a {type(line).__name__} is not a commitment line, so it is not "
                    f"written to a commitment ledger (AD-28)."
                )
            texts.append(line.text)
        return self._storage.append_commitments(texts, scope=scope, after=after)


# ── What an approval says about its commitment ───────────────────────────────

REASONS: Mapping[str, str] = MappingProxyType(
    {
        "busy": "the ledger was busy",
        "git_not_excluded": "git does not exclude the project's memory/",
        "disk": "the ledger could not be written",
        "ledger_unreadable": "the ledger could not be read",
        "ledger_malformed": (
            "a line of commitments_log.md that names this item does not parse; fix or "
            "remove that line"
        ),
        "too_long": "a stored value is over its field's limit",
        "scope_refused": "its scope keeps no commitment ledger",
        "no_approval_time": "the store records no instant for its approval",
    }
)
"""The reason codes, and the default words for each."""

PERMANENT = frozenset({"too_long", "scope_refused", "no_approval_time"})
"""Reasons that will not change by trying again."""


@dataclass(frozen=True, slots=True)
class Reason:
    code: str
    text: str

    @property
    def permanent(self) -> bool:
        return self.code in PERMANENT


@dataclass(frozen=True, slots=True)
class Recording:
    """What one approval did about its commitment: the JSON `commitment` object (15a)."""

    commitment_id: str
    scope: str
    state: CommitmentState
    recorded: bool
    """A line for this root is in the ledger now."""
    repaired: bool
    """This command wrote a line an earlier approval had left unwritten."""
    reason: Reason | None
    day: date | None
    day_from: str | None
    due: Due
    wrote: LineKind | None = None
    """The kind of line this command wrote, if any."""


def recording_json(recording: Recording | None) -> dict[str, object] | None:
    """The approve and edit objects' `commitment` (story 15a); `None` for no commitment."""
    if recording is None:
        return None
    due = recording.due
    found = due.date
    return {
        "commitment_id": recording.commitment_id,
        "scope": recording.scope,
        "state": recording.state.value,
        "recorded": recording.recorded,
        "repaired": recording.repaired,
        "reason": None
        if recording.reason is None
        else {"code": recording.reason.code, "text": recording.reason.text},
        "day": None if recording.day is None else recording.day.isoformat(),
        "day_from": recording.day_from,
        "due": {
            "said": due.said,
            "value": due.value,
            "date": None if found is None else found.isoformat(),
            "estimated": due.estimated,
        },
    }
