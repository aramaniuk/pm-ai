# Adversarial review, second pass — spine update of 2026-10-11

**Lens:** build two units one level down that each obey every AD to the letter and still fail to fit together. Each pair below is a hole, with the tightening that closes it.

**Scope:** only whether the new identity text closes the first pass's holes (A1–A9 in `review-adversarial-2026-10-11.md`) and whether it opens new ones. The new text is AD-34 item 2 ("Actor resolution — at read time"), the AD-27 enforcement paragraph that replaces `unresolved:<system>:<handle>`, the AD-48 trusted reason for `mentions`, and the Open Risks row on the alias table. Inputs also read: the last seven memlog entries; slice specs `33d`, `23c`; code read but not changed: `pm_ai/domain/identity.py`, `pm_ai/core/normalize.py`, `pm_ai/storage/service.py:1702`, `pm_ai/skills/registry.py:61,112`, `pm_ai/domain/event_entries.py:161`, `pm_ai/connectors/graph/__init__.py:894-1134`, `pm_ai/connectors/gitlab.py:258`.

**Verdict:** the read-time decision closes the first pass's biggest holes. One producer and one spelling closes A1 and A5. Making the line the authority closes A3. One system per kind of handle closes A2. The lowercase rule closes A8. A6 goes away, because no actor id is ever written to a line. But four holes remain or are new, and a builder of `33d` or of the handle slice meets the first two straight away:
- the sentinel was refused without a replacement for "no person behind this";
- write-time resolution still exists in AD-36's third mechanism and in the envelope row;
- self-action lines are left out of the rule, but share its column;
- "the PM" has three definitions.

---

## What the new text closes

| First-pass hole | Status |
| --- | --- |
| A1 — nothing produces the reserved spelling; the envelope writes the bare sentinel | **Closed in text.** One producer in `domain`, the envelope actor included, sentinel refused. Owed by the slice before `33d`. |
| A2 — the Graph alias key is an email in code and the user id in the spine | **Closed.** `graph:` means a user id and `email:` means an address. The calendar moves to `email:`. |
| A3 — write-time resolution fixes a wrong alias into Tier 1 | **Closed.** The line holds the handle, and a reader resolves it. |
| A4 — the PM's identity set | **Partly closed.** See H4. |
| A5 — no shape check, so 33d's raw ids pass | **Closed in text** ("a raw string without a system ... refused"). But `33d`'s frozen text and matrix still say "as Microsoft sent them" and "actor unresolved". See H1. |
| A6 — no rule for minting actor ids | **Closed.** No actor id appears on a line any more. It returns when the alias table is persisted (A7, still open, and correctly recorded in Open Risks). |
| A7 — the alias table has no owner | **Open, and recorded.** That is acceptable, because nothing in wave 2 reads it. |
| A8 — letter case | **Closed** for `graph` and `email`. |
| A9 — 8q's guest fallback | **Closed** by the memlog's tenant-match rule. |

**Angles checked and found not to be holes:**
- **The dedup natural key** `(scope, source_system, source_ref)` contains no actor, and no `source_ref` grammar embeds one. So a corrected alias cannot change dedup.
- **A person handle against a scopeless ref.** Both are two-part `<system>:<x>`, but they never share a field: refs live in `src` and `target`, handles in `actor`, `p.assignee` and `p.mentions`. A reader always knows which one it holds from the field it read. The residue is low and is folded into H3's tightening: keep the two system-name sets disjoint.
- **A display name before the alias table exists.** `23c` renders the handle verbatim and says so. That looks ugly but it is consistent, so it is not a mismatch between builders.

---

## H1. A line with no person behind it has no spelling (severity: high; the `33d` builder meets it on day one)

**The new text** refuses the bare `actor_unresolved` sentinel, a display name, and a string without a system. It gives a spelling only for a handle that exists. It says a mention with no user "is not a member", but that rule covers list members only. The envelope actor is required on every line (CAP-10, `ledger.py:106`), so it cannot simply be left out.

