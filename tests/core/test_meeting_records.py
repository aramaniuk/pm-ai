"""Story 11a — `MeetingRecords`, one test per row of the story's matrix.

Every row runs against a real temporary root through `StorageService`, because
most of the claims are claims about a filesystem: which scope tree a record
landed in, that a foreign file was not parsed, that one day's read never opened
another day's file. A double over `StoragePort` would let all three pass while
nothing was written — which is the defect this story exists to fix, so the tests
are not allowed to be able to miss it.

The render/parse pair is exercised directly as well, without storage: it is a
pure function pair and the drift between its halves is the failure mode (the
same property story 4g guards for `config.toml`).
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pm_ai.core.meeting_records import (
    MEETINGS,
    NOTES_HEADING,
    MalformedMeeting,
    MeetingDisplaced,
    MeetingNotFound,
    MeetingRecord,
    MeetingRecords,
    as_stored,
    parse_record,
    record_name,
    render_record,
)
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
from pm_ai.domain.meetings import Meeting
from pm_ai.platform.paths import ScopePaths
from pm_ai.storage.crypto import PlaintextCrypto
from pm_ai.storage.service import MalformedCaptureName, StorageService

NOW = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")
PERSONAL = DataScope(ScopeKind.PERSONAL)
PEOPLE = DataScope(ScopeKind.PEOPLE, person_id="bob")
UTC = timezone.utc
TOKYO = timezone(timedelta(hours=9))


class _NoRepository:
    """Git: no working tree here. The capture guard is not what this is about."""

    def working_tree(self, path):
        return None

    def repository_marker_above(self, path):
        return None

    def tracking(self, path, *, repository):  # pragma: no cover
        raise AssertionError("tracking asked with no working tree")


@pytest.fixture
def storage(tmp_path: Path) -> StorageService:
    return StorageService(
        ScopePaths.rooted(tmp_path),
        now=lambda: NOW,
        vcs=_NoRepository(),
        crypto=PlaintextCrypto(),
    )


@pytest.fixture
def records(storage: StorageService) -> MeetingRecords:
    return MeetingRecords(storage)


def meeting(
    meeting_id: str = "mtg_01HX",
    *,
    scope: DataScope = PROJECT,
    start: datetime = NOW,
    duration_minutes: int = 45,
    attendees: tuple[Actor, ...] = (Actor("actor_andrei"), Actor("actor_alex")),
    title: str = "Project Alpha architecture sync",
    calendar_event_ref: str | None = "outlook:evt_9931",
) -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title=title,
        start=start,
        duration_minutes=duration_minutes,
        attendees=attendees,
        scope=scope,
        calendar_event_ref=calendar_event_ref,
    )


def collection(tmp_path: Path, scope: DataScope) -> Path:
    """Where a scope's `meetings/` actually is, for the filesystem assertions."""
    return ScopePaths.rooted(tmp_path).resolve(scope, MEETINGS)


def members(tmp_path: Path, scope: DataScope) -> list[str]:
    directory = collection(tmp_path, scope)
    return sorted(entry.name for entry in directory.iterdir()) if directory.is_dir() else []


# ── Write and read back ──────────────────────────────────────────────────────


def test_a_meeting_is_written_to_its_scope_and_read_back(records, tmp_path):
    """Matrix: write and read back. The record lands under that scope's `meetings/`."""
    subject = meeting()
    records.put(subject)

    assert len(members(tmp_path, PROJECT)) == 1
    assert records.get("mtg_01HX", scope=PROJECT).meeting == as_stored(subject)


def test_a_personal_subject_meeting_lands_in_the_personal_tree(records, tmp_path):
    """Matrix: `Meeting.scope` decides, and the accessor never guesses (AD-33/AD-38)."""
    records.put(meeting("mtg_own", scope=PERSONAL))

    assert len(members(tmp_path, PERSONAL)) == 1
    assert members(tmp_path, PROJECT) == []
    assert records.get("mtg_own", scope=PERSONAL).meeting.scope == PERSONAL
    with pytest.raises(MeetingNotFound):
        records.get("mtg_own", scope=PROJECT)


def test_a_people_scope_meeting_is_written_to_that_persons_tree(records, tmp_path):
    """Matrix: a 1:1 with a direct report — `meetings/` is declared there too."""
    records.put(meeting("mtg_1on1", scope=PEOPLE))

    assert len(members(tmp_path, PEOPLE)) == 1
    assert records.get("mtg_1on1", scope=PEOPLE).meeting.scope == PEOPLE


