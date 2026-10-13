"""GitLab harvester — class H egress, read-only by construction (AD-1, AD-9).

One project's commits on its default branch, read through `client.py` (story
8n). Everything around the wire was already real — paging, the cursor, when
coverage may be claimed, what a commit becomes; until 8n the wire was a
placeholder, and a harvest walked an in-memory list that was empty on every
real machine. That list is gone: a default that walks a fake is the defect 8n
ended.

Story `8a` deleted the one thing here that was not a shape but a claim.
`harvest` reported a coverage window of `now() - 4h` to `now()`,
unconditionally, tied to nothing that proved a fetch had happened. Coverage is
derived from pages that actually came back, and its absence is expressible.

## The cursor is a commit date

GitLab lists a project's commits newest first, with no ascending order, so a
position in the list moves as commits arrive and cannot be a cursor. The cursor
is the newest plausible commit date seen on a **complete** walk, never past
now. A walk that did not finish moves it nothing: the pages not walked are the
older ones.

The next run asks from `MERGE_LAG_DAYS` before the cursor. A commit made on a
branch reaches the default branch only when merged, keeping the date it was
made, so asking from the cursor alone would miss it. What was already recorded
comes back and is dropped as a duplicate on its natural key (AD-34); a commit
merged more than thirty days after it was made is a stated loss.

## No credential, no socket

A connector with no token is not set up, which is not a fault: `check_health`
reports it ABSENT, `pm_ai.app.pipelines.run_harvest` does not harvest it, and a
harvest asked for directly returns a non-retryable failure without opening a
socket.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pm_ai.connectors.gitlab.client import (
    DEFAULT_HOST,
    FETCH_BUDGET,
    GitLabClient,
    GitLabRequest,
    GitLabResponse,
    Page,
    PageUnavailable,
    _does_not_wait,
    _urllib_transport,
    host_refusal,
    project_refusal,
    render_instant,
)
from pm_ai.domain.clocks import ImplausibleTimestamp, validate_occurred_at
from pm_ai.domain.events import CommitPayload, NormalizedEvent, ObservedEventType, Provenance
from pm_ai.domain.handles import EMAIL, NO_PERSON, usable_handle
from pm_ai.domain.harvest import (
    UNCLASSIFIED_FAULT_IS_RETRYABLE,
    Cursor,
    HarvestFailure,
    HarvestOutcome,
    HarvestResult,
    RowRefusal,
)
from pm_ai.domain.health import Health, Probe
from pm_ai.domain.identity import DataScope, MalformedReference, ScopeKind, SourceRef
from pm_ai.domain.lifecycle import CoverageWindow

__all__ = [
    "DEFAULT_HOST",
    "FETCH_BUDGET",
    "FIRST_RUN_REACH_BACK",
    "HOST_KEY",
    "MAX_REACH_BACK_MINUTES",
    "MERGE_LAG_DAYS",
    "PAGE_CAP",
    "PROJECT_KEY",
    "REACH_BACK_KEY",
    "SAMPLE_ROW",
    "CommitQuery",
    "GitLabConnectorAdapter",
    "Page",
    "PageUnavailable",
    "RowRefused",
    "host_refusal",
    "project_refusal",
]

# ── The row keys this connector reads ────────────────────────────────────────

HOST_KEY = "host"
"""The GitLab host, defaulting to `DEFAULT_HOST` — the one defaulted key."""

PROJECT_KEY = "project"
"""The GitLab project path, `group/project`. Never derived from the instance."""

REACH_BACK_KEY = "first_run_reach_back_minutes"
"""Graph's key, read the same way: whole minutes, one minute to ten years.
Optional here; absent, a first run reaches back `FIRST_RUN_REACH_BACK`."""

# ── The two reaches ──────────────────────────────────────────────────────────

MERGE_LAG_DAYS = 30
"""How far before the cursor each run asks from, in days.

A commit keeps the date it was made when its branch is merged, so a commit
merged after the cursor passed its date would never be asked for again. Thirty
days of re-asking covers the ordinary merge; what was already recorded is
dropped as a duplicate, and a commit merged later than this is a stated loss.
"""

MAX_REACH_BACK_MINUTES = 10 * 366 * 24 * 60
"""The far end of a first-run reach-back: ten years of minutes.

