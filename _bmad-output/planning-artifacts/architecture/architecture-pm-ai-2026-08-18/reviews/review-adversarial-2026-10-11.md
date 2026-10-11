# Adversarial review — spine update of 2026-10-11

**Lens:** build two units one level down that each obey every AD to the letter and still fail to fit together. Each pair below is a hole, with the tightening that closes it.

**Inputs:** the spine diff of 2026-10-11 (AD-5 single claim, AD-9 `answered_at` built, AD-27 / AD-48 `mentions` rule); the last memlog entries; slice specs `33d`, `33f`, `23c`, `8q`; code read but not changed: `pm_ai/domain/identity.py`, `pm_ai/domain/payload_fields.py`, `pm_ai/domain/events.py`, `pm_ai/connectors/graph/__init__.py`, `pm_ai/storage/service.py`, `pm_ai/platform/claims.py`, and the tests that use the reserved spelling.

**Verdict:** the `mentions` rule is right in intent, but it is not yet buildable as one thing. Five things are left open: who produces the reserved spelling, which handle a Graph alias is keyed on, when resolution happens, how the PM's own identity is found, and what an actor id looks like. Two frozen slice specs (`33d`, `23c`) contradict the new text outright. The AD-5 single-claim text has two smaller holes: a nested claim refuses itself, and "two spellings, one lock" is wider than what the code does.

---

## Part A — `MessagePayload.mentions` and the people it names

### A1. Nothing produces `unresolved:<system>:<handle>`, so the sender and the mention are spelled two ways (severity: high)

**What the spine says:** each mention is the actor id, or `unresolved:graph:<Graph user id>`, "the rule a sender and `assignee` follow"; "never the bare sentinel".

**What the code does:** `resolve_actor` (`identity.py`) returns the bare `UNRESOLVED` actor (`actor_unresolved`) for every handle it cannot resolve. The handle is gone at that point. The envelope writer (`storage/service.py:1702`) writes `actor=stamped.actor.actor_id`, so an unresolved sender reaches the line as `actor_unresolved`. The payload encoder refuses the sentinel in payload fields, but nothing refuses it in the envelope's `actor`. No module in `pm_ai/` builds the string `unresolved:…`; only tests do.

**The pair:**
- *Builder A (33d)* calls `resolve_actor(system="graph", handle=from.user.id)` for the sender, as 33d's text says ("resolved through the alias table or left unresolved"). For each mention it writes `f"unresolved:graph:{id}"` itself when the lookup misses. Every check passes. The line reads `actor=actor_unresolved p.mentions=unresolved:graph:9f…`.
- *Builder B (33d)* writes a new helper that returns `Actor("unresolved:graph:<id>")` and uses it for both the sender and the mentions. The line reads `actor=unresolved:graph:4c… p.mentions=unresolved:graph:9f…`.

Both obey the spine. Under A, "who messaged the PM, and whom did they mention" cannot be joined, and every sender pm-ai cannot name becomes one person. That is the failure AD-34 rule 2 exists to prevent. A also sends `33d`'s "Sender is a bot" row to the bare sentinel. Graph's calendar code (`graph/__init__.py:899`, `:1134`) is a working example of A today.

**Tightening (AD-34 rule 2):** there is exactly one producer of the reserved spelling. It is a function in `domain/identity.py` that takes `(system, handle)` and returns either the resolved actor or `Actor("unresolved:<system>:<handle>")`. `resolve_actor` either becomes that function or is renamed to make clear it is only a lookup. The envelope's `actor` follows the payload rule: the storage service refuses `actor_unresolved` on an observed event, as the encoder already does for payload fields. The one exception is a handle that is absent, which needs its own sentinel and its own spelling. A non-user sender (a bot) gets a declared system name, for example `unresolved:graph_app:<application id>`. That way "never the bare sentinel" has a spelling for every row in 33d's matrix.

### A2. The Graph alias key is an email in the code and the Graph user id in the spine (severity: high)

**The spine says** "the Graph handle is always the Graph user id" and "never an email".

**The code** resolves Graph actors by email: `resolve_actor(system="graph", handle=row.organizer.email)` and `handle=attendee.email`. The tests that pin the reserved spelling use `unresolved:graph:dana@example.com` for a mention (`tests/core/test_payload_serialisation.py:190,198,201`; `tests/domain/test_payload_fields.py:51`). Those tests now contradict the rule they were written to pin.

**The pair:**
- *Builder A (alias seeding)* registers `("graph", "dana@contoso.com") → actor_dana`, because that is how the calendar resolves Graph people today.
- *Builder B (33d)* looks up `("graph", "<Dana's Graph user id>")`.

