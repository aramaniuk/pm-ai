---
title: 'compact cards in interactive review'
type: 'feature'
created: '2026-10-24'
status: 'done'
baseline_commit: 'bbb09e6e7e929d0ca212c6d6921884cc83701f26'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/13a-proposals-approved-edited-or-rejected.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** In `pm-ai proposal review` (13a), every card ends with three full command lines: `approve:`, `edit:` and `reject:`, each with the long proposal id, `--scope` and `--if-version`. The `edit:` line also repeats every field. A wrapper needs these lines, but the person in the walk only presses `a`, `e`, `r`, `s` or `q`. On his first real review (2026-10-24), Andrei found that the commands buried the item he was deciding on.

**Approach:** One card renderer, two text forms. The full form stays as it is. A compact form prints only the item, and only the interactive review walk asks for it.

## Boundaries & Constraints

**Always:**

- **The compact card** prints, in this order:
  - the heading: the type's label and the state in plain words, "waiting for you until <date>" for a waiting item;
  - the field lines, from the type's `card`: for an action item, the owner, the text and the due date if any. A "may repeat an item you edited: <id>" line stays, because it is a warning about the item itself;
  - where it came from: the meeting's title and start, and the scope (or "meeting record unavailable", or the citation).

  Then the key prompt follows, as today.
- **The compact card leaves out:**
  - the `approve:`, `edit:` and `reject:` lines, and the "unavailable: <reason>" lines in their place. A key whose action is unavailable is refused when pressed, with the reason, as today;
  - the `replaces <id>` and `staged again as <id>` lines, which carry only ids.
- **Where the compact card is used:** only in the review walk, which already runs only when stdin is a terminal. That covers both the card shown before each key prompt (including the current card shown again after a conflict) and the replacement's card that an edit shows before "approve it? [Y/n]".
- **One renderer (AD-13).** The compact form is an option of 13a's text renderer (`card_text`), not a second card. It uses the same `Card` value and the same lines, and only leaves some out. Availability is still decided by `build_card` alone.
- **Unchanged outside the walk:** `proposal show`, `proposal list`, `approve`, `reject` and `edit` print the full card. Every `--json` object is unchanged. A refusal's text still shows the full card. `review` with no terminal is still refused with the same message. The dashboard's lines, completion and the prompt's daemon-routed output are unchanged. A wrapper reads none of the compact text.
- **Unchanged inside the walk:** the key prompt, the edit's field questions, the sentence printed after each answer, and the closing lines (the counts, such as "3 approved.", the "more waiting" lines and the `next:` lines).

**Never:**

- No change to `Card`, `build_card`, `card_json`, `CARD_FORMAT`, or any command's `--json` output.
- No change to the full text card of `show`, `list`, `approve`, `reject`, `edit` or any refusal.
- No compact card chosen anywhere except the interactive review walk.
- No new command, option or flag.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Interactive review | `proposal review --title weekly` at a terminal, 3 waiting action items | each card is the heading, the owner and text (and due), and the origin line, then the key prompt; no `approve:`, `edit:` or `reject:` line, no `--if-version`; the closing counts and `next:` lines as before | exit 0 |
| Edit inside review | `e`, a new due, then the "approve it?" question | the replacement's card is compact too: heading, the edited field lines, the origin; no command lines and no `replaces` line | continues |
| Conflict inside review | a re-stage changes the item while its card is open | the refusal's sentence, then the current card shown again, compact | continues |
| Expiry inside review | the item expires while its card is open | the refusal's sentence, "expired on <date>, not reviewed", with no command line; the walk moves on, as in 13a | continues |
| `show` and `list` | `proposal show <id>`, `proposal list` | the full card, with its `approve:`, `edit:` and `reject:` commands, as in 13a | exit 0 |
| `--json` | `list`, `show`, `approve`, `reject`, `edit` with `--json` | the same objects as before, the actions' commands included | as in 13a |
| No terminal | `proposal review` with stdin not a TTY | refused with today's message, naming `proposal list --json` and the verbs | exit 3 |

</frozen-after-approval>

## Code Map

Anchors checked against `origin/main` at `bbb09e6`.

- `pm_ai/core/proposal_cards.py:297` -- `card_text`, the one text renderer: heading, field lines, origin, `replaces` and `staged again as`, then the action lines
- `pm_ai/core/proposal_cards.py:449`, `:470`, `:510` -- `refusal_text`, `outcome_text` and `listing_text`, which keep the full form
- `pm_ai/surfaces/cli/dispatch.py:4833` -- `_proposal_review`, refused without a terminal; `:4888` `_review_one`, which prints each card before the key prompt (`:4892`); `:4927` `_review_edit`, which prints the replacement's card (`:4951`)
- `tests/slice/test_proposals.py:1040` -- `_review`, the walk driven by scripted answers; `:1053` onward, the review tests
- `tests/core/` -- no test of the card renderer on its own today; `tests/core/test_proposal_cards.py` is new

