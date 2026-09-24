---
title: 'A check that found nothing still counts as having checked'
type: 'bugfix'
created: '2026-09-24'
status: 'done'
review_loop_iteration: 0
baseline_commit: '2b0d6120b8c1'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** pm-ai can never report a broken promise on a quiet project.

Before it says someone failed to deliver, pm-ai has to be sure it actually went looking and found nothing. Otherwise a laptop that was closed all week would look identical to a promise nobody kept, and the nudge that follows is not something you can take back.

The proof that it looked is a small record: *"I checked GitLab between 09:00:04 and 09:00:06."* The bug is that pm-ai writes that record **only when the check found something**. Check a quiet project, get an empty answer, and nothing is written — so there is no proof it ever looked, and "this promise was broken" stays permanently out of reach. The promise sits at "don't know yet" forever instead.

On a busy project nobody notices, because something always comes back. On a quiet one — where a dropped commitment is most likely, and most worth catching — **pm-ai can only ever deliver good news.** A kept promise still reports as kept, because that needs evidence that arrived; only the bad news is unreachable.

**Approach:** Write the "I checked" record whenever pm-ai actually checked, instead of only when the check returned something. It already knows the two timestamps — it reads its own clock before and after asking the provider — so the record it needs already exists and is being thrown away.

## Boundaries & Constraints

**Always:**
- **Only a real check counts.** The record must come from having actually asked the provider and got an answer, timed by pm-ai's own clock either side of the request. It is never computed from the clock alone. This is the whole point of the rule that exists today, and it stays: a previous version invented "I checked the last four hours" out of nothing every time it ran, and that fabrication is what made the guard worthless. Nothing here lets that back in.
- **Three cases still count as "did not check", and they are unchanged.** (1) The request failed — pm-ai could not look, and that already has its own signal. (2) Things came back but pm-ai could not read any of their timestamps, so it cannot honestly say *when* it looked. (3) Things came back and pm-ai rejected all of them as unreadable — that is pm-ai's own failure to understand the data, not proof the project was quiet, and treating it as proof would let a parsing bug accuse someone of breaking a promise.
- **Nothing about a promise's verdict changes.** The rules that turn evidence into kept / broken / don't-know are untouched. This slice only stops throwing away one of their inputs.

**Ask First:** Nothing.

