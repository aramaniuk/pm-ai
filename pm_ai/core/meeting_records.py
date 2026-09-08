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

**Reserved means refused, not ignored.** Everything between the field block and
`## Notes` is pm-ai's, and the only thing this slice writes there is the
`## Summary` heading — so anything else a hand edit leaves in that region is a
`MalformedMeeting` naming the region and pointing at `## Notes`. Until this was
enforced the region was parsed past and dropped on the next rewrite: a
hand-written summary body vanished, and a `duration_minutes=999` line placed one
line *below* the blank separator parsed away in silence while the record went on
reporting 30. That is precisely the failure `_parse_field` refuses an unknown key
for — "a typo silently dropped is a value the writer believes it stored" — in a
file whose whole premise is that a human may edit it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone, tzinfo

from pm_ai.domain.event_entries import (
    MAX_ENTRY_LENGTH,
    MalformedEntry,
    render_value,
    scan_fields,
)
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
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

_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)$")
"""The one spelling of `duration_minutes` this grammar reads or writes.

`int()` is not the parser for a text field. It accepts Python *source* syntax —
`1_0` is ten, `+45` is forty-five — and every non-ASCII decimal digit besides,
so a file saying `1_0` parsed as `10` and a file saying `٤٥` parsed as `45`: the
text and the value disagreed, and the record is the thing a human reads and
diffs. Canonical rather than merely unambiguous (`007` is refused too), because
`23a` requires a byte-identical re-render and a value with two spellings has two
renders.
"""

