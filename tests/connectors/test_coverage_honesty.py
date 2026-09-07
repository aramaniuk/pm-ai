"""Story 8a — the coverage-honesty matrix, row by row.

The defect this file exists over: `GitLabConnectorAdapter.harvest` built its
`CoverageWindow` as `now() - 4h` to `now()`, unconditionally, because
`HarvestResult` *required* one. A provider declining with an empty `200`
therefore recorded four hours of harvest coverage, and AD-35's fail-closed guard
reads a covered window as "absence of evidence is evidence of absence" — the one
reading that may fire an irreversible nudge.

Two properties carry most of the weight here, and both are asserted positively
rather than by absence:

**A window's bounds are measured, not computed.** Asserting only that no window
was recorded would have passed against the fabricating connector too — coverage
rows are keyed on `CoverageWindow.connector_instance`, so a window filed under a
different key already read back as `[]`. And grepping for `timedelta(hours=4)`
is satisfied by `timedelta(minutes=240)`. So the rows below name the exact
instants the connector's clock returned.

**A failure is a value, and it survives the process.** A raise cannot carry the
coverage a partial fetch earned, which is why the page-one-ok/page-two-failed row
asserts three things in one return value. And "ran and failed" used to persist as
the absence of a coverage window — indistinguishable, after a restart, from "ran
and learned nothing" — which left `evaluate_commitment`'s required
`harvest_failed` with no source at all.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from pm_ai.app.pipelines import run_harvest
from pm_ai.app.wiring import build
from pm_ai.connectors.gitlab import GitLabConnectorAdapter, Page, PageUnavailable
from pm_ai.domain.harvest import Cursor, HarvestFailure, HarvestOutcome, HarvestResult
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.lifecycle import CoverageWindow
from pm_ai.storage.service import CoverageInstanceMismatch

NOW = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
SCOPE = DataScope(ScopeKind.PROJECT, "alpha")
INSTANCE = "gitlab:alpha"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

TICK = timedelta(seconds=1)

ROWS = [
    {
        "sha": "9f2a1c",
        "message": "Fix auth refactor",
        "author_email": "alex@example.com",
        "committed_at": NOW - timedelta(hours=2),
    },
    {
        "sha": "3b7e02",
        "message": "Add Redis benchmark",
        "author_email": "alex@example.com",
        "committed_at": NOW - timedelta(hours=1),
    },
]


class Ticking:
    """A clock that moves, so `start` and `end` can be told apart.

    A frozen clock makes an honest window a point, which is fine for the daemon
    fixture and useless here: the rule under test is that `start` is the instant
    the *first* page came back and `end` the instant fetching stopped, and a
    constant satisfies any pair of instants. Every reading is recorded, so a row
    can assert which reading a bound came from rather than that it looks recent.
    """

    def __init__(self, start: datetime = NOW, step: timedelta = TICK) -> None:
        self.at = start
        self.step = step
        self.readings: list[datetime] = []

    def __call__(self) -> datetime:
        reading = self.at
        self.readings.append(reading)
        self.at += self.step
        return reading


def connector(**kwargs) -> GitLabConnectorAdapter:
    kwargs.setdefault("now", lambda: NOW)
    return GitLabConnectorAdapter(project="alpha", scope=SCOPE, **kwargs)


def daemon(tmp_path, *, rows=None, page_size: int = 100, now=None, fetch_page=None):
    """A wired daemon whose GitLab connector is the one under test.

    Storage keeps the frozen `NOW` — its clock names event-log segments and
    stamps `ingested_at` — while the connector gets whatever clock the row needs.
    Two clocks on purpose: coverage is the *connector's* reading, and sharing one
    would let a row pass because storage happened to agree.
    """
    shutil.rmtree(tmp_path, ignore_errors=True)
    wired = build(tmp_path, "alpha", now=lambda: NOW)
    gitlab = wired.connectors[INSTANCE]
    gitlab._fake_api = list(rows or [])
    gitlab.page_size = page_size
    if now is not None:
        gitlab.now = now
    if fetch_page is not None:
        gitlab.fetch_page = fetch_page
    return wired


def paging(*pages: tuple[dict, ...], fails_at: PageUnavailable | None = None):
    """A transport handing back exactly these pages, then optionally refusing.

    Injected rather than simulated by trimming `_fake_api`, because the row that
    matters most is the one where a *later* page fails — which no arrangement of
    rows can express. The last supplied page ends the walk when nothing is set to
    fail, and points at one more page when something is: that next fetch is where
    `fails_at` is raised.
    """
    boundaries: dict[int, Page] = {}
    offset = 0
    for index, rows in enumerate(pages):
        last = index == len(pages) - 1
        following = offset + len(rows)
        boundaries[offset] = Page(
            rows=rows,
            next_offset=None if last and fails_at is None else following,
        )
        offset = following

    def fetch(at: int) -> Page:
        if at in boundaries:
            return boundaries[at]
        if fails_at is not None:
            raise fails_at
        return Page()

    return fetch


# ── Rows that returned something ─────────────────────────────────────────────


def test_two_pages_bound_the_window_with_the_connectors_own_clock():
    """Row: *fetch returns rows* — coverage spans first page back to fetch end."""
    clock = Ticking()
    result = connector(now=clock, page_size=1, _fake_api=list(ROWS)).harvest(Cursor())

    assert result.outcome is HarvestOutcome.HARVESTED
    assert len(result.events) == 2
    assert result.failure is None
    assert result.coverage == CoverageWindow(
        connector_instance=INSTANCE,
        start=clock.readings[0],
        end=clock.readings[-1],
    )
    # Named, not merely "distinct": the first reading is taken when page one
    # comes back and the last when the walk finishes, so a connector that read
    # its clock once and subtracted an interval cannot produce this pair. The
    # middle reading is the wall-clock budget check, taken once per page the
    # walk decides to continue past.
    assert clock.readings == [NOW, NOW + TICK, NOW + 2 * TICK]
    assert result.cursor == Cursor(b"2")


def test_two_pages_read_back_exactly_one_window_bounded_by_the_fetch(tmp_path):
    """The same row, asserted against storage rather than the return value.

    A positive bound assertion through `coverage_windows`, which is what the
    sweeper reads. Absence here proves nothing — the fabricated window was filed
    under its own `connector_instance` and this call already returned `[]` for a
    mismatched key.
    """
    clock = Ticking()
    wired = daemon(tmp_path, rows=ROWS, page_size=1, now=clock)
    persisted = run_harvest(wired, INSTANCE)

    assert persisted.persisted == 2
    assert wired.storage.coverage_windows(INSTANCE) == [(NOW, NOW + 2 * TICK)]
    assert wired.storage.harvest_failure(INSTANCE) is None


def test_rows_whose_clocks_cannot_be_believed_claim_no_coverage(tmp_path):
    """Row: *rows returned, no usable clock* — harvested, and no window claimed.

    One row has no `committed_at` at all and the other carries the zero-value
    parse (`EARLIEST_PLAUSIBLE` exists for exactly that). Something arrived and
    nothing says when, and an unknowable start is not a guessable one — so the
    events persist and the coverage claim does not.
    """
    unplaceable = [
        {"sha": "aaaaaa", "message": "no clock", "author_email": "a@example.com",
         "committed_at": None},
        {"sha": "bbbbbb", "message": "epoch", "author_email": "a@example.com",
         "committed_at": datetime(1970, 1, 1, tzinfo=timezone.utc)},
    ]
    result = connector(_fake_api=list(unplaceable)).harvest(Cursor())
    assert result.outcome is HarvestOutcome.HARVESTED
    assert len(result.events) == 2
    assert result.coverage is None

    wired = daemon(tmp_path, rows=unplaceable)
    run_harvest(wired, INSTANCE)
    assert wired.storage.coverage_windows(INSTANCE) == []


def test_a_refused_rows_clock_cannot_earn_coverage_for_the_rows_that_survived(tmp_path):
    """Row *rows returned, no usable clock*, from the direction that hid.

    The credibility predicate walked the raw page while the coverage gate asked
    about surviving events, so a refused row's believable timestamp answered
    "can anything here be placed in time" on behalf of rows nobody kept. Here
    the only surviving event is dated 2126 and the credible clock belongs to the
    row that was thrown away: no event that persists can be placed in time, so
    no window may be claimed.
    """
    emitted_but_unplaceable = {
        "sha": "aaaaaa", "message": "from the future",
        "author_email": "a@example.com",
        "committed_at": datetime(2126, 9, 7, 12, 0, tzinfo=timezone.utc),
    }
    refused_but_well_dated = {
        "sha": "bbbbbb", "message": None,  # refused: `message` is not a string
        "author_email": "a@example.com", "committed_at": NOW,
    }
    rows = [emitted_but_unplaceable, refused_but_well_dated]

    result = connector(_fake_api=[dict(r) for r in rows]).harvest(Cursor())

    assert result.outcome is HarvestOutcome.HARVESTED
    assert len(result.events) == 1
    assert len(result.refusals) == 1
    assert result.coverage is None, (
        "the only credible clock belonged to a refused row, so coverage was "
        "claimed on evidence the harvest discarded"
    )

    wired = daemon(tmp_path, rows=[dict(r) for r in rows])
    run_harvest(wired, INSTANCE)
    assert wired.storage.coverage_windows(INSTANCE) == []


def test_a_surviving_rows_clock_still_earns_coverage_beside_a_refusal(tmp_path):
    """The opposite direction, so the rule cannot degrade into "any refusal voids".

    A refusal beside a placeable event withholds nothing: rows arrived, one of
    them persists and can be placed in time, and that is what coverage records.
    """
    rows = [
        {"sha": "cccccc", "message": "readable", "author_email": "a@example.com",
         "committed_at": NOW},
        {"sha": "dddddd", "message": None, "author_email": "a@example.com",
         "committed_at": NOW},
    ]

    result = connector(_fake_api=[dict(r) for r in rows]).harvest(Cursor())

    assert len(result.events) == 1
    assert len(result.refusals) == 1
    assert result.coverage == CoverageWindow(
        connector_instance=INSTANCE, start=NOW, end=NOW
    )


def test_a_cursor_advances_even_when_no_coverage_was_earned(tmp_path):
    """Row: *cursor with no coverage* — the cursor is saved, the window is not.

    `save_cursor` takes `CoverageWindow | None` by signature, so `None` travels
    through as itself instead of being read off an object through `getattr` and
    silently becoming "no coverage" for any reason at all.
    """
    unplaceable = [
        {"sha": "cccccc", "message": "no clock", "author_email": "a@example.com",
         "committed_at": None},
    ]
    wired = daemon(tmp_path, rows=unplaceable)
    assert wired.storage.load_cursor(INSTANCE) == Cursor()

    run_harvest(wired, INSTANCE)
    assert wired.storage.load_cursor(INSTANCE) == Cursor(b"1"), "the cursor did not advance"
    assert wired.storage.coverage_windows(INSTANCE) == []


def test_the_cursor_stops_where_the_rows_stopped_not_where_the_provider_said():
    """One definition of "where to resume", and it is the rows that were received.

    The loop had two. The terminal and non-advancing branches resumed from rows
    actually received; the ordinary branch resumed from `page.next_offset` — the
    provider's *claim*. So a provider that returns two rows while announcing
    that the next page begins at offset 5 advanced the cursor past rows 2, 3 and
    4, and no later run revisits a position the cursor has passed.

    The page after the gap is still *asked* for, because `next_offset` is a
    perfectly good thing to ask; what it may not do is decide what was walked.
    """
    pages = {
        0: Page(rows=(ROWS[0], ROWS[1]), next_offset=5),
        5: Page(rows=(), next_offset=None),
    }
    result = connector(now=Ticking(), fetch_page=lambda at: pages[at]).harvest(Cursor())

    assert result.outcome is HarvestOutcome.HARVESTED
    assert len(result.events) == 2
    assert result.cursor == Cursor(b"2"), (
        "the cursor advanced to the offset the provider announced, so rows 2 "
        "through 4 are behind it and nothing will ever fetch them"
    )


def test_a_page_loop_is_bounded_by_a_cap_and_keeps_what_it_walked():
    """The bound "next_offset must increase" does not give.

    A provider advancing one row per page satisfies that condition forever, so
    the walk is capped, and the cap is a refusal rather than a quiet stop: a
    silent stop would report a harvest as complete.
    """
    fetch = paging((ROWS[0],), (ROWS[1],))
    result = connector(now=Ticking(), fetch_page=fetch, page_cap=1).harvest(Cursor())

    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None and "cap" in result.failure.reason
    assert [event.payload.sha for event in result.events] == [ROWS[0]["sha"]]
    assert result.coverage is not None, "the page that was walked still covers"
    assert result.cursor == Cursor(b"1"), "and the cursor stops where the rows did"


def test_a_walk_that_outlives_its_budget_returns_what_it_has():
    """The dimension the cap does not cover: a provider answering slowly.

    Measured from the instant the first page came back, on the injected clock —
    a wall-clock bound asserted against `time.monotonic` would be a bound no
    test could exercise.
    """
    fetch = paging((ROWS[0],), (ROWS[1],))
    result = connector(now=Ticking(), fetch_page=fetch, budget=TICK).harvest(Cursor())

    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None and "budget" in result.failure.reason
    assert result.failure.retryable is True, "the rest of the window is the next run's"
    assert len(result.events) == 1
    assert result.coverage is not None


def test_a_transport_that_answers_with_something_other_than_a_page_is_a_value():
    """The check `GraphClient._send` applies to its own injected transport.

    `fetch_page` is a seam. A callable that answers `None` raised
    `AttributeError` on `page.rows`, *outside* every handler in the loop — so
    page one's events, page one's real coverage and the outcome went up the
    stack together, which is the partial-page row's whole prohibition.
    """
    pages: dict[int, object] = {0: Page(rows=(ROWS[0],), next_offset=1), 1: None}
    result = connector(now=Ticking(), fetch_page=lambda at: pages[at]).harvest(Cursor())  # type: ignore[arg-type]

    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None
    assert "NoneType" in result.failure.reason and "Page" in result.failure.reason
    assert result.failure.retryable is False, "a wiring fault in pm-ai does not clear by waiting"
    assert [event.payload.sha for event in result.events] == [ROWS[0]["sha"]]
    assert result.coverage == CoverageWindow(INSTANCE, NOW, NOW + 2 * TICK)


def test_a_stored_cursor_this_connector_did_not_write_is_a_failure_value():
    """`int(since.token)` on a token that is not a decimal offset raised.

    A cursor is opaque to everything but its own connector, which cuts both
    ways: a token written by another connector, or by an older encoding, is not
    a position here. `harvest` promises a value for every outcome, and a
    `ValueError` out of its first line is not one.
    """
    stale = Cursor(b"page-2-token")
    result = connector(_fake_api=list(ROWS)).harvest(stale)

    assert result.outcome is HarvestOutcome.FAILED
    assert result.events == ()
    assert result.coverage is None
    assert result.cursor is stale, "nothing may advance past rows nobody fetched"
    assert result.failure is not None and result.failure.retryable is False
    assert "page-2-token" not in result.failure.reason, (
        "a cursor is opaque provider state and this reason is written to Tier 2"
    )


def test_a_duplicate_row_across_pages_counts_its_span_once(tmp_path):
    """Row: *duplicate across pages* — deduped on the natural key, one window."""
    repeated = ROWS[0]
    wired = daemon(tmp_path, fetch_page=paging((repeated,), (repeated,)), now=Ticking())
    persisted = run_harvest(wired, INSTANCE)

    assert (persisted.persisted, persisted.duplicates) == (1, 1)
    assert wired.storage.coverage_windows(INSTANCE) == [(NOW, NOW + 2 * TICK)], (
        "the span is one window regardless of how many pages carried the row"
    )


# ── Rows that came back and could not be read ────────────────────────────────
#
# Not a matrix row of its own, and the reason it belongs in this file anyway:
# the matrix's *partial-page* row says a page that arrived keeps its events and
# its earned coverage even when something else went wrong. `_to_event`
# subscripted `row["sha"]`, `row["message"]` and `row["author_email"]`, and the
# mapping ran outside every `try` in `harvest` — so one row missing one field
# raised out of the method and took page one's events, page one's real coverage
# and the outcome with it, one field lower than the row that forbids exactly
# that.

UNREADABLE = {
    "sha": "3b7e02",
    # Not a string, which is the one shape `_to_event` refuses outright: the
    # message is the evidence a verifier reads, and an invented empty body reads
    # as a commit that said nothing.
    "message": None,
    "author_email": "alex@example.com",
    "committed_at": NOW - timedelta(hours=1),
}


def test_one_unreadable_row_costs_that_row_and_nothing_beside_it(tmp_path):
    """The good row, the refusal, and the coverage — all three in one value.

    The coverage is the half that carries the weight. A raise out of the mapping
    discarded a window this fetch had genuinely earned, and a window discarded
    is indistinguishable, later, from a window never earned: AD-35's fail-closed
    guard reads both as "we never looked".
    """
    result = connector(_fake_api=[dict(ROWS[0]), dict(UNREADABLE)]).harvest(Cursor())

    assert result.outcome is HarvestOutcome.HARVESTED
    assert len(result.events) == 1
    assert result.events[0].payload.sha == ROWS[0]["sha"]
    (refusal,) = result.refusals
    assert refusal.identifier == UNREADABLE["sha"], (
        "the refusal names the row, or nobody can go and look at it"
    )
    assert "message" in refusal.reason
    assert result.coverage == CoverageWindow(INSTANCE, NOW, NOW), (
        "the coverage the fetch earned was discarded along with the row it could "
        "not read"
    )
    assert result.failure is None, "an unreadable row is not a failed fetch"

    wired = daemon(tmp_path, rows=[dict(ROWS[0]), dict(UNREADABLE)])
    persisted = run_harvest(wired, INSTANCE)
    assert persisted.persisted == 1
    assert wired.storage.coverage_windows(INSTANCE) == [(NOW, NOW)]
    assert wired.storage.harvest_failure(INSTANCE) is None


def test_a_committed_at_that_arrives_as_a_string_refuses_one_row_not_the_harvest():
    """The shape real GitLab sends, and the exception it used to raise.

    `committed_at` on the wire is an ISO **string**. Passed on unparsed it
    reaches `validate_occurred_at`, whose `_assert_comparable` asks a `str` for
    `.tzinfo` — an `AttributeError` out of the coverage check, out of `harvest`,
    and out of `run_harvest`, none of which catch it. So the whole harvest died
    on a field that is present and well-formed at the provider.

    Refused by name instead, and the credible row beside it keeps its coverage.
    """
    stringly = {
        "sha": "5c1d4e",
        "message": "committed_at as GitLab really sends it",
        "author_email": "alex@example.com",
        "committed_at": "2026-08-19T07:00:00+00:00",
    }
    result = connector(_fake_api=[dict(ROWS[0]), stringly]).harvest(Cursor())

    assert result.outcome is HarvestOutcome.HARVESTED
    assert [event.payload.sha for event in result.events] == [ROWS[0]["sha"]]
    (refusal,) = result.refusals
    assert refusal.identifier == "5c1d4e"
    assert "str" in refusal.reason and "datetime" in refusal.reason
    assert result.coverage == CoverageWindow(INSTANCE, NOW, NOW)


def test_a_refused_row_advances_the_cursor_past_itself(tmp_path):
    """Otherwise the same unreadable row is re-fetched and re-refused forever.

    A refusal is a decision about the row, not a reason to come back for it:
    re-asking would refuse it identically, and a cursor held back on it would
    never reach the rows behind it.
    """
    wired = daemon(tmp_path, rows=[dict(ROWS[0]), dict(UNREADABLE)])
    run_harvest(wired, INSTANCE)
    assert wired.storage.load_cursor(INSTANCE) == Cursor(b"2")


def test_a_page_whose_every_row_is_refused_is_harvested_not_empty():
    """Rows arrived and pm-ai could not read them, which is the one of the two
    silences that needs somebody to look — so it cannot collapse into EMPTY.
    """
    result = connector(_fake_api=[dict(UNREADABLE)]).harvest(Cursor())

    assert result.outcome is HarvestOutcome.HARVESTED
    assert result.events == ()
    assert len(result.refusals) == 1
    assert result.coverage is None, "no event survived, so there is nothing to cover"


# ── Rows that returned nothing ───────────────────────────────────────────────


def test_an_empty_200_is_ran_and_learned_nothing(tmp_path):
    """Row: *provider returns empty 200* — no rows, no error, and no coverage.

    The row the fabricated window got most wrong: a provider that declined
    politely recorded four hours of coverage, and nothing anywhere disagreed.
    """
    result = connector(_fake_api=[]).harvest(Cursor())
    assert result.outcome is HarvestOutcome.EMPTY
    assert result.events == ()
    assert result.coverage is None
    assert result.failure is None

    wired = daemon(tmp_path, rows=[])
    run_harvest(wired, INSTANCE)
    assert wired.storage.coverage_windows(INSTANCE) == [], (
        "a provider that answered with nothing covered nothing"
    )


# ── Rows that failed ─────────────────────────────────────────────────────────


def test_a_5xx_is_a_value_and_moves_nothing(tmp_path):
    """Row: *provider 5xx* — failure outcome, no coverage, cursor unmoved.

    That this test can `assert` at all is half the row: `harvest` returns rather
    than raising, because a raise on page two would take page one's coverage with
    it.
    """
    since = Cursor(b"7")
    refused = PageUnavailable("GitLab answered 502", retryable=True)
    result = connector(fetch_page=paging(fails_at=refused)).harvest(since)

    assert result.outcome is HarvestOutcome.FAILED
    assert result.events == ()
    assert result.coverage is None
    assert result.cursor is since, "the cursor must be the one handed in, unchanged"
    assert result.failure is not None
    assert "502" in result.failure.reason
    assert result.failure.retryable is True

    wired = daemon(tmp_path, fetch_page=paging(fails_at=refused))
    run_harvest(wired, INSTANCE)
    assert wired.storage.coverage_windows(INSTANCE) == []
    stored = wired.storage.harvest_failure(INSTANCE)
    assert stored is not None and "502" in stored.reason


def test_page_two_failing_returns_page_ones_events_coverage_and_the_failure(tmp_path):
    """Row: *partial page failure* — all three, in one value.

    The shape an exception cannot express, and the reason the outcome is a value
    rather than a raise. The cursor advances to page one's end only, so page two
    is fetched again next time instead of skipped.
    """
    clock = Ticking()
    fetch = paging((ROWS[0],), fails_at=PageUnavailable("page two refused", retryable=False))
    result = connector(now=clock, fetch_page=fetch).harvest(Cursor())

    assert result.outcome is HarvestOutcome.FAILED
    assert len(result.events) == 1
    assert result.coverage == CoverageWindow(INSTANCE, clock.readings[0], clock.readings[-1])
    assert result.failure is not None and "page two refused" in result.failure.reason
    assert result.cursor == Cursor(b"1"), "the cursor advanced past page two"

    wired = daemon(tmp_path, now=Ticking(), fetch_page=fetch)
    persisted = run_harvest(wired, INSTANCE)
    assert persisted.persisted == 1
    assert wired.storage.coverage_windows(INSTANCE) == [(NOW, NOW + 2 * TICK)]
    assert wired.storage.load_cursor(INSTANCE) == Cursor(b"1")
    assert wired.storage.harvest_failure(INSTANCE) is not None


def test_a_429_surfaces_its_retry_hint_and_keeps_what_it_walked(tmp_path):
    """Row: *throttled* — the pages already walked keep their real coverage."""
    fetch = paging(
        (ROWS[0],),
        fails_at=PageUnavailable(
            "429 Too Many Requests", retryable=True, retry_after=timedelta(seconds=90)
        ),
    )
    result = connector(now=Ticking(), fetch_page=fetch).harvest(Cursor())

    assert result.outcome is HarvestOutcome.FAILED
    assert result.coverage == CoverageWindow(INSTANCE, NOW, NOW + 2 * TICK)
    assert result.failure is not None
    assert result.failure.retryable is True
    assert result.failure.retry_after == timedelta(seconds=90)

    wired = daemon(tmp_path, now=Ticking(), fetch_page=fetch)
    run_harvest(wired, INSTANCE)
    stored = wired.storage.harvest_failure(INSTANCE)
    assert stored is not None
    assert (stored.retryable, stored.retry_after) == (True, timedelta(seconds=90)), (
        "a retry hint that does not survive the write is a hint no scheduler sees"
    )


def test_a_transport_that_throws_something_unclassified_still_returns():
    """Not a matrix row — the reason the `except` in `harvest` is broad.

    Any exception a real HTTP client can raise has to become this value. If one
    escapes, the pages already walked lose their coverage on the way up, which is
    precisely what the partial-page row forbids.
    """

    def explode(offset: int) -> Page:
        raise TimeoutError("the socket gave up on https://gitlab.example/api?private_token=s3cret")

    result = connector(fetch_page=explode).harvest(Cursor())
    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None
    assert "TimeoutError" in result.failure.reason
    assert "s3cret" not in result.failure.reason, (
        "`PageUnavailable`'s own rule — no credential material in a reason that "
        "is written to Tier 2 — binds this branch too, and it used to interpolate "
        "the exception's `repr`"
    )
    assert result.failure.retryable is False, (
        "a fault nobody classified must not be promised to clear on its own"
    )


def test_a_provider_position_that_does_not_advance_is_reported_not_looped():
    """Also not a matrix row — the walk must terminate.

    A transport handing back a next offset at or behind the one just fetched
    would re-harvest the same rows forever. Reported as a failure so the pages
    already walked keep their coverage.
    """
    result = connector(
        now=Ticking(), fetch_page=lambda offset: Page(rows=(ROWS[0],), next_offset=0)
    ).harvest(Cursor())

    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None and "does not advance" in result.failure.reason
    assert len(result.events) == 1
    assert result.coverage == CoverageWindow(INSTANCE, NOW, NOW + TICK)


# ── What survives the process ────────────────────────────────────────────────


def test_a_failed_harvest_is_still_a_failure_after_a_restart(tmp_path):
    """Row: *failure read back after a restart*.

    Asserted across a fresh `StorageService`, because in one process the
    distinction survives in memory and proves nothing. Before this slice both
    "failed" and "learned nothing" persisted as the absence of a coverage window,
    so `evaluate_commitment`'s required `harvest_failed` had no source and a dead
    credential resolved to `UNKNOWN` — patience, forever.
    """
    wired = daemon(
        tmp_path,
        fetch_page=paging(fails_at=PageUnavailable("token rejected", retryable=False)),
    )
    run_harvest(wired, INSTANCE)

    restarted = build(tmp_path, "alpha", now=lambda: NOW)
    failure = restarted.storage.harvest_failure(INSTANCE)
    assert failure is not None, "the failure did not outlive the process"
    assert "token rejected" in failure.reason
    assert failure.retryable is False
    assert restarted.storage.coverage_windows(INSTANCE) == []

    # The other half of the distinction: an empty harvest, read back the same
    # way, is not a failure. Without this the assertion above passes for a
    # reader that answers "failed" to everything. The freshly built connector
    # holds no rows and no injected transport, so this run is an empty `200`.
    run_harvest(restarted, INSTANCE)

    again = build(tmp_path, "alpha", now=lambda: NOW)
    assert again.storage.harvest_failure(INSTANCE) is None, (
        "a repaired connector must stop reading as broken, or ERROR is permanent"
    )
    assert again.storage.coverage_windows(INSTANCE) == []


def test_a_stored_failure_says_when_it_was_recorded(tmp_path):
    """`harvest_failures.at` was written from the first commit and read by nothing.

    Which made the age of a failure unknowable: "token rejected" recorded three
    minutes ago and the same sentence recorded in March are one row to a reader
    that cannot see the stamp, and Tier 2 is never rebuilt, so there is nowhere
    else to recover it from.

    The instant is the *storage* clock's, not the connector's — when a row was
    written is the single writer's fact (AD-5) — which is why the fixture gives
    the two different clocks and this asserts the storage one.
    """
    wired = daemon(
        tmp_path,
        now=Ticking(),
        fetch_page=paging(fails_at=PageUnavailable("token rejected", retryable=False)),
    )
    run_harvest(wired, INSTANCE)

    stored = wired.storage.harvest_failure(INSTANCE)
    assert stored is not None
    assert stored.at == NOW, "the storage clock, not the connector's ticking one"

    restarted = build(tmp_path, "alpha", now=lambda: NOW)
    assert restarted.storage.harvest_failure(INSTANCE).at == NOW, (  # type: ignore[union-attr]
        "the stamp is Tier 2 and has to outlive the process like the reason does"
    )

    # And a connector's own value carries no such claim: it does not know when
    # the writer will get to it, so it says nothing rather than guessing.
    assert HarvestFailure(reason="anything", retryable=False).at is None


def test_the_same_window_harvested_twice_is_stored_once(tmp_path):
    """Row: *same window harvested twice* — one window, not two.

    `save_cursor` inserted unconditionally and nothing constrained uniqueness, so
    a re-run over the same range accumulated identical rows and any count over
    `coverage_windows` measured re-runs rather than coverage.
    """
    wired = daemon(tmp_path, rows=ROWS)
    run_harvest(wired, INSTANCE)
    # Replay the same range through the public write path, exactly as a cursor
    # restore does.
    wired.storage.save_cursor(INSTANCE, Cursor(), None, None)
    second = run_harvest(wired, INSTANCE)

    assert (second.persisted, second.duplicates) == (0, 2)
    assert wired.storage.coverage_windows(INSTANCE) == [(NOW, NOW)]


def test_a_persist_that_refuses_discards_the_cursor_and_the_coverage_together(tmp_path):
    """Row: *persist raises after page one* — both, or neither.

    Stated rather than left to call ordering: `persist_events` is all-or-nothing,
    and `run_harvest` saves the cursor *after* it, so a refusal there leaves the
    cursor where it was and no window behind. The alternative — a cursor that
    advanced past events nobody stored — loses them silently.
    """
    wired = daemon(tmp_path, rows=ROWS)

    def refuse(*args, **kwargs):
        raise RuntimeError("the segment write refused")

    wired.storage.persist_events = refuse  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        run_harvest(wired, INSTANCE)

    assert wired.storage.load_cursor(INSTANCE) == Cursor()
    assert wired.storage.coverage_windows(INSTANCE) == []


# ── The type that carries the rule ───────────────────────────────────────────


def test_a_window_saved_under_a_disagreeing_instance_is_refused(tmp_path):
    """`save_cursor` keys coverage on `coverage.connector_instance`.

    The two must agree or the window is filed where nothing reads it — and then
    an absence assertion over the intended instance passes with a real row on
    disk, which is the failure mode that made this whole story's acceptance
    criteria untrustworthy.
    """
    wired = daemon(tmp_path, rows=ROWS)
    elsewhere = CoverageWindow("gitlab:beta", NOW, NOW)

    with pytest.raises(CoverageInstanceMismatch) as refused:
        wired.storage.save_cursor(INSTANCE, Cursor(b"1"), elsewhere, None)
    assert "gitlab:beta" in str(refused.value) and INSTANCE in str(refused.value)
    assert wired.storage.coverage_windows("gitlab:beta") == []


def test_a_refused_window_leaves_no_cursor_advance_for_a_later_commit_to_promote(tmp_path):
    """The other half of the refusal: it must not half-write.

    The refusal above used to fire *after* the cursor insert, on a shared
    connection with no `rollback`, so a refused `save_cursor` left an advance
    pending — invisible for as long as nothing else committed, and then durable
    the moment any unrelated write did. That trailing commit is what made the
    old bug visible, so it is part of this test rather than an afterthought:
    without it the assertion would pass against a store holding a pending
    advance that the next real harvest would promote.

    Both-or-neither, the same property the matrix states one method up for a
    persist that raises after page one.
    """
    wired = daemon(tmp_path, rows=ROWS)
    assert wired.storage.load_cursor(INSTANCE) == Cursor()
    elsewhere = CoverageWindow("gitlab:beta", NOW, NOW)

    with pytest.raises(CoverageInstanceMismatch):
        wired.storage.save_cursor(INSTANCE, Cursor(b"99"), elsewhere, None)

    assert wired.storage.load_cursor(INSTANCE) == Cursor(), (
        "the refused save left its cursor insert pending on the connection"
    )

    # An unrelated, entirely legitimate write, which commits — and would carry
    # the pending advance with it.
    wired.storage.save_cursor("gitlab:beta", Cursor(b"1"), None, None)

    assert wired.storage.load_cursor(INSTANCE) == Cursor(), (
        "a later commit promoted the cursor advance the refusal was supposed to "
        "have discarded"
    )
    assert wired.storage.load_cursor("gitlab:beta") == Cursor(b"1"), (
        "the rollback must discard the refused write and nothing else"
    )

    restarted = build(tmp_path, "alpha", now=lambda: NOW)
    assert restarted.storage.load_cursor(INSTANCE) == Cursor(), "and it did not outlive the process"
    assert restarted.storage.coverage_windows("gitlab:beta") == []


@pytest.mark.parametrize(
    "kwargs, complaint",
    [
        ({"outcome": HarvestOutcome.FAILED}, "disagree"),
        ({"outcome": HarvestOutcome.HARVESTED}, "no events"),
        (
            {"outcome": HarvestOutcome.EMPTY, "coverage": CoverageWindow(INSTANCE, NOW, NOW)},
            "returned no rows",
        ),
    ],
)
def test_harvest_result_refuses_an_outcome_its_fields_contradict(kwargs, complaint):
    """The outcome is not a decoration over the other fields.

    Chiefly the third row: coverage claimed by a harvest that returned no rows is
    the fabrication this story deletes, and a type that accepts it leaves the
    next connector free to reintroduce it.
    """
    with pytest.raises(ValueError) as refused:
        HarvestResult(events=(), cursor=Cursor(), **kwargs)
    assert complaint in str(refused.value)


def test_a_coverage_window_that_ends_before_it_began_is_refused():
    """A clock that steps backwards mid-fetch would otherwise store a reversed
    window, and nothing downstream reads one as suspect.

    The fold `evaluate_commitment`'s `covered` needs asks whether a union of
    windows spans a period; a window contributing a negative span is a hole
    nobody can see. `CalendarWindow` already refused the same shape one module
    over.

    `start == end` stays legal, and that is the half worth pinning: a fetch that
    began and finished inside one clock reading really did cover an instant, and
    it is the ordinary shape under a frozen clock — half this file's rows assert
    exactly it.
    """
    with pytest.raises(ValueError, match="before it began"):
        CoverageWindow(INSTANCE, NOW, NOW - TICK)
    assert CoverageWindow(INSTANCE, NOW, NOW).start == NOW


def test_a_clock_that_steps_backwards_mid_harvest_claims_no_coverage():
    """And `harvest` still reports rather than raising.

    The refusal above is a `ValueError` out of a constructor the connector calls
    *after* its loop, outside every handler — so a laptop waking, or an NTP
    correction, between page one and the finish would have turned a working
    harvest into an exception. No coverage is claimed instead: the fetch
    happened and there is no honest interval to say it happened in.
    """
    backwards = Ticking(step=-timedelta(hours=1))
    result = connector(now=backwards, _fake_api=list(ROWS)).harvest(Cursor())

    assert result.outcome is HarvestOutcome.HARVESTED
    assert len(result.events) == 2
    assert backwards.readings[-1] < backwards.readings[0], "the premise of the row"
    assert result.coverage is None


def test_save_cursors_signature_is_what_rejects_a_non_window(tmp_path):
    """The criterion: `uv run mypy` catches a caller passing the wrong thing.

    `coverage` was typed `object` and read through three `getattr` calls, so
    "accepts an absent window explicitly" was carried by no type — any object at
    all was accepted and a misspelled attribute silently became "no coverage".

    Both halves are checked. Asserting only that the bad call fails would pass on
    an unrelated error, and asserting only that the good call passes would hold
    against the `object` annotation this replaced.
    """
    if shutil.which("mypy") is None:
        pytest.fail(
            "mypy is not on PATH, so the signature contract did not run. It is a "
            "declared dev dependency, which makes its absence a broken "
            "environment rather than an optional feature — and a skip would "
            "report green while the rule went unchecked. Use `uv run pytest`."
        )

    preamble = (
        "from pm_ai.domain.harvest import Cursor\n"
        "from pm_ai.storage.service import StorageService\n\n\n"
        "def call(storage: StorageService) -> None:\n"
    )

    def check(name: str, call: str) -> subprocess.CompletedProcess[str]:
        workspace = tmp_path / name
        workspace.mkdir(parents=True)
        source = workspace / "caller.py"
        source.write_text(preamble + f"    {call}\n")
        return subprocess.run(
            ["mypy", "--no-incremental", "--cache-dir", str(workspace / "cache"), str(source)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    good = check("good", 'storage.save_cursor("gitlab:alpha", Cursor(), None, None)')
    assert good.returncode == 0, (
        "passing an absent window is the ordinary case and must type-check:"
        f"\n\n{good.stdout}\n{good.stderr}"
    )

    bad = check("bad", 'storage.save_cursor("gitlab:alpha", Cursor(), "a window", None)')
    assert bad.returncode != 0, (
        "mypy accepted a `str` where a coverage window belongs, so the signature "
        "is still tolerating anything"
    )
    assert "save_cursor" in bad.stdout and "CoverageWindow | None" in bad.stdout, (
        f"mypy failed for some other reason:\n\n{bad.stdout}\n{bad.stderr}"
    )

    # The same rule one parameter over, and the reason it is a rule: `failure`
    # had a `None` default, and passing `None` *deletes* the `harvest_failures`
    # row. So every three-argument call — a cursor restore, a replay, a caller
    # written before the parameter existed — silently reported a dead connector
    # as repaired, and `evaluate_commitment` read it as patience. Pinned here
    # because the omission is invisible at the call site: the code reads exactly
    # like a save that says nothing about the failure, and it is a save that
    # says the failure is over.
    silent = check("silent", 'storage.save_cursor("gitlab:alpha", Cursor(), None)')
    assert silent.returncode != 0, (
        "mypy accepted a save_cursor with no `failure` argument, which is a "
        "durable clear of the harvest_failures row that no call site says out "
        "loud"
    )
    assert "failure" in silent.stdout and "save_cursor" in silent.stdout, (
        f"mypy failed for some other reason:\n\n{silent.stdout}\n{silent.stderr}"
    )
