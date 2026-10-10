---
title: 'One claim for every whole-file write'
type: 'refactor'
created: '2026-10-10'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Until the daemon exists, every `pm-ai` command writes files itself, and what stops two commands from overwriting each other on a file rewritten whole is a claim held across the read and the write. pm-ai has two such claims with opposite designs. The project list (`projects.toml`) uses a kernel lock: if the holder is killed, the kernel releases it and the next command carries on. The credentials file (`config.json`), the meeting records (`meetings/`) and the Graph sign-in store use a marker file: if the holder is killed, the marker stays and every later command refuses — the credentials file stays unusable until someone deletes a hidden file the refusal names. The second design is the one the first's own reasoning rejects, and each new whole-file path (`8q`, `9a`, `9b`) would have to choose again.

**Approach:** One claim, the kernel lock, keyed on the file it guards, so two callers guarding one file hold one lock. The storage service offers it through the same door as today and reaches it through a small port the composition root hands in, as it already does for the keychain and git. One refusal, named the same everywhere. Nothing waits for a busy file except the Graph sign-in store, which already does.

## Boundaries & Constraints

**Always:**
- **One mechanism.** The claim is a kernel lock (`flock`) on an empty sidecar `<name>.lock` beside the file it guards, as the project list already does. The marker-file design is removed, not kept as a fallback; exclusivity now requires a filesystem with `flock` semantics.
- **Keyed on the file.** The file's path is canonicalised before the sidecar is named, so two spellings of one file share one lock; whoever asks — enrolment, the sign-in store, project onboarding, a record write — gets the same lock for the same path.
- **A kill releases the claim.** The kernel drops the lock with the process, however it died. The sidecar is never deleted (deleting it races a process already waiting on it) and is declared once, beside the scope trees, as structure outside the tier model — never backed up, rebuilt, pruned or encrypted — so the set of files under `~/.pm-ai`, `~/.manager-ai` and a project's `.project-ai/` is derivable from the trees again. Inside a repository the sidecar is covered by a derived ignore rule, as the artifacts beside it are.
- **One refusal.** A busy file is refused as "artifact busy" (`ArtifactBusy`), naming the artifact and its scope: nothing was changed, run the command again. It names no file to delete, because there is none. The lock's own refusal (`ClaimHeld`) is translated into it once, where the claim is taken, and caught by no command; a claim refused inside a claimed body is that inner file's refusal, never re-reported as the outer file being busy.
- **A fault is not "busy".** A filesystem without `flock` (exFAT, some network mounts — reachable only through an enrolled project repository) is refused naming the mount and never silently unlocked; a permission fault on the sidecar, or a sidecar that is a directory, a link or unreadable, is the operating system's own error, reported as such.
- **A refusal is immediate.** No caller waits, except the Graph sign-in store, whose wait — a number of tries at an interval, about two seconds in all, stated once in the composition root — is the only wait in pm-ai and is not re-decided by this slice.
- **A marker left by an older build is reported, not obeyed.** Before the lock is taken, a `.<name>.claim` beside the file is looked for; if present, the writer says so once on the error stream — the marker's path and "delete it; nothing holds it" — then claims or refuses as it otherwise would. Once per writer, not per run: `setup` builds a second writer and may say it twice. During an upgrade an older build still obeys the marker while a new one holds the lock; that window is accepted.
- **A job that meets the refusal fails that attempt by name.** A harvest whose meeting-record write finds `meetings/` claimed records a failure saying so — retryable, the harvest position unchanged, naming the record it stopped at and that earlier records were written and will be rewritten — before the refusal is raised on, so the scheduled harvest (`9a`) has a record to back off from. Only the busy refusal is recorded this way; a displaced or malformed record keeps today's behaviour.
- **Commands keep their shape.** `connector add`, `project add`, `setup` and `connector check` refuse a busy file by name and exit as today; `project add` and `setup` now also refuse an operating-system fault on the sidecar by name rather than crashing; a rotated token still waits for the file and is still saved.
- **The lock on an encrypted file lives inside its owner-only directory**, which the claim creates owner-only when it is missing and leaves alone when it exists.
- **A kill between staging and publishing** leaves the staged `.part` file to the existing cleanup rule; the lock changes nothing there.

**Ask First:** Nothing.

