---
title: 'Teams channel messages reach the project event log'
type: 'feature'
created: '2026-10-09'
status: 'done'
baseline_commit: '57320f4adfbca18bdca441fb11ad5fafdb36c066'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The dashboard's Proactive Enablement section can only say that no connector in this build writes message entries. The Graph connector reads the PM's calendar and nothing else, so what people post in a project's Teams channels — including messages that name the PM — never reaches pm-ai.

**Approach:** The Graph connector also reads the Teams channels the PM has listed by hand in its settings file, turns each message's HTML into plain text, records the sender and everyone mentioned as person handles around Microsoft's own user ids, and writes one `message_posted` entry per message and reply into that project's event log. Chats are the next slice: a different endpoint, different paging, a scope the PM chooses per chat.

## Boundaries & Constraints

**Always:**
- **Which channels are read is written by hand** in the connector's settings file: a list of entries, each with a team id, a channel id, the enrolled project the channel belongs to, and an optional label for the dashboard. pm-ai never lists teams or channels. No list means calendar only, as today. A list that is not a list, an entry missing its team or channel id, a project that is not enrolled, or a channel id listed twice under any team or project, stops the connector being built and names the entry.
- **A channel message belongs to its project** (AD-4): its project's event log, never the personal one.
- **No new permission.** The channel-message permission is already asked for at sign-in; the declared set does not change, so nobody consents again.
- **Two timestamps, two jobs.** Microsoft returns channel messages newest first, fifty per page, ordered by when each thread's first post was created (measured on a real page, PR #72; amended by `33h`), so first-post creation time drives the cut: the walk stops after the first page whose readable first posts are in order and the oldest of them was created strictly before a horizon 90 days before the channel's position; once the order breaks, only on a page whose every readable first post is older than the horizon. Replies in threads started before the horizon are an admitted limit. A message's own creation time drives emission: created at or after the position, duplicates dropped by the natural key (AD-34). The position is the channel's stored one, or the first-run reach-back the row already holds.
- **Replies are events too** (decided 2026-10-09): they arrive inline with their thread, and mentions of the PM mostly land in them. Microsoft returns up to two hundred inline and a link for more; a thread with more is emitted as far as it got and refused by name — no second reply paging here.
- **Which rows are messages.** Only a row Microsoft types as a message: system events, typing notices, chat events and unknown types are skipped — not emitted, not refused. A row with no id, no body, a body neither HTML nor text, or a creation time missing, unparseable or without a timezone is refused by name; the page continues. A message edited or reacted to after harvest is not emitted again.
- **After a complete walk the channel's position is the newest creation time seen.** When the page cap or the time budget stops a walk short, the position still advances to the newest message seen, and the failure names the channel and says which loss it is — a stated loss, never a silent one (amended by `33h`): cut before any first post older than the position was read, messages and replies created after the position in threads the walk did not reach were skipped, retryable; cut after such first posts but before the horizon, only replies in threads started between the horizon and the oldest first post walked were skipped, the failure names the horizon, not retryable. An empty page that still carries a next-page link does not stop the walk.
- **One time budget per harvest**, shared by the calendar and every channel.
- **The cursor carries one position per resource.** A cursor written before this slice, calendar only, still reads; channels then start from a first run.
- **One message, one event:** `message_posted`; the provider's creation time as `occurred_at` (implausible is flagged, never replaced); the sender as the person handle `graph:<user id>` from `2n`'s producer — never resolved through the alias table when written, since a person is matched only when read (AD-34, Andrei 2026-10-11); beside it in the payload, the sender's display name as Microsoft sent it with the same branch of `from` that gave the handle, for display only and never compared, absent for `none:` (`23f`); the reference `graph:<project>:message:<channel id>/<message id>`; provenance unknown; no id minted (AD-34, AD-36). `channel` in the payload is the label, or the channel id without one.
- **The body becomes plain text with the standard library**, no new dependency: tags and attributes dropped, all character data kept, entities decoded, block boundaries as line breaks; a body sent as text is taken as is. The excerpt is the first 280 characters (decided 2026-10-09 — an excerpt is short), cut and never cleaned: stored as posted (AD-29); the whole message stays in Teams, where the reference points. A mentioned person's name inside the text stays in the excerpt as text a person typed, untrusted.
- **`mentions` holds a person handle `graph:<user id>` for each person mentioned**, built by `2n`'s producer from Microsoft's user id (AD-34, Andrei 2026-10-11; was raw ids "as Microsoft sent them"). The field and its trusted declaration arrive with the grammar slice; this slice fills it, and no display name is ever recorded in it. A mention with no user id — a channel, team, tag or bot — is left out; the text still carries its words. `mentions` is absent when Microsoft sent no list, explicitly empty when it named nobody. Whether the PM was mentioned is derived when read, never stored.
- **The text fields stay declared untrusted**, sanitized at the model boundary, not here (AD-12).
- **Every result that reached the provider says when it first answered** (AD-9, 2026-10-09): the instant the calendar fetch already measures becomes the result's answer instant, from whichever resource answered first.
- **One coverage window for the whole run**, built here: from the first resource's answer instant to the end of the last resource's walk, withheld entirely when any listed resource failed. Stricter on purpose than "a partial walk keeps its window": the connector may not vouch for a resource it could not read, and the calendar's window is not separable while the result carries one. The per-resource limitation goes to deferred work.
- **Failures are per resource; the run's one failure is honest about all of them.** A permission refused on a listed channel means pm-ai is not a member of it or lacks access: non-retryable, the remedy names the channel — not the tenant-wide consent-change reading a 403 gets elsewhere. A mistyped team or channel id (not-found or bad-request) fails that channel only, non-retryable, naming the entry. The calendar and the other channels still harvest and advance. Several failures: retryable only if every one is, carrying the largest retry hint. A sign-in failure on the calendar — stale credential, declined consent — ends the run; no later resource is attempted.
- **The connector declares exactly `{calendar_event_held, message_posted}`**, and the dashboard's "no connector in this build writes message entries" sentence is deleted.
- **Fixtures come from a real tenant, redacted** — owed (Andrei, 2026-10-12: a hand-written fixture shaped on Microsoft's published example is accepted for now, its `_provenance` saying so). The spike is re-run against one listed channel and the page kept with ids, names and text replaced, structure intact, replacing it.
- **The dashboard reads the calendar only** (Andrei, 2026-10-12). Its live read walks no channel, so a failing channel never marks the calendar unread and no message is fetched only to be discarded; the scheduled harvest still reads every listed channel.
- **Lands after four earlier slices:** every result carrying the provider's answer instant; `mentions` and the list grammar; ledger segments created readable by the owner alone; person handles and their producer (`2n`).

**Ask First:** Nothing. Replies and the excerpt length were settled on 2026-10-09; the chat questions belong to the chat slice.

**Never:** No chats, no transcripts. No enumeration of teams or channels. No thread field on the payload; no display name in `mentions`. No sanitization in the connector. No change to the grammar machinery. No server-side filter or sort on the channel endpoint — it refuses both.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Listed channel, new messages | three created since the position | three events in the project log; position is the newest creation time | N/A |
| No channel list | row as today | calendar only | N/A |
| List malformed | not a list; entry without team or channel id | not built; entry named | refused by name |
| Unenrolled project | `"project": "ghost"` | not built; entry named | `UnknownProject` |
| Channel id listed twice | one id, two entries | not built; id named | refused by name |
| HTML body with a mention | `<at id="0">Jane</at>`, `mentioned.user.id` | excerpt plain text with "Jane"; `mentions` holds `graph:<that id>`, lowercased | N/A |
| Mention without a user | channel or tag mention | left out; text intact | N/A |
| No mentions list | key absent | `mentions` absent, not empty | N/A |
| Body sent as text | `contentType: text` | taken as is | N/A |
| Not a message | system event, typing, chat event, unknown type | skipped, no refusal | N/A |
| Row unreadable | no id; body null or unknown kind; creation time missing, unparseable or naive | refused by name; page continues | `RowRefusal` |
| Edited after harvest | newer modified time, same id | not emitted again | N/A |
| Old thread, fresh reply | root inside the 90-day horizon, reply today, on any page before the horizon is crossed | reply emitted, root not; a root before the horizon on a later page is not read (`33h`) | N/A |
| Thread over the inline reply page | more-replies link present | replies received emitted; thread refused by name | `RowRefusal` |
| Long message | 6,000 characters | excerpt is the first 280 | N/A |
| Sender is a bot | `from.application` set | emitted; actor `graph_app:<application id>` | N/A |
| First run | no channel position | reach-back is the position | N/A |
| Old calendar-only cursor | bare ISO instant | calendar position read; channels first-run | N/A |
| Empty page with a next link | `value: []` plus link | walk continues | N/A |
| Walk cut by cap or budget | horizon not reached | position is the newest seen; failure names the channel and which loss: before the position, the skipped messages (retryable); after it, the horizon and the skipped replies (not retryable); no coverage | failure, retryable or not (`33h`) |
| Not a member of the channel | 403 on B | B named, remedy names B; A and calendar persisted; B's position unchanged; no coverage | not retryable |
| Mistyped channel id | 404 or 400 on B | B named, entry named; others persisted | not retryable |
| Two resources fail differently | 429 on A, 403 on B | one failure, not retryable, carrying A's hint | failure outcome |
| Calendar sign-in fails | stale credential | no channel attempted this run | not retryable |
| Empty channel, empty calendar | both answer nothing | empty outcome with the answer instant and a window from it to the walk end | N/A |
| Same message id in two channels | colliding ids | distinct references — the channel id is in them | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/connectors/graph/__init__.py:218-222` row keys; `:357` `graph_category_scopes`, model for the list reader; `:502` `GraphConnector`; `:551-557` `emits()`; `:599` `harvest`, `:685` `_result`; `:747` cursor encode and `:897` `_calendar_position`, replaced by the per-resource token; `:760` coverage pass-through, replaced by the built window; `:849` `_to_event`, `:917` `_assert_citable`, models for the message event and its reference guard
- `pm_ai/connectors/graph/calendar.py:747` `RowRefusal`; `:760` `CalendarFetch` (its `reached_at` is, after `8p`, the result's `answered_at`); `:813-837` `fetch`; `:998` `_read`; `:1081` `_failure` -- what `channels.py` mirrors
- `pm_ai/connectors/graph/client.py:136,162` page cap, budget; `:230` `ConsentChanged`, the tenant-wide reading a per-channel 403 must not inherit; `:753` `walk`; `:805` `calendar_view`, model for a messages URL with `$top=50&$expand=replies`
- `pm_ai/domain/events.py:113` `MessagePayload` -- `mentions` and its `TRUSTED_TEXT` entry (`:246`) are `2m`'s; `pm_ai/domain/harvest.py:272` `HarvestResult` -- `answered_at` is `8p`'s; both read here, not changed
- `pm_ai/domain/identity.py:160` reference grammar (`native_id` is `\S+`, so `:` and `@` in a channel id are legal); the person-handle producer is `2n`'s (`resolve_actor` at `:351` is not called on the write path since `2n`)
- `pm_ai/core/connector_enrolment.py:121` `RESERVED_ROW_KEYS`
- `pm_ai/app/wiring.py:813` `_graph_connector`; `:908-928` builds the connector; `:1323` `_unbuilt`; `pm_ai/app/pipelines.py:139` `_persist_by_scope`, unchanged
- `pm_ai/core/rendering.py:241,633` `NO_PRODUCER_YET` and its branch, deleted; `:655` `_signal_line`
- `tests/connectors/test_graph_calendar_fetch.py:90,128` fixture builders; `test_graph_calendar_mapping.py:1` scripted transport, `:750-752` the `emits()` assertion that flips; `tests/architecture/test_domain_invariants.py:95` the AD-27 gate
- `_bmad-output/implementation-artifacts/slice-0-graph-spike-2026-09-06.md:122-141` paging refusals; message shape unmeasured

## Tasks & Acceptance

**Execution:**
- [x] scratch script, not committed -- re-run the spike on one listed channel with the existing auth and client; save the redacted page as `tests/connectors/fixtures/graph/channel_messages.json` -- done 2026-10-16: the page was captured 2026-10-15 through pm-ai's own Graph client, redacted, and saved as `tests/connectors/fixtures/graph/channel_messages_recorded.json` beside the hand-written fixture — a change from this task's filename, so the hand-written page and the tests that assert its values stay. The page shows threads listed by first-post creation time, not latest activity: see the deferred-work entry "Data loss in busy channels" (`_bmad-output/implementation-artifacts/deferred-work.md`)
- [x] `pm_ai/app/pipelines.py` (`_ask`) and the Graph connector -- the dashboard's live read is calendar-only; a render with listed channels sends no channel request and a failing channel leaves the calendar read
- [x] `pm_ai/connectors/graph/channels.py` -- new: message row, HTML-to-text, newest-first walk with the thread-activity cut and creation-time emission, the cap/budget position rule, per-row refusals, clock readings, per-channel 403/404/400 readings
- [x] `pm_ai/connectors/graph/__init__.py` -- `channels` reader with refusals at construction; per-resource cursor reading the old form; the message event; `emits()`; one shared budget; the built window; the combined failure; stop after a calendar sign-in failure
- [x] `pm_ai/app/wiring.py` -- read `channels` off the row, build the fetchers
- [x] `pm_ai/core/rendering.py` -- delete `NO_PRODUCER_YET` and its branch
- [x] `tests/connectors/test_graph_channel_messages.py` -- one test per matrix row from the fixture through a scripted transport; `$top=50&$expand=replies` asserted on the recorded request; after a per-channel 403 the returned cursor holds that channel's previous position
- [x] `tests/connectors/test_graph_calendar_mapping.py:750-752` -- flip the `emits()` assertion; `tests/core/test_rendering_sections.py` -- a render with zero message entries asserts the deleted sentence is absent

**Acceptance Criteria:**
- Given a row listing one channel mapped to `alpha`, when `run_harvest` runs against the fixture, then every `message_posted` entry is in `alpha`'s event log and none in the personal one.
- Given any emitted message event, then `authored_by` is unknown and no `id` is set.
- Given the declared scope set, then it equals the set `33a` declared.
- Given `uv run pytest -q`, then all pass; the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09).

## Design Notes

Row key: `channels`, a list of `{team, channel, project, label?}`; `RESERVED_ROW_KEYS` (`connector_enrolment.py:121`) governs clashes. Cursor: a JSON object with `calendar` (ISO instant), `channels` (channel id → ISO instant) and, after `33f`, `chats`; a stored bare ISO string reads as a calendar-only cursor.

"List channel messages" (v1.0, learn.microsoft.com/graph/api/channel-list-messages) supports only `$top` (default 20, maximum 50) and `$expand`, sorts "by the last modified date of the entire reply chain", and returns up to 200 replies inline with `replies@odata.nextLink` for more. A root's own timestamps say nothing about its place in the list, so the cut reads the thread's newest activity; the spike re-run must confirm replies arrive newest first, as the reference's example shows. `Team.ReadBasic.All` and `Channel.ReadBasic.All` stay declared though unused: dropping them changes the consented set for nothing.

Dependencies: `8p` (`answered_at`), `2m` (`mentions`, list grammar; AD-27 / AD-48, 2026-10-09), `1q` (segments at `0600`), `2n` (person handles, AD-34 2026-10-11).

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_graph_channel_messages.py -q` -- expected: every matrix row passes, no network
- `uv run pytest -q` -- expected: all pass; skip count unchanged from the baseline (24 on 2026-10-09)
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

## Spec Change Log

- **2026-10-11, frozen block renegotiated by Andrei** (architecture AD-34, same day). A person on a line is the provider's handle and is matched to a pm-ai person only when read. Amended: the sender and each `mentions` member are `graph:<user id>` handles from `2n`'s producer (were: the sender resolved through the alias table; mentions as raw Graph ids); a bot sender is `graph_app:<application id>` (was: actor unresolved); lands after `2n`. Avoided: a Graph account fixed as a person's identity in an immutable line, and a wrong alias that could never be corrected.
- **2026-10-12, frozen block amended by Andrei** (reviewing this slice). The real-tenant fixture is owed, the hand-written one accepted for now; the dashboard's live read becomes calendar-only, because each render walked every listed channel and one failing channel marked the calendar unread.
- **2026-10-17, frozen block amended by `33h`** (Andrei's horizon decision of 2026-10-16). Threads are listed by first-post creation, not latest activity: the ordering sentence, the cut-short bullet, the "Old thread, fresh reply" row and the "Walk cut by cap or budget" row now state the 90-day first-post horizon, its admitted limit and the two cut-short losses.
- **2026-10-18, frozen block amended by `23f`** (readable message signals, Andrei's decision of 2026-10-16). The payload gains the sender's display name, `sender_name`, taken from the same branch of `from` as the actor (`from.user.displayName` for `graph:`, `from.application.displayName` for `graph_app:`, none for `none:`), stripped, cut to 100 characters, and absent when it is not text, only invisible characters, or shaped like a handle. The sender is still the handle; `mentions` still records no display name.

## Suggested Review Order

1. The settings list and its refusals: [`graph_channel_listings`](../../../../pm_ai/connectors/graph/__init__.py#L429).
2. The walk per channel: [`GraphChannelFetch.fetch`](../../../../pm_ai/connectors/graph/channels.py#L300) — the stop rule, emission by creation time, the position after a full or cut-short walk; the body as text in [`plain_text`](../../../../pm_ai/connectors/graph/channels.py#L234).
3. One message, one event: [`_message_event`](../../../../pm_ai/connectors/graph/__init__.py#L1171) — `graph:`/`graph_app:` sender, mentions as handles, the reference and the excerpt.
4. The run as a whole: [`harvest`](../../../../pm_ai/connectors/graph/__init__.py#L798) — one time budget, one coverage window, failures per resource; the cursor's one position per resource in [`_Positions`](../../../../pm_ai/connectors/graph/__init__.py#L1353).
5. The dashboard's calendar-only read: [`read_calendar`](../../../../pm_ai/connectors/graph/__init__.py#L819), the optional [`CalendarReadPort`](../../../../pm_ai/ports/__init__.py#L87), and its caller in [`pipelines.py`](../../../../pm_ai/app/pipelines.py#L575).
6. Tests: `tests/connectors/test_graph_channel_messages.py` over the hand-written fixture `tests/connectors/fixtures/graph/channel_messages.json`.
