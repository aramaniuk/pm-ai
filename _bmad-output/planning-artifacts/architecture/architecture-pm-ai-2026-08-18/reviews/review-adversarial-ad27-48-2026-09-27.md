# Adversarial review: AD-27 additive-grammar rule and AD-48 list fields (2026-09-27)

**Lens:** build two units one level down that each follow every AD exactly and still don't fit together. Only holes that the 2026-09-27 text creates or leaves open are counted.
**Target:** the uncommitted diff to `ARCHITECTURE-SPINE.md` (AD-27 rule, AD-48 rule and its named limit, two Deferred entries) and the three new `.memlog.md` entries.
**Checked against code:** `pm_ai/domain/event_entries.py`, `pm_ai/core/ledger.py`, `pm_ai/storage/service.py` (`_payload_fields`), `pm_ai/domain/events.py`, `pm_ai/core/rendering.py`. Every claim marked *measured* was run with `uv run python` against the real functions.

## Verdict

**Do not start 33d on this text as written. It needs four tightenings, all small.** The direction is right. Additive-only with no marker is the cheapest scheme that can work on a hand-grepped file, and settling it before anything has written a segment is the right time. The text still has three gaps:

1. The pin test protects the thing that is not at risk (the dataclass field names). It does not protect the three things readers actually depend on: the wire name, the type/encoding, and whether a field is required.
2. "A reader treats a missing field as absent" turns every writer/reader naming mismatch into a silent empty read. That mismatch already exists in the code today (F1).
3. The list encoding sits on top of the line's own escaping, and the spine does not say how the two layers relate. Two compliant decoders therefore disagree on hand-edited lines, and the empty list collides with a one-element list (F4).

Nine findings. Three are measured in the running code (F1, F2, F4). The rest are built against unbuilt paths (33d, 23c, story 19, Tier-3 rebuild).

---

## F1 — The writer and the one existing reader already disagree on payload field names, and the new rule makes that silent. *(measured, highest)*

- **Unit A, the writer** (`service.py:1619`, `_payload_fields`): writes `MessagePayload` fields as `p.channel`, `p.excerpt`. The `p.` prefix is there so a payload field can never shadow an envelope field.
- **Unit B, the reader** (`core/rendering.py:656-658`, the Proactive Enablement signal line from 23a): reads `dict(entry.fields).get("channel")` and `.get("excerpt")`, with no prefix. Its tests (`tests/core/test_rendering_sections.py:104`, `tests/core/test_project_rendering.py:124`) build entries by hand with the unprefixed names, so they pass.
- **Result:** once 33d writes real `message_posted` lines, every signal line shows the actor only, with no channel and no excerpt. No test fails. Under the new AD-27 sentence "a reader treats a missing field as absent", this is *correct behaviour*: the rule turns a naming bug into a valid read.
- The memlog fact "nothing in pm_ai reads p. payload fields back yet" is wrong. One reader exists and it reads the wrong names. 23c will be the second reader, and a Tier-3 rebuild the third. Each will decode `p.*` by hand.
- **What the pin test misses:** it pins *dataclass* field names. The readers look up *wire* names. Nothing ties the two together.
- **Close:** add to AD-27 or AD-48: *the wire name of a payload field is `p.<field>`, and it is produced and consumed by one pair of functions in `domain`: `_payload_fields` and its inverse, which rebuilds the payload from an entry. No reader looks up a payload field by a string literal.* Enforce it with a round-trip test over every class in `PAYLOAD_FOR` (payload → line → `parse_line` → payload, compared for equality). That test also catches F3 and F4.

## F2 — Adding a required self-action field is "additive" and makes every older segment unreadable. *(measured)*

- The AD-27 text allows "a payload field or an envelope field may be added". Self-action fields (`SELF_ACTION_FIELDS`) are neither, so the rule says nothing about them. The pin test covers "every payload class's field names and both enumerations' members", which does not include `SELF_ACTION_FIELDS` either.
- `SELF_ACTION_FIELDS` is a *required floor*, and `parse_line` builds an `EventEntry`, which runs that floor check in `__post_init__`. *Measured:* a `goal_set` line without `channel` makes `parse_segment` raise `MalformedEntry` **for the whole segment**.
- **Unit A:** a story adds `reason` to `SELF_ACTION_FIELDS[SECURITY]`. That feels additive, and nothing is removed or renamed. **Unit B:** the dashboard, retrospective and rebuild reading a month that already has `security` lines. Every one of them now fails on that month.
- The reverse also slips through: dropping `channel` from `GOAL_SET`'s floor passes the pin test.
- **Close:** (a) name self-action fields in the rule alongside payload and envelope fields. (b) Say that a field added to an *existing* category is optional on read. The floor applies at *write* (`_assert_recordable` / the writer), never in `parse_line`. (c) Include `SELF_ACTION_FIELDS` in the pinned set.

