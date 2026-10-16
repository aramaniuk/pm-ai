# Adversarial review — the wave-3 amendments (spine, 2026-10-16)

**Scope.** Every `[2026-10-16]` amendment to `ARCHITECTURE-SPINE.md`: AD-1 (classes F and LM), AD-3 (aliases, GitLab project work), AD-5 (a mode change is not a write; the goals refresh point), AD-6 (what "private" covers), AD-7, AD-9 (the optional calendar read), AD-12, AD-15, AD-16, AD-17, AD-19 (the model pool), AD-21 (the two routing tables), AD-23 (four drop formats), AD-31 (`sent`/`completed`), AD-33 (the decision reference), AD-34 (scopeless restated, `decision`, `gitlab`, the saved alias table), AD-38 and AD-49. I read them against the 21 wave-3 slices that build on them (`1s`, `4s`–`4w`, `7a`–`7e`, `8r`–`8t`, `11d`, `23e`–`23g`, `33g`–`33i`), and checked where a slice leans on code that already exists: `pm_ai/app/daemon.py` (start order), `pm_ai/app/wiring.py` (`_enrolment_scope`), `pm_ai/app/concurrency.py` (`IOPool`), `pm_ai/surfaces/cli/dispatch.py` and `pm_ai/app/prompts.py` (the routing tables).

**Method.** For each finding I name two units. Each unit obeys every AD as now written. Together they still build something incompatible: two shapes for one piece of data, two owners of one entity, or two paths that change one piece of state. Each finding ends with AD text that would close it.

**Verdict.** The amendments are careful one slice at a time. Where they come apart is where two slices meet. Four holes are serious enough to fix before the slices are built:

- A meeting summary's version check guards only its last write, so a summary that loses the race has already overwritten the record and the staged items (Finding 1).
- The saved alias table is application-wide and maps a name to one handle. That makes it the first reader to join `gitlab:` ids across projects, which AD-34's own stated limit rules out (Finding 2).
- GitLab project work is keyed by project scope, but nothing stops two GitLab rows from filing into one scope (Finding 3).
- Jobs waiting on the single model thread hold I/O workers, which starves the request path. 7c also puts a model call on the I/O pool, which AD-19 forbids in so many words (Finding 4).

Findings 5–11 are smaller but each still names two units that clash.

---

## Finding 1 — Critical: a summary's version check guards only its last write; the losing job has already written the record, the decisions and the staged items

**Unit A — `7b`'s automatic summary job, written to the letter.** "A job reads the version when it starts and its final write compares and sets it." It also states the write order: "the record's block, the decision events, the proposals, then the state row". Superseding happens with the proposals: "each still-staged `action_item` … whose id the new summary does not produce is moved to `superseded`". Every step is a correct AD-5 claimed step, and each one "leaves storage complete without the steps after it".

**Unit B — a requested summary (`7b` `--by local`, or `7c` `--by claude`).** "A requested summary sets the row first, so an automatic job still running loses its write and is discarded." The `Two jobs` matrix row allows both to be in flight at once.

**The interleaving.** The request finishes first and writes its block (provenance "Claude …, on request"), its decisions and its proposals, then its row. The automatic job finishes next. It writes its block over the request's block, appends its own differently worded decisions (AD-33: "decisions accumulate"), and supersedes the request's staged items while staging its own. Only then does its version check fail on the row. The end state:

- the row says `summarized`, `frontier`, asked for;
- the record says "local model, automatically";
- the staged items are the automatic set;
- the frontier answer the PM paid for is gone everywhere except its `completed` ledger line.

`7d` reads "who made it" from the record and the state from the row, so the page contradicts itself. `7e` has already been handed both answers and has appended lines for both. A later part re-opening the summary (`33g`) and a calendar `put` resetting the row to "offered" (`7b`) are two more writers of the same row with the same gap.

AD-37 does not catch this. Its rule covers Proposal and Commitment transitions only, and the summary state is neither.

**Proposed AD-37 addition:**

> **A multi-step write guarded by a version is fenced at its first write, not its last** `[2026-10-16]`. A job that will write several artifacts on the strength of a row's version takes a lease first: one claimed step that compares and sets the row from the version it read to `writing`, naming the job. Every later claimed step of that job checks the lease inside the same step, on the writer thread, before it writes. A request that supersedes a running job moves the row to a new version, which revokes the lease, so the losing job stops before its next write rather than after its last. A row that a command still writing for itself (before story 10) also writes takes its version as `version + 1` in the same statement, never a constant. This binds `7b`'s and `7c`'s summary row, `33g`'s re-open, and the calendar `put`'s reset.

