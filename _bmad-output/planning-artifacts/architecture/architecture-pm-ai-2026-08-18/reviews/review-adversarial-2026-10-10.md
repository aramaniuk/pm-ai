# Adversarial review — the 2026-10-09 amendments

**Scope.** Every passage of `ARCHITECTURE-SPINE.md` tagged `2026-10-09`: AD-3/AD-44/AD-47 (dashboard is Tier 3), AD-5 (every invocation is the writer, serialized by `exclusive()`), AD-9 (`answered_at`), AD-11 (4m built, 4o open), AD-21 (foreground commands exempt), AD-27/AD-48 (grammar enforcement owed by 2m, person as id alone, lists exactly `tuple[str, ...] | None`), AD-33 (one record per meeting), AD-39 (rotation through the injected store, never prompts, `connector check` and the briefing surface unhealthy instances). Neighbours read: AD-6, AD-12, AD-13, AD-23, AD-34, AD-35, AD-40, AD-45, AD-46. Code read where a claim rests on it: `platform/claims.py`, `storage/service.py` (`exclusive`, `write_artifact`, `_payload_fields`), `app/pipelines.py` (`run_harvest`, `run_dashboard`, `_todays_calendar`), `app/wiring.py` (clock, credential-store wait), `core/meeting_records.py`, `domain/meetings.py`, `domain/harvest.py`, `domain/identity.py`, `connectors/graph/{auth,calendar,__init__}.py`, `connectors/gitlab.py`, `connectors/registry.py`, `surfaces/cli/dispatch.py`. Specs read: 8p, 8m, 4n, 4o, 2m, 23b, 23a, 11a, 33d, and the 9a/11b/33e entries in `stories.yaml`.

**Method.** For each amended sentence, construct two units one level down that each obey it to the letter and build incompatibly. Every pair found is a hole; each hole gets one tightened sentence.

**Verdict: REVISE.** Three holes are build-blocking for wave-2 slices that are about to start (F1 for every new write path, F2/F3 for `11b`/`33e`/`9a`, F4/F5 for `9a`/`23c`/`4n`). The rest are one-sentence tightenings that the slices named can carry. Nothing here reopens a decision Andrei took on 2026-10-09; every sentence below is a narrowing of one he took.

---

## F1 — AD-5: the amendment documents one of two `exclusive()` mechanisms, and they do not exclude each other

**Where.** AD-5, paragraph `[2026-10-09]`: "what holds it today is `exclusive()` in `platform/claims.py` — a `flock` on a sidecar the claim deliberately never unlinks … `ArtifactBusy` is therefore a real condition".

**What the code does.** There are two primitives with the same name and opposite designs:

- `pm_ai.platform.claims.exclusive(path)` — `fcntl.flock(LOCK_EX | LOCK_NB)` on `<name>.lock`, never unlinked, released by the kernel on death. Raises `ClaimHeld`. Used for `projects.toml`.
- `StorageService.exclusive(scope, artifact)` (`storage/service.py:1302`) — `O_CREAT | O_EXCL` on `.<name>.claim`, unlinked on exit, **orphaned by a kill** and then refused forever until an operator deletes it. Raises `ArtifactBusy`. Used for `config.json` (credentials), `meetings/` (`MeetingRecords.put`), and anything 8b-era.

`platform/claims.py`'s own docstring rejects the second design by name ("a lock file records that *somebody* claimed it, and nothing releases it when that somebody is killed"). The spine now says the first is "what holds it today"; for two of the three guarded artifacts it is the second. On top of that there are three wait policies: platform refuses at once; storage refuses at once; the sealed credential store in `wiring.py:1271` sleeps `interval` for `attempts` tries and then raises `StoreBusy`, which AD-39 describes as "retried briefly and then reported" and AD-5 describes as "never retrying silently".