## F3 — "Added" doesn't say "optional". Making an Optional field required, or changing its type, keeps the name and passes the pin test.

- The rule forbids removing, renaming, and new meaning. It does not forbid:
  - **adding a payload field with no default** (`WorkItemPayload.priority: str`): every older line lacks `p.priority`, so the F1 inverse cannot build a payload from it;
  - **tightening `title: str | None` to `title: str`**: older lines legally omitted `p.title` (None is omitted, `service.py:295`), and those lines now cannot be read back;
  - **changing a field's type while keeping its name**, e.g. `excerpt: str` becoming `tuple[str, ...]`. An old line `p.excerpt="see a, b"` then decodes as two elements. Under AD-48's list encoding, a type change *is* a change of meaning, but the text treats "meaning" as author judgment only.
- **Who decides "a new meaning":** nobody is named, and nothing mechanical catches it. For example, `WorkItemPayload.state` switching from the provider's workflow name to a normalised pm-ai enum would keep the name, type and pin, and every older line would be misread.
- **Close:** pin `(field name, resolved annotation)` rather than names. A field added to an existing class must be `X | None = None`, or `()` for a list only where F5 allows it. A pinned annotation may never change. For meaning: give the pinned record a one-line meaning per field, so a change of meaning has to show up as a diff to that record and gets reviewed. Author judgment then at least has a place where it must be written down.

## F4 — The list encoding collides with the line's own escaping in two ways. *(measured)*

Tested with the actual `render_value` and `scan_fields`, applying the AD-48 element escaping first:

| list | encoded | on the line | line-decoded |
|---|---|---|---|
| `()` | `` | `p.m=` | `''` |
| `("",)` | `` | `p.m=` | `''` |
| `("a,b",)` | `a\,b` | `p.m="a\\,b"` | `a\,b` → one element ✔ |

- **(a) Empty list and one-empty-element list are the same bytes.** Two decoders that both follow AD-48 can return `()` or `("",)`. This is the very absent/none/empty distinction the text says it protects, one level down.
- **(b) A hand edit decodes differently depending on quoting.** AD-3 promises hand-editable Tier 1. A PM who types `p.mentions="a\,b"` (one backslash, quoted) gets `a,b` after the line decoder, because `_read_value` falls back to "next character literally" for an escape it doesn't know. The list decoder then sees **two** mentions. Typing the same thing bare, `p.mentions=a\,b`, gets **one**. The visible content is identical and the membership differs.
- The spine never says the list layer runs *inside* the line layer (encode elements, then `render_value`; decode the line, then split). So one implementer can split before unescaping and another after.
- **Close:** say that the list layer runs inside the line layer. Say that the list decoder accepts only `\,` and `\\`. Say that the line decoder refuses unknown escapes instead of passing the next character through, which is a one-line change in `_read_value`. Either forbid empty elements (refuse at construction) or give `("",)` its own spelling. Put the encoder and decoder in `domain` next to `render_value` (the same F1 pair).

## F5 — The encoding keeps "none" and "not recorded" apart, but the field's default merges them again.

- AD-48 writes `()` explicitly because an omitted field means *not recorded*. It never says what a list field's **default** is.
- **Unit A, 33d:** declares `mentions: tuple[str, ...] = ()`, the natural default for a frozen dataclass. A channel-message path that never asks Graph for mentions (or a later Slack connector) builds the payload without the field and writes `p.mentions=`, which reads as "nobody was mentioned".
- **Unit B, 23c:** works out "PM was not mentioned" from the empty list, exactly as the mentions decision says, and ranks the message down.
- Both units follow every AD. The line says *none* when the truth is *didn't look*.
- **Close:** a list payload field is `tuple[str, ...] | None = None`. `None` is omitted (not recorded) and `()` is written empty (looked, found none). A producer passes `()` only when it actually got the provider's answer. This also means the AD-48 declaration check must accept `tuple[str, ...] | None`, which the current text ("every `tuple[str, ...]` field") does not cover. `_is_text` today recognises only `str` and `str | None`.

## F6 — `mentions` is trusted because of what it *should* contain. Nothing checks that it does.

- The trust reason is "provider identifiers nobody types". Graph's `chatMessageMention.mentioned` is a union: `user`, `application`, `conversation` (channel/team), or `tag`. A tag or channel mention has no user id. The obvious fallback in 33d, taking `displayName` or the tag name, puts typed text into a trusted field and so past the sanitizer.
- AD-48's trusted record is a *reason*, not a check. The field's population rule belongs to 33d, and the architecture doesn't bind it.
- **Close:** either (a) add to the mentions decision: *only `mentioned.user.id` values; any other mention kind is dropped or goes in a separate untrusted field*, and have the payload validate the element shape (AAD object id format) at construction; or (b) declare it untrusted. Per AD-29 that costs nothing, because the raw survives. (a) matches the decision's intent. Without the check it is the `getattr` guess again, this time with a reason attached.

