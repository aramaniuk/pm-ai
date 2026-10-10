# Rubric review — spine amendments of 2026-10-09

Reviewer: architecture gate, rubric walker. Date: 2026-10-10.
Scope: only the passages tagged `2026-10-09` in `ARCHITECTURE-SPINE.md` and the memlog entries dated 2026-10-09, each read in its surrounding AD and checked against the cited code. Everything else in the spine is out of scope.

Checklist applied per passage: (a) still fixes a real divergence point for two units built one level down; (b) enforceable, and prevents the divergence it names; (c) contradicts no other AD; (d) ratifies what the code does today; (e) leaves nothing elsewhere in the spine silently disagreeing.

## Verdict

The decisions are sound and each one closes a divergence that had been found in the code; four of the amendments carry wording or cross-reference defects that leave the spine disagreeing with itself or with the modules it cites. Fix before the branch merges: AD-5's lock paragraph names the wrong mechanism, the dashboard's tier change is unapplied in two other places, the Identifiers row and two neighbours disagree about `evt_`, and the frontmatter date is stale.

## Findings

### F1 — AD-5 (line 173): the paragraph attributes `ArtifactBusy` to a mechanism that does not raise it, and ratifies the wrong lock design

What the passage says: what holds the single-writer rule today is `exclusive()` in `platform/claims.py`, "a `flock` on a sidecar the claim deliberately never unlinks", and "`ArtifactBusy` is therefore a real condition".

What the code does: there are two claim mechanisms with opposite designs.

- `pm_ai/platform/claims.py` `exclusive(guarded)` — `fcntl.flock` on `<name>.lock`, never unlinked, released by the kernel on death. Raises `ClaimHeld` (`pm_ai/domain/claims.py`). Its only caller is `pm_ai/app/wiring.py:1612`, for the project registry.
- `pm_ai/storage/service.py:1302` `StorageService.exclusive(scope=, artifact=)` — an `O_CREAT|O_EXCL` claim file `.<name>.claim`, removed on release, orphaned by a kill. Raises `ArtifactBusy` (`pm_ai/ports/__init__.py:160`). Callers: `core/connector_enrolment.py:377,453` (the credential store), `core/meeting_records.py:504` (`meetings/`), `app/wiring.py:1292` (the sealed refresh-token store). This is the one every Tier-1/Tier-2 whole-file path uses.

So the sentence names the flock and the exception belongs to the lock-file. Worse for rule (d): `claims.py`'s own docstring argues the lock-file design is wrong ("nothing releases it when that somebody is killed... the remedy is 'delete this file'"), and `service.py:1319` documents exactly that remedy for the storage claim. The spine now ratifies one of the two designs as "what holds the rule" while the artifact paths run the other. A builder of a new write path reading AD-5 will reach for `platform.claims.exclusive` and get a mechanism no artifact path uses, or for `storage.exclusive` and get the orphaning behaviour the paragraph says cannot happen ("never unlinked").

Checklist: (a) yes, real divergence; (b) not enforceable as written — it names a function that is not the one on the path; (c) conflicts with AD-39's "a busy file is retried briefly and then reported" only in tone (the retry at `wiring.py:1193` is bounded and then reports by name, so the two are reconcilable — say so); (d) no.

