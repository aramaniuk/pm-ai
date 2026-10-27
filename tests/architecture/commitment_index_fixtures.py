"""Fixture ledgers for story 15b's tests: lines built by 15a's and 15c's own renderer.

Names are `11d`'s fixture names (`Example Weekly Sync-up`, `Alex Morgan`,
`Sam Rivera`); nothing here is field data.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from pm_ai.core.commitment_log import DueSet, Marked, OwnerSet, Pending, Revised, Undone, entry_id, render_line
from pm_ai.domain.commitments import Cites, Commitment, Due, Mark, StateSource
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.lifecycle import CommitmentState

UTC = timezone.utc
START = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
"""The fixture meeting: Mon 19 Oct 2026, 10:00 UTC."""
APPROVED = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
PERSONAL = DataScope(ScopeKind.PERSONAL)
ALPHA = DataScope(ScopeKind.PROJECT, "alpha")


def commitment(
    root: str,
    *,
    type: str = "action_item",
    what: str = "send the cache TTL numbers",
    owner: str | None = "Sam Rivera",
    speaker: str | None = None,
    due: Due | None = None,
    meeting_id: str | None = "mtg_a",
    start: datetime | None = START,
    day: date | None = date(2026, 10, 19),
    day_from: str | None = None,
    approved_at: datetime = APPROVED,
) -> Commitment:
    return Commitment(
        commitment_id=root,
        proposal_id=root,
        type=type,
        meeting_id=meeting_id,
        title="Example Weekly Sync-up" if start is not None else None,
        start=start,
        day=day,
        day_from=day_from,
        what=what,
        owner=owner,
        speaker=speaker,
        due=due if due is not None else Due("by Thursday", "2026-10-22"),
        state=CommitmentState.PENDING,
        state_source=StateSource.NONE,
        approved_at=approved_at,
        cites=Cites(None if meeting_id is None else f"meeting:{meeting_id}", speaker or owner),
    )


def pending(item: Commitment) -> str:
    return render_line(Pending(item, via="terminal", written="approval", at=item.approved_at)).text


def marked(root: str, mark: Mark, occurred: datetime) -> str:
    return render_line(Marked(root, mark, occurred, via="terminal", at=occurred)).text


def mark_entry(root: str, mark: Mark, occurred: datetime) -> str:
    return entry_id((root, "marked", mark.value, occurred.astimezone(UTC).isoformat()))


def undone(root: str, mark_line_entry: str, occurred: datetime) -> str:
    return render_line(Undone(root, mark_line_entry, occurred, via="terminal", at=occurred)).text


def due_set(root: str, day: date, occurred: datetime) -> str:
    return render_line(DueSet(root, day, occurred, via="terminal", at=occurred)).text


def owner_set(root: str, owner: str, occurred: datetime) -> str:
    return render_line(OwnerSet(root, owner, occurred, via="terminal", at=occurred)).text


def revised(root: str, proposal: str, what: str, occurred: datetime) -> str:
    return render_line(
        Revised(
            root, proposal, root, what, "Sam Rivera", None, occurred,
            via="terminal", written="approval", at=occurred,
        )
    ).text


def verdict(root: str, state: str, occurred: datetime) -> str:
    """A telemetry verdict, hand-written as story 16 will write it."""
    when = occurred.isoformat()
    return f"- entry={entry_id((root, 'verdict', state, when))} commitment={root} kind=verdict state={state} occurred={when} at={when}"


def ledger(*lines: str) -> bytes:
    return "".join(line + "\n" for line in lines).encode("utf-8")


def after(hours: float) -> datetime:
    return APPROVED + timedelta(hours=hours)