_RECORDING_SCOPES: frozenset[ScopeKind] = frozenset(
    {ScopeKind.PERSONAL, ScopeKind.PEOPLE, ScopeKind.PROJECT}
)
"""The three trees that declare `meetings/` (`scope_model.py:554,650,716`).

Spelled here so this module refuses an application-scoped meeting in its own
vocabulary. `ScopePaths.resolve` refuses it too — with `ArtifactNotInScope`,
which is a `pm_ai.platform` sentence about a directory rather than this one's
sentence about a meeting — and the scope is a *parsed field*, so a record
hand-edited to `scope=application` would otherwise read back as a `Meeting` this
accessor cannot store and surface a path error two layers down on the next `put`.
"""


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
    return f"{day}-{_slug(meeting_id)}{_digest_suffix(meeting_id)}"


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
    scope = _assert_records_meetings(meeting.scope, where="Meeting.scope")
    values: dict[str, str] = {
        _MEETING_ID: meeting.meeting_id,
        _TITLE: _assert_recordable_text(meeting.title, where="Meeting.title"),
        _START: start.isoformat(),
        _DURATION: str(duration),
        _ATTENDEES: _render_attendees(meeting.attendees),
        _SCOPE: str(scope),
    }
    if meeting.calendar_event_ref is not None:
        values[_CALENDAR_REF] = meeting.calendar_event_ref
    lines = [
        _assert_readable_length(
            f"{key}={render_value(values[key], where=f'field {key!r}')}",
            where=f"the rendered {key!r}",
        )
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

    Between the two lies the reserved `## Summary` region, which is pm-ai's and
    which this slice leaves empty — so anything in it beyond the heading itself
    is refused rather than parsed past. That region used to be read and dropped:
    a hand-written summary body disappeared on the next `put`, and a
    `duration_minutes=` line one line below the blank separator was ignored
    while the writer believed it had been stored.
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
    reserved_at = len(head)
    for index, raw in enumerate(head):
        line = raw.rstrip("\r\n")
        if not line.strip() or line.startswith("##"):
            reserved_at = index
            break
        # Both bounds the renderer keeps, asked on the way in as well. A value
        # this module will not write is a value it must not accept back — the
        # rule `MalformedMeeting` states — and a hand edit is the only thing that
        # can put an unbounded line or a raw NUL in a file pm-ai wrote.
        _assert_readable_length(line, where=f"{source}: a field")
        key, value = _parse_field(line, fields=fields, source=source)
        fields[key] = value
    _assert_reserved_region_empty(head[reserved_at:], source=source)

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
            meeting_id=_assert_recordable_id(fields[_MEETING_ID]),
            title=_assert_recordable_text(fields[_TITLE], where=f"{source}: title"),
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
        replaced anyway are exactly what the mangling damaged. That extends to a
        member that is not valid UTF-8 — `_repairable_text` decodes it lossily
        rather than refusing, because `put` is the one operation that can repair
        such a file and it must not be the operation that raises on it.

        A record for this id already sitting under another day's name is refused
        rather than duplicated — see `MeetingDisplaced`.

        **This is a read-modify-write, and it is one step.** The previous member
        is listed and read, and the replacement written, inside
        `StoragePort.exclusive` over `meetings/` — the same claim `8b` takes over
        `private/config.json`, and for the same reason: two `put`s that each read
        a different snapshot of the notes would each write their own back and one
        human edit would be gone. The claim refuses rather than waits, so a
        concurrent `put` in another process raises `ArtifactBusy` naming the
        claim file instead of quietly winning.
        **What the claim does not cover is a human's editor**, which honours no
        claim of pm-ai's: an edit saved between the read and the write is still
        lost. Stated rather than implied, because the region this preserves is
        exactly the region a human is expected to be editing — closing that
        window needs the single writer to compare what it read against what is
        on disk at publish time, which no artifact operation does today.
        """
        scope = _assert_records_meetings(meeting.scope, where="Meeting.scope")
        name = record_name(meeting.meeting_id, meeting.start)
        with self._storage.exclusive(scope=scope, artifact=MEETINGS):
            notes = ""
            for member in self._members_for(meeting.meeting_id, scope=scope):
                if member != name:
                    raise MeetingDisplaced(
                        f"{meeting.meeting_id} is already recorded in {scope} as "
                        f"{member}, and its start now falls on another UTC day, so "
                        f"this write would publish {name} and leave that one "
                        f"behind. One id with two records is a citation nothing "
                        f"can resolve; remove the stale member and write again."
                    )
                notes = _notes_of(self._repairable_text(member, scope=scope))
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

        The confirmation lives in `_record`, which refuses a member whose name
        and content disagree and names both ids. It is there rather than here so
        that `for_day` gets it too, and because the honest answer to "the name
        says one meeting and the field says another" is a refusal rather than
        `MeetingNotFound`: absence is the ordinary state of a clean machine, and
        reporting a contradiction as absence hands the caller the wrong sentence.
        """
        scope = _assert_records_meetings(scope, where="get(scope=…)")
        for member in self._members_for(meeting_id, scope=scope):
            record = self._record(member, scope=scope)
            if record is not None:
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
        if not isinstance(tz, tzinfo):
            raise MalformedMeeting(
                f"for_day(..., tz={tz!r}) was not given a timezone. `None` in "
                f"particular is not 'no preference': `datetime.combine` reads it "
                f"as the machine's local zone, so the query would answer for "
                f"whichever zone the daemon happens to be running in and say "
                f"nothing about having done so — a boundary read as local time "
                f"without announcing that it did (AD-35), and the exact silence "
                f"the explicit parameter exists to prevent. Refused for the same "
                f"reason a `datetime` day is, one line up."
            )
        scope = _assert_records_meetings(scope, where="for_day(scope=…)")
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
        suffix = _digest_suffix(meeting_id)
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

    def _repairable_text(self, member: str, *, scope: DataScope) -> str:
        """One member's text for `put` to salvage `## Notes` out of, never a refusal.

        Deliberately not `_text`. That method refuses a member which is not
        UTF-8, which is right for a *read*: a binary file under a record's name
        is a foreign file rather than a record, and reporting it as a meeting
        would be inventing one. It was wrong for `put`, which raised on exactly
        the file it is the only operation able to repair — a corrupted record
        could neither be read nor rewritten, so nothing could fix it.

        Lossy rather than discarded. A corruption confined to the machine-owned
        region leaves the human's notes intact and they are carried over; one
        inside the notes carries U+FFFD through, which is visible in the file the
        PM then reads rather than silence. Absence answers as no notes, which is
        the same sentence `_text` tells `get`.
        """
        raw = self._storage.read_artifact(scope=scope, artifact=MEETINGS, name=member)
        return "" if raw is None else raw.decode("utf-8", errors="replace")

    def _record(self, member: str, *, scope: DataScope) -> MeetingRecord | None:
        text = self._text(member, scope=scope)
        if text is None:
            return None
        record = parse_record(text, source=member)
        _assert_name_agrees(member, record.meeting)
        return record


# ── Field rendering and parsing ──────────────────────────────────────────────


def _render_attendees(attendees: tuple[Actor, ...]) -> str:
    handles: list[str] = []
    for attendee in attendees:
        handle = attendee.actor_id
        if handle in handles:
            raise MalformedMeeting(
                f"attendee handle {handle!r} appears twice. `attendees` is a set "
                f"of the people who were in the room, and every count that reads "
                f"it groups by actor: a repeated handle makes one person two in "
                f"the Man-Hour Cost, in every per-actor tally, and in anything "
                f"`23a` renders from the list."
            )
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
    """A canonical decimal integer, which is narrower than `int()` accepts.

    `int()` reads Python source syntax and every Unicode decimal digit, so
    `duration_minutes=1_0` used to parse as ten and `+45` as forty-five: the file
    said one thing and the record held another, in the one field the Man-Hour
    Cost multiplies. `_DECIMAL` carries the rest of the reasoning.
    """
    if not _DECIMAL.match(value):
        raise MalformedMeeting(
            f"{source}: duration_minutes={value!r} is not a decimal integer. "
            f"Minutes are written as digits with no sign, no underscore and no "
            f"leading zero — `0` for an all-day meeting per `33c`, and nothing "
            f"negative, since Man-Hour Cost multiplies this by the attendee "
            f"count and the blended rate."
        )
    return _assert_duration(int(value), source=source)


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
    repeated = sorted({handle for handle in handles if handles.count(handle) > 1})
    if repeated:
        raise MalformedMeeting(
            f"{source}: attendees={value!r} names {repeated} more than once. One "
            f"person listed twice is two people to the Man-Hour Cost and to "
            f"every per-actor count that groups by handle — refused on the way "
            f"in for the reason it is refused on the way out."
        )
    return tuple(Actor(actor_id=handle) for handle in handles)


def _parse_scope(value: str, *, source: str) -> DataScope:
    try:
        parsed = DataScope.parse(value)
    except ValueError as unparseable:
        raise MalformedMeeting(
            f"{source}: scope={value!r} is not a scope. It decides which tree "
            f"holds the record and whether a git-committed artifact may cite it, "
            f"so it is not a field to guess at."
        ) from unparseable
    return _assert_records_meetings(parsed, where=f"{source}: scope")


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


def _assert_recordable_text(value: str, *, where: str) -> str:
    """Refuse the control characters the line grammar does not neutralise.

    Narrower than `_assert_recordable_id`, and the asymmetry is the point rather
    than an oversight. An id is interpolated into a filename and quoted verbatim
    into every message that reports the record, so a newline there splits one
    record's name across two lines. A title is neither: `render_value` escapes
    `\\`, `"`, `\n` and `\r`, so all four survive one line and round-trip
    unchanged, and refusing them would refuse a real Graph subject that carries
    one.

    What escaping does not answer for is the rest of C0 and DEL. A NUL or a bell
    inside a title reaches the file unescaped, where it is invisible to the PM
    who is meant to read, grep and diff a plaintext record — and active in the
    terminal that prints the record's name back at them. Tab, newline and
    carriage return are the three exceptions, because the first is ordinary in
    prose and the other two are escaped.
    """
    illegal = sorted(
        {
            character
            for character in value
            if (ord(character) < 32 and character not in "\t\n\r")
            or ord(character) == 127
        }
    )
    if illegal:
        raise MalformedMeeting(
            f"{where} contains the control character(s) {illegal!r}. The field "
            f"grammar escapes a backslash, a quote, a newline and a carriage "
            f"return, so those survive a line and are allowed — the rest of C0 "
            f"and DEL survive nothing a human reads, and reach the terminal that "
            f"reports the record."
        )
    return value


def _assert_readable_length(line: str, *, where: str) -> str:
    """One rendered field line, bounded as `render_entry` bounds a ledger line.

    The record inherits the ledger's escaping and its per-line shape, and until
    this guard it inherited neither of the ledger's bounds: `render_entry`
    refuses a line past `MAX_ENTRY_LENGTH` because a segment is plaintext
    Markdown the PM is meant to read, grep and diff by hand, and a record is the
    same kind of file for the same reader. `title` and the joined `attendees` are
    the two fields a caller can make arbitrarily long — a Graph subject and a
    meeting with a thousand invitees — and either would have produced a file no
    editor opens.
    """
    if len(line) > MAX_ENTRY_LENGTH:
        raise MalformedMeeting(
            f"{where} line is {len(line)} characters, past the "
            f"{MAX_ENTRY_LENGTH} bound a ledger line keeps. A meeting record is "
            f"plaintext Markdown meant to be read, grepped and diffed by hand, "
            f"and one field carrying a megabyte defeats all three — refused here, "
            f"where the message names the field, rather than as a file nobody "
            f"can open."
        )
    return line


def _assert_records_meetings(scope: DataScope, *, where: str) -> DataScope:
    """Only a tree that declares `meetings/` may hold a record. See `_RECORDING_SCOPES`."""
    if scope.kind not in _RECORDING_SCOPES:
        raise MalformedMeeting(
            f"{where}={scope} has no `meetings/`. The collection is declared in "
            f"the personal, people and project trees and nowhere else "
            f"(`scope_model.py:554,650,716`), so there is no directory in the "
            f"{scope.kind.value} scope for this record to land in and nothing "
            f"there for a citation to resolve against. Refused in this "
            f"vocabulary rather than left to surface as a path error from the "
            f"resolver two layers down."
        )
    return scope


def _assert_name_agrees(member: str, meeting: Meeting) -> None:
    """The filename and the record it holds must name the same meeting and day.

    Both halves of the name are derived from the record's own fields, so a
    disagreement is a hand edit — and both disagreements used to be silent in
    opposite directions, which is the worst available pair:

    - **the day.** `for_day` opens only the members whose prefix falls inside
      the span, and it then filters on the parsed `start`. A record whose `start`
      was hand-moved to another day was therefore invisible on *every* day: the
      prefix excluded it from the new day's read and the parsed instant excluded
      it from the old day's, and neither stage called it an error. That is a
      dashboard row that silently disappears, which the story's Design Notes say
      must never happen.
    - **the id.** The digest narrows a listing to one candidate and the field is
      what confirms it. A record whose `meeting_id=` was hand-changed answered
      `MeetingNotFound` — absence, the ordinary state of a clean machine —
      rather than naming the contradiction.

    Reachable only by hand now that a held meeting's `start` is immutable
    (2026-09-07), which is exactly why it is worth a refusal that names both
    values: the operator who made the edit is the only person who can undo it.
    """
    named_day, held_day = member[:10], meeting.start.date().isoformat()
    if named_day != held_day:
        raise MalformedMeeting(
            f"{member} is named for {named_day} and holds a meeting starting on "
            f"{held_day}. The filename's day is what lets `for_day` read one day "
            f"without opening every file, so a record that disagrees with its own "
            f"name is returned by no day at all — the {named_day} read filters it "
            f"out on the parsed instant and the {held_day} read never opens it. "
            f"Either restore start= to {named_day} or rename the file to carry "
            f"{held_day}."
        )
    if not member.endswith(_digest_suffix(meeting.meeting_id)):
        raise MalformedMeeting(
            f"{member} does not carry the digest of {meeting.meeting_id!r}, the "
            f"id it holds. The name's digest is what narrows a listing to one "
            f"candidate and the field is what confirms it, so a record found "
            f"under one id and declaring another resolves for neither. Either "
            f"restore meeting_id= or rename the file to "
            f"{record_name(meeting.meeting_id, meeting.start)}."
        )


def _assert_reserved_region_empty(lines: list[str], *, source: str) -> None:
    """Nothing between the field block and `## Notes` but the reserved heading.

    That region is pm-ai's — `## Summary` is machine-owned and this slice leaves
    it empty — and it used to be read past and dropped on the next rewrite. Two
    silent losses came out of that, and they are the two this refuses:

    - a hand-written `## Summary` body disappeared on the next `put`, with no
      message and nothing in the file to say it had been there;
    - a field-shaped line placed one line *below* the blank separator was
      ignored, so `duration_minutes=999` or `title=hijacked` parsed away while
      the record went on reporting the value it already held.

    The second is what `_parse_field` refuses an unknown key for, in this file's
    own words: a typo silently dropped is a value the writer believes it stored.
    The first is the same rule for prose. Both point the human at `## Notes`,
    which is theirs, is copied through verbatim, and is where a hand-written
    paragraph belongs until a model can fill the summary in (`11b`, story 7).
    """
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line.strip() or line == SUMMARY_HEADING:
            continue
        key = line.partition("=")[0].strip()
        if "=" in line and key in _FIELDS:
            raise MalformedMeeting(
                f"{source} declares {key!r} below the field block, in the "
                f"reserved `## Summary` region, where nothing reads it. The field "
                f"block is the *leading* run of lines and ends at the first blank "
                f"line, so this line is dropped and the record keeps whatever "
                f"value it already had — the same silent loss an unknown key is "
                f"refused for. Move it up into the field block."
            )
        raise MalformedMeeting(
            f"{source} has content in the reserved `## Summary` region: "
            f"{line!r}. That region is pm-ai's and this slice writes nothing into "
            f"it — it is transcript-derived and needs a model — so anything left "
            f"there is dropped by the next write rather than kept. Hand-written "
            f"prose belongs under `## Notes`, which is copied through "
            f"byte-identical."
        )


def _assert_utc(moment: datetime, *, where: str) -> datetime:
    if moment.tzinfo is None or not _is_utc(moment.tzinfo, moment):
        raise MalformedMeeting(
            f"{where}={moment!r} is not aware UTC. Every instant in a record is, "
            f"so a naive one cannot be compared against the day boundary "
            f"`for_day` computes — and a naive value reads as local time without "
            f"announcing that it did (AD-35). A *named* zone that merely happens "
            f"to be zero-offset at this instant is refused too: `Europe/London` "
            f"would otherwise be accepted in January and refused in July for the "
            f"same calendar, and the record's day — which its filename carries — "
            f"would depend on which half of the year the meeting fell in. "
            f"Convert with `.astimezone(timezone.utc)`."
        )
    return moment


def _is_utc(zone: tzinfo, moment: datetime) -> bool:
    """Whether `zone` *is* UTC, rather than zero-offset at one instant.

    A single `utcoffset()` read cannot tell the two apart, and half the zones
    that fail this test pass it for part of the year: `Europe/London` is +00:00
    in January and +01:00 in July. Sampled six months either side as well as at
    the instant itself, which is enough to separate a fixed-zero zone from any
    zone in the database that has a summer — three reads rather than a
    whole-year walk, because no rule keeps a zone at +00:00 for six months and
    moves it in the other six.
    """
    naive = moment.replace(tzinfo=None)
    for shift in (timedelta(0), timedelta(days=183), timedelta(days=-183)):
        try:
            offset = zone.utcoffset(naive + shift)
        except (OverflowError, ValueError):
            # Only within a few months of `datetime.min`/`max`, which no meeting
            # is. Refused rather than assumed UTC: a zone that cannot answer for
            # an instant is not one this record can be keyed on.
            return False
        if offset != timedelta(0):
            return False
    return True


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


def _digest_suffix(meeting_id: str) -> str:
    """The tail of the filename an id owns, spelled once.

    `record_name` composes it, `_members_for` matches on it and
    `_assert_name_agrees` checks a member against it — three callers, so a
    fourth spelling of the same `sha256` is a way for a lookup to stop finding
    what a write produced.
    """
    return f"-{hashlib.sha256(meeting_id.encode('utf-8')).hexdigest()}.md"


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

    Not necessarily 24 hours wide. A DST transition makes the local day 23 or 25
    hours — measured: `America/Havana` 2018-11-04 spans 25 and
    `America/Sao_Paulo` 2018-11-04 spans 23 — which is why both ends are
    converted from the local wall clock rather than one end being offset from
    the other.
    """
    begin = datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(
        timezone.utc
    )
    return begin, end


def _utc_days(begin: datetime, end: datetime) -> frozenset[str]:
    """Which `YYYY-MM-DD` filename prefixes can hold a record inside the span.

    One or two in every zone the database describes, but the count is **walked
    rather than reasoned to**, and the reason it used to give for that was
    wrong. "A 24-hour half-open interval touches at most two UTC dates" assumed
    a width no local day guarantees: a DST transition makes it 23 or 25 hours
    (`America/Havana` 2018-11-04 is 25, `America/Sao_Paulo` 2018-11-04 is 23),
    and zone offsets range over ±14 hours. The conclusion survives the correction
    — the widest span any of that produces still touches two dates — and the
    walk is what makes it survive: an interval of any width yields exactly the
    prefixes it covers, so an unusual span widens the read instead of losing a
    record. Reasoning from a fixed 24 hours would have been reasoning from
    something no zone promises.
    """
    days: set[str] = set()
    cursor = begin.date()
    last = (end - timedelta(microseconds=1)).date()
    while cursor <= last:
        days.add(cursor.isoformat())
        cursor += timedelta(days=1)
    return frozenset(days)
