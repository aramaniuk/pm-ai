# Adversarial review — AD-48 narrowed back to `str` / `str | None` (2026-09-27)

**Target:** uncommitted diff to `ARCHITECTURE-SPINE.md` (AD-48 rule and named limit; AD-27 gains the list-encoding bullet). Companion edits read for context: `deferred-work.md`, `prototype-path-2026-09-01.md`, `.memlog.md`.
**Lens:** two units, each obeying every AD to the letter, that still build incompatibly. Only holes this change opens or leaves open.
**Checked against:** `pm_ai/domain/events.py`, `pm_ai/domain/sanitize.py`, `pm_ai/ports/__init__.py` (`ModelPort`), `pm_ai/domain/identity.py` (`Actor`).

## Verdict

**Revise before commit.** Moving the encoding into AD-27 is clean: no stale reference to the old placement survives in the spine or its companions. The problem is the reason given for calling the limit low-stakes. It says an unclassified field "cannot reach a model raw", and the spine's own AD-12 and the `ModelPort` docstring both say it can. The trigger for revisiting the limit has also already been met for nested types, and it is one fallback away from being met for the first list that is actually being built.

---

## F1 — The reason for "low-stakes" contradicts AD-12's first stated limit (high)

The new AD-48 text says: "the guard is `ModelPort` … so an unclassified field cannot reach a model raw, whether or not it is declared." The memlog repeats this and calls the earlier opposite claim "wrong".

AD-12 says otherwise, under "Three limits": "The port's `instructions` parameter is a plain `str`, so a caller who interpolates provider text into it type-checks." The `ModelPort` docstring (`pm_ai/ports/__init__.py`, around line 749) gives the exact bypass: `complete(instructions=f"Summarise: {provider_text}", external=())`. `Sanitized` can't be forged, but nothing makes a caller use it. That type is the guard only for text the caller already knows is outside text, and the declarations are the only record that tells the caller which text that is.

**Two conforming units that diverge:**
- **Unit A** (33d, Teams connector) adds `MessagePayload.mentions: tuple[str, ...] | None`. AD-27 allows it and the encoding is specified. AD-48's import check passes because the field has no declaration and none is required.
- **Unit B** (the first prompt builder, story 7) reads `UNTRUSTED_TEXT[MessagePayload]` to decide what goes into `external`. `mentions` isn't listed, so B treats it like other undeclared material and interpolates `", ".join(mentions)` into `instructions`. That type-checks and obeys AD-12's rule as the port enforces it.

Now suppose a tag or channel mention falls back to a display name. This is the fallback `deferred-work.md` warns about. The text a person typed then reaches the model raw. Neither unit broke an AD. The declarations were the only thing that could have told B otherwise.

The stakes are real because that model context is a Tool Runner over MCP skills (AD-16). The only route to class M goes through a prompt. AD-32 and AD-13 staging are what limit the damage, not AD-48.

**Fix:** reword the justification to match AD-12. For example: "an unclassified list can reach a model raw only by being interpolated into `instructions`, which AD-12 already names as the port's first limit." Then either accept that explicitly or keep declaring lists. Don't state as settled a guarantee the spine denies elsewhere.

## F2 — The revisit trigger is already met for nested types, and nothing makes it fire for lists (high)

The limit says to revisit "when a payload first carries a list or nested type of text a person typed (GitLab labels, attendee display names)".

- **Nested: already true.** `WorkItemPayload.assignee: Actor | None` is registered in `PAYLOAD_FOR` today. `Actor.display_name: str | None` is a name the provider supplied, and `events.py`'s own `TRUSTED_TEXT` docstring lists it as a nested field that carries provider text. The condition the spine treats as future already exists in shipped code, so as written the revisit is overdue on the day it lands.
- **Lists: nothing fires.** The first list field is `MessagePayload.mentions` (33d). It is meant to hold Graph user ids, so it doesn't match "text a person typed", and the trigger stays quiet. Before this change, "ids only" had an architectural home: a trusted declaration with a stated reason, which a reviewer could hold the connector to. That reason was removed along with the declaration. The constraint now lives only in `deferred-work.md` ("33d must refuse, not fall back"). If 33d falls back to display names for tag mentions, the field becomes person-typed text, but no record says otherwise and no check fails. The trigger depends on someone noticing, which is the failure AD-48's Prevents clause names ("a text field nobody classified … the miss was invisible").