def test_an_unknown_id_is_reported_absent_rather_than_returned_empty(records):
    """Matrix: a record whose every field is a default would be citable."""
    with pytest.raises(MeetingNotFound):
        records.get("mtg_never_written", scope=PROJECT)


def test_the_same_id_in_two_scopes_is_not_detected(records, tmp_path):
    """Matrix: the stated limit. Enumerating three trees on every `put` would
    breach AD-25's wall from a project-scoped caller, so uniqueness rests on the
    provider id `33c` keys on. Asserted so the limit is visible rather than
    assumed: both records exist and neither knows about the other.
    """
    records.put(meeting("mtg_dup", scope=PERSONAL, title="mine"))
    records.put(meeting("mtg_dup", scope=PROJECT, title="theirs"))

    assert records.get("mtg_dup", scope=PERSONAL).meeting.title == "mine"
    assert records.get("mtg_dup", scope=PROJECT).meeting.title == "theirs"
    assert len(members(tmp_path, PERSONAL)) == 1
    assert len(members(tmp_path, PROJECT)) == 1


# ── Rewrite, and the human-owned region ──────────────────────────────────────


def test_a_rewrite_replaces_the_fields_and_preserves_the_notes(records, tmp_path):
    """Matrix + criterion: the notes region is hashed, because "preserved" and
    "regenerated identically" are indistinguishable from a success message.
    """
    records.put(meeting(title="first title"))
    member = collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]
    hand_written = "Alex owes me the migration plan.\n\n### my own heading\n- and a bullet\n"
    member.write_text(member.read_text() + hand_written, encoding="utf-8")
    before = hashlib.sha256(hand_written.encode("utf-8")).hexdigest()

    records.put(meeting(title="second title"), tentative=True)

    reread = records.get("mtg_01HX", scope=PROJECT)
    assert reread.meeting.title == "second title"
    assert reread.tentative is True
    assert hashlib.sha256(reread.notes.encode("utf-8")).hexdigest() == before
    assert members(tmp_path, PROJECT) == [member.name], "one id, one record"


def test_a_rewrite_over_a_mangled_machine_region_still_keeps_the_notes(records, tmp_path):
    """The notes are lifted from raw text before any field is parsed.

    A hand edit that broke the field block must not cost the human their notes —
    the fields are being replaced anyway, which is exactly what was damaged.
    """
    records.put(meeting())
    member = collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]
    member.write_text(
        f"this is not a field block at all\n\n{NOTES_HEADING}\nkeep me\n", encoding="utf-8"
    )

    records.put(meeting())

    assert records.get("mtg_01HX", scope=PROJECT).notes == "keep me\n"


def test_a_start_that_moves_to_another_day_is_refused_rather_than_duplicated(records):
    """One id with two records is a citation nothing can resolve, and the single
    writer offers no delete — so the write is refused and says what to remove.
    """
    records.put(meeting())
    with pytest.raises(MeetingDisplaced):
        records.put(meeting(start=NOW + timedelta(days=1)))


def test_a_rewrite_within_the_same_day_is_not_displaced(records, tmp_path):
    records.put(meeting())
    records.put(meeting(start=NOW + timedelta(hours=2)))

    assert len(members(tmp_path, PROJECT)) == 1
    assert records.get("mtg_01HX", scope=PROJECT).meeting.start == NOW + timedelta(hours=2)


# ── Attendees, duration, and the cost that is never stored ───────────────────


def test_an_attendee_handle_containing_a_comma_is_refused(records):
    """Matrix: comma is the list separator, and nothing downstream would notice
    the split — including the Man-Hour Cost, which counts attendees.
    """
    with pytest.raises(MalformedMeeting, match="comma"):
        records.put(meeting(attendees=(Actor("Smith, Bob"),)))


def test_an_empty_or_padded_attendee_handle_is_refused(records):
    with pytest.raises(MalformedMeeting):
        records.put(meeting(attendees=(Actor(""),)))
    with pytest.raises(MalformedMeeting):
        records.put(meeting(attendees=(Actor(" actor_alex"),)))


def test_no_attendees_round_trips_as_no_attendees(records):
    records.put(meeting("mtg_solo", attendees=()))
    assert records.get("mtg_solo", scope=PROJECT).meeting.attendees == ()


