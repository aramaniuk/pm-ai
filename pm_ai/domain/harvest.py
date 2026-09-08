"""Harvest primitives: the opaque cursor and what a harvest returns.

`Cursor` is opaque bytes because AD-9 says so — core must not interpret a
provider's pagination scheme. `HarvestResult` carries the coverage window
because AD-35 needs it — but **optionally**, since story `8a`.

A mandatory `CoverageWindow` was the defect, not the guard. It cannot be
forgotten and it can be fabricated, and a fabricated window is worse than a
missing one because it reads as evidence: the GitLab connector satisfied the
constructor with `started - 4h` to `started`, tied to nothing that proved a
fetch had happened, so a provider declining with an empty `200` claimed four
hours of coverage it never had. Absence is now expressible, and the outcome
says which absence it is.

Imports nothing from `pm_ai` except sibling domain modules (AD-30).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from pm_ai.domain.events import NormalizedEvent
from pm_ai.domain.lifecycle import CoverageWindow
from pm_ai.domain.meetings import Meeting


UNCLASSIFIED_FAULT_IS_RETRYABLE = False
"""Whether a fault no connector classified may be promised to clear on its own.

**The one place the rule is stated, cited by every connector that needs it.** A
scheduler reads `HarvestFailure.retryable` and cannot see which provider produced
it, so two connectors answering this question differently would make the same
fault mean "come back in a minute" from one and "a human has to look" from the
other. `gitlab.harvest` and `graph.calendar.GraphCalendarFetch` both catch
everything their transports can raise, and `graph.client.GraphFaultUnclassified`
is the same verdict one layer lower.

