"""The ingestion pipeline: harvest → attribute → normalize → persist.

Lives in `app` because it must touch a connector, the core, and storage — which
no other layer is permitted to do (AD-30).

**No sanitization step, deliberately (story 8e).** One stood here — a
`sanitize(...)` call whose return value was discarded, under a comment claiming
AD-12 was enforced. AD-12 asks for the guard "at the consumer, not only at the
producer", and a producer-side pass is one forgotten call site away from being
false. It is `pm_ai.ports.ModelPort` that enforces it now, by accepting
`Sanitized` and never `str` for externally-sourced text.

Nothing was lost by dropping the step, and nothing here needs to replace it:
`sanitize` is a pure function, AD-29 guarantees the raw it reads is retained,
and so the derived copy is rebuilt at the consumer — not on this path, and not
on the write path, which therefore carries no second copy of anything.
"""

from __future__ import annotations

import errno
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from pm_ai.app.wiring import Daemon
from pm_ai.core.config import ConfigRefused
from pm_ai.core.event_log import EventLog
from pm_ai.core.extraction import extract
from pm_ai.core.goal_register import ARTIFACT as GOALS_ARTIFACT
from pm_ai.core.goal_register import parse_goals
from pm_ai.core.normalize import attribute_all
from pm_ai.core.rendering import (
    CalendarAnswer,
    render_dashboard,
    render_project_dashboard,
)
from pm_ai.domain.disclosure import assert_citation_legal
from pm_ai.domain.events import NormalizedEvent, ObservedEventType
from pm_ai.domain.identity import DataScope, ScopeKind, TargetRef
from pm_ai.domain.lifecycle import ProposalState
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.proposals import Proposal
from pm_ai.domain.harvest import (
    UNCLASSIFIED_FAULT_IS_RETRYABLE,
    Cursor,
    HarvestFailure,
    HarvestOutcome,
    HarvestResult,
    NoCalendarConnector,
    PartialCalendar,
    PersistResult,
    UnreadCalendar,
)
from pm_ai.domain.scope_model import ScopeResolutionError, artifacts_in

DASHBOARD_ARTIFACT = "daily_dashboard.md"
"""CAP-9's file, spelled once — the personal tree's copy and the project's.

Declared in exactly two scope trees (`scope_model.py:540,733`), which is what
makes `--scope people:bob` a refusal by name rather than a path that resolves to
somewhere nothing declared.
"""

PERSONAL = DataScope(ScopeKind.PERSONAL)
"""CAP-9 names `~/.manager-ai/memory/daily_dashboard.md`, so this is the default."""


def run_harvest(daemon: Daemon, instance: str) -> PersistResult:
    connector = daemon.connectors[instance]
    cursor = daemon.storage.load_cursor(instance)  # scheduler owns the cursor (AD-9)

    result = connector.harvest(cursor)

    # AD-36 — the match step. Connectors emit `unknown`; this is the only layer
    # that can see the executed-mutation ledger, so this is where provenance is
    # decided. Without it, pm-ai's own writes harvest back as external evidence.
    attributed = attribute_all(result.events, daemon.storage.executed_mutations())

    # The Tier-1 records this harvest earned, written **before** their events
    # (story 33c). A `CALENDAR_EVENT_HELD` cites `meeting:<id>` (AD-33), so an
    # event persisted ahead of a failed `meetings/` write leaves a citation
    # nothing can resolve — and the failure is propagated rather than counted,
    # because the alternative is a batch of events whose referents are missing.
    #
    # Through `11a`'s accessor, and to `meeting.scope` rather than the daemon's:
    # a connector may not import `pm_ai.storage`, which is why the records travel
    # out on the result and are written here.
    #
    # **No `scope=` here, while every `persist_events` call below carries one,
    # and the asymmetry is the two writers' rather than an oversight.**
    # `MeetingRecords.put` takes no scope at all: it reads `meeting.scope` and
    # writes into that tree, so a record cannot be offered to a scope other than
    # its own and there is no cross-scope offer for a guard to refuse. An event
    # can — `persist_events` takes the destination as an argument — which is why
    # that path had to be restructured to group by `event.scope` the moment a
    # connector emitted in two.
    #
    # So no, `put` does **not** run `disclosure.assert_writable`, the AD-38 write
    # guard `_append_batch` runs on every event — and running it here would be
    # running a tautology. That guard compares the scopes a record *references*
    # against the scope it is *bound for*, and for a `Meeting` those are one
    # value: `referenced_scopes` reads `record.scope`, which is the very field
    # `put` derives the destination from. It can only ever compare
    # `meeting.scope` with itself. The guard has teeth on the event path because
    # there the destination is an argument that can disagree with
    # `event.scope` — which is exactly the disagreement `_persist_by_scope`
    # below exists to stop producing.
    #
    # What does bind a record is narrower and earlier: `_assert_records_meetings`
    # refuses any scope whose tree does not declare `meetings/`, and the project
    # ids a connector may put there were checked against the registry when its
    # `CategoryScopes` was built.
    for meeting in result.records:
        daemon.meetings.put(meeting)

    # Persist first, then record where the harvest got to. The order is the
    # matrix row about a persist that raises after page one: `persist_events` is
    # all-or-nothing, and this call sequence means a refusal there discards page
    # one's cursor *and* its coverage together rather than leaving a cursor that
    # advanced past events nobody stored.
    persisted = _persist_by_scope(daemon, attributed)
    # AD-35 — `result.coverage` is `CoverageWindow | None` and `None` is passed
    # through as itself. A harvest that asked and got an empty answer *does*
    # record a window (`8i`): the connector's own clock readings either side of
    # the request it made. `None` means it did not check — the request failed,
    # nothing that came back had a readable time, everything that came back was
    # rejected as unreadable, or this machine's clock stepped backwards during
    # the request, leaving no honest interval to record. What is gone is the
    # window the connector used to fabricate from the clock alone to satisfy a
    # mandatory field, which the fail-closed guard read as evidence.
    #
    # `result.failure` travels with it, in the same write, so "ran and failed"
    # survives the process as something other than the absence of coverage.
    daemon.storage.save_cursor(instance, result.cursor, result.coverage, result.failure)
    return persisted


