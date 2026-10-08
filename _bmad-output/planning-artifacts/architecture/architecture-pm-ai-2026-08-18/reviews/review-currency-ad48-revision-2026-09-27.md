# Review: AD-48 revision (lists move to AD-27), 2026-09-27

**Scope:** the uncommitted diff to `ARCHITECTURE-SPINE.md`. AD-48 now covers `str` and `str | None` only. Its rewritten "Named limit" says an unclassified list or nested field is a low-stakes hole, because `ModelPort` accepts outside text only as `Sanitized` (AD-12), and "a `Sanitized` cannot be built around unsanitized text — so an unclassified field cannot reach a model raw, whether or not it is declared." The list encoding moved into AD-27's additive-grammar bullets.

**Lens:** was every committed decision checked against reality, or only asserted?

**Verdict: revise before commit.** Moving the list encoding into AD-27 and narrowing AD-48 to what the code does are both sound. The argument that makes the remaining hole "low-stakes" does not hold. The code contradicts it, AD-12 contradicts it, and the last paragraph of AD-48 itself contradicts it.

---

## What was checked

- `pm_ai/ports/__init__.py`: `ModelPort.complete(*, task_class: TaskClass, instructions: str, external: Sequence[Sanitized]) -> str`, and its docstring.
- `pm_ai/domain/sanitize.py`: `Sanitized` (`@typing.final`, `@dataclass(frozen=True, slots=True)`, `__post_init__` fixed-point check) and `sanitize()`.
- Every caller or implementation of `ModelPort` in `pm_ai/`. There are none. `pm_ai/models/{local,frontier}` hold only empty `__init__.py` files. The only references are docstrings and comments in `core/extraction.py`, `app/pipelines.py`, and `domain/events.py`, plus the mypy fixture `tests/architecture/fixtures/model_port_misuse.py`.
- `pm_ai/domain/events.py`: `_assert_payload_text_is_declared`, `_is_text`, and the "known limit" docstring on `TRUSTED_TEXT`.
- AD-12 in the spine (lines 237–245).
- Experiments run with `uv run python -c` and `uv run mypy` (below).

## AD-12 is the right reference

Confirmed. AD-12 (line 243) is where the spine states "The port accepts only that type for externally-sourced text". AD-15 (line 265) states the one-port routing rule. The `(AD-12)` citation in the new text is correct.

---

## Findings

### F1 (high): the "cannot reach a model raw" claim is false as written. `instructions: str` is an open door, and both the code and AD-12 say so.

`ModelPort.complete` takes `instructions: str`. The port's own docstring says this is a bound on what it can promise: "`complete(instructions=f"Summarise: {provider_text}", external=())` type-checks, and no signature can refuse it". AD-12's "Three limits" paragraph (line 245) repeats it: a caller who interpolates provider text into `instructions` type-checks.

Checked with mypy. This scratch file:

```python
def labels_in_instructions(model: ModelPort, labels: tuple[str, ...]) -> str:
    return model.complete(task_class=TaskClass.CLASSIFICATION,
                          instructions="Labels: " + ", ".join(labels), external=())
```

gives `Success: no issues found`. This is exactly the list-of-labels case the named limit calls low-stakes. An unclassified `tuple[str, ...]` field is the field most likely to be joined into a prompt string, because nothing marks it as outside text.

So the declarations are not "a classification record, not the guard". For `instructions`, they are the only thing that tells a caller which text must go into `external` instead. AD-48's own final paragraph says this: "nothing yet requires a caller assembling a prompt to sanitize all of them rather than some." A field in neither record is one the caller has no way to know about.

### F2 (high): the guard it leans on does not exist yet, and one path already skips it.

AD-12's third limit: "nothing in `pm_ai/` implements or calls `ModelPort` yet". Confirmed. There is no adapter and no caller. Its second limit: the transcript path never reaches the port, because `Extraction` flattens the pair into bare `raw: str` / `for_model: str` (`core/extraction.py:23-29`). Story `11b` owns that fix. The claim "an unclassified field cannot reach a model raw" talks about a mechanism that has no call sites. AD-12 deliberately reads it as "enforced for anyone who writes the call". The AD-48 text drops that qualifier.

### F3 (medium): "a `Sanitized` cannot be built around unsanitized text" goes beyond what `__post_init__` checks.

Results from `uv run python -c`:

- `Sanitized(raw=inj, for_model=inj)` with an injection phrase raises `ForgedSanitization`. The main claim holds.
- `Sanitized(raw=t, for_model=t)` for any `t` the regex does not match builds without calling `sanitize()`. The check is that `for_model` is a fixed point, not that the text went through `sanitize()`. The docstring accepts this on purpose, and it is output-equivalent. It still means "unsanitized" really means "text the current patterns would change". Under the recorded regex ceiling (see the memory note on regex sanitization limits), an injection the patterns miss passes this check.
- At runtime, a subclass with a no-op `__post_init__` builds over an injection string and passes `isinstance(x, Sanitized)`. `@final` is static only. AD-12 says "both are static guarantees rather than runtime ones", and that qualifier is missing here.
- `object.__setattr__(s, "for_model", inj)` on a frozen instance works. The check runs only when the object is built.

None of these is a new hole. AD-12 already states them. But a sentence that calls the guard a construction impossibility claims more than the checks prove. It should say "statically refused, for text the sanitizer's patterns would change".

### F4 (medium): the "revisit when…" trigger has already fired for nested types.

The new text says to revisit "when a payload first carries a list or nested type of text a person typed (… attendee display names)". `WorkItemPayload.assignee: Actor | None` (`events.py:89`) and `NormalizedEvent.actor: Actor` (`events.py:436`) already hold `Actor.display_name: str | None`, which is provider-supplied. `events.py:282-284` names exactly these two as nested types carrying provider text today. The trigger should say it is already met for nested types and the hole is open, or it should name a different trigger.

### F5 (low): the containers list is shorter than the code's.

The named limit lists `tuple[str, ...]` and nested types. The code's own limit note (`events.py:285`) also names `list[str]` and `dict[str, str]`. Both pass `_is_text` as non-text and are neither required nor refused. Name all three, or say "any container of text".

---

## What holds

- AD-48's narrowed Rule now matches `_assert_payload_text_is_declared`. `_is_text` accepts only `str` and `str | None`. A declared non-text field raises. An undeclared container is ignored. The removed "Not yet built: … the reverse of this rule" gap between the rule and the code is gone.
- The list encoding in AD-27 is self-consistent: the list layer sits inside the line layer, there are no empty elements, an empty list is written as an explicitly empty value, and a list defaults to `None`. It sits in the right AD, since it is grammar and not trust. It is still unbuilt (nothing in `event_entries.py` implements it). The AD-27 enforcement bullet names `33d`, and that covers it.

## Suggested direction (not applied)

Keep the narrowing. Change the reason the hole is acceptable from "the port makes it unreachable" to the honest version: the hole is open, AD-12's three limits apply to it, and nothing calls a model yet, so no field can reach one today. Then make the revisit trigger the first real `ModelPort` caller (the same binding AD-48's "What it does not do" paragraph already names), together with the first `tuple[str, ...]` payload field. Note that the nested case already exists through `Actor.display_name`.
