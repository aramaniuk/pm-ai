"""Stories 8a and 8i — the coverage-honesty matrix, row by row.

The defect this file exists over: `GitLabConnectorAdapter.harvest` built its
`CoverageWindow` as `now() - 4h` to `now()`, unconditionally, because
`HarvestResult` *required* one. A provider declining with an empty `200`
therefore recorded four hours of harvest coverage, and AD-35's fail-closed guard
reads a covered window as "absence of evidence is evidence of absence" — the one
reading that may fire an irreversible nudge.

**8a's repair overshot, and 8i corrected it** — which inverted this file's
central row, so the two stories are read together here rather than one of them
being quietly overwritten. 8a required that *something came back* as the proof a
fetch had happened. That is a stand-in for the real fact, and the two come apart
in exactly one case: the provider answering "nothing here", which is the
ordinary case on a quiet project. So no window was ever recorded there, AD-35's
guard was never armed, and `BROKEN` was unreachable — a verifier that could only
deliver good news. 8i records the window whenever the check completed, and keeps
8a's three exclusions for the runs that did not check: a failed fetch, rows with
no clock pm-ai can trust, and rows it refused wholesale.

Three properties carry most of the weight here, and all are asserted positively
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

**A window says pm-ai looked, not that pm-ai was running.** The 8i rows assert
both directions: an instant inside a recorded check is covered, and an instant
*between* two of them is not. Checks are discrete, the gaps between them are
real, and a fold that let adjacent windows merge would hand `BROKEN` the seconds
nobody watched.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from gitlab_fakes import TOKEN, Pages, commit

from pm_ai.app.pipelines import run_harvest
from pm_ai.app.wiring import build
from pm_ai.connectors.gitlab import (
    FETCH_BUDGET,
    FIRST_RUN_REACH_BACK,
    MERGE_LAG_DAYS,
    CommitQuery,
    GitLabConnectorAdapter,
    Page,
    PageUnavailable,
)
from pm_ai.domain.harvest import (
    Cursor,
    HarvestFailure,
    HarvestOutcome,
    HarvestResult,
    RowRefusal,
)
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.lifecycle import CommitmentState, CoverageWindow, evaluate_commitment
from pm_ai.domain.meetings import Meeting
from pm_ai.storage.service import CoverageInstanceMismatch

NOW = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
SCOPE = DataScope(ScopeKind.PROJECT, "alpha")
INSTANCE = "gitlab:alpha"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

TICK = timedelta(seconds=1)

MEETING = Meeting(
    meeting_id="AAMk-one",
    title="a meeting a harvest mapped",
    start=NOW - timedelta(hours=1),
    duration_minutes=30,
    attendees=(),
    scope=DataScope(ScopeKind.PERSONAL),
)
"""One mapped meeting, for the two rows of the table below that need evidence.

Story 33c's `records` and `live` are the second and third things a harvest can
come back holding, and the outcome has to stay honest about them exactly as it
does about events.
"""

ROWS = [
    commit("9f2a1c", "Fix auth refactor", at=NOW - timedelta(hours=2)),
    commit("3b7e02", "Add Redis benchmark", at=NOW - timedelta(hours=1)),
]

NEWEST = Cursor(b"2026-08-19T08:00:00Z")
"""The cursor a finished walk over `ROWS` leaves: the newer commit's date."""


