---
title: 'Fresh replies in older channel threads are read'
type: 'bugfix'
created: '2026-10-16'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Microsoft lists a channel's threads newest first by when each thread's **first post** was created, not by latest activity as `33d` assumed (measured on a real page, PR #72). The walk stops at the first page on which every thread is older than the channel's position, so a fresh reply in an older thread on a later page is never read — and nothing says so.

**Approach:** Keep the one request and stop the walk by the order Microsoft actually uses: after the first page whose oldest first post was created before a **horizon 90 days before the channel's position** (Andrei's decision of 2026-10-16), since every later page holds only older threads. Replies in threads started before the horizon are an admitted limit.

## Boundaries & Constraints

**Always:**
- **The stop rule reads first-post creation times.** The first posts whose creation time can be read are tracked across pages; they are *in order* when each is at or before the one before it (ties allowed), the first of a page compared with the oldest readable first post of the previous page. A page stops the walk when its readable first posts are in order and the oldest of them was created strictly before the horizon (a first post created exactly at the horizon is not older). A page out of order never stops the walk early; from then on the walk stops only on a page whose every readable first post is older than the horizon. A first post whose creation time cannot be read is left out of both tests — so a page whose single readable first post is older than the horizon can stop the walk — and a page of threads none of whose creation times can be read counts as older, as today, so it cannot hold every walk open to the page cap. An empty page with a next-page link does not stop the walk.
- **The horizon is a named constant, 90 days, measured back from the position** (the stored one, or the first-run reach-back). Not a setting.
- **Unchanged:** a message or reply is emitted when created at or after the position; the position becomes the newest creation time seen, never past now; the natural key drops duplicates; per-row refusals.
- **A walk cut short by the 200-page cap or the time budget** advances the position to the newest message seen, as today, and its failure says which loss it is. **Cut before any first post older than the position was read:** messages and replies created after the position in threads the walk did not reach are skipped — retryable, as today. **Cut after first posts older than the position but before the horizon:** only replies in threads started between the horizon and the oldest first post walked are skipped, the failure names the horizon, and it is not retryable — the next scheduled harvest walks again, and an immediate retry would hit the same cut.
- **Coverage is unchanged** and does not show the horizon limit; the deferred-work entry carries it.
- **Kept:** one request shape (`$top=50&$expand=replies`), the 200-page cap and the shared time budget, the refusal of a thread whose replies did not all arrive inline, per-channel failure readings, the dashboard's calendar-only live read.
- **No new permission.** The request is `33d`'s, under the declared `ChannelMessage.Read.All`.
- **`33d`'s frozen block is amended** when this slice is built (frozen text edited, dated Change Log line), by its task: the ordering sentence (`33d:26`), the cut-short bullet (`33d:29`), the "Old thread, fresh reply" row (`33d:64`) and the "Walk cut by cap or budget" row (`33d:71`).
- **The recorded test passes:** `test_a_fresh_reply_listed_behind_a_wholly_older_page_is_still_emitted` loses its strict expected-failure marker and the lines asserting today's cause; its emission assertion stays.

**Ask First:** Nothing — the horizon and its admitted limit are Andrei's decision of 2026-10-16 (memlog, "33h horizon").

**Never:** No `messages/delta`, no per-thread reply requests, no second request kind. No change to emission by creation time. No horizon setting.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Old thread, fresh reply, later page | page 1 wholly older than the position, its oldest first post inside the horizon; page 2 a thread started 59 days ago with replies after the position | page 2 read; the fresh replies emitted, the root not | N/A |
| Old thread, fresh reply, last on the page | a thread started a year ago, last on a page whose other threads are recent | its fresh reply emitted; the walk stops after that page | N/A |
| Thread older than the horizon | first post 120 days before the position, reply today, on a page after the stopping page | not read; no refusal — the admitted limit | N/A |
| Page crosses the horizon | oldest first post on page 1 before the horizon | one request | N/A |
| Exactly at the horizon | oldest first post created at the horizon instant | not older; walk continues | N/A |
| Out of order within a page | a newer first post after an older one | no early stop; walk continues | N/A |
| Out of order across pages | page 2's first post newer than page 1's oldest | no early stop from then on | N/A |
| One readable time | one readable first post, older than the horizon, among unreadable ones | stops after that page | N/A |
| No readable time | no readable creation time on any thread | counts as older; walk stops | N/A |
| Empty page with next link | `value: []` plus link | walk continues | N/A |
| Cut before the position | cap or budget hit while first posts are newer than the position | position to the newest seen; failure names the skipped messages | failure, retryable |
| Cut after the position, before the horizon | cap or budget hit after first posts older than the position | position to the newest seen; failure names the horizon and the skipped replies | failure, not retryable |
| Thread over the inline replies | more-replies link | replies received emitted; thread refused by name | `RowRefusal` |

</frozen-after-approval>

## Code Map

Lines as of PR #72 (its docstring edit shifts `channels.py` by 7 lines); 33h lands after it.

