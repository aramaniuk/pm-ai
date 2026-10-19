---
title: 'A listed channel may file into the personal scope'
type: 'feature'
created: '2026-10-16'
status: 'done'
review_loop_iteration: 0
baseline_commit: '8b79328d36da9e733063f9c84eac425d500956f4'
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A channel entry in the Graph settings file must name an enrolled project, and its messages always go to that project's event log (`33d`). On the field test four of the five channels the PM wanted to follow belong to no project, so they could not be listed at all. Andrei decided on 2026-10-15 that a listed channel may file into the personal scope.

**Approach:** A channel entry may say where its messages go with the same `scope` a chat entry uses — `personal` or `project:<id>` — read by one shared parser with the same refusals; the existing `project` key keeps working. A personal channel's messages go to the personal event log and are cited with `personal` as the scope, as a personal chat's are.

## Boundaries & Constraints

**Always:**
- **Two spellings, one meaning each.** `"project": "<id>"` files into that project, as today; `"scope": "personal"` or `"scope": "project:<id>"` is the chat spelling. An entry *carries* a key when the key is present, whatever its value: carrying both is refused naming the entry, and carrying neither is refused naming the entry ("there is no default") with `ChannelListRefused` (today a missing `project` raises `UnknownProject`).
- **One scope reader for chats and channels:** `_chat_scope` becomes `_listed_scope(value, *, where, noun, refused, registered)`, its sentences naming the noun ("a channel's messages", "a chat's messages") and raising the caller's refusal type (`ChannelListRefused` or `ChatListRefused`; `UnknownProject` for an unenrolled project, as today). Its refusals: missing or padded, neither personal nor a project, a people scope with the sentence about the missing team-member registry, a project no registry knows, a project named `personal` in any case, a project id the reference grammar cannot carry.
- **The old `project` key gets the same project checks:** a project named `personal` in any case, and an id the reference grammar cannot carry, are refused on it too — otherwise its references would collide with personal ones (`graph:personal:message:…`). A row that relied on either was already producing ambiguous references.
- **AD-4 holds:** a personal channel's messages go to the personal event log, never a project's; a project channel's go to that project's, never the personal one. A channel's scope comes only from its entry.
- **`ChannelListing` carries a scope**, not a project id, and refuses anything but personal or a project when built, as `ChatListing` does; the reference is `graph:<personal | project id>:message:<channel id>/<message id>`, the scope segment spelled by `reference_scope` as a chat's — so a project channel's references are byte-identical to today's.
- **The cursor stays keyed on the channel id.** An entry moved from one scope to another keeps its position: the new scope's log receives messages created after it; messages already filed stay in the old scope's log, and none is filed twice, except the newest message already filed in the old scope, which the new scope's log receives once more: an accepted limit (Andrei, 2026-10-19).
- **Wording that named a project names the scope:** the 400/404 failure sentence (`channels.py:424`) and the duplicate-channel refusal ("one channel's messages go to one scope").
- **Every place that reads a channel's project is changed or checked:** `_files_into` reads a channel's `scope` the way it reads a chat's (only `project:<id>` names a project) and its `project` as today, so `connector check` in the daemon names a row whose channel files into a project enrolled since the start, and not a personal one; the Graph row's filed scope for `9a` (`instance_scopes`) stays personal; the personal dashboard's Proactive Enablement already lists personal message entries (eight from chats on the field test), so it needs no change.
- **No new permission:** the request is `33d`'s.
- **`33d`'s frozen block is edited when this slice is built** (frozen text edited, dated Change Log line), after `23f` and `33h` have amended it, by its task: the channel-list rule (`33d:23`, "the enrolled project the channel belongs to"), "A channel message belongs to its project" (`33d:24`) and the reference (`33d:32`, `graph:<project>:message:…` → `graph:<personal | project id>:message:…`), with a dated Change Log line.

**Ask First:** Nothing.

**Never:** No people scope for a channel. No listing of teams or channels. No change to how a project channel is cited, cursored or filed. No migration of existing rows.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Personal channel | `"scope": "personal"` | messages in the personal event log; references `graph:personal:message:<channel>/<id>`; none in any project log | N/A |
| Project channel, old spelling | `"project": "alpha"` | as today; references unchanged | N/A |
| Project channel, scope spelling | `"scope": "project:alpha"` | same events and references as the old spelling | N/A |
| Both keys | `project` and `scope`, either empty or null | not built; entry named | `ChannelListRefused` |
| Neither key | team and channel only | not built; entry named, "there is no default" | `ChannelListRefused` |
| People scope | `"scope": "people:x"` | not built; the registry sentence | `ChannelListRefused` |
| Unenrolled project | `"scope": "project:ghost"` or `"project": "ghost"` | not built | `UnknownProject` |
| Project named personal | `"scope": "project:Personal"` or `"project": "Personal"` | not built; why named | `ChannelListRefused` |
| Malformed scope | `"scope": "team"` | not built; entry named, the sentence says "a channel's messages" | `ChannelListRefused` |
| One channel, two scopes | same id under personal and a project | not built; id named | `ChannelListRefused` |
| Moved scope | entry changed from `project:alpha` to `personal` | position kept; new messages to personal; alpha's stay | N/A |
| Daemon, project enrolled since | channel `"scope": "project:new"` | row named with the restart sentence, not built | N/A |
| Daemon, personal channel | `"scope": "personal"` | row built and probed | N/A |
| Mixed run | one personal channel, one project channel | each channel's events in its own log | N/A |

