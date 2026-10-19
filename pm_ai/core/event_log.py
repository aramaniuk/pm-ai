"""One vocabulary over a scope's event log (`derivation-services.md`, rule 3).

Rule 3 names three shared Tier-1 accessors and gives the reason plainly: without
them, "three jobs parse the same Markdown three ways and the fourth reader
disagrees with all of them." `EventLog` is the one story 2 owns. Harvest,
transcript processing and every audit write touch `event_log/` already; the
retrospective, compaction and the search index will.

**It is a vocabulary, not a second writer.** Every byte moves through the single
writer (AD-5). This holds no path, opens no file, and depends on `StoragePort`
rather than the concrete service — which is also what lets its whole surface be
exercised without a filesystem. An accessor that needed one would be holding a
path.

Scope is an argument on every method, never a construction-time default: the
debug-flag notice belongs to the application scope and a skill invocation to the
skill's own, and an accessor bound to one scope makes the other a mistake nobody
sees.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from enum import Enum

from pm_ai.core import ledger
from pm_ai.domain.event_entries import IMPLAUSIBLE, OCCURRED_AT_FLAG, EventEntry
from pm_ai.domain.identity import DataScope
from pm_ai.ports import StoragePort

__all__ = ["EventLog", "LogRead", "Unplaceable", "ingested_at", "posted_at"]


class LogRead(tuple[EventEntry, ...]):
    """The entries a read returned, each with the segment it was read from.

    A tuple, so every caller that wanted the entries has them unchanged. The
    segments ride beside them for a reader that has to tell the PM where a line
    is (`23c`'s footnote naming the lines that could not be decoded): an entry
    carries no segment of its own, and deriving one from `ingested_at` would be
    a guess the segment's name happens to agree with.
    """

    segments: tuple[str, ...]

    def __new__(cls, located: Iterable[tuple[str, EventEntry]] = ()) -> LogRead:
        pairs = tuple(located)
        read = super().__new__(cls, (entry for _, entry in pairs))
        read.segments = tuple(segment for segment, _ in pairs)
        return read


class EventLog:
    def __init__(self, storage: StoragePort) -> None:
        self._storage = storage

    def append(self, entry: EventEntry, *, scope: DataScope) -> None:
        """Delegate to the single writer, which mints the id and stamps the clock."""
        self._storage.append_event_log(entry, scope=scope)

    def open_segment(self, *, scope: DataScope) -> str | None:
        """The segment appends land in, or `None` if the log is empty.

        The writer sorts and filters; this takes the last. Deliberately not a
        second `max()` over the names — story 2g's whole point is that "which
        segment is open" has one definition, and two computations of it are the
        divergence the sealed-segment guard exists to prevent.
        """
        segments = self._storage.event_log_segments(scope=scope)
        return segments[-1] if segments else None

    def read(
        self,
        *,
        scope: DataScope,
        ingested_since: datetime | None = None,
        ingested_until: datetime | None = None,
    ) -> LogRead:
        """Every entry in `scope`, in **arrival order**, optionally bounded.

        Arrival order — segments by name, lines by position — because that is the
        exact chronology: storage is the single writer and each append adds one
        line at the end, so there are no ties even among records sharing a
        timestamp. `ledger.fold` is one call away for a caller deriving state
        across a rebuild; it is a different question, and making it the default
        here would silently reorder for every caller that just wanted to know
        what happened.

        **The bounds are on `ingested_at`, and the parameters say so.** Segment
        filenames derive from the write clock, so skipping a segment outside the
        range is sound for that clock and wrong for the other — an event that
        occurred in July and was ingested in August lives in the August segment.
        AD-35 makes mixing the two the defect, and an unnamed `since` is how it
        happens.

        An entry with no `ingested_at` is returned by an unbounded read and
        excluded from a bounded one: the range asks a question that entry cannot
        answer, and guessing either way is worse than leaving it out.

        Returned as a `LogRead`: a tuple of the entries that also names, for each,
        the segment it came from.
        """
        _assert_comparable(ingested_since, name="ingested_since")
        _assert_comparable(ingested_until, name="ingested_until")

        located: list[tuple[str, EventEntry]] = []
        for name in self._storage.event_log_segments(scope=scope):
            if not _segment_overlaps(name, ingested_since, ingested_until):
                continue
            text = self._storage.read_event_log_segment(scope=scope, name=name)
            located.extend(
                (name, entry) for entry in ledger.parse_segment(text, source=name)
            )

        if ingested_since is None and ingested_until is None:
            return LogRead(located)
        return LogRead(
            (name, entry)
            for name, entry in located
            if _within(entry, ingested_since, ingested_until)
        )


def _assert_comparable(bound: datetime | None, *, name: str) -> None:
    """A naive bound is a caller error, refused here rather than mid-scan.

    Comparing it against a stored aware `ingested_at` raises `TypeError` from
    inside the loop, which surfaces as the read *failing* rather than answering —
    and `_within` catches only `ValueError`, so nothing absorbed it.
    """
    if bound is None:
        return
    if bound.tzinfo is None or bound.utcoffset() != timedelta(0):
        raise ValueError(
            f"{name}={bound!r} is not aware UTC. Every `ingested_at` in the "
            f"ledger is, so this bound cannot be compared against them."
        )


def _segment_overlaps(
    name: str, since: datetime | None, until: datetime | None
) -> bool:
    """Whether a `%Y-%m.md` segment can hold anything inside the range.

    Compared as month strings rather than parsed dates: the filename *is* a
    month, and `2026-08` sorting between `2026-07` and `2026-09` is the same
    fact whichever way it is spelled.
    """
    month = name.removesuffix(".md")
    if since is not None and month < f"{since:%Y-%m}":
        return False
    if until is not None and month > f"{until:%Y-%m}":
        return False
    return True


def _within(entry: EventEntry, since: datetime | None, until: datetime | None) -> bool:
    raw = dict(entry.fields).get("ingested_at")
    if raw is None:
        return False
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if since is not None and at < since:
        return False
    if until is not None and at > until:
        return False
    return True


# ── The two instants a dashboard reads off a line ────────────────────────────
# Moved here from `rendering` (story `23g`), because two sections now read
# them: Proactive Enablement and Team Activity. `ingested_at` gained one guard
# (an instant with no UTC equivalent is unreadable, not a crash); `posted_at`
# is unchanged.


class Unplaceable(Enum):
    """Why an entry got no instant — two facts a section states separately."""

    ABSENT = "absent"
    """No `ingested_at` field at all: an entry written before story `2e`."""

    UNREADABLE = "unreadable"
    """A field that is there and yields no instant — malformed, or naive.

    Naive belongs here rather than being converted: there is no zone to assume,
    and assuming one is the silent wrong answer. It is a value that could not be
    read *as an instant*, which is precisely what the sentence says.
    """


def ingested_at(entry: EventEntry) -> datetime | Unplaceable:
    """The write clock a window is measured on, or why there is none.

    An aware value at any offset is a real instant and is converted to UTC —
    `2026-09-09T10:30:00+02:00` is 08:30Z and belongs in a window that holds
    08:30Z. Refusing it dropped a placeable signal and then printed a reason
    that was not the one computed. Only `now` and the meeting starts are held to
    UTC-on-arrival: those come from this codebase's own callers, while this
    field is read back off a ledger line.
    """
    raw = dict(entry.fields).get("ingested_at")
    if raw is None:
        return Unplaceable.ABSENT
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        return Unplaceable.UNREADABLE
    if at.tzinfo is None or at.utcoffset() is None:
        return Unplaceable.UNREADABLE
    try:
        return at.astimezone(timezone.utc)
    except OverflowError:
        # `0001-01-01T00:00+05:00` has no UTC instant: unplaceable, not a crash
        # (guarded since `23g`, which reads every commit line through here).
        return Unplaceable.UNREADABLE


def posted_at(entry: EventEntry, *, now: datetime) -> datetime | None:
    """The provider's posted time, when a line may be labelled with it (`23f`).

    `None` — so the line takes its harvest time, marked — when `occurred_at` is
    absent or `unknown`, unreadable, without a timezone, outside the range a
    UTC instant can hold, flagged implausible
    when it was stored (AD-35), or later than this render's instant: a posted
    time in the future cannot be right yet.
    """
    fields = dict(entry.fields)
    if fields.get(OCCURRED_AT_FLAG) == IMPLAUSIBLE:
        return None
    raw = fields.get("occurred_at")
    if raw is None:
        return None
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        # `unknown` lands here too: it is not an instant.
        return None
    if at.tzinfo is None or at.utcoffset() is None:
        return None
    try:
        at = at.astimezone(timezone.utc)
    except OverflowError:
        # `0001-01-01T00:00+05:00` has no UTC instant: unusable, not a crash.
        return None
    return None if at > now else at
