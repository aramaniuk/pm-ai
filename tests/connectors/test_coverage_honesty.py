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
from pm_ai.domain.harvest import Cursor, HarvestOutcome, HarvestResult
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
    # its clock once and subtracted an interval cannot produce this pair.
    assert clock.readings == [NOW, NOW + TICK]
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
    assert wired.storage.coverage_windows(INSTANCE) == [(NOW, NOW + TICK)]
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


def test_a_duplicate_row_across_pages_counts_its_span_once(tmp_path):
    """Row: *duplicate across pages* — deduped on the natural key, one window."""
    repeated = ROWS[0]
    wired = daemon(tmp_path, fetch_page=paging((repeated,), (repeated,)), now=Ticking())
    persisted = run_harvest(wired, INSTANCE)

    assert (persisted.persisted, persisted.duplicates) == (1, 1)
    assert wired.storage.coverage_windows(INSTANCE) == [(NOW, NOW + TICK)], (
        "the span is one window regardless of how many pages carried the row"
    )


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
    assert wired.storage.coverage_windows(INSTANCE) == [(NOW, NOW + TICK)]
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
    assert result.coverage == CoverageWindow(INSTANCE, NOW, NOW + TICK)
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
        raise TimeoutError("the socket gave up")

    result = connector(fetch_page=explode).harvest(Cursor())
    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None
    assert "TimeoutError" in result.failure.reason
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
    wired.storage.save_cursor(INSTANCE, Cursor(), None)
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
        wired.storage.save_cursor(INSTANCE, Cursor(b"1"), elsewhere)
    assert "gitlab:beta" in str(refused.value) and INSTANCE in str(refused.value)
    assert wired.storage.coverage_windows("gitlab:beta") == []


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

    good = check("good", 'storage.save_cursor("gitlab:alpha", Cursor(), None)')
    assert good.returncode == 0, (
        "passing an absent window is the ordinary case and must type-check:"
        f"\n\n{good.stdout}\n{good.stderr}"
    )

    bad = check("bad", 'storage.save_cursor("gitlab:alpha", Cursor(), "a window")')
    assert bad.returncode != 0, (
        "mypy accepted a `str` where a coverage window belongs, so the signature "
        "is still tolerating anything"
    )
    assert "save_cursor" in bad.stdout and "CoverageWindow | None" in bad.stdout, (
        f"mypy failed for some other reason:\n\n{bad.stdout}\n{bad.stderr}"
    )
