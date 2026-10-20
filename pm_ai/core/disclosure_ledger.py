"""Reading `disclosure.md`: AD-17's monthly total and AD-31's audit query.

After story 2i the ledger could be written and not read, so neither question the
architecture asks of it had a source — "what has left this machine" (AD-31) and
"what has the month cost" (AD-17).

**Totals are recomputed on every call, never cached and never stored.** A stored
total is a second structure that can disagree with the records it summarises, and
AD-17's whole point is that the figure is *evidence* rather than a counter. At one
line per frontier call, reading the file is cheap; if it ever is not, the fix is
an index in Tier 3, which `derivation-services.md` already has a shape for.

**One clock here, unlike the event log.** A `DisclosureRecord` carries a single
`at`: pm-ai made the call, so when it happened and when it was recorded are the
same instant. A period query therefore needs no `occurred_at`/`ingested_at`
choice, which is why these parameters carry no clock in their names and
`EventLog`'s do.
"""

from __future__ import annotations

import math

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pm_ai.domain.disclosure import (
    UNKNOWN,
    DisclosureRecord,
    DisclosureStatus,
    MalformedDisclosure,
)
from pm_ai.domain.event_entries import scan_fields
from pm_ai.domain.identity import DataScope
from pm_ai.ports import StoragePort

__all__ = [
    "MONTHLY_TARGET_USD",
    "DisclosureLedger",
    "MonthlyTotal",
    "parse_ledger",
    "parse_disclosure_line",
]

MONTHLY_TARGET_USD = 20.0
"""NFR-13's monthly frontier target (AD-17): a monitored target, not a cap.

A constant, no setting (story 7c). Past it a reply warns; nothing is refused
or downgraded."""

from pm_ai.domain.disclosure import NO_DESTINATION as _NONE


@dataclass(frozen=True, slots=True)
class MonthlyTotal:
    """What a month cost, and whether that passed a target someone supplied.

    `breached` is `None` when no target was given: "nothing was compared" and
    "nothing was exceeded" are different facts, and collapsing them would let a
    caller that forgot to pass its target read the result as reassurance. It is
    `None` too when the known total is within the target and some calls are of
    unknown cost (story 7c): within it cannot be claimed.
    """

    year: int
    month: int
    records: int
    """The calls of known cost counted in `cost_usd` (story 7c: calls, not lines)."""
    input_tokens: int
    output_tokens: int
    cost_usd: float
    breached: bool | None = None
    unknown: int = 0
    """Calls of unknown cost (story 7c): a `sent` line with no later line, a
    `failed` line without usage, or usage with no price. Never counted as zero."""
    malformed: tuple[str, ...] = ()
    """The lines that could not be read while totalling, named (only when asked
    to total past them, `skip_malformed`)."""


def parse_ledger(text: str, *, source: str = "disclosure.md") -> tuple[DisclosureRecord, ...]:
    """Every complete record in `text`, in ledger order.

    An unterminated final line is dropped — the daemon may be appending while a
    briefing reads, and treating that as corruption would make every concurrent
    read a failure. A *complete* line that will not parse is refused instead.
    """
    records, malformed = _parse(text, source=source)
    if malformed:
        raise malformed[0]
    return records


def _parse(
    text: str, *, source: str
) -> tuple[tuple[DisclosureRecord, ...], list[MalformedDisclosure]]:
    """Every complete record, and every complete line that would not parse, named."""
    records = []
    malformed: list[MalformedDisclosure] = []
    for number, line in enumerate(text.split("\n")[:-1], start=1):
        try:
            records.append(parse_disclosure_line(line))
        except MalformedDisclosure as refusal:
            named = MalformedDisclosure(f"{source} line {number}: {refusal}")
            named.__cause__ = refusal
            malformed.append(named)
    return tuple(records), malformed


def parse_disclosure_line(line: str) -> DisclosureRecord:
    """The inverse of `render_disclosure`, refusing anything it could not write."""
    if not line.startswith("- "):
        raise MalformedDisclosure(f"{line!r} does not open with the record marker.")
    try:
        fields = dict(scan_fields(line[2:], line=line))
    except ValueError as refusal:
        raise MalformedDisclosure(f"{line!r}: {refusal}") from refusal

    missing = {
        "at", "task_class", "model", "input_tokens",
        "output_tokens", "cost_usd", "scopes", "destination",
    } - fields.keys()
    if missing:
        raise MalformedDisclosure(f"{line!r} is missing {sorted(missing)}.")
    # Story 7c: a call id and a status together, or neither (a pre-7c line).
    called = "call" in fields or "status" in fields
    if called and not ("call" in fields and "status" in fields):
        raise MalformedDisclosure(f"{line!r} carries one of `call` and `status` without the other.")
    # `unknown` is allowed only on a line with a status: a pre-7c line is a
    # completed call of the cost it states.
    unknowable = called

    def count(key: str) -> int | None:
        raw = fields[key]
        if unknowable and raw == UNKNOWN:
            return None
        value = int(raw)
        if value < 0:
            raise ValueError(f"{key} is negative")
        return value

    def cost() -> float | None:
        raw = fields["cost_usd"]
        if unknowable and raw == UNKNOWN:
            return None
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("cost_usd is not finite")
        return value

    try:
        return DisclosureRecord(
            at=datetime.fromisoformat(fields["at"]),
            task_class=fields["task_class"],
            model=fields["model"],
            contributing_scopes=frozenset(
                DataScope.parse(raw) for raw in fields["scopes"].split(",") if raw
            ),
            input_tokens=count("input_tokens"),
            output_tokens=count("output_tokens"),
            estimated_cost_usd=cost(),
            destination=(
                None
                if fields["destination"] == _NONE
                else DataScope.parse(fields["destination"])
            ),
            call_id=fields["call"] if called else None,
            status=DisclosureStatus(fields["status"]) if called else None,
        )
    except (ValueError, KeyError) as refusal:
        raise MalformedDisclosure(f"{line!r}: {refusal}") from refusal