Both are keyed under `system="graph"`. The lookup misses. Dana appears in the calendar as `actor_dana` and in messages as `unresolved:graph:<id>`. Neither builder broke a rule, because the spine does not say which handle an alias entry under `graph` is keyed on.

**Tightening:** one system name means one kind of handle. Either every Graph alias is keyed on the Graph user id, and the calendar code must read attendees' and organizers' user ids (Graph's calendar attendees carry only an email address, so this costs a lookup or a different endpoint), or the email-keyed handles get their own system name (`graph_email`), and each event says which one it resolved under. Fix the four test fixtures in the same change. Also correct the reason text in `events.py:117` and `events.py:271`, which still say "Graph user ids", to match AD-48's new reason.

### A3. Resolution at write time against immutable Tier 1: when it happens, and who can correct it (severity: high)

**The spine says** that a line written before its alias entry keeps the unresolved spelling, and "a rebuild re-resolves it". AD-5 says no Tier 1 file is ever edited in place. AD-34's natural key drops a re-harvested message as a duplicate, so harvesting again does not refresh it either.

**The pair:**
- *Builder A (a reader that counts per person, such as a future "who asks the PM most" view)* reads `mentions` and the envelope `actor` as written and groups by string. After an alias for Dana is added on 1 November, October's lines say `unresolved:graph:<id>` and November's say `actor_dana`. Dana is two people.
- *Builder B (the same reader)* runs every person id through the current alias table first, mapping `unresolved:graph:<id>` to `actor_dana`. Dana is one person.

Both obey the spine, and their counts differ. "A rebuild re-resolves it" fits only B, but it names no rebuild: Tier 1 cannot be rebuilt, and no Tier-3 projection of people is declared. Worse, an alias entry that was wrong when a line was written (the wrong user id mapped to `actor_dana`) is now baked into an immutable line with no handle left to correct it. The reserved spelling was introduced to keep exactly that handle.

**Tightening (AD-34 rule 2, plus AD-27):** state that the alias table is the authority at read time, and that a person id on a line is a hint written at a moment in time. Every reader that joins or groups people goes through one domain function that maps a line's person id to an actor using the current table, and no reader compares raw person strings. Then decide one of these, a choice for Andrei:
- (a) Lines always carry the native handle (`graph:<id>`), and resolution happens only at read time. Simplest, and an alias correction fixes history.
- (b) Lines carry the resolved id when one exists, which accepts that a wrongly mapped line cannot be corrected.

Under (b), say so in the spine. Delete "a rebuild re-resolves it", or name the Tier-3 projection that does the re-resolving.

### A4. The PM's own identity: 23c's frozen text forbids what the spine requires (severity: high)

**The spine says** "`23c` decides 'mentions you' by the PM's actor id or `unresolved:graph:<the PM's user_id from 8q>`, not the raw id".

**23c (frozen) says** it marks "the messages whose mentions hold the PM's own Graph user id", and its Never list has "No name resolution, no alias table read, no reverse index". It names slice `8m` as the one that saves `user_id`, where the spine and `8q` say `8q`.

**The pair:**
- *Builder A (23c, following its own frozen text)* compares each member of `mentions` with the raw `user_id`. After the spine's rule lands, a member is `unresolved:graph:<uid>` or an actor id, never the raw uid. Nothing is ever marked, and no test fails, because "mentions someone else" renders the same way.
- *Builder B (23c, following the spine)* has to find the PM's actor id. That needs an alias table read, which 23c forbids, and the alias table is not persisted anywhere (`identity.py:341-344`; 23c's own Code Map says so). So B can only compare `unresolved:graph:<uid>`. Once an alias for the PM exists, B silently stops marking every newer line.

There is also no rule for what "the PM's actor id" is. One builder takes `resolve(graph, user_id)` for each enabled Graph row. Another adds a settings key `me`. With two tenants, the two `user_id`s can resolve to two different actor ids, or to one id and one miss.

**Tightening:** add one sentence to AD-34. The PM's identity set is, for each enabled Graph row with a usable `user_id`, the reserved spelling of that `user_id` together with whatever it resolves to now. "Mentions you" means some member of the list, mapped through A3's read-time function, is in that set. Then renegotiate 23c's frozen text: compare against the set rather than the raw id, allow exactly one alias-table read through the domain function, and fix the `8m` → `8q` reference. Until the alias table is persisted, the set has only the reserved spellings, and 23c's tests should pin both members.