Fix: name both mechanisms and which artifacts each guards; say which design is the rule for a new path (the memlog's "sidecar never unlinked" suggests the flock is intended — then the storage claim is a gap row, not the substitute), and pin that `ArtifactBusy` is raised by `storage.exclusive`, `ClaimHeld` by the registry claim. Either way, record the orphaned-claim remedy as the admitted cost or as the thing slice `4e` removes.

### F2 — AD-3 / the dashboard is Tier 3 (lines 92, 103): two places in the spine still file it under Tier 1

The decision is consistent across AD-3's two tables, AD-44 (line 632), AD-47's write-shape table (line 694), the Deferred graph-depth note (line 1048) and the gap row (line 1115). Two passages were not touched:

- AD-3, line 108: "the **project** tree's `memory/` and its four members — `event_log/`, `commitments_log.md`, `daily_dashboard.md`, `meetings/` — joined them" is in the paragraph that explains why **Tier 1** is mostly not committed. The dashboard is no longer a Tier-1 member; the sentence now lists a Tier-3 artifact as one of Tier 1's four.
- The storage diagram, lines 904 and 909: `P2["memory/: goals, coaching, dashboard, ... — T1, plaintext md"]` and `R2["memory/: dashboard, commitments_log, ..."]` under the `PROJ` subgraph headed "plaintext md, T1". Only `A6` carries the T3 label, and it does not name the dashboard.

Checklist: (a) yes; (b) yes once `scope_model.py:540,733` flips (owed by `9a`, and `tests/architecture/test_capture_guard.py:490-519` and `test_encryption_policy.py:125` read the dashboard off the trees, so the flip has tests to carry it); (c) no contradiction in the ADs; (e) the two disagreements above.

Fix: drop the dashboard from line 108's list (three members, with a dated note), and move it in the diagram from P2/R2 into a T3 box beside the indexes — or at least strike the "T1" label on the project memory node.

Also worth a clause: the Tier-3 contract says "produced by a declared job (AD-45)... deleted and re-rendered by `pm-ai reindex`". Today `run_dashboard` (`pm_ai/app/pipelines.py:253`) is an inline pipeline called from the CLI, not a job row, and `pm-ai reindex` exists only in docstrings (`platform/paths.py:157`, `core/ledger.py:6`). AD-3 already said this of the indexes, so nothing new is wrong, but the dashboard sentence reads as present tense and the "declared job" half is also `9a`'s — say that both halves are owed, not one.

### F3 — Identifiers row (line 718): "random, not time-sortable" while two other rows and AD-34 still call `evt_` a ULID

`storage/service.py:300` `_ulid()` returns `"evt_" + secrets.token_hex(10)` — random, as the row now says. But:

- the Event envelope row (line 720): "the `evt_` **ULID** surrogate is minted by storage at persist";
- AD-34 natural key (line 456): "The `evt_` **ULID** is a surrogate assigned by the storage service";
- the function itself is named `_ulid`.

A ULID is by definition time-ordered, so the word re-asserts the claim the row withdraws, two lines below it. Also: AD-33 now mints `mtg_` ids ("`mtg_` plus random hex") and the Identifiers row's prefix list does not include it.

Checklist: (a) the row was a divergence point (story 2f asked whether to build sortability); (c) contradicts lines 456 and 720; (d) ratifies the code except the function name.

Fix: replace "ULID" with "surrogate" at 456 and 720; add `mtg_` to the prefix list; ask `2m` or the next storage slice to rename `_ulid` (a one-line rename, no on-disk effect).

### F4 — AD-21 (line 311): the exemption adds an obligation nothing builds and nobody owns

"runs inline and **declares its own bound and prints it in its help**" — `pm_ai/surfaces/cli/dispatch.py` has one bounded command, `connector check` (`:972`, ten seconds, in its docstring/help). `dashboard`, `doctor`, `setup`, `connector add`, `connector sign-in`, `project add`, `goal set`, `key enrol`, `config show` declare no bound and print none. The struck gap row (line 1116) had two halves — `connector check`'s ten seconds under a five-second rule, and "`pm-ai dashboard` performs N live calendar walks inline with no bound at all" — and the narrowing closes only the first. `deferred-work.md` records no AD-21 item, and no slice in `stories.yaml` is named for it.

Checklist: (a) yes — the five-second rule would otherwise be read as precedent either way, as the gap row said; (b) not enforceable as written: there is no test, no slice, and the dashboard's bound is not knowable until `9a` moves the calendar reach-back off the render path; (c) none; (d) no — present tense for nine commands that do not do it.

Fix: either mark the "declares and prints" clause not yet built with an owner (the natural one is whichever slice touches each command's help next, with `dashboard`'s bound settled by `9a`), or weaken it to "names its bound where it has one; a command with no bound says so" — which is what the code does today.

### F5 — AD-9 `answered_at` (line 211) versus AD-35 (line 469): the two ADs put the window's start on opposite sides of the request

AD-9 now: `answered_at` is "the instant the provider answered... a coverage window must start at". AD-35, unchanged: "the bounds are the connector's own clock either side of the request" — which puts the start *before* the request is sent. Story `8p`'s spec (line 27) resolves it by reusing the reading GitLab already takes "once the first page is in hand" (`gitlab.py:522-523`), i.e. after the answer. So AD-9 and `8p` agree, and AD-35's "either side" is now the stale phrase. A builder of the window check (`harvest.py:405-422`) reads both.

Also: `HarvestResult` (`harvest.py:272`) has no `answered_at` today (fields: events, cursor, outcome, coverage, failure, refusals). The sentence is tagged `[slice 8p]` but written in the present tense ("which every result that reached the provider carries"); the spine's convention elsewhere is an explicit "not yet built".

Fix: in AD-35 replace "either side of the request" with "from the instant the first page was read (`answered_at`, AD-9) to the clock after the last", and mark AD-9's clause not yet built, owed by `8p`.

### F6 — Frontmatter `updated: 2026-10-08`

The spine carries nine passages dated 2026-10-09 and the memlog's last eight entries are 2026-10-09; the frontmatter was not bumped. Fix: `updated: 2026-10-09` (or the date of the next derive).

## Passages that pass

- **AD-39 custody bullet (line 528).** Ratified: `wiring.py:1251` `replace_credential` is accepted only inside `exclusive()`; `connectors/graph/auth.py:805,1328` go through `self.store.exclusive()`; the bounded retry is `wiring.py:1193`. The withdrawn "never writes one itself" was a real contradiction. No other AD says "never writes" (grepped). One wording note: "sealed under the shared credentials lock for that one instance" reads as if the lock were per instance; it is one lock on one file, the entry is per instance.
- **AD-39 scheduler bullet (line 529).** "A connector never prompts for a credential" — consistent with the next bullet (re-consent is a Proposal) and with AD-9 (no connector timer). `connector sign-in` is a real command (`dispatch.py:321`). Pass.
- **AD-39 doctor vs connector check (line 531).** Consistent with the Structural Seed's Health line (946: "registry membership only, contacting nothing") and AD-11's always-OK `project selection` probe. Owed by `4n`, named. Pass.
- **AD-11 built-by-4m paragraph (line 239).** Consistent with the memlog's adoption of `4o`. One phrase imports an object that does not exist: "those commands run, on a daemon that watches no project" — there is no daemon (gap row line 1112); say "with nothing enrolled" instead.
- **AD-27/AD-48 reassignment to `2m` (lines 366, 1021).** `2m` exists in `stories.yaml` (line 213) and `stories/2m-payload-line-grammar.md`; `deferred-work.md:303,861,865` carry the assignment. The list rule now accepts exactly `tuple[str, ...] | None` in both ADs. The dated exemption (Actor written as id alone, repr spellings corrected before any real line) is consistent with "a line is never re-rendered" because no machine-written line exists — stated. Pass.
- **AD-33 one record per meeting (line 445).** Rule is decidable (title fold + 15-minute start; two candidates refuse; none mints). Scope-local matching is reconciled with the untagged-meeting gap row (line 1109) explicitly. `33e` and `11b` exist in `stories.yaml`. The 15-minute and title-fold constants appear nowhere else in the spine, so nothing disagrees. Pass, apart from the `mtg_` prefix omission in F3.
- **Deferred graph-depth note (line 1048).** "Tier 3 is unwatchable (AD-46)" matches AD-46 line 670 and AD-45 line 658. Pass.
- **Gap rows (lines 1112, 1115, 1116, 1120, 1121).** Five, as the memlog says: one annotated, four struck, each naming the closing decision or story. The AD-5 row is correctly left open on the daemon. Pass — but the AD-21 row's second half is not closed (F4).
- **Open Risks — watchdog (line 1151).** Verified this run: `uv run --no-sync python -c "import watchdog, _watchdog_fsevents"` succeeds under 3.14.7. Pass.
- **Open Risks — Rosetta (line 1152).** Verified this run: `uname -m` is `arm64`; `uv python list --only-installed` shows only `macos-x86_64-none` builds (3.15.0rc1, 3.14.7, 3.13.14); the project interpreter reports `platform.machine() == "x86_64"`. The claim that both builds ship the same Unicode tables is plausible and not checked here. Pass.
- **AD-44 grains paragraph (line 632).** "Both are Tier 3 as of 2026-10-09" — consistent. Pass.
- **AD-47 write-shape table (line 694).** Dashboard moved to whole-file replace with the tier noted. Pass.

## Cross-reference greps performed

`hand-edit` (90, 103, 178, 666, 723, 873, 1115 — none now claims the dashboard is hand-editable); `ULID|sortable` (456, 718, 720 — F3); `watch` near dashboard/Tier 3 (103, 652, 658, 670, 1048 — consistent); `5 seconds|acknowledg` (305, 309, 1116 — consistent after the narrowing); `never prompt|re-consent` (529, 530 — consistent); `doctor` (189, 224, 239, 311, 531, 946, 981 — consistent); `mtg_|15 minutes|manual-only` (445 only); `answered_at|either side|own clock` (211, 469 — F5); `daily_dashboard|dashboard` across the Structural Seed, Capability map and storage diagram (904, 909 — F2; 965, 996 — no tier claim).