**The incompatible pair.** Builder A (a new whole-file path, say `9a`'s render-instant record or `8q`'s one-key row writer) reads AD-5 and takes `platform.claims.exclusive` on `<file>.lock`. Builder B (any slice touching an artifact 8b already guards) takes `StoragePort.exclusive` on `.<file>.claim`. Both obey "a new whole-file write path takes a claim before it reads what it will replace". Neither lock sees the other; the lost update AD-5 exists to prevent is back, with both builders' tests green. A third pair: a job (9a's scheduled harvest calling `MeetingRecords.put`) meets `ArtifactBusy` and has nobody to "report it by name" to — the scheduler's backoff is exactly the silent retry the sentence forbids.

**Sentence that closes it.** *"There is one claim primitive: `StoragePort.exclusive` is a façade over `platform.claims.exclusive`, keyed on the artifact's resolved path, so two callers guarding one file hold one `flock`; `ArtifactBusy` is the one refusal (`ClaimHeld` is its platform spelling and is mapped, never caught separately); a claim that survives a kill is a defect, not a state the refusal explains; no caller waits except the sealed credential store, whose wait (`attempts × interval`, named in wiring) is the only wait and is re-decided nowhere; and a job that meets the refusal fails that attempt with the claim named in its `JobResult`, for the scheduler's recorded backoff — a retry that is logged is not silent."*

---

## F2 — AD-33: the connector mints the meeting id the record is supposed to keep, and the event cites it before adoption can happen

**Where.** AD-33 `[2026-10-09, Andrei]`: "Whichever record exists first in a scope keeps its id and citations never move … a later calendar harvest that finds such a manual-only record **adopts** it, writing the calendar reference into it, rather than minting a second."

**What the code does.** `run_harvest` (`pipelines.py:69`) writes every `result.records` meeting unconditionally: `for meeting in result.records: daemon.meetings.put(meeting)`. `MeetingRecords.put` keys the file on `(meeting_id, start's UTC day)` and replaces fields whole. The connector builds `Meeting.meeting_id` from Graph's per-mailbox event id (33c), and the `CALENDAR_EVENT_HELD` event it emits already carries `MeetingHeldPayload.meeting_id` and `source_ref = meeting:<graph id>` — before `app` has seen the record.

**The incompatible pair.** Builder A (`11b`) implements adoption in `put`: finds the manual-only `mtg_abc` by title and start, writes `calendar_event_ref` into it, keeps `mtg_abc`. Builder B (`33c` as shipped, `9a` scheduling it) persists the harvest's events, which cite `meeting:<graph id>` — a record that now does not exist. On the next harvest the same meeting arrives again (calendar windows overlap by design), `put` is called with `meeting_id=<graph id>`, and unless adoption is a lookup on *every* put, the second record the rule forbids is minted anyway. Both builders obey the sentence; the citation root AD-33 exists to protect dangles.

**Sentence that closes it.** *"Adoption is a lookup made on every write of a calendar-sourced meeting, not once: `MeetingRecords.put` resolves the record first by `calendar_event_ref`, then by the title-and-start rule, and only then mints. A connector's `meeting_id` is therefore a calendar reference and never a record id: the accessor returns the id it kept or assigned, and `app` fills `MeetingHeldPayload.meeting_id` and the event's `source_ref` from that id after the put and before `persist_events` — a connector never names a record id, and a `CALENDAR_EVENT_HELD` can only cite a record that exists."*

---

## F3 — AD-33: "adopts … writing the calendar reference into it" leaves the field, day, tolerance and two-candidate questions to two slices

**Where.** The same paragraph: "title matches ignoring case and runs of spaces and whose start is within 15 minutes … Two candidate matches refuse the drop".

**The incompatible pairs.**

