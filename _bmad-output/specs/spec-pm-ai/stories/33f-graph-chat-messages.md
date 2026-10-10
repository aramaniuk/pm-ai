---
title: 'Listed Teams chats reach the event log'
type: 'feature'
created: '2026-10-09'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** After the channel slice the Graph connector reads the calendar and the listed project channels, but the 1:1 and group chats where people actually ask the PM for things are still invisible. They cannot ride on the channel slice: chats are reached through a different endpoint that can filter on the server, they have no team to be listed under, and which scope a chat belongs to is the PM's call.

**Approach:** The PM lists each chat to be read by hand, with the scope its messages go to. The connector reads each listed chat's new messages with Microsoft's own date filter and writes one `message_posted` entry per message into that scope's event log, using the channel slice's message mapping unchanged.

## Boundaries & Constraints

**Always:**
- **Only listed chats are read** (decided 2026-10-09). The settings file holds a list of entries, each with a chat id, the scope its messages go to — personal, or an enrolled project — and an optional label. Nothing is enumerated: no chat listing, no preview of last messages. A list that is not a list, an entry without chat id or scope, a scope that is neither personal nor an enrolled project, or a chat id listed twice, stops the connector being built and names the entry.
- **A chat with a direct report cannot be listed yet.** A people scope is refused with a sentence saying the team-member registry does not exist, so such a chat cannot be filed where AD-4 says it belongs and must not be listed until it can.
- **No new permission.** The chat-read permission is already in the declared set.
- **Each listed chat is read newest first, fifty per page, asking the server for only messages modified after the chat's position** — on a first run, after the first-run reach-back the row holds. Microsoft honours that filter only beside a sort on the same field, so both are sent; the connector still cuts on the client: a message is emitted when its creation time is at or after the position, duplicates dropped by the natural key, so an old message edited later is not emitted twice.
- **The channel slice's rules carry over as written:** which rows are messages and which are refused by name, the plain-text excerpt, `mentions`, the event shape, the one shared time budget, the per-resource cursor, the per-resource failure reading, and the one coverage window withheld when any resource failed.
- **The cursor gains one position per chat:** after a complete walk, the newest creation time seen; a chat that yielded nothing keeps its position. A cursor written by the channel slice, with no chat positions, still reads; every listed chat then starts from its first run.
- **One failure fails one chat.** A mistyped chat id (not-found or bad-request) or a permission refused on a chat fails that chat only, non-retryable, naming the entry. A throttle or server error on a chat's second page keeps that chat's position unmoved, keeps the messages already read, and skips the remaining chats, carrying the hint.
- **`channel` in the payload** is the label; without one, the chat's topic when not blank; otherwise one of four fixed words from the chat's kind — `1:1 chat`, `group chat`, `meeting chat`, or `chat` for anything else.
- **A meeting chat is filed by its listed scope**, not joined to its calendar meeting.
- **The reference is `graph:<scope>:message:<chat id>/<message id>`**, the scope spelled as the event log spells it.
- **Fixtures come from a real tenant, redacted**, captured by re-running the spike against one listed chat before any fixture is written.

**Ask First:** Nothing — decided 2026-10-09 (listed chats only).