def _persist_by_scope(
    daemon: Daemon, events: tuple[NormalizedEvent, ...]
) -> PersistResult:
    """Write each event into the scope it declares, and report the whole run.

    One `persist_events` call per scope, because an event carries its own and
    AD-38's write guard reads it: a personal meeting harvested by a
    project-scoped daemon is a `CommittedScopeLeak` the moment it is offered to
    the project's log, and it is *right* that it is — a private appointment in
    the team's ledger is the leak the scope model exists to refuse, whether or
    not that ledger is one git would carry (AD-38, revised 2026-09-03). Story
    33c is the first connector to emit events in more than one scope; before it,
    every event carried the daemon's own and this grouping produced exactly the
    single call it replaced.

    An empty harvest still calls the writer once, in the daemon's scope, so the
    `at` a caller reads is a real clock read from the single writer rather than
    one composed here (AD-5).

    All-or-nothing per scope rather than across the batch, which is the cost of
    the split and is stated rather than hidden: `persist_events` is the unit of
    atomicity and there is no cross-scope transaction to be had — the two logs
    are different files in different trees, one of them committed.
    """
    if not events:
        return daemon.storage.persist_events((), scope=daemon.scope)
    grouped: dict[DataScope, list[NormalizedEvent]] = {}
    for event in events:
        grouped.setdefault(event.scope, []).append(event)
    written = [
        daemon.storage.persist_events(tuple(batch), scope=scope)
        for scope, batch in grouped.items()
    ]
    return PersistResult(
        persisted=sum(result.persisted for result in written),
        duplicates=sum(result.duplicates for result in written),
        # The last writer's stamp, not one composed here: `at` is a clock read
        # the single writer owns (AD-5), and averaging or inventing one would put
        # a timestamp in the report that nothing measured.
        at=written[-1].at,
        flagged=sum(result.flagged for result in written),
    )