- `pm_ai/connectors/graph/channels.py:1-57` the module docstring's ordering account (rewritten); `:318` `fetch`; `:447-497` `_read`, where the stop test lives; `:524-575` `_cut_short`, its sentence at `:551-571` gains the two-case wording; `:803` `_activity`, read only by the old stop test, deleted with its docstring
- `pm_ai/connectors/graph/client.py:155` `PAGE_CAP`, `:181` `FETCH_BUDGET`, `:987` `channel_messages` -- unchanged
- `pm_ai/core/cadence.py:80` `FIRST_RETRY` and `:137` the doubling ladder a retryable cut triggers
- `tests/connectors/test_graph_channel_messages.py:531` (old thread, fresh reply), `:549` (wholly older page stops), `:817` (undatable thread), `:610`, `:628` (the two cut tests)
- `tests/connectors/test_graph_channel_messages_recorded.py` and `tests/connectors/fixtures/graph/channel_messages_recorded.json` (PR #72) -- the xfail, and the evidence test `test_threads_are_in_order_of_root_creation_not_of_their_latest_reply`
- `_bmad-output/specs/spec-pm-ai/stories/33d-graph-channel-messages.md:26,29,64,71` -- amended

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/connectors/graph/channels.py` -- `THREAD_HORIZON = timedelta(days=90)`; `_read` returns "stop" by the first-post rule with the order check carried across pages; `_cut_short` gains its two cases; `_activity` deleted; the module docstring states the measured order, the horizon and the admitted limit -- the fix
- [ ] `tests/connectors/test_graph_channel_messages.py` -- rewrite `:531` and `:549`, keep `:817`, extend the cut tests -- one test per matrix row
- [ ] `tests/connectors/test_graph_channel_messages_recorded.py` -- drop the xfail marker and the cause lines; assert two requests and the meeting thread's refusal for its unread replies -- the recorded proof
- [ ] `_bmad-output/specs/spec-pm-ai/stories/33d-graph-channel-messages.md` -- when this slice is built: edit the frozen text at `:26`, `:29`, `:64`, `:71` and add a Change Log line dated that day, "amended by `33h`". 33d is amended in build order `23f`, `33h`, `33i`; re-read its lines first, since `23f` edits it before this slice
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark "Data loss in busy channels" resolved by `33h`; add the horizon's admitted limit with paging long-lived threads as the follow-up

**Acceptance Criteria:**
- Given the recorded threads served as two pages and a position of 2026-10-10T00:00Z, when the channel is harvested, then two requests are made, the three replies created after the position in the meeting thread are emitted, and that thread is refused by name for its unread replies.
- Given the recorded page alone and a position of 2026-10-15T12:00Z, when the channel is harvested, then one request is made — the page's oldest first post is before the horizon.
- Given the change, when `uv run pytest -q` runs, then all pass and PR #72's strict xfail is gone rather than skipped.

## Spec Change Log

- **2026-10-16, created.** Carries: the measured order (first-post creation, PR #72) replacing `33d`'s latest-activity reading; the stop rule — a 90-day first-post horizon behind the position, chosen over per-thread reply paging and `messages/delta`; emission, position, page cap, budget, the inline-replies refusal and the permissions unchanged; replies in threads older than the horizon an admitted limit.
- **2026-10-16, review:** order check defined (ties allowed, carried across pages); strict horizon comparison with a row; first posts whose creation time cannot be read defined; the cut-short failure split into two cases, the second not retryable, and `33d`'s cut-short bullet and row added to the amendments; coverage stated not to show the horizon; AC positions pinned; anchors re-based on PR #72; Problem shortened; Andrei's horizon decision cited. Depends on no spine amendment.
- **2026-10-16, review (consistency):** the 33d amendment follows the one method shared with `33i`: frozen text edited and a dated Change Log line added when this slice is built, in build order `23f`, `33h`, `33i`.

## Design Notes

**Evidence.** On the recorded page (49 threads) threads are in exact order of the first post's `createdDateTime`; the thread with the newest reply was started 59 days earlier and sat 33rd; the page is not in order of the first post's `lastModifiedDateTime` either. The list-messages reference (checked 2026-10-16) still says "sorted by the last modified date of the entire reply chain" — hence an order check rather than trust in either.

**Why the horizon, not the other two.** *Paging each thread's replies* (`GET …/messages/{id}/replies`, `$top` up to 50, no filter) reads threads already reached; it does not reach a page the walk never asks for. *`messages/delta`*: the v1.0 chatMessage delta page (checked 2026-10-16) documents only `GET /users/{id}/chats/getAllMessages/delta`, delegated "Not supported", application `Chat.Read.All`, messages "within the last eight months", replies left to the list-replies call. No channel delta path appears on it; whether one still answers delegated calls is assumed, not checked. Either way it needs a permission pm-ai does not hold or an undocumented path.

**Cost.** A run reads about (threads started from the horizon to now) ÷ 50 pages — 90 days plus the days since the position, times the threads started per day, over 50. A full recorded page was about 800 KB. The recorded channel needs one page (one page reached first posts more than a year old). The 200-page cap is 10,000 threads, which a channel starting more than about 111 threads a day would exceed every run — which is why that cut is a stated loss that waits for the next harvest instead of retrying at once.

**Limit.** A recurring channel meeting keeps one thread for the life of its series, so a series older than the horizon loses its fresh replies; paging known long-lived threads is the recorded follow-up.

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_graph_channel_messages.py tests/connectors/test_graph_channel_messages_recorded.py -q` -- expected: every matrix row passes, no xfail left
- `uv run pytest -q` -- expected: all pass
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
