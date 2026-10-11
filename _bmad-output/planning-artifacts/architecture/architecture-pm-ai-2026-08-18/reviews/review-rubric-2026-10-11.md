# Rubric review — 2026-10-11 update (AD-5, AD-9, AD-27, AD-48, gap row)

Scope: the changed lines in the 2026-10-11 diff (spine lines 173, 211, 368, 1025, 1116), the last seven memlog entries, and the code on `main` at 8bfac86 (slices 1q, 1r, 2m, 8p merged).

**Verdict: needs fixes before finalizing.** AD-5 and AD-9 ratify the code accurately, apart from small wording slips. The new `mentions` rule (AD-27 line 368, AD-48 line 1025) has three problems. The code does not do what the rule says. The rule leaves two builders free to diverge on how an unresolved member is spelled. Both slice specs it binds still contradict it. The gap row at 1116 still states, in its body, a problem that the new AD-5 text solved.

## Findings, ranked by consequence

### R1 — "The Graph handle is always the Graph user id" is contradicted by code already built (line 368; AD-34 line 459)
- Code: `pm_ai/connectors/graph/__init__.py:899-902` resolves the calendar organizer as `resolve_actor(system="graph", handle=row.organizer.email)`. The `graph` alias key is therefore an **email** for calendar events and, under the new rule, a **user id** for messages. One person would need two alias entries, so a sender, an organizer and a mention of the same person cannot be joined on the line. That is the divergence the rule exists to prevent.
- AD-34 line 459 still lists the native handles as "commit email, tenant account, speaker label", which does not say which Graph handle to use.
- Fix: in AD-34 item 2, state one handle per system ("graph: the Graph user id"). Record the calendar organizer as owed: it should resolve on `organizer.emailAddress`'s user id, or be named as an admitted exception with an owning slice. Alternatively, narrow line 368's sentence to `mentions` and senders of messages only, and say so explicitly.

### R2 — "Never the bare sentinel" has no shared producer, so two builders can diverge (line 368)
- `domain/identity.py:351 resolve_actor` returns `UNRESOLVED` (the bare `actor_unresolved`) on a miss. Nothing in `pm_ai` builds `unresolved:<system>:<handle>`. `payload_fields._refuse_sentinel` refuses the bare sentinel inside a list, so 33d must write its own fallback. 23c must build the same string independently to match `unresolved:graph:<PM user_id>`.
- `resolve_actor` lowercases the handle for lookup, but nothing says whether the unresolved spelling is lowercased. 33d and 23c could disagree on case, and the match would fail silently. `deferred-work.md:1081` already records that `resolve_actor` "still returns exactly that sentinel".
- The rule also says "the rule a sender … follow[s]". However, the envelope `actor` is written as `actor_id` (`storage/service.py:1702`), so every unresolved sender, including today's calendar organizer, lands on the line as the bare sentinel. The 2m exemption covers payload fields only.
- Fix: name one domain function as the only producer of the spelling, either `resolve_actor` returning `Actor("unresolved:<system>:<handle>")` on a miss or a sibling helper. Fix the case rule: the handle is written as received, and comparison is case-insensitive. Make 33d and 23c both use that function. Either extend the sender clause to the envelope actor as owed work, with a slice, or drop "a sender" from the sentence.

### R3 — AD-48 line 1025 states as built a trusted reason the code does not carry
- `pm_ai/domain/events.py:271`: `TRUSTED_TEXT[MessagePayload]["mentions"]` still reads "Graph user ids — Microsoft generates them…". `events.py:117` says "The Graph user ids the message mentions".
- The spine line sits inside "*Built 2026-10-11 (slice 2m)*" context, so a reader takes the actor-id reason as current fact.
- Fix: mark the restated reason as owed, naming an owner (33d is natural, since it fills the field). Or change the two code comments in the same slice that records the decision.
- The 2m tests `tests/domain/test_payload_fields.py:51` and `tests/core/test_payload_serialisation.py:190` use `unresolved:graph:dana@example.com`, an email the rule now forbids. The grammar is agnostic, so this is harmless, but the tests model a forbidden value. List them with the 2m spec correction.

### R4 — Spliced sentence at line 368: "a rebuild re-resolves it" and the stale trailing clause
- "A line written before its alias entry keeps the unresolved spelling; a rebuild re-resolves it" sits beside AD-27's "**A line is never re-rendered**" (line 367). A builder could read "rebuild" as rewriting Tier-1 lines.
- Fix: say "a reader (a Tier-3 rebuild, 23c's match) re-resolves it when it reads; the line is never rewritten."
- The insertion also moved "— and today's Python-repr spelling of it and of tuples is replaced before any real line exists" so that "it" now refers to a mention, not `assignee`. The clause is also stale, because 2m built the replacement.
- Fix: move the clause back after the `assignee` sentence and phrase it in the past tense ("was replaced by 2m before any line existed").