def _utc(at: datetime) -> datetime:
    """The same instant, expressed in UTC, so calendar fields mean one thing."""
    return at.astimezone(timezone.utc)


def _assert_comparable(bound: datetime | None, *, name: str) -> None:
    """A naive bound raises `TypeError` mid-scan; refused here instead.

    An audit query that crashes answers nothing, and "what left this machine" is
    the question least well served by an exception from inside a loop.
    """
    if bound is None:
        return
    if bound.tzinfo is None or bound.utcoffset() != timedelta(0):
        raise ValueError(
            f"{name}={bound!r} is not aware UTC. Every `at` in the ledger is, so "
            f"this bound cannot be compared against them."
        )


def _breached(cost: float, target: float | None, *, unknown: int) -> bool | None:
    """Past `target`: `True` on the known total alone; `None` when nothing was
    compared, or when the known total is within it but calls of unknown cost
    could take it past (story 7c) — never a `False` the ledger cannot back."""
    if target is None:
        return None
    if cost > target:
        return True
    return None if unknown else False


class DisclosureLedger:
    """The two aggregates AD-17 and AD-31 name, over the one file that holds them."""

    def __init__(self, storage: StoragePort) -> None:
        self._storage = storage

    def records(
        self, *, since: datetime | None = None, until: datetime | None = None
    ) -> tuple[DisclosureRecord, ...]:
        """Records in ledger order, optionally bounded — AD-31's audit query.

        Bounds are inclusive: an audit asking "what left the machine on the 15th"
        means the whole of the 15th, and an exclusive bound silently drops the
        record written at the instant asked about.
        """
        _assert_comparable(since, name="since")
        _assert_comparable(until, name="until")

        found = parse_ledger(self._storage.read_disclosure())
        if since is None and until is None:
            return found
        return tuple(
            record
            for record in found
            if (since is None or record.at >= since)
            and (until is None or record.at <= until)
        )

    def monthly_total(
        self,
        year: int,
        month: int,
        *,
        target_usd: float | None = None,
        skip_malformed: bool = False,
    ) -> MonthlyTotal:
        """AD-17's running monthly total. **Warns only** — nothing here blocks.

        The target arrives as an argument rather than from configuration: no
        story owns that key yet, and hard-coding a budget figure would put one in
        the domain. A caller that has a configured target supplies it.

        **Calls, not lines** (story 7c, AD-38). Lines are paired by call id,
        across months; a call belongs to the UTC month of its `sent` line (of
        its first line, when no `sent` line is there). Its last line decides
        it: one that reports usage — `completed` or `failed` — is summed, and
        its cost too when the line states one; a `sent` line with no later
        line, a `failed` line without usage, and usage with no cost are each a
        call of unknown cost, never counted as zero. A line written before 7c,
        with no call id or status, is a completed call of the cost it states.

        A malformed complete line refuses the total, unless `skip_malformed`:
        then the total is over the lines that read, and `malformed` names the
        rest.
        """
        if not 1 <= month <= 12:
            raise ValueError(f"{month} is not a month. A total needs one to name.")
        found, broken = _parse(self._storage.read_disclosure(), source="disclosure.md")
        if broken and not skip_malformed:
            raise broken[0]
        calls: dict[str, list[DisclosureRecord]] = {}
        legacy: list[DisclosureRecord] = []
        for record in found:
            if record.call_id is None:
                legacy.append(record)
            else:
                calls.setdefault(record.call_id, []).append(record)

        def in_month(at: datetime) -> bool:
            # Converted before the month is read: `record.at.month` on an
            # offset-carrying value is its *wall-clock* month, which straddles
            # the boundary the caller asked about.
            return _utc(at).year == year and _utc(at).month == month

        counted: list[DisclosureRecord] = [r for r in legacy if in_month(r.at)]
        tokens_from: list[DisclosureRecord] = list(counted)
        unknown = 0
        for lines in calls.values():
            sent = next((r for r in lines if r.status is DisclosureStatus.SENT), lines[0])
            if not in_month(sent.at):
                continue
            last = lines[-1]
            if last.status is DisclosureStatus.SENT or not last.reports_usage:
                unknown += 1
                continue
            tokens_from.append(last)
            if last.estimated_cost_usd is None:
                unknown += 1
            else:
                counted.append(last)
        # `math.fsum` rather than `sum`: correctly rounded, and free. No drifting
        # case was reproducible with realistic sub-cent costs, so this is not a
        # fix for an observed defect — it is the exact option taken because AD-17
        # calls this figure evidence.
        cost = math.fsum(record.estimated_cost_usd or 0.0 for record in counted)
        return MonthlyTotal(
            year=year,
            month=month,
            records=len(counted),
            input_tokens=sum(record.input_tokens or 0 for record in tokens_from),
            output_tokens=sum(record.output_tokens or 0 for record in tokens_from),
            cost_usd=cost,
            breached=_breached(cost, target_usd, unknown=unknown),
            unknown=unknown,
            malformed=tuple(str(line) for line in broken),
        )