---

## Finding 2 — High: the saved alias table is application-wide and maps a name to a `gitlab:` id, which joins numeric ids across hosts and projects

**Unit A — AD-34 as amended, and `8t`.** "`gitlab` … *Stated limit:* a numeric id is unique within one GitLab host only. Every reader so far (`8t`, `23g`, `7e`) reads it inside one project scope … joining `gitlab` handles across projects needs the host in the handle first."

**Unit B — AD-34's saved alias table, and `7e`.** It is "Tier 1, application scope (`aliases.md`) … one row per name the PM confirmed … optionally limited to one meeting", and "Later name resolution — Graph speakers, chat senders — reads the same table; there is no second map, per project". `pm-ai member name --scope project:alpha "Ваня" ivan.p` appends `Ваня → gitlab:17`. The row records no project and no host. Only a meeting can narrow it.

**The clash, in two directions:**

1. **Across hosts and projects.** Project `beta`'s GitLab is a different host, and `gitlab:17` there is a different person. `7e`'s rule (1) is "an alias row … whose handle is a member in `8t`'s record". It finds `gitlab:17` among beta's members and attributes beta's "Ваня" to beta's user 17, on a team page. Each slice reads "inside one project scope". The table between them is the cross-project join the AD says needs the host first, and the AD's sentence "every reader so far reads it inside one project scope" is false the day `7e` lands.
2. **Across handle systems.** A person has a `graph:` handle and a `gitlab:` handle. The table maps a name to *one* handle, and "a later row removes or replaces a mapping". The Graph-speaker resolution the AD invites writes `Ivan Petrov → graph:abc`, which replaces `7e`'s `Ivan Petrov → gitlab:17`. `7e`'s rule (1) then finds no member and falls back to name equality: the PM's confirmation is silently lost. The reverse order loses the Graph mapping. The table has two owners keyed on one column.

**Proposed AD-34 text (replaces "keyed by one `normalise()` … optionally limited to one meeting"):**

> A row is keyed by `(normalise(name), handle system, context)`, where context is the project scope for a project-local system (`gitlab`, until its handle carries the host) or the application for a global one (`graph`, `email`), optionally narrowed further to one meeting. A later row replaces only a row with the same key. A name may therefore map to one handle per system and per context, and a reader asks for the system it joins on. A `gitlab` row is never read outside the project it was written for. When the handle → person table is saved, it joins one person's handles across systems; the alias table never does.

---

## Finding 3 — High: GitLab project work is keyed by project scope, but two GitLab rows can file into one scope

**Unit A — `8t` and AD-3 as amended.** "`operational.db` holds, per project scope, the members, the issues and their assignees, the merge requests …" Members are "replaced" on every complete walk. The reader is `project_work(scope)`. `7e` builds an issue's reference "`gitlab:<pm-ai project id>:issue:<iid>` from the scope and the iid", and `23g` resolves a bare `#142` against "the open issues in `8t`'s record".

**Unit B — AD-10 and the shipped placement rule.** A connector instance is `(scope, connector_type, config, cursor)`, and nothing limits a scope to one instance of a type. `wiring._enrolment_scope` (`wiring.py:1171`) files *any* GitLab row into the only watched project ("a row named anything at all … files into that project"). So `gitlab:alpha` (repository `acme/api`) and `gitlab:acme/web` both file into `alpha` today. AD-34's `gitlab:` handle is host-local, and the two rows may name different hosts.

**The clash.**

- Issue iids are per GitLab project, so `acme/api#142` and `acme/web#142` are one row and one `gitlab:alpha:issue:142`.
- Each row's complete members walk replaces the other's members, so `23g`'s list flips between two teams on alternate cycles.
- `gitlab_work_status` holds one status per section, which two walks overwrite.
- A bare `#142` in a commit, or one said in a meeting, is ambiguous.
- `gitlab:17` from two hosts is two people under one handle.

Re-pointing one row's `project` key at another repository merges the new repository's issues into the old one's rows by iid, with no error.

**Proposed AD-3 text (the GitLab paragraph):**