## F7 — Sanitizing each element vs assembling a prompt from the whole list.

- AD-48 sanitizes each element of an untrusted list. The first prompt assembler (story 7) will reasonably join a list of labels or tags into one string. A payload split across elements (`"ignore previous"`, `"instructions and …"`) passes every per-element check and is whole in the prompt. The fail-closed size cap is also per element, so a list of N elements at the cap gets through at N times the cap.
- This is the "sanitizer per element, declaration per field" mismatch named in the brief. The declaration unit (field) and the enforcement unit (element) differ, and the spine doesn't say which one the prompt boundary sees.
- **Close:** one sentence in AD-48's "What it does not do", or on the field: *text reaching a prompt is sanitized in the form it reaches the prompt. A joined list is sanitized after joining, and the per-element pass protects only per-element uses.* Or define the unit as the joined field value and sanitize that.

## F8 — COMPACTION's `replaced` is already a list, it's outside the new encoding, and compaction's carry-forward can rewrite old lines.

- `storage-contract.md:115` shows `replaced=2026-06.md:d41d8cd9…`. Compaction replaces *several* segments, so `replaced` is a list of `name:md5` pairs. It is a **self-action** field, not a payload field, so AD-48's list rule does not bind it. Story 19 will pick its own joining (`,`? `;`? repeated keys, which `parse_line` refuses?). That is exactly the "each producer invents its own joining" the decision rejected, and it produces two list encodings in one ledger.
- **Carry-forward:** AD-5 says a milestone summary "carries forward the compaction entries of the segments it replaces". A compliant story 19 parses those entries and **re-renders** them with the current `render_entry`. Every line stays readable, as the rule promises, but the bytes change. The quoting of an old value may differ, any field later added to the writer gets stamped on, and the MD5-based restore check (AD-5, storage-contract §compaction) compares against content that no longer exists anywhere.
- **Close:** (a) AD-48's list encoding applies to every list-valued ledger field, self-action ones included (`replaced` is the first). (b) Add to AD-5 or AD-27: *carried-forward entries are copied byte-for-byte, never re-rendered*. Only then does "every line ever written stays readable" also mean "every line ever written stays the same".

## F9 — "Readable under every later grammar" is one direction only. Categories break the other direction, and envelope fields have no namespace.

- **Downgrade:** payload fields added later are ignored by an older reader, but a *category* added later makes an older binary's `category()` raise `UnknownCategory`, and `parse_segment` refuses the **whole segment**. On this project that is routine, not hypothetical: `uv run` from `main` after writing from a feature branch that added a member. The dashboard and retrospective then fail on the current month.
  - **Close:** either state that reading is backward-compatible only, and a newer segment under an older binary is out of scope (and say so in the refusal message), or have the reader skip and count unknown categories rather than refuse. The second weakens "closed". The first costs one sentence.
- **Envelope namespace:** payload fields got `p.` so they can't collide. Envelope fields and self-action fields share the one unprefixed namespace (`src`, `occurred_at`, `ingested_at` beside `goal_id`, `channel`, `target`…). Adding an envelope field is allowed, but an envelope field named `channel` or `target` would collide with a self-action field, and `parse_line` refuses repeated keys. The pin test can't see this, because it pins neither set.
  - **Close:** pin the envelope field names and the self-action field names as one set, and add a disjointness check at import next to `_assert_vocabularies_agree`.

---

## Also worth closing (lower stakes)

- **The type→payload mapping isn't pinned.** Moving `MERGE_COMPLETED` from `ReviewPayload` to a new `MergePayload` removes and renames nothing (both classes and both members still exist), yet every older merge line is read against the wrong shape. Pin `PAYLOAD_FOR` as well.
- **Deferred list:** the struck-through "Versioning the two closed vocabularies" entry keeps its full old body under the "Settled" marker. A reader skimming Deferred sees an open-sounding paragraph. Cut the body to one line.
- **`.memlog.md`** records a fact that is false (see F1). It should be corrected through the skill, not by hand, per AGENTS.md.

## What holds

- Rejecting the per-line token and the per-segment header is sound: each would break a property (grep-ability, every-line-is-a-record) that AD-3 and the append rule depend on.
- Writing an empty list explicitly rather than omitting it is the right call. F5 only asks that the default respect it.
- Recording ids rather than "PM was mentioned" keeps Tier 1 as observations and leaves room for 23c to derive from them. That is correct under AD-3's rebuild test.
