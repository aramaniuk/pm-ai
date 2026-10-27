## Time-Critical Activities

- **09:00** 1:1 with Dana — ended, 45 min, 1 attendee
- **09:00** Standup — ended, 30 min, 2 attendees
- **13:00** Payments Gateway Sync — upcoming, 60 min, 5 attendees, your response is tentative

Open commitments, soonest due first:

- **due Thu 22 Oct, past due** Sam Rivera: send the cache TTL numbers ("by Thursday") — from Example Weekly Sync-up, Mon 19 Oct (`project:alpha`)
- **due Fri 30 Oct (meeting day estimated)** Alex Morgan: book the load test — from Example Weekly Sync-up, Mon 19 Oct
- **due Fri 30 Oct, set by you** said by Sam Rivera: share the draft — altered on Sat 24 Oct — from Example Weekly Sync-up, Mon 19 Oct
- **no due date worked out from "next sprint"** Alex Morgan: migrate the cache — from Example Weekly Sync-up, Mon 19 Oct
- **due cleared by you** unassigned: write the notes — pm-ai could not check by telemetry — from Example Weekly Sync-up, Mon 19 Oct
- to mark or correct them: `pm-ai commitment review --scope personal`, `pm-ai commitment review --scope project:alpha`
- closed since 2026-09-09 07:00: 2 done (1 marked by you, 1 by telemetry), 1 withdrawn (marked by you): `pm-ai commitment list --scope personal --state all`

commitments_log.md in personal: 1 line could not be read: `pm-ai commitment list --scope personal`.

1 approved item in `personal` is not yet recorded as a commitment: `pm-ai proposal approve prp_older --scope personal`.

`project:gone` is no longer enrolled, so its commitments are not listed.

Meeting summaries recorded between 2026-09-08 12:30 and 2026-09-09 12:30 — in the last 24 hours (no earlier render recorded), quoted from the meeting records:

- **09:00** Release sync — The team planned the release. (made by the local model qwen3:8b, automatically)
  - Ваня: send the deck (Friday), to review by 2026-09-23
  - unassigned: book the room, approved, not yet recorded as a commitment: `pm-ai proposal approve prp_room --scope personal`
  - 3 commitments from this meeting, 2 open: `pm-ai commitment list --scope personal --meeting mtg_a --state all`
  - to review: `pm-ai proposal review --scope personal --meeting mtg_a`
  - meeting `mtg_a`; to ask for a summary: `pm-ai transcript summarize --scope personal --meeting mtg_a --again`
- **09:00** Alpha planning (`project:alpha`) — The team planned the release. (made by Claude claude-sonnet-5-5, on request)
  - 1 commitment from this meeting, closed: `pm-ai commitment list --scope project:alpha --meeting mtg_c --state all`
  - meeting `mtg_c`; to ask for a summary: `pm-ai transcript summarize --scope project:alpha --meeting mtg_c --again`

## Proactive Enablement

Message signals in the last 24 hours (no earlier render recorded), up to 2026-09-09 12:30:

- **11:40** u_sam in #oncall
- **08:15** u_dana in #payments — Cache TTL is still 60s in staging

## 3-Tier Strategic Milestones

- **Project**
  - `goal:g_payments_latency` (short) Cut payment latency below 200ms
- **Team**
  - `goal:g_oncall_load` (medium) Halve the on-call pages per week
- **Personal**
  - `goal:g_staff_eng` (long) Reach staff engineer

## Leadership Notes

No synthesis runs when this dashboard is rendered: it is a deterministic function with no model in its path. The meeting summaries under Time-Critical Activities are quoted from their meeting records, each naming the model that wrote it.

Frontier spend this month (2026-09, UTC): $4.25 across 2 calls of known cost, against the $20.00 monitored target, and 1 call of unknown cost. With calls of unknown cost, whether the month is past the $20.00 target cannot be told.
