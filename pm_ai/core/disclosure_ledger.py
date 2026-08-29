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

from dataclasses import dataclass
from datetime import datetime

from pm_ai.domain.disclosure import DisclosureRecord, MalformedDisclosure
from pm_ai.domain.event_entries import scan_fields
from pm_ai.domain.identity import DataScope

__all__ = ["DisclosureLedger", "MonthlyTotal", "parse_ledger", "parse_disclosure_line"]

_NONE = "none"


@dataclass(frozen=True, slots=True)
class MonthlyTotal:
    """What a month cost, and whether that passed a target someone supplied.

    `breached` is `None` when no target was given: "nothing was compared" and
    "nothing was exceeded" are different facts, and collapsing them would let a
    caller that forgot to pass its target read the result as reassurance.
    """

    records: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    breached: bool | None = None


def parse_ledger(text: str, *, source: str = "disclosure.md") -> tuple[DisclosureRecord, ...]:
    """Every complete record in `text`, in ledger order.

    An unterminated final line is dropped — the daemon may be appending while a
    briefing reads, and treating that as corruption would make every concurrent
    read a failure. A *complete* line that will not parse is refused instead.
    """
    records = []
    for number, line in enumerate(text.split("\n")[:-1], start=1):
        try:
            records.append(parse_disclosure_line(line))
        except MalformedDisclosure as refusal:
            raise MalformedDisclosure(f"{source} line {number}: {refusal}") from refusal
    return tuple(records)


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

    try:
        return DisclosureRecord(
            at=datetime.fromisoformat(fields["at"]),
            task_class=fields["task_class"],
            model=fields["model"],
            contributing_scopes=frozenset(
                DataScope.parse(raw) for raw in fields["scopes"].split(",") if raw
            ),
            input_tokens=int(fields["input_tokens"]),
            output_tokens=int(fields["output_tokens"]),
            estimated_cost_usd=float(fields["cost_usd"]),
            destination=(
                None
                if fields["destination"] == _NONE
                else DataScope.parse(fields["destination"])
            ),
        )
    except (ValueError, KeyError) as refusal:
        raise MalformedDisclosure(f"{line!r}: {refusal}") from refusal


class DisclosureLedger:
    """The two aggregates AD-17 and AD-31 name, over the one file that holds them."""

    def __init__(self, storage) -> None:
        self._storage = storage

    def records(
        self, *, since: datetime | None = None, until: datetime | None = None
    ) -> tuple[DisclosureRecord, ...]:
        """Records in ledger order, optionally bounded — AD-31's audit query.

        Bounds are inclusive: an audit asking "what left the machine on the 15th"
        means the whole of the 15th, and an exclusive bound silently drops the
        record written at the instant asked about.
        """
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
        self, year: int, month: int, *, target_usd: float | None = None
    ) -> MonthlyTotal:
        """AD-17's running monthly total. **Warns only** — nothing here blocks.

        The target arrives as an argument rather than from configuration: no
        story owns that key yet, and hard-coding a budget figure would put one in
        the domain. A caller that has a configured target supplies it.
        """
        found = [
            record
            for record in parse_ledger(self._storage.read_disclosure())
            if record.at.year == year and record.at.month == month
        ]
        cost = sum(record.estimated_cost_usd for record in found)
        return MonthlyTotal(
            records=len(found),
            input_tokens=sum(record.input_tokens for record in found),
            output_tokens=sum(record.output_tokens for record in found),
            cost_usd=cost,
            breached=None if target_usd is None else cost > target_usd,
        )
