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
    SUMMARY_HEADING,
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

# The grammar's closed key set, imported rather than restated. Two tests below
# assert that the file carries no key outside it, and a second hand-written copy
# of the tuple would go on passing after the module's had a key added to it.
from pm_ai.core.meeting_records import _FIELDS
from pm_ai.domain.event_entries import MAX_ENTRY_LENGTH
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
from pm_ai.domain.meetings import Meeting
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import ArtifactBusy
from pm_ai.storage.crypto import PlaintextCrypto
from pm_ai.storage.service import StorageService

NOW = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")
PERSONAL = DataScope(ScopeKind.PERSONAL)
PEOPLE = DataScope(ScopeKind.PEOPLE, person_id="bob")
APPLICATION = DataScope(ScopeKind.APPLICATION)
UTC = timezone.utc
TOKYO = timezone(timedelta(hours=9))
WELL_FORMED = (
    "meeting_id=mtg_01HX\ntitle=a\nstart=2026-09-04T09:00:00+00:00\n"
    "duration_minutes=45\nattendees=x\nscope=project:alpha\n"
)
"""A minimal record body, for the hand-edit cases that vary one field of it."""


class _MemberVanishes:
    """The real writer, with one member removed between the listing and the read.

    Delegates everything but `read_artifact` for that one name, so what is under
    test is the accessor's handling of `read_artifact`'s `None` rather than a
    double's idea of storage. It exists because the state it produces — a member
    `list_collection` reported and `read_artifact` cannot find — is a genuine
    race and not something a sequence of filesystem calls can be made to hit.
    """

    def __init__(self, storage: StorageService, vanished: str) -> None:
        self._storage = storage
        self._vanished = vanished

    def __getattr__(self, name):
        return getattr(self._storage, name)

    def read_artifact(self, *, scope, artifact, name=None):
        if name == self._vanished:
            return None
        return self._storage.read_artifact(scope=scope, artifact=artifact, name=name)


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