### A5. 33d's frozen text writes raw ids, and nothing in the grammar can tell (severity: medium)

**33d says** "`mentions` holds the Graph user ids of the people mentioned, as Microsoft sent them". Its matrix has "`mentions` holds the id". Each element of the list is checked only for being non-empty, being text, and not being the sentinel (`payload_fields.py:_encode_element`). A raw GUID passes. So does `dana@contoso.com`. So does a display name, since the field is declared trusted and AD-48 skips sanitizing it.

**The pair:** *Builder A* follows 33d and writes bare GUIDs. *Builder B* follows the spine and writes `unresolved:graph:<guid>`. Both pass every check that exists. A's lines then fail B-style readers silently, and they are Tier 1, so they stay.

**Tightening:** give the person grammar a shape check, at encode and decode, for every person field and every element of a person list. A value is either an actor id (see A6) or `unresolved:<system>:<handle>` with `<system>` from a declared set. Anything else is refused as unencodable or corrupted. Also renegotiate 33d's "as Microsoft sent them" sentence and its matrix row before it is built.

### A6. "pm-ai mints actor ids" has no minting rule, which reopens AD-48's trusted claim (severity: medium)

**AD-48's new reason** is that `mentions` holds "pm-ai actor ids, which pm-ai mints … nobody types either". No AD says what an actor id looks like, who mints it, or when. The existing ones are `actor_alex` and `actor_pm_ai`.

**The pair:** *Builder A (alias seeding)* mints `actor_<slug of the display name>`. *Builder B* mints `actor_<random surrogate>` in storage, as AD-34 does for `evt_`. Under A, a name a person typed reaches a list that is declared trusted and never sanitized, so AD-48's reason is false. A name slug can also collide (two Danas) or contain `:`, which makes it read as the reserved spelling.

**Tightening (AD-34):** actor ids are random surrogates that storage mints when it creates the alias entry, written `actor_<surrogate>`, never derived from a name, an email or a handle. `unresolved:` is a reserved prefix that no actor id may start with. The existing constants `actor_pm_ai` and `actor_unresolved` are the only fixed ones.

### A7. The alias table has no owner, no scope and no row shape (severity: medium, blocks A3 and A4)

The spine mentions the alias table once (AD-34 rule 2). The code says it is "persisted as Tier-1 data", but it is a module dict that nothing saves. No AD says:
- which scope the table lives in (application, or per project),
- whether `system` for Graph includes the tenant,
- whether an entry can be changed or retracted, and how that is recorded under append-only Tier 1,
- which writer owns it (AD-5).

**The pair:** *Builder A* stores aliases per project, next to that project's event log. *Builder B* stores them at application scope. Dana, resolved in project `alpha` and unresolved in personal scope, is two people on the personal dashboard, which reads the personal log.

**Tightening:** one AD sentence. The alias table lives at application scope as an append-only ledger of `(system, handle) → actor_id` entries. A correction is a new entry, and the latest entry wins at read. It is written through the storage service. The Graph `system` value is `graph` (Graph user ids are unique across tenants, so the tenant is not needed in the key). Give this a slice ID before any reader depends on it.

### A8. Letter case of handles (severity: low, cheap to close)

`register_alias` and `resolve_actor` lowercase the handle for lookup. The reserved spelling keeps the handle "as it came". 8q stores `oid` as the token gives it. 23c compares strings exactly.

**The pair:** *Builder A (33d)* writes `unresolved:graph:<id as sent>`. *Builder B (33d)* lowercases it, to match the alias rule. If one Microsoft surface ever returns the GUID in a different case from another, 23c's exact comparison misses. A GUID in any case is the same id.

**Tightening:** the handle in the reserved spelling is the form `resolve_actor` uses as its key: lowercased, for every system, applied in the one producer from A1. 8q stores `user_id` lowercased too, or 23c lowercases before comparing. Pick one and state it.

### A9. 8q's fallback can record an id that is not the PM's Graph user id in that tenant (severity: medium)

8q falls back to "the first part of the two-part account id MSAL reports". MSAL's `home_account_id` is `<uid>.<utid>` for the account's **home** tenant. For a PM signed in as a guest in a client's tenant, that is their home-tenant object id. Teams in the client's tenant names them by their guest object id there. The fallback then records a value that 8q's own Design Notes warn against: it "would make 23c's comparison silently false forever".

**The pair:** *Builder A (8q)* accepts any two-part id, as the text allows. *Builder B (23c)* compares correctly against the recorded value and never marks anything.