## Tasks & Acceptance

**Execution:**
- [x] **The compact form.** `pm_ai/core/proposal_cards.py`: `card_text(card, *, compact=False)`. With `compact=True`, it prints the heading, the field lines and the origin, and leaves out the `replaces`, `staged again as` and action lines. Its docstring says the compact form is for the interactive review walk only.
- [x] **The walk asks for it.** `pm_ai/surfaces/cli/dispatch.py`: `_review_one` and `_review_edit` call `card_text(…, compact=True)`. Nothing else changes.
- [x] **Tests.** `tests/slice/test_proposals.py`: one test per matrix row. The review cards hold no `--if-version`, no `approve:`/`edit:`/`reject:` line and no proposal id, but hold the owner, the text, the due and the origin. `show` and `list` text still hold the commands. The `--json` objects are unchanged. `tests/core/test_proposal_cards.py` (new): a unit test of `card_text` compares the two forms on one card: the compact form's lines are the full form's first lines.

**Acceptance Criteria:**
- Given a summarised fixture meeting with waiting items, when `pm-ai proposal review` runs at a terminal, then no card it prints contains `--if-version`, a command line or its proposal's own id, and each holds the item's owner, text and origin.
- Given the same meeting, when `pm-ai proposal show <id>` and `pm-ai proposal list` run, then their cards print the `approve:`, `edit:` and `reject:` commands as before, and their `--json` output is unchanged.
- Given the change, when `uv run pytest` runs, then everything passes with 8 skips, as before.

## Spec Change Log

- **2026-10-24, created and approved for build (Andrei),** after his first real `pm-ai proposal review`: the commands on each card were useful to a wrapper but noise to a person pressing keys. The intent was approved as written, so no Ask First is open.
- **2026-10-24, build: frozen block edited (recorded here).** The matrix's conflict row said an item that expires while its card is open is shown again, compact. 13a's walk shows a card again only while it is still waiting, so an expired item prints the refusal's sentence and the walk moves on. The row is split in two: a re-stage (the card shown again, compact) and an expiry (the sentence only, as in 13a). No behaviour changed.
- **2026-10-24, built and reviewed.** Three review layers ran (blind hunter, edge-case hunter, verification gap); the edge-case and verification-gap layers found nothing. From the blind hunter, fixed: the first Acceptance Criterion now says a card holds no id of its own proposal, since a "may repeat" line names another; `card_text` has one exit, with the full form's extra lines in `_detail_lines`; the review tests check each card line, the order of the conflict sentence and the replacement card before "approve it?", and the outcome text of a verb; the duplicate expiry and no-terminal tests were merged into 13a's. Not taken: a key whose action is unavailable is still known only when pressed, and a replacement no longer says it replaces an item; both follow the approved intent and are Andrei's to change.

## Design Notes

**Why an option of one renderer, not a second renderer.** AD-13 gives every surface one card. The compact form is the full form with lines left out, so the two forms cannot disagree about the heading, the fields or the origin.

**Why the per-answer sentence keeps its id.** The sentence printed after an answer (such as "<id> approved: you agreed to it; nothing is carried out.") comes from the engine and is the same one `approve` prints to a wrapper. Changing it is outside this slice.

## Verification

**Commands:**
- `uv run pytest tests/slice/test_proposals.py tests/core/test_proposal_cards.py -q` -- expected: every matrix row passes
- `uv run pytest -q` -- expected: all pass, 8 skips
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a scratch home (`HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1`), drop and summarise the recorded fixture, then run `pm-ai proposal review`. Expected: each card shows the item and its origin, then the key prompt, with no command lines.

## Suggested Review Order

- The compact form: the full form's first lines, the rest moved to one helper.
  [`proposal_cards.py:297`](../../../../pm_ai/core/proposal_cards.py#L297)

- What the full form adds after the origin: `replaces`, `staged again as`, the commands.
  [`proposal_cards.py:313`](../../../../pm_ai/core/proposal_cards.py#L313)

- The only two callers of the compact form: the card before each key prompt.
  [`dispatch.py:4892`](../../../../pm_ai/surfaces/cli/dispatch.py#L4892)

- And the replacement's card before "approve it? [Y/n]".
  [`dispatch.py:4951`](../../../../pm_ai/surfaces/cli/dispatch.py#L4951)

- The walk printed line by line, compact.
  [`test_proposals.py:1132`](../../../../tests/slice/test_proposals.py#L1132)

- `show`, `list` and a verb keep the full card.
  [`test_proposals.py:1207`](../../../../tests/slice/test_proposals.py#L1207)

- The two forms compared on one card.
  [`test_proposal_cards.py`](../../../../tests/core/test_proposal_cards.py)