A commit history is not read back a decade on a first run, so a number past it
is a mistyped digit — and bounding it before it becomes a `timedelta` keeps the
arithmetic from overflowing. The same bound Graph sets on its own minute
settings, stated here for GitLab rather than borrowed.
"""

FIRST_RUN_REACH_BACK = timedelta(days=7)
"""How far back a run with no cursor asks. A row may override it with
`first_run_reach_back_minutes`."""

PAGE_CAP = 500
"""How many pages one `harvest` may walk before it refuses.

A loop whose continuation is a value the provider controls has to be bounded —
`graph/client.py`'s "What the response body is allowed to decide" is the
argument. At 100 rows a page this is 50,000 commits in one harvest.
"""

# The row `sample_events` maps, in GitLab's own shape. Fixed values, and mapped
# by the same code a harvest runs, so a sample cannot drift from what the
# connector really emits. The date is in the past and after
# `EARLIEST_PLAUSIBLE`, so it stays deterministic and plausible.
SAMPLE_ROW = {
    "id": "0" * 40,
    "author_email": "sample@example.invalid",
    "message": "a sample commit, mapped by the same code path as a real one",
    "committed_date": "2026-01-15T09:30:00.000+00:00",
}

_CURSOR_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class RowRefused(ValueError):
    """One provider row cannot be mapped, and the harvest continues without it.

    A `ValueError` because the provider's data is what is wrong, matching
    `ImplausibleTimestamp` one layer down.
    """


class _UnreadableCursor(ValueError):
    """A stored cursor this connector did not write."""


@dataclass(frozen=True, slots=True)
class CommitQuery:
    """What one page fetch asks for — the injected seam's single argument.

    A value rather than positional arguments so a test standing in for GitLab
    can record what was asked and assert the `since` a run used.

    `deadline` is when the run's budget ends, once the first page has come
    back; `None` before that, and the client measures its own.
    """

    since: datetime
    page: int
    deadline: datetime | None = None


@dataclass
class GitLabConnectorAdapter:
    instance: str
    """This connector's registry identity, handed in by composition.

    The enrolled instance name (`gitlab:alpha`), or `gitlab:<project id>` for
    the built-in each watched project gets. It used to be derived from the
    GitLab project path, so a row enrolled as `gitlab:alpha` for `acme/web`
    called itself `gitlab:acme/web` and every cursor save it made was refused
    as naming another connector. The registry key, the coverage window and the
    probe row all read this one value.
    """

    project: str
    """The GitLab project path, from the row's own `project` setting."""

    scope: DataScope
    """The pm-ai project this connector files into. Its id — never the GitLab
    path, which holds a `/` the reference grammar refuses — is the scope slot of
    every commit reference."""

    host: str = DEFAULT_HOST
    name: str = "gitlab"
    system: str = "gitlab"
    # Injected (AD-30), never read from the ambient environment: AD-35's coverage
    # windows are a fail-closed guard, and a guard you cannot test deterministically
    # is a guard you cannot trust.
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    # The enrolled personal access token, filled from the sealed store at
    # composition (`pm_ai.app.wiring._enrolled_connectors`). `None`, empty and
    # whitespace are one state: not set up.
    credential: str | None = None
    # The wire, replaced in tests by a recorded-answer callable; the real
    # `urllib` transport by default.
    transport: Callable[[GitLabRequest], GitLabResponse] = _urllib_transport
    # How a throttle hint is waited out. The default does not wait, by name, and
    # the client refuses rather than spinning on it; composition injects
    # `time.sleep`.
    wait: Callable[[float], None] = _does_not_wait
    # The page seam. `None` asks GitLab through `transport`; a test standing in
    # for GitLab at the page level injects one here. Returns a `Page` or raises
    # `PageUnavailable`; `harvest` converts the raise into a value.
    fetch_page: Callable[[CommitQuery], Page] | None = None
    first_run_reach_back: timedelta = FIRST_RUN_REACH_BACK
    # The two bounds on the page loop, injected so a test can reach them.
    page_cap: int = PAGE_CAP
    budget: timedelta = FETCH_BUDGET

    def __post_init__(self) -> None:
        longest = timedelta(minutes=MAX_REACH_BACK_MINUTES)
        if not timedelta(0) < self.first_run_reach_back <= longest:
            raise ValueError(
                f"{self.instance!r} was given a first-run reach-back of "
                f"{self.first_run_reach_back}, outside the one minute to ten "
                f"years a first run may ask from."
            )
        if self.scope.kind is not ScopeKind.PROJECT:
            raise ValueError(
                f"a GitLab connector files into one pm-ai project, and "
                f"{self.instance!r} was handed a {self.scope.kind.value} scope. "
                f"A commit reference names the project it belongs to (AD-34)."
            )

    @property
    def token(self) -> str:
        """The stored token with surrounding whitespace removed — what is sent.

        Stripped here so this and `has_credential` read one value: a token
        pasted with a trailing newline is the token, not a header the client
        must refuse. Empty when nothing usable is held.
        """
        return (self.credential or "").strip()

    @property
    def has_credential(self) -> bool:
        """Whether a usable token is held — anything but `None`, empty or blank."""
        return bool(self.token)

    def emits(self) -> frozenset[ObservedEventType]:
        """Only from the core taxonomy — a connector may not mint a type (AD-27)."""
        return frozenset({ObservedEventType.COMMIT_PUSHED})

    # ── Mapping ──────────────────────────────────────────────────────────────

    def _to_event(self, row: object) -> NormalizedEvent:
        """One GitLab commit row, mapped. The only place this connector builds an event.

        GitLab's shape: `id` is the sha, `message`, `author_email`, and
        `committed_date` — an ISO string, parsed here to an aware UTC instant.
        Raises `RowRefused` for a row it will not map; the caller records the
        refusal and keeps walking.
        """
        if not isinstance(row, Mapping):
            raise RowRefused(
                f"a GitLab commit list held a {type(row).__name__} rather than "
                f"a commit object."
            )
        sha = row.get("id")
        if not isinstance(sha, str) or not sha.strip():
            raise RowRefused(
                f"a GitLab commit row carries no usable `id` ({sha!r}), so "
                f"nothing downstream could key the commit on it or cite it "
                f"(AD-34). Refused rather than emitted under an invented "
                f"identifier."
            )
        sha = sha.strip()
        message = row.get("message")
        if not isinstance(message, str):
            raise RowRefused(
                f"GitLab commit {sha} carries no `message` string "
                f"({type(message).__name__}). Refused rather than emitted with "
                f"a substituted one: the message is the evidence a verifier "
                f"reads."
            )
        at = _commit_date(row.get("committed_date"), sha=sha)
        try:
            # AD-34 grammar — the pm-ai project id in the scope slot, never the
            # provider path, which holds a `/` the grammar refuses.
            reference = SourceRef.parse(f"gitlab:{self.scope.project_id}:commit:{sha}")
        except MalformedReference:
            raise RowRefused(
                f"GitLab commit {sha!r} cannot be cited: its id does not fit "
                f"the reference grammar (AD-34)."
            ) from None
        return NormalizedEvent(
            scope=self.scope,
            type=ObservedEventType.COMMIT_PUSHED,
            source_ref=reference,
            # AD-34 — what the provider said, as a person handle; a reader
            # decides who that is. An absent or unusable address is `none:`.
            actor=usable_handle(EMAIL, row.get("author_email")) or NO_PERSON,
            # Provider clock (AD-35); absent is a real state.
            occurred_at=at,
            payload=CommitPayload(sha=sha, message=message),
            # AD-36 — a connector may never assert `external`; normalization
            # decides, and this emits the fail-closed default.
            authored_by=Provenance.UNKNOWN,
            # no `id`: the storage service mints the surrogate (AD-34)
        )

    def sample_events(self) -> tuple[NormalizedEvent, ...]:
        """One representative event, built offline through the harvest mapping.

        Non-empty by construction: the AD-34 gate asserts over every event a
        connector samples, and an empty tuple would pass it vacuously.
        """
        return (self._to_event(dict(SAMPLE_ROW)),)

    # ── Health ───────────────────────────────────────────────────────────────

    def _client(self) -> GitLabClient:
        return GitLabClient(
            host=self.host,
            project=self.project,
            token=self.token,
            transport=self.transport,
            now=self.now,
            wait=self.wait,
            budget=self.budget,
        )

    def check_health(self) -> Probe:
        """Whether this instance has a token, and whether GitLab answers with it.

        Reports; never raises. ABSENT with no token — not set up yet, which is
        not a fault. Otherwise one commit of the project is asked for: OK on a
        2xx, FAILING with the status's reading otherwise. It asks for a commit
        rather than the signed-in user because a `read_user` token reads the
        user endpoints and nothing else, so a check on `/user` would pass a
        token every harvest fails with. The ten-second bound is the registry's;
        the socket's own timeout is thirty.
        """
        if not self.has_credential:
            return Probe(
                self.instance,
                Health.ABSENT,
                f"no usable credential is stored for {self.instance}, so it is "
                f"not set up yet",
                f"Enrol one with `pm-ai connector add gitlab {self.instance}`. "
                "Harvests are skipped until then, which is a setup step "
                "outstanding rather than a fault.",
            )
        try:
            self._client().check()
        except PageUnavailable as refused:
            return Probe(
                self.instance,
                Health.FAILING,
                f"{self.instance}: {refused.reason}",
                _remedy(refused, instance=self.instance),
            )
        except Exception as unexpected:  # noqa: BLE001 — a probe reports, never raises
            return Probe(
                self.instance,
                Health.FAILING,
                f"{self.instance}'s health check raised "
                f"{type(unexpected).__name__}, which pm-ai cannot classify",
                "Report it; this is a fault in pm-ai rather than an answer from GitLab.",
            )
        return Probe(
            self.instance,
            Health.OK,
            f"{self.host} answered for {self.project}",
        )

    # ── Harvest ──────────────────────────────────────────────────────────────

    def _asked_from(self, since: Cursor) -> tuple[datetime | None, datetime]:
        """The stored cursor read, and the instant this run asks from.

        The first is what a finished walk must not move the cursor behind:
        `None` on a first run, and `None` too for a stored date past this
        machine's now, so the walk that follows replaces it with a real one.

        Raises `_UnreadableCursor` for a token this connector did not write.
        """
        if not since.token:
            return None, self.now() - self.first_run_reach_back
        try:
            text = since.token.decode("ascii")
            stored = datetime.strptime(text, _CURSOR_FORMAT).replace(tzinfo=timezone.utc)
        except (UnicodeDecodeError, ValueError):
            if since.token.isdigit():
                raise _UnreadableCursor(
                    "it is a decimal page offset, the form this connector wrote "
                    "before it read a real GitLab"
                ) from None
            raise _UnreadableCursor("it is not a commit date this connector wrote") from None
        # A stored date past this machine's now — the clock was moved back —
        # is read as now: asking from a future instant would ask for nothing.
        now = self.now()
        if stored > now:
            return None, now - timedelta(days=MERGE_LAG_DAYS)
        return stored, stored - timedelta(days=MERGE_LAG_DAYS)

    def _fetch_from_gitlab(self) -> Callable[[CommitQuery], Page]:
        """The default page fetch: one client for the run, asking GitLab."""
        client = self._client()

        def fetch(query: CommitQuery) -> Page:
            return client.commits(query.since, query.page, deadline=query.deadline)

        return fetch

    def harvest(self, since: Cursor) -> HarvestResult:
        """Walk pages from the cursor, and report what actually happened (AD-9, AD-35).

        Reports; never raises on a transport failure. A page-one-succeeded,
        page-two-failed fetch hands back page one's events, page one's real
        coverage *and* the failure.

        The coverage window exists only when a page came back and either it
        held nothing or one of its kept rows can be placed in time; its `start`
        is this connector's clock when the first page returned and its `end`
        the clock when fetching stopped. The cursor moves only on a walk that
        finished — see the module docstring.

        The loop is bounded twice, by `page_cap` and by `budget`.
        """
        if not self.has_credential:
            return _failed(
                since,
                f"{self.instance} has no usable credential, so it is not set up "
                f"and nothing was asked. Enrol one with `pm-ai connector add "
                f"gitlab {self.instance}`.",
            )
        try:
            stored, asked_from = self._asked_from(since)
        except _UnreadableCursor as unreadable:
            # The cursor itself is never quoted: it is opaque provider state.
            return _failed(
                since,
                f"{self.instance} was handed a stored cursor of "
                f"{len(since.token)} byte(s) that it cannot read: {unreadable}. "
                f"Nothing was fetched and the cursor is unchanged. Waiting will "
                f"not change this, and no pm-ai command clears a stored cursor "
                f"yet — that is recorded as deferred work — so this connector "
                f"refuses every run until one exists.",
            )
        fetch = self.fetch_page or self._fetch_from_gitlab()

        page_number = 1
        rows: list[object] = []
        # The instant the *first* page came back, and `None` for as long as none
        # has. It separates "the provider answered" from "we asked".
        reached_at: datetime | None = None
        failure: HarvestFailure | None = None
        walked = 0

        while True:
            query = CommitQuery(
                since=asked_from,
                page=page_number,
                deadline=None if reached_at is None else reached_at + self.budget,
            )
            try:
                page = fetch(query)
            except PageUnavailable as refused:
                failure = HarvestFailure(
                    reason=(
                        f"{self.instance} could not fetch page {page_number} of "
                        f"{self.project!r}'s commits: {refused.reason}"
                    ),
                    retryable=refused.retryable,
                    retry_after=refused.retry_after,
                )
                break
            except Exception as unexpected:  # noqa: BLE001 — a failure is a value here
                # The exception's **type** and never its text: a transport's
                # message is where a URL or a header would appear.
                failure = HarvestFailure(
                    reason=(
                        f"{self.instance} could not fetch page {page_number}: "
                        f"the transport raised {type(unexpected).__name__}, "
                        f"which pm-ai cannot classify"
                    ),
                    retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
                )
                break

            if not isinstance(page, Page):
                failure = HarvestFailure(
                    reason=(
                        f"{self.instance}'s transport answered with "
                        f"{type(page).__name__} rather than a Page while "
                        f"fetching page {page_number}. That is a wiring fault in "
                        f"pm-ai rather than a provider fault."
                    ),
                    retryable=False,
                )
                break

            if reached_at is None:
                reached_at = self.now()
            rows.extend(page.rows)
            walked += 1
            if page.next_page is None:
                break
            if page.next_page != page_number + 1:
                # GitLab's offset paging always answers `page + 1`. A value
                # that does not advance would loop forever, and one that jumps
                # would skip the pages between and still read as a complete
                # walk — coverage over rows nobody fetched.
                failure = HarvestFailure(
                    reason=(
                        f"{self.instance} was told the page after {page_number} "
                        f"is {page.next_page}, which "
                        + (
                            "does not advance — walking it again would never end"
                            if page.next_page <= page_number
                            else "skips the pages between; the walk is not "
                            "reported complete"
                        )
                    ),
                    retryable=False,
                )
                break
            if walked >= self.page_cap:
                failure = HarvestFailure(
                    reason=(
                        f"{self.instance} reached the {self.page_cap}-page cap "
                        f"with page {page.next_page} still offered. What was "
                        f"walked keeps its coverage and the cursor does not move."
                    ),
                    retryable=True,
                )
                break
            if self.now() - reached_at >= self.budget:
                failure = HarvestFailure(
                    reason=(
                        f"{self.instance} spent its {self.budget} fetch budget "
                        f"after {walked} page(s), with page {page.next_page} "
                        f"still offered. The rest is the next run's."
                    ),
                    retryable=True,
                )
                break
            page_number = page.next_page

        finished_at = self.now()
        # Mapped one row at a time: a row that cannot be read must not discard
        # the rows beside it, the coverage this fetch earned, or the outcome.
        mapped: list[NormalizedEvent] = []
        refusals: list[RowRefusal] = []
        for row in rows:
            try:
                mapped.append(self._to_event(row))
            except RowRefused as refused:
                sha = row.get("id") if isinstance(row, Mapping) else None
                refusals.append(
                    RowRefusal(
                        identifier=sha.strip() if isinstance(sha, str) and sha.strip() else None,
                        reason=str(refused),
                    )
                )
        events = tuple(mapped)

        outcome = HarvestOutcome.of(
            mapped=bool(events), refused=bool(refusals), failed=failure is not None
        )

        # Kept rows that can be placed in time, by the provider's clock. They
        # decide whether a window may be claimed and, on a finished walk, where
        # the cursor goes; an implausible date does neither.
        placed = _plausible_dates(events, now=finished_at)
        coverage: CoverageWindow | None = None
        if (
            (outcome is HarvestOutcome.EMPTY or placed)
            and reached_at is not None
            # This machine's clock stepping backwards mid-harvest leaves no
            # honest interval, and `CoverageWindow` refuses a reversed one.
            and finished_at >= reached_at
        ):
            coverage = CoverageWindow(
                connector_instance=self.instance,
                start=reached_at,
                end=finished_at,
            )

        return HarvestResult(
            events=events,
            cursor=_advanced(since, stored, placed, now=finished_at)
            if failure is None
            else since,
            outcome=outcome,
            coverage=coverage,
            failure=failure,
            refusals=tuple(refusals),
            # The reading the window's start was taken from (`8p`).
            answered_at=reached_at,
        )