**Tightening (8q, renegotiate):** accept the fallback only when `utid` equals the row's configured tenant id, and the row names a specific tenant rather than `common` or `organizations`. Otherwise refuse, as the no-identifier case does.

---

## Part B — AD-5: one claim primitive

### B1. A nested claim on the same artifact refuses itself (severity: medium)

`flock` belongs to an open file. `FlockClaims` opens the sidecar again for each claim and takes `LOCK_EX | LOCK_NB`. So a second claim on the same path from inside the same process fails as `ArtifactBusy`, against itself. The `StorageService.exclusive` docstring already expects nested claims on *other* files ("a refusal raised inside the body is the inner file's").

**The pair:** *Builder A (8q's `record_user_id`)* claims the connector row, re-reads it, sets `user_id` and writes it, as 8q requires. *Builder B (`connector add graph`, or 8m's `connector sign-in`)* already holds the claim on that row for its own read-modify-write and calls A inside it. Each one is correct on its own. Together they always fail with `ArtifactBusy` naming a claim that nobody else holds. The scheduler's backoff (a job "fails that attempt") turns this into a retry loop that never succeeds.

**Tightening:** claims are not reentrant. A second claim on a path this process already holds is a programming error, raised as its own exception and never reported as `ArtifactBusy`. The façade can detect it, because it can keep the set of paths it holds. A function that takes a claim says so in its signature or name (for example a `_claimed` variant that expects the caller to hold it). Optionally, add one more rule: when an operation holds two claims, it takes them in a declared order. Nothing waits today, so this is not a deadlock risk, but two refusals each blaming the other confuse whoever reads them.

### B2. "Two spellings of one file hold one lock" is wider than what the code does (severity: low)

The code builds the key as `guarded.parent.resolve() / guarded.name`. Only the directory part is canonicalised; the artifact's own name is not. That is deliberate: it keeps the sidecar inside the scope when the artifact is a symlink. But it means two names for one file in the same directory (a symlink `a → b`, or a hard link) get two sidecars, `a.lock` and `b.lock`, and two locks.

**The pair:** *Builder A* claims `connectors/graph.json`. *Builder B* claims through a compatibility symlink `connectors/msgraph.json → graph.json`. Both obey "two spellings hold one lock", and the race comes back.

**Tightening:** change the text to "two spellings *of its directory* hold one lock". Then pick one: refuse to claim an artifact that is itself a symlink (an `lstat` check in the façade), or say that a declared node may not be a symlink. Either is one sentence.

### B3. Claiming a collection versus a record inside it (severity: low)

`meetings/` is named as one claimed artifact, and the sidecar sits "beside the artifact". So the claim is `meetings.lock` in the parent directory. Nothing stops a later slice from claiming one record, `meetings/<id>.md`, which gets `meetings/<id>.md.lock`. These two locks do not exclude each other: a writer of the whole collection and a writer of one record run at the same time.

**Tightening:** a claim is always taken on a node declared in the scope model, never on a child of a declared collection. A collection writer and a record writer for the same collection both claim the collection. One sentence in AD-5, plus a check in the façade that the artifact argument resolves to a declared node.

### B4. The `.gitignore` line for existing projects (severity: low)

"An enrolled project's `.gitignore` carries `/.project-ai/**/*.lock`" is stated as a fact. It does not say who adds the line to a project enrolled before slice `1r`. *Builder A* adds it at enrolment only. *Builder B* checks it on every write to project scope. Under A, a project enrolled earlier commits its sidecars, and AD-43's "ask git what it tracks" check then reports tracked structure.

**Tightening:** say which operation adds the line to an existing project (enrolment, plus a `doctor` check that names a missing line), and that its absence is a named problem, not something fixed silently on write.

---

## Ranked: what to close first

1. **A1 + A5:** one producer of the reserved spelling, the envelope `actor` held to it, and a shape check on every person value. Without these, the rule cannot be checked, and each builder spells people its own way.
2. **A3 + A7:** decide whether the alias table or the line is the authority, and give the table an owner, a scope and a row shape. That decision settles whether a mistaken alias can ever be corrected.
3. **A4:** define the PM's identity set and renegotiate 23c's frozen text, which as written can never mark a mention.
4. **A2:** one kind of handle per system name, and fix the tests that pin an email in `mentions`.
5. **B1:** non-reentrant claims raise as a programming error, never as `ArtifactBusy`. 8q's `record_user_id` is the first place that hits it.

Lower: A6 (actor id minting), A8 (letter case), A9 (8q's guest fallback), B2–B4.