def run_transcript_ingestion(daemon: Daemon, transcript, meeting, *, provider: str = "gitlab") -> dict:
    """Ingest a bound transcript, extract, then execute or stage per AD-32.

    Lives in `app` for the same reason the harvest pipeline does: it must reach a
    connector, the core, storage, and the skill registry, which no single layer
    below is permitted to do (AD-30).
    """
    # AD-38 — check the citation direction BEFORE extracting anything. A meeting
    # owned by `personal` or `people` cannot be cited from the project scope,
    # and every extraction below will cite this meeting (AD-33).
    assert_citation_legal(cited=meeting.scope, into=daemon.scope)
    results = extract(transcript, meeting, pm_handle=daemon.pm_handle, provider=provider)
    # The Tier-1 record, and the citation root every extraction above points at
    # (AD-33). Through the accessor since story 11a: this was an assignment into
    # a process-lifetime dict, so the record a citation resolved against was gone
    # the moment the daemon stopped. `meeting.scope` decides which tree it lands
    # in, which is why the legality check runs first.
    #
    # **After `extract` and before the first write that cites the meeting**, and
    # both halves of that are the ordering rather than an accident. Moving the
    # record to disk made this pipeline non-atomic in a way the dict could not
    # be: a dict assignment cannot fail and does not survive a restart, while
    # this line can raise and does. So:
    #
    # - `extract` runs first because it is the step that fails — a malformed
    #   transcript, a provider verb it cannot parse — and it produces nothing
    #   durable, so its failure leaves no record for an ingestion that minted no
    #   citations. That was the state reachable when the write came first.
    # - the record is written before the loop below, because everything the loop
    #   writes cites this meeting: a staged proposal or an executed mutation
    #   whose `cites` resolves to nothing is worse than one extraction lost, so a
    #   failure part-way through staging leaves a record its partial proposals
    #   can still be resolved against.
    #
    # What remains is a record for a transcript that extracted *nothing*, and
    # that is deliberate: the meeting happened, `Meeting` is Tier-1 in its own
    # right, and `33c` records meetings with no transcript at all.
    daemon.meetings.put(meeting)

    executed, staged = [], []
    for i, ex in enumerate(results):
        if ex.disposition == "execute":
            target = TargetRef.parse(ex.detail["target"])
            from pm_ai.core.jobs import idempotency_key

            key = idempotency_key(ex.detail["verb"], target.lock_key, ex.detail)
            inv = daemon.skills.invoke(
                f"{provider}.{ex.detail['verb']}",
                target=target, payload={"comment": ex.detail["rest"]}, idempotency_key=key,
            )
            executed.append((ex, inv))
        else:
            p = Proposal(
                proposal_id=f"prp_{meeting.meeting_id}_{i}",
                type=ex.kind,
                summary=ex.for_model[:80],
                payload=ex.detail,
                target=TargetRef.parse(ex.detail.get("target", "gitlab:alpha:issue:0")),
                cites=ex.cites,  # AD-33 — the meeting, never the transcript
                created_at=meeting.start,
                state=ProposalState.STAGED,
            )
            daemon.storage.stage_proposal(p)
            staged.append(p)
    return {"executed": executed, "staged": staged, "extractions": results}


# ── The dashboard pipeline (story 23b) ───────────────────────────────────────


def run_dashboard(
    daemon: Daemon, *, scope: DataScope | None = None, now: datetime
) -> Path:
    """Read the day, render it, and write `daily_dashboard.md`. Once, then exit.

    In `app` for `run_harvest`'s reason: it has to touch storage, `core` and the
    scope model at once, which no layer below may do (AD-30). It is also the only
    layer that can reach a connector *and* the renderer, and today's schedule now
    comes from a live calendar read rather than off disk — `core` is I/O-free and
    `render_dashboard` is a pure function, so the composition root reads and the
    renderer renders.

    **Nothing here is a write but the last line.** No cursor is saved, no
    `meetings/` record is mapped, no event is persisted and no event-log entry is
    appended: a render projects truth rather than changing it, and a dashboard is
    fully derivable from Tier 1 plus a clock, so a line per day per scope would
    record nothing a reader could not reconstruct. That is also why `run_harvest`
    is not called from here — a render that also ingests makes the one artifact
    the PM reads at 07:00 a side effect of a write path, and hands a failed
    harvest a second way to spoil the morning.

    **Read and render fully before writing anything.** `write_artifact` replaces
    a file whole, so opening the target first and meeting a malformed goals file
    afterwards destroys yesterday's dashboard. The render is the last thing that
    can fail, and it returns a string.

    Returns the path written, so a surface can say where the file went without
    composing one of its own.
    """
    scope = PERSONAL if scope is None else scope
    _assert_dashboard_scope(scope)
    # First, and before any read. The display zone decides which instants count
    # as today, and it is read once here so that the day boundary the dashboard
    # *shows* and the day boundary its meetings were *selected by* cannot
    # disagree. Unset is refused rather than defaulted to UTC: a PM two zones
    # away would silently get a day that is not theirs.
    zone = _display_zone(daemon)

    meetings = _todays_calendar(daemon, scope=scope, now=now, zone=zone)
    entries = EventLog(daemon.storage).read(scope=scope)

    if scope.kind is ScopeKind.PERSONAL:
        # `read_artifact` answers `None` for an absent file, which `parse_goals`
        # is defined to read as "no file" — distinct from `b""`, a file the PM
        # created and has not filled in. The two need different sentences.
        document = render_dashboard(
            meetings,
            entries,
            parse_goals(
                daemon.storage.read_artifact(scope=scope, artifact=GOALS_ARTIFACT),
                scope=scope,
            ),
            now,
            tz=zone,
        )
    else:
        # A different function, not a flag. `render_project_dashboard` has no
        # goals parameter, so this branch cannot hand it the personal register
        # even by mistake (AD-25) — and no personal-scope artifact is opened on
        # this path at all, which is the half a signature cannot enforce.
        document = render_project_dashboard(meetings, entries, now, tz=zone)

    try:
        return daemon.storage.write_artifact(
            document.encode("utf-8"), scope=scope, artifact=DASHBOARD_ARTIFACT
        )
    except OSError as blocked:
        named = _blocked_by_a_directory(daemon, scope=scope)
        if named is None:
            raise
        raise named from blocked


