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

from datetime import datetime, timedelta

from pm_ai.core import ledger
from pm_ai.domain.event_entries import EventEntry
from pm_ai.domain.identity import DataScope
from pm_ai.ports import StoragePort

__all__ = ["EventLog"]


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
    ) -> tuple[EventEntry, ...]:
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
        """
        _assert_comparable(ingested_since, name="ingested_since")
        _assert_comparable(ingested_until, name="ingested_until")

        entries: list[EventEntry] = []
        for name in self._storage.event_log_segments(scope=scope):
            if not _segment_overlaps(name, ingested_since, ingested_until):
                continue
            text = self._storage.read_event_log_segment(scope=scope, name=name)
            entries.extend(ledger.parse_segment(text, source=name))

        if ingested_since is None and ingested_until is None:
            return tuple(entries)
        return tuple(
            entry
            for entry in entries
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
