"""One vocabulary over a scope's `meetings/` (`derivation-services.md`, rule 3).

`Meeting` is the citation root for everything said in a meeting (AD-33) and is
declared Tier-1 in three scope trees. Until this module existed it was never
persisted: `Daemon.meetings` was a plain `dict[str, object]` the transcript
pipeline wrote into, so every citation minted resolved against process memory
and died with the process. This is the accessor rule 3 names, and it is the
third of the three — `CommitmentLog` and `EventLog` are the others.

**It is a vocabulary, not a second writer.** Every byte moves through the single
writer (AD-5). This holds no path, opens no file, and depends on `StoragePort`
rather than the concrete service.

## Where a record lands

`put` writes to `meeting.scope` and never to a scope it was handed
(AD-33/AD-38). `Meeting.scope` is required rather than defaulted precisely
because it decides where the record goes and whether a git-committed scope may
cite it, so the accessor honours the field and never guesses. `get` and
`for_day` take a scope explicitly, because there is no `Meeting` to ask.

Nothing here checks the *citation direction* — whether a project-scoped daemon
may cite this meeting at all. `assert_citation_legal` owns that and is called by
`pm_ai.app.pipelines` before extraction, which is before this accessor is
reached; moving the record to disk does not move that check.

## The record is the ledger's field grammar, without the ledger's envelope

One `key=value` per line, rendered by `render_value` and parsed by `scan_fields`
— then a free body with sections. It is deliberately **not** a ledger line:
`ledger.parse_line` requires the `- [id] category actor=` envelope and refuses
duplicate keys, and this is a document. A reader who assumes `parse_line`
applies here will be wrong, which is why the difference is stated rather than
left to be discovered.

`attendees` is one comma-separated value, and comma is therefore reserved: the
grammar has no lists, and repeated fields are what `parse_line` refuses. Only
the *handle* — `Actor.actor_id` — is stored, and an `actor_id` carrying a comma
is refused, because a display name like "Smith, Bob" would split into two
attendees and nothing would notice. `Actor.display_name` is consequently **not
part of the record**: `as_stored` is what a caller can compare against, and
`get` returns handle-only actors. AD-34 makes a display name an alias rather
than an identity, so the alias table is its home; a record that carried one
would be a second place it could be wrong.

**Man-Hour Cost is never stored.** CAP-1 puts it in the summary card's header,
which is a rendered surface, and it derives from `blended_hourly_rate` in
`config.toml` — so a stored value is wrong the moment the rate changes. The
record stores `attendees` and `duration_minutes`; `Meeting.man_hour_cost`
multiplies.

## Only meetings that have happened are recorded

Decided 2026-09-07: a future meeting is not persisted at all. The calendar is its
source of truth, and a local copy of a record that can be moved or cancelled
outside pm-ai at any moment cannot be kept accurate — it is duplication whose
staleness the system would then have to model. Consumers that want a meeting
which has not yet happened read the calendar live (story `33b`).

So **neither `tentative` nor `stale` is a field here.** `tentative` is a response
status for a meeting that has not occurred, and `stale` means "absent from a
window pm-ai actually harvested", which is a cancellation; both are questions
about the future and neither has an answer about a meeting already held. `33c`'s
amended spec keeps the flag useful without making it durable — it is to ride on
the in-memory `Meeting` that slice returns, and reach no file. That is a
statement about `33c`, which is unimplemented: `Meeting` has no such field
today, and the only `tentative` elsewhere in `pm_ai` is the connector's parse of
Graph's response status.

That is also what makes a record's identity stable rather than volatile: a held
meeting's `start` cannot move, so keying the filename on its UTC day is sound for
the life of the citation.

## Machine-owned and human-owned regions

pm-ai owns the fields and `## Summary`; `## Notes` is the human's and is copied
through verbatim on every rewrite. That is what lets this slice replace fields
on a rewrite *and* honour the rule that a hand edit is never silently
overwritten — the regions differ, so both hold.

`## Summary` is reserved and left empty here. It is transcript-derived and needs
a model, which is out of waves 1 and 2; amendments — the other half of that
design — are queued in `deferred-work.md` with the reasoning that removed them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone, tzinfo

from pm_ai.domain.event_entries import MalformedEntry, render_value, scan_fields
from pm_ai.domain.identity import Actor, DataScope
from pm_ai.domain.meetings import Meeting
from pm_ai.ports import StoragePort

__all__ = [
    "MEETINGS",
    "NOTES_HEADING",
    "SUMMARY_HEADING",
    "MalformedMeeting",
    "MeetingDisplaced",
    "MeetingNotFound",
    "MeetingRecord",
    "MeetingRecords",
    "as_stored",
    "parse_record",
    "record_name",
    "render_record",
]


MEETINGS = "meetings/"
"""The artifact key, spelled once.