def _blocked_by_a_directory(
    daemon: Daemon, *, scope: DataScope
) -> IsADirectoryError | None:
    """The write refused because a directory stands where a `File` is declared.

    `None` when it is anything else — a full disk, a read-only mount — which is
    re-raised untouched: pm-ai did not decline those, and dressing them in a
    sentence of its own would put a policy voice over a broken filesystem.

    Named here rather than in the single writer, because the writer's
    `os.replace` reports only `[Errno 21] Is a directory` with no path and no
    idea what was expected. The empty-directory case raises `IsADirectoryError`
    and the non-empty one `ENOTEMPTY`, which is why the discriminator is the
    target's own node type rather than the errno.
    """
    target = daemon.storage.paths.resolve(scope, DASHBOARD_ARTIFACT)
    if not target.is_dir():
        return None
    return IsADirectoryError(
        errno.EISDIR,
        f"{DASHBOARD_ARTIFACT} is declared a File in {scope} and a directory is "
        f"standing where it belongs, so the dashboard could not be written. "
        f"Nothing was changed; move or remove the directory",
        str(target),
    )


def _assert_dashboard_scope(scope: DataScope) -> None:
    """Refuse a scope whose tree declares no dashboard, by name.

    `resolve` would refuse it too, but at the *write*, which is after every read
    and every render — so the operator would pay for a fetch to be told the
    command was never going to work. Named here, and named against the
    declaration rather than a hand-kept list of two scopes.
    """
    if DASHBOARD_ARTIFACT in artifacts_in(scope.kind):
        return
    declared = sorted(
        kind.value for kind in ScopeKind if DASHBOARD_ARTIFACT in artifacts_in(kind)
    )
    raise ScopeResolutionError(
        f"{scope} declares no {DASHBOARD_ARTIFACT}, so there is nowhere in that "
        f"tree for this command to write. It is declared in {', '.join(declared)} "
        f"and nowhere else."
    )


def _display_zone(daemon: Daemon) -> tzinfo:
    """`config.toml`'s `display_timezone`, or a refusal — never a default.

    A typo'd zone never reaches here: `Config.__post_init__` validates the key
    against the zone database when it is loaded, so the only state left to
    handle is unset. `ConfigRefused` is the type for both, which is what keeps
    "the zone is wrong" and "the zone is missing" one outcome to an operator
    reading an exit code.
    """
    named = daemon.config.display_timezone
    if not named:
        raise ConfigRefused(
            "display_timezone is not set in config.toml, so pm-ai does not know "
            "which instants count as today. The dashboard's day boundary and the "
            "calendar read that selects its meetings both depend on it, and "
            "defaulting to UTC would put a meeting on the wrong day with nothing "
            "on the page saying so. Set it to an IANA zone, such as "
            "`display_timezone = \"Europe/Warsaw\"`."
        )
    return ZoneInfo(named)