**Never:** No chat enumeration, no all-chats endpoint, no delta, no application permission. No change to the message mapping, the payload or the grammar. No people scope. No joining a meeting chat to its meeting or project.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Two listed chats, new messages | messages after both positions | events in each chat's listed scope; both positions advanced | N/A |
| Entry scoped to a project | `"scope": "project:alpha"` | events in `alpha`'s log | N/A |
| Entry scoped to a person | `"scope": "people:dana"` | not built; sentence names the missing registry | refused by name |
| List malformed, or id twice | not a list; no chat id or scope; one id in two entries | not built; entry named | refused by name |
| Quiet chat | nothing after its position | no events; the returned cursor still carries its position | N/A |
| First run | no chat positions | reach-back is each chat's position | N/A |
| Cursor from the channel slice | no chat member | calendar and channel positions read; every chat first-run | N/A |
| Server ignores the filter | older rows returned | not emitted — the client cut holds | N/A |
| Edited after harvest | newer modified time, same id | not emitted again | N/A |
| Label, topic, kind | no label; blank topic; `oneOnOne` / `group` / `meeting` / unknown | `channel` is `1:1 chat`, `group chat`, `meeting chat` or `chat`; a topic wins over the kind; a label over both | N/A |
| Mistyped chat id, or not permitted | 404, 400 or 403 on B | B named; others persisted; B's position unchanged; no coverage | not retryable |
| Throttled on a chat's second page | 429 on A page two | A's page-one events kept, A's position unmoved, later chats skipped; hint carried | failure, retryable |
| Same message id in two chats | colliding ids | distinct references — the chat id is in them | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/connectors/graph/channels.py` -- `33d`'s message row, HTML-to-text and event mapping; reused, not copied
- `pm_ai/connectors/graph/__init__.py:599` `harvest`, `:685` `_result` -- where the chat walk joins; `33d`'s cursor token gains `chats`; `:357` `graph_category_scopes`, model for the list reader
- `pm_ai/connectors/graph/client.py:805` `calendar_view` -- model for a chat-messages URL: `$top=50&$orderby=lastModifiedDateTime desc&$filter=lastModifiedDateTime gt <position>`; `:230,753` `ConsentChanged`, `walk`
- `pm_ai/connectors/graph/calendar.py:813-837` `fetch` -- clock readings and failure classes to mirror
- `pm_ai/domain/identity.py:108-110,128` a scope spells itself `personal` or `project:<id>` and parses back; `:25-26` the rule the reference grammar relies on — a project is never named `personal` — stated in the docstring, not refused by `pm_ai/core/project_registry.py` today; `:160` the reference grammar
- `pm_ai/core/connector_enrolment.py:121` `RESERVED_ROW_KEYS`
- `pm_ai/app/wiring.py:908-928` -- the connector construction, which gains the chat fetchers
- `tests/connectors/test_graph_channel_messages.py` -- `33d`'s scripted-transport pattern and fixture loader
- `_bmad-output/implementation-artifacts/slice-0-graph-spike-2026-09-06.md:73` -- the spike's only chat line; the shape is unmeasured

## Tasks & Acceptance

**Execution:**
- [ ] scratch script, not committed -- re-run the spike on one listed chat; save the redacted page as `tests/connectors/fixtures/graph/chat_messages.json`
- [ ] `pm_ai/connectors/graph/chats.py` -- new: the per-chat filtered walk with the client-side cut, the `channel` label rule, clock readings, failure classes
- [ ] `pm_ai/connectors/graph/__init__.py` -- `chats` reader with refusals at construction (people scope refused with its sentence); the `chats` cursor member; chats walked after the channels under the shared budget; per-chat failures
- [ ] `pm_ai/app/wiring.py` -- read `chats` off the row, build the fetchers
- [ ] `tests/connectors/test_graph_chat_messages.py` -- one test per matrix row from the fixture through a scripted transport; sort and filter asserted on the recorded request; a channel-slice cursor read back with every chat first-run; a quiet chat's position present in the returned cursor

**Acceptance Criteria:**
- Given a row listing one chat scoped personal and one scoped `project:alpha`, when `run_harvest` runs against the fixture, then each chat's `message_posted` entries are in its own scope's event log and nowhere else.
- Given a row listing a chat scoped `people:dana`, when the daemon composes, then the connector is not built and the message names the missing team-member registry.
- Given `uv run pytest -q`, then all pass; the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09).

## Design Notes

Row key: `chats`, a list of `{chat, scope, label?}`; `RESERVED_ROW_KEYS` (`connector_enrolment.py:121`) governs clashes. The cursor's JSON object gains `chats` (chat id → ISO instant) beside `33d`'s `calendar` and `channels`.

"List messages in a chat" (v1.0, learn.microsoft.com/graph/api/chat-list-messages) is delegated `Chat.Read`, supports `$top` up to 50, `$orderby` on `lastModifiedDateTime` or `createdDateTime` descending only, and a `$filter` on `lastModifiedDateTime` that is ignored unless `$orderby` names the same property — a chat can be read from a server-side position, which a channel cannot. "getAllMessages" and "chatMessage: delta" list delegated permissions as not supported, so the single-call paths are closed on the device-code flow.

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_graph_chat_messages.py -q` -- expected: every matrix row passes, no network
- `uv run pytest -q` -- expected: all pass; skip count unchanged from the baseline (24 on 2026-10-09)
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
