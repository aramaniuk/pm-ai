# Rubric review: the wave-3 amendments (2026-10-16)

**Scope.** Every passage of `ARCHITECTURE-SPINE.md` tagged `[2026-10-16]`: AD-1 (class F from the daemon only, class LM), AD-3 (GitLab project work, `aliases.md`), AD-5 (a mode change is not a write; `strategic_goals.md` refresh), AD-6 (what "private" covers), AD-7, AD-9 (the calendar read), AD-12 (third limit retired; Tier-2 text), AD-15 (`meeting_summary`, the router's table), AD-16, AD-17, AD-19 (the model pool, the server's limits), AD-21 (the new commands, `DAEMON_BOUNDS` / `TERMINAL_BOUND`), AD-23, AD-27's pointer, AD-31, AD-33 (decision references), AD-34 (`decision`, `gitlab`, the two tables), AD-38, AD-49, the Stack rows (`httpx2`, `requests`, `prompt_toolkit`, Ollama, Ollama client), the Integration prerequisite, the API notes, the Deferred entries (local-model selection, task-class side) and the Open Risks (fallbacks, whisper + Ollama, the handle → person table). Read against the whole spine, the memlog entries of 2026-10-16 (including its five flagged questions), the 21 wave-3 specs (1s, 4s–4w, 7a–7e, 8r–8t, 11d, 23e–23g, 33g–33i) and the code where a statement is about it (`pm_ai/storage/service.py`, `pm_ai/app/daemon.py`, `pm_ai/surfaces/api/app.py`, `uv.lock`). `lint_spine.py` reports no findings.

**Verdict.** The amendments cover what the 21 specs asked for, and most Rules are enforceable. Four problems need text before `7a`, `7e` and `8t` are built. First, the saved alias table works across all projects, but a `gitlab:` handle is unique on one GitLab server only, so a confirmed name can resolve to the wrong person in another project. Second, nothing says where `7a`'s "is the model there" check lives, and its code map puts a second Ollama client in `pm_ai.platform`, which AD-1 and AD-8 forbid. Third, AD-19 still contains the sentence its own amendment says it replaces. Fourth, `aliases.md` is written by one process and read by the daemon, and it has no declared refresh point. Two specs depend on a spine text that the spine does not match: `7a` (AD-49) and `7e` (the Open Risk).

## Checklist

| Item | Result |
|---|---|
| Fixes the real divergence points for the 21 specs, missing none | Mostly. It misses: where the local-model presence check lives (F2); when the daemon re-reads `aliases.md` (F4); the line between per-member activity and a monitored metric on a project page (F7); one Graph row filing into several scopes (F10) |
| Every amended Rule is enforceable and prevents its stated divergence | No, in two places. AD-34's `gitlab` limit rests on a premise the alias table makes false (F1). AD-19 still requires the server setting it says it cannot make (F3) |
| Nothing under Deferred or Open Risks lets two units diverge | No. The Rosetta Open Risk and the amended Deferred entry give `7a` two different instructions (F8) |
| Amended ADs do not contradict unamended ones | No. AD-21's "the one exception" (F11); AD-34's restated scopeless test against meeting ids (F12); AD-23's "captures are encrypted" against AD-6 (F13) |
| Ratifies the codebase (nothing present-tense is false) | No. Six statements beside the amended passages describe `4r`, `9a`, `9b` and `4q` as not yet built; all four are built (F9) |
| Every wave-3 spec's "depends on spine amendment" is met | 19 of 21. Not met as written: `7a` (AD-49 puts `ScopeFlowRefused` in `domain`, F5) and `7e` (it says it closes the Open Risk; the spine closes the name half only, F16) |
| Named tech is current and matches the lock | Yes. `httpx` 0.28.1, `httpx2` 2.12.0, `requests` 2.34.2 and `anthropic` 1.2.0 match `uv.lock`; `prompt_toolkit` is pinned at build by rule |
| Plain language for a non-engineer owner | Mostly yes. "Class LM" and "flow" are defined where first used. "Scopeless" is restated but now inaccurate (F12) |

---

## F1: High. The saved alias table works across projects, but a `gitlab:` handle is unique on one server only

**Amended text (AD-34).** "*Stated limit:* a numeric id is unique within one GitLab host only. Every reader so far (`8t`, `23g`, `7e`) reads it inside one project scope, whose connector names one host." And the two-tables paragraph: the saved alias table is "application scope … one row per name the PM confirmed … there is no second map, per project or per slice."

