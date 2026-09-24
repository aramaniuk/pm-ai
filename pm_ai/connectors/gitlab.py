"""GitLab harvester — class H egress, read-only by construction (AD-1, AD-9).

The HTTP call is stubbed for the slice; everything around it is the real shape a
connector must have: one method, no scheduling, no id minting, no writes.

Story `8a` deleted the one thing here that was not a shape but a claim. `harvest`
reported a coverage window of `now() - 4h` to `now()`, unconditionally, tied to
nothing that proved a fetch had happened — so a provider declining with an empty
`200` recorded four hours of harvest coverage, and AD-35's fail-closed guard read
that as evidence we had looked. Coverage is now derived from pages that actually
came back, and its absence is expressible.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from pm_ai.domain.clocks import ImplausibleTimestamp, validate_occurred_at
from pm_ai.domain.events import CommitPayload, NormalizedEvent, ObservedEventType, Provenance
from pm_ai.domain.harvest import (
    UNCLASSIFIED_FAULT_IS_RETRYABLE,
    Cursor,
    HarvestFailure,
    HarvestOutcome,
    HarvestResult,
    RowRefusal,
)
from pm_ai.domain.health import Health, Probe
from pm_ai.domain.identity import DataScope, SourceRef, resolve_actor
from pm_ai.domain.lifecycle import CoverageWindow

# The row `sample_events` maps. Fixed values, because the point of a sample is
# that two runs produce the same shape — and it goes through the *same* mapping
# `harvest` uses, so a sample cannot drift from what the connector really emits.
SAMPLE_ROW = {
    "sha": "0" * 40,
    "author_email": "sample@example.invalid",
    "message": "a sample commit, mapped by the same code path as a real one",
    # A real provider clock, not `None`. The sample is the one event the AD-27
    # and AD-34 gates inspect, and with no `occurred_at` the AD-35 path — a
    # provider timestamp, and its plausibility — was the one thing the fixture
    # they read could not represent. Fixed and in the past, so it stays
    # deterministic and stays plausible: after `EARLIEST_PLAUSIBLE`, and never
    # inside the five-minute future skew tolerance.
    "committed_at": datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc),
}


@dataclass(frozen=True, slots=True)
class Page:
    """One provider response: the rows it carried, and where the next one starts.

    `next_offset` is `None` on the last page, stated rather than inferred from a
    short one. "Fewer rows than asked for" is a heuristic, and a provider that
    happens to fill its final page would send the walk round again — which is
    also the difference between a window bounded by pages actually walked and a
    window bounded by a guess.
    """

    rows: tuple[dict, ...] = ()
    next_offset: int | None = None


PAGE_CAP = 500
"""How many pages one `harvest` may walk before it refuses.

The bound `GraphClient.walk` applies, for the reason stated in that module's
docstring — a loop whose continuation is a value the provider controls has to be
bounded, and "the next offset must increase" is not a bound: a provider advancing
one row per page satisfies it forever. Not restated here; `graph/client.py`'s
"What the response body is allowed to decide" is the argument.

The number differs from Graph's because the page size does. At the default 100
rows a page this is 50,000 commits in one harvest, far more than any cycle asks
for and small enough that a misbehaving provider cannot page until the budget
runs out.
"""

FETCH_BUDGET = timedelta(minutes=10)
"""How long one whole harvest may keep asking for pages, wall-clock.

`GraphClient.budget`'s value and `GraphClient.budget`'s reasoning: chosen against
CAP-2's 240-minute cycle, so a fetch cannot outlive its own cycle and overlap the
next. It bounds the walk in the dimension the page cap does not — a provider
answering slowly rather than endlessly.