**Never:**
- No change to what is claimed or when: enrolment, replacement, the sign-in store, `meetings/` and the project list claim exactly the files they claim today, for the reads and writes they do today.
- No change to the busy-credentials verdict (`9a` owns it), no scheduler, no daemon-held lock (`4e` decides that), no `pm-ai doctor` probe for stale markers.
- No Windows port of the lock; the port's shape is what makes one possible later.
- No waiting added anywhere, and no re-entrancy: a caller that holds a claim and asks for it again is still refused.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Second claim, same file | one holder; another asks | refused at once; the holder is unaffected | `ArtifactBusy` naming artifact and scope; no file to delete named |
| Two doors, one file | the platform lock held directly on the resolved path; onboarding runs through the façade | onboarding is refused; project list unchanged | `ArtifactBusy` |
| Two spellings | the same file reached through a symlinked and a plain path | one lock; the second claim refused | `ArtifactBusy` |
| Holder killed | a child process holds `config.json` through the façade and is killed with SIGKILL | the next claim succeeds; the sidecar is still there | N/A |
| Sidecar already present | empty `<name>.lock` exists, nobody holds it | claim succeeds | N/A |
| Release on exit | body ends, normally or by raising | lock released; sidecar kept; the next claim succeeds | the body's own error, unchanged |
| Inner claim refused | `config.json` claimed inside a `meetings/` claim, and busy | the refusal names `config.json`, not `meetings/` | `ArtifactBusy` for the inner file |
| Older build's marker | `.config.json.claim` beside the file | one error-stream line names the marker and says to delete it, whether the claim then succeeds or is refused; once per writer | N/A |
| No `flock` on the filesystem | `ENOTSUP`/`EOPNOTSUPP` | refused naming the mount; nothing written | `OSError`, named |
| Permission fault | `EACCES` opening or locking the sidecar | refused as a permission fault, not busy; the sign-in store reports custody | `OSError`, unchanged |
| Sidecar is not a plain file | a directory, a symlink (`O_NOFOLLOW`) or unreadable | refused as the operating system's fault | `OSError`, named |
| `project add`, registry held | another process holds `projects.toml` | refused naming `projects.toml` and the application scope; exit 3 | `ArtifactBusy`; `OSError` on the sidecar refused by name too |
| `setup` step 2, registry held | as above, during setup | "setup stopped at step 2 (project); step 1 stays done" plus the sentence above | as above |
| `connector add`, credentials held | another process holds `config.json` | refused with the sentence: artifact, scope, "nothing was changed", no file to delete; exit 3 | `ArtifactBusy` |
| Credentials file, sign-in store | file busy, then freed within the wait | token read or saved after waiting, as today | N/A |
| Credentials file, past the wait | busy for the whole wait | reported busy, not as a bug; no marker named; today's redo-sign-in remedy after a rotation | `StoreBusy` → `CredentialStoreBusy`, as today |
| Harvest meets a claimed `meetings/` | another process holds it at record N | failure recorded: `meetings/`, the scope, record N, "earlier records written and will be rewritten"; retryable; position unchanged; the refusal then raised on | `ArtifactBusy`; if the recording itself fails, that is logged and the original refusal raised |

</frozen-after-approval>

## Code Map

Verified against `ddcb9aa` on 2026-10-10.