def _todays_calendar(
    daemon: Daemon, *, scope: DataScope, now: datetime, zone: tzinfo
) -> CalendarAnswer:
    """What every enrolled calendar says about today, as one of four answers.

    **Which connector is asked is derived from what connectors declare.** The
    calendar is whichever enrolled instance's `emits()` contains
    `CALENDAR_EVENT_HELD` — a capability `ConnectorPort` already declares — so
    this slice keeps no second list of vendor names to fall out of step with the
    first.

    **Every calendar that answers is read, and no calendar's silence stops
    another's answer.** Two enrolled connectors is the ordinary two-tenant PM
    rather than a misconfiguration, so the days are merged rather than refused;
    each is asked independently, and one that fails takes down its own rows and
    nothing else.

    **Sorted instance order, so a re-run of unchanged inputs is byte-identical**
    — including which copy of a cross-invited meeting supplies the title and the
    tentative flag.
    """
    instances = sorted(
        instance
        for instance, connector in daemon.connectors.items()
        if ObservedEventType.CALENDAR_EVENT_HELD in connector.emits()
    )
    if not instances:
        # Its own value, never a `HarvestFailure`: that branch of the renderer
        # ends in `_retry_advice`, whose sentence quotes "the connector" — a
        # report attributed to a connector nobody enrolled.
        return NoCalendarConnector()

    day_start, day_end = _day_bounds(now, zone=zone)
    collected: list[Meeting] = []
    unread: list[UnreadCalendar] = []
    answered = 0
    for instance in instances:
        result = _ask(daemon, instance)
        if result.failure is not None:
            unread.append(UnreadCalendar(instance=instance, failure=result.failure))
        else:
            # Counted rather than inferred from whether rows came back. A
            # calendar that answered and held nothing *measured* the day, and a
            # calendar that could not be read did not — the whole distinction the
            # union exists for, and one an empty list cannot carry.
            answered += 1
        # A failed fetch still carries the spans it did walk, and those meetings
        # are real. Data that is present is never discarded because data
        # elsewhere is missing — which is the same rule that makes a partial day
        # a rendered day rather than a refused one.
        #
        # `records` as well as `live`, because the day is the whole day: `live`
        # holds what has not ended and `records` what has, and a dashboard read
        # at 15:00 that showed only the former would report an afternoon of four
        # finished meetings as an empty calendar.
        collected.extend(
            meeting
            for meeting in (*result.records, *result.live)
            if _on_day(meeting, start=day_start, end=day_end)
            and _belongs(meeting, scope=scope)
        )

    meetings = _deduplicate(collected)
    if not unread:
        return meetings
    if answered or meetings:
        # Something was read. **Whether any calendar answered, not whether the
        # merged list is non-empty**: a connector reporting an empty day and a
        # connector that could not be reached are different facts, and collapsing
        # the first into the `HarvestFailure` branch would tell the reader the
        # day is unknown when one calendar measured it and found nothing.
        #
        # `meetings` alone also keeps a partial walk here: a connector that
        # failed on its second span still handed over its first span's rows, and
        # those are present data.
        return PartialCalendar(meetings=meetings, unread=tuple(unread))
    # Nothing was read at all, so there is no query result to report and the
    # renderer must say so rather than show an empty day.
    return _one_failure(unread)


def _ask(daemon: Daemon, instance: str) -> HarvestResult:
    """One connector's harvest, with a contract breach costing only that one.

    `ConnectorPort.harvest` reports rather than raises, and both connectors in
    this build honour it. The guard is here anyway because the whole point of
    asking each calendar independently is that one going wrong must not take the
    others' rows with it — and an exception escaping this loop would do exactly
    that, one line before the meetings that did arrive were rendered.

    The exception's **type** and not its message: this sentence is read by a
    human off a Markdown page, and a provider's own words arriving through an
    unexamined `repr` is how a token ends up in a file.
    """
    try:
        return daemon.connectors[instance].harvest(Cursor())
    except Exception as unexpected:  # noqa: BLE001 — a failure is a value here
        return HarvestResult(
            events=(),
            cursor=Cursor(),
            outcome=HarvestOutcome.FAILED,
            failure=HarvestFailure(
                reason=(
                    f"{instance} raised {type(unexpected).__name__} instead of "
                    f"reporting, which pm-ai cannot classify. That is a bug in "
                    f"pm-ai rather than a verdict about the provider."
                ),
                retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
            ),
        )


def _one_failure(unread: list[UnreadCalendar]) -> HarvestFailure:
    """Every calendar failed, as the single value the renderer's branch takes.

    One unread calendar passes its failure through **verbatim**, hint and all:
    that is the ordinary single-tenant case, and paraphrasing a connector's own
    sentence would lose the `Retry-After` the provider actually sent.

    More than one is joined rather than picked between. Each reason already
    names its own instance, so the sentence stays attributable; `retryable` is
    the conjunction, because telling an operator to wait is only honest if
    waiting clears *all* of them; and `retry_after` is dropped, because no
    provider hinted at a wait covering somebody else's fault and composing one
    would be a number nothing measured.
    """
    if len(unread) == 1:
        return unread[0].failure
    return HarvestFailure(
        reason="; ".join(entry.failure.reason for entry in unread),
        retryable=all(entry.failure.retryable for entry in unread),
    )