def test_a_display_name_is_not_part_of_the_record(records):
    """`attendees` is one comma-separated value, so a display name cannot ride
    beside a handle. `as_stored` is what a caller compares against, and the loss
    is asserted here rather than left to be discovered — AD-34 makes the alias
    table the home of a display name.
    """
    records.put(meeting(attendees=(Actor("actor_alex", "Alex Smith"),)))

    stored = records.get("mtg_01HX", scope=PROJECT).meeting
    assert stored.attendees == (Actor("actor_alex"),)
    assert stored.attendees[0].display_name is None


def test_an_all_day_meeting_stores_zero_minutes_and_costs_nothing(records):
    """Matrix: `duration_minutes` is `0` per `33c`; the renderer's cost is `0.0`."""
    records.put(meeting("mtg_allday", duration_minutes=0))

    stored = records.get("mtg_allday", scope=PROJECT).meeting
    assert stored.duration_minutes == 0
    assert stored.man_hour_cost(150.0) == 0.0


def test_a_negative_duration_is_refused(records):
    with pytest.raises(MalformedMeeting, match="negative"):
        records.put(meeting(duration_minutes=-30))


def test_the_record_never_stores_the_man_hour_cost(records, tmp_path):
    """CAP-1 puts the cost in a rendered card and it derives from a `config.toml`
    value, so a stored one is wrong the moment the rate changes.
    """
    records.put(meeting())
    text = (collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]).read_text()

    assert "cost" not in text
    assert "rate" not in text


def test_tentative_is_stored_and_stale_is_not(records, tmp_path):
    """Provider data is persisted; a derivable claim is not stored twice."""
    records.put(meeting(), tentative=True)
    text = (collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]).read_text()

    assert "tentative=true" in text
    assert "stale" not in text
    assert records.get("mtg_01HX", scope=PROJECT).tentative is True


# ── Names ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "provider_id",
    [
        # The `AAMkA` prefix and `=` padding are the shape this repository
        # records for a Graph event id (`tests/connectors/test_graph_calendar_fetch.py`
        # fixture `AAMkAGI2-single`), lengthened past `write_artifact`'s 128-byte
        # name limit — which is what makes the encoding load-bearing rather than
        # decorative, since `33b` is the first writer and a refusal there blocks it.
        "AAMkAGI2" + "Zm9vYmFyLWJheg_-" * 12 + "==",
        # Dot-leading, which `_capture_name` refuses outright: a dotfile hides a
        # record from the operator, so an id shaped like one must be encoded
        # rather than passed through.
        ".AAMkAGI2-occurrence-2026-09-04",
        # `=` alone is enough to make a name need quoting in the field grammar,
        # so the id inside the record is exercised too.
        "AAMkA==",
    ],
)
def test_a_provider_id_unsafe_as_a_filename_is_encoded_and_preserved(
    records, tmp_path, provider_id
):
    """Matrix + criterion: encoded to a safe stable name, id preserved inside."""
    records.put(meeting(provider_id))

    written = members(tmp_path, PROJECT)
    assert len(written) == 1
    assert len(written[0].encode("utf-8")) <= 128
    assert not written[0].startswith(".")
    assert "/" not in written[0] and "\\" not in written[0]
    assert records.get(provider_id, scope=PROJECT).meeting.meeting_id == provider_id


def test_the_name_is_stable_across_rewrites(records):
    """A meeting id is stable for the life of the record, because `source_ref`
    derives from it and a 30-day transcript purge must not empty a citation.
    """
    first = record_name("AAMkA==", NOW)
    assert record_name("AAMkA==", NOW) == first
    assert record_name("AAMkA==", NOW + timedelta(hours=1)) == first


@pytest.mark.parametrize(
    "unsafe", ["../memory/leak.md", "..\\leak", "", "   ", "mtg\n01", "  mtg_01HX"]
)
def test_a_path_unsafe_id_is_refused_before_anything_is_written(records, tmp_path, unsafe):
    """Matrix: an id containing `../` is refused, as `write_artifact` already
    refuses for captures — asked here so the message names the meeting.
    """
    with pytest.raises((MalformedMeeting, MalformedCaptureName)):
        records.put(meeting(unsafe))
    assert members(tmp_path, PROJECT) == []


# ── `for_day` ────────────────────────────────────────────────────────────────