def field_keys(text: str) -> list[str]:
    """The keys the field block declares — the only place a stored value can be.

    The two tests below used to search the *whole file* for a substring, which
    includes the human-supplied title, the attendee handles and the notes: a
    meeting titled "Corporate cost review" contains "cost", and "corporate"
    contains "rate", so both assertions failed on ordinary data rather than on a
    stored Man-Hour Cost. The claim is about the grammar, so it is asserted
    against the grammar.
    """
    head, _, _ = text.partition(f"\n\n{SUMMARY_HEADING}")
    return [line.partition("=")[0] for line in head.splitlines()]


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

    records.put(meeting(title="second title"))

    reread = records.get("mtg_01HX", scope=PROJECT)
    assert reread.meeting.title == "second title"
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

    The 2026-09-07 decision made this unreachable through a legitimate write: a
    held meeting's `start` cannot move, and no future one is recorded. The guard
    and this test are kept anyway — reaching it would mean something upstream had
    recorded a meeting that had not happened, which is worth a refusal at the
    write rather than two records for `get` to choose between.
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

    Titled so the assertion this replaced would fail: "Corporate cost review"
    contains "cost", and "corporate" contains "rate". The claim is that no *key*
    holds a cost, so that is what is asserted — the title is the human's and may
    say anything.
    """
    records.put(meeting(title="Corporate cost review"))
    text = (collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]).read_text()

    assert "man_hour_cost" not in field_keys(text)
    assert not set(field_keys(text)) - set(_FIELDS)


def test_neither_tentative_nor_stale_is_a_field(records, tmp_path):
    """Decided 2026-09-07: only meetings that have happened are recorded, so both
    questions are about a meeting this record can never be of. `tentative` is a
    response status for a meeting that has not occurred and `stale` means absent
    from a harvested window, which is a cancellation.

    A field key, not a substring, for the reason one field over: a title reading
    "a tentative agenda and a stale backlog" is ordinary English and broke the
    whole-file search on both words.
    """
    records.put(meeting(title="a tentative agenda and a stale backlog"))
    text = (collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]).read_text()

    keys = field_keys(text)
    assert "tentative" not in keys and "stale" not in keys
    assert not set(keys) - set(_FIELDS)


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
    ("unsafe", "refusal"),
    [
        pytest.param("../memory/leak.md", "path separator", id="posix-traversal"),
        pytest.param("..\\leak", "path separator", id="windows-traversal"),
        pytest.param("", "empty or only whitespace", id="empty"),
        pytest.param("   ", "empty or only whitespace", id="whitespace-only"),
        pytest.param("mtg\n01", "control character", id="newline"),
        pytest.param("  mtg_01HX", "padded with whitespace", id="padded"),
    ],
)
def test_a_path_unsafe_id_is_refused_before_anything_is_written(
    records, tmp_path, unsafe, refusal
):
    """Matrix: an id containing `../` is refused, as `write_artifact` already
    refuses for captures — asked here so the message names the meeting.

    Pinned to `MalformedMeeting` *and* to the message. This accepted either that
    or `MalformedCaptureName`, which meant it would still pass with
    `_assert_recordable_id` deleted and the storage layer catching the fallout —
    precisely the outcome the guard exists to improve on, since the whole reason
    it duplicates a refusal one layer down is that its sentence names the
    meeting.
    """
    with pytest.raises(MalformedMeeting, match=refusal):
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


@pytest.mark.parametrize(
    ("zone", "day", "instants", "expected"),
    [
        pytest.param(
            "America/Havana",
            date(2018, 11, 4),
            # The local day is 25 hours: the clocks fall back at 01:00 local, so
            # the span is 2018-11-04T04:00Z → 2018-11-05T05:00Z. 00:30 local is
            # before the transition and 23:30 local is after it.
            (
                datetime(2018, 11, 4, 4, 30, tzinfo=timezone.utc),
                datetime(2018, 11, 5, 4, 30, tzinfo=timezone.utc),
            ),
            ["mtg_open", "mtg_close"],
            id="twenty-five-hour-day",
        ),
        pytest.param(
            "America/Sao_Paulo",
            date(2018, 11, 4),
            # 23 hours: the clocks spring forward, so the span is
            # 2018-11-04T03:00Z → 2018-11-05T02:00Z. 01:30 and 23:30 local.
            (
                datetime(2018, 11, 4, 3, 30, tzinfo=timezone.utc),
                datetime(2018, 11, 5, 1, 30, tzinfo=timezone.utc),
            ),
            ["mtg_open", "mtg_close"],
            id="twenty-three-hour-day",
        ),
    ],
)
def test_a_day_a_dst_transition_reshapes_is_read_whole(
    records, zone, day, instants, expected
):
    """A local day is not always 24 hours, and `_utc_days` used to reason as if
    it were.

    Measured: `America/Havana` 2018-11-04 spans 25 hours and
    `America/Sao_Paulo` 2018-11-04 spans 23. The conclusion the docstring drew —
    at most two UTC filename prefixes — survives; the premise it drew it from
    did not, and the walk is what makes the arithmetic independent of the width.
    Both ends of each day are asserted, and so is the exclusion of the neighbours
    a mis-shaped span would leak into.
    """
    tz = ZoneInfo(zone)
    opening, closing = instants
    records.put(meeting("mtg_open", start=opening))
    records.put(meeting("mtg_close", start=closing))

    found = records.for_day(day, tz=tz, scope=PROJECT)
    assert [held.meeting.meeting_id for held in found] == expected
    assert records.for_day(day - timedelta(days=1), tz=tz, scope=PROJECT) == ()
    assert records.for_day(day + timedelta(days=1), tz=tz, scope=PROJECT) == ()


def test_for_day_refuses_a_datetime(records):
    """A `datetime` is a `date` subclass, so this would otherwise silently drop
    the time of day the caller believed it was passing.
    """
    with pytest.raises(MalformedMeeting, match="datetime"):
        records.for_day(NOW, tz=UTC, scope=PROJECT)


def test_for_day_refuses_a_missing_timezone(records):
    """`tz=None` answered in the machine's local zone and said nothing about it.

    Measured before the fix: `for_day(date(2026, 9, 4), tz=None)` computed
    2026-09-03T21:00Z → 2026-09-04T21:00Z on the machine this was run on. The
    annotation says `tzinfo` and the module refuses the *other* type-hint
    violation — a `datetime` day — with a paragraph on why an implicit boundary
    is unacceptable, and `_assert_utc`'s own message names the "reads as local
    time without announcing that it did" failure. Same refusal.
    """
    with pytest.raises(MalformedMeeting, match="timezone"):
        records.for_day(date(2026, 9, 4), tz=None, scope=PROJECT)


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
            "duration_minutes=45\nattendees=x\nscope=project:alpha\n",
            id="duplicate-key",
        ),
        pytest.param(
            "meeting_id=mtg_01HX\ntitle=a\nstart=2026-09-04T09:00:00+00:00\n"
            "duration_minutes=45\nattendees=x\nscope=project:alpha\n"
            "man_hour_cost=900\n",
            id="unknown-key",
        ),
        pytest.param(
            # `tentative` is retired, so a hand-added one is an unknown key and
            # nothing more — the refusal every key outside the grammar gets,
            # rather than a bespoke message for one name that used to be a field.
            # No record pm-ai wrote can carry it: the field left the module before
            # `put` had a production caller.
            "meeting_id=mtg_01HX\ntitle=a\nstart=2026-09-04T09:00:00+00:00\n"
            "duration_minutes=45\nattendees=x\nscope=project:alpha\ntentative=false\n",
            id="retired-tentative-key",
        ),
        pytest.param(
            "meeting_id=mtg_01HX\ntitle=a\nstart=2026-09-04T09:00:00\n"
            "duration_minutes=45\nattendees=x\nscope=project:alpha\n",
            id="naive-start",
        ),
        pytest.param(
            "meeting_id=mtg_01HX\ntitle=a\nstart=not-a-time\n"
            "duration_minutes=45\nattendees=x\nscope=project:alpha\n",
            id="unparseable-start",
        ),
        pytest.param(
            "meeting_id=mtg_01HX\ntitle=a\nstart=2026-09-04T09:00:00+00:00\n"
            "duration_minutes=half an hour\nattendees=x\nscope=project:alpha\n",
            id="duration-not-a-number",
        ),
        pytest.param(
            "meeting_id=mtg_01HX\ntitle=a\nstart=2026-09-04T09:00:00+00:00\n"
            "duration_minutes=45\nattendees=a,,b\nscope=project:alpha\n",
            id="empty-attendee",
        ),
        pytest.param(
            "meeting_id=mtg_01HX\ntitle=a\nstart=2026-09-04T09:00:00+00:00\n"
            "duration_minutes=45\nattendees=x\nscope=nowhere\n",
            id="unknown-scope",
        ),
        pytest.param(
            # A scope that parses and has no `meetings/`. Left to the resolver
            # this surfaced as `ArtifactNotInScope` — a `pm_ai.platform` sentence
            # about a directory, out of this vocabulary and out of the type
            # `MalformedMeeting` a caller catches.
            WELL_FORMED.replace("scope=project:alpha", "scope=application"),
            id="scope-with-no-meetings-collection",
        ),
        pytest.param(
            WELL_FORMED.replace("attendees=x", "attendees=a,b,a"),
            id="duplicate-attendee",
        ),
        pytest.param(
            WELL_FORMED.replace("duration_minutes=45", "duration_minutes=1_0"),
            id="duration-in-python-integer-syntax",
        ),
        pytest.param(
            WELL_FORMED.replace("duration_minutes=45", "duration_minutes=+45"),
            id="duration-signed",
        ),
        pytest.param(
            WELL_FORMED.replace("duration_minutes=45", "duration_minutes=٤٥"),
            id="duration-in-arabic-indic-digits",
        ),
        pytest.param(
            WELL_FORMED.replace("duration_minutes=45", "duration_minutes=007"),
            id="duration-with-leading-zeros",
        ),
        pytest.param(
            # Both bounds the renderer keeps, asked on the way in too: a value
            # this module will not write is a value it must not accept back.
            WELL_FORMED.replace("title=a", "title=weekly\x00sync"),
            id="control-character-in-title",
        ),
        pytest.param(
            WELL_FORMED.replace("title=a", f"title={'x' * (MAX_ENTRY_LENGTH + 1)}"),
            id="field-line-past-the-length-bound",
        ),
        pytest.param(
            # Refused by the name-agreement check rather than by the id guard —
            # `record_name` cannot mint a filename for an id it refuses, so a
            # traversal id on disk always disagrees with its own name. The id
            # guard's own parse-side case is asserted against `parse_record`
            # directly, below.
            WELL_FORMED.replace("meeting_id=mtg_01HX", "meeting_id=../leak"),
            id="path-unsafe-id-disagreeing-with-its-name",
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


def test_a_rewrite_repairs_a_record_that_is_not_utf_8(records, tmp_path):
    """`put` is the one operation that can fix a corrupted record, so it is the
    one operation that must not raise on one.

    It did: the notes were lifted through the same reader `get` uses, which
    refuses a member that is not UTF-8 — correct for a read and fatal for a
    repair, since the file could then be neither read nor rewritten and nothing
    could get it back. The decode is lossy instead, so a corruption confined to
    the machine-owned region leaves the human's notes intact.
    """
    records.put(meeting())
    member = collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]
    member.write_bytes(b"\xff\xfe not a record\n\n## Notes\nkeep me\n")
    with pytest.raises(MalformedMeeting, match="UTF-8"):
        records.get("mtg_01HX", scope=PROJECT)

    records.put(meeting(title="repaired"))

    reread = records.get("mtg_01HX", scope=PROJECT)
    assert reread.meeting.title == "repaired"
    assert reread.notes == "keep me\n"


def test_a_record_whose_start_day_disagrees_with_its_name_is_refused(records, tmp_path):
    """It was invisible on every day, and neither stage called it an error.

    `for_day` opens only the members whose filename prefix falls inside the span
    and then filters on the parsed `start`, so a record hand-moved to another day
    was excluded from its new day by the prefix and from its old day by the
    instant. The Design Notes say a missing dashboard row nobody can explain is
    the thing this module refuses, so the disagreement is named where the record
    is opened.
    """
    records.put(meeting())
    member = collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]
    member.write_text(
        member.read_text().replace("start=2026-09-04T", "start=2026-09-06T"),
        encoding="utf-8",
    )

    with pytest.raises(MalformedMeeting, match="2026-09-04.*2026-09-06|2026-09-06"):
        records.get("mtg_01HX", scope=PROJECT)
    with pytest.raises(MalformedMeeting, match="2026-09-06"):
        records.for_day(date(2026, 9, 4), tz=UTC, scope=PROJECT)
    # The other two days never open the file — its prefix is outside their span —
    # so they answer empty. That is why the refusal has to land on the day whose
    # read does open it, which is the day the operator edited away from.
    assert records.for_day(date(2026, 9, 5), tz=UTC, scope=PROJECT) == ()
    assert records.for_day(date(2026, 9, 6), tz=UTC, scope=PROJECT) == ()


def test_a_record_whose_id_disagrees_with_its_name_is_refused(records, tmp_path):
    """The digest narrows the listing and the field confirms it — and the
    confirmation was untested and answered with the wrong sentence.

    A name carrying one id's digest over a record declaring another used to fall
    through to `MeetingNotFound`, which is absence: the ordinary state of a clean
    machine, reported for a contradiction only a hand edit can produce.
    """
    records.put(meeting())
    member = collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]
    member.write_text(
        member.read_text().replace("meeting_id=mtg_01HX", "meeting_id=mtg_someone_else"),
        encoding="utf-8",
    )

    with pytest.raises(MalformedMeeting, match="mtg_someone_else"):
        records.get("mtg_01HX", scope=PROJECT)
    with pytest.raises(MalformedMeeting, match="digest"):
        records.for_day(date(2026, 9, 4), tz=UTC, scope=PROJECT)


# ── The reserved `## Summary` region ─────────────────────────────────────────


def test_a_field_shaped_line_below_the_field_block_is_refused(records, tmp_path):
    """The field block is the *leading* run of lines, so a field one line below
    the blank separator was parsed away and the record kept its old value.

    Measured before the fix: `duration_minutes=999` and `title=hijacked` placed
    there left `get` reporting 45 and the original title, with no message. That
    is what `_parse_field` refuses an unknown key for — a typo silently dropped
    is a value the writer believes it stored — in a file whose whole premise is
    that a human may edit it.
    """
    records.put(meeting())
    member = collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]
    member.write_text(
        member.read_text().replace(
            f"{SUMMARY_HEADING}\n", f"{SUMMARY_HEADING}\nduration_minutes=999\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(MalformedMeeting, match="duration_minutes"):
        records.get("mtg_01HX", scope=PROJECT)


def test_a_hand_written_summary_body_is_refused_rather_than_dropped(records, tmp_path):
    """`## Summary` is machine-owned and empty until a model can fill it, so a
    paragraph written there was read past on parse and gone on the next `put`.

    Refused, and the refusal points at `## Notes` — which is the human's, is
    copied through byte-identical, and is where the paragraph belongs.
    """
    records.put(meeting())
    member = collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]
    member.write_text(
        member.read_text().replace(
            f"{SUMMARY_HEADING}\n", f"{SUMMARY_HEADING}\nWe agreed to cut scope.\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(MalformedMeeting, match=r"## Notes"):
        records.get("mtg_01HX", scope=PROJECT)


def test_the_reserved_region_still_tolerates_its_own_absence(records, tmp_path):
    """Refusing content there must not become refusing a record without the
    heading: the machine region is intact and the next `put` writes it back, the
    same forgiveness `## Notes` gets.
    """
    records.put(meeting())
    member = collection(tmp_path, PROJECT) / members(tmp_path, PROJECT)[0]
    member.write_text(
        member.read_text().replace(f"{SUMMARY_HEADING}\n\n", ""), encoding="utf-8"
    )

    assert records.get("mtg_01HX", scope=PROJECT).meeting == as_stored(meeting())


# ── Scopes that can hold a record ────────────────────────────────────────────


def test_a_scope_with_no_meetings_collection_is_refused_in_this_vocabulary(records):
    """`meetings/` is declared in three trees and the application scope is not
    one of them, so a record there has nowhere to land.

    All three entry points, because the scope arrives three ways: a `Meeting`
    field for `put`, an argument for `get` and `for_day`, and a parsed field on
    the way back in. Left to `ScopePaths.resolve` this escaped as
    `ArtifactNotInScope` — a `pm_ai.platform` refusal about a directory, which is
    neither this module's vocabulary nor a type its callers catch.
    """
    with pytest.raises(MalformedMeeting, match="meetings/"):
        records.put(meeting("mtg_app", scope=APPLICATION))
    with pytest.raises(MalformedMeeting, match="meetings/"):
        records.get("mtg_app", scope=APPLICATION)
    with pytest.raises(MalformedMeeting, match="meetings/"):
        records.for_day(date(2026, 9, 4), tz=UTC, scope=APPLICATION)
    with pytest.raises(MalformedMeeting, match="meetings/"):
        render_record(MeetingRecord(meeting("mtg_app", scope=APPLICATION)))


# ── Attendees counted once, fields bounded ───────────────────────────────────


def test_a_duplicate_attendee_handle_is_refused(records):
    """One person listed twice is two people to the Man-Hour Cost and to every
    per-actor count that groups by handle.
    """
    with pytest.raises(MalformedMeeting, match="twice"):
        records.put(meeting(attendees=(Actor("actor_alex"), Actor("actor_alex"))))


def test_a_display_name_does_not_make_two_handles_one_attendee(records):
    """The handle is what the record holds, so two actors differing only in
    display name are one duplicate rather than two attendees.
    """
    with pytest.raises(MalformedMeeting, match="twice"):
        records.put(
            meeting(attendees=(Actor("actor_alex", "Alex"), Actor("actor_alex", "A. Smith")))
        )


@pytest.mark.parametrize(
    "field",
    [
        pytest.param("title", id="title"),
        pytest.param("attendees", id="attendees"),
    ],
)
def test_a_field_line_past_the_ledgers_length_bound_is_refused(records, field):
    """The record inherits the ledger's escaping; it inherited neither of its
    bounds. `render_entry` refuses a line past `MAX_ENTRY_LENGTH` because a
    segment is plaintext Markdown meant to be read, grepped and diffed by hand,
    and a record is the same kind of file for the same reader — `title` and the
    joined `attendees` are the two fields a caller can make arbitrarily long.
    """
    oversized = (
        {"title": "x" * (MAX_ENTRY_LENGTH + 1)}
        if field == "title"
        else {"attendees": tuple(Actor(f"actor_{n:05d}") for n in range(2000))}
    )
    with pytest.raises(MalformedMeeting, match=str(MAX_ENTRY_LENGTH)):
        records.put(meeting(**oversized))


@pytest.mark.parametrize("control", ["\x00", "\x07", "\x1b"])
def test_a_control_character_in_a_title_is_refused(records, control):
    """`_assert_recordable_id` refuses these in an id and `title` took them raw.

    Narrower than the id's refusal, deliberately: `render_value` escapes a
    backslash, a quote, a newline and a carriage return, so those four survive a
    line and a real Graph subject carrying one is still storable — the
    round-trip case `title-needing-every-escape` is what pins that. What
    escaping does not answer for is the rest of C0 and DEL, which are invisible
    in the file and active in the terminal that prints it.
    """
    with pytest.raises(MalformedMeeting, match="control character"):
        records.put(meeting(title=f"weekly{control}sync"))


# ── The claim `put` holds ────────────────────────────────────────────────────


def test_put_takes_an_exclusive_claim_over_the_collection(records, storage):
    """`put` reads the previous member and then replaces the file whole, which is
    a read-modify-write and was unlocked.

    Two concurrent `put`s each preserved a different snapshot of `## Notes` and
    each wrote their own back, so one human edit was lost — the same loss `8b`
    takes this claim over `private/config.json` to prevent. It refuses rather
    than waits, so a second writer is named instead of quietly winning.

    The claim does not cover a *human's* editor, which honours nothing of
    pm-ai's; that window is stated on `put` rather than implied to be closed.
    """
    with storage.exclusive(scope=PROJECT, artifact=MEETINGS):
        with pytest.raises(ArtifactBusy):
            records.put(meeting())


def test_a_member_listed_then_removed_is_absent_rather_than_malformed(
    storage, records, tmp_path
):
    """`_text` answers `None` for a member that was listed and then removed, and
    `get` and `for_day` do two different right things with it.

    The branch documented both and no case constructed the state — it is a race
    between `list_collection` and `read_artifact` that no ordering of filesystem
    calls produces on demand, so one member's read is intercepted while every
    other call goes to the real writer.
    """
    records.put(meeting("mtg_gone"))
    records.put(meeting("mtg_here"))
    vanished = next(name for name in members(tmp_path, PROJECT) if "mtg_gone" in name)
    racing = MeetingRecords(_MemberVanishes(storage, vanished))

    with pytest.raises(MeetingNotFound):
        racing.get("mtg_gone", scope=PROJECT)
    found = racing.for_day(date(2026, 9, 4), tz=UTC, scope=PROJECT)
    assert [held.meeting.meeting_id for held in found] == ["mtg_here"]


# ── The render/parse pair ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "record",
    [
        pytest.param(MeetingRecord(meeting()), id="ordinary"),
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

    assert round_tripped == MeetingRecord(as_stored(record.meeting), record.notes)


def test_parse_record_refuses_what_render_record_will_not_write():
    """Render and parse move together: a value this module will not write is one
    it must not accept back — the rule `MalformedMeeting`'s docstring states.

    Asserted against `parse_record` rather than through the accessor for the id
    case, because `record_name` cannot mint a filename for an id it refuses, so
    no member on disk can carry one and the name-agreement check answers first.
    `parse_record` is public and `33c` will hand it text.
    """
    with pytest.raises(MalformedMeeting, match="path separator"):
        parse_record(
            WELL_FORMED.replace("meeting_id=mtg_01HX", "meeting_id=../leak"),
            source="under-test.md",
        )
    with pytest.raises(MalformedMeeting, match="control character"):
        parse_record(
            WELL_FORMED.replace("title=a", "title=weekly\x07sync"),
            source="under-test.md",
        )
    with pytest.raises(MalformedMeeting, match=str(MAX_ENTRY_LENGTH)):
        parse_record(
            WELL_FORMED.replace("title=a", f"title={'x' * (MAX_ENTRY_LENGTH + 1)}"),
            source="under-test.md",
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

    Partitioned on `SUMMARY_HEADING`, not on the literal — the same file already
    imports `NOTES_HEADING` for the other heading, and a literal here would have
    left a green test pinning the old spelling after a rename.
    """
    text = render_record(MeetingRecord(meeting()))

    head, _, rest = text.partition(f"{SUMMARY_HEADING}\n")
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