**Never:** No new kind of harvest outcome. No change to how often pm-ai checks, or to where it resumes from next time. **The part that reads these records and decides a promise is broken is not built yet and is not built here** — that is story 16. This slice makes its input honest so that it can be correct when it arrives.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| A quiet project | pm-ai asks, the provider answers "nothing here" | the check **is** recorded, timed by when pm-ai asked and when the answer came back | N/A |
| The old fabrication | a record invented from the clock, with no request behind it | prevented **at the connector**, which reads its clock either side of a real request | nothing refuses it when the result is built — two timestamps are two timestamps, and at that point a measured window and an invented one are indistinguishable. Before this slice, refusing an empty answer a window happened to block this too; that side effect is gone and the guard now lives only where the measuring happens |
| Unreadable timestamps | things came back, none with a time pm-ai can trust | no record — it cannot say when it looked | N/A |
| Everything rejected | things came back and pm-ai could read none of them | no record — this is pm-ai's own failure, not a quiet project | N/A |
| The request failed | pm-ai could not reach the provider at all | no record; the existing "it failed" signal carries the meaning | refused if a record is claimed anyway |
| Failed halfway | the first part answered, a later part failed | the part that answered is recorded, as today | N/A |
| Clock jumps backwards | the machine's clock steps back mid-request | no record claimed, and this is not reported as a failure | refused when the record is built |
| A quiet project over weeks | many checks, all finding nothing | the checks accumulate, so the period **is** covered and a broken promise becomes reportable | N/A |
| Stored shape | any check | no new database column and no version bump — this writes what the existing table already holds | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/harvest.py:348-354` -- the rule that throws the record away when nothing came back; the central fix
- `pm_ai/domain/harvest.py:78-83` -- the `EMPTY` outcome's own description, which states the mistaken reason ("nothing came back to bound a window with"). The bounds are clock readings, not rows, so this sentence is the bug written down
- `pm_ai/domain/harvest.py:356-362` -- why rejected rows still earn no record; stays true, and explains itself
- `pm_ai/connectors/gitlab.py:605-628` -- `if events and reached_at is not None and ...`; `reached_at`/`finished_at` are already measured and already honest
- `pm_ai/connectors/gitlab.py:631-639` -- where the outcome is chosen, just below
- `pm_ai/connectors/graph/__init__.py:739-758` -- `coverage=fetched.coverage if mapped else None`, the same gate on the Graph side
- `pm_ai/connectors/graph/calendar.py:1060-1074` -- the unreadable-timestamp rule, which must keep applying whenever rows exist
- `pm_ai/domain/lifecycle.py:250-260` -- where a promise becomes kept / broken / don't-know. **Read-only here**, and the reason this matters
- `pm_ai/storage/service.py:1804-1812` -- reads the records back; unchanged
- `tests/connectors/test_coverage_honesty.py:523-539` -- `test_an_empty_200_is_ran_and_learned_nothing`, which asserts today's wrong answer and whose expectation this slice inverts
- `ARCHITECTURE-SPINE.md`, AD-35's coverage bullet and AD-9's Rule -- both were written on 2026-09-23 to describe the buggy behaviour and must move with the fix

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/domain/harvest.py` -- keep the record when the provider answered with nothing; correct the `EMPTY` description that states the mistaken reason -- one rule in one place, and the description is where the next reader learns it
- [x] `pm_ai/domain/harvest.py` -- **added in review:** `HarvestOutcome.of`, so "the check completed and found nothing" has one definition. The condition gained a second consumer here, and the two connectors had spelled it differently -- they agreed only because GitLab's refusals are a subset of its rows
- [x] `pm_ai/connectors/gitlab.py` -- record the check even when it returned nothing, keeping the unreadable-timestamp and rejected-row exclusions for when rows do exist
- [x] `pm_ai/connectors/graph/calendar.py`, `pm_ai/connectors/graph/__init__.py` -- the same on the Graph side
- [x] `tests/connectors/test_coverage_honesty.py` -- invert the empty-answer case and add the quiet-project one, both halves of it; add the empty-page-then-failure case, without which dropping one conjunct turns a provider outage into a `ValueError` out of a method that promises not to raise
- [x] `tests/connectors/test_graph_calendar_fetch.py`, `tests/connectors/test_graph_calendar_mapping.py` -- **added in review:** the Graph mirror of the inverted row, and the two assertions whose stated reason the inversion falsified. Not foreseen when the tasks were written; the empty-answer rule is pinned in three files, not one
- [x] `_bmad-output/planning-artifacts/.../ARCHITECTURE-SPINE.md` -- amend AD-35's coverage bullet and AD-9's Rule, and strike the Open Risk this closes
- [x] `_bmad-output/specs/spec-pm-ai/stories/8a-honest-harvest-outcomes.md` -- **added in review:** a superseding note on the one 8a row this inverts. Its frozen matrix row stands; the note and the struck acceptance criterion are what keep the 8a -> 8i trail readable

**Acceptance Criteria:**
- Given a provider that answers "nothing here", when the harvest is stored, then a check record exists, and its two timestamps are when pm-ai asked and when the answer arrived — not a fixed span computed from the clock.
- Given repeated empty checks across a period, then **each check is recorded**, so the evidence a later reader needs exists where before there was none — and the instants between two checks are *not* covered, which is the half that keeps the fail-closed reading honest. **Whether a whole period counts as covered is not settled here.** A window is the milliseconds a request took and checks run hours apart, so deciding "this period was watched" means allowing for the gaps between them — that belongs to the part that reads these records (story 16), and `lifecycle.py` already says it needs a union with a gap tolerance. This slice makes `BROKEN` *representable*; it does not by itself make it reachable.
- Given a failed request that returned nothing and claims a record anyway, then building the result is refused — pm-ai still tells "I looked and saw nothing" apart from "I could not look".
- Given a first page that answered with nothing and a second fetch that failed, then the harvest **returns** with no window rather than raising — the refusal above is a `ValueError` from a constructor called inside a method the port documents as reporting, and `run_harvest` has no `except`. Added in review: without it the guarding conjunct could be deleted with the suite still green.
- Given the full suite, then the skip count is unmoved and the only behaviour that changes is the one this slice names: a window where `None` used to be. **Amended in review** — the original criterion said nothing outside `test_coverage_honesty.py` changes, which was wrong. Three tests asserted the old rule: the Graph mirror of the empty-answer row, a Graph row whose calendar held only a filtered meeting, and this file's own restart case, whose second half runs an empty harvest.
- Given `uv run pytest tests/connectors/test_coverage_honesty.py`, then every case except the empty-answer one and the restart case passes unedited.

## Spec Change Log