def test_for_day_on_an_absent_collection_is_empty_not_a_failure(records):
    """Matrix: a first-run state."""
    assert records.for_day(date(2026, 9, 4), tz=UTC, scope=PROJECT) == ()


def test_for_day_returns_a_days_meetings_ordered_by_start(records):
    """Matrix: a date with two meetings — both returned, ordered by `start`."""
    later = meeting("mtg_later", start=NOW + timedelta(hours=3))
    earlier = meeting("mtg_earlier", start=NOW)
    records.put(later)
    records.put(earlier)

    found = records.for_day(date(2026, 9, 4), tz=UTC, scope=PROJECT)
    assert [held.meeting.meeting_id for held in found] == ["mtg_earlier", "mtg_later"]


def test_tied_start_instants_are_ordered_by_meeting_id(records):
    """Matrix: `23a` requires a byte-identical re-render, so the tie cannot be
    broken by directory order.
    """
    records.put(meeting("mtg_bbb"))
    records.put(meeting("mtg_aaa"))

    found = records.for_day(date(2026, 9, 4), tz=UTC, scope=PROJECT)
    assert [held.meeting.meeting_id for held in found] == ["mtg_aaa", "mtg_bbb"]


def test_the_day_boundary_belongs_to_the_stated_timezone(records):
    """A meeting at 23:00Z on the 4th is the 5th in Tokyo and the 4th in UTC.

    The parameter is explicit precisely so this is a decision a caller makes
    rather than one whichever module was written first made silently.
    """
    late = datetime(2026, 9, 4, 23, 0, tzinfo=timezone.utc)
    records.put(meeting("mtg_late", start=late))

    assert [h.meeting.meeting_id for h in records.for_day(date(2026, 9, 4), tz=UTC, scope=PROJECT)] == [
        "mtg_late"
    ]
    assert records.for_day(date(2026, 9, 4), tz=TOKYO, scope=PROJECT) == ()
    assert [
        h.meeting.meeting_id for h in records.for_day(date(2026, 9, 5), tz=TOKYO, scope=PROJECT)
    ] == ["mtg_late"]


def test_a_local_day_straddling_two_utc_dates_reads_both(records):
    """A negative offset puts a local day's records under two filename prefixes,
    and both are read — the arithmetic, against a real zone rather than a
    hand-computed offset.
    """
    pacific = ZoneInfo("America/Los_Angeles")
    morning = datetime(2026, 9, 4, 16, 0, tzinfo=timezone.utc)  # 09:00 local
    evening = datetime(2026, 9, 5, 3, 0, tzinfo=timezone.utc)  # 20:00 local, same day
    records.put(meeting("mtg_morning", start=morning))
    records.put(meeting("mtg_evening", start=evening))

    found = records.for_day(date(2026, 9, 4), tz=pacific, scope=PROJECT)
    assert [held.meeting.meeting_id for held in found] == ["mtg_morning", "mtg_evening"]


def test_a_midnight_start_belongs_to_the_day_that_begins(records):
    """The interval is half-open, so one meeting is on exactly one day."""
    midnight = datetime(2026, 9, 4, 0, 0, tzinfo=timezone.utc)
    records.put(meeting("mtg_midnight", start=midnight))

    assert len(records.for_day(date(2026, 9, 4), tz=UTC, scope=PROJECT)) == 1
    assert records.for_day(date(2026, 9, 3), tz=UTC, scope=PROJECT) == ()


def test_for_day_refuses_a_datetime(records):
    """A `datetime` is a `date` subclass, so this would otherwise silently drop
    the time of day the caller believed it was passing.
    """
    with pytest.raises(MalformedMeeting, match="datetime"):
        records.for_day(NOW, tz=UTC, scope=PROJECT)


def test_a_malformed_record_for_another_day_does_not_break_this_day(records, tmp_path):
    """Matrix: `for_day` reads only its own day; unrelated damage does not raise.

    The filename carries the UTC day for exactly this reason — the damaged file
    is never opened, rather than opened and forgiven.
    """
    records.put(meeting("mtg_good"))
    directory = collection(tmp_path, PROJECT)
    (directory / f"2026-01-01-junk-{'0' * 64}.md").write_text("not a record\n", encoding="utf-8")

    found = records.for_day(date(2026, 9, 4), tz=UTC, scope=PROJECT)
    assert [held.meeting.meeting_id for held in found] == ["mtg_good"]