def test_a_named_zone_that_is_merely_zero_offset_today_is_refused():
    """`Europe/London` is +00:00 in January and +01:00 in July, so an offset read
    at one instant accepted the same calendar in winter and refused it in summer.

    The record's UTC day is in its filename, so which half of the year a meeting
    fell in decided whether it could be stored at all — and a January meeting
    stored through a London zone would have been compared against `for_day`'s UTC
    boundary as if it were UTC, which it is only by coincidence.
    """
    london = ZoneInfo("Europe/London")
    winter = datetime(2026, 1, 14, 9, 0, tzinfo=london)
    assert winter.utcoffset() == timedelta(0), "the case's premise: zero offset"

    with pytest.raises(MalformedMeeting, match="aware UTC"):
        render_record(MeetingRecord(meeting(start=winter)))
    with pytest.raises(MalformedMeeting, match="aware UTC"):
        render_record(
            MeetingRecord(meeting(start=datetime(2026, 7, 14, 9, 0, tzinfo=london)))
        )


def test_a_zone_that_really_is_utc_is_still_accepted():
    """The guard above must refuse a zone that merely coincides with UTC, not
    every zone that is not the `timezone.utc` singleton — `ZoneInfo("UTC")` and a
    zero fixed offset are both UTC for all time.
    """
    for zone in (ZoneInfo("UTC"), timezone(timedelta(0)), timezone.utc):
        text = render_record(
            MeetingRecord(meeting(start=datetime(2026, 9, 4, 9, 0, tzinfo=zone)))
        )
        assert parse_record(text, source="a.md").meeting.start == NOW