- `pm_ai/platform/claims.py:46-75` -- `exclusive(guarded)`: `flock` on `<name>.lock` (`LOCK_SUFFIX` at 37), never unlinked; `ClaimHeld` on `EAGAIN`/`EACCES` (63) — `EACCES` must stop being "busy"; other `OSError` propagated; `os.open` at 58 gains `O_NOFOLLOW`; `mkdir` at 56 runs at the umask, so the façade creates an enclave first. Gains `FlockClaims` implementing the port; canonicalises (`Path.resolve`) before naming the sidecar
- `pm_ai/domain/claims.py:20` -- `ClaimHeld`; its docstring (3-8) says the CLI names it, which stops being true. `LOCK_SUFFIX` moves here (platform re-exports)
- `pm_ai/ports/__init__.py:160-174` `ArtifactBusy` (names a file to remove); `:689-704` `StoragePort.exclusive`; `:314` `VcsPort` — the shape to copy for `ClaimPort` (`exclusive(guarded: Path) -> AbstractContextManager[None]`, raises `ClaimHeld`)
- `pm_ai/storage/service.py:1301-1354` -- `StorageService.exclusive`: enclave or `mkdir` (1336-1339), marker (1340-1349), `unlink` (1354) → the façade in Design Notes; `:541-548` `__init__` gains `claims: ClaimPort`; `:217` `_mkdir_enclave`; `:1024-1027` the staged-file cleanup rule ("only a kill can orphan one")
- `pm_ai/app/wiring.py:89` platform import; `:1612` `with exclusive(known.project_registry)` in `onboard_project` (1537) → `storage.exclusive(scope=APPLICATION, artifact=REGISTRY_ARTIFACT)`; `:535` the production `StorageService(...)` in `_writer` (522); `:1192-1196` `CLAIM_WAIT_ATTEMPTS`/`CLAIM_WAIT_INTERVAL`; `:1270-1316` `SealedRefreshTokenStore.exclusive` — `storage.exclusive` 1292, `except ArtifactBusy` 1299, `except OSError` 1301 — unchanged in logic
- `pm_ai/core/connector_enrolment.py:377`, `:453` -- the two `config.json` claims; unchanged
- `pm_ai/core/meeting_records.py:489-494`, `:504` -- `put`'s claim; docstring says the refusal names the claim file; `MalformedMeeting` 224, `MeetingDisplaced` 237 — not recorded by `run_harvest`
- `pm_ai/core/project_scaffold.py:28-36` `project_rules()` from `GITIGNORED[PROJECT]`; gains the sidecar rule from `LOCK_SUFFIX`; `pm_ai/domain/scope_model.py:440` `projects.toml`; `:1011` `ENCRYPTION` is derived from the trees, so the sidecar is declared as structure, never as a `File`
- `pm_ai/app/pipelines.py:114-115` -- `run_harvest`'s `put` loop, no `except`; `:135` `save_cursor`; `pm_ai/domain/harvest.py:127` `HarvestFailure(reason, retryable)`
- `pm_ai/connectors/graph/auth.py:261-267` `CredentialStoreBusy` docstring ("left its claim file behind"); `:293-298` `StoreBusy`; `:972-980` the health remedy's "claim file named above … can be removed"; `:1328-1333` `StoreBusy` → `CredentialStoreBusy`
- `pm_ai/surfaces/cli/dispatch.py:59` `ClaimHeld` import; the `ArtifactBusy` sites, each keeping its refusal shape: `:961` connector add (`Refusal(str(claimed))`), `:1180` project add (`ClaimHeld` → `ArtifactBusy`; gains `OSError`), `:1309` setup step 2 (`ClaimHeld` → `ArtifactBusy`; `OSError` already at 1313), `:1434` setup config, `:1568` goal set
- `.importlinter:160-170` `surfaces-through-core` — `pm_ai.platform` joins `forbidden_modules`
- Tests: `tests/architecture/test_storage_capabilities.py:281-345` (`_claim_for` 281, `.claim` assertion 296, release tests 302/309 assert the marker gone, enclave test 326 — not 320, which is the two-artifacts test); `tests/slice/test_project_onboarding.py:28-30`, `:347-358`; `tests/slice/test_rotated_tokens_are_kept.py:278-282` `LEFT_OVER`, `:306-326`, `:329-348`, `:394-411`, `:599-611`; `tests/core/test_connector_enrolment.py:900`; `tests/core/test_meeting_records.py:975`; `tests/architecture/test_static_rules.py:147-158`, `:193-198`; `tests/surfaces/test_cli_subcommands.py` (command-boundary rows)
- Thirteen files construct `StorageService(...)` directly (`grep -rn 'StorageService(' pm_ai tests`)
- `_bmad-output/implementation-artifacts/deferred-work.md:436`; `stories/9a-scheduled-harvest.md:49`, `:54-55`, `:87-88`

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/claims.py` -- `LOCK_SUFFIX` moves in; `ClaimHeld` docstring: the platform spelling storage translates, caught by no caller
- [ ] `pm_ai/ports/__init__.py` -- `ClaimPort` beside `VcsPort`; `ArtifactBusy` and `StoragePort.exclusive` docstrings stop promising a file to delete
- [ ] `pm_ai/platform/claims.py` -- `FlockClaims`; canonicalise before naming the sidecar; `O_NOFOLLOW`; `EACCES` propagates as `OSError`; `ENOTSUP`/`EOPNOTSUPP` raised as an `OSError` naming the mount; `LOCK_SUFFIX` re-exported
- [ ] `pm_ai/storage/service.py` -- `__init__` takes `claims: ClaimPort`; `exclusive` per Design Notes: marker check first (once per writer, a set on the instance), enclave, the port's claim, `ClaimHeld` → `ArtifactBusy` at this enter only
- [ ] `pm_ai/domain/scope_model.py`, `pm_ai/core/project_scaffold.py` -- declare the `.lock` sidecar as structure outside the tier model, naming `LOCK_SUFFIX`; `project_rules()` derives its ignore rule -- closes the undeclared-sidecar entry
- [ ] `pm_ai/app/wiring.py` -- `_writer` passes `FlockClaims()`; `onboard_project` claims through `storage.exclusive`; the platform import goes
- [ ] `pm_ai/app/pipelines.py` -- `run_harvest`: `except ArtifactBusy` around the `put` loop only; `save_cursor` with the loaded position, no coverage, `HarvestFailure(retryable=True)` naming `meetings/`, the scope, record N and the earlier-records sentence; a failure of that save is logged and the original refusal raised; then raise
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- `:1180` and `:1309` catch `ArtifactBusy`; `:1180` gains `except OSError` in the shape of 1313; the `ClaimHeld` import goes
- [ ] `.importlinter` -- `pm_ai.platform` added to `surfaces-through-core`'s forbidden modules
- [ ] `pm_ai/core/meeting_records.py`, `pm_ai/connectors/graph/auth.py` -- docstrings and the health remedy stop naming a claim file to remove
- [ ] `tests/architecture/test_storage_capabilities.py` -- one test per storage row, `FlockClaims()` under test: refusal names no file; release keeps the sidecar; pre-existing sidecar; two spellings via a symlinked `tmp_path`; inner refusal names the inner file; marker line asserts the marker path and "delete" in captured stderr, before a refused claim too, once per writer and again from a second writer; `ENOTSUP`, `EACCES` and a directory sidecar through a `ClaimPort` fake raising them; enclave mode. Kill test: the child runs `sys.executable`, builds `StorageService(ScopePaths.rooted(tmp_path), …, claims=FlockClaims())`, holds `exclusive(scope=APPLICATION, artifact="config.json")`, prints `held`; the parent kills it with SIGKILL only after reading that line, then claims and succeeds
- [ ] `tests/` -- an in-memory `ClaimPort` fake for every `StorageService(...)` not testing the lock (thirteen files), so a filesystem without `flock` fails only the lock tests
- [ ] `tests/slice/test_project_onboarding.py` -- the held-claim test holds the platform lock directly on the resolved registry path; onboarding through the façade is refused with `ArtifactBusy`
- [ ] `tests/surfaces/test_cli_subcommands.py` -- `project add` and `setup` step 2 with a held registry, and an `OSError` on the sidecar; `connector add` with held credentials asserting the exact sentence
- [ ] `tests/slice/` -- `run_harvest` with `meetings/` held at record 2 of 3: the failure row names `meetings/`, the scope, the record, the earlier-records sentence; retryable; position unchanged; `ArtifactBusy` raised; a `save_cursor` that raises still re-raises the refusal
- [ ] `tests/slice/test_rotated_tokens_are_kept.py`, `tests/core/test_connector_enrolment.py`, `tests/core/test_meeting_records.py` -- drop the `.config.json.claim` and `LEFT_OVER` assertions; otherwise unchanged and green
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark `:436` resolved; add that `9a`'s `run_harvest` `try/except` must treat `ArtifactBusy` as the already-recorded retryable failure

**Acceptance Criteria:**
- Given one process holding `config.json` through the sign-in store, when `pm-ai connector add` runs in another, then it is refused naming `config.json` and the application scope, says nothing was changed, names no file to delete, and exits 3.
- Given a child process holding `config.json` through the façade (constructed as the kill test says) that is killed with SIGKILL after printing `held`, when the parent claims `config.json` through the façade, then it succeeds and the sidecar is still present.
- Given a `.config.json.claim` left by an earlier build, when a writer claims `config.json`, then it proceeds, one error-stream line names the marker and says to delete it, and the marker is still there.
- Given `uv run lint-imports`, then every contract passes with `pm_ai.platform` forbidden to `pm_ai.surfaces`; and the grep in Verification finds no `pm_ai.domain.claims` import under `pm_ai/surfaces/`.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-10).

## Design Notes

The façade, in outline. The marker check comes first so a refused claim still reports it; the enclave step precedes the port because the platform lock's own `mkdir` runs at the umask:

```python
target = self._paths.resolve(scope, artifact)
self._report_older_build_marker(target)      # stderr, once per writer per path
_mkdir_enclave(target.parent) if is_encrypted(str(target)) else target.parent.mkdir(parents=True, exist_ok=True)
try:
    claimed = self._claims.exclusive(target)
    claimed.__enter__()                       # only this enter is translated
except ClaimHeld as held:
    raise ArtifactBusy(f"{artifact} in {scope} is claimed by another pm-ai process; "
                       f"nothing was changed. Run the command again.") from held
with claimed:                                 # a ClaimHeld from the body is the body's
    yield
```

`flock` is held per open file description, so two claims in one process on two descriptors conflict exactly as two processes do — which is why the in-process tests (an `ExitStack` holding `storage.exclusive` while the store tries) keep working, and why the sign-in store's thread lock stays. `run_harvest` records before it raises because the record is Tier 2, read by `9a`'s backoff and `doctor`, while the exception is what the command at the terminal reports.

## Verification

**Commands:**
- `uv run pytest tests/architecture/test_storage_capabilities.py tests/slice/test_project_onboarding.py tests/slice/test_rotated_tokens_are_kept.py tests/core/test_connector_enrolment.py tests/core/test_meeting_records.py tests/surfaces/test_cli_subcommands.py -q` -- expected: all pass
- `uv run pytest -q` -- expected: all pass; the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-10)
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept, including `pm_ai.platform` forbidden to `pm_ai.surfaces`
- `grep -rn 'pm_ai.domain.claims\|ClaimHeld' pm_ai/surfaces/` -- expected: no output
