# Slice 0 — what a real tenant answered

Run 2026-09-06 against a live Microsoft 365 tenant, delegated device-code flow,
on branch `wave-1/pre-33a-deferred`. The spike itself is throwaway and kept
nothing; these findings are its only output. Two runs were needed — the first
was blocked by a permission the design had not declared.

**Deliberately generalised.** This repository is public and the spike ran
against a real workplace tenant, so what is recorded here is the *shape* of
each answer and the design consequence that follows from it. Tenant
configuration, meeting cadence, subjects, attendees, identifiers and transcript
content are all omitted. Every conclusion below is reproducible by running the
spike against any tenant that permits transcripts.

## Unknown 1 — can a tenant permit Graph transcript access?

**Yes — the permitted path is real and was exercised end to end.**
`GET /me/onlineMeetings/{id}/transcripts` returned **HTTP 200 with a list of
transcripts**, so the calendar → meeting → transcript route the prototype path
assumed is reachable on the delegated flow.

That is a capability finding, not a guarantee about any particular deployment:
`GraphAccessToTranscriptsDisabled` remains a tenant-side switch, so `33e` must
still implement the 403 degradation rather than treat the happy path as the
only one. What this run establishes is that the degradation path is not the
*only* path — which is what the slice's scope depended on.

Transcript object keys, verbatim:

```
callId · contentCorrelationId · createdDateTime · endDateTime · id ·
meetingId · meetingOrganizer · transcriptContentUrl
```

Two things follow:

- **Content is a second fetch.** `transcriptContentUrl` is a pointer; the rows
  carry no text. `33e`'s `/content` call is a separate request per transcript.
- **There is no `startDateTime`.** Only `createdDateTime` and `endDateTime`. A
  transcript is placed in time by when it was recorded, not by when its meeting
  was scheduled.

## The finding that changes a design: one join URL, many transcripts

The probed meeting was a **recurring series**. `GET /me/onlineMeetings?
$filter=JoinWebUrl eq '...'` resolved it to **one** `onlineMeeting`, and that one
object held a transcript **per transcribed occurrence** — many, not one.

So the join URL is stable across a whole series, and `joinUrl → onlineMeeting →
transcripts` does not identify an occurrence. `33e` cannot map a transcript to a
meeting by the join URL alone: it must match on time, using the transcript's
`createdDateTime`/`endDateTime` against the occurrence's window.

The calendar side already carries what that needs — `seriesMasterId` and
`occurrenceId` are both present on the event (see below). This was not visible
from the documentation and would have surfaced as "every occurrence of a
recurring meeting gets the whole series' transcripts attached".

## Unknown 2 — which scopes consent cleanly

All requested scopes were granted and a **refresh token was returned**, so
`33a`'s silent-refresh design is viable on the device-code flow. Consent was
granted by a tenant administrator, so this does *not* establish that an ordinary
user could self-consent — the two permissions that normally require an admin are
`ChannelMessage.Read.All` and `OnlineMeetingTranscript.Read.All`.

**The declared four were not enough.** Reading a resource and *finding* it are
separate permissions in Graph, and the spike hit this twice:

| Permission | Why it is needed | Whose scope set it belongs in |
|---|---|---|
| `Calendars.Read` | the calendar view | `33b`, declared |
| `Chat.Read` | chat messages | `33d`, declared |
| `ChannelMessage.Read.All` | channel message **content** | `33d`, declared |
| `OnlineMeetingTranscript.Read.All` | the transcripts themselves | `33e`, declared |
| `offline_access` | refresh tokens | `33a`, declared |
| **`OnlineMeetings.Read`** | resolve `joinUrl` → meeting id | **`33e` — was missing** |
| `Team.ReadBasic.All` | enumerate teams | `33d`, or an explicit team id in config |
| `Channel.ReadBasic.All` | enumerate channels | `33d`, or an explicit channel id in config |

`OnlineMeetings.Read` is the important one. Without it the meeting lookup
returns **403 `Forbidden` "Insufficient permissions"** and the transcript
endpoint is never reached at all — a 403 that looks exactly like the tenant
switch and is not it. `33e` must declare it, or it will fail before it
can ask the question it exists to ask.

## Unknown 3 — real payload shapes

### calendarView

`GET /me/calendarView` → HTTP 200. Event keys, verbatim:

```
@odata.etag · allowNewTimeProposals · attendees · body · bodyPreview ·
categories · changeKey · createdDateTime · end · hasAttachments · hideAttendees ·
iCalUId · id · importance · isAllDay · isCancelled · isDraft · isOnlineMeeting ·
isOrganizer · isReminderOn · lastModifiedDateTime · location · locations ·
occurrenceId · onlineMeeting · onlineMeetingProvider · onlineMeetingUrl ·
organizer · originalEndTimeZone · originalStartTimeZone · recurrence ·
reminderMinutesBeforeStart · responseRequested · responseStatus · sensitivity ·
seriesMasterId · showAs · start · subject · transactionId · type · uid · webLink
```

**The timestamp confirms `33b`'s conversion is load-bearing.** The returned
shape, with the digits and fields exactly as Graph sent them and the values
replaced — a real start time is tenant data and this repository is public:

```
start.dateTime = "2026-01-02T09:00:00.0000000"      start.timeZone = "UTC"
```

The string is **naive** — no offset — with the zone in a separate field, and it
carries **seven** fractional digits. Measured on this interpreter (3.14.7),
`datetime.fromisoformat` accepts seven digits and returns `tzinfo=None`. So the
failure mode is not a parse error that would announce itself; it is a naive
datetime that silently reads as local time. `33b`'s `{dateTime, timeZone}` →
aware UTC step is the whole job, and omitting it fails quietly.

`isOnlineMeeting`, `onlineMeeting.joinUrl` and `onlineMeetingProvider` are all
present, so the calendar → transcript path `prototype-path` assumed is real.

### Query options are not uniform across collections

Three refusals in one spike, all HTTP 400 `Query option 'Top' is not allowed`:

- `GET /me/joinedTeams` — rejects `$top`
- `GET /teams/{id}/channels` — rejects `$top`
- `GET /teams/{id}/channels/{id}/messages` — **accepts** `$top` (per the docs
  already checked on 2026-09-01: `$top` and `$expand` only, no `$filter`, no
  `$orderby`)

A connector cannot assume a query option is supported because a sibling
collection supports it. `33b` and `33d` should treat paging options as
per-endpoint facts, not as a Graph-wide convention.

### Channel messages — still unmeasured

Not reached: the `$top` refusal on `/channels` stopped the walk before any
message was fetched. The message payload shape, `body.contentType`, and whether
`mentions` is present on the row all remain unknown. This blocks nothing in
wave 1 — channel messages are `33d`, in wave 2 — and the spike is fixed and
re-runnable if `33d` wants the shapes before it is written.

## What this changes

1. **`33e` declares `OnlineMeetings.Read`.** Without it, it 403s before reaching
   a transcript.
2. **`33e` matches transcripts to occurrences by time, not by join URL.** One
   series resolves to one meeting holding every occurrence's transcript.
3. **`33e` fetches content separately** via `transcriptContentUrl`.
4. **`33b`'s timezone conversion is confirmed necessary**, and its failure mode
   is silent rather than loud.
5. **`33d` needs `Team.ReadBasic.All` and `Channel.ReadBasic.All`**, or explicit
   team and channel ids in configuration — enumeration is not covered by
   `ChannelMessage.Read.All`.
6. **The transcript degradation path stays in `33e`** as a handled case. The
   permitted path is confirmed reachable, so it is not the only one — but the
   tenant-side switch is real and a 403 must degrade rather than retry.