1. *Which fields win.* `11b` reads "writing the calendar reference into it" literally: only `calendar_event_ref` changes; the drop's hand-typed title, start and attendees stay. `33c`'s `put` replaces fields whole with the calendar's. One record, two futures: a dashboard built from it shows the typed title in one build and the calendar's in the other, and `man_hour_cost` is computed from a typed attendee list or the calendar's.
2. *The day file.* `record_name` carries the UTC day of `start`; a calendar start 14 minutes after a hand-typed one that straddles midnight UTC lands on another day, and `put` raises `MeetingDisplaced` — the adoption the rule requires is refused by the accessor that must perform it.
3. *The candidate set.* `for_day(day, tz)` is the only read; a drop at 23:50 matched against `for_day(23:50's day)` cannot see a calendar record at 00:04 the next day, inside 15 minutes. Builder A searches one day file; builder B searches `[start−15, start+15]` across files. Same rule, different matches.
4. *"Ignoring case and runs of spaces"* says nothing about leading/trailing whitespace, tabs, or Unicode case folding; "within 15 minutes" says nothing about inclusivity. `11b` and `33e` each pick, and a title that matches in one does not in the other.
5. *Two candidates on the harvest side.* The rule refuses *the drop*; a calendar harvest finding two manual-only candidates is unspecified. Builder A mints a third record; builder B refuses the row (`RowRefusal`), which also drops the `CALENDAR_EVENT_HELD` event and the coverage window with it (AD-35: wholesale refusal withholds coverage).

**Sentence that closes it.** *"On adoption the calendar's fields replace the manual ones — title, start, duration, attendees — and only the record id and `## Notes` survive; the record is re-filed under the calendar start's day by the accessor, which is the one legitimate move of a record between day files. Candidates are every record in the scope whose start lies in `[start − 15 min, start + 15 min]`, inclusive, read across day files; titles compare after NFKC case-folding, trimming both ends and collapsing every whitespace run to one space. A calendar arrival with two manual-only candidates adopts neither, mints its own record, and names both candidates in the harvest's refusals without refusing the row."*

---

## F4 — AD-3/AD-45: "produced … from Tier 1 plus a clock" is false today, and AD-45's staleness rule skips the render

**Where.** AD-3 `[2026-10-09]`: "produced by a declared job (the render, `9a`) from Tier 1 plus a clock, replaced whole, never watched … deleted and re-rendered by `pm-ai reindex`"; AD-45: "nothing enters Tier 3 that a job cannot rebuild from Tier 1 alone … a requested job whose inputs are unchanged does no work, because staleness is the stored per-entry checksum against the file".

**What the code does.** `run_dashboard` calls `_todays_calendar`, a live read of every enrolled calendar (23b, decided 2026-09-07 because future meetings are not persisted). The render's inputs are Tier 1, the network, and the clock. There is no index to hold a source checksum for the dashboard; the only place one could live is the file itself or a Tier-2 row (9a "records the last render instant").

**The incompatible pair.** Builder A (`9a`) builds the render as AD-45's job: `inputs()` = `event_log/`, `meetings/`, `strategic_goals.md`; staleness by checksum; `reindex` deletes the file and requests the job — whose inputs are unchanged, so it does no work, and there is no dashboard until the next schedule; and a rebuild from Tier 1 alone, offline, yields "the calendar could not be read" where yesterday's file listed meetings. Builder B (`23b`/`23c`) keeps the live read and the clock-dependent Time-Critical section, so the output is never byte-stable across runs and never derivable from Tier 1. Each obeys its AD; the Tier-3 promise ("rebuildable from Tier 1 with zero loss") holds for one and is false for the other.

**Sentence that closes it.** *"`daily_dashboard.md` is Tier 3 because nothing in it is truth, not because a rebuild reproduces it: its inputs are Tier 1, Tier 2's last recorded harvest failure per instance, a live calendar read and the clock. It therefore declares no watched inputs, is triggered by schedule and by request only, a request always runs it (the checksum short-circuit applies to watched inputs, which it has none of), and `pm-ai reindex` re-renders it with whatever answers now and promises no byte-identity with what it deleted."*

---

## F5 — AD-39: "the briefing surfaces any instance not healthy" while "nothing persists health"