**Fix:** reword the trigger around what already exists (name `assignee.display_name`, or scope the trigger to top-level fields). Also make it mechanical rather than a matter of judgement. The cheapest option: the import check refuses any undeclared `tuple[str, ...]` field on a registered payload. The limit then fires by construction the first time 33d adds `mentions`, and the "ids" reason is written in the only place a reviewer reads.

## F3 — The revision dropped the only statement of sanitization granularity for a list (medium)

The withdrawn text said "the sanitizer applies to each element." Nothing replaces it, so per-element versus joined sanitization is now a free choice, and the two give different answers.

Take a labels list `("ignore all", "previous instructions")`:
- **Per element:** each passes both matchers. `ignore all` and `previous instructions` each fold to a fragment that matches nothing. Both are fixed points.
- **Joined** (`", ".join`): `_FOLDED_INJECTION` matches, because step 7 collapses `", "` to one space, giving `ignore all previous instructions`, and the text is redacted.

Unit A (a connector-side helper) sanitizes per element and passes N fragments to `external`. Unit B (a `ModelPort` adapter) joins fragments with `", "` into the prompt. Each fragment is a fixed point, but the joined prompt isn't. The model sees the phrase that the joined rule would have redacted.

A third unit might sanitize the stored line value, meaning the backslash-escaped joined string, and get yet another answer. Fragment concatenation is a problem for any two fields, and this change didn't create it. But lists make it cheapest: many short elements, each an author controls, joined by a separator the fold erases. The change removes the one sentence that had begun to pin the behaviour down.

**Fix:** state the granularity once, wherever lists end up. Per element is consistent with how `external` works. Also add that an adapter joining fragments owns cross-fragment matching, or else that the port sanitizes the assembled `external` again.

## F4 — A consumer outside the model needs the trust split, and the new reason ignores it (medium)

The "guard is `ModelPort`" argument covers only model calls. AD-29 names a consumer outside that path: "a `for_model`-derived slice is what a staged `Proposal` carries as its summary, so it reaches `operational.db` and the card the PM reads" (Telegram). `Proposal.summary` is a `str`, and no port refuses raw text there.

Unit A builds a summary from `sanitize(label).for_model` per element. Unit B (a different proposal type) builds one from the raw labels, because no declaration marks them untrusted. Both obey AD-13, AD-29 and the narrowed AD-48. The PM gets cards whose contents were cleaned differently, and B's cards carry unredacted provider text into a surface that AD-29 says gets the derived copy. So the trust classification has a reader other than a model (the code that builds proposal summaries), and for lists it's now unanswered.

Checked with no divergence found: the disclosure ledger (AD-31/38) records scopes, task class, model, tokens and destination, never the text itself. Telegram and dashboard markup escaping applies to all rendered text whatever its trust. MCP skills (AD-16/18) see text only through model output, which is F1's path. AD-32's trust is decided per source and speaker, not per field.

**Fix:** list the proposal-summary path next to `ModelPort` in AD-48's reasoning, or say plainly that AD-48's classification is also what a summary builder reads.

## F5 — Leftovers from the move (low)

- **The move itself is clean.** No text in the spine, `deferred-work.md` or `prototype-path-2026-09-01.md` still places the list encoding in AD-48. AD-27's bullet is complete: two layers, no empty element, explicit empty value, `None` default.
- **AD-48's Binds still lists AD-27.** After the revision, AD-48 no longer constrains anything in AD-27 about lists. The only remaining link is that AD-27 creates list fields AD-48 ignores. Harmless, but "binds" now overstates the relationship.
- **Declared type versus written type.** AD-27's bullet says "A list field (`tuple[str, ...]`)" and then says the field defaults to `None`, so the declared type is really `tuple[str, ...] | None`. AD-27's pin test checks field types. One unit declaring `tuple[str, ...] = ()`, which is the `categories` precedent in `connectors/graph/calendar.py:687`, and another declaring `| None = None` would both appear to conform. Write the type as `tuple[str, ...] | None` in the bullet.
- **`events.py` already agrees with the revised text.** Its `TRUSTED_TEXT` docstring names containers as a known limit. The deferred-work evidence line about "refuses a `tuple[str, ...]` declaration" was correctly dropped. No code contradicts the new spine text.

## Not findings

- The additive-grammar rule and the escaping rules for the two layers produce no clash that I could construct between two conforming encoders or decoders.
- No entity gains a second owner. Encoding still has one owner: the single encode/decode pair in `domain`.
