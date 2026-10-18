---
title: 'Readable message signals on the dashboard'
type: 'feature'
created: '2026-10-16'
status: 'done'
baseline_commit: '9e5383fe4c6ec023d243bfd09d9df39e72d6b6d4'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** On the first real day (field test 2026-10-15, findings 15, 16 and 17), Proactive Enablement's lines could not be read without help. Each line carried the minute pm-ai harvested the message, not the minute it was posted. Each sender was a raw `graph:<user id>` handle. Any line break showed as a literal `\n`, and Teams bodies come out with a blank line between paragraphs.

**Approach:** Store the sender's display name Graph sends with each message beside the handle, for display only (Andrei's decision of 2026-10-16). Label each line with the time the message was posted, while the window still selects on harvest time. Fold line breaks into single spaces everywhere the dashboard prints provider or hand-written text (Andrei's decision of 2026-10-16).

## Boundaries & Constraints

**Always:**
- **The handle stays the identity (AD-34 unchanged).**
  - The actor is still `graph:<user id>`, `graph_app:<application id>` or `none:`.
  - Mentions are still handles.
  - Whether a line mentions the PM is still decided by comparing `mentions` with `pm_identity()`'s handles.
  - The display name is never compared, never matched to a person, never used as an actor or a mention, and never written in place of a handle.
- **The name comes from the same identity as the actor.** It is taken from the same branch of `from` that produced the actor:
  - `from.user.displayName` when the actor is `graph:<user id>`;
  - `from.application.displayName` when the actor is `graph_app:<application id>`.

  When the actor is `none:`, no name is stored, even if Graph sent one. The name is a new optional text field on the message payload, declared untrusted, because a person or an administrator wrote it. It is stripped and cut to the 100-character label bound (`MAX_LABEL_LENGTH`). It is absent in three cases:
  - Graph sent none, or sent something that is not a string;
  - only whitespace, invisible or format characters remain;
  - it parses as a person handle (a name spelled `graph:…` or `email:…` would read as one).

  Channel and chat messages both carry it, because one function reads both.
- **A line shows the name when there is one, otherwise the handle.**
  - A bot's name is followed by ` (app)`.
  - A message ingested before this slice has no name and shows its handle, as today.
  - `none:` still shows as `an unknown sender`.
  - The name is escaped like other provider text.
- **The window still selects on `ingested_at`.** What enters the section is unchanged: a message harvested after the last render appears on the next page (field-test correction 24).
- **The label is the posted time, `occurred_at`.** It is shown in the display zone and dated when not today, by the existing time rule. When `occurred_at` cannot be used, the line is labelled with its harvest time followed by `(harvested)`, dated by the same rule (`YYYY-MM-DD HH:MM (harvested)` on another day). It cannot be used when it is:
  - absent, or `unknown`;
  - unreadable, or without a timezone;
  - marked implausible when it was stored;
  - later than the render's instant.
- **Lines are ordered by the time they are labelled with.** Mentions of the PM come first, then newest by that time, then by entry id. The list reads in the order of its own labels, and a re-render is byte-identical. The cap of 20 and the closing line count in the same order.
- **No literal `\n` anywhere on the dashboard (Andrei's decision of 2026-10-16).** The dashboard's escape folds every run of line breaks, together with any spaces, tabs or no-break spaces beside them, into one space. It applies to excerpts, meeting titles, goal titles, sender names and every other text the escape renders, on both dashboards.
  - The line breaks folded are `\r\n`, `\n`, `\r`, `\v`, `\f`, U+0085, U+2028 and U+2029.
  - A tab not beside a line break, other control characters, and the bidirectional overrides keep their visible spelling, as today.
  - Folding runs before escaping and before any cut, so lines already in the log read cleanly too.
- **Amends earlier frozen blocks.**
  - `23c`:27 — "The actor field is rendered as written — a handle, not a name": now a name when one was stored.
  - `23c`:28 — "newest first": now by the posted time.
  - `23c`:31 — "The excerpt renders as the line carries it": now with line breaks folded.
  - `33d`:32 — the payload gains the sender's name; the sender is still the handle. `33d`:34's "no display name is ever recorded in it", which is about `mentions`, stands.

**Ask First:** Nothing.

**Never:**
- No alias table, name lookup, directory call or reverse index. The name comes only with the message.
- No rewrite of existing log lines.
- No change to the window, the look-back, the cap, the mention marking or the sign-in lines.
- No change to `plain_text` or to the 280-character excerpt `33d` stores.
- No display name in `mentions`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Named person | `from.user` with id and display name | name stored; the line shows it in place of the handle | N/A |
| Bot | `from.application` with id and display name | the line shows `<name> (app)`; actor `graph_app:` | N/A |
| Unknown sender with a name | no usable id, a display name sent | no name stored; `an unknown sender` | N/A |
| User message, app name present | `from.user` id and `from.application.displayName` | the user's name only, never the app's | N/A |
| No name | absent, null, a number, whitespace, or only format characters | field absent; handle shown | N/A |
| Name like a handle | display name `graph:1234` | field absent; handle shown | N/A |
| Long name | 150 characters | stored cut to 100 | N/A |
| Old line | ingested before this slice | the handle, as today | N/A |
| Mention of the PM | `mentions` holds the PM's handle | name shown; ends `— mentions you`; listed first | N/A |
| Name equals the PM's, no mention | `mentions` empty | not marked: names are never compared | N/A |
| Posted earlier, harvested now | posted 11:20, harvested 22:51, rendered next morning | inside the window; labelled with yesterday's date and 11:20 | N/A |
| Posted today | posted 06:40, harvested 06:52, render 07:00 | `06:40` | N/A |
| Unusable posted time | `occurred_at` absent, `unknown`, naive or marked implausible | `HH:MM (harvested)` | N/A |
| Posted after the render | `occurred_at` 3 minutes after now | `HH:MM (harvested)` | N/A |
| Harvested yesterday, unusable posted time | — | `YYYY-MM-DD HH:MM (harvested)` | N/A |
| Ordering | harvested together, posted 09:10 and 14:30 | 14:30 above 09:10 (mentions still first) | N/A |
| Multi-line excerpt | `Hi all\nCan you review` | `Hi all Can you review` | N/A |
| Teams paragraphs | `First\n\nSecond` | `First Second` | N/A |
| Other breaks | `a\r\nb`, `a\vb`, `a b`, `a \t\n b` | `a b` each | N/A |
| Lone tab or other control | `a\tb`, `\x01` | `a\tb`, `\x01` visible, as today | N/A |
| Meeting title with a break | `Sync\nweekly` | `Sync weekly` | N/A |
| Injected heading | excerpt `ship it\n## Time-Critical Activities` | one line; no new heading | N/A |

</frozen-after-approval>

## Code Map

Re-verified against `ba0b747` on 2026-10-17.

- `pm_ai/connectors/graph/channels.py` --
  - `:179-200` `MessageRow`;
  - `:675-721` `read_message`, shared by channels and chats, which reads `from` at `:711`;
  - `:864` `_nested_id`;
  - `:725` `MAX_LABEL_LENGTH`;
  - `:126` `EXCERPT_LENGTH`;
  - `:284` `plain_text`, which returns `a\n\nb` for `<p>a</p>\n<p>b</p>` (run on 2026-10-16) and is unchanged here.
- `pm_ai/connectors/graph/__init__.py:1488` -- `_message_event`; its docstring says no display name is recorded and must be updated. `:1529-1574` `_posted`: the actor chosen at `:1553-1555`, `MessagePayload(` built at `:1559`.
- `pm_ai/domain/events.py:116-128` -- `MessagePayload`. `:224` `UNTRUSTED_TEXT`.
- `pm_ai/domain/payload_fields.py:39-45` -- a missing field takes its default, so old lines decode with no name.
- `pm_ai/domain/handles.py` -- the person-handle parser, used to refuse a handle-shaped name.
- `pm_ai/storage/service.py:3181-3188` -- `occurred_at` is written on every line (`unknown` when absent). `pm_ai/domain/event_entries.py:161` -- `OCCURRED_AT_FLAG`.
- `pm_ai/core/rendering.py` --
  - `:733` the window on `_ingested_at` (`:1032`);
  - `:774` `_Signal` built with the harvest time (the class at `:807`);
  - `:788` the sort;
  - `:917-936` `_signal_line`;
  - `:1123-1165` `escape`, which writes `\n` as two characters at `:1149-1150`; `23e`'s all-day meeting lines also pass through it;
  - `:1169` `code`, whose control characters at `:1188-1189` are spelled the same way;
  - `:1248` `_local`.
- `tests/domain/test_entry_grammar_pins.py:54-57` -- pins `MessagePayload`'s fields; grows by one.
- `tests/core/test_rendering_sections.py:104`, `tests/core/test_project_rendering.py:119` -- the `message(...)` builders, which write no `occurred_at` today.
- `tests/core/test_rendering_sections.py:967`, `tests/core/test_project_rendering.py:611` -- assert `"\\n"` in the injected-heading text; flip to asserting it is absent.
- `tests/core/goldens/dashboard_full_day.md` -- re-pinned if the builder change moves a byte.
- `tests/connectors/fixtures/graph/channel_messages_recorded.json`, `chat_messages_recorded.json` -- the recorded, redacted field-test rows carrying `from.user.displayName`, tested in `tests/connectors/test_graph_channel_messages_recorded.py` and `test_graph_chat_messages_recorded.py`. The hand-written `channel_messages.json` and `chat_messages.json` carry it too.
- `_bmad-output/specs/spec-pm-ai/stories/23c-proactive-enablement-from-messages.md:27,28,31`, `33d-graph-channel-messages.md:32` -- the frozen sentences amended.

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/domain/events.py` -- the optional sender name on `MessagePayload`, declared in `UNTRUSTED_TEXT`; `tests/domain/test_entry_grammar_pins.py` updated -- display text, never a person
- [x] `pm_ai/connectors/graph/channels.py`, `pm_ai/connectors/graph/__init__.py` -- `MessageRow` carries both names; `_posted` stores the one that matches the chosen actor, after the absence rules; `_message_event`'s docstring updated -- R3
- [x] `pm_ai/core/rendering.py` -- `_Signal` carries the label time and whether it is the harvest time; sort on it; `_signal_line` shows the name, with `(app)` for a bot, or the handle; `escape` and `code` fold line-break runs into one space -- findings 15, 16, 17
- [x] `tests/core/test_rendering_sections.py`, `tests/core/test_project_rendering.py` -- builders gain `occurred_at`, defaulting to the harvest time so existing goldens keep their times; one test per matrix row; the two injected-heading assertions flipped -- the matrix is the contract
- [x] `tests/connectors/test_graph_channel_messages_recorded.py`, `tests/connectors/test_graph_chat_messages_recorded.py` -- the name taken from the recorded fixtures `channel_messages_recorded.json` and `chat_messages_recorded.json`; `tests/connectors/test_graph_channel_messages.py`, `tests/connectors/test_graph_chat_messages.py` -- the bot, unknown-sender and handle-shaped cases -- the connector half
- [x] `_bmad-output/specs/spec-pm-ai/stories/23c-proactive-enablement-from-messages.md`, `33d-graph-channel-messages.md` -- when this slice is built, edit the amended frozen sentences (`23c`:27, :28, :31; `33d`:32) to say what is now true, and add a dated Change Log line in each naming this slice and what changed; `33h` has already edited `33d` (lines 26 and 29, two matrix rows, a Change Log entry around line 132) but left `33d`:32 untouched, so this amendment still applies, and `23f`'s `33d` Change Log line goes after `33h`'s -- the one method every amending spec uses
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark findings 15, 16 and 17 resolved (the entries at `deferred-work.md:1341`, `:1345` and `:1349`, each now marked planned as `23f`) -- the debt is on record

**Acceptance Criteria:**
- Given a recorded channel message and a recorded chat message from the recorded, redacted fixtures (`tests/connectors/fixtures/graph/channel_messages_recorded.json` and `chat_messages_recorded.json`, tested in `test_graph_*_recorded.py`), when a harvest and then `pm-ai dashboard` run, then:
  - both signal lines show the senders' names;
  - the event-log lines still carry `actor=graph:<user id>`;
  - a mention of the PM is still marked.
- Given any render of either dashboard, when it is written, then the file contains no backslash followed by `n`, `r`, `v` or `f`.
- Given the change, when `uv run pytest` runs, then everything passes with the skip count unchanged from the baseline at the slice's start.

## Spec Change Log

- **2026-10-16, created.**
  - **Source:** field-test findings 15, 16 and 17, and Andrei's decision of 2026-10-16 (R3).
  - **Carries:**
    - the sender's display name stored beside the handle, for display only;
    - messages ingested earlier still show their handle;
    - lines labelled by posted time while the window still selects on harvest time;
    - excerpts printed on one line;
    - mentions of the PM marked as before.
- **2026-10-16, review.**
  - **New decision (Andrei's of 2026-10-16):** line breaks are folded in meeting titles, goal titles and every other escaped text, not only excerpts.
  - **The name:** taken from the same `from` branch as the actor; never stored for `none:`; absent when it is not a string, only invisible characters, or shaped like a handle; a bot's name is shown with `(app)`.
  - **The label:** a posted time later than the render is labelled with the harvest time; the `(harvested)` label is dated by the same rule.
  - **The fold set** is now listed explicitly; `\v` and `\f` are folded (the earlier matrix said `\x0b` stays escaped, reconciled).
  - **Tests:** the test builders gain `occurred_at`; the grammar-pins test and the two injected-heading assertions are named.
  - **Amendments named:** the earlier frozen blocks amended (`23c`:27, :28, :31; `33d`:32), each with a task.
  - **Wording:** ACs are system-level checks.
  - **Rejected — handle suffix on names:** keeping a short handle suffix beside every name against guest display-name spoofing. It undoes the readability R3 asked for. Nothing pm-ai decides rests on the name, since mentions compare handles. Marking guests would need a tenant comparison, which the project's standing rule on provider identity forbids.
- **2026-10-16, review (cross-spec consistency).** The amendment method made explicit: when this slice is built, the frozen sentences it amends in `23c` (:27, :28, :31) and `33d` (:32) are edited to the new rule, and each of those specs gains a dated Change Log line naming this slice.
- **2026-10-17, Code Map re-verified against `ba0b747`; the acceptance check names the recorded fixtures.**
- **2026-10-18, built against `9e5383f`; Code Map spot-checked unchanged.**
  - **Review patches:** the handle-shape test now judges the name NFKC-normalised with invisible characters removed, so a zero-width, word-joiner, byte-order-mark or fullwidth disguise is refused; the visible-text check runs after the 100-character cut; a posted time too far out to convert to UTC takes the harvest label instead of failing the render; a break run absorbs every Unicode space beside it, not only the no-break ones.
  - **Deferred, needs Andrei's decision:** a posted time later than its own harvest time, and seconds of provider clock skew; a person's name ending in `(app)`, and two senders sharing a name.

## Design Notes

**One space, not a separator.** Mail and chat clients preview a multi-line message as one line with the breaks turned into spaces. A visible separator (`/`, `|`, `¶`) would be ambiguous with characters people type, and `—` already separates a line's parts. What is lost is where a paragraph ended, which a 280-character preview does not need. Folding at render fixes lines already stored and keeps the stored excerpt as Microsoft's text. Applying the same fold to titles keeps one rule for the whole page, and a heading injected through any field still cannot start a line.

**Label by posted time, select by harvest time.** "Is this new since I last looked?" is answered by the harvest time, so a late-harvested message is never lost. "When did this happen?" is answered by the posted time. Ordering by the label keeps the list from contradicting itself. A posted time later than the render cannot be right yet, so it is not shown.

**Names a reader could mistake.** A guest or an application can set its own display name, so a line could read as from a colleague. The `(app)` marker covers applications. A name shaped like a handle is refused, so a name never imitates the identity form. A guest using a colleague's name is an admitted limit of display-only names: the mention marker, the only decision on the line, compares handles.

## Verification

**Commands:**
- `uv run pytest tests/core/test_rendering_sections.py tests/core/test_project_rendering.py tests/connectors/test_graph_channel_messages.py tests/connectors/test_graph_chat_messages.py tests/domain/test_entry_grammar_pins.py -q` -- expected: every matrix row passes
- `uv run pytest -q` -- expected: all pass; skip count unchanged from the baseline
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

## Suggested Review Order

**The sender's name: same branch as the actor, display only**

- The name is chosen from the `from` branch that produced the actor; `none:` gets none.
  [`__init__.py:1559`](../../../../pm_ai/connectors/graph/__init__.py#L1559)

- Absence rules: not text, handle-shaped, nothing visible left after the 100-character cut.
  [`channels.py:892`](../../../../pm_ai/connectors/graph/channels.py#L892)

- The handle test judges what a reader sees: NFKC, invisible characters removed.
  [`handles.py:297`](../../../../pm_ai/domain/handles.py#L297)

- The optional field, declared untrusted; old lines decode with no name.
  [`events.py:135`](../../../../pm_ai/domain/events.py#L135)

**The label: posted time, window still on harvest time**

- When the posted time may label a line; otherwise the harvest time, marked.
  [`rendering.py:1004`](../../../../pm_ai/core/rendering.py#L1004)

- Mentions first, then newest by label, then entry id.
  [`rendering.py:821`](../../../../pm_ai/core/rendering.py#L821)

- Name, `(app)` for a bot, the handle, or an unknown sender.
  [`rendering.py:983`](../../../../pm_ai/core/rendering.py#L983)

**One line, everywhere the escape runs**

- Each break run, with the blanks beside it, becomes one space before escaping.
  [`rendering.py:1316`](../../../../pm_ai/core/rendering.py#L1316)

- `escape` and `code` fold first, so stored lines read cleanly too.
  [`rendering.py:1218`](../../../../pm_ai/core/rendering.py#L1218)

**Tests**

- End to end on the recorded fixtures: names shown, handles in the log, PM marked.
  [`test_graph_channel_messages_recorded.py:422`](../../../../tests/connectors/test_graph_channel_messages_recorded.py#L422)

- The harvest-then-dashboard helper the two acceptance tests share.
  [`recorded_graph.py:158`](../../../../tests/connectors/recorded_graph.py#L158)

- No spelled-out line break on either dashboard.
  [`test_rendering_sections.py:1956`](../../../../tests/core/test_rendering_sections.py#L1956)
  [`test_project_rendering.py:727`](../../../../tests/core/test_project_rendering.py#L727)