> **GitLab project work is Tier 2, keyed by the connector instance that read it** `[2026-10-16, 8t; tightened]`: `(project scope, instance, host, GitLab project id)`, never by the scope alone. A row whose host or GitLab project changes starts a new key and leaves the old rows to prune. `project_work(scope)` returns one section per instance. **Until a reader can name which repository a bare `#N` means, a project scope has at most one GitLab instance:** enrolment and composition refuse a second, naming the first. An issue reference carries the instance.

And in AD-34, move "joining `gitlab` handles across projects needs the host" to "across instances", since one scope can hold two hosts.

---

## Finding 4 — High: jobs waiting on the model thread hold I/O workers, and `7c` runs a model call on the I/O pool, which AD-19 forbids

**Unit A — AD-19 as amended.** "The daemon's **model pool is one thread** … A model call never runs on the loop, the writer thread or an I/O worker … the model pool takes model work only." `7a` says the same. `7b`: "Reading the parts, sanitizing and splitting run on an I/O worker; each model call on the model pool", with 300 s per call, chunked, and up to 3 meetings per harvest plus requests. `7e` adds one classification call per project meeting.

**Unit B — `4r` as built.** "Jobs run on the I/O pool, and so does every inline command; a fifth job waits `queued`." The status probes also run on the I/O pool (`4r`, `concurrency.py:351`, four threads).

**The clash, two ways:**

1. **Starvation.** Each summary job's body sits on an I/O worker, waiting on a model-pool future that queues behind every other model call. With three harvest-offered meetings and one requested summary, all four I/O workers are blocked for tens of minutes. Then `config show` and `goal list`, which AD-21 says answer inline in under five seconds, wait behind them, and so does the status probe, so `doctor` reports a running daemon as not answering. AD-5 caps the wait for the *writer* thread at one second. Nothing caps the wait for an I/O worker, and nothing forbids an I/O worker from waiting on the model pool. `4w` met the same problem from the other side: it kept `logs --follow` off the daemon because it "would hold one of the daemon's four workers".
2. **The frontier call has no legal pool.** `7c`: "The request runs on the I/O pool as a bounded step with a 300-second deadline — it is network waiting, not local memory." By AD-19's words that is a model call on an I/O worker. If instead the single helper `7b` builds is used, which submits every `ModelPort.complete` to the model pool, a 300-second frontier call holds the one heavy-local-model slot and the local queue waits for Anthropic. The router knows the side (AD-15's table), but the caller chooses the pool before it calls the router, and no AD connects the two.

**Proposed AD-19 text:**

> **The pool is chosen by the router's side, not by the caller** `[2026-10-16]`. `local` classes run on the model pool. `frontier` classes are network waits and run as bounded steps on the I/O pool, under the router's own submission. A caller hands the router a request and awaits it; it never chooses a pool. **No I/O worker waits on the model pool.** A job whose next step is a local model call ends its turn on the worker and is resumed by the model pool's completion, so a worker is never held across a model queue. **One I/O worker is reserved for the request path** (inline commands and the status probe). Jobs use the other three, so AD-21's inline answer and `doctor`'s two-second status do not wait behind background work.

---

## Finding 5 — Medium: the disclosure ledger has three statuses, and the total counts two

**Unit A — `7c`, the writer.** A stop reason other than `end_turn` writes "a `completed` line with the usage reported" (and nothing to the record). A failure after sending writes "`failed`, input unknown unless the answer reported usage". So a `failed` line can carry real usage, which is a real cost.

**Unit B — AD-38 and `7d`, the reader.** "The monthly total counts completed calls and states sent-but-unfinished ones as calls of unknown cost, never as free." `7d`: "completed calls, their total cost …, calls of unknown cost if any."

**The clash.**

- A `failed` line that carries usage is neither completed nor unfinished, so the total drops a paid call. That is the "free" the AD forbids.
- A `sent` line still in flight reads the same as one abandoned by a crash, so a render during a call reports an unknown-cost call that later becomes known.
- The month is not tied to either line. A call sent at 23:59 UTC on 31 October and completed in November is "unknown" in October forever unless the reader joins across months, and `7c`'s reply ("the month's total") is computed at completion.
- Nothing says how the call id is minted. A per-process counter pairs a `completed` with another run's `sent` after a restart.

**Proposed AD-38 text:**

> A call id is random (`call_` plus 128 bits of hex) and never reused. Each call is attributed to the UTC month of its `sent` line. A reader pairs lines by call id across month boundaries. The monthly total sums the reported cost of every line, `completed` or `failed`, that carries usage. It states as unknown every `sent` line with no later line and every `failed` line without usage. It never counts either as zero. `completed` means "an answer arrived", not "the answer was written".