class Ticking:
    """A clock that moves, so `start` and `end` can be told apart.

    A frozen clock makes an honest window a point, which is fine for the daemon
    fixture and useless here: the rule under test is that `start` is the instant
    the *first* page came back and `end` the instant fetching stopped, and a
    constant satisfies any pair of instants. Every reading is recorded, so a row
    can assert which reading a bound came from rather than that it looks recent.

    On a first run the harvest's first reading is the one it asks from (now −
    the first-run reach-back), so the first page's reading is the second.
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
    kwargs.setdefault("credential", TOKEN)
    return GitLabConnectorAdapter(instance=INSTANCE, project="alpha", scope=SCOPE, **kwargs)


def paging(*pages: tuple[dict, ...], fails_at: PageUnavailable | None = None) -> Pages:
    """A page fetch handing back exactly these pages, then optionally refusing.

    Injected at the page seam, because the row that matters most is the one
    where a *later* page fails — which no arrangement of rows can express.
    """
    return Pages(*pages, fails_at=fails_at)


def daemon(tmp_path, *, rows=None, now=None, fetch_page=None):
    """A wired daemon whose GitLab connector is the one under test.

    The built-in holds no token, so it is given one, and `rows` become one page
    unless a fetch is supplied. Storage keeps the frozen `NOW` — its clock names
    event-log segments and stamps `ingested_at` — while the connector gets
    whatever clock the row needs. Two clocks on purpose: coverage is the
    *connector's* reading, and sharing one would let a row pass because storage
    happened to agree.
    """
    shutil.rmtree(tmp_path, ignore_errors=True)
    wired = build(tmp_path, "alpha", now=lambda: NOW)
    gitlab = wired.connectors[INSTANCE]
    gitlab.credential = TOKEN
    gitlab.fetch_page = fetch_page if fetch_page is not None else paging(tuple(rows or ()))
    if now is not None:
        gitlab.now = now
    return wired


# ── Rows that returned something ─────────────────────────────────────────────


def test_two_pages_bound_the_window_with_the_connectors_own_clock():
    """Row: *fetch returns rows* — coverage spans first page back to fetch end."""
    clock = Ticking()
    result = connector(now=clock, fetch_page=paging((ROWS[0],), (ROWS[1],))).harvest(Cursor())

    assert result.outcome is HarvestOutcome.HARVESTED
    assert len(result.events) == 2
    assert result.failure is None
    assert result.coverage == CoverageWindow(
        connector_instance=INSTANCE,
        start=clock.readings[1],
        end=clock.readings[-1],
    )
    # Named, not merely "distinct": the first reading is where a first run
    # asks from, the second is taken when page one comes back, the third is the
    # wall-clock budget check before page two, and the last when the walk
    # finishes — so a connector that read its clock once and subtracted an
    # interval cannot produce this pair.
    assert clock.readings == [NOW, NOW + TICK, NOW + 2 * TICK, NOW + 3 * TICK]
    assert result.cursor == NEWEST, "the newest commit date the walk saw"
    assert result.answered_at == clock.readings[1], (
        "page one's reading is the instant, not the budget check after it (8p)"
    )


def test_two_pages_read_back_exactly_one_window_bounded_by_the_fetch(tmp_path):
    """The same row, asserted against storage rather than the return value.

    A positive bound assertion through `coverage_windows`, which is what the
    sweeper reads. Absence here proves nothing — the fabricated window was filed
    under its own `connector_instance` and this call already returned `[]` for a
    mismatched key.
    """
    clock = Ticking()
    wired = daemon(tmp_path, fetch_page=paging((ROWS[0],), (ROWS[1],)), now=clock)
    persisted = run_harvest(wired, INSTANCE)

    assert persisted.persisted == 2
    assert wired.storage.coverage_windows(INSTANCE) == [(NOW + TICK, NOW + 3 * TICK)]
    assert wired.storage.harvest_failure(INSTANCE) is None
    assert wired.storage.load_cursor(INSTANCE) == NEWEST


def test_rows_whose_clocks_cannot_be_believed_claim_no_coverage(tmp_path):
    """Row: *rows returned, no usable clock* — harvested, and no window claimed.

    One row has no `committed_date` at all and the other carries the zero-value
    parse (`EARLIEST_PLAUSIBLE` exists for exactly that). Something arrived and
    nothing says when, and an unknowable start is not a guessable one — so the
    events persist and the coverage claim does not. Nor does the cursor move:
    no date here may set it.
    """
    unplaceable = [
        commit("aaaaaa", "no clock", at=None, email="a@example.com"),
        commit("bbbbbb", "epoch", at=datetime(1970, 1, 1, tzinfo=timezone.utc)),
    ]
    result = connector(fetch_page=paging(tuple(unplaceable))).harvest(Cursor())
    assert result.outcome is HarvestOutcome.HARVESTED
    assert len(result.events) == 2
    assert result.coverage is None
    assert result.cursor == Cursor()

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
    emitted_but_unplaceable = commit(
        "aaaaaa", "from the future", at=datetime(2126, 9, 7, 12, 0, tzinfo=timezone.utc)
    )
    refused_but_well_dated = commit("bbbbbb", None, at=NOW)  # refused: no message
    rows = (emitted_but_unplaceable, refused_but_well_dated)

    result = connector(fetch_page=paging(rows)).harvest(Cursor())

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
    rows = (commit("cccccc", "readable", at=NOW), commit("dddddd", None, at=NOW))

    result = connector(fetch_page=paging(rows)).harvest(Cursor())

    assert len(result.events) == 1
    assert len(result.refusals) == 1
    assert result.coverage == CoverageWindow(
        connector_instance=INSTANCE, start=NOW, end=NOW
    )


def test_a_cursor_advances_even_when_no_coverage_was_earned(tmp_path):
    """Row: *cursor with no coverage* — the cursor is saved, the window is not.

    `save_cursor` takes `CoverageWindow | None` by signature, so `None` travels
    through as itself instead of being read off an object through `getattr` and
    silently becoming "no coverage" for any reason at all. Reached here by this
    machine's clock stepping back a second per reading: the walk finished, so
    the cursor moves to the newest commit date, and there is no honest interval
    to call the window.
    """
    wired = daemon(tmp_path, rows=ROWS, now=Ticking(step=-TICK))
    assert wired.storage.load_cursor(INSTANCE) == Cursor()

    run_harvest(wired, INSTANCE)
    assert wired.storage.load_cursor(INSTANCE) == NEWEST, "the cursor did not advance"
    assert wired.storage.coverage_windows(INSTANCE) == []


def test_the_next_page_header_decides_what_is_asked_and_nothing_else():
    """GitLab's offset paging always answers `page + 1`, and nothing else is walked.

    A provider jumping from page 1 to page 5 would skip pages 2 to 4 and still
    read as a complete walk — coverage claimed over rows nobody fetched. Refused,
    not retryable, as a page that does not advance is; page 1 keeps its events
    and the cursor does not move.
    """
    pages = {
        1: Page(rows=(ROWS[0], ROWS[1]), next_page=5),
        5: Page(rows=(), next_page=None),
    }
    asked: list[CommitQuery] = []

    def fetch(query: CommitQuery) -> Page:
        asked.append(query)
        return pages[query.page]

    result = connector(now=Ticking(), fetch_page=fetch).harvest(Cursor())

    assert [query.page for query in asked] == [1], "page 5 was asked for"
    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None and result.failure.retryable is False
    assert "skips" in result.failure.reason
    assert len(result.events) == 2
    assert result.cursor == Cursor(), "a walk that skipped pages is not complete"


def test_a_page_loop_is_bounded_by_a_cap_and_keeps_what_it_walked():
    """The bound "the next page must advance" does not give.

    A provider advancing one page at a time satisfies that condition forever,
    so the walk is capped, and the cap is a refusal rather than a quiet stop: a
    silent stop would report a harvest as complete.
    """
    fetch = paging((ROWS[0],), (ROWS[1],))
    result = connector(now=Ticking(), fetch_page=fetch, page_cap=1).harvest(Cursor())

    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None and "cap" in result.failure.reason
    assert [event.payload.sha for event in result.events] == [ROWS[0]["id"]]
    assert result.coverage is not None, "the page that was walked still covers"
    assert result.cursor == Cursor(), (
        "a walk that did not finish moves the cursor nothing: GitLab lists "
        "newest first, so the pages not walked are the older ones"
    )


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
    assert result.cursor == Cursor()


def test_the_budget_deadline_travels_to_every_page_after_the_first():
    """The client honours a throttle hint only when it fits what is left, so it
    has to be told what is left: the first page measures its own, and every
    later page carries the deadline the first page's reading set.
    """
    fetch = paging((ROWS[0],), (ROWS[1],))
    clock = Ticking()
    connector(now=clock, fetch_page=fetch).harvest(Cursor())

    assert [query.deadline for query in fetch.asked] == [None, clock.readings[1] + FETCH_BUDGET]


def test_a_transport_that_answers_with_something_other_than_a_page_is_a_value():
    """The check `GraphClient._send` applies to its own injected transport.

    `fetch_page` is a seam. A callable that answers `None` raised
    `AttributeError` on `page.rows`, *outside* every handler in the loop — so
    page one's events, page one's real coverage and the outcome went up the
    stack together, which is the partial-page row's whole prohibition.
    """
    pages: dict[int, object] = {1: Page(rows=(ROWS[0],), next_page=2), 2: None}
    result = connector(now=Ticking(), fetch_page=lambda query: pages[query.page]).harvest(Cursor())  # type: ignore[arg-type,return-value]

    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None
    assert "NoneType" in result.failure.reason and "Page" in result.failure.reason
    assert result.failure.retryable is False, "a wiring fault in pm-ai does not clear by waiting"
    assert [event.payload.sha for event in result.events] == [ROWS[0]["id"]]
    assert result.coverage == CoverageWindow(INSTANCE, NOW + TICK, NOW + 3 * TICK)


def test_a_stored_cursor_this_connector_did_not_write_is_a_failure_value():
    """A cursor is opaque to everything but its own connector, which cuts both
    ways: a token written by another connector is not a position here, and
    `harvest` promises a value for every outcome rather than a raise.
    """
    stale = Cursor(b"page-2-token")
    fetch = paging(tuple(ROWS))
    result = connector(fetch_page=fetch).harvest(stale)

    assert result.outcome is HarvestOutcome.FAILED
    assert result.events == ()
    assert result.coverage is None
    assert result.cursor is stale, "nothing may advance past rows nobody fetched"
    assert result.failure is not None and result.failure.retryable is False
    assert "page-2-token" not in result.failure.reason, (
        "a cursor is opaque provider state and this reason is written to Tier 2"
    )
    assert fetch.asked == [], "nothing was asked"


def test_the_old_decimal_cursor_is_refused_by_name():
    """Row: *old cursor* — the offset the in-memory stub wrote is not a date.

    Refused, named, and left as it was: there is no position in a newest-first
    list that a decimal offset could still mean.
    """
    old = Cursor(b"7")
    fetch = paging(tuple(ROWS))
    result = connector(fetch_page=fetch).harvest(old)

    assert result.outcome is HarvestOutcome.FAILED
    assert result.cursor is old
    assert result.failure is not None and result.failure.retryable is False
    assert "decimal" in result.failure.reason
    assert fetch.asked == []


def test_a_duplicate_row_across_pages_counts_its_span_once(tmp_path):
    """Row: *duplicate across pages* — deduped on the natural key, one window."""
    repeated = ROWS[0]
    wired = daemon(tmp_path, fetch_page=paging((repeated,), (repeated,)), now=Ticking())
    persisted = run_harvest(wired, INSTANCE)

    assert (persisted.persisted, persisted.duplicates) == (1, 1)
    assert wired.storage.coverage_windows(INSTANCE) == [(NOW + TICK, NOW + 3 * TICK)], (
        "the span is one window regardless of how many pages carried the row"
    )


# ── The cursor is a commit date (story 8n) ───────────────────────────────────


def test_one_page_of_three_commits_sets_the_cursor_to_the_newest(tmp_path):
    """Row: *one page* — three events, the cursor is the newest date, one window."""
    rows = [
        commit("a1", "one", at=NOW - timedelta(hours=3)),
        commit("a3", "three", at=NOW - timedelta(minutes=30)),
        commit("a2", "two", at=NOW - timedelta(hours=2)),
    ]
    wired = daemon(tmp_path, rows=rows)
    persisted = run_harvest(wired, INSTANCE)

    assert persisted.persisted == 3
    assert wired.storage.load_cursor(INSTANCE) == Cursor(b"2026-08-19T08:30:00Z")
    assert wired.storage.coverage_windows(INSTANCE) == [(NOW, NOW)]


def test_an_empty_answer_leaves_the_cursor_where_it_was():
    """Row: *empty* — no events, a window, and the cursor unchanged."""
    result = connector(fetch_page=paging(())).harvest(NEWEST)

    assert result.outcome is HarvestOutcome.EMPTY
    assert result.coverage == CoverageWindow(INSTANCE, NOW, NOW)
    assert result.cursor is NEWEST


def test_the_next_run_asks_from_thirty_days_before_the_cursor(tmp_path):
    """Row: *merge lag* — asked from T − 30 days; re-seen commits are duplicates.

    A commit keeps the date it was made when its branch is merged, so asking
    from the cursor alone would miss a commit merged after the cursor passed
    its date. Re-asking thirty days brings back what was already recorded, and
    the natural key drops it.
    """
    wired = daemon(tmp_path, rows=ROWS)
    run_harvest(wired, INSTANCE)
    assert wired.storage.load_cursor(INSTANCE) == NEWEST

    again = paging(tuple(ROWS))
    wired.connectors[INSTANCE].fetch_page = again
    second = run_harvest(wired, INSTANCE)

    assert again.since == [datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)]
    assert (second.persisted, second.duplicates) == (0, 2)
    assert wired.storage.load_cursor(INSTANCE) == NEWEST


def test_a_stored_cursor_in_the_future_is_read_as_now():
    """The clock was moved back: a cursor past now would ask for nothing, so it
    is read as now before the merge lag is subtracted, and the next finished
    walk sets it to the newest date it saw."""
    future = Cursor(b"2027-01-01T00:00:00Z")
    fetch = paging(tuple(ROWS))
    result = connector(fetch_page=fetch).harvest(future)

    assert fetch.since == [NOW - timedelta(days=30)]
    assert result.cursor == NEWEST


def test_a_token_is_stripped_and_a_blank_one_is_no_credential():
    """`has_credential` and what is sent read one value: surrounding whitespace
    is not part of the token, and a token of only whitespace is none at all."""
    padded = connector(credential=f"  {TOKEN}\n", fetch_page=paging(()))
    assert padded.has_credential and padded.token == TOKEN
    assert padded.harvest(Cursor()).outcome is HarvestOutcome.EMPTY

    fetch = paging(())
    blank = connector(credential=" \t\n", fetch_page=fetch)
    assert not blank.has_credential
    assert blank.harvest(Cursor()).outcome is HarvestOutcome.FAILED
    assert fetch.asked == []


@pytest.mark.parametrize("reach_back", [timedelta(0), timedelta(minutes=-5), timedelta(days=3700)])
def test_the_adapter_refuses_a_reach_back_outside_its_bounds(reach_back):
    with pytest.raises(ValueError, match="reach-back"):
        connector(first_run_reach_back=reach_back)


def test_a_bug_in_mapping_is_not_recorded_as_a_provider_row_fault(monkeypatch):
    """Only `RowRefused` is a refused row. Any other error out of the mapping is
    pm-ai's, and it is not filed against GitLab's data."""
    adapter = connector(fetch_page=paging((ROWS[0],)))

    def broken(row):
        raise ValueError("a pm-ai bug")

    monkeypatch.setattr(adapter, "_to_event", broken)
    with pytest.raises(ValueError, match="a pm-ai bug"):
        adapter.harvest(Cursor())


def test_a_cursor_never_moves_backwards():
    """A re-asked span that no longer holds the commit that set the cursor must
    not send the next run further back than the last one."""
    stored = Cursor(b"2026-08-19T08:30:00Z")
    result = connector(fetch_page=paging((ROWS[0],))).harvest(stored)

    assert result.cursor is stored


def test_a_future_dated_commit_is_flagged_and_never_sets_the_cursor(tmp_path):
    """Row: *future date* — flagged at persist, and the cursor is the newest
    plausible date, never tomorrow."""
    rows = [
        commit("f1", "from tomorrow", at=NOW + timedelta(days=1)),
        commit("f2", "an hour ago", at=NOW - timedelta(hours=1)),
    ]
    wired = daemon(tmp_path, rows=rows)
    persisted = run_harvest(wired, INSTANCE)

    assert (persisted.persisted, persisted.flagged) == (2, 1)
    assert wired.storage.load_cursor(INSTANCE) == NEWEST


def test_a_cursor_within_the_skew_tolerance_is_never_past_now():
    """A date a few minutes ahead is plausible (AD-35's skew tolerance), and the
    cursor still stops at this machine's now."""
    ahead = commit("s1", "slightly ahead", at=NOW + timedelta(minutes=2))
    result = connector(fetch_page=paging((ahead,))).harvest(Cursor())

    assert result.cursor == Cursor(b"2026-08-19T09:00:00Z")


def test_a_first_run_reaches_back_thirty_days_or_the_rows_override():
    """Row: *first run* — now − 30 days, the span every later run asks from
    before its cursor (story 8s), or the override the row sets."""
    assert FIRST_RUN_REACH_BACK == timedelta(days=MERGE_LAG_DAYS) == timedelta(days=30)

    default = paging(())
    connector(fetch_page=default).harvest(Cursor())
    assert default.since == [NOW - timedelta(days=30)]

    overridden = paging(())
    connector(fetch_page=overridden, first_run_reach_back=timedelta(minutes=90)).harvest(Cursor())
    assert overridden.since == [NOW - timedelta(minutes=90)]


def test_the_second_run_asks_from_thirty_days_before_the_newest_commit_date():
    """Story 8s, finding 19: the cursor is the newest commit's date D, not the
    first run's time T, so the second run asks from D − 30 days — and what it
    adds beyond the first run's range is D − 30 days up to T − 30 days."""
    first_run_at = NOW
    newest = NOW - timedelta(days=3, hours=4)
    first = paging((commit("d1", "the newest", at=newest),))
    result = connector(fetch_page=first).harvest(Cursor())
    assert first.since == [first_run_at - timedelta(days=30)]
    assert result.cursor == Cursor(b"2026-08-16T05:00:00Z")

    second = paging(())
    later = first_run_at + timedelta(hours=4)
    connector(fetch_page=second, now=lambda: later).harvest(result.cursor)
    assert second.since == [newest - timedelta(days=30)]
    added = first.since[0] - second.since[0]
    assert added == first_run_at - newest, "what is new is D − 30 days up to T − 30 days, a span T − D long"


def test_a_hand_set_short_reach_back_still_back_fills_at_the_second_run():
    """Row: *GitLab short key by hand* — one day first, then 30 days before the
    cursor, so the second run back-fills up to 29 days, as it always did."""
    first = paging((commit("d1", "today", at=NOW - timedelta(hours=1)),))
    result = connector(fetch_page=first, first_run_reach_back=timedelta(minutes=1440)).harvest(
        Cursor()
    )
    assert first.since == [NOW - timedelta(days=1)]

    second = paging(())
    connector(fetch_page=second, first_run_reach_back=timedelta(minutes=1440)).harvest(
        result.cursor
    )
    assert second.since == [NOW - timedelta(hours=1) - timedelta(days=30)]


def test_page_two_failing_twice_asks_from_the_same_instant_both_times(tmp_path):
    """Acceptance: a walk that did not finish moves the cursor nothing, so the
    run after it asks exactly what this one asked."""
    wired = daemon(tmp_path, rows=ROWS)
    run_harvest(wired, INSTANCE)

    failing = paging((ROWS[0],), fails_at=PageUnavailable("GitLab answered 503", retryable=True))
    wired.connectors[INSTANCE].fetch_page = failing
    run_harvest(wired, INSTANCE)
    run_harvest(wired, INSTANCE)

    first, _, second, _ = failing.asked
    assert (first.page, second.page) == (1, 1)
    assert first.since == second.since == datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)
    assert wired.storage.load_cursor(INSTANCE) == NEWEST