`False`, because an exception nobody classified is as likely to be a bug in pm-ai
as a hiccup at the provider, and no amount of waiting clears a bug. A provider
**5xx** is a different fact and stays retryable: the provider said, in its own
words, that the fault is its and transient.
"""


@dataclass(frozen=True, slots=True)
class Cursor:
    """Provider-defined position. Opaque to everything but its own connector.

    Deliberately exposes no `.timestamp`, `.page`, or `.offset`: cross-connector
    ordering uses the ingested_at watermark, never cursor internals (AD-9).
    """

    token: bytes = b""

    def __repr__(self) -> str:  # keeps provider tokens out of logs and tracebacks
        return f"Cursor(<{len(self.token)} bytes>)"


class HarvestOutcome(Enum):
    """What a harvest run *did*, as a value rather than as a control flow.

    Three states, because AD-35's guard needs to tell two silences apart and a
    two-state world (`events` empty or not) cannot. `HARVESTED` and `EMPTY` are
    both "the provider answered"; `FAILED` is "it did not", and only the last of
    those may resolve an overdue commitment to `ERROR` rather than `UNKNOWN`.

    `FAILED` is not "raised". A raised exception cannot carry the coverage a
    partial harvest earned: a page-one-succeeded, page-two-failed fetch has real
    events, a real window and a real failure, and all three have to travel
    together or the caller has to choose which two to keep.
    """

    HARVESTED = "harvested"
    """The provider answered and there was something in the answer."""

    EMPTY = "empty"
    """The provider answered and there was nothing new — an empty `200`.

    Distinct from `FAILED` because it is evidence: we looked. Distinct from
    `HARVESTED` because nothing came back to bound a window with, so this
    outcome never carries coverage.
    """

    FAILED = "failed"
    """The fetch could not be completed. Always accompanied by a `failure`."""


@dataclass(frozen=True, slots=True)
class HarvestFailure:
    """Why a harvest could not finish, in a form that survives the process.

    Carries no exception object and no traceback: this value is persisted beside
    the cursor and read back after a restart, which is what gives
    `evaluate_commitment`'s required `harvest_failed` a source. Without it, a
    dead credential and a laptop that never woke up are the same absent coverage
    window, and a permanently broken connector reads as patience.

    `retryable` has no default. A connector must say whether the operator should
    wait or act — a 429 with a hint clears itself, an expired token never will —
    and a default would pick one of those answers on the connector's behalf for
    every failure nobody thought about.

    `reason` carries no credential material, for the same reason `ProbeFailed`
    does not: it is written to Tier 2, which is never rebuilt, and read back into
    reports. The rule binds whoever *builds* the value — a provider message or an
    exception `repr` passed through unexamined is how a token ends up in a
    durable row — which is why the transport-level refusals connectors raise
    carry a reason they composed rather than the client's own.
    """

    reason: str
    retryable: bool
    retry_after: timedelta | None = None
    """The provider's own hint, when it gave one (a 429's `Retry-After`).

    `None` means "no hint", not "retry immediately": a retryable failure with no
    stated delay is the scheduler's decision, and inventing a number here would
    put a made-up interval where a measured one belongs.
    """

    at: datetime | None = None
    """When the failure was recorded, filled in by storage on the way back out.

    `None` on a value a connector just built: a connector reads its own clock for
    coverage bounds and nothing else, and the instant that matters for this row is
    the one the single writer stamped it with (AD-5, AD-30) — so `save_cursor`
    ignores whatever is here and writes `harvest_failures.at` from the storage
    clock, and `harvest_failure()` reads it back into this field.

    Surfaced rather than dropped because the column is Tier 2 and never rebuilt:
    a failure recorded four minutes ago and one recorded in March are the same
    `reason` and a different problem, and a reader that cannot see which is which
    cannot tell a connector that just broke from one nobody has looked at since.
    """


@dataclass(frozen=True, slots=True)
class RowRefusal:
    """One provider row a connector would not map, and why. The harvest continued.

    A value rather than a raise, for the same reason the failure is one: a row
    that cannot be read must not discard the rows beside it, the coverage the
    fetch earned, or the outcome. `gitlab.harvest` mapped its rows outside every
    `try`, so a single row missing a field raised out of the method and took
    page one's events, its real coverage and its outcome with it — the shape the
    matrix's partial-page row forbids, one field lower.

    Counted rather than logged, exactly as `33b`'s `RowRefusal` is: a harvest
    that refused every row and a harvest of a provider with nothing in it both
    produce no events, and only this tells them apart.

    `reason` carries no credential material, for the same reason
    `HarvestFailure.reason` does not — it names a row, and rows come from
    providers.
    """

    identifier: str | None
    """What the row could be cited by — a commit sha — or `None` when the thing
    missing was the identifier itself.
    """

    reason: str


@dataclass(frozen=True, slots=True)
class HarvestResult:
    """What every connector returns (AD-9, AD-35).

    `coverage` is optional and `outcome` is not. That pairing is the whole point:
    a connector that learned nothing has an honest way to say so, and no way to
    stay silent about which of the three things happened.

    `__post_init__` refuses the combinations that would make the outcome a
    decoration over the other fields — `FAILED` with no failure, `EMPTY` with
    events, and above all coverage claimed by a fetch that returned no rows,
    which is the fabrication this story exists to delete.
    """

    events: tuple[NormalizedEvent, ...]
    cursor: Cursor
    outcome: HarvestOutcome
    coverage: CoverageWindow | None = None
    failure: HarvestFailure | None = None
    refusals: tuple[RowRefusal, ...] = ()
    """The rows that came back and could not be mapped, one by one.

    Separate from `failure`, which is about the *fetch*: a page that arrived
    carrying one unreadable row among fifty is not a failed fetch, and reporting
    it as one would tell an operator to expect the harvest to have moved
    nothing. Empty is the ordinary answer.
    """

    records: tuple[Meeting, ...] = ()
    """Tier-1 domain records this harvest earned, for `app` to write (story 33c).

    Beside `events` rather than derived from them, because a record cannot be
    derived from one: `MeetingHeldPayload` carries `meeting_id`, an attendee
    *count* and a duration — no title, no start, no `calendar_event_ref` and no
    attendee list. A `Meeting` rebuilt from an event would be a different, poorer
    meeting wearing the same id.

    Written by `pm_ai.app.pipelines` through `pm_ai.core.meeting_records`, and
    **before** the events are persisted: a `CALENDAR_EVENT_HELD` cites
    `meeting:<id>` (AD-33), so an event emitted ahead of a failed record write
    leaves a citation nothing can resolve.

    Only meetings that have already happened belong here. One that has not is
    `live` below — the calendar owns it, and pm-ai keeps no copy.
    """

    live: tuple[Meeting, ...] = ()
    """Meetings mapped and handed back **without** being persisted anywhere.

    The other half of story 33c's past/future split, and the reason it is a
    second field rather than a flag on the first: `records` is the list `app`
    writes, so anything in it is written, and a caller that had to consult a
    boolean before writing would be one `if` away from persisting a meeting the
    calendar can move or cancel ten seconds later.

    Nothing here reaches a file. It exists so a surface asking a connector for
    the day ahead gets domain objects rather than provider rows — the read the
    dashboard makes instead of reading `event_log/`, which could not answer it:
    `MeetingHeldPayload` has no field for a title or a start, so a meeting that
    has not happened has no honest representation as an event (AD-27).
    """

    def __post_init__(self) -> None:
        if (self.outcome is HarvestOutcome.FAILED) != (self.failure is not None):
            raise ValueError(
                f"outcome={self.outcome.value} and failure={self.failure!r} "
                f"disagree. FAILED is the outcome that carries a reason, and a "
                f"reason with any other outcome is a failure nothing will "
                f"persist — which is how a dead connector reads as patience."
            )
        if (
            self.outcome is HarvestOutcome.HARVESTED
            and not self.events
            and not self.refusals
            and not self.records
            and not self.live
        ):
            raise ValueError(
                "outcome=harvested with no events, no records, nothing live and "
                "nothing refused. EMPTY is the value for a provider that "
                "answered with nothing; collapsing the two loses the only "
                "distinction AD-35's two silences are built on."
            )
        # HARVESTED with no events but with refusals is the third case, and it
        # is legal: rows came back and none could be read. EMPTY is refused for
        # it below, so without this it was unrepresentable — `gitlab.harvest`
        # raised out of the method for a page it had just refused row by row,
        # which is the escape the row-at-a-time mapping exists to stop.
        if self.outcome is HarvestOutcome.EMPTY and self.events:
            raise ValueError(
                f"outcome=empty with {len(self.events)} events. Whatever came "
                f"back, the run did not learn nothing."
            )
        if self.outcome is HarvestOutcome.EMPTY and self.refusals:
            raise ValueError(
                f"outcome=empty with {len(self.refusals)} refused row(s). Rows "
                f"came back and pm-ai could not read them, which is not the "
                f"same as a provider that answered with nothing — and it is the "
                f"one of the two that needs somebody to look."
            )
        if self.outcome is HarvestOutcome.EMPTY and (self.records or self.live):
            raise ValueError(
                f"outcome=empty with {len(self.records)} record(s) and "
                f"{len(self.live)} live meeting(s). A harvest that mapped a "
                f"meeting did not learn nothing, and EMPTY is the value AD-35's "
                f"fail-closed reading treats as 'the provider answered and there "
                f"was nothing there'."
            )
        # `records` and `live` join `events` here rather than being a second
        # rule, because they are the same evidence in a different shape: story
        # 33c's connector maps a window of upcoming meetings to `live` and emits
        # no event at all, and a window that reached the provider and came back
        # full of them has earned its coverage exactly as a page of commits has.
        if self.coverage is not None and not self.events and not self.records and not self.live:
            raise ValueError(
                "coverage claimed by a harvest that returned no rows. Coverage "
                "is evidence a fetch reached something (AD-35); a window over a "
                "fetch that came back empty is a four-hour claim tied to the "
                "clock and to nothing else."
            )
        # Refused rows deliberately do **not** earn coverage, though they do
        # make the outcome HARVESTED. Coverage arms AD-35's fail-closed reading
        # — within a covered window, absence of evidence is evidence of absence,
        # and `evaluate_commitment` returns BROKEN. If every row was refused the
        # absence is pm-ai's own mapping defect, not a broken promise, and FR-26
        # nudges are irreversible. So a page nobody could read stays UNKNOWN and
        # `refusals` is what says somebody has to look.


@dataclass(frozen=True, slots=True)
class PersistResult:
    """What the single writer reports back (AD-5, AD-34)."""

    persisted: int
    duplicates: int
    at: datetime
    flagged: int = 0
    """How many of the persisted events carried a provider clock we cannot believe.

    Reported rather than raised, because AD-35 says an implausible `occurred_at`
    is flagged and the batch is all-or-nothing. Without a count, a connector whose
    clock is wrong flags every event it emits and nothing anywhere says so — the
    entries are in the ledger and no one is looking at them.
    """