Measured from the instant the **first page came back**, not from the top of the
call. The first request is always sent, because a harvest that refused before
asking anything would report a failure it never attempted; what the budget bounds
is how long the walk may keep going after that.
"""


class PageUnavailable(Exception):
    """What a transport raises when a page cannot be fetched.

    The retry semantics ride on the exception rather than inside its message,
    because `harvest` converts this into a `HarvestFailure` and "wait 90 seconds"
    (a 429 with `Retry-After`) is a different instruction to an operator than
    "this token is dead".

    Never escapes `harvest`. A raise cannot carry the coverage a partial fetch
    already earned, which is exactly why the outcome is a value.

    `reason` must carry no credential material. It becomes a `HarvestFailure`,
    which is persisted in Tier 2 and never rebuilt, so a transport that pasted a
    signed URL or an authorization header into its message would put a token in a
    durable row and in every report that reads one.
    """

    def __init__(
        self, reason: str, *, retryable: bool, retry_after: timedelta | None = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.retry_after = retry_after


class RowRefused(ValueError):
    """One provider row cannot be mapped, and the harvest continues without it.

    The GitLab twin of `graph.calendar.CalendarRowRefused`, and it exists for
    the reason that one does: `_to_event` subscripted `row["sha"]`,
    `row["message"]` and `row["author_email"]`, and the mapping ran *outside*
    every `try` in `harvest` — so one row missing one field raised a `KeyError`
    out of the method and discarded page one's events, page one's real coverage
    and the outcome, which is exactly what this connector's own docstring and
    `8a`'s partial-page row forbid.

    A `ValueError` because the provider's data is what is wrong, matching
    `ImplausibleTimestamp` one layer down.
    """


def _stubbed_reach() -> str:
    """The placeholder transport, named so a verdict can recognise it.

    A lambda could not be compared by identity, which is what `check_health`
    needs to tell "GitLab answered" apart from "nothing was asked".
    """
    return "gitlab (stubbed transport) answered"


@dataclass
class GitLabConnectorAdapter:
    project: str
    scope: DataScope
    name: str = "gitlab"
    system: str = "gitlab"
    # Injected (AD-30), never read from the ambient environment: AD-35's coverage
    # windows are a fail-closed guard, and a guard you cannot test deterministically
    # is a guard you cannot trust.
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    _fake_api: list[dict] = field(default_factory=list)
    # Whether a token has been enrolled for this instance. `None` is the
    # first-run state `check_health` reports as ABSENT — a machine nobody has
    # finished setting up, which is not a broken one. Story `8b` seals the
    # credential and story `33a` reads it back: `pm_ai.app.wiring
    # ._enrolled_connectors` fills this from the sealed store at composition,
    # which is the only layer that may open both `core` and `storage`. Between
    # the two slices this stayed `None` on every real machine, and a connector
    # enrolled ten seconds earlier reported ABSENT.
    credential: str | None = None
    # Injected exactly as `_fake_api` is, and for the same reason: the HTTP call
    # is stubbed for this slice, and a health probe that reached the network
    # could not be exercised against the failures it exists to report — a
    # provider that refuses, and one that never answers at all.
    reach: Callable[[], str] = _stubbed_reach
    # How many rows one page carries. Injected for the same reason `now` is: the
    # coverage window's `start` is the instant the *first* page came back, and a
    # connector that can only ever fetch one page cannot be tested against the
    # partial-page failure that rule exists for.
    page_size: int = 100
    # The transport seam, alongside `reach`. `None` walks `_fake_api` in
    # `page_size` slices; a real GitLab client — and a test standing in for one
    # that refuses on page two — is injected here. Returns a `Page` or raises
    # `PageUnavailable`; `harvest` converts the raise into a value.
    fetch_page: Callable[[int], Page] | None = None
    # The two bounds on the page loop, injected for the reason `now` is: a guard
    # that cannot be exercised deterministically is a guard nobody can trust,
    # and no test is going to walk five hundred pages to reach a cap.
    page_cap: int = PAGE_CAP
    budget: timedelta = FETCH_BUDGET

    def emits(self) -> frozenset[ObservedEventType]:
        """Only from the core taxonomy — a connector may not mint a type (AD-27)."""
        return frozenset({ObservedEventType.COMMIT_PUSHED})

    @property
    def instance(self) -> str:
        """This connector's registry identity: the system, plus what it covers.

        `name` is `"gitlab"` on every instance, so it cannot tell two projects
        apart. The same string `CoverageWindow.connector_instance` already
        carries, spelled once so the registry key, the coverage window and the
        probe row cannot drift.
        """
        return f"gitlab:{self.project}"

    def _to_event(self, row: dict) -> NormalizedEvent:
        """One provider row, mapped. The only place this connector builds an event.

        `sample_events` goes through here too, so the sample an architecture gate
        inspects is built by the code a real harvest runs rather than beside it.

        Raises `RowRefused` for a row it will not map, never a `KeyError`: the
        caller records the refusal and keeps walking. Three fields are read and
        each absence has a different right answer, so each is stated rather than
        subscripted.
        """
        sha = row.get("sha")
        if not isinstance(sha, str) or not sha.strip():
            raise RowRefused(
                f"a gitlab row carries no usable `sha` ({sha!r}), so nothing "
                f"downstream could key the commit on it or cite it (AD-34). "
                f"Refused rather than emitted under an invented identifier."
            )
        sha = sha.strip()
        message = row.get("message")
        if not isinstance(message, str):
            raise RowRefused(
                f"gitlab commit {sha} carries no `message` string "
                f"({type(message).__name__}). Refused rather than emitted with "
                f"a substituted one: the message is the evidence a verifier "
                f"reads, and an invented empty body reads as a commit that said "
                f"nothing."
            )
        at = row.get("committed_at")
        if at is not None and not isinstance(at, datetime):
            # The shape real GitLab sends — `committed_at` as an ISO string.
            # Refused here, naming the gap, because everything downstream
            # compares it as a datetime: `validate_occurred_at` reaches
            # `_assert_comparable`, which asks a `str` for `.tzinfo` and raises
            # `AttributeError` — out of the coverage check, out of `harvest`,
            # and out of `persist_events`, none of which catch it. This
            # connector's transport is still stubbed and does not parse provider
            # timestamps; until it does, a string clock is a mapping gap that
            # refuses one row rather than an exception that ends the harvest.
            raise RowRefused(
                f"gitlab commit {sha} carries a `committed_at` of type "
                f"{type(at).__name__} rather than a datetime. Refused rather "
                f"than passed on: every clock comparison downstream (AD-35) "
                f"expects an aware datetime, and this connector's transport "
                f"does not parse provider timestamps yet."
            )
        return NormalizedEvent(
            scope=self.scope,
            type=ObservedEventType.COMMIT_PUSHED,
            # AD-34 grammar — not a URL, so it joins across connectors
            source_ref=SourceRef.parse(f"gitlab:{self.project}:commit:{sha}"),
            # AD-34 — a native handle resolves to an Actor or to UNRESOLVED,
            # never to itself. `.get`, and no refusal: an absent handle is what
            # `UNRESOLVED` is *for*, and a commit whose author pm-ai cannot name
            # is still a commit that happened.
            actor=resolve_actor(system="gitlab", handle=row.get("author_email")),
            # Provider clock (AD-35), and absent is a real state:
            # `NormalizedEvent.occurred_at` is `datetime | None` and a row with
            # no timestamp is a row we cannot place in time, not a KeyError.
            occurred_at=at,
            payload=CommitPayload(sha=sha, message=message),
            # AD-36 — a connector may NEVER assert `external`. It cannot see
            # the executed-mutation ledger, so it cannot know whether this is
            # pm-ai's own write coming back. Normalization decides; this
            # emits the fail-closed default. Hard-coding EXTERNAL here made
            # our own comments admissible as evidence that our own promises
            # were kept.
            authored_by=Provenance.UNKNOWN,
            # no `id`: the storage service mints the surrogate (AD-34)
        )

    def sample_events(self) -> tuple[NormalizedEvent, ...]:
        """One representative event, built offline through the harvest mapping.

        Non-empty by construction. The AD-34 gate asserts over every event a
        connector samples, so an empty tuple would pass it without executing a
        single assertion — the vacuous shape that gate spent its whole life in
        while the module it imports did not exist.
        """
        return (self._to_event(dict(SAMPLE_ROW)),)

    def check_health(self) -> Probe:
        """Whether this instance has a credential, and whether GitLab answers.

        Reports; never raises. `ABSENT` before a token is enrolled, because a
        fresh install is not a broken machine; `FAILING` when the provider
        refused or could not be reached, because a dead connector reads forever
        as "no coverage yet" (AD-39) unless something says otherwise.

        The ten-second bound is not enforced here. This call cannot cancel
        itself, so `ConnectorRegistry.check_health` bounds the *wait* and
        abandons the attempt — see its docstring.
        """
        # `None`, empty and whitespace are one state: no usable credential.
        # A blank string is what a half-finished enrolment leaves behind, and
        # reporting it as configured would say setup is done when it is not.
        if not (self.credential or "").strip():
            return Probe(
                self.instance,
                Health.ABSENT,
                f"no usable credential is stored for {self.instance}",
                f"Enrol one with `pm-ai connector add gitlab {self.instance}`. "
                "Harvests are skipped until then, which is a setup step "
                "outstanding rather than a fault.",
            )
        try:
            answer = self.reach()
        except Exception as unreachable:  # noqa: BLE001 — a probe reports, never raises
            return Probe(
                self.instance,
                Health.FAILING,
                f"{self.instance} did not answer: {unreachable!r}",
                "Check the token has not expired and that this machine can "
                "reach the GitLab host. An unreachable connector is "
                "indistinguishable from a sleeping laptop in the coverage "
                "windows (AD-35), so it has to be reported here.",
            )
        if self.reach is _stubbed_reach:
            # `OK` is a claim that this machine reached GitLab. The default
            # transport is a stub that returns a string without opening a
            # socket, so reporting `OK` from it would put a reachability
            # verdict in the report that nothing measured. `8b` wires the real
            # transport; until then the honest answer is that it is untested.
            return Probe(
                self.instance,
                Health.WARNING,
                f"{self.instance} has a credential, but its transport is still "
                f"a stub — reachability has not been tested",
                "Nothing to fix on this machine. The GitLab connector has no "
                "real HTTP transport yet — story 33a brought Microsoft Graph's "
                "and not this one's — so until it does, this row reports what is "
                "configured rather than what answered.",
            )
        return Probe(self.instance, Health.OK, f"{answer}")

    def _fetch_from_fake_api(self, offset: int) -> Page:
        """The default transport: `_fake_api`, walked in `page_size` slices.

        Paginated rather than sliced-to-the-end so the one shape that matters —
        several pages, of which a later one can fail — is expressible against the
        stub. The whole coverage rule is about which pages came back.
        """
        rows = tuple(self._fake_api[offset : offset + self.page_size])
        walked = offset + len(rows)
        return Page(rows=rows, next_offset=walked if walked < len(self._fake_api) else None)

    def _bounded_by_a_credible_clock(self, rows: list[dict], *, now: datetime) -> bool:
        """Whether any returned row can be placed in time at all (AD-35).

        This does **not** supply the window's bounds — those are this
        connector's own clock, because coverage is expressed in `ingested_at`
        and a bound lifted off a provider row would be the mixed-clock defect
        AD-35 forbids. What the rows decide is whether there is any coverage to
        claim: a page of rows whose timestamps are all absent or all
        unbelievable tells us something arrived and nothing about when, and an
        unknowable start is not a guessable one.

        Flagging, not rejecting: the events still persist, and this only
        withholds the coverage claim.

        **`PersistResult.flagged` counts one of the two halves, not both.** An
        *implausible* provider clock is flagged and counted (`storage/service.py`
        writes `occurred_at_flag=implausible` and increments the counter); an
        *absent* one is not, because `validate_occurred_at(None)` returns `None`
        without raising and `_append_batch` renders `occurred_at=unknown` — a
        known answer rather than a suspect one, pinned by
        `tests/slice/test_storage_resolution.py::test_an_absent_timestamp_is_not_flagged`.
        So a page whose every row has no timestamp withholds coverage here and
        adds nothing to `flagged`, and this docstring used to promise otherwise.
        """
        for row in rows:
            at = row.get("committed_at")
            if not isinstance(at, datetime):
                # Absent, or a shape this connector will not map — a provider
                # ISO string is the real case. Skipped for the same reason
                # `None` is: it places no row in time. Not merely `is None`,
                # because `validate_occurred_at` reaches `_assert_comparable`,
                # which asks the value for `.tzinfo` and raises `AttributeError`
                # — out of this predicate and out of `harvest`, past every
                # failure handler. `_to_event` already refuses such a row; this
                # walks the raw page, so it has to refuse it too, and the bug
                # was order-dependent: one credible row ahead of it returned
                # early and hid the escape.
                continue
            try:
                validate_occurred_at(at, now=now)
            except ImplausibleTimestamp:
                continue
            return True
        return False

    def harvest(self, since: Cursor) -> HarvestResult:
        """Walk pages from `since`, and report what actually happened (AD-9, AD-35).

        Reports; never raises on a transport failure. A page-one-succeeded,
        page-two-failed fetch has to hand back page one's events, page one's real
        coverage window *and* the failure, and an exception can carry at most the
        last of those.

        Three things are only ever derived from pages that came back:

        - the coverage window exists at all (a page came back, and either it
          held nothing at all or at least one of its rows can be placed in
          time). The empty half is `8i`'s repair: a provider answering "nothing
          here" is a check that happened, and refusing it a window left a quiet
          project permanently uncovered — the one place a broken promise is
          most likely and the one place it could never be reported;
        - its `start` is this connector's clock at the instant the first page
          returned, and its `end` the clock when fetching stopped — never
          `now() - some_interval`, which is what the deleted four-hour window
          was: a claim about the world computed from nothing but the clock;
        - the cursor advances only past **rows actually received**, so a failure
          on page two leaves page three to be fetched again rather than skipped.

        That last rule had two definitions in one loop. The terminal and
        non-advancing branches resumed from rows received; the ordinary branch
        resumed from `page.next_offset` — the provider's *claim* about where the
        next page begins — so a provider returning two rows while announcing
        offset 5 advanced the cursor past rows 2, 3 and 4 permanently, since no
        later run revisits a position the cursor has passed. `next_offset` still
        decides what is *asked for* next; it decides nothing about what was
        walked.

        The loop is bounded twice, by `page_cap` and by `budget`. "The next
        offset must increase" is not a bound — a provider advancing one row per
        page satisfies it forever — and the argument is `graph/client.py`'s,
        which states it for a continuation condition the response body controls.
        """
        fetch = self.fetch_page or self._fetch_from_fake_api
        try:
            origin = int(since.token or b"0")
        except ValueError:
            # A cursor is opaque to core (AD-9) and this connector is the only
            # thing that may read one — but "opaque" cuts both ways: a token
            # written by another connector, or by an older encoding of this one,
            # is not a position here. Reported as the failure this method
            # promises rather than raised out of it, and the cursor is handed
            # back unchanged so nothing advances past rows nobody fetched.
            return HarvestResult(
                events=(),
                cursor=since,
                outcome=HarvestOutcome.FAILED,
                failure=HarvestFailure(
                    reason=(
                        f"{self.instance} was handed a stored cursor of "
                        f"{len(since.token)} byte(s) that is not a decimal "
                        f"offset, so there is no position to resume from. "
                        f"Nothing was fetched and the cursor is unchanged; a "
                        f"cursor this connector did not write needs clearing "
                        f"rather than waiting."
                    ),
                    retryable=False,
                ),
            )
        offset = origin
        # Where the next run resumes: `origin` plus the rows this one actually
        # received, and nothing else ever assigns it. One definition, in one
        # place, so no branch can quietly adopt the provider's claim instead.
        resume = origin
        rows: list[dict] = []
        # The instant the *first* page came back, and `None` for as long as none
        # has. It is what separates "the provider answered" from "we asked" —
        # and the latter earns no coverage no matter how long it took.
        reached_at: datetime | None = None
        failure: HarvestFailure | None = None
        walked = 0

        while True:
            try:
                page = fetch(offset)
            except PageUnavailable as refused:
                failure = HarvestFailure(
                    reason=f"{self.instance} could not fetch from offset {offset}: {refused.reason}",
                    retryable=refused.retryable,
                    retry_after=refused.retry_after,
                )
                break
            except Exception as unexpected:  # noqa: BLE001 — a failure is a value here
                # Broad on purpose. Anything a transport can throw has to become
                # this value, or the pages already walked lose their coverage on
                # the way up the stack. Not retryable per
                # `UNCLASSIFIED_FAULT_IS_RETRYABLE`, which is where that rule is
                # stated for both connectors — a scheduler reading
                # `HarvestFailure.retryable` cannot see which one produced it.
                #
                # The exception's **type** and not its `repr`: this sentence is
                # written to Tier 2, never rebuilt, and read back into reports,
                # and a transport's message is exactly where a signed URL or an
                # authorization header would appear.
                failure = HarvestFailure(
                    reason=(
                        f"{self.instance} could not fetch from offset {offset}: "
                        f"the transport raised {type(unexpected).__name__}, "
                        f"which pm-ai cannot classify"
                    ),
                    retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
                )
                break

            if not isinstance(page, Page):
                # The check `GraphClient._send` applies to its injected
                # transport, mirrored: `fetch_page` is a seam, and a callable
                # that answers with `None` would raise `AttributeError` on
                # `page.rows` *outside* every handler above — taking the pages
                # already walked and their coverage with it. A wiring fault in
                # pm-ai rather than anything GitLab said, so it is not
                # retryable.
                failure = HarvestFailure(
                    reason=(
                        f"{self.instance}'s transport answered with "
                        f"{type(page).__name__} rather than a Page while "
                        f"fetching from offset {offset}, so there are no rows "
                        f"to read and no next offset. That is a wiring fault in "
                        f"pm-ai rather than a provider fault."
                    ),
                    retryable=False,
                )
                break

            if reached_at is None:
                reached_at = self.now()
            rows.extend(page.rows)
            walked += 1
            resume = origin + len(rows)
            if page.next_offset is None:
                break
            if page.next_offset <= offset:
                # A provider handing back a position that does not advance would
                # loop here forever, re-harvesting the same rows. Reported as a
                # failure so the pages already walked keep their coverage.
                failure = HarvestFailure(
                    reason=(
                        f"{self.instance} was told the next page begins at "
                        f"{page.next_offset} while fetching from {offset}, which "
                        f"does not advance — walking it again would never end"
                    ),
                    retryable=False,
                )
                break
            if walked >= self.page_cap:
                # The ceiling `next_offset > offset` does not give: a provider
                # advancing one row per page satisfies that condition for as
                # long as it likes. Refused with what was walked rather than
                # stopped quietly, which would report a harvest as complete.
                failure = HarvestFailure(
                    reason=(
                        f"{self.instance} reached the {self.page_cap}-page cap "
                        f"with a further page still offered at offset "
                        f"{page.next_offset}. The continuation condition is a "
                        f"value the provider controls, so it is bounded; what "
                        f"was walked keeps its coverage and the cursor stops "
                        f"where the rows stopped."
                    ),
                    retryable=True,
                )
                break
            if self.now() - reached_at >= self.budget:
                # The other dimension: a provider answering slowly rather than
                # endlessly. Retryable, because the next run resumes from the
                # rows this one received rather than the window being reported
                # complete.
                failure = HarvestFailure(
                    reason=(
                        f"{self.instance} spent its {self.budget} fetch budget "
                        f"after {walked} page(s), with a further page offered "
                        f"at offset {page.next_offset}. The rest is the next "
                        f"run's; a fetch that outlived its own harvest cycle "
                        f"would overlap the one behind it."
                    ),
                    retryable=True,
                )
                break
            offset = page.next_offset

        finished_at = self.now()
        # Mapped one row at a time, because a row that cannot be read must not
        # discard the rows beside it, the coverage this fetch earned, or the
        # outcome. `_to_event` raises `RowRefused` rather than `KeyError` now,
        # but the difference only matters if the caller catches it — mapping
        # them in one comprehension left the refusal escaping `harvest` exactly
        # as the `KeyError` did, which is what the matrix's partial-page row
        # forbids one field lower.
        mapped: list[NormalizedEvent] = []
        # The raw rows whose mapping succeeded. The credibility predicate below
        # must read *these* and not `rows`: a refused row is discarded, so
        # letting its timestamp answer "can anything here be placed in time"
        # claims coverage on the strength of evidence nothing kept. Graph's
        # `_any_credible_clock` reads its emitted rows for the same reason —
        # that asymmetry is what hid this.
        kept: list[dict] = []
        refusals: list[RowRefusal] = []
        for row in rows:
            try:
                mapped.append(self._to_event(row))
            except RowRefused as refused:
                sha = row.get("sha")
                refusals.append(
                    RowRefusal(
                        identifier=sha if isinstance(sha, str) and sha.strip() else None,
                        reason=str(refused),
                    )
                )
            else:
                kept.append(row)
        events = tuple(mapped)

        # Decided before the window, because the window now reads it. `EMPTY` is
        # "a page came back, nothing in it needed mapping, and nothing went
        # wrong" — a check that was performed, and `8i` exists because it
        # recorded none: a quiet project accumulated no coverage, AD-35's
        # fail-closed reading was never armed over it, and a promise nobody kept
        # there could never be reported broken. Both bounds were already
        # measured; only the claim was missing.
        #
        # `HarvestOutcome.of` rather than three branches here and three more in
        # `graph`, because this condition now has two consumers instead of one
        # and the two connectors had spelled it differently. The `failed` half is
        # not decoration: page one can come back empty and page two fail, and
        # that run did *not* complete — coverage over it is refused by
        # `HarvestResult.__post_init__`, out of a method whose contract is that
        # it reports rather than raises.
        outcome = HarvestOutcome.of(
            mapped=bool(events), refused=bool(refusals), failed=failure is not None
        )

        coverage: CoverageWindow | None = None
        # Rows that arrived must be placed in time; rows that never arrived need
        # not be. A page whose every row was refused reached the provider and
        # yielded nothing readable, and coverage over it would let a mapping
        # defect read as a kept-or-broken promise — so the predicate reads
        # `kept`, and `_bounded_by_a_credible_clock([])` is `False`. That `False`
        # is exactly what used to speak for the empty case too, which is how a
        # check that found nothing lost its proof of having looked.
        if (
            (
                outcome is HarvestOutcome.EMPTY
                or self._bounded_by_a_credible_clock(kept, now=finished_at)
            )
            and reached_at is not None
            # This machine's clock, not the provider's: an NTP correction or a
            # laptop waking mid-harvest steps it backwards, and the pair would
            # be a reversed window — which `CoverageWindow` refuses, out of a
            # method whose contract is that it reports rather than raises. No
            # coverage claimed instead: the fetch happened and there is no
            # honest interval to say it happened in.
            and finished_at >= reached_at
        ):
            coverage = CoverageWindow(
                # Keyed on `instance`, which is what `save_cursor` stores the
                # window under and what `coverage_windows` reads it back by.
                connector_instance=self.instance,
                start=reached_at,
                end=finished_at,
            )

        # Refusals count as having harvested: rows arrived. A page whose every
        # row was refused and a provider with nothing in it both produce no
        # events, and `refusals` is what tells them apart — which is also why
        # `__post_init__` refuses EMPTY beside a refusal, and why `EMPTY` above
        # is safe to read as "the provider answered and there was nothing here".
        return HarvestResult(
            events=events,
            # `since` itself when nothing was walked, so "the cursor did not
            # move" is identity rather than a re-encoding that happens to parse
            # to the same integer.
            cursor=since if resume == origin else Cursor(str(resume).encode()),
            outcome=outcome,
            coverage=coverage,
            failure=failure,
            refusals=tuple(refusals),
        )