# ── Helpers ──────────────────────────────────────────────────────────────────


def _commit_date(value: object, *, sha: str) -> datetime | None:
    """`committed_date` as an aware UTC instant, `None` when absent, or a refusal."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise RowRefused(
            f"GitLab commit {sha} carries a `committed_date` of type "
            f"{type(value).__name__} rather than an ISO date string."
        )
    try:
        parsed = datetime.fromisoformat(value.strip())
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("no offset")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise RowRefused(
            f"GitLab commit {sha} carries a `committed_date` that is not an "
            f"ISO date with an offset, so it cannot be placed in time. "
            f"Refused rather than guessed."
        ) from None


def _plausible_dates(events: tuple[NormalizedEvent, ...], *, now: datetime) -> list[datetime]:
    """The kept events' provider dates that AD-35 would believe."""
    placed: list[datetime] = []
    for event in events:
        if event.occurred_at is None:
            continue
        try:
            validate_occurred_at(event.occurred_at, now=now)
        except ImplausibleTimestamp:
            continue
        placed.append(event.occurred_at)
    return placed


def _advanced(
    since: Cursor, stored: datetime | None, placed: list[datetime], *, now: datetime
) -> Cursor:
    """The cursor after a finished walk: the newest plausible date, never past now.

    Never earlier than the stored one — a re-asked span that no longer holds
    the commit that set it must not send the next run further back — and
    `since` itself when nothing moved it, so "unchanged" is identity.
    """
    if not placed:
        return since
    newest = min(max(placed), now).replace(microsecond=0)
    if stored is not None and newest <= stored:
        return since
    return Cursor(render_instant(newest).encode("ascii"))