def test_a_connector_with_no_credential_asks_nothing():
    """Row: *adapter, no credential* — no socket, a failure, not retryable."""
    fetch = paging(tuple(ROWS))
    result = connector(credential=None, fetch_page=fetch).harvest(NEWEST)

    assert fetch.asked == []
    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None and result.failure.retryable is False
    assert "not set up" in result.failure.reason
    assert result.coverage is None
    assert result.cursor is NEWEST


def test_a_built_in_with_no_credential_is_not_harvested_and_records_nothing(tmp_path):
    """Row: *built-in, no credential* — nothing asked, nothing recorded.

    Not set up is not a fault, so no failure row may say otherwise — and no
    window may say pm-ai looked.
    """
    wired = build(tmp_path, "alpha", now=lambda: NOW)
    builtin = wired.connectors[INSTANCE]
    fetch = paging(tuple(ROWS))
    builtin.fetch_page = fetch
    assert builtin.credential is None

    persisted = run_harvest(wired, INSTANCE)

    assert fetch.asked == []
    assert (persisted.persisted, persisted.duplicates) == (0, 0)
    assert wired.storage.load_cursor(INSTANCE) == Cursor()
    assert wired.storage.coverage_windows(INSTANCE) == []
    assert wired.storage.harvest_failure(INSTANCE) is None
    probe = builtin.check_health()
    assert probe.health.value == "absent" and "not set up yet" in probe.detail


