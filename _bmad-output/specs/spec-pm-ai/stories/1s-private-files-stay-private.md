---
title: 'Private files stay private'
type: 'bugfix'
created: '2026-10-16'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Other people logged in to the same Mac can read what pm-ai keeps about its user. Measured on a real machine during the field test (findings 3, 8 and 14 in `_bmad-output/implementation-artifacts/field-test-2026-10-15.md`, which lands with PR #71): the personal dashboard, which lists the day's meetings, was `0644` and its folders under `~/.manager-ai` `0755`, while the project copy of the same page was `0600`; an existing `~/.pm-ai` stayed `0755`; `connectors/`, `logs/`, `transcripts/` and its `temp/` staging folder were `0755`; `daemon.log`, `config.toml` and `projects.toml` were `0644`; and `operational.db` with its `-wal` and `-shm` side files was `0644` (recorded in 9a's deferred work). The architecture already says what it should be — AD-6: "plaintext at `0600` inside `0700`" — but the code ties a file's mode to "kept out of version control", which the personal scope is not; folders are made `0700` only around the two encrypted artifacts (new ones, plus the existing folder directly above an encrypted file) and for the daemon's lock folder; and nothing looks at a home that already exists.

**Approach:** Say which files are private by where they live, not by whether git may see them: everything under `~/.pm-ai` and `~/.manager-ai`, and the machine-local parts of each project (`.project-ai/memory/` and `.project-ai/transcripts/`), is private — files `0600`, folders `0700`. Every way pm-ai creates a file or folder there creates it that way, SQLite's and the log's own files included. Each daemon start then tightens what is already on disk in those places, logs every change, and prints one line saying what it did.

## Boundaries & Constraints

**Always:**
- **Private means:** under `~/.pm-ai` (the application scope, with the team-member scope inside it) and under `~/.manager-ai` (the personal scope), every file is `0600` and every folder `0700`, the two roots included. New files and folders pm-ai creates are made that way; the start-up walk tightens everything else found inside the two homes too, files the person put there (a `.git` folder in `~/.manager-ai`, notes, scripts) included. In a project, only `.project-ai/memory/` and `.project-ai/transcripts/` and everything inside them.
- **`config.toml` and `projects.toml` are private.** They hold no secret, but `projects.toml` names the path of every repository the person manages and `config.toml` their hourly rate and time zone; no other account on the machine needs either, and pm-ai reads them as their owner. One rule for the whole home is also the rule a person can check with one `ls -l`.
- **The project's shared parts stay as the team's tools expect:** `.project-ai/` itself, `rules/`, `skills/` and the repository's `.gitignore` keep the operating system's default (the umask), are never created through the private-folder rule, and are never walked. They travel through the repository, and `0600` on a file git checks out for teammates would only break a shared checkout. This narrows AD-6's "everything else on disk", which the spine is amended to say.
- **Created at the mode, never widened then narrowed.** A new private file is created with `0600` as its creation mode and the mode set again on the open file, as the whole-file writer and the append writer already do, so it never exists wider and a umask that strips owner bits cannot lock pm-ai out of it. A new private folder, and every missing folder above it up to its scope's root, is created at `0700` and then set to `0700` again, since a umask of `200` would otherwise leave it `0500`. In a project, `.project-ai/` and the repository itself are created at the umask, as today.
- **Every way pm-ai creates a file or folder follows the rule:** whole files written to a hidden temporary name and then renamed into place (the dashboard, `config.toml`, `projects.toml`, connector rows, goals, the API token) and their temporary files; ledgers that are appended to (event-log segments, `disclosure.md`), the personal event log included; transcript captures and their `temp/` staging; the empty lock files pm-ai takes a lock on beside a file it is about to rewrite (`<name>.lock`) and the daemon's lifetime lock, each set to `0600` when created, with the folders made for them; project onboarding's private folders; the `logs/` folder and the disclosure ledger's folder; `operational.db`, created at `0600` before SQLite opens it, so SQLite gives its `-wal` and `-shm` files the same mode; and `logs/daemon.log` with its rotated copies, created at `0600` each time the log rolls over.
- **Each daemon start tightens what already exists**, after it holds its lifetime lock and before its writer thread starts: the two home roots and everything inside them, and the private folders of every project the daemon loaded at start. A registered project repository that sits inside one of the homes is skipped by the home's walk, except for its own private folders. Tightening sets each mode to the old mode with the group and other bits removed and nothing else — owner bits, the executable bit and the setuid, setgid and sticky bits are kept — so it never makes a file less usable to its owner. It touches no file's content and no store row: it is not a write in AD-5's sense and does not run on the writer thread (the spine is amended to say so). A thin client writing at the same moment loses nothing: a new file is already private, and a path that disappears mid-walk is skipped.
- **Every change is logged**: one line in `daemon.log` per path changed, naming its root (`application`, `personal`, or the project id) and the path relative to it, with the old and the new mode. Then one line on the console where the daemon runs, when anything was changed or refused: how many folders and files were tightened, how many could not be, and that `daemon.log` names each.
- **Tightening never stops the daemon.** A path it may not change (another owner, a read-only volume), a folder it cannot read, and a mode the filesystem does not keep (found by reading the mode back after the change, as on exFAT or a network share) are logged with the reason as "not tightened", not counted as changed, and the walk goes on; such a path is reported again at every start. A root that is missing or whose repository is not mounted is skipped, never created. Symbolic links inside a root are neither followed nor changed, and each change is made without following a link, so a path swapped for a link mid-walk changes nothing outside. A root that is itself a link is followed once, as where the person keeps that scope; a link to nothing or to a file is logged and skipped. A file with more than one name (a hard link, which may also be reachable from outside the root) is logged and not changed. Access-control lists are neither read nor changed.

**Ask First:** Making `.project-ai/`, `rules/`, `skills/` or the `.gitignore` private. Tightening anything outside the two homes and the projects' private folders. A `doctor` line reporting a home wider than private.

**Never:**
- No change to which artifacts are encrypted, to any tier, or to which artifacts git must exclude: the `gitignored` declarations keep their own job (asking git first) and stop deciding file modes.
- No mode ever widened, no owner bit removed, no content rewritten to change a mode.
- No setting to turn this off, and no refusal to start or to write because something on disk is too open.
- No tightening by a command other than the daemon's start, and no change to `doctor`'s output.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh machine, umask `022` | no `~/.pm-ai`, no `~/.manager-ai`; setup, a render of both scopes and one harvest run in-process, no daemon started | every file under both roots `0600`, every folder `0700` | N/A |
| Personal dashboard | render of the personal scope | `daily_dashboard.md` `0600`; `memory/` and `~/.manager-ai` `0700` | N/A |
| Project folders | project add, a render, then a transcript drop | `memory/`, `transcripts/`, `transcripts/temp/` `0700`; dashboard and capture `0600`; `.project-ai/`, `rules/`, `skills/`, `.gitignore` at the umask | N/A |
| Capture staging | a transcript drop interrupted after staging | the staged file in `temp/` is `0600` | N/A |
| `config.toml`, `projects.toml` | setup, project add | both `0600` | N/A |
| Connector row, goals, API token | `connector add`, `goal set`, first daemon start | `connectors/<instance>.json`, `strategic_goals.md`, `private/api_token` `0600` | N/A |
| Disclosure ledger | first record | `disclosure.md` `0600`, its folder `0700` | N/A |
| Lock files | a dashboard render; a daemon start | `<name>.lock` and `daemon.lock` `0600` | N/A |
| Operational store | first open on a fresh home | `operational.db` `0600`; `-wal` and `-shm` `0600` while open | N/A |
| Diagnostic log | daemon start; log rolls over | `logs/` `0700`; `daemon.log` and the new file after rollover `0600` | N/A |
| Personal event log | first entry of a month | segment `0600` (was the umask's answer before this slice) | N/A |
| Umask strips owner bits | umask `200` | private files still `0600`, private folders `0700`; pm-ai can write to each again afterwards | N/A |
| Existing wide home | `~/.pm-ai` `0755`, `connectors/` `0755`, `config.toml` `0644`, `operational.db` `0644` | at daemon start: all tightened; one log line each; one console line with the counts | N/A |
| Existing special bits | a `0755` file under `~/.manager-ai/skills/`; a `2775` folder | `0700`; `2700` | N/A |
| Already private | a home created by this slice | nothing changed, nothing logged, no console line | N/A |
| A path pm-ai may not change | a file owned by another account inside a root | "not tightened" with the reason; the console line counts it; the start goes on | permission error, logged |
| Mode not kept | the filesystem reads back the old mode | "not tightened", not counted as changed; again at the next start | logged |
| Unreadable folder | a folder inside a root without read permission | its contents skipped and logged; the walk goes on | logged |
| Link inside a root | `~/.manager-ai/rules/x.md` is a link to a file elsewhere | link and target untouched | N/A |
| Root is a link | `~/.manager-ai` links to a folder elsewhere | that folder and its contents tightened | N/A |
| Root link broken or to a file | `~/.manager-ai` links to nothing | logged and skipped; nothing created | logged |
| Missing root or project | `~/.manager-ai` absent; a project on an unmounted volume | skipped; nothing created | N/A |
| Hard-linked file | a file inside a root with two names | logged, not changed | logged |
| Project inside a home | a registered repository under `~/.manager-ai/work/` | its `rules/`, `skills/`, `.gitignore` untouched; its `memory/`, `transcripts/` tightened | N/A |
| Path removed mid-walk | a staged file renamed away while the walk runs | skipped silently | N/A |
| A project's shared parts | `rules/` `0755`, `.gitignore` `0644` | untouched by the walk | N/A |
| Command without the daemon | `pm-ai dashboard` on a wide home | the files it writes are private; nothing else is tightened | N/A |

</frozen-after-approval>

## Code Map

Verified against `a1182f8` on 2026-10-16.

- `pm_ai/domain/storage_tiers.py:196`, `:206-236` -- `RESTRICTED_FILE_MODE` and `restricted_mode`, which answers `0600` only for an artifact in `GITIGNORED`; its docstring's "tightening `~/.pm-ai` … nobody asked for" is the decision this slice reverses
- `pm_ai/storage/crypto.py:167`, `:174` -- `ENCLAVE_DIR_MODE = 0o700`, `ENCRYPTED_FILE_MODE` (an alias of `RESTRICTED_FILE_MODE`)
- `pm_ai/domain/scope_model.py:433`, `:441`, `:452`, `:455`, `:558`, `:566`, `:754` -- the declarations behind the measured modes: `config.toml`, `projects.toml`, `logs/` and every personal-scope artifact are `gitignored=False`, so `restricted_mode` answers `None`; the project dashboard is `gitignored=True`, hence its `0600`
- `pm_ai/storage/service.py:345-360` -- `_mkdir_enclave`: creates missing folders up to the first that exists at `0700` (masked by the umask, never set again), leaves existing ones alone — the docstring's "existing ones are left alone" becomes "the start-up walk tightens them"; in a project with no `.project-ai/` yet it would create that one at `0700` too
- `pm_ai/storage/service.py:1076-1089` -- the store's folder via `_mkdir_enclave`, then `sqlite3.connect` creates `operational.db` at the umask
- `pm_ai/storage/service.py:1563-1576` -- `_writable_dir`: `resolve(create=True)` makes event-log and capture folders at the umask
- `pm_ai/storage/service.py:1733`, `:315` -- the append writer (`_append`, `_open_for_append`, 1q): the creation-mode pattern, already right once the declared mode is
- `pm_ai/storage/service.py:1825-1859`, `:1868-1950` -- the whole-file writer (`_replace`, `_publish`): for a plaintext file the temporary file's folder is made with `mkdir(parents=True)` at the umask (`:1919`); for an encrypted one the existing folder directly above is set to `0700` (`:1915-1916`); the temporary file takes `mode` and `fchmod` (`:1922`, `:1944`)
- `pm_ai/storage/service.py:2059-2134` -- `write_artifact` passing `restricted_mode(scope.kind, artifact)`
- `pm_ai/storage/service.py:2180-2189`, `:2202-2216` -- `prepare_private_dir`; `diagnostic_log`, a plain `RotatingFileHandler` under `resolve(create=True)`
- `pm_ai/storage/service.py:2435-2439`, `:2540`, `:2675` -- `exclusive`'s parent folder at the umask for plaintext artifacts; the disclosure ledger's folder and the capture staging folder via `resolve(create=True)`
- `pm_ai/platform/claims.py:128-136`, `:215-218` -- the lock file beside a rewritten file and the lifetime lock, both `os.open(…, 0o600)` with no `fchmod`, their parent made by `mkdir(parents=True)` at the umask
- `pm_ai/platform/paths.py:583-600` -- `resolve(create=True)`: `mkdir(parents=True)` at the umask; never creates files
- `pm_ai/app/wiring.py:3309-3311`, `:3469-3471` -- project onboarding creates every project folder through `resolve(create=True)`
- `pm_ai/app/daemon.py:189-221`, `:223-242` -- `_serve`: the port, then `take_lifetime_lock`, then `_serve_locked` starts the writer thread; the tightening goes between the last two. `_announce` (`:517`) is the console line's stream (stdout); `_log` writes to `daemon.log` once `_RequestLog.attach` (`:774`) has run, which `serve` does first
- `tests/architecture/test_storage_capabilities.py:275-291`, `:1013-1018` -- the rows pinning `config.toml` and personal `persona.md` at `None` and the personal event log at `0644`; they flip. `:908-917` `test_a_segment_from_before_1q_keeps_its_mode` stays true (an append changes no mode) and its docstring is reworded to say the start-up walk is what tightens
- `tests/core/test_connector_enrolment.py:145-153` -- `test_the_application_root_mode_is_untouched`: still true of a write (writes never change an existing folder's mode); docstring reworded, since the daemon start now does
- `tests/architecture/test_storage_capabilities.py:61`, `:71` -- the fixture and `_mode` helper; `tests/architecture/test_atomic_writes.py:237-276` the umask-`200` pattern

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/storage_tiers.py` -- `restricted_mode` answers `0600` for every artifact of the application, team-member and personal scopes, and for a project artifact in `GITIGNORED`; a folder counterpart answers `0700` for the same set, from one constant that `ENCLAVE_DIR_MODE` aliases; the docstring says the axis is where the file lives, and why `gitignored` no longer decides it -- one rule, from the trees
- [ ] `pm_ai/storage/service.py` -- one helper creates a private folder and its missing ancestors at `0700` and sets the mode again after each `mkdir`, stopping at the scope's shared part in a project and never used for `rules/` or `skills/`; `_writable_dir`, the temporary-file folder, `exclusive`'s parent, the disclosure ledger's folder, `logs/`, the capture staging folder and the store's folder use it; `operational.db` created at `0600` before `sqlite3.connect`; `diagnostic_log` returns a rotating handler whose files are opened at `0600`, keeping the standard rotation by rename (the full file renamed to `daemon.log.1`, a new `daemon.log` created), which 4w's follow relies on; `_mkdir_enclave`'s docstring reworded; a method that tightens one root as the frozen block says, its `lstat` and change calls injectable so the not-permitted, not-kept and swapped-link rows are testable without another account -- every write path, and the walk
- [ ] `pm_ai/platform/claims.py` -- the lock file and the lifetime lock set to `0600` on the open file after creating them -- under umask `200` they are `0400` today
- [ ] `pm_ai/app/wiring.py` -- project onboarding creates the project's private folders through the storage helper, the shared ones as today -- onboarding was the one writer outside storage
- [ ] `pm_ai/app/daemon.py` -- after the lifetime lock, before the writer thread: tighten both home roots (skipping registered project repositories inside them) and each loaded project's private folders; log each change and refusal with its root; one console line when anything changed or was refused -- the existing-home half
- [ ] `tests/architecture/test_private_modes.py` -- new: one test per matrix row, each under a real process umask restored in `finally`; the fresh-machine row asserts before any daemon start, so the walk cannot hide a write path that creates the wrong mode; SQLite's `-wal` and `-shm` asserted on a real open store; rollover asserted by forcing one; the walk timed on a generated home of a stated number of files, the figure, the count and the date recorded beside the walk method -- the matrix is the contract, and the cost is measured
- [ ] `tests/architecture/test_storage_capabilities.py`, `tests/core/test_connector_enrolment.py` -- flip the `config.toml`, personal `persona.md` and personal event-log rows to `0600`; reword the two docstrings named in the Code Map -- they pin or explain the old rule
- [ ] `_bmad-output/specs/spec-pm-ai/stories/1q-ledger-segment-file-mode.md` -- frozen text edited and a dated Spec Change Log line added, when this slice is built: since 1s the personal event log is private (`0600`), and the daemon's start-up walk tightens existing segments and folders, though an append still changes no existing file's mode -- its frozen text says the personal log stays at the umask and no existing file is changed
- [ ] `_bmad-output/specs/spec-pm-ai/stories/8f-storage-port-capabilities.md` -- frozen text edited and a dated Spec Change Log line added, when this slice is built: since 1s a file's mode is decided by where it lives, not by its `gitignored` declaration; new private folders and their missing parents are made `0700`; and the daemon's start tightens the application root and everything in it -- its frozen text says a restricted file never tightens its parents or `~/.pm-ai`
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark resolved: folder permissions left to the umask (1a), 600 for the whole plaintext set, segments created before 1q, `disclosure.md` from before 1q, `operational.db` 0644 (9a), and the field test's file-mode entry -- what this closes

**Acceptance Criteria:**
- Given a home with the field test's modes, when the daemon starts, then every path in the measured list is private, `daemon.log` holds one line per path changed with its root, old and new mode, the console shows one line with the counts, and a second start changes nothing and prints no such line.
- Given a daemon started on a tightened home, when it serves a dashboard job and a scheduled harvest, then both succeed and every file they wrote is private — the walk broke nothing pm-ai reads or writes.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged from the baseline at the slice's start.

## Spec Change Log

- **2026-10-16, created.** Carries: private by location (both homes whole; a project's `memory/` and `transcripts/` only), not by the `gitignored` declaration; `config.toml` and `projects.toml` private; `.project-ai/`, `rules/`, `skills/`, `.gitignore` left at the umask; `operational.db` pre-created at `0600` so SQLite's side files follow; the log opened at `0600` on every rollover; tightening at every daemon start only, after the lifetime lock and before the writer thread, not a claimed step, group and other bits removed and nothing else, links not followed, refusals logged and never fatal; one log line per change and one console line with counts.
- **2026-10-16, review:** the walk covers everything inside both homes, the person's own files included, and skips registered project repositories inside them except their private folders; folders set to `0700` again after creation (umask `200`); `logs/`, the disclosure ledger's folder and the lock files (`platform/claims.py`) brought under the rule; modes changed as old minus group and other bits, special bits kept, without following links; missing or broken roots skipped, unreadable folders, unkept modes and hard-linked files logged as not tightened; access-control lists out of scope; log lines name their root, the console line also counts refusals; matrix rows for captures and staging, `disclosure.md`, lock files, the API token, a connector row, goals, a linked root, special bits; the fresh-home row asserted before any daemon start; the walk's calls injectable and its cost measured by a task; Intent corrected on where `0700` is applied today; 1q and 8f change-log lines spelled out; two test docstrings reworded; repeated acceptance criteria replaced by a system-level one. Depends on spine amendment: AD-6 — a project's shared parts (`.project-ai/`, `rules/`, `skills/`, `.gitignore`) stay at the umask, narrowing "everything else on disk is plaintext at `0600` inside `0700`". Depends on spine amendment: AD-5 — the daemon's start-up mode walk is not a write in AD-5's sense and runs before the writer thread starts.
- **2026-10-16, review (cross-spec):** the Change Log moved before Design Notes; the diagnostic log's `0600` handler keeps rotation by rename, which 4w's follow relies on; tasks amending done specs say their frozen text is edited, with a dated Change Log line, when this slice is built.

## Design Notes

Why location and not `gitignored`: the two answer different questions. `gitignored` says whether a write must ask git first; the personal scope answers "no" because the person may keep it as their own private repository, and that is exactly why its dashboard landed at `0644`. Who may read a file depends on whose home it is in. The project is the one scope split in two, and the split is the one the scope model already draws: `memory/` and `transcripts/` are this machine's, `rules/` and `skills/` are the team's.

Why every start and not once: a person's editor, a `git pull` in `~/.manager-ai` or a restored backup can bring in a `0644` file at any time; a walk at each start keeps the promise without a migration flag. Its cost is measured by the test task and recorded with the method. A thin client never tightens, so a start is the one moment, and the console line makes it visible.

Why outside the writer thread (choice, review 2026-10-16): AD-5 routes every write and claim through the writer thread so that file content and store rows have one writer. Removing permission bits changes neither, only ever narrows, and cannot conflict with a write in progress, so the walk runs before the writer thread starts rather than delaying its first steps; the spine amendment states that a mode change of this kind is not a write.

Choices made in review (2026-10-16): a mode the filesystem does not keep is reported every start rather than remembered, since remembering it would need a store row; a hard-linked file is left alone because its other name may be outside the home; access-control lists are out of scope because the field test measured only permission bits.

## Verification

**Commands:**
- `uv run pytest tests/architecture/test_private_modes.py tests/architecture/test_storage_capabilities.py tests/core/test_connector_enrolment.py -q` -- expected: all pass
- `uv run pytest -q` -- expected: all pass; the skip count unchanged from the slice's baseline
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks (if no CLI):**
- On a machine with a wave-2 home: `ls -la ~/.pm-ai ~/.pm-ai/* ~/.manager-ai ~/.manager-ai/memory` before and after one `pm-ai daemon run`; every entry `drwx------` or `-rw-------` (or `-rwx------`), and `daemon.log` names each change.