def test_a_foreign_file_in_the_collection_is_ignored(records, tmp_path):
    """Matrix: `.DS_Store`, an editor swap file — not parsed as a record."""
    records.put(meeting("mtg_good"))
    directory = collection(tmp_path, PROJECT)
    (directory / ".DS_Store").write_bytes(b"\x00\x01binary")
    (directory / "notes.md").write_text("a stray note\n", encoding="utf-8")
    (directory / f"2026-09-04-x-{'0' * 64}.md.swp").write_bytes(b"\x00swap")

    found = records.for_day(date(2026, 9, 4), tz=UTC, scope=PROJECT)
    assert [held.meeting.meeting_id for held in found] == ["mtg_good"]


# ── Hand edits ───────────────────────────────────────────────────────────────


def test_a_well_formed_hand_edit_is_read_back(records, tmp_path):
    """Matrix: parsed if well-formed. The record is meant to be editable."""
    records.put(meeting())
    member = collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]
    member.write_text(
        member.read_text().replace(
            'title="Project Alpha architecture sync"', "title=retitled-by-hand"
        ),
        encoding="utf-8",
    )

    assert records.get("mtg_01HX", scope=PROJECT).meeting.title == "retitled-by-hand"


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param("meeting_id=mtg_01HX\ntitle=a\n", id="missing-fields"),
        pytest.param("a bare token\n", id="not-a-field"),
        pytest.param("meeting_id=a meeting_id=b\n", id="two-pairs-on-one-line"),
        pytest.param(
            'meeting_id=mtg_01HX\ntitle=a\ntitle=b\nstart=2026-09-04T09:00:00+00:00\n'
            "duration_minutes=45\nattendees=x\ntentative=false\nscope=project:alpha\n",
            id="duplicate-key",
        ),
        pytest.param(
            "meeting_id=mtg_01HX\ntitle=a\nstart=2026-09-04T09:00:00+00:00\n"
            "duration_minutes=45\nattendees=x\ntentative=false\nscope=project:alpha\n"
            "man_hour_cost=900\n",
            id="unknown-key",
        ),
        pytest.param(
            "meeting_id=mtg_01HX\ntitle=a\nstart=2026-09-04T09:00:00\n"
            "duration_minutes=45\nattendees=x\ntentative=false\nscope=project:alpha\n",
            id="naive-start",
        ),
        pytest.param(
            "meeting_id=mtg_01HX\ntitle=a\nstart=not-a-time\n"
            "duration_minutes=45\nattendees=x\ntentative=false\nscope=project:alpha\n",
            id="unparseable-start",
        ),
        pytest.param(
            "meeting_id=mtg_01HX\ntitle=a\nstart=2026-09-04T09:00:00+00:00\n"
            "duration_minutes=half an hour\nattendees=x\ntentative=false\nscope=project:alpha\n",
            id="duration-not-a-number",
        ),
        pytest.param(
            "meeting_id=mtg_01HX\ntitle=a\nstart=2026-09-04T09:00:00+00:00\n"
            "duration_minutes=45\nattendees=a,,b\ntentative=false\nscope=project:alpha\n",
            id="empty-attendee",
        ),
        pytest.param(
            "meeting_id=mtg_01HX\ntitle=a\nstart=2026-09-04T09:00:00+00:00\n"
            "duration_minutes=45\nattendees=x\ntentative=maybe\nscope=project:alpha\n",
            id="third-tentative-state",
        ),
        pytest.param(
            "meeting_id=mtg_01HX\ntitle=a\nstart=2026-09-04T09:00:00+00:00\n"
            "duration_minutes=45\nattendees=x\ntentative=false\nscope=nowhere\n",
            id="unknown-scope",
        ),
        pytest.param('title="unclosed\n', id="unclosed-quote"),
    ],
)
def test_a_malformed_hand_edit_is_surfaced_never_skipped(records, tmp_path, damage):
    """Matrix: a skipped meeting is a missing dashboard row nobody can explain."""
    records.put(meeting())
    member = collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]
    member.write_text(damage, encoding="utf-8")

    with pytest.raises(MalformedMeeting):
        records.get("mtg_01HX", scope=PROJECT)
    with pytest.raises(MalformedMeeting):
        records.for_day(date(2026, 9, 4), tz=UTC, scope=PROJECT)


