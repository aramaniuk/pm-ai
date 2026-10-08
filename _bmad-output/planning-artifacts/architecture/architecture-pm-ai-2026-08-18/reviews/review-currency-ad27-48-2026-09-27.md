# Currency / reality-check review — AD-27 additive grammar, AD-48 list fields, two Deferred items

Date: 2026-09-27
Scope: the uncommitted diff to `ARCHITECTURE-SPINE.md` only — AD-27's new "entry grammar evolves additively" paragraph, AD-48's extension to `tuple[str, ...]` fields, and the two edited Deferred bullets.
Lens: every factual claim checked against the repository, since the change names no new technology or library version.

## Verdict

**Revise before committing.** The direction holds up. The claims that describe today's code are mostly accurate: `parse_line` does ignore unknown fields, and `key=` does round-trip as an empty string distinct from an omitted field. But four claims are false as of this commit:

1. A test pinning field names is described as existing, and it does not.
2. The line "a reader treats a missing field as absent" is broken by the parser's own required-field check.
3. The empty-list encoding collides with a one-element list holding an empty string.
4. "Tier 1's part settled" is wider than the rule, which covers only `event_log/`. Two other Tier-1 grammars refuse unknown keys, and one has already removed a key.

No web research was needed, because no library, version or external technology is named.

## Method

- Read `render_value`, `render_entry`, `scan_fields`, `_read_value` (`pm_ai/domain/event_entries.py`), `parse_line`, `parse_segment` (`pm_ai/core/ledger.py`), `_payload_fields` (`pm_ai/storage/service.py:274`), `_assert_payload_text_is_declared` and `_is_text` (`pm_ai/domain/events.py:295–400`).
- Read every consumer of `entry.fields` (`core/rendering.py`, `core/retrospective.py`, `core/event_log.py`, `core/ledger.py`), and every other `scan_fields` caller (`core/disclosure_ledger.py`, `core/meeting_records.py`).
- Ran round-trip experiments with `uv run python -c ...` (outputs quoted below).
- `uv run pytest -q`: 2661 passed, 24 skipped.

## Findings

### F1 — High — "A test pins every payload class's field names and both enumerations' members" describes a test that does not exist

The sentence is in the present tense and carries the whole enforcement weight of the additive rule. The closest existing tests:

- `tests/core/test_payload_serialisation.py:64–82` and `tests/architecture/test_sanitize_boundary.py:984` read the field names *from* `dataclasses.fields(...)` at test time. They follow a rename rather than fail on it. `test_a_new_payload_field_needs_no_registry_change` exists specifically so that no field-name list has to be maintained.
- `tests/domain/test_event_entries.py:47–54` checks that every member *is typed*, not which members exist. Removing a member passes.
- `test_compaction_declares_the_checksums_it_must_record` pins one `SELF_ACTION_FIELDS` tuple. That covers one self-action out of four, and no payload class.

So today a removal or rename passes the suite, which is exactly what the sentence says cannot happen. Either write the test in the same slice, or restate the sentence as a requirement ("a test must pin…") and add it to the story that implements it. This is the "no invented evidence" rule applied to the spine.

### F2 — High — "A reader treats a missing field as absent" is false for self-action entries, and the rule as written allows the change that breaks it

`parse_line` builds an `EventEntry`, and that runs `__post_init__` to check `SELF_ACTION_FIELDS` as a required minimum. Experiment:

```
parse_line("- [evt_1] goal_set actor=pm-ai goal_id=g1 domain=d horizon=h title=t")
-> MalformedEntry: goal_set must record ['channel']; ...
```

A field is "added" to a self-action by appending it to `SELF_ACTION_FIELDS`, which is additive under the new rule. From that moment every earlier line of that type is refused while reading, and `parse_segment` fails the whole segment. The rule allows this edit and forbids its consequence. The paragraph needs to say one of two things:

- **(a)** Requirements are checked when a line is written, never when it is read. The fix is to move the check out of `__post_init__` or bypass it on the parse path.
- **(b)** A field added to a type is optional forever, so `SELF_ACTION_FIELDS` membership is frozen once a type has written lines.

Related, and lower severity: rendering and retrospective already use `dict(fields).get(...)`, so they treat a missing field as absent. That is correct, but it also makes a reader/writer name mismatch silent. `core/rendering.py:656–658` reads `channel` and `excerpt`, while production writes `p.channel` and `p.excerpt` (`_payload_fields(MessagePayload(channel="#a", excerpt="hi"))` returns `(('p.channel','#a'),('p.excerpt','hi'))`). `tests/core/test_rendering_sections.py:104` builds its fixtures with the unprefixed key, so the mismatch passes. Under "missing means absent" this reads as "not recorded" and never raises. A pin on the writer's field names (F1) does not catch drift on the reader's side. The rule should say what does. That could be a test that parses a line from the real writer through each reader, or a shared constant for each field name.

### F3 — Medium — The empty-list encoding is ambiguous with a one-element list holding an empty string