def _day_bounds(now: datetime, *, zone: tzinfo) -> tuple[datetime, datetime]:
    """Midnight to midnight in the display zone, as two UTC instants.

    The arithmetic is done on the *local* wall clock and converted afterwards,
    which is what makes a DST day come out at 23 or 25 hours rather than at a
    fixed 24. Adding a day to the UTC instant is the version that gets this
    wrong, silently, twice a year.
    """
    local = now.astimezone(zone)
    opened = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        opened.astimezone(timezone.utc),
        (opened + timedelta(days=1)).astimezone(timezone.utc),
    )


def _on_day(meeting: Meeting, *, start: datetime, end: datetime) -> bool:
    """Whether a meeting touches the display day at all.

    Overlap rather than "starts today", so a meeting that began yesterday
    evening and is still running at 07:00 is on the page. The renderer decides
    nothing about which day it is rendering — it renders what it is handed — so
    dropping that meeting here would be this function silently deciding the PM
    is not in it.

    A zero-length row — an all-day marker, which `33c` records as zero minutes —
    has no span to overlap with, so it is placed by its start.
    """
    if meeting.start >= end:
        return False
    finishes = meeting.start + timedelta(minutes=meeting.duration_minutes)
    if finishes > start:
        return True
    return finishes == meeting.start and meeting.start >= start


def _belongs(meeting: Meeting, *, scope: DataScope) -> bool:
    """Whether this meeting may appear on this scope's dashboard.

    **Asymmetric, deliberately.** A project dashboard carries only meetings the
    connector placed in that project: AD-38 forbids a record written to the
    project scope from referencing personal- or people-scope material, and a
    project file listing the PM's 1:1 is exactly that. The personal dashboard
    carries the PM's whole day, project meetings included, because CAP-9 asks
    for *their* morning briefing and the meetings are all on their calendar —
    filtering them out would empty the dashboard of a PM whose work is all
    categorised, which is the failure mode the honesty rules exist to prevent.

    The wall is one-directional because the leak is.
    """
    if scope.kind is ScopeKind.PERSONAL:
        return True
    return meeting.scope == scope


def _deduplicate(meetings: list[Meeting]) -> tuple[Meeting, ...]:
    """One meeting cross-invited to two tenants, listed once.

    **Matched on identity, never on resemblance.** `meeting_id` is Graph's
    per-mailbox `id`, so the same meeting reaching two tenants arrives under two
    different ids and id-matching alone would deduplicate nothing in the case
    that motivates deduplicating at all. `ical_uid` is the key that survives the
    crossing, and the key is the **pair** `(ical_uid, start)`: whether Graph
    reuses one `iCalUId` across a recurring series' occurrences is unmeasured —
    the 2026-09-06 spike listed the field and never compared two occurrences —
    and the pair is correct either way, because two tenants' copies of one
    meeting share the instant and two occurrences of a series do not.

    The `meeting_id` fallback applies **only when neither side carries a uid**.
    A meeting that carries one has already been judged on it, and tying it to
    another by id as well collapses two meetings whose uids disagree — where a
    disagreeing uid is the evidence that they are different meetings, not a tie
    for the id to break.

    **Never on start and title.** Two organisations each holding a "Weekly Sync"
    at 09:00 is a real double-booking, and it is the single most actionable fact
    on the page — merging it away would hide the one thing the 07:00 reader most
    needs to see.

    The first copy in sorted-instance order is the one kept, so a re-run of
    unchanged inputs is byte-identical down to which copy supplied the title and
    the tentative flag.
    """
    kept: list[Meeting] = []
    seen: set[tuple[str, datetime]] = set()
    seen_unidentified: set[str] = set()
    for meeting in meetings:
        uid = meeting.ical_uid
        if uid:
            key = (uid, meeting.start)
            if key in seen:
                continue
            seen.add(key)
            # Deliberately **not** also recorded by id. A meeting carrying a uid
            # has been judged on it; falling back to the id for it as well is
            # what collapsed two meetings that share an id and disagree about
            # their uid — and a uid that disagrees is evidence of difference.
        else:
            if meeting.meeting_id in seen_unidentified:
                continue
            seen_unidentified.add(meeting.meeting_id)
        kept.append(meeting)
    return tuple(kept)