def test_a_record_whose_notes_heading_was_removed_still_reads(records, tmp_path):
    """The machine region is intact and the human region is merely absent, so
    this is a repair the next `put` makes rather than a refusal.
    """
    records.put(meeting())
    member = collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]
    member.write_text(
        member.read_text().replace(f"{NOTES_HEADING}\n", ""), encoding="utf-8"
    )

    assert records.get("mtg_01HX", scope=PROJECT).notes == ""


def test_a_binary_file_under_a_record_name_is_refused(records, tmp_path):
    records.put(meeting())
    member = collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]
    member.write_bytes(b"\xff\xfe not utf-8")

    with pytest.raises(MalformedMeeting, match="UTF-8"):
        records.get("mtg_01HX", scope=PROJECT)


# ── The render/parse pair ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "record",
    [
        pytest.param(MeetingRecord(meeting()), id="ordinary"),
        pytest.param(MeetingRecord(meeting(), tentative=True), id="tentative"),
        pytest.param(
            MeetingRecord(meeting(), notes="hand written\n\n### heading\n"), id="notes"
        ),
        pytest.param(MeetingRecord(meeting(attendees=())), id="no-attendees"),
        pytest.param(MeetingRecord(meeting(duration_minutes=0)), id="all-day"),
        pytest.param(
            MeetingRecord(meeting(calendar_event_ref=None)), id="no-calendar-ref"
        ),
        pytest.param(MeetingRecord(meeting(calendar_event_ref="")), id="empty-calendar-ref"),
        pytest.param(
            MeetingRecord(meeting(title='a "quoted" title = with\nnewline\tand \\ backslash')),
            id="title-needing-every-escape",
        ),
        pytest.param(MeetingRecord(meeting(title="")), id="empty-title"),
        pytest.param(MeetingRecord(meeting(scope=PERSONAL)), id="personal"),
        pytest.param(MeetingRecord(meeting(scope=PEOPLE)), id="people"),
        pytest.param(MeetingRecord(meeting("AAMkA==")), id="padded-provider-id"),
        pytest.param(
            MeetingRecord(meeting(attendees=(Actor("actor with spaces"),))),
            id="handle-needing-quotes",
        ),
        pytest.param(
            MeetingRecord(meeting(), notes=""), id="empty-notes"
        ),
    ],
)
def test_a_record_survives_render_and_parse(record):
    """Criterion: rendered and parsed back, it is equal — the drift pair.

    Equality is against `as_stored`, which is the record's own reach: a display
    name is not a field of the record and `as_stored` is the whole of that loss.
    """
    round_tripped = parse_record(render_record(record), source="under-test.md")

    assert round_tripped == MeetingRecord(
        as_stored(record.meeting), record.tentative, record.notes
    )


def test_an_empty_calendar_ref_is_not_the_same_as_none():
    """The field is omitted for `None` and rendered for `""`, which is the only
    way the two survive a round trip as different values.
    """
    absent = render_record(MeetingRecord(meeting(calendar_event_ref=None)))
    empty = render_record(MeetingRecord(meeting(calendar_event_ref="")))

    assert "calendar_event_ref" not in absent
    assert "calendar_event_ref=" in empty
    assert parse_record(absent, source="a.md").meeting.calendar_event_ref is None
    assert parse_record(empty, source="b.md").meeting.calendar_event_ref == ""


def test_the_summary_region_is_reserved_and_left_empty():
    """It is transcript-derived and needs a model, which decision 2 puts beyond
    wave 2 — so the region exists and this slice writes nothing into it.
    """
    text = render_record(MeetingRecord(meeting()))

    head, _, rest = text.partition("## Summary\n")
    assert rest.strip() == NOTES_HEADING
    assert head.endswith("\n\n")


def test_a_naive_start_is_refused_at_render():
    """`for_day` computes a UTC boundary, and a naive value reads as local time
    without announcing that it did (AD-35).
    """
    with pytest.raises(MalformedMeeting, match="aware UTC"):
        render_record(MeetingRecord(meeting(start=datetime(2026, 9, 4, 9, 0))))


def test_a_non_utc_offset_start_is_refused_at_render():
    with pytest.raises(MalformedMeeting, match="aware UTC"):
        render_record(MeetingRecord(meeting(start=datetime(2026, 9, 4, 9, 0, tzinfo=TOKYO))))