**Where.** AD-39 health bullet `[revised 2026-10-09]`: "It is not carried: nothing persists it … `pm-ai connector check` and the briefing surface any instance not healthy; `doctor` cannot, because health is not carried".

**The incompatible pair.** Builder A (`23c`/`9a`) reads "the briefing surfaces any instance not healthy" and calls `check_health()` inside the render — a live probe with a ten-second bound inside a Tier-3 job, run at 07:00 against the network, and the same probe `doctor` was just forbidden. Builder B reads "health is not carried" and surfaces the last recorded `HarvestFailure` per instance from Tier 2 — which is what AD-35's `ERROR` is built from and what `SPEC.md:206` calls the briefing's input. The two briefings disagree on the same machine: a token that died since the last harvest is "not healthy" in A and silent in B; a harvest that failed on a 429 four hours ago is "not healthy" in B and `OK` in A. `SPEC.md:206` still says `ERROR` is surfaced "in `pm-ai doctor` and the briefing", which the amendment contradicts — a derive is owed.

**Sentence that closes it.** *"The briefing never probes: it surfaces every instance whose last recorded harvest attempt is a failure (Tier 2's `harvest_failures`, the input AD-35's `ERROR` is built from), naming the instance and its remedy, and says when that attempt was. `check_health` is called by `connector check` and by the enrolment commands' bounded check, and by nothing else."*

---

## F6 — AD-39: "a connector never prompts" against "a connector needing human consent raises it as a staged item"

**Where.** AD-39, cadence bullet `[revised 2026-10-09]` and the re-consent bullet beneath it.

**The incompatible pair.** Builder A (`33d`, `8m`) returns a non-retryable `HarvestFailure` whose remedy names `pm-ai connector sign-in <instance>` and reports `FAILING` from `check_health` — the connector stages nothing, because AD-9's surface is closed and a connector may not reach the proposal store. Builder B (story 13) reads "a connector … raises it as a staged item on both surfaces" and hands every connector a proposal-staging port, which AD-9 forbids and which lets the connector decide the push occasion AD-40 reserves for a declared registration. Worse, a Proposal is "staged-then-approved" with an executor (AD-13): approving a re-consent card on Telegram would have the executor run a device-code flow that needs a terminal and a human reading a code — the Proposal cannot execute what it proposes.

**Sentence that closes it.** *"A connector reports a credential the provider will not renew as a non-retryable `HarvestFailure` whose remedy names `pm-ai connector sign-in <instance>`, and as `FAILING` from `check_health`; it stages nothing and reaches no proposal store. The scheduler (story 13) turns that failure into the staged item, under a registered push occasion (AD-40), and the item's executor does not sign in — approval acknowledges the remedy, and the sign-in is still the foreground command, because a device code needs a terminal."*

---

## F7 — AD-21: "foreground command" is defined by posture, so a REPL line can be read as exempt and a CLI verb as a request

**Where.** AD-21 `[revised 2026-10-09]`: the request path is "a line typed at the REPL (slice `4f`), a Telegram message"; exempt is "a command a person runs at a terminal and waits for".

**The incompatible pair.** `run_dashboard` performs N live calendar walks. Builder A (`4f`, the REPL) reads the first sentence: `dashboard` typed at the REPL is a line on the daemon's request path, acks at 5 s and delivers later. Builder B (CLI) reads the second: the REPL *is* a terminal and the person *is* waiting, so `dashboard` there runs inline under the bound `pm-ai dashboard --help` prints. AD-7 says no feature may differ by surface. A third reading: the bound `dashboard` "declares" is the CLI's, and `9a`'s scheduled render of the same pipeline declares none — one pipeline, one bound on one entry and none on the other, while the Graph fetch underneath has its own per-harvest time budget (33d) that N instances multiply past the command's.

