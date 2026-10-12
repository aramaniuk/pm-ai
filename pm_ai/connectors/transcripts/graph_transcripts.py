"""Microsoft Graph transcript adapter — the primary path (AD-23, story 33e).

Acquisition only: this finds a meeting's transcript, downloads it and hands it
back verbatim. Reading it into utterances and speakers is `11b`'s.

## The path, as slice 0 measured it against a real tenant

    calendar event id ──GET /me/events/{id}──▶ join link (never stored)
    join link ──GET /me/onlineMeetings?$filter=JoinWebUrl eq '…'──▶ one online meeting
    online meeting ──GET …/transcripts──▶ one transcript per recorded occurrence
    transcript ──GET …/transcripts/{id}/content──▶ WebVTT

A series is **one** online meeting holding a transcript per recorded occurrence,
so a transcript is matched to an occurrence by time, never by the link: it
belongs to the occurrence whose window — start to end, widened by
`PLACEMENT_SLACK` either side — contains the instant it was created, provided
it ended after the occurrence started. Meetings sharing a link are resolved and
listed once.

## What stops a run, and what does not

A provider fault — a throttle, a server error, no answer, or a credential that
no longer signs in — stops the run at the meeting it struck: that meeting is
refused retryably with the provider's hint, and the meetings after it are not
reached, so they wait for the next run with nothing recorded. Everything else is
an answer about one meeting and the run continues: a tenant that switched
transcript access off, a permission missing, a meeting not the PM's to read, a
transcript that does not fit.

A credential failure is retryable here although the calendar treats most as
final: a refusal that is not retryable closes the meeting for good, and a
signed-out connector is repaired by signing in, after which the meeting must
still be fetchable.

Read-only (AD-1 class H) over the Graph client, with its origin check, page cap,
throttle handling and token refresh. The content is requested at the documented
`/content` path built from the two ids, never by following
`transcriptContentUrl`: the client refuses what it cannot check against its
origin.

**The listing's early stop is earned, not assumed.** The spec states Graph lists
transcripts newest first, and the endpoint accepts `$top` and no ordering
option, so nothing *requests* that order. The stop rule — no further page once
a transcript older than the reach-back's start less `LISTING_SLACK` is seen —
is only sound under it: listed oldest first, the first page would hold the old
rows and the recent ones would sit on pages never fetched. So the order is
checked on every row read: while `createdDateTime` never increases the early
stop applies, and the first row newer than one before it switches it off for
that listing, which is then paged to the client's cap. A long series listed in
another order is refused by name at the cap rather than matched against a
listing that silently stopped short.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from pm_ai.connectors.graph.auth import GraphAuthError
from pm_ai.connectors.graph.client import (
    ConsentChanged,
    GraphCallFailed,
    GraphClient,
    GraphProtocolError,
    GraphRefused,
    TRANSCRIPTS_DISABLED,
    PageCapReached,
)
from pm_ai.domain.harvest import UNCLASSIFIED_FAULT_IS_RETRYABLE
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.transcripts import (
    CaptureFormat,
    TranscriptAbsent,
    TranscriptCapture,
    TranscriptCaptured,
    TranscriptOutcome,
    TranscriptRefused,
    TranscriptSkipped,
)
from pm_ai.ports import TranscriptSourceUnavailable

__all__ = [
    "CALENDAR_PERMISSION",
    "EVENT_FIELDS",
    "GraphTranscriptAdapter",
    "LISTING_SLACK",
    "LOOKUP_PERMISSION",
    "PLACEMENT_SLACK",
    "SPEAKER_ATTRIBUTION_OFF",
    "TEAMS_PROVIDER",
    "TENANT_SWITCH",
    "TRANSCRIPT_PERMISSION",
    "UNATTRIBUTED",
    "UnenrolledGraphTranscripts",
    "VTT",
]


PLACEMENT_SLACK = timedelta(minutes=15)
"""How far a transcript's created instant may fall outside its occurrence's slot.