A `Collection` in the personal, people and project trees, and the trailing slash
is the node type rather than a convention typed by hand. Named here so the three
methods below cannot disagree about which artifact they address.
"""

SUMMARY_HEADING = "## Summary"
NOTES_HEADING = "## Notes"

_SLUG_LIMIT = 32
"""How much of a meeting id is kept in the filename, for a human reading `ls`.

The digest that follows it is what makes the name unique, so this prefix is
allowed to be lossy. Bounded because a real Graph event id runs to hundreds of
characters and `write_artifact` refuses a name past 128 bytes.
"""

_UNSAFE_IN_SLUG = re.compile(r"[^A-Za-z0-9._-]")

_RECORD_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-.+\.md$")
"""What this module's own writes look like, and nothing else in the directory.

`.DS_Store` never reaches here — `list_collection` omits dot-prefixed entries —
but an editor's `…​.md~` or `.md.swp` sibling does, and parsing one as a record
is a malformed-record refusal for a file pm-ai did not write.
"""

_MEETING_ID = "meeting_id"
_TITLE = "title"
_START = "start"
_DURATION = "duration_minutes"
_ATTENDEES = "attendees"
_SCOPE = "scope"
_CALENDAR_REF = "calendar_event_ref"

_FIELDS: tuple[str, ...] = (
    _MEETING_ID,
    _TITLE,
    _START,
    _DURATION,
    _ATTENDEES,
    _SCOPE,
    _CALENDAR_REF,
)
"""Every key the record may carry, in render order.