- **2026-09-24, two corrections after the review, both authorised by the human — the slice claimed more than it delivers.**
  **The headline claim was too strong.** An acceptance criterion said repeated empty checks make a period "read as covered". True of the test, false in production: a window is the milliseconds a request took and checks run hours apart, so a due instant almost always falls in a gap. The test passed only because its clock steps a second per reading, making consecutive windows abut. The criterion now says what is actually delivered — the evidence exists and carries honest bounds, which makes `BROKEN` **representable** — and names the missing half: deciding a whole period was watched needs a union with a gap tolerance, which `lifecycle.py` already requires and story 16 owns. The test says as much in its own docstring rather than reading as proof of something it does not prove, and a negative case pins that an instant between two checks is uncovered.
  **A matrix row promised a guard that no longer exists.** It said the old fabricated window is "refused when the harvest result is built". That was true only as a side effect of refusing an empty answer a window at all, and this slice removed that refusal. At the point a result is built, a measured window and an invented one are two timestamps and nothing can tell them apart. The row now says where the guard actually lives — the connector, which reads its clock either side of a real request — and the connector tests pin each window to its own measured readings, which is stronger than any width bound.
  **How both got through.** The matrix audit checked that every row had a test *named* for it, not that the behaviour it claimed still held. Two review lenses found the fabrication independently; the overclaim was found by one and confirmed by measuring a real window against a real harvest cadence.
  KEEP: the fixed rule and its three exclusions; the connector-level equality assertions; and the empty-first-page/failed-second test, which holds a guard that had none and whose absence hid a raise out of a method contracted to report rather than raise.

## Design Notes

Worth recording why a careful fix caused this. An earlier repair found a connector claiming it had checked when it had not, and required that *something came back* as the proof of having checked. That works almost everywhere, because usually something does. But "something came back" and "I actually checked" are different facts, and they come apart in exactly one case — the provider answering "nothing here" — which is the ordinary case on a quiet project. So the proof failed precisely where the guard mattered most, and it failed silently: a system that can never report a broken promise looks exactly like a team that never breaks one.

The timestamps were never the difficulty. A check record is "the seconds the request actually took", and both connectors already read the clock either side of the request. Both numbers are in hand with nothing to show for the request.

**What this does not fix, and how the two problems meet.** pm-ai cannot run more than one project today: the second one you enrol is reported as a fault, and nothing ever goes looking at it. That is a separate bug with its own entry in the deferred work, and this slice does not touch it.

They do meet, though, and it is worth being plain about it. This slice makes the "I checked" record honest **for a project that gets checked at all**. Right now that is one project. So when this lands, a quiet *second* project still cannot have a broken promise reported — not because the record is wrong any more, but because nobody ever went looking. Fixing either one alone leaves the other in the way. Fixing this one first is still right: it is small, it is understood, and the multi-project fix would otherwise be built on top of a record that lies.

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_coverage_honesty.py -q` -- expected: all cases pass, including the inverted empty-answer case, the quiet-project one and the empty-page-then-failure one
- `uv run pytest -q` -- expected: 2615 passed, 24 skipped. The skip count is what must be unmoved; the pass count rises by the three tests this slice adds, from 2612 at `2b0d6120b8c1`
- `uv run lint-imports` -- expected: 12 contracts kept
- `uv run mypy` -- expected: clean

## Suggested Review Order

**The rule**

- The one line that was throwing the evidence away; read the exemption and the reason beside it first.
  [`harvest.py:407`](../../../../pm_ai/domain/harvest.py#L407)
- One place that decides what a run did, so three call sites cannot drift apart.
  [`harvest.py:101`](../../../../pm_ai/domain/harvest.py#L101)

**What each connector now claims**

- GitLab: the window a completed fetch earned, gated on the outcome rather than on rows.
  [`gitlab.py:624`](../../../../pm_ai/connectors/gitlab.py#L624)
- Graph asks the same question the same way, which is what keeps the two honest against each other.
  [`calendar.py:951`](../../../../pm_ai/connectors/graph/calendar.py#L951)

**The evidence it produces, and the limits of it**

- Repeated quiet checks each leave a record — and the docstring says plainly what the fake clock does not prove.
  [`test_coverage_honesty.py:584`](../../../../tests/connectors/test_coverage_honesty.py#L584)
- The guard two reviewers found untested, and the raise it was hiding.
  [`test_coverage_honesty.py:650`](../../../../tests/connectors/test_coverage_honesty.py#L650)

**Peripherals**

- AD-9 and AD-35 now describe the fixed rule and record why the earlier repair went wrong.
  [`ARCHITECTURE-SPINE.md`](../../../planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md)