**Where the case already exists:**
- **33d's frozen matrix:** "Sender is a bot (`from.application` set) — emitted; actor unresolved". Teams system messages also have a `from` of null.
- **The Graph calendar:** an organizer or attendee with a null `emailAddress` resolves to `UNRESOLVED` today (`graph/__init__.py:1113`). Under `email:` there is no address to spell.
- **GitLab:** a commit with no `author_email` (`gitlab.py:258`).
- **Transcripts:** a VTT speaker label is a display name, so it is refused. AD-34's own Prevents line names the speaker label as one of a person's handles. AD-32 says a VTT label carries no provider identity. So an event from a manual transcript has nothing legal to write as its actor.

**The pair:**
- *Builder A (33d)* writes a bot sender as `graph:<application id>`, because it is a Graph id and lowercased. That breaks the rule that `graph:` always means a user id. It also lets an app id join a person through the alias table.
- *Builder B (33d)* drops the bot message, because no legal actor exists. That breaks 33d's "emitted".
- *Builder C (the transcript slice)* invents `vtt:<label>`, which is a system nobody declared and carries typed text, unsanitized, into the trusted actor column.

Each one follows the new AD-34 text exactly as written, and no two of them produce the same line.

**Tightening (AD-34 item 2):** give absence and non-person senders a declared spelling.
1. **Application senders.** A provider application, such as a Graph bot or a GitLab bot token, is `<system>_app:<app id>`, for example `graph_app:<id>`. It is a system of its own, never mapped to a person by the alias table, and lowercased.
2. **Absence.** When no handle exists (null sender, null address, no author), the line uses one fixed value, for example `none:`. The producer is the only thing that emits it, and the mapping function maps it to "no person". It is not a person, so a reader never counts it as one.
3. **Speaker labels.** A speaker label from an untrusted transcript is never a person handle. Its event's actor is the absence value, and the label stays in untrusted text. A provider-authenticated speaker (AD-32) uses that provider's user-id system.

After that, renegotiate 33d's "actor unresolved" row and its "as Microsoft sent them" sentence before 33d is built.

## H2. Write-time resolution survives in AD-36 and the envelope row (severity: high)

**AD-36 mechanism 3** still says: "actor resolution (AD-34) marks it independently". Normalization decides `authored_by` and writes it to the line. The code does this through `event.actor.is_pm_ai` (`normalize.py:82`), which is an alias lookup done at write time. The **Event envelope row** in Consistency still says `actor` is a "resolved `Actor`". The **AD-48 Named limit** still describes `NormalizedEvent.actor` as an `Actor` that holds a display name.

**The pair:**
- *Builder A (the handle slice)* follows AD-34. `NormalizedEvent.actor` becomes a handle, and `is_pm_ai` can never be true for one. Mechanism 3 then dies silently. A change made by pm-ai's own Graph or GitLab bot account is attributed `EXTERNAL`. `EXTERNAL` is the one value AD-36 accepts as evidence, so pm-ai's own write can verify pm-ai's own promise. That is exactly the hole AD-36 exists to close.
- *Builder B* follows AD-36 and the envelope row. It keeps `Actor` on the event, resolves at normalization to decide provenance, and writes `actor.actor_id` (`service.py:1702`). The line then carries a resolved id. That is the write-time form Andrei rejected.

Both builders are reading spine text that is in force.

**Tightening:**
- **AD-36 mechanism 3:** pm-ai's own bot accounts are a declared set of person handles (for example `graph_app:<id>` or `gitlab:<bot user id>`), recorded at enrolment. Normalization compares the handle on the line against that set. It never asks the alias table, so provenance stays a write-time fact that does not depend on any alias.
- **Envelope row:** `actor` is a person handle (AD-34), not a resolved `Actor`.
- **AD-48 Named limit:** the display name no longer travels on a line, so the limit closes for `actor` and `assignee`. If a later slice wants a name on the line, it adds a separate payload field declared untrusted, never a field inside the handle.
- **The handle slice:** make `NormalizedEvent.actor` a domain value type that only the producer can construct.

## H3. Self-action lines share the actor column but sit outside the rule (severity: medium)