</frozen-after-approval>

## Code Map

Re-verified against `5f4f43e` on 2026-10-18.

- `pm_ai/connectors/graph/channels.py`: `:154-175` `ChannelListing` (`project: str` at `:164`; `scope` always a project at `:172-175`); `:424` the 400/404 sentence naming the project (the frozen block's `channels.py:365` is this sentence, moved by `33h` and `23h`)
- `pm_ai/connectors/graph/chats.py:153-188` `ChatListing` -- the model: scope, `__post_init__` refusal at `:166`, `reference_scope` at `:178-188`
- `pm_ai/connectors/graph/__init__.py:292` `CHANNELS_KEY`; `:463-539` `graph_channel_listings` (missing project raises `UnknownProject` at `:500-506`; duplicate wording at `:523-529`); `:557-623` `graph_chat_listings`; `:626` `_citable_scope`; `:669-714` `_chat_scope` (raises `ChatListRefused`, "A chat's messages go to…"); `:805-810` `SAMPLE_CHANNEL` (`project="sample"`); `:1489-1509` `_message_event`, the reference at `:1504`; `:1511-1530` `_chat_message_event`, the `reference_scope` model at `:1524`
- `pm_ai/app/wiring.py:985-1022` `_files_into` (`:1001` pairs channels with `project`, read at `:1011`); `:958` a Graph row filed personal; `:1414` where the channel listings are read
- `ChannelListing(` call sites to update: `pm_ai/connectors/graph/__init__.py` (2: `:532`, `:805`), `tests/connectors/test_graph_channel_messages.py` (2: `:178`, `:1258`), `tests/connectors/test_graph_calendar_mapping.py` (1: `:1649`), `tests/connectors/test_graph_chat_messages.py` (5: `:608`, `:871`, `:1004`, `:1013`, `:1107`), `tests/connectors/test_graph_channel_messages_recorded.py` (1: `:106`)
- `tests/connectors/test_graph_channel_messages.py:358-403` the list refusals (assert `ChannelListRefused`/`UnknownProject`); `tests/slice/test_line_requests_hardening.py:304-316` `_files_into`
- Spine: AD-10's "One row, several scopes" paragraph (`ARCHITECTURE-SPINE.md:267`, `[2026-10-16, gate; 33i]`) already says a listed channel may file into the personal scope since `33i`; no further amendment is owed

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/connectors/graph/channels.py` -- `ChannelListing.scope: DataScope` with the chat's refusal and `reference_scope`; sentences name the scope -- the listing
- [x] `pm_ai/connectors/graph/__init__.py` -- `_listed_scope` shared and parameterised; `graph_channel_listings` takes `project` or `scope` (present-key rule), applies the project checks to both; duplicate wording; `_message_event` cites by `reference_scope` -- parsing and citing
- [x] `pm_ai/app/wiring.py` -- `_files_into` reads a channel's `scope` like a chat's, and its `project` as today -- the later-project rule
- [x] the `ChannelListing(` call sites above -- construct with a scope
- [x] `tests/connectors/test_graph_channel_messages.py` -- one test per matrix row; the refusal tests adjusted to the new types and wording; `tests/slice/test_line_requests_hardening.py` -- `_files_into` over both spellings -- proof
- [x] `_bmad-output/specs/spec-pm-ai/stories/33d-graph-channel-messages.md` -- when this slice is built: edit the frozen text at `:23`, `:24`, `:32` and add a Change Log line dated that day, "amended by `33i`". 33d is amended in build order `23f`, `33h`, `33i`; re-read its lines first, since the earlier two edit it. As of `5f4f43e` (after `23f` and `33h`) the three sentences are still at `33d:23`, `:24` and `:32`; `23f` added the sender display name to `:32` and left the reference text `graph:<project>:message:<channel id>/<message id>` that this slice replaces

**Acceptance Criteria:**
- Given `33d`'s channel fixture listed with `"scope": "personal"`, when `run_harvest` runs, then every message is in the personal event log with a `graph:personal:message:` reference, and no project log changed.
- Given a row written before this slice, when the connector is built and harvested, then its references and its cursor are identical to before.
- Given the change, when `uv run pytest -q` runs, then all pass.

## Spec Change Log

- **2026-10-16, created.** Carries Andrei's decision of 2026-10-15: a channel entry may carry the chat's `scope` (`personal` or `project:<id>`) with the chat's refusals; the old `project` key unchanged; both or neither refused; personal channel messages in the personal log, cited `graph:personal:…`; `_files_into` reads the new key; dashboard and `9a`'s filed scope unchanged. Supersedes `33d`'s "a channel message belongs to its project … never the personal one".
- **2026-10-16, review:** "carries a key" defined as present; the shared reader named (`_listed_scope`) with the noun and refusal type parameterised; the personal-name and citable checks applied to the old `project` key; the "neither" refusal type named; the cursor rule for a moved entry stated; scope wording for the 400/404 and duplicate sentences; the `ChannelListing` call sites listed; `33d:23` and `:32` added to the amendments, frozen text edited with a dated line; AC 1 made system-level; anchors re-based on PR #72. Depends on no spine amendment.
- **2026-10-16, review (consistency):** the 33d amendment follows the one method shared with `33h`: frozen text edited and a dated Change Log line added when this slice is built, in build order `23f`, `33h`, `33i`.
- **2026-10-18, Code Map re-verified against `5f4f43e`; 33d line references updated after 23f and 33h amended it.**
- **2026-10-18, decision (Andrei):** the frozen block's reference to the 400/404 failure sentence corrected from `channels.py:365` to `channels.py:424`, where `33h` and `23h` moved it. Cosmetic; no behaviour change.
- **2026-10-18, built and reviewed.** Three review layers ran. Patched: `_files_into` trims a channel's `project`; tests for the project channel's 400/404 wording, a lone null, empty or non-text `scope`, the padded-scope sentence, and the filed scope of a project-channel row; 33d's channel-list rule names the new refusals. Deferred, needs Andrei's decision: a channel moved to another scope files the message at its stored position a second time in the new scope (strict xfail `test_a_channel_moved_to_personal_files_no_message_twice`), against the frozen "none is filed twice". The 400/404 sentence now sits at `channels.py:441`, since this slice added lines above it.
- **2026-10-19, decision (Andrei):** frozen block edited (recorded here): "none is filed twice" now names its one exception as an accepted limit. The walk reads from the stored position inclusively and a message's reference holds the scope, so after a move the newest message already filed is filed once more in the new scope, once per move. Fixing it would change how a project channel is cursored, which this slice's "Never" forbids. The strict xfail became `test_a_channel_moved_to_another_scope_files_only_its_newest_old_message_again`, which pins the limit.

## Design Notes

Reusing the chat's scope reader rather than adding `"project": "personal"` keeps one spelling for "where messages go" across the settings file and inherits its refusals.

The cursor stays keyed on the channel id rather than on (scope, channel): keying on the pair would re-read the reach-back into the new scope and file a second copy of messages already filed in the old one; carrying the position over files each message once, in the scope its entry named when it was harvested.

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_graph_channel_messages.py tests/slice/test_line_requests_hardening.py -q` -- expected: every matrix row passes
- `uv run pytest -q` -- expected: all pass
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

## Suggested Review Order

**Where a channel's messages go**

- Entry point: the present-key rule, `project` or `scope`, never both or neither.
  [`__init__.py:539`](../../../../pm_ai/connectors/graph/__init__.py#L539)

- One scope reader for chats and channels; noun and refusal type are parameters.
  [`__init__.py:720`](../../../../pm_ai/connectors/graph/__init__.py#L720)

- Project checks shared by both spellings, so old `project` rows cannot collide with personal.
  [`__init__.py:767`](../../../../pm_ai/connectors/graph/__init__.py#L767)

- The listing carries a scope; refuses anything but personal or a project.
  [`channels.py:168`](../../../../pm_ai/connectors/graph/channels.py#L168)

**Citing**

- Reference segment spelled like a chat's; project references stay byte-identical.
  [`channels.py:181`](../../../../pm_ai/connectors/graph/channels.py#L181)

- The message reference built from `reference_scope`.
  [`__init__.py:1592`](../../../../pm_ai/connectors/graph/__init__.py#L1592)

**Wording and the daemon**

- The 400/404 sentence names the scope.
  [`channels.py:441`](../../../../pm_ai/connectors/graph/channels.py#L441)

- Later-enrolled project check reads a channel's `scope` like a chat's, trims `project`.
  [`wiring.py:985`](../../../../pm_ai/app/wiring.py#L985)

**Tests**

- Acceptance 1: a personal channel through `run_harvest`.
  [`test_graph_channel_messages.py:2014`](../../../../tests/connectors/test_graph_channel_messages.py#L2014)

- Moved scope: what holds, and the strict xfail for the deferred double filing.
  [`test_graph_channel_messages.py:2185`](../../../../tests/connectors/test_graph_channel_messages.py#L2185)

- `_files_into` over both spellings.
  [`test_line_requests_hardening.py:305`](../../../../tests/slice/test_line_requests_hardening.py#L305)