`calendar_event_ref` is the only optional one: it is omitted when `None` and
rendered — possibly empty — otherwise, which is what distinguishes "no calendar
event" from "an event whose reference is the empty string" across a round trip.
"""

_REQUIRED: frozenset[str] = frozenset(_FIELDS) - {_CALENDAR_REF}


# ── Refusals ─────────────────────────────────────────────────────────────────


class MeetingNotFound(KeyError):
    """No record in that scope carries that id.

    A `KeyError`, matching `ProposalNotFound`: the caller asked for one record by
    id and there is none. Deliberately *not* an empty `Meeting` — a record whose
    every field is a default is indistinguishable from a real meeting that has
    not been filled in, and something downstream would cite it.
    """


class MalformedMeeting(ValueError):
    """A record that cannot be rendered, or one on disk that cannot be read.

    Both directions, one name, for the reason `MalformedEntry` covers both for a
    ledger line: render and parse have to move together, and a value this module
    will not write is a value it must not accept back.

    Surfaced rather than skipped. A `meetings/` file someone hand-edited into
    nonsense is a dashboard row that silently disappears, which is worse than a
    refusal naming the file — the same rule the scope-model guards hold.
    """


class MeetingDisplaced(ValueError):
    """A record for this id already exists under another day's name.

    The record's filename carries the UTC day of its `start`, which is what lets
    `for_day` read one day without opening every file in the collection. A
    meeting whose start moves to a different UTC day therefore belongs under a
    different name, and publishing the new one would leave the old one behind:
    the single writer offers no delete, so one id would have two records and
    `get` would have to pick.

    Refused rather than either silently duplicated or silently left under the
    stale name — under the stale name `for_day` would never find it again, which
    is a wrong answer rather than an error.

    **The 2026-09-07 decision made this unreachable, and it is kept anyway.**
    Only meetings that have already happened are recorded, and a held meeting's
    `start` cannot move, so no legitimate `put` can now land a second day's name
    on an existing id. Kept because a guard that refuses an impossible state is
    correct and cheap: reaching it would mean something upstream had recorded a
    meeting that had not happened, and a refusal names that at the write rather
    than leaving `get` to pick between two records.
    """


# ── The record ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MeetingRecord:
    """What one file in `meetings/` holds: the meeting, plus the human's notes.

    Two parts rather than a fattened `Meeting`, because they have two different
    owners. `meeting` is the domain entity. `notes` is the human's, and is never
    an argument to `put` — the accessor reads it off the existing record and
    writes it back, so no caller can set, clear or regenerate it.

    There is no provider region. `tentative` was one until the 2026-09-07
    decision that no future meeting is recorded: a response status is an answer
    about a meeting that has not occurred, and every record here is of one that
    has.
    """

    meeting: Meeting
    notes: str = ""


def as_stored(meeting: Meeting) -> Meeting:
    """The meeting as the record can hold it — handles, without display names.

    The comparison a caller needs to state the render/parse property honestly.
    `attendees` is one comma-separated value and comma is reserved, so a display
    name cannot be stored beside a handle; `get` therefore returns actors with
    `display_name=None`, and this is what "equal after a round trip" means for a
    meeting that arrived with display names attached.

    Everything else round-trips unchanged, so this is the whole of the loss.
    """
    return replace(
        meeting,
        attendees=tuple(Actor(actor_id=attendee.actor_id) for attendee in meeting.attendees),
    )


def record_name(meeting_id: str, start: datetime) -> str:
    """The single-component filename one record lives under.

    `<utc-day>-<slug>-<sha256 of the id>.md`, and each of the three parts is
    load-bearing:

    - **the day** so `for_day` can read one day's records without opening every
      file in the collection. Without it, one hand-mangled record for an
      unrelated date would refuse every dashboard render;
    - **the slug** so an operator reading `ls meetings/` sees something of the
      meeting. Lossy on purpose — every character outside `[A-Za-z0-9._-]`
      becomes `_` — because the digest is what makes the name unique;
    - **the digest** so the name is safe *and* injective for a provider id that
      is neither. A real Graph event id runs to hundreds of characters, may lead
      with a dot and carries `=` and `/`; `write_artifact` refuses such a name
      outright, and truncating one would collide. The id itself is preserved
      inside the record, which is where `get` reads it back from.

    The name is derived, never stored, so it stays stable for the life of the
    record exactly as long as the id and the start day do — which is what a
    citation surviving the 30-day transcript purge rests on.
    """
    _assert_recordable_id(meeting_id)
    day = _assert_utc(start, where="Meeting.start").date().isoformat()
    digest = hashlib.sha256(meeting_id.encode("utf-8")).hexdigest()
    return f"{day}-{_slug(meeting_id)}-{digest}.md"


def render_record(record: MeetingRecord) -> str:
    """One record as the text written to disk, with a terminating newline policy.

    The fields come first, one per line, then a blank line, then the two
    sections. `## Notes` is last and its content is emitted verbatim, so
    whatever the human wrote — trailing newline or not, further headings of
    their own or not — comes back byte-identical from `parse_record`.
    """
    meeting = record.meeting
    _assert_recordable_id(meeting.meeting_id)
    start = _assert_utc(meeting.start, where="Meeting.start")
    duration = _assert_duration(meeting.duration_minutes)
    values: dict[str, str] = {
        _MEETING_ID: meeting.meeting_id,
        _TITLE: meeting.title,
        _START: start.isoformat(),
        _DURATION: str(duration),
        _ATTENDEES: _render_attendees(meeting.attendees),
        _SCOPE: str(meeting.scope),
    }
    if meeting.calendar_event_ref is not None:
        values[_CALENDAR_REF] = meeting.calendar_event_ref
    lines = [
        f"{key}={render_value(values[key], where=f'field {key!r}')}"
        for key in _FIELDS
        if key in values
    ]
    head = "\n".join(lines)
    return f"{head}\n\n{SUMMARY_HEADING}\n\n{NOTES_HEADING}\n{record.notes}"


def parse_record(text: str, *, source: str) -> MeetingRecord:
    """The inverse of `render_record`; `source` names the file in every refusal.

    The field block is the leading run of lines, ending at the first blank line
    or the first `##` heading. A line in it that is not one `key=value` pair is a
    refusal rather than a skip, and so is a duplicate key — the rule
    `ledger.parse_line` holds, for the reason it holds it: a second `title=`
    silently winning is a record whose content depends on line order.

    Everything after the `## Notes` heading is the human's region, returned
    exactly as it was read. A record with no `## Notes` heading at all yields
    empty notes rather than a refusal: the machine-owned region is intact, the
    human region is simply absent, and the next `put` writes the heading back.
    """
    lines = text.splitlines(keepends=True)
    notes_at = next(
        (
            index
            for index, line in enumerate(lines)
            if line.rstrip("\r\n") == NOTES_HEADING
        ),
        None,
    )
    notes = "" if notes_at is None else "".join(lines[notes_at + 1 :])
    head = lines if notes_at is None else lines[:notes_at]

    fields: dict[str, str] = {}
    for raw in head:
        line = raw.rstrip("\r\n")
        if not line.strip() or line.startswith("##"):
            break
        key, value = _parse_field(line, fields=fields, source=source)
        fields[key] = value

    missing = sorted(_REQUIRED - fields.keys())
    if missing:
        raise MalformedMeeting(
            f"{source} is missing {missing}. A record without them is not a "
            f"meeting anything may cite: pm-ai owns the field block, so a hand "
            f"edit that removed one is a repair to make rather than a default "
            f"to invent."
        )
    return MeetingRecord(
        meeting=Meeting(
            meeting_id=fields[_MEETING_ID],
            title=fields[_TITLE],
            start=_parse_start(fields[_START], source=source),
            duration_minutes=_parse_duration(fields[_DURATION], source=source),
            attendees=_parse_attendees(fields[_ATTENDEES], source=source),
            scope=_parse_scope(fields[_SCOPE], source=source),
            calendar_event_ref=fields.get(_CALENDAR_REF),
        ),
        notes=notes,
    )


# ── The accessor ─────────────────────────────────────────────────────────────


class MeetingRecords:
    """`meetings/`, as three questions, through the single writer.

    Scope is an argument rather than a construction-time default, as it is on
    `EventLog`: a project's team meeting and a 1:1 with a direct report are two
    scopes, and an accessor bound to one makes the other a mistake nobody sees.
    `put` is the exception and takes neither — `meeting.scope` decides, which is
    the whole reason that field is required.
    """

    def __init__(self, storage: StoragePort) -> None:
        self._storage = storage

    def put(self, meeting: Meeting) -> None:
        """Write one record into the scope that owns its subject.

        Only for a meeting that has already happened. A future meeting is not
        persisted at all (decided 2026-09-07) — the calendar is its source of
        truth and consumers read it live — which is why there is no `tentative`
        argument here: a response status is an answer about a meeting that has
        not occurred.

        Fields are replaced whole and `## Notes` is carried over byte-identical.
        The notes are lifted out of the previous file's *raw text*, before any
        field is parsed, deliberately: a record whose machine-owned region got
        mangled by hand must not cost the human their notes, and the fields being
        replaced anyway are exactly what the mangling damaged.

        A record for this id already sitting under another day's name is refused
        rather than duplicated — see `MeetingDisplaced`.
        """
        scope = meeting.scope
        name = record_name(meeting.meeting_id, meeting.start)
        notes = ""
        for member in self._members_for(meeting.meeting_id, scope=scope):
            if member != name:
                raise MeetingDisplaced(
                    f"{meeting.meeting_id} is already recorded in {scope} as "
                    f"{member}, and its start now falls on another UTC day, so "
                    f"this write would publish {name} and leave that one behind. "
                    f"One id with two records is a citation nothing can resolve; "
                    f"remove the stale member and write again."
                )
            notes = _notes_of(self._text(member, scope=scope) or "")
        self._storage.write_artifact(
            render_record(MeetingRecord(meeting, notes)).encode("utf-8"),
            scope=scope,
            artifact=MEETINGS,
            name=name,
        )

    def get(self, meeting_id: str, *, scope: DataScope) -> MeetingRecord:
        """The record that id names, or `MeetingNotFound`.

        The id inside the record is what decides, never the filename: the digest
        in the name is what narrows the listing to one candidate, and the field
        is what confirms it. That is the difference between a lookup that is
        probably right and one that is right.
        """
        for member in self._members_for(meeting_id, scope=scope):
            record = self._record(member, scope=scope)
            if record is not None and record.meeting.meeting_id == meeting_id:
                return record
        raise MeetingNotFound(
            f"no meeting {meeting_id!r} is recorded in {scope}. Nothing has been "
            f"written under that id, which on a clean machine is the ordinary "
            f"state — but it is not an empty meeting, and nothing may cite it."
        )

    def for_day(
        self, day: date, *, tz: tzinfo, scope: DataScope
    ) -> tuple[MeetingRecord, ...]:
        """Every record whose `start` falls inside `day` **in `tz`**, ordered.

        The timezone is explicit because `Meeting.start` is aware UTC, so "today"
        for a PM outside UTC is a different set of meetings — and an implicit
        boundary means whichever caller was written first decides it silently.
        A local day maps to a UTC interval that can straddle two UTC dates, and
        both are read.

        Ordered by `(start, meeting_id)`, total rather than merely sorted: two
        meetings can share a start instant, and a dashboard that must re-render
        byte-identically cannot have the tie broken by directory order.

        A collection nothing has been written to answers with an empty tuple —
        a first run, not a failure. Only the files whose names carry a day inside
        the range are opened, so a malformed record for an unrelated date does
        not refuse this day's read; one inside the range does, by design.
        """
        if isinstance(day, datetime):
            raise MalformedMeeting(
                f"for_day({day!r}) was given a datetime. A day boundary is a "
                f"date plus a timezone, and the time of day this carries is one "
                f"this query cannot honour — pass `day.date()` and say which "
                f"zone it is a date in."
            )
        begin, end = _utc_span(day, tz)
        days = _utc_days(begin, end)
        found: list[MeetingRecord] = []
        for member in self._storage.list_collection(scope=scope, artifact=MEETINGS):
            if not _RECORD_NAME.match(member) or member[:10] not in days:
                continue
            record = self._record(member, scope=scope)
            if record is not None and begin <= record.meeting.start < end:
                found.append(record)
        return tuple(
            sorted(found, key=lambda held: (held.meeting.start, held.meeting.meeting_id))
        )

    # ── Internals ────────────────────────────────────────────────────────────

    def _members_for(self, meeting_id: str, *, scope: DataScope) -> tuple[str, ...]:
        """The collection members whose name carries this id's digest.

        Names, never paths — `list_collection` is what keeps this module unable
        to learn where an artifact lives (story 1a). Ordinarily one member or
        none; two would mean a start that moved days, which `put` refuses.

        The three trees are **not** enumerated. Detecting the same id written to
        both the personal and the project scope would mean resolving all of them
        on every write, which breaches AD-25's wall from a project-scoped caller.
        Uniqueness rests on the provider id story 33c keys on; the limit is
        recorded rather than papered over.
        """
        suffix = f"-{hashlib.sha256(meeting_id.encode('utf-8')).hexdigest()}.md"
        return tuple(
            member
            for member in self._storage.list_collection(scope=scope, artifact=MEETINGS)
            if _RECORD_NAME.match(member) and member.endswith(suffix)
        )

    def _text(self, member: str, *, scope: DataScope) -> str | None:
        """One member's text, or `None` when it is no longer there.

        `read_artifact` answers `None` for absence, which here means the member
        was listed and then removed. A caller asking for a specific id sees that
        as `MeetingNotFound` and a day read simply omits it, which is the honest
        report in both cases; inventing a `MalformedMeeting` for a file that does
        not exist would name the wrong fault.
        """
        raw = self._storage.read_artifact(scope=scope, artifact=MEETINGS, name=member)
        if raw is None:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as undecodable:
            raise MalformedMeeting(
                f"{member} in {scope} is not UTF-8. A meeting record is "
                f"plaintext Markdown the PM is meant to read and diff, so a "
                f"binary file under a record's name is a foreign file rather "
                f"than a record to salvage."
            ) from undecodable

    def _record(self, member: str, *, scope: DataScope) -> MeetingRecord | None:
        text = self._text(member, scope=scope)
        return None if text is None else parse_record(text, source=member)


# ── Field rendering and parsing ──────────────────────────────────────────────


def _render_attendees(attendees: tuple[Actor, ...]) -> str:
    handles = []
    for attendee in attendees:
        handle = attendee.actor_id
        if not handle or handle.strip() != handle:
            raise MalformedMeeting(
                f"attendee handle {handle!r} is empty or padded. It is one item "
                f"of a comma-separated value, so an empty item is an attendee "
                f"nobody can name and a padded one is two spellings of one "
                f"person in every count that groups by actor."
            )
        if "," in handle:
            raise MalformedMeeting(
                f"attendee handle {handle!r} contains a comma, which is the list "
                f"separator. The field grammar has no lists and refuses repeated "
                f"keys, so a display name like 'Smith, Bob' that reached the "
                f"handle would split into two attendees and nothing downstream "
                f"would notice — including the Man-Hour Cost, which counts them."
            )
        handles.append(handle)
    return ",".join(handles)


def _parse_field(
    line: str, *, fields: dict[str, str], source: str
) -> tuple[str, str]:
    """One `key=value` line of the field block, or a refusal naming the file.

    A key outside `_FIELDS` is refused rather than ignored, and that is also the
    whole of the answer to a hand-added `tentative=` line. No record pm-ai wrote
    ever carried one — `tentative` left the record on 2026-09-07 before any
    production caller of `put` existed — so a file containing it was hand-edited,
    and the honest report is the same one every unknown key gets: this is not a
    field of a meeting record. It is deliberately *not* special-cased by name,
    because a key retired from the grammar is an unknown key, and one retired key
    given a bespoke message would be the start of a second grammar to maintain.
    """
    try:
        pairs = scan_fields(line, line=line)
    except MalformedEntry as unreadable:
        raise MalformedMeeting(f"{source}: {unreadable}") from unreadable
    if len(pairs) != 1 or not pairs[0][0]:
        raise MalformedMeeting(
            f"{source}: {line!r} is not one `key=value` pair. The field block is "
            f"one field per line — a bare token, a missing `=` or two pairs on "
            f"one line all shift what every reader thinks it is looking at."
        )
    key, value = pairs[0]
    if key in fields:
        raise MalformedMeeting(
            f"{source} declares {key!r} twice. Which one wins would depend on "
            f"line order, so the record has no single value for it — the same "
            f"refusal a ledger line makes."
        )
    if key not in _FIELDS:
        raise MalformedMeeting(
            f"{source} declares {key!r}, which is not a field of a meeting "
            f"record. Known: {sorted(_FIELDS)}. Refused rather than ignored, "
            f"because a typo silently dropped is a value the writer believes it "
            f"stored."
        )
    return key, value


def _parse_start(value: str, *, source: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as unparseable:
        raise MalformedMeeting(
            f"{source}: start={value!r} is not an ISO 8601 instant."
        ) from unparseable
    return _assert_utc(parsed, where=f"{source}: start")


def _parse_duration(value: str, *, source: str) -> int:
    try:
        minutes = int(value)
    except ValueError as unparseable:
        raise MalformedMeeting(
            f"{source}: duration_minutes={value!r} is not an integer."
        ) from unparseable
    return _assert_duration(minutes, source=source)


def _parse_attendees(value: str, *, source: str) -> tuple[Actor, ...]:
    """The comma-separated handles, as handle-only actors.

    An empty value is no attendees rather than one nameless one, which is the
    only reading that survives a round trip through `",".join(())`.
    """
    if not value:
        return ()
    handles = value.split(",")
    if any(not handle or handle.strip() != handle for handle in handles):
        raise MalformedMeeting(
            f"{source}: attendees={value!r} has an empty or padded item. Comma "
            f"is the separator and nothing else, so `a,,b` names a person the "
            f"record cannot identify."
        )
    return tuple(Actor(actor_id=handle) for handle in handles)


def _parse_scope(value: str, *, source: str) -> DataScope:
    try:
        return DataScope.parse(value)
    except ValueError as unparseable:
        raise MalformedMeeting(
            f"{source}: scope={value!r} is not a scope. It decides which tree "
            f"holds the record and whether a git-committed artifact may cite it, "
            f"so it is not a field to guess at."
        ) from unparseable


# ── Guards ───────────────────────────────────────────────────────────────────


def _assert_recordable_id(meeting_id: str) -> str:
    """Refuse an id that cannot be half of a filename, before anything is written.

    The same four refusals `_capture_name` makes one layer down, asked here so
    the message names the *meeting* — and asked at all because the id is
    interpolated into a path component, where a separator writes outside the
    directory every guard above just answered for. `write_artifact` validates the
    composed name again, so this is the message rather than the boundary.

    A long or dot-leading provider id is *not* refused: that is ordinary Graph
    data, and `record_name` encodes it.
    """
    if not meeting_id or not meeting_id.strip():
        raise MalformedMeeting(
            "a meeting record needs an id and this one is empty or only "
            "whitespace. The id is what every extracted fact cites (AD-33), so "
            "there is nothing for a citation to resolve against."
        )
    if meeting_id != meeting_id.strip():
        raise MalformedMeeting(
            f"meeting_id {meeting_id!r} is padded with whitespace, which makes "
            f"two distinguishable meetings one meeting in every listing and log "
            f"that reports them."
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in meeting_id):
        raise MalformedMeeting(
            f"meeting_id {meeting_id!r} contains a control character. A newline "
            f"in particular splits one record's name across two lines in every "
            f"message that reports it, including this one."
        )
    if "/" in meeting_id or "\\" in meeting_id:
        raise MalformedMeeting(
            f"meeting_id {meeting_id!r} contains a path separator. A record is "
            f"one file in the collection: a nested or absolute name is written "
            f"somewhere the scope resolver was never asked about. Both "
            f"separators, because a name reaching a Linux daemon from a Windows "
            f"client is still a traversal there."
        )
    return meeting_id


def _assert_utc(moment: datetime, *, where: str) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
        raise MalformedMeeting(
            f"{where}={moment!r} is not aware UTC. Every instant in a record is, "
            f"so a naive one cannot be compared against the day boundary "
            f"`for_day` computes — and a naive value reads as local time without "
            f"announcing that it did (AD-35)."
        )
    return moment


def _assert_duration(minutes: int, *, source: str | None = None) -> int:
    """Zero is legitimate; negative is not.

    An all-day meeting is `0` minutes by story 33c's decision, and the Man-Hour
    Cost of one is `0.0` — which is the honest answer for a span nobody sat
    through. A negative duration would make it negative, and a cost that
    subtracts is not a number any card should show.
    """
    where = f"{source}: duration_minutes" if source else "duration_minutes"
    if minutes < 0:
        raise MalformedMeeting(
            f"{where}={minutes} is negative. Man-Hour Cost multiplies it by the "
            f"attendee count and the blended rate, so a negative duration is a "
            f"meeting that gave time back."
        )
    return minutes


# ── Names and days ───────────────────────────────────────────────────────────


def _slug(meeting_id: str) -> str:
    reduced = _UNSAFE_IN_SLUG.sub("_", meeting_id)[:_SLUG_LIMIT]
    return reduced or "_"


def _notes_of(text: str) -> str:
    """The human-owned region of a record's raw text, without parsing the rest."""
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.rstrip("\r\n") == NOTES_HEADING:
            return "".join(lines[index + 1 :])
    return ""


def _utc_span(day: date, tz: tzinfo) -> tuple[datetime, datetime]:
    """The half-open UTC interval one local day occupies.

    Half-open because a meeting starting at exactly midnight belongs to the day
    that begins, and to one day only.
    """
    begin = datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(
        timezone.utc
    )
    return begin, end


def _utc_days(begin: datetime, end: datetime) -> frozenset[str]:
    """Which `YYYY-MM-DD` filename prefixes can hold a record inside the span.

    One or two: a 24-hour half-open interval touches at most two UTC dates.
    Computed by walking rather than assumed, so an offset no zone has today
    would widen the read instead of losing a record.
    """
    days: set[str] = set()
    cursor = begin.date()
    last = (end - timedelta(microseconds=1)).date()
    while cursor <= last:
        days.add(cursor.isoformat())
        cursor += timedelta(days=1)
    return frozenset(days)