**The new rule** covers "every person value on an *observed* event's line". Self-action entries write the same envelope `actor=` column with three spellings today, none of them a handle:
- `pm-ai` (`DAEMON_ACTOR`, `event_entries.py:161`, used by `entry.py:406` and `wiring.py:580`);
- `actor_pm_ai` (`PM_AI_ACTOR`, `identity.py:312`);
- the skill's qualified name `<system>.<skill>` (`registry.py:112`).

The producer "refuses a raw string without a system". The spine does not say whether the envelope writer checks this on every entry or only on observed entries.

**The pair:**
- *Builder A (the handle slice)* puts the check in `render_entry`, the one envelope writer. Every daemon and skill self-action entry is then refused.
- *Builder B* puts the check only in the observed-event path. A reader that maps the `actor` column of every line, such as an activity view or a per-person metric over the whole log, then calls the mapping function on `pm-ai` or `gitlab.create_mr`, and the function must either refuse it or guess.

Both readings are fair, and A breaks the build.

**Tightening (AD-34 item 2, one sentence):**
- A self-action line's actor is a declared pm-ai identity, never a person handle. Choose one spelling, for example the single constant `pm-ai`, and record the acting component in a field, as `skill_invoked` already does with `target`. This retires `actor_pm_ai` as a second spelling.
- The handle check runs where observed events are written. The mapping function's input is the actor column of an observed-type line, and a reader picks between the two by the entry's vocabulary.
- Declare the set of person-handle system names closed in `domain` (today `graph`, `email`, plus H1's `*_app` and the absence value), each with its handle kind and case rule. Keep it disjoint from the scopeless-ref set (`meeting`, `goal`). Then the next connector cannot mint `gitlab:<username>` in one slice and `gitlab:<numeric id>` in another. GitLab usernames can be renamed, so they are the wrong key.

## H4. "The PM" has three definitions (severity: medium)

**The spine now says** `23c` decides "mentions you" by `graph:<8q user_id>` for each **enabled** Graph row, "plus any handle the alias table maps to the PM". It leaves three things open:
- **The PM has no defined pm-ai person.** No id or entry says which person in the alias table is the PM.
- **23c's frozen Never list** still says "No name resolution, no alias table read". That contradicts the "plus" half of the spine's rule.
- **AD-32** needs "the speaker resolves to the PM" in order to auto-execute, and it is never tied to the same set.

**Read time adds a new consequence.** The set is computed from today's config, so disabling or removing a Graph row stops marking mentions on every past line from that tenant. A past dashboard re-rendered by reindex then changes its history.

**The pair:**
- *Builder A (23c)* follows its frozen text: Graph rows only, no alias read, enabled rows.
- *Builder B (23c)* follows the spine: Graph rows plus the alias table, which it reads through the module dict that tests fill.
- *Builder C (the AD-32 auto-execute slice)* defines the PM as a settings key `me`.

When the alias table is persisted, A and B disagree about the same line, and C disagrees with both about who may give an order.

**Tightening (AD-34 item 2, one sentence):** there is one function in `domain`, `pm_identity()`. Its answer is:
- the set of `graph:<user_id>` handles recorded by `8q` on every Graph row ever enrolled, not only the enabled ones, so that history stays fixed;
- then, once the alias table is persisted, every handle mapped to a fixed PM person id that the table's slice declares.

`23c` and AD-32 both call this function. Renegotiate 23c's Never line to say "no alias read until the table is persisted; then only through `pm_identity()`".

---

## Ranked: what to close first

1. **H1** — a spelling for absence and for application senders, plus the speaker-label rule. The handle slice cannot finish without it, and 33d's frozen matrix needs it.
2. **H2** — AD-36 mechanism 3 compares declared bot handles, the envelope row is restated, and AD-48's named limit is closed. Otherwise the handle slice silently brings back the AD-36 evidence hole.
3. **H3** — one self-action actor spelling, the handle check limited to observed writes, and a closed, disjoint set of handle systems.
4. **H4** — `pm_identity()` shared by 23c and AD-32, built over every row ever enrolled.