**Sentence that closes it.** *"The request path is a transport, not a posture: everything that arrives through the daemon API — every REPL line, every Telegram message — is on it, whoever is waiting; the exempt set is exactly the `pm-ai <verb>` subcommands `entry` parses from `argv`, and a REPL or Telegram spelling of one of them acks at 5 s and delivers the same output. A bound belongs to the pipeline, declared once beside it in `app`, and every caller — the CLI verb, the REPL, the scheduled job — inherits it; a connector budget inside the pipeline is bounded by the pipeline's, never the reverse."*

---

## F8 — AD-9: `answered_at` names no clock, and two honest clocks exist

**Where.** AD-9 `[2026-10-09, slice 8p]`: "`answered_at`, the instant the provider answered".

**The incompatible pair.** "The instant the provider answered" is honestly the provider's `Date` header — builder A (`8n`'s real GitLab transport) reads it there, or reads the local clock when response headers arrive. Builder B (`33d`, the spec's "the instant the calendar fetch already measures") reads `self.now()` after the first page *body* is in hand. 8p's spec pins the local clock at body-read; the spine sentence does not, and the spine is what the next connector's builder reads. A third clock exists in the same process by rule: storage stamps `HarvestFailure.at` from its own clock and *ignores* the connector's (`harvest.py:160-166`), a precedent for restamping `answered_at` on the way in — after which the window-start equality 8p enforces at construction would fail against the stored value.

**Sentence that closes it.** *"`answered_at` is this machine's clock, read once by the connector when the first page's body is in hand — never a provider header, never read at the request or at the headers, never re-read or restamped by storage — and the connector's `now` and the storage clock are the one `clock` the composition root builds, so a window, an `answered_at` and an `ingested_at` are comparable by construction."*

---

## F9 — AD-27/AD-48: "a person-typed field is written as the id alone" writes `unresolved` and discards the handle, while `mentions` on the same line keeps raw ids

**Where.** AD-27 enforcement paragraph `[reassigned 2026-10-09]` and AD-48's list paragraph; `2m` spec: "The assignee is written as its actor id; a display name is an alias, never reaches a line".

**The incompatible pair.** `Actor` is `(actor_id, display_name)`; an unresolvable handle becomes `UNRESOLVED` (`identity.py:337`), whose id is a sentinel. Builder A (`2m`) writes the id alone: an unresolved assignee or sender lands on Tier 1 as `unresolved`, and the native handle — the only thing a later alias entry could resolve — is gone from the tier that is the source. Builder B (`33d`) writes `mentions` as raw Graph user ids, trusted, on the same line. One person: `p.actor=unresolved` as sender, `<graph id>` in `mentions` as the mentioned — two spellings of one identity on one line, unjoinable, and the Tier-3 rebuild AD-3 promises cannot re-resolve what Tier 1 never kept. Both obey "the id alone".

**Sentence that closes it.** *"On a line a person is the resolved actor id, or, unresolved, the native handle under the grammar's reserved spelling `unresolved:<system>:<handle>` — never the bare sentinel, so Tier 1 keeps what a later alias entry resolves and the rebuild can re-resolve it; an id list such as `mentions` uses the same spelling for an unresolved member, so a sender and a mention of one person are joinable on the line."*

---

## F10 — AD-11/4o: "commands aimed at the personal or application scope run" lets `doctor` and `connector check` count connectors differently

**Where.** AD-11 `[2026-10-09]`: "those commands run, on a daemon that watches no project"; `4o`: a GitLab row is "saved but not built … `connector check` does not list it"; `4n`: `doctor` lists "every enabled row naming a known system".

**The incompatible pair.** On one machine with no project and `gitlab:x` enrolled, `doctor` (4n) prints `1 enrolled: gitlab:x` and `connector check` (4o) prints "no connectors are registered", exit 0 — both to spec, and the only commands that answer the question disagree on it. The deferred-work note under 4n already names the gap and assigns it to "whichever slice gives `doctor` a build-time reason per row" — no slice.

**Sentence that closes it.** *"`connector check` reports every enrolled row, not every built connector: a row the daemon did not build is listed with the reason it was not built (no project enrolled, a settings key missing), never omitted, so `doctor`'s count and `connector check`'s count are one count and the difference between them is always a stated reason."*

---

## F11 — AD-6/Consistency: "hand-editable" survives as a Markdown property, so a Tier-3 `.md` can grow a human region

**Where.** AD-6 Prevents: "destroying git-diffability and hand-editability" (of a markdown file); Consistency Conventions: "parsers must tolerate hand-edits"; AD-46: "Markdown is hand-editable **by design** (AD-3, Tier 1)" — qualified there, unqualified in the first two.

**The incompatible pair.** `11a` gave meeting records a `## Notes` region no writer may touch, under the Markdown-wide wording. Builder A (`23d`/`23c`, the project dashboard) adds a `## Notes` region to `daily_dashboard.md` on the same precedent — "it is Markdown, parsers tolerate hand-edits, preserve the human's region on re-render". Builder B (`9a`) replaces the file whole, as AD-3 now says. A's test (notes survive a render) and B's test (no edit survives) cannot both pass against one file.

**Sentence that closes it.** *"Hand-editability is a Tier-1 promise and not a Markdown one: a Tier-3 `.md` has no human region, no `## Notes`, and no parser owes it tolerance — a reader that finds it edited reports nothing, and the next render replaces it whole."*

---

## F12 — AD-48: "exactly `tuple[str, ...] | None`" compares a spelling or a type, and the two differ under `from __future__ import annotations`

**Where.** AD-48 list paragraph `[reassigned 2026-10-09]`: "accepts exactly `tuple[str, ...] | None` as a list — a `list[str]` or a required tuple is refused".

**The incompatible pair.** With postponed annotations every field annotation is a string. Builder A compares the string: `Optional[tuple[str, ...]]`, `None | tuple[str, ...]` and `Tuple[str, ...] | None` are refused as "not exactly". Builder B resolves `get_type_hints` and compares the type: all three are accepted. `33d`'s `mentions` declared one way imports under B and is refused under A.

**Sentence that closes it.** *"The check resolves the annotation (`typing.get_type_hints`) and compares the resolved type, so `Optional[tuple[str, ...]]`, the reversed union and the `typing.Tuple` spelling are the same declaration; a string annotation is never compared as text."*

---

## Smaller notes, no pair found

- **AD-5, "an append relies on the record rule instead."** Two processes appending to one open segment without a claim: `_append` is `O_APPEND` and the record rule holds per `write(2)` only while each record is one write under `PIPE_BUF`-like atomicity, which Darwin does not promise for regular files above a page. `MAX_ENTRY_LENGTH` is 16,384. Worth one measured sentence in `1q`, which is already in `_append`.
- **AD-39, "under the shared credentials lock for that one instance."** `replace_credential` claims `config.json`, which is every instance's file; "for that one instance" is the *entry* written, not the lock's grain. One clause: "the lock is the file's; the write is the entry's."
- **AD-3, "both scopes' copies move together (AD-44)."** AD-44 makes durability global by basename; the project dashboard is also gitignored (`scope_model.py:733`), the personal one not — fine under the third grain, but `reindex` deleting a project-scope Tier-3 file inside a repository is a working-tree deletion git will show. Say that `reindex` touches project Tier 3 only under the gitignore AD-43 verifies.
- **`SPEC.md:206`** still says `ERROR` is surfaced "in `pm-ai doctor` and the briefing"; the amended AD-39 says `doctor` cannot. The spec is skill-derived, so the memlog, not the file, needs the line.

---

## Suggested landing

- F1, F4, F5 into the spine before `9a` is specified; F2, F3 before `11b`/`33e`; F6 before story 13; F7 before `4f`; F8 into AD-9 now (8p is in flight and the sentence is what 8p already builds); F9 and F12 into `2m` before it starts; F10 into `4o`/`4n`; F11 into AD-3 as one clause.