---

## Finding 6 — Medium: AD-33's adoption rule drops the `## Summary` region that `7b` says survives every `put`

**Unit A — AD-33, tightened 2026-10-10.** "On adoption the calendar's fields replace the manual ones — title, start, duration, attendees — and **only the record id and `## Notes` survive**; the accessor re-files the record under the calendar start's day."

**Unit B — `7b`.** "`MeetingRecord` gains `summary`, carried over on every `put` as `## Notes` is." The summary row lives in Tier 2, not in the record.

**The clash.** The PM drops a transcript, and the meeting is minted manual-only and summarised. The next calendar harvest adopts the record. An accessor built to AD-33's letter drops `## Summary`, while the state row still says `summarized` and nothing re-offers it. `7d` then shows "summarized" with no excerpt, and `7e`'s lines keep citing decisions the record no longer lists. There is also a smaller drift: the decision events' time is "the meeting's start", which adoption changes and the appended events do not.

**Proposed AD-33 text:** replace "only the record id and `## Notes` survive" with "the record id, `## Notes` and every pm-ai-written region declared by a later slice (`## Summary`, `7b`) survive. A region that does not parse is dropped, and the row that owns it is reset to be made again."

---

## Finding 7 — Medium: AD-21 sorts a command into a table by name, but `transcript drop` asks questions only for some arguments

**Unit A — AD-21 as amended.** "Each of these commands is in exactly one of two tables … `TERMINAL_BOUND` — it asks questions, reads a secret, or holds the terminal." `transcript drop` is in neither AD-21 list. In code it is not in `TERMINAL_BOUND` (`dispatch.py:2768`), so `4q` sends it to the daemon, and `11d` says so: "`~/x.vtt` typed at the prompt and sent to the daemon".

**Unit B — `11c`, kept by `11d`.** "Otherwise the command asks for the title and start … Without a terminal, a drop that would need to ask is refused before anything is written." Only `--meeting <id>` avoids the questions.

**The clash.** At the prompt, every drop without `--meeting` reaches the daemon, which has no terminal, and is refused. That includes `11d`'s own `~ at the prompt` matrix row (`--scope personal`, no meeting), which cannot pass. `4t` and `7e` solved the same shape the other way: `goal update` and `member name` ask only sometimes, and both are in `TERMINAL_BOUND` "whatever options are typed". So two slices classify by "may ask" and a third by "is a path command".

**Proposed AD-21 text:** "A command that *may* ask a question for some arguments is `TERMINAL_BOUND` for all of them. If it must also reach the daemon, it splits: the questions are asked in the client's process and the answers are sent as options, never asked by the daemon. `transcript drop` is `TERMINAL_BOUND` until its questions become options."

---

## Finding 8 — Medium: `7e` retries a failed title match through `7b`'s offer, and `7b`'s offer re-summarises

**Unit A — `7e`.** When the classification call fails, "the meeting is offered again at the next harvest within `7b`'s bound of 3 meetings per harvest, at most 3 attempts". `7e` declares only Tier-1 ledgers. It names no row holding the attempt count and no offer mechanism of its own.

**Unit B — `7b`.** The only offer mechanism is the summary state row moving to `waiting`. Offering a meeting means summarising it again, and a re-summary "appends decisions it words differently", supersedes items and hands new item ids to `7e`, which writes `withdrawn` lines.

**The clash.** A builder who reuses the one offer path turns "retry one local classification" into "re-summarise the whole meeting". That costs a chunked model run, and at temperature 0 with a reworded merge it can produce new decision events and churn the staged items, all to retry a match. A builder who does not reuse it needs a Tier-2 row nobody declared, and the retry consumes `7b`'s per-harvest budget of 3 without either slice saying who wins. The per-harvest offer budget has two owners.

**Proposed text (AD-45 or AD-3):** "Each derived step after a summary (`7e`'s match) keeps its own Tier-2 state row and attempt count, keyed by `(scope, meeting, step)`, and is offered on its own. A retry of a later step never re-runs an earlier one. The per-harvest bound is per step."

---

## Finding 9 — Medium: decisions accumulate in the event log with no record of which reading is current

**Unit A — AD-33/`7b`.** "Decisions accumulate: a re-summary that words a decision differently appends another and removes none." The current set lives in the record's `## Summary` list.