**Why it breaks.** The limit's premise is false for the table the same amendment creates. `7e` writes rows like `"Alex" → gitlab:123` into the one application-wide `aliases.md` (`7e`:29, 31). Its resolver uses "an alias row for that label scoped to this meeting, then an unscoped one, whose handle is a member in `8t`'s record" (`7e`:30). Suppose project B's GitLab server also has a user 123, who is a different person. Then "Alex" said in a project B meeting resolves to that person, and project B's Team Activity shows the wrong member against an issue. Nothing refuses it, because user 123 *is* a member of B. AD-10 allows a different GitLab server per project, so this is reachable today. A second problem has the same cause. A name is one key for every project, so two different people called "Alex" in two projects cannot both be confirmed. `member name` "replaces" the other project's row after a confirmation (`7e`:31). Andrei's decision was "no per-project map". That rules out a second table, but not a scope recorded on the row.

**Suggested text (discuss: it touches Andrei's "7e name map" decision).** Replace AD-34's stated limit with:

> *Stated limit, and the rule that follows from it:* a numeric id is unique within one GitLab server only, and AD-10 allows a different server per project. So an alias row whose handle is `gitlab:` also records the project scope it was confirmed in, and a reader uses that row only in that scope. A project scope names one GitLab connector, so it names one server. It is still one table. A `gitlab:` row confirmed for one project never resolves a name in another, and confirming the same name in a second project adds a row rather than replacing the first. A row whose handle is unique across servers (`graph:`, `email:`) needs no scope. Joining `gitlab` handles across projects still needs the server in the handle first.

`7e`'s spec then needs `--scope` stored on the row and its resolver's step (1) limited to rows of the page's scope. The alternative is to put the server in the handle (`gitlab:<host>/<id>`) before `8t` writes its first row. That costs a grammar decision now and removes the limit entirely.

---

## F2: High. Nothing says where the "is the model there" check lives, and `7a` puts a second Ollama client in `pm_ai.platform`

**Amended text (AD-1, class LM).** "`pm_ai.models.local`, via the router … Loopback only, by construction: one HTTP client with the constant base URL `http://127.0.0.1:11434` …"

**Why it breaks.** `7a` has three places talk to Ollama, and the spine names one. `setup` and `doctor` "say whether the model is there", refuse a remote (`cloud`) model and report a malformed or slow answer (`7a`:24, 27, 67). `7a`'s code map sends that probe to `pm_ai/platform/doctor.py` and tells the builder to copy `_exchange`, "the bounded loopback request" (`7a`:82, 97). Three rules forbid that. The Design Paradigm table says `pm_ai.platform` is "forbidden an HTTP client (AD-1)". AD-8 says the `doctor` exchange "connects only to the loopback address and the configured port", meaning the daemon's. The LM row allows one home and one client. One implementer will build the probe in `platform`, a second will route it through the adapter, and a third will give `setup` its own request. Each of these requests is to loopback, so this is not a leak today. But this is the egress AD, and its "by construction" claim is what makes the class safe.

**Suggested text.** Add to the LM row's Constraints, after "remote models refused":

> One other kind of request reaches the server: the presence check `setup` and `doctor` make (which models are pulled, and whether a named one runs locally). It carries no prompt text. It is a method of the same adapter in `pm_ai.models.local`, using the same client, and is called through the composition root (`app/entry`'s diagnosis and `setup`). There is never a second client in `pm_ai.platform` or `pm_ai.surfaces`. AD-8's exchange in `doctor` reaches the daemon's port only.

`7a`'s code-map line 82 and task line 97 then need to point at the adapter rather than `platform/doctor.py`.

---

## F3: High. AD-19 still contains the sentence its own amendment says it replaces

**Text (AD-19 Rule, first paragraph, unchanged).** "Default bound: **one heavy local-model job at a time**, configurable. The bound is only half the guard: because Ollama holds models resident in its own process, the server must be constrained too, and a client-side semaphore alone satisfies the letter of this AD while the machine thrashes."

**Amended text (same AD).** "What pm-ai guarantees, and what it only names `[2026-10-16, 7a; replaces "the daemon must also constrain the server"]` … the daemon's **model pool is one thread**". The new Prevents adds "a rule requiring a server setting the daemon cannot make".

**Why it breaks.** The replaced wording is still in the Rule, two sentences above the paragraph that replaces it. "The server must be constrained" is the rule the new Prevents says must not exist. A builder can read it as permission to set `OLLAMA_*` or start `ollama serve` with them, which `7a`'s Never forbids. "Configurable" contradicts "the model pool is one thread". A setting that raises it to two lets two generations overlap, which is the thrashing case the AD exists to prevent.

**Suggested text (autofix).** Replace the two sentences with:

> The bound is **one heavy local-model job at a time**: the daemon's model pool of one thread (below). It is not a setting. It is only half the guard, because Ollama keeps models loaded in its own process. The other half is the server's own two limits, which pm-ai names and cannot set (below).

---

## F4: High. `aliases.md` has no refresh point, although another process writes it and the daemon reads it

**Amended text (AD-5, AD-7).** "What the daemon reads from an artifact another command may write is refreshed at one declared point". The list has three entries: `projects.toml` (start only), `config.toml` / connector rows / credentials (every job and command), and `strategic_goals.md` (every line and command that reads it).

**Why it breaks.** `aliases.md` is new in this wave and has exactly that shape. `member name` is in `TERMINAL_BOUND`, so it "runs in the prompt's process" (`4t`:32 states the routing rule) and appends to `aliases.md` from a client process. The daemon reads the file for every project render (`7d`:27, `7e`:33) and for `member link` and `member unmatched` (`DAEMON_BOUNDS`). `7e`'s acceptance criterion is that "after `member name`, the next render attributes it" (`7e`:101). With no declared point, one builder caches the table when the daemon starts, as projects are cached, and that criterion fails until a restart. `4t` asked for this exact entry for `strategic_goals.md` and got it. `7e` has the same need but did not ask.

**Suggested text (autofix).** Add to AD-5's list:

> - `aliases.md` `[2026-10-16, 7e]`: read again for every render and every command that resolves a name (`member link`, `member unmatched`). `member name` appends to it from the client's own process.

Add after the list: "An artifact a client process writes and the daemon reads that is not on this list has no refresh point. Building one is a spine amendment, never a default to caching." Add "`aliases.md` for every render and every command that resolves a name" to AD-7's summary sentence.

---

## F5: Medium. AD-49's test is applied to one model refusal and not to the other, and `7a`'s dependency is not met as written

**Amended text (AD-49).** `LocalModelUnavailable`, `NoLocalModel`, `PossiblyTruncated`, `ModelTimedOut` and `ModelAnswerRefused` go in `pm_ai.ports`. `ScopeFlowRefused` goes in `pm_ai.domain`, because "it is about the closed flow table … a rule `domain` owns".

**Why it breaks.**
- **`ModelAnswerRefused`.** By the same test it is not about the port either. `7b` raises it from "one answer shape, validated in `core`" (`7b`:25): an unknown speaker label, a statement too long after cleaning, JSON that does not parse twice (`7b`:59–61). These are rules of `7b`'s answer shape. No adapter raises it, and `7c` reuses the same validator (`7c`:58). The AD's own sentence ("decided by what the refusal is *about*, not by who raises it") puts it beside the shape, in `domain`.
- **`7a`'s dependency.** `7a` says "**AD-49** — the five model refusals live in `pm_ai/ports`" (`7a`:115) and declares `ScopeFlowRefused` there (`7a`:25). The spine says otherwise. The memlog flags this. Until `7a`'s text changes, a builder following the spec breaks the AD.

**Suggested text.** In AD-49, move `ModelAnswerRefused` into the domain sentence: "`ScopeFlowRefused` (the flow table, AD-31) and `ModelAnswerRefused` (`7b`'s answer shape) are about rules `domain` owns, so each is declared in `pm_ai.domain` beside its rule." Record in the memlog that `7a`:25 and `7b`'s Code Map owe the matching edit (through `bmad-spec`). Alternatively, if Andrei prefers all model refusals in one place, add an explicit exception to AD-49 rather than leaving the test half-applied.

---

## F6: Medium. "The one place a name becomes a handle" against "display name and username … for name matching"

**Amended text (AD-34).** The saved alias table "is the one place a name becomes a handle". The `gitlab` paragraph says "Display name and username sit beside the handle for display and for name matching."

**Why it breaks.** `7e` turns a name into a handle without a row. Its resolver's step (2) is "exactly one active member whose display name or username equals the label" (`7e`:30). So the alias table is not the one place. Meanwhile `23f` stores Graph senders' display names and says "the display name is never compared, never matched to a person" (`23f`, Always). Two readers of the same kind of field take opposite rules, and the spine text supports both. The next slice that resolves Graph speakers ("later name resolution — Graph speakers, chat senders — reads the same table") has to choose between them.

**Suggested text.** Replace "It is the one place a name becomes a handle" with:

> It is the one *saved* place a name becomes a handle. Besides it, a reader may match a name to exactly one member's display name or username inside one project's GitLab member list (`7e`). That match is never saved and is made again on every read. No other matching is allowed: a Graph display name (`23f`) is never compared with anything.

---

## F7: Medium. The spine has no rule for per-member activity on a project page

**Context.** `23g` puts each member's assigned issues and recent activity on the project dashboard. `7e` adds meeting updates under them. Both rest on Andrei's kernel amendment of 2026-10-16 ("per-member activity against that member's own tasks … never rankings or comparisons"), which `23g` says is not yet re-derived into `SPEC.md:245`. The memlog asks the gate to check "the line holds".

**Why it is a divergence point.** The line holds inside `23g` and `7e`. Each states its own version: "Nothing totals the marks per member", "No member-level total, rate, trend, score" (`23g`), and "never counted per person" (`7e`:35). The spine states it nowhere. AD-25 governs the PM's own analytics, and AD-4/AD-25 put "per-employee monitored metrics (FR-30)" in `people`. The next slice that adds something to a project page (a count of a member's open issues, days since their last commit) has no AD to fail. Two slices have each written the rule in their own words, and that is the condition for a spine rule.

**Suggested text (after the kernel is re-derived).** Add to AD-25, after the `people` paragraph:

> **Per-member activity on a project page** `[2026-10-16, 23g, 7e]`. A project page may list each member's own assigned work and what moved on it, ordered by something that does not depend on activity (name, milestone). It never prints a per-member total, rate, trend, score, ranking or comparison with another member or another day. Those are FR-30 monitored metrics, and they live in `people` (AD-4).

---

## F8: Medium. The Rosetta Open Risk and the amended Deferred entry give `7a` two instructions

**Text.** Open Risks (unchanged): "the slice that first needs a local model (story 7) must switch and re-measure first, because emulation there is the difference between usable and not." Deferred (amended): `7a` benchmarks "recording the CPU architecture pm-ai ran as". `7a`:33 records it and does not switch.

**Why it matters.** Ollama is a native program, so its timings hold either way. But `7a`'s benchmark also records pm-ai's peak memory "beside a daemon persisting the largest harvest batch". The selection tie-break is "the lower peak memory", and the timings `7b` reuses for chunk sizing are measured under emulation. The memlog flags this and leaves it undecided, and the two texts disagree.

**Suggested text (discuss).** Either keep the Open Risk and add "`7a` switches the interpreter to arm64 before its benchmark" to the Deferred entry and `7a`, or narrow the Open Risk to: "`7a` records the architecture; Ollama's timings are native either way; pm-ai's own figures (memory beside the daemon, `7b`'s chunk timings) are re-measured when the interpreter is switched, which is owed before `<slice>`."

---

## F9: Medium. Six statements beside the amended passages say built work is not yet built

The amendment edited AD-5, AD-7 and AD-19 in place and left these unchanged. `git log` shows `4r` (15c16e8), `4q` (3539466), `9a` (522892a) and `9b` (11cb053) built. `daemon.py:204-208` takes the lifetime lock. `concurrency.WriterThread` exists. `api/app.py:166-174` hands status to the I/O pool. `service.py:224-233` records `requests`, `harvest_runs` and `renders` as added.

- AD-5 known limits: "in `4e` the daemon keeps it by taking no claim at all, and from `4r` it will keep it by entering every claim on its one writer thread".
- AD-5 "**Not yet built** `[2026-10-14]`. Today the daemon has no writer thread …" (the whole paragraph).
- AD-7: "So **before `9a` attaches any timer, the daemon must hold** … Until then, two daemons are two writers". The new `[2026-10-16]` sentence sits between these two.
- AD-19: "`4e`'s status handler and its request log line … are the current exceptions, and both move off the loop in `4r`."
- Enforcement, still-open table: "`GET /v1/status` runs its probes on the loop thread".
- Open Risks, last bullet: "`4r`, `9a`, `9b` and `4q` … their specs still describe the design it replaced".

`1s` depends on the lifetime lock and the writer thread existing ("after it holds its lifetime lock and before its writer thread starts"). A reader of the spine alone would think `1s` is blocked on `9a`.

**Suggested action (autofix).** Rewrite each in the past tense with the slice that built it, as the spine already does for other closed items ("Built 2026-10-08 by story `4m`"). Strike the Enforcement row and the Open Risks bullet as closed, keeping their text, as the document's convention is.

---

## F10: Medium. AD-10 says one scope per connector instance, but a Graph row files into several

**Text (AD-10, unamended).** "A connector instance is the tuple `(scope, connector_type, config, cursor)`." Personal instances "are separate instances under the personal scope".

**Why it matters now.** The Graph row is one instance, and its filed scope is `personal` (`33i`: "`instance_scopes` stays personal"). Its entries file into several scopes, each with its own cursor: chats (`33f`), channels with a `project` key (`33d`), calendar categories mapped to projects (`4s` `connector categories`), and now channels filed personally (`33i`). `33i` says it "depends on no spine amendment", which holds only because nothing in the spine describes the shape it extends. The next connector with a per-entry scope (Slack channels, Outlook folders) has two models to copy: AD-10's tuple, or the Graph row.

**Suggested text.** Add to AD-10:

> **One row, several scopes, when its entries say so** `[2026-10-16, 33i]`. A connector row may list entries (channels, chats, calendar categories), each naming the one scope it files into. Each entry keeps its own cursor and is filed by AD-4's rule into that scope alone. The row itself belongs to the scope that holds its credential (the Graph row: `personal`). An entry's scope comes only from its own declaration, never from the row's.

---

## F11: Medium. AD-21's "the one exception", and the growing terminal-only set against AD-7

- **"The one exception" is false.** AD-21 says "`transcript summarize` is daemon-only, and the one exception to running inline". `frontier key` also needs the daemon from a terminal: it hands the key over the loopback API, and with no daemon it is refused naming `pm-ai daemon run` (`7c`:33, 54). Suggested text: "**Two commands need the daemon from a terminal too**: `transcript summarize` … and `frontier key` …, each refused naming `pm-ai daemon run` when no daemon answers."
- **AD-7's same-functionality rule.** AD-7 says "no feature may exist on only one surface". Before this wave, `TERMINAL_BOUND` held set-up commands (`setup`, `key enrol`, `connector sign-in`). Wave 3 adds everyday PM actions to it: `member name` (confirming who said what), `goal set` / `update` / `delete`, `connector categories`, `frontier key`, and `logs`. None of these can be reached from Telegram, and the spine does not say whether that is allowed. Suggested text, as a Deferred entry: "**How a `TERMINAL_BOUND` command reaches Telegram.** AD-7's same-functionality rule and AD-21's terminal-only set disagree for commands that ask questions or read a secret. Decide with the Telegram bridge (story 5): either Telegram names the terminal command, or a question becomes a Proposal (AD-13), as re-consent does (AD-39)."

---

## F12: Low. The restated "scopeless" test is not true of meeting ids

**Amended text (AD-34).** "**What makes a system scopeless**: its id is unique without a scope component, and the scope that owns the entity travels in the event's envelope and in the natural key."

**Why.** `7b` says, correctly, that "a meeting may have a record in more than one scope". That is why its proposal ids include the scope (`7b`:35). So a meeting id is not unique without its scope. What the design relies on is that one id names one real-world thing, and the natural key's scope keeps the records apart.

**Suggested text.** "its id names one real-world entity without a scope component — the same id in two scopes is the same meeting or decision — and the scope that owns each record travels in the envelope and in the natural key below, never in the reference."

---

## F13: Low. AD-23's amended text sits beside two sentences it now contradicts

- "a watched folder accepting `.vtt` / `.txt` / `.md` / `.docx`" against "the four formats are accepted at the manual drop only". `11b` says the watched folder is story `10a` and `11d` is the drop command. A `10a` builder cannot tell whether the folder counts as "the manual drop". Suggested text: "a drop command and, later, a watched folder (`10a`), both accepting …; the four formats are accepted by those two manual paths only, never by the readers shared with Graph's transcript path."
- "Captures are encrypted and never committed" contradicts AD-6 (transcripts left the encrypted set on 2026-08-27) and the storage diagram ("PLAINTEXT"). `11d` stores `.docx` text as a capture. Suggested text: "Captures are plaintext (AD-6) and never committed."

---

## F14: Low. The disclosure ledger's first grammar change has no rule for older lines

AD-38's amendment adds a call id and a status to each line. The Deferred entry "Format and layout evolution" says the disclosure ledger's own line format is still open, to be revisited "when the first Tier-1 grammar change lands". This is that change. No frontier call has been made, so no older line exists from a call. But `2i` built the append, and a hand-added or test-written line has no status. Suggested text, in AD-38: "A line without a call id or status (written before `7c`) is counted as a completed call of the cost it states. The grammar is additive from here, as AD-27's is." Also in AD-31: "The key check (listing models) carries no prompt and writes no line." Update the Deferred entry to say the disclosure ledger's grammar was settled with `7c`.

---

## F15: Low. Smaller items

- **New Tier-2 tables.** `8t`, `33g` and `7b` all add tables "created on open; the store's version number does not change". They agree, because the convention exists, but only in a code comment (`service.py:224-233`). AD-5's migration paragraph should say it: "A new table arrives by `CREATE TABLE IF NOT EXISTS` on open and does not move the version; a change to an existing table is a migration and does."
- **AD-16's fallback.** If the API refuses an empty tool set, the replacement (`messages.create` without tools) contradicts AD-16's Rule ("Frontier calls use the Anthropic SDK Tool Runner"). So the choice is an AD-16 amendment, not a decision inside `7c`'s Ask First. Say so: "if it does not, the answer is an amendment to this AD, raised from `7c`."
- **The frontier key's home** is named only in `7c` (`config.json`, its own top-level key). AD-39's custody bullet covers connector credentials only. One clause: "the frontier key is held in `config.json` under its own top-level key, outside every connector entry (`7c`)."
- **Front matter** still says `updated: 2026-10-14`. The Stack's "Claude models" row lists Sonnet 5 for "briefings, drafts, inquiry" but not `meeting_summary`.
- **AD-47's "at the declared mode"** for whole-file replace now means AD-6's location rule, not a per-node declaration. "at its mode (AD-6)" says it without implying a field nobody declares.

---

## F16: Low. `7e` says it closes the Open Risk; the spine closes half of it

The spine applies the memlog's first flagged question as "name half closed, handle half open". That is consistent with `7e`'s own cross-spec text ("the existing table, unchanged and still unsaved"). `7e`:29 and its Change Log still say the slice "closes the spine's open risk". Either Andrei confirms the spine's reading and `7e`'s two sentences are narrowed to "closes the name half", or a slice is named for the handle → person table. The spine text is right as written.

---

## What holds up

- **Egress classes.** Class F "from the daemon only, through one client" and class LM "loopback by construction" are specific enough to test: explicit key and URL, `trust_env` off, no redirects, `max_retries=0`. The decision to drop the `ollama` package because it reads `OLLAMA_HOST` and follows redirects matches the Stack row. The Stack pins match `uv.lock`.
- **The flow check on every model call (AD-31.3)** puts the destination rule in front of the local model as well as the frontier. This closes a gap the old wording left: a local call could read personal material for a project page.
- **The disclosure ledger's `sent` / `completed` lines** fit AD-5's steps without new machinery. The `sent` line can be its own claimed step before the bounded request (the spine leaves this placement implicit; `7b`'s "every write is a claimed step" covers it). A call abandoned by a deadline or shutdown keeps that line and is counted as a call of unknown cost, never as a free one.
- **The router's table (AD-15)** closes the Deferred "which side a new task class runs on" with a test, not a convention.
- **GitLab work in Tier 2 (AD-3)** gives the right reason (the natural key would keep an issue's first state). It is written in one claimed step and read through one port method, so `23g` and `7e` read the same thing.
- **The calendar read (AD-9)** names the argument (two aware UTC instants, the display day, 23 or 25 hours across a clock change) and refuses a fetch given both a cursor and a range. That removes the choice two calendar connectors would otherwise make differently.
- **`decision` references (AD-33, AD-34)** are content-addressed, scoped through the natural key and attributed to pm-ai, so a re-summary never renumbers or drops a decision and never counts as evidence.
- **AD-6's privacy rule by location**, not by the `gitignored` flag, matches the field-test measurements. It also keeps `0600` off files git checks out for teammates. The "a mode change is not a write" clause is correctly placed: before the writer thread, under the lifetime lock, with no claim taken.
- **AD-21's two tables** turn "inline, acknowledged, or terminal only" into a test failure when a command is in neither. That is the right enforcement for the five slices that each add commands.