AD-48 says an empty list is "an explicitly empty value", and that elements are joined by `,` with `,` and `\` backslash-escaped. Experiment, using exactly that encoding through `render_entry`/`parse_line`:

| tuple | line | read back |
|---|---|---|
| `()` | `p.labels=` | `''` |
| `("",)` | `p.labels=` | `''` |
| `("","")` | `p.labels=,` | `','` |
| `("a,b","c")` | `p.labels="a\\,b,c"` | `'a\\,b,c'` |
| `("a\\","b")` | `p.labels="a\\\\,b"` | `'a\\\\,b'` |

Two things hold up. First, the escaping layers do nest unambiguously. `render_value` quotes any value that contains `\`, and doubles each one. `_read_value` undoes exactly that, so the element-level `\,` and `\\` reach the list decoder intact, and `,` alone never forces quoting. Second, `key=` and `key=""` both read as `''` and both differ from an omitted key, so "explicitly empty" can be written and read back as claimed.

But `()` and `("",)` render to the same bytes. Either forbid empty-string elements (a check when the payload is constructed), or choose a distinct token for the empty list. Note that `UNKNOWN_VALUE = "unknown"` already shows why a bare token can collide with real data. Say which one in the rule.

The rule also describes a mechanism nothing implements yet. `_payload_fields` does `str(getattr(...))`, so a `tuple[str, ...]` field today writes its Python repr: `('p.labels', "('a', 'b')")` and `('p.labels', '()')`. The paragraph should say the encoding is a requirement on `_payload_fields`, not something the code already does. The same `str()` also writes a nested `Actor` as `"Actor(actor_id='u1', display_name='Ann')"` today. The "named limit" paragraph covers this for sanitization but not for serialization.

### F4 — Medium — AD-48's extension contradicts the check as it runs today

`_assert_payload_text_is_declared` (`events.py:311+`) uses `_is_text`, which accepts only `str` and `str | None`. A `tuple[str, ...]` field today:

- is **not required** to be declared (the `unaccounted` check skips it), and
- is **refused** if declared (`not_text`: "Only `str` and `str | None` can be sanitized").

So the new sentence "every `tuple[str, ...]` field … is declared in one of two records" cannot be satisfied, and a payload that tried would fail at import. The `TRUSTED_TEXT` docstring (`events.py:279–287`) still names containers as the known limit. No registered payload has a tuple field today (`WorkItemPayload`…`MeetingHeldPayload` hold only `str`, `str | None`, `int`, `Actor | None`), so nothing breaks now. But the spine describes a rule the code rejects, and that should be marked as not yet built. Also left open: whether `tuple[str, ...] | None` counts (the rule does not say), and whether `list[str]` does, which the old limit named and the new text drops without comment.

### F5 — Medium — "Tier 1's part settled" is wider than what AD-27 settles

AD-27's rule covers the `event_log/` entry grammar. Tier 1 also includes `disclosure.md` and `meetings/` (spine line 90), and both are parsed with `scan_fields` under the opposite policy:

- `core/meeting_records.py:737–743` **refuses** an unknown key ("Refused rather than ignored") and has already **removed** one, `tentative` on 2026-09-07. That is a non-additive change the new rule forbids.
- `core/disclosure_ledger.py:80–86` **refuses** a line missing any of eight fields. It also already writes a comma-joined list (`scopes`) with no escaping, and marks absence with a sentinel (`destination == _NONE`). That is a second, conflicting list-and-absence convention inside Tier 1.

So the claim is not settled for Tier 1 as a whole. The Deferred bullet should read "the `event_log/` grammar's part settled". It should also either extend the rule to the other Tier-1 line grammars or say explicitly that they are outside it. If they are outside it, the Deferred item about the disclosure ledger recording the sanitization rule, which cites "the Tier-1 entry grammar", needs revisiting, because the disclosure grammar is not the one AD-27 now governs.

### F6 — Low — The additive rule protects old lines read by new readers, not new lines read by old readers, and the text implies more

"every line ever written stays readable under every later grammar" is correct as stated, and `parse_line` does ignore unknown field names. But `category()` raises `UnknownCategory` on an unknown type, deliberately (`ledger.py:127`). So an older binary reading a segment that a newer one appended to refuses the whole segment. The same happens if a restore or a second checkout runs an older version. That is acceptable for a single-writer, single-machine tool, but the rule should state the direction: readable under later grammars, and not promised under earlier ones.

### F7 — Low — The struck-through Deferred bullet keeps text that is now wrong

The "Versioning the two closed vocabularies" bullet still ends with "It becomes real the first time the grammar changes…", and still lists three options as open. The strikethrough is on the title only. And the Deferred item directly above it, about the disclosure ledger recording the sanitization rule, still says the grammar "has no versioning mechanism, which is the deferral directly below". That item may now be open again under the additive rule, subject to F5. The code comment at `event_entries.py:61–70` still points to `deferred-work.md` for a decision the spine now calls made.

## Claims confirmed

- `parse_line` ignores unknown field names. There is no allow-list, and `zz_new=1` round-trips.
- `key=` and `key=""` both read back as `''`, distinct from an omitted field. `_payload_fields` already omits `None`, and `test_an_unset_optional_payload_field_is_omitted` pins that.
- A comma-joined, backslash-escaped element list survives `render_value`/`_read_value` without ambiguity for non-empty elements, including elements with `,`, `\`, spaces and `"`.
- The removed `GRAMMAR_VERSION` history matches the code comment at `event_entries.py:61–70`.
- The full suite passes on the unmodified code (2661 passed, 24 skipped). No spine claim is tested by it.