**Unit B — `7e`.** For "each decision the new summary no longer lists, a `withdrawn` line is appended" to `member_updates.md`, a project-only file.

**The clash.** The current set of decisions has two owners: the record (rewritten) and `7e`'s ledger (project meetings only). The event log, which is the domain truth (AD-24), has none. A personal meeting's decisions are never withdrawn anywhere. A later event-log reader, such as FR-25's drift audit, a decision search or `7c`'s own output beside `7b`'s, sees both wordings of every re-summarised decision as standing, and can tell them apart only by opening a record that may have been purged or adopted (Finding 6). AD-33 calls derived records "self-contained", yet this one cannot say whether it is still standing.

**Proposed AD-33 text:** "Each summary that writes decisions appends one self-action, `decisions_read`, in the meeting's scope, naming the summary (row version and maker) and the decision references it holds. A reader's current set is the latest `decisions_read` for the meeting. Earlier decision events stay as history and are never deleted. `7e` takes its withdrawals from this entry rather than computing its own."

---

## Finding 10 — Low–Medium: the mode walk runs while the port already accepts connections, and changes modes by path

**Unit A — AD-5/AD-7 as amended, and `1s`.** The walk "runs after it holds AD-7's lifetime lock and before the writer thread starts". It has no bound, and it follows a root that is a link (`1s`: "A root that is itself a link is followed once"). That root can be a large `~/.manager-ai` holding a `.git`, or a network share.

**Unit B — the start order as built.** `daemon.py:201-208` binds and `listen(128)`s the port *before* taking the lifetime lock, so that a second daemon "meets the first one's listener". AD-5 also says the master key is asked for "before it listens", which the code already contradicts.

**The clash.** For as long as the walk takes, clients connect and get no answer. `doctor`'s two-second status reports the daemon as down, a `transcript summarize` waits on a socket nobody serves, and AD-5's one-second "busy, try again" never fires because no server exists to send it. Separately, a command that still writes for itself (before story 10) can publish by rename between the walk's `lstat` and its path-based `chmod`. The new file then gets a mode computed from the old file. Group and other bits are never added, but owner, setuid and execute bits can be, which goes against "no mode is ever widened". The hard-link check has the same window.

**Proposed AD-5 text:** "The walk is a bounded step on an I/O worker, run after the daemon serves (it is not a write, so it needs neither the writer nor a pre-listen slot), with a time budget whose overrun is logged and resumed at the next start. Each entry is opened `O_NOFOLLOW` and its mode set with `fchmod` from that descriptor's own `fstat`, so a path replaced mid-walk never receives another file's mode. The spine states the start order as built: port, lifetime lock, writer, master key, serve."

---

## Finding 11 — Low: AD-9's fallback calls a calendar connector's `harvest` from the render

**Unit A — AD-9 as amended.** "A calendar connector without the read is asked through `harvest`, and the render selects the day from what it returns", and "the harvest's window, cursor and coverage are untouched by it".

**Unit B — what `harvest` is.** It owns a window policy (not the display day), and for Graph it also walks channels, chats and transcripts within the shared budget. AD-21 makes "the scheduled harvest the one cursor-owning read".

**The clash.** A render through the fallback makes every provider call a harvest makes, then discards all but the calendar. Because the window is not the day, the fallback also breaks `23e`'s "the day asked for and the day shown cannot disagree". It is unreachable today (Graph implements the read). The next calendar connector inherits it.

**Proposed AD-9 text:** "A calendar connector implements `read_calendar`. The render never calls `harvest`, and a connector without the read is not shown on the day's schedule."

---

## Not findings

- The decision reference's form (`decision:<meeting id>_<digest>`) parses safely if split at the last `_`. Say so in `domain` beside the grammar.
- `33i`'s scope move keeps the cursor, and the reference carries the scope, so no message is filed twice.
- `33g`'s parts and `11d`'s `-manual.txt` captures do not collide: `11c`'s one-capture prefix rule refuses a drop once a Graph part exists, and `33g` does not ask about a meeting holding a manual capture. Name the manual suffixes `33g` checks (`-manual.*`, not `-manual.vtt` as its matrix spells it) when it is built.
- `7c`'s key stored under its own top-level key in `config.json` depends on the CLI's enrolment keeping keys it does not know. `7c` states this as a constraint. A test that enrolment round-trips an unknown top-level key would make it checkable.