### R5 — Slice specs contradict the spine (report only; not edited)
- `stories/33d-graph-channel-messages.md:17` and `:33` say "`mentions` holds the Graph user ids … as Microsoft sent them". `:55` says "`mentions` holds the id". The spine now requires alias resolution with an `unresolved:graph:` fallback. `:31` is consistent for the sender, but see R2.
- `stories/23c-proactive-enablement-from-messages.md` has five conflicts:
  - `:17`, `:24` and `:44` match "the PM's own Graph user id" raw. The spine requires the PM's actor id or `unresolved:graph:<user_id>`.
  - `:36` says "No name resolution, no alias table read". Matching by the PM's actor id needs an alias read.
  - `:24` names slice `8m` as the one that saves `user_id`. The spine (line 368) and `stories.yaml:378` say `8q`.
  - `:47` (two tenants) needs restating per spelling.
  - `:26`/`:78` note that the alias table is in memory and "saved nowhere" (`identity.py:344`; `register_alias` is called only from tests). Today every mention therefore resolves to the unresolved spelling, and the "actor id" branch of the rule is unreachable until the table is persisted. The spine should say so, or name the slice that persists it.
- `stories/2m-payload-line-grammar.md:57` and `:116` use the email example, as the memlog already notes.

### R6 — Gap row (line 1116) still asserts what is now false
- The title still says "what actually holds the single-writer rule is undocumented". The middle column says the `flock` is something "which no AD names". The right column says `ArtifactBusy` is caught "for a condition AD-5 says cannot arise". All three are false after the line-173 text.
- Fix: strike the "undocumented substitute" half as closed 2026-10-09/11 and leave a daemon-only row (owner `4e`).

### R7 — AD-5 append bound omits the batch write (line 173)
- Line 173 says the record rule "holds only while one record is one write", with a measured bound of up to 1 MiB.
- `domain/event_entries.py:205-208` adds that "one harvest batch is a single write of many records with no bound of its own, so a batch above 1 MiB is outside what was measured". The record limit is therefore not the binding one.
- Fix: add the batch caveat, and either bound a batch's single write or name the slice that does (9a, 33d).

## Smaller wording points (AD-5, AD-9)
- "`config.json`, `meetings/`, the sealed token store and the project registry": the sealed credential store *is* `config.json` (`core/connector_enrolment.py:98 CREDENTIAL_STORE = "config.json"`). That is three artifacts, not four.
- "`ClaimHeld` is the adapter's spelling": the class is declared in `pm_ai/domain/claims.py`, not in the adapter. Say "the port's spelling". The sealed store also translates `ArtifactBusy` into `StoreBusy` for the connector (`app/wiring.py` ~1222). Say that one translation exists, so "the one refusal" is not read as absolute.
- "two spellings of one file hold one lock": canonicalisation is by parent directory only (`platform/claims.py:84`). A symlinked *artifact* and its target take different sidecars. Say "two spellings of its directory".
- "`run_harvest` records a busy `meetings/` as a retryable failure": it is retryable only if the connector's own failure for that attempt was (`app/pipelines.py _record_busy_meetings`). Add "unless the fetch already failed non-retryably".
- AD-9: "every result that reached the provider carries" `answered_at`. The code (`domain/harvest.py:347` docstring) sets it to `None` when the provider "answered only with an error". Say "every result in which a page was read".

## Checked and consistent
- AD-5 matches the code on these points:
  - `StorageService.exclusive` over `ClaimPort`/`FlockClaims`.
  - The `<name>.lock` sidecar, never unlinked.
  - `CLAIM_SIDECAR_SUFFIX` declared in `scope_model.py:797`, with the node-name refusal at `:1146`, hidden from listings (`service.py:1333`), and the project `.gitignore` rule in `core/project_scaffold.py:69`.
  - No command catches `ClaimHeld` (grep).
  - `ENOTSUP`/`EOPNOTSUPP`/`ENOLCK` named with the mount, and directory/FIFO sidecars refused.
  - An older build's `.claim` marker reported on stderr and not obeyed.
  - Only the credential store waits (`CLAIM_WAIT_ATTEMPTS` × `CLAIM_WAIT_INTERVAL`, wiring.py:1194-1198).
  - The registry claims through `storage.exclusive` (wiring.py:1616).
  - The 1q measurement is recorded beside `MAX_ENTRY_LENGTH`.
- AD-9 matches the code: the type refuses `EMPTY` without `answered_at`, refuses non-UTC instants, and refuses a window not starting at the instant. The GitLab gap is recorded in `deferred-work.md` under 8n.
- AD-27 enforcement and the AD-48 list check match what 2m built (`payload_fields.py`, `get_type_hints`).
- No stale "owed by" or "not yet built" text tied to 1q/1r/2m/8p remains. No mention of the old O_EXCL claim file remains. `mentions` is stated only at lines 368 and 1025.