def _failed(since: Cursor, reason: str) -> HarvestResult:
    """A run that asked nothing: failed, not retryable, cursor unchanged."""
    return HarvestResult(
        events=(),
        cursor=since,
        outcome=HarvestOutcome.FAILED,
        failure=HarvestFailure(reason=reason, retryable=False),
    )


def _remedy(refused: PageUnavailable, *, instance: str) -> str:
    """What to do about a failed health check, by the status that failed it."""
    status = refused.status
    if status is None and not refused.retryable:
        # Refused before or around the request, not by GitLab: a host, project
        # path or token value that cannot be sent, or a redirect.
        return (
            f"Fix the `host` or `project` in connectors/{instance}.json, or "
            f"enrol the token again with `pm-ai connector add gitlab "
            f"{instance}`; the network is not the problem."
        )
    if status == 401:
        return (
            f"Create a new personal access token and enrol it with "
            f"`pm-ai connector add gitlab {instance}`."
        )
    if status == 403:
        return "Give the token the `read_api` scope, or enrol one that has it."
    if status == 404:
        return (
            f"Check the `project` and `host` in connectors/{instance}.json, and "
            f"that the token's account can see that project."
        )
    if status == 429:
        return "GitLab is rate-limiting this machine; check again later."
    return (
        "Check that this machine can reach the GitLab host and that the row's "
        "settings are right. An unreachable connector is indistinguishable from "
        "a sleeping laptop in the coverage windows (AD-35), so it is reported here."
    )