# ── Rows that came back and could not be read ────────────────────────────────
#
# Not a matrix row of its own, and the reason it belongs in this file anyway:
# the matrix's *partial-page* row says a page that arrived keeps its events and
# its earned coverage even when something else went wrong. `_to_event`
# subscripted its fields and the mapping ran outside every `try` in `harvest`
# — so one row missing one field raised out of the method and took page one's
# events, page one's real coverage and the outcome with it, one field lower than
# the row that forbids exactly that.

UNREADABLE = commit(
    "3b7e02",
    # Not a string, which is the one shape `_to_event` refuses outright: the
    # message is the evidence a verifier reads, and an invented empty body reads
    # as a commit that said nothing.
    None,
    at=NOW - timedelta(hours=1),
)


def test_one_unreadable_row_costs_that_row_and_nothing_beside_it(tmp_path):
    """The good row, the refusal, and the coverage — all three in one value.

    The coverage is the half that carries the weight. A raise out of the mapping
    discarded a window this fetch had genuinely earned, and a window discarded
    is indistinguishable, later, from a window never earned: AD-35's fail-closed
    guard reads both as "we never looked".
    """
    result = connector(fetch_page=paging((ROWS[0], UNREADABLE))).harvest(Cursor())

    assert result.outcome is HarvestOutcome.HARVESTED
    assert len(result.events) == 1
    assert result.events[0].payload.sha == ROWS[0]["id"]
    (refusal,) = result.refusals
    assert refusal.identifier == UNREADABLE["id"], (
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


def test_a_committed_date_that_will_not_parse_refuses_that_row_alone():
    """Row: *unparseable date* — that row refused by sha, the rest kept.

    Everything downstream compares the provider clock as an aware datetime, so
    a date that cannot be read is refused by name rather than passed on.
    """
    unreadable = commit("5c1d4e", "dated in words", at="yesterday")
    naive = commit("6d2e5f", "no offset", at="2026-08-19T07:00:00")
    result = connector(fetch_page=paging((ROWS[0], unreadable, naive))).harvest(Cursor())

    assert result.outcome is HarvestOutcome.HARVESTED
    assert [event.payload.sha for event in result.events] == [ROWS[0]["id"]]
    assert [refusal.identifier for refusal in result.refusals] == ["5c1d4e", "6d2e5f"]
    assert all("committed_date" in refusal.reason for refusal in result.refusals)
    assert result.coverage == CoverageWindow(INSTANCE, NOW, NOW)


def test_gitlabs_date_string_becomes_an_aware_utc_instant():
    """GitLab's `committed_date` carries the committer's offset and milliseconds."""
    row = commit("7e3f60", "east of Greenwich", at="2026-08-19T10:30:00.000+03:00")
    (event,) = connector(fetch_page=paging((row,))).harvest(Cursor()).events

    assert event.occurred_at == datetime(2026, 8, 19, 7, 30, tzinfo=timezone.utc)
    assert event.occurred_at.utcoffset() == timedelta(0)


def test_a_refused_row_does_not_hold_the_cursor_back(tmp_path):
    """A refusal is a decision about the row, not a reason to come back for it:
    the cursor is the kept rows' newest date, as though the refused one was not
    there."""
    wired = daemon(tmp_path, rows=[dict(ROWS[0]), dict(UNREADABLE)])
    run_harvest(wired, INSTANCE)
    assert wired.storage.load_cursor(INSTANCE) == Cursor(b"2026-08-19T07:00:00Z")


def test_a_page_whose_every_row_is_refused_is_harvested_not_empty():
    """Rows arrived and pm-ai could not read them, which is the one of the two
    silences that needs somebody to look — so it cannot collapse into EMPTY.
    """
    result = connector(fetch_page=paging((UNREADABLE,))).harvest(Cursor())

    assert result.outcome is HarvestOutcome.HARVESTED
    assert result.events == ()
    assert len(result.refusals) == 1
    assert result.coverage is None, "no event survived, so there is nothing to cover"


def test_a_row_that_is_not_an_object_is_refused_not_raised():
    """A commit list holding something other than commit objects costs that item."""
    result = connector(fetch_page=paging((ROWS[0], "not a commit"))).harvest(Cursor())  # type: ignore[arg-type]

    assert len(result.events) == 1
    (refusal,) = result.refusals
    assert refusal.identifier is None and "str" in refusal.reason


# ── Rows that returned nothing ───────────────────────────────────────────────


def test_an_empty_200_is_ran_and_learned_nothing_and_says_it_ran(tmp_path):
    """Row: *provider returns empty 200* — no rows, no error, **and a window**.

    Inverted by `8i`, and the inversion is the story. `8a` required that
    *something came back* as the proof a fetch had happened, which is right
    almost everywhere and wrong in exactly one case: the provider answering
    "nothing here", which is the ordinary answer on a quiet project. So the
    guard failed precisely where it mattered — a quiet project accumulated no
    coverage, AD-35's fail-closed reading was never armed over it, and `BROKEN`
    was unreachable there permanently.

    What the earlier row was really defending is asserted below rather than
    dropped: the bounds are the two clock readings either side of the request,
    one tick apart, and not `now() - 4h` to `now()`. A fabricated window and a
    measured one are told apart by their *provenance*, which is what the
    `Ticking` clock makes visible — not by whether any row came back.
    """
    clock = Ticking()
    result = connector(now=clock, fetch_page=paging(())).harvest(Cursor())
    assert result.outcome is HarvestOutcome.EMPTY
    assert result.events == ()
    assert result.failure is None
    assert result.coverage == CoverageWindow(INSTANCE, NOW + TICK, NOW + 2 * TICK), (
        "the window is the two readings either side of the request"
    )
    assert clock.readings == [NOW, NOW + TICK, NOW + 2 * TICK], (
        "where a first run asks from, then the two readings around the fetch"
    )
    assert result.answered_at == clock.readings[1], (
        "the instant the empty page was read, and the window starts there (8p)"
    )

    ticking = Ticking()
    wired = daemon(tmp_path, rows=[], now=ticking)
    run_harvest(wired, INSTANCE)
    assert wired.storage.coverage_windows(INSTANCE) == [(NOW + TICK, NOW + 2 * TICK)], (
        "a provider that answered with nothing was still asked, and the record "
        "of having asked survives the write"
    )


def test_a_quiet_project_accumulates_the_coverage_that_makes_broken_reachable(tmp_path):
    """Many checks, all finding nothing — and each one leaves a record.

    The property the bug removed, asserted on the far side of storage because
    that is where story 16's sweeper will read it. Every one of these harvests
    is EMPTY, so before `8i` this loop stored nothing at all and
    `evaluate_commitment`'s `covered` could only ever be `False` here: a quiet
    project could report `FULFILLED` and never `BROKEN`, which is a verifier
    that only delivers good news.

    The fold is written out rather than imported: the reader that decides a
    promise is broken is story 16 and is not built. What this pins is its
    input — a due instant that falls inside one of the recorded checks is
    enclosed by a window, and the verdict that follows is then reachable.

    **What this does not prove.** Real windows are the milliseconds a request
    took, hours apart, and a due instant will almost always fall in a gap —
    which the negative case below pins deliberately. Deciding that a whole
    *period* was watched needs a union with a gap tolerance, which
    `lifecycle.py` already says and story 16 owns.
    """
    ticking = Ticking()
    wired = daemon(tmp_path, rows=[], now=ticking)
    for _ in range(3):
        run_harvest(wired, INSTANCE)

    # Three readings a run: where a first run asks from (an empty answer moves
    # no cursor, so every run is a first run), and the two around the fetch.
    windows = wired.storage.coverage_windows(INSTANCE)
    assert windows == [
        (NOW + TICK, NOW + 2 * TICK),
        (NOW + 4 * TICK, NOW + 5 * TICK),
        (NOW + 7 * TICK, NOW + 8 * TICK),
    ], "three checks that found nothing are three records of having checked"

    def covers(instant: datetime) -> bool:
        return any(start <= instant <= end for start, end in windows)

    def verdict(instant: datetime) -> CommitmentState:
        return evaluate_commitment(
            overdue=True,
            evidence_admissible=False,
            covered=covers(instant),
            harvest_failed=False,
        )

    inside = NOW + 4 * TICK
    assert covers(inside), "the instant a T-dated event would have arrived in is checked"
    assert verdict(inside) is CommitmentState.BROKEN, (
        "which is the whole point: on a quiet project, BROKEN is reachable at all"
    )

    # The other half, and the half that must not move. Checks are discrete: the
    # second between two of them is a second nobody looked, and a reading that
    # let adjacent windows merge across it would make a laptop that was closed
    # between two harvests indistinguishable from a promise nobody kept. FR-26
    # nudges are irreversible, so this gap fails closed.
    between = NOW + 2 * TICK + timedelta(milliseconds=500)
    assert not covers(between), "an instant between two checks is not a checked instant"
    assert verdict(between) is CommitmentState.UNKNOWN, (
        "8i widens what counts as having looked; it does not widen when"
    )


def test_an_empty_first_page_and_a_failed_second_claims_nothing_and_still_returns():
    """The conjunct that keeps `8i` from turning a partial walk into a crash.

    `EMPTY` is what earns a window over no rows, and `EMPTY` requires that
    nothing failed. Drop that requirement and this fetch builds `FAILED` +
    coverage + nothing mapped, which `HarvestResult.__post_init__` refuses with
    a `ValueError` — raised from `harvest`, which the port documents as
    reporting rather than raising.
    """

    def empty_then_gone(query: CommitQuery) -> Page:
        if query.page == 1:
            return Page(rows=(), next_page=2)
        raise PageUnavailable("the provider went away", retryable=True)

    clock = Ticking()
    result = connector(now=clock, fetch_page=empty_then_gone).harvest(Cursor())

    assert result.outcome is HarvestOutcome.FAILED
    assert result.answered_at == clock.readings[1], (
        "page one was read, so the provider answered — the failure on page two "
        "does not undo when (8p)"
    )
    assert result.events == ()
    assert result.failure is not None
    assert "went away" in result.failure.reason
    assert result.coverage is None, (
        "the first page answered with nothing, but the walk did not finish — "
        "'I looked and there was nothing' is a claim only a completed check "
        "may make"
    )


# ── Rows that failed ─────────────────────────────────────────────────────────


def test_a_5xx_is_a_value_and_moves_nothing(tmp_path):
    """Row: *provider 5xx* — failure outcome, no coverage, cursor unmoved.

    That this test can `assert` at all is half the row: `harvest` returns rather
    than raising, because a raise on page two would take page one's coverage with
    it.
    """
    since = NEWEST
    refused = PageUnavailable("GitLab answered 502", retryable=True)
    result = connector(fetch_page=paging(fails_at=refused)).harvest(since)

    assert result.outcome is HarvestOutcome.FAILED
    assert result.events == ()
    assert result.coverage is None
    assert result.cursor is since, "the cursor must be the one handed in, unchanged"
    assert result.answered_at is None, "no page was read, so there is no instant"
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
    rather than a raise. The cursor does not move: the pages not walked are the
    older ones, so the next run asks from the same instant.
    """
    clock = Ticking()
    fetch = paging((ROWS[0],), fails_at=PageUnavailable("page two refused", retryable=False))
    result = connector(now=clock, fetch_page=fetch).harvest(Cursor())

    assert result.outcome is HarvestOutcome.FAILED
    assert len(result.events) == 1
    assert result.coverage == CoverageWindow(INSTANCE, clock.readings[1], clock.readings[-1])
    assert result.answered_at == clock.readings[1], "page one's reading, not a later one"
    assert result.failure is not None and "page two refused" in result.failure.reason
    assert result.cursor == Cursor(), "the cursor moved on a walk that did not finish"

    wired = daemon(tmp_path, now=Ticking(), fetch_page=fetch)
    persisted = run_harvest(wired, INSTANCE)
    assert persisted.persisted == 1
    assert wired.storage.coverage_windows(INSTANCE) == [(NOW + TICK, NOW + 3 * TICK)]
    assert wired.storage.load_cursor(INSTANCE) == Cursor()
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
    assert result.coverage == CoverageWindow(INSTANCE, NOW + TICK, NOW + 3 * TICK)
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

    def explode(query: CommitQuery) -> Page:
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
    """Row: *next page does not advance* — reported, not walked again.

    A next-page header repeating the page just fetched would re-harvest the
    same rows forever. Reported as a failure so the page already walked keeps
    its coverage.
    """
    result = connector(
        now=Ticking(), fetch_page=lambda query: Page(rows=(ROWS[0],), next_page=query.page)
    ).harvest(Cursor())

    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None and "does not advance" in result.failure.reason
    assert result.failure.retryable is False
    assert len(result.events) == 1
    assert result.coverage == CoverageWindow(INSTANCE, NOW + TICK, NOW + 2 * TICK)
    assert result.cursor == Cursor()


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
    # is given a token and an empty page, so this run is an empty `200`.
    restarted.connectors[INSTANCE].credential = TOKEN
    restarted.connectors[INSTANCE].fetch_page = paging(())
    run_harvest(restarted, INSTANCE)

    again = build(tmp_path, "alpha", now=lambda: NOW)
    assert again.storage.harvest_failure(INSTANCE) is None, (
        "a repaired connector must stop reading as broken, or ERROR is permanent"
    )
    # And the repaired run left a window behind, where it used to leave nothing
    # (`8i`). Under the frozen clock this build is handed, the fetch begins and
    # ends inside one reading — a real instant, and the shape half this file
    # asserts. The two silences stay distinguishable: the failed run above wrote
    # a failure and no window, this one a window and no failure.
    assert again.storage.coverage_windows(INSTANCE) == [(NOW, NOW)]


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
            {
                "outcome": HarvestOutcome.FAILED,
                "failure": HarvestFailure(reason="could not look", retryable=True),
                "coverage": CoverageWindow(INSTANCE, NOW, NOW),
            },
            "mapped nothing",
        ),
        (
            {
                "outcome": HarvestOutcome.HARVESTED,
                "refusals": (RowRefusal(identifier="9f2a1c", reason="no clock"),),
                "coverage": CoverageWindow(INSTANCE, NOW, NOW),
            },
            "mapped nothing",
        ),
        (
            {"outcome": HarvestOutcome.EMPTY, "records": (MEETING,)},
            "did not learn nothing",
        ),
        (
            {"outcome": HarvestOutcome.EMPTY, "live": (MEETING,)},
            "did not learn nothing",
        ),
    ],
)
def test_harvest_result_refuses_an_outcome_its_fields_contradict(kwargs, complaint):
    """The outcome is not a decoration over the other fields.

    Chiefly the middle two rows: coverage claimed by a harvest that returned no
    rows **and did not complete** is the fabrication `8a` deleted, and a type
    that accepts it leaves the next connector free to reintroduce it. The two
    that can reach the rule are the two that are not proof of having looked — a
    FAILED fetch, which could not look, and a HARVESTED one holding only
    refusals, which looked and could read none of what it got, which is pm-ai's
    own defect rather than a quiet project.

    `EMPTY` with a window is deliberately **not** here: it was, until `8i`
    found that it was the case the guard got wrong. It is the outcome reached
    only by a request that completed and was told "nothing here", so it is the
    one no-rows case that really did check, and a quiet project's whole
    coverage is made of it.

    The last two rows are story 33c's carriers. `records` and `live` are the
    other two ways a harvest can hold evidence, and EMPTY is the value AD-35's
    fail-closed reading treats as "the provider answered and there was nothing
    there" — so a window that mapped a meeting and reported EMPTY would arm that
    reading against its own evidence. Added because the refusal shipped with its
    three siblings pinned here and itself untested.
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
    result = connector(now=backwards, fetch_page=paging(tuple(ROWS))).harvest(Cursor())

    assert result.outcome is HarvestOutcome.HARVESTED
    assert len(result.events) == 2
    assert backwards.readings[-1] < backwards.readings[0], "the premise of the row"
    assert result.coverage is None
    assert result.answered_at == backwards.readings[1], (
        "the window is withheld, but the first page was still read at this "
        "instant, and that fact stays (8p)"
    )


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