Widens a placement window, start to end. `11b`'s 15 minutes is a start-to-start
match tolerance for a dropped file — two figures that happen to agree, not one
rule. A meeting that overran its slot by more than this is refused by name
rather than guessed (an admitted limit; `11c`'s drop is the repair).
"""

LISTING_SLACK = timedelta(minutes=15)
"""The listing stops once a transcript was created earlier than the reach-back's
start less this — older than any occurrence the run is asking about could own."""

TEAMS_PROVIDER = "teamsForBusiness"
"""Graph's `onlineMeetingProvider` for a Teams meeting — the only kind with a transcript here."""

VTT = "text/vtt"
UNATTRIBUTED = "application/vnd.microsoft.graph.transcript+text"
"""Microsoft's unattributed format: cue timings and words, no voice tags, no
`WEBVTT` header. Asked for by `Accept` only, never `$format` (Graph reference,
checked 2026-10-10)."""

TENANT_SWITCH = TRANSCRIPTS_DISABLED
"""The detailed code of a 403 from a tenant that switched transcript access off.
There is no request-side workaround, so it is never retried."""

SPEAKER_ATTRIBUTION_OFF = "SpeakerAttributionNotAllowed"
"""The detailed code of a 403 on `/content` only: the tenant forbids speaker
names, and the unattributed format is asked for once instead."""

LOOKUP_PERMISSION = "OnlineMeetings.Read"
TRANSCRIPT_PERMISSION = "OnlineMeetingTranscript.Read.All"
CALENDAR_PERMISSION = "Calendars.Read"

EVENT_FIELDS = "isOnlineMeeting,onlineMeeting,onlineMeetingProvider,start,end,type"
"""The event's `$select`: whether it is an online meeting, its join link, and
`type` — which tells a one-off (`singleInstance`) from a series' occurrence."""

_ONE_OFF = "singleInstance"

_EXPIRED = "expired"
"""Looked for in the provider's error *codes*, never its message: a code is an
identifier, the message is provider prose that never reaches a durable reason."""

_NOT_READABLE = (
    "Graph answered the join-link lookup with no meeting: it is not the PM's to "
    "read (not organiser or invited) or it has expired."
)


# ── What one request taught, before it becomes an outcome ────────────────────


@dataclass(frozen=True, slots=True)
class _Verdict:
    """An answer about every meeting behind one event, link or listing."""

    kind: str
    reason: str
    retryable: bool = False

    def for_meeting(self, meeting_id: str) -> TranscriptOutcome:
        if self.kind == "absent":
            return TranscriptAbsent(meeting_id, self.reason)
        if self.kind == "skipped":
            return TranscriptSkipped(meeting_id, self.reason)
        return TranscriptRefused(meeting_id, self.reason, retryable=self.retryable)


def _absent(reason: str) -> _Verdict:
    return _Verdict("absent", reason)


def _refused(reason: str) -> _Verdict:
    return _Verdict("refused", reason, retryable=False)


@dataclass(frozen=True, slots=True)
class _Event:
    """The parts of a calendar event acquisition reads. `join_url` is `None`
    for anything that is not an online Teams meeting."""

    join_url: str | None
    kind: str | None


@dataclass(frozen=True, slots=True)
class _Listed:
    """One transcript as the listing names it. No text: that is a second fetch."""

    transcript_id: str
    created: datetime | None
    ended: datetime | None


class _ProviderFault(Exception):
    """A fault that stops the run: the provider, not the meeting, is the problem."""

    def __init__(self, reason: str, retry_after: timedelta | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


# ── The adapters ─────────────────────────────────────────────────────────────


@dataclass
class GraphTranscriptAdapter:
    """`TranscriptSourcePort` over Microsoft Graph, through one connector's client.

    Built by the composition root from the enrolled Graph connector's auth and
    client, so it spends the same credential under the same origin check, page
    cap, throttle handling and budget. Holds no state between runs: where each
    meeting got to is the operational store's, kept by
    `pm_ai.core.transcript_acquisition`.
    """

    client: GraphClient
    instance: str
    """The connector instance whose credential this spends."""
    name: str = "graph"
    requires_network: bool = True

    def acquire(
        self, meetings: Sequence[Meeting], *, since: datetime
    ) -> tuple[TranscriptOutcome, ...]:
        """One outcome per meeting reached, in order; a provider fault ends the run."""
        if since.tzinfo is None or since.utcoffset() != timedelta(0):
            raise ValueError(
                f"since={since!r} is not aware UTC; it is compared with the "
                f"provider's UTC instants"
            )
        return _Run(client=self.client, meetings=tuple(meetings), since=since).go()


@dataclass
class UnenrolledGraphTranscripts:
    """The daemon's Graph transcript source when no Graph connector is enrolled.

    Refuses by name rather than leaving the key out, so a caller holding the
    port meets one sentence instead of a `KeyError`.
    """

    name: str = "graph"
    requires_network: bool = True

    def acquire(
        self, meetings: Sequence[Meeting], *, since: datetime
    ) -> tuple[TranscriptOutcome, ...]:
        raise TranscriptSourceUnavailable(
            "no Graph connector is enrolled, so there is no Microsoft account to "
            "ask for a meeting's transcript. Enrol one with `pm-ai connector add "
            "graph`; nothing was asked and no meeting's state was recorded."
        )


@dataclass
class _Run:
    """One acquisition: the caches that make a series resolve and list once."""

    client: GraphClient
    meetings: tuple[Meeting, ...]
    since: datetime
    events: dict[str, _Event | _Verdict] = field(default_factory=dict)
    online: dict[str, str | _Verdict] = field(default_factory=dict)
    listings: dict[str, tuple[_Listed, ...] | _Verdict] = field(default_factory=dict)

    def go(self) -> tuple[TranscriptOutcome, ...]:
        outcomes: list[TranscriptOutcome] = []
        for meeting in self.meetings:
            try:
                outcomes.append(self._settle(meeting))
            except _ProviderFault as fault:
                outcomes.append(
                    TranscriptRefused(
                        meeting.meeting_id,
                        f"{fault.reason} The meetings after this one were not "
                        f"asked about and wait for the next run.",
                        retryable=True,
                        retry_after=fault.retry_after,
                    )
                )
                break
            except Exception as unexpected:  # noqa: BLE001 — one meeting, not the run
                # A bug in this adapter, or an answer it has no reading for: that
                # meeting is refused naming the type, and the run continues. The
                # type and never the message, which may carry provider text.
                outcomes.append(
                    TranscriptRefused(
                        meeting.meeting_id,
                        f"acquiring the transcript raised "
                        f"{type(unexpected).__name__}, which pm-ai cannot classify.",
                        retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
                    )
                )
        return tuple(outcomes)

    # ── One meeting ──────────────────────────────────────────────────────────

    def _settle(self, meeting: Meeting) -> TranscriptOutcome:
        if not meeting.calendar_event_ref:
            return TranscriptAbsent(
                meeting.meeting_id,
                "the record holds no calendar reference, so there is no event "
                "to resolve a join link from.",
            )
        event = self._event(meeting)
        if isinstance(event, _Verdict):
            return event.for_meeting(meeting.meeting_id)
        if event.join_url is None:
            return TranscriptAbsent(
                meeting.meeting_id, "not an online Teams meeting, so it has no transcript."
            )
        online = self._online(event.join_url)
        if isinstance(online, _Verdict):
            return online.for_meeting(meeting.meeting_id)
        listing = self._listing(online)
        if isinstance(listing, _Verdict):
            return listing.for_meeting(meeting.meeting_id)
        return self._match(meeting, event, online, listing)

    def _match(
        self,
        meeting: Meeting,
        event: _Event,
        online_id: str,
        listing: tuple[_Listed, ...],
    ) -> TranscriptOutcome:
        mid = meeting.meeting_id
        floor = self.since - LISTING_SLACK
        listed = [t for t in listing if t.created is None or t.created >= floor]
        mine = [t for t in listed if _fits(t, meeting)]
        unplaced = [t for t in listed if t.created is None]

        if event.kind == _ONE_OFF:
            # A one-off meeting's online meeting holds only its own recordings,
            # so one that fits nowhere means the time match has failed.
            stray = [t for t in listed if t.created is not None and t not in mine]
            if stray:
                return TranscriptRefused(
                    mid,
                    f"online meeting {online_id} holds transcript(s) "
                    f"{_ids(stray)} created outside this one-off meeting's "
                    f"window, so which recording is whose cannot be decided; "
                    f"nothing was stored.",
                    retryable=False,
                )
        if len(mine) > 1:
            return TranscriptRefused(
                mid,
                f"transcripts {_ids(mine)} of online meeting {online_id} all fit "
                f"this meeting — a recording restarted — so none is stored.",
                retryable=False,
            )
        if not mine:
            if unplaced:
                return TranscriptSkipped(
                    mid,
                    f"transcript(s) {_ids(unplaced)} of online meeting "
                    f"{online_id} have no created instant yet: still being "
                    f"produced.",
                )
            return TranscriptAbsent(mid, "no transcript was recorded for this meeting.")
        (transcript,) = mine
        if transcript.ended is None:
            return TranscriptSkipped(
                mid,
                f"transcript {transcript.transcript_id} has no end instant yet: "
                f"Teams is still producing it.",
            )
        shared = [
            other.meeting_id
            for other in self.meetings
            if other.meeting_id != mid
            and _fits(transcript, other)
            and self._same_link(other, event.join_url)
        ]
        if shared:
            return TranscriptRefused(
                mid,
                f"transcript {transcript.transcript_id} of online meeting "
                f"{online_id} fits this meeting and {', '.join(shared)}, so it "
                f"is stored for none of them.",
                retryable=False,
            )
        return self._download(mid, online_id, transcript)

    def _same_link(self, other: Meeting, join_url: str | None) -> bool:
        if not other.calendar_event_ref:
            return False
        event = self._event(other)
        return isinstance(event, _Event) and event.join_url == join_url

    # ── The four requests ────────────────────────────────────────────────────

    def _event(self, meeting: Meeting) -> _Event | _Verdict:
        cached = self.events.get(meeting.meeting_id)
        if cached is not None:
            return cached
        ref = meeting.calendar_event_ref or ""
        url = f"{self.client.base}/me/events/{quote(ref, safe='')}?$select={EVENT_FIELDS}"
        answer: _Event | _Verdict
        try:
            body = self._page(url)
        except ConsentChanged as refused:
            answer = _permission(refused, CALENDAR_PERMISSION, "the calendar event")
        except GraphRefused as refused:
            if refused.status == 404:
                answer = _refused(
                    "the calendar event this record names is gone (404), so its "
                    "join link cannot be resolved."
                )
            else:
                answer = _rejected(refused, "the calendar event")
        except GraphProtocolError as refused:
            answer = _refused(f"the calendar event could not be read: {refused.reason}")
        else:
            answer = _event_of(body)
        self.events[meeting.meeting_id] = answer
        return answer

    def _online(self, join_url: str) -> str | _Verdict:
        cached = self.online.get(join_url)
        if cached is not None:
            return cached
        # OData quotes a literal with `'` and escapes one inside it by doubling.
        literal = join_url.replace("'", "''")
        criterion = f"JoinWebUrl eq '{literal}'"
        url = f"{self.client.base}/me/onlineMeetings?$filter={quote(criterion, safe='')}"
        answer: str | _Verdict
        try:
            body = self._page(url)
        except ConsentChanged as refused:
            answer = _permission(refused, LOOKUP_PERMISSION, "the online-meeting lookup")
        except GraphRefused as refused:
            if refused.status == 404 or _says_expired(refused):
                answer = _absent(_NOT_READABLE)
            else:
                answer = _rejected(refused, "the online-meeting lookup")
        except GraphProtocolError as refused:
            answer = _refused(f"the online-meeting lookup could not be read: {refused.reason}")
        else:
            answer = _resolved(body)
        self.online[join_url] = answer
        return answer

    def _listing(self, online_id: str) -> tuple[_Listed, ...] | _Verdict:
        cached = self.listings.get(online_id)
        if cached is not None:
            return cached
        url = f"{self.client.base}/me/onlineMeetings/{quote(online_id, safe='')}/transcripts"
        floor = self.since - LISTING_SLACK
        listed: list[_Listed] = []
        newest_first = True
        previous: datetime | None = None
        answer: tuple[_Listed, ...] | _Verdict
        try:
            for body in self._walk(url):
                rows = body.get("value")
                if not isinstance(rows, list):
                    raise GraphProtocolError("a transcripts page carried no `value` list.")
                for row in rows:
                    item = _listed(row)
                    if item is None:
                        raise GraphProtocolError("a listed transcript carried no usable id.")
                    listed.append(item)
                    if item.created is not None:
                        if previous is not None and item.created > previous:
                            newest_first = False
                        previous = item.created
                if newest_first and any(
                    item.created is not None and item.created < floor for item in listed
                ):
                    # Newest first, as every row so far confirms: everything past
                    # here is older than any occurrence this run asks about, so no
                    # further page is fetched.
                    break
        except PageCapReached:
            answer = _refused(
                f"the transcripts of online meeting {online_id} run past the "
                f"{self.client.page_cap}-page cap before reaching the start of "
                f"the reach-back, so the listing is incomplete and nothing is "
                f"matched against it."
            )
        except ConsentChanged as refused:
            answer = _permission(refused, TRANSCRIPT_PERMISSION, "the transcript listing")
        except GraphRefused as refused:
            if refused.status == 404 or _says_expired(refused):
                answer = _absent(
                    "Graph says this meeting's transcripts are not the PM's to "
                    "read, or the meeting has expired."
                )
            else:
                answer = _rejected(refused, "the transcript listing")
        except GraphProtocolError as refused:
            answer = _refused(
                f"the transcripts of online meeting {online_id} could not be "
                f"read: {refused.reason}"
            )
        else:
            answer = tuple(listed)
        self.listings[online_id] = answer
        return answer

    def _download(
        self, meeting_id: str, online_id: str, transcript: _Listed
    ) -> TranscriptOutcome:
        if transcript.created is None or transcript.ended is None:
            # Unreachable: `_match` placed it by `created` and skipped it without
            # `ended`. Said rather than asserted, so `python -O` cannot remove it.
            return TranscriptSkipped(meeting_id, "the transcript is still being produced.")
        url = (
            f"{self.client.base}/me/onlineMeetings/{quote(online_id, safe='')}"
            f"/transcripts/{quote(transcript.transcript_id, safe='')}/content"
        )
        attributed = True
        try:
            try:
                raw = self._bytes(url, accept=VTT)
            except ConsentChanged as refused:
                if not refused.names(SPEAKER_ATTRIBUTION_OFF):
                    raise
                # The tenant forbids speaker names on transcripts. Asked once
                # more for the unattributed format: timings and words, no names.
                attributed = False
                try:
                    raw = self._bytes(url, accept=UNATTRIBUTED)
                except ConsentChanged as again:
                    if not again.names(SPEAKER_ATTRIBUTION_OFF):
                        raise
                    return TranscriptRefused(
                        meeting_id,
                        f"the tenant forbids speaker attribution "
                        f"({SPEAKER_ATTRIBUTION_OFF}) and refused transcript "
                        f"{transcript.transcript_id} in the unattributed format "
                        f"as well, so there is no form of it pm-ai may read.",
                        retryable=False,
                    )
        except ConsentChanged as refused:
            verdict = _permission(refused, TRANSCRIPT_PERMISSION, "the transcript download")
            return verdict.for_meeting(meeting_id)
        except GraphRefused as refused:
            if _says_expired(refused):
                return TranscriptAbsent(
                    meeting_id,
                    "Graph says the meeting has expired, so its transcript can no "
                    "longer be read.",
                )
            if refused.status == 404:
                return TranscriptRefused(
                    meeting_id,
                    f"transcript {transcript.transcript_id} was listed and its "
                    f"content answered 404: it is gone.",
                    retryable=False,
                )
            return _rejected(refused, "the transcript download").for_meeting(meeting_id)
        except GraphProtocolError as refused:
            return TranscriptRefused(
                meeting_id,
                f"transcript {transcript.transcript_id} could not be downloaded: "
                f"{refused.reason}",
                retryable=False,
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return TranscriptRefused(
                meeting_id,
                f"transcript {transcript.transcript_id}'s content is not UTF-8 "
                f"text, so it is not stored.",
                retryable=False,
            )
        if not text.strip():
            return TranscriptRefused(
                meeting_id,
                f"transcript {transcript.transcript_id}'s content was empty — "
                f"Teams may still be writing it — so it is tried again next run.",
                retryable=True,
            )
        return TranscriptCaptured(
            meeting_id,
            TranscriptCapture(
                meeting_id=meeting_id,
                transcript_id=transcript.transcript_id,
                created=transcript.created,
                ended=transcript.ended,
                text=text,
                attributed=attributed,
                format=CaptureFormat.GRAPH,
            ),
        )

    # ── Transport, with the faults that stop a run lifted out ────────────────

    def _page(self, url: str) -> Mapping[str, Any]:
        page: Mapping[str, Any] = self._guarded(lambda: self.client.page(url))
        return page

    def _bytes(self, url: str, *, accept: str) -> bytes:
        raw: bytes = self._guarded(lambda: self.client.download(url, accept=accept))
        return raw

    def _walk(self, url: str) -> Iterator[Mapping[str, Any]]:
        pages = self.client.walk(url)
        while True:
            try:
                body: Mapping[str, Any] = self._guarded(lambda: next(pages))
            except StopIteration:
                return
            yield body

    def _guarded(self, call: Callable[[], Any]) -> Any:
        """Run one request; a fault that waiting clears becomes `_ProviderFault`."""
        try:
            return call()
        except GraphCallFailed as failed:
            if failed.retryable:
                raise _ProviderFault(failed.reason, failed.retry_after) from failed
            raise
        except GraphAuthError as unauthenticated:
            raise _ProviderFault(
                f"the Graph credential could not be used "
                f"({type(unauthenticated).__name__}); sign the connector in again "
                f"with `pm-ai connector sign-in`.",
                None,
            ) from unauthenticated


# ── Reading the provider's answers ───────────────────────────────────────────


def _fits(transcript: _Listed, meeting: Meeting) -> bool:
    """Created inside the occurrence's widened window, and not ended before it began."""
    if transcript.created is None:
        return False
    begin = meeting.start - PLACEMENT_SLACK
    end = meeting.start + timedelta(minutes=meeting.duration_minutes) + PLACEMENT_SLACK
    if not begin <= transcript.created <= end:
        return False
    return transcript.ended is None or transcript.ended > meeting.start


def _event_of(body: Mapping[str, Any]) -> _Event:
    kind = body.get("type")
    online = body.get("onlineMeeting")
    join = online.get("joinUrl") if isinstance(online, Mapping) else None
    teams = (
        body.get("isOnlineMeeting") is True
        and body.get("onlineMeetingProvider") == TEAMS_PROVIDER
        and isinstance(join, str)
        and join.strip().lower().startswith("https://")
    )
    return _Event(
        join_url=join.strip() if teams and isinstance(join, str) else None,
        kind=kind if isinstance(kind, str) else None,
    )


def _resolved(body: Mapping[str, Any]) -> str | _Verdict:
    """The one online meeting a join-link lookup answered, or why not."""
    value = body.get("value")
    if not isinstance(value, list):
        return _refused("the online-meeting lookup answered without a `value` list.")
    ids = [item.get("id") if isinstance(item, Mapping) else None for item in value]
    usable = [item for item in ids if isinstance(item, str) and item]
    if len(usable) != len(ids):
        return _refused("the online-meeting lookup answered a meeting with no usable id.")
    if not usable:
        return _absent(_NOT_READABLE)
    if len(usable) > 1:
        return _refused(
            f"one join link resolved to {len(usable)} online meetings "
            f"({', '.join(usable)}), so which one holds this meeting's transcript "
            f"cannot be decided."
        )
    return usable[0]


def _listed(row: object) -> _Listed | None:
    if not isinstance(row, Mapping):
        return None
    transcript_id = row.get("id")
    if not isinstance(transcript_id, str) or not transcript_id.strip():
        return None
    return _Listed(
        transcript_id=transcript_id,
        created=_instant(row.get("createdDateTime")),
        ended=_instant(row.get("endDateTime")),
    )


def _instant(value: object) -> datetime | None:
    """A Graph instant as aware UTC, or `None` when absent, unreadable or a sentinel.

    Graph sends seven fractional digits and a `Z`; the fraction is trimmed to
    the six `datetime` holds. An instant without an offset is read as UTC — the
    transcript resource documents both of its instants as UTC.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, _, tail = text.partition(".")
        span = 0
        while span < len(tail) and tail[span].isdigit():
            span += 1
        digits, remainder = tail[:span], tail[span:]
        text = f"{head}.{digits[:6]}{remainder}" if digits else head + remainder
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed.year <= 1:
        # Graph's sentinel for "not yet": `0001-01-01T00:00:00Z` on a
        # transcript still being produced. Absent, never an instant to place.
        return None
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _says_expired(refused: GraphCallFailed) -> bool:
    return any(_EXPIRED in code.casefold() for code in refused.codes)


def _permission(refused: ConsentChanged, permission: str, what: str) -> _Verdict:
    """A 403, read by its detailed code: the tenant switch, or a missing permission."""
    if refused.names(TENANT_SWITCH):
        return _refused(
            f"the tenant has switched Graph access to transcripts off "
            f"({TENANT_SWITCH}); there is no way round it from pm-ai, so this "
            f"meeting is not asked about again."
        )
    return _refused(
        f"Graph refused {what} with 403: the {permission} permission is not "
        f"granted. It is in the declared set and enrolment refuses a partial "
        f"grant, so this enrolment predates it — enrol the connector again."
    )


def _rejected(refused: GraphCallFailed, what: str) -> _Verdict:
    status = f" {refused.status}" if refused.status is not None else ""
    codes = f" ({', '.join(refused.codes)})" if refused.codes else ""
    return _refused(f"Graph rejected {what} with{status}{codes}.")


def _ids(transcripts: Sequence[_Listed]) -> str:
    return ", ".join(transcript.transcript_id for transcript in transcripts)
