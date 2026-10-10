---
title: 'A long-lived daemon answers on loopback'
type: 'feature'
created: '2026-10-10'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** pm-ai has no long-running process. Every command builds everything from scratch, does its one job and exits, so nothing can answer a REPL (`4f`) or a phone message, and nothing can take work a command should not wait for. The architecture promises one daemon that every other pm-ai process talks to over a local-only, authenticated connection (AD-7, AD-8); today that is a one-line docstring, and what keeps two commands from writing the same file at once is a cross-process file lock (AD-5, as amended 2026-10-09; made one lock by `1r`, which lands before this slice).

**Approach:** `pm-ai daemon run` starts the daemon in the foreground. It composes once, owns one writer for as long as it runs, listens on `127.0.0.1` only, requires a per-user token on every request, and reports its health. `pm-ai doctor` says whether one is running. The prompt and job endpoints are `4r`, built on this; the REPL as a client of the daemon is `4q`.

## Boundaries & Constraints

**Always:**
- **Foreground only.** `pm-ai daemon run` runs until Ctrl-C or a stop signal, then exits 0. A closed terminal (the hang-up signal) counts as a stop signal. Starting it at login under launchd is a later slice; the command's help says so.
- **It listens on `127.0.0.1` and nothing else**, on the port `daemon_port` names in `config.toml`, default **8747**. Checked 2026-10-10 against IANA's port registry: 8747 lies inside its "8732-8749 Unassigned" range. `daemon_port` is the fifth key in the settings file's closed vocabulary (a `4g`-style change): a whole number from 1024 to 65535, anything else refused with the loader's usual sentence; `pm-ai config show` lists it; it is written only when set; a settings file pm-ai writes round-trips it as a whole number, never `8747.0`. No `0.0.0.0`, no other interface, asserted by a test.
- **A port already taken refuses the start**, exit 3, naming the port and the key. That is also what keeps the daemon single: a second `daemon run` meets the first one's port, and the port is taken before the token is touched.
- **The token.** At first start the daemon mints a random token and writes it to `~/.pm-ai/private/api_token`, readable by its owner only, declared in the scope model as Tier 3: re-minted whenever absent, gitignored, never a backup target. The write fails if the file already exists, and the daemon then reads it. A file that is empty, whitespace, shorter than 32 characters, or readable by group or others refuses the start, exit 3, naming the file — never minted over. Every request carries the token in the `Authorization: Bearer` header; a request without it, or with any other value, is answered `401` with a body saying only that a valid token is required. The token is never printed, never in a web address, never in any log line; comparison takes the same time however much of it matches.
- **The request log** records method, path and status — never headers, never the part of the address after `?`. It goes to the rotating diagnostic log under `~/.pm-ai/logs/` (AD-24), never to the event log.
- **One endpoint in this slice.** `GET /v1/status` returns what `doctor` reports (each probe's name, state, detail) plus `healthy`, `uptime_seconds` and `started_at` — without the `daemon` probe: this process is the daemon and never sends a request to itself. Streamed answers, when `4r` adds one, travel over this same connection (AD-8).
- **One writer for the daemon's lifetime.** The daemon composes once at start. The encryption-off warning prints once, at that start; the event-log entry is written once, before its first plaintext protected write (AD-6, as `1p` built it). Nothing is composed per request. Background work (`9a`'s timers, `9b`'s render) attaches when the application starts serving, on the daemon's one event loop.
- **The thin clients still write.** In this wave `pm-ai dashboard`, `connector add`, `goal set` and the rest keep writing directly, serialized by `1r`'s one cross-process lock; the daemon is one more writer under the same lock. That answers the question AD-5 left to this slice: the daemon keeps the lock for the thin clients until story 10 makes it the sole writer with the CLI going through it. AD-5's paragraph is amended by this slice to say so.
- **The operational database under two processes.** The daemon and a thin client can meet on `operational.db`. The connection waits up to five seconds for a busy database (the standard library's default; pm-ai sets none of its own) and the database is in write-ahead mode, so readers never wait for the writer. A process that waits out the five seconds is refused by name, never left hanging.
- **`doctor` gains a `daemon` probe.** It reads the token file to ask (an absent file is named, the token never printed) and waits at most two seconds. `OK` when a daemon answers (address, uptime, start time); `OK` when nothing answers on the port — not running is an ordinary state. `WARNING` when something answers but not as pm-ai, because `daemon run` would then be refused; `WARNING` "a pm-ai daemon answers but rejects this token file" on `401`; `WARNING` "not answering" when the port accepts the connection and nothing comes back in two seconds. It speaks with the standard library's HTTP client, the one client the contracts allow in that layer; loopback to pm-ai's own port is not egress (AD-1).
- **Enrolment after the start.** A connector enrolled while the daemon runs is seen by the next status request and by `9a`'s next harvest cycle. A project enrolled while it runs needs a restart in this wave; the start time on `doctor`'s daemon line is how the operator tells.
- **Runs with no project enrolled** (D-4o), watching none; it acts in pm-ai's own scope.
- **Runtime packages are an extra for the install, not for the tests.** `daemon run` without them is refused, exit 3, naming the extra. From this slice on the test suite needs the extra: when it is missing, collection fails naming `uv sync --extra runtime` — never a skip that reads as coverage.
- **Shutdown:** stop listening, close the writer, exit 0.

**Ask First:** Any second settings key. Any change to the status response's fields once `4r` and `4q` build against them.

**Never:**
- No launchd plist, no auto-start, no restart-on-crash — a later slice.
- No prompt endpoint, no jobs, no stream, no worker threads — `4r`.
- No scheduler, no harvest cadence, no file watchers, no Telegram, no model: `9a`, story 10, story 5, story 7.
- No CLI subcommand is rerouted through the daemon; `connector check`'s ten-second inline wait stands (D5).
- No TLS, no second transport, no token in a query string.
- No new dependency beyond the `runtime` extra already declared.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First start | no token file, port free | token minted, owner-readable only; "listening on 127.0.0.1:8747"; runs until stopped | N/A |
| Later start | token file present | the same token is read, none minted | unreadable file → refused, exit 3 |
| Two starts at once | both start together | the port decides: the loser exits 3 naming it before touching the token; a token file found already present is read, never replaced | exit 3 |
| Bad token file | empty, whitespace, 31 characters, or group-readable | refused naming the file; the file is unchanged | exit 3 |
| Port taken | another process on the port | refused, naming port and `daemon_port` | exit 3 |
| Runtime extra missing | `fastapi` not installed | refused, naming the extra | exit 3 |
| No token | request without the header | `401`, body says a valid token is required; log line has no header | N/A |
| Wrong token | header with another value | `401`, same | N/A |
| Status | valid token | `200`: probes without a `daemon` line, `healthy`, `uptime_seconds`, `started_at` | N/A |
| Unknown path | valid token, `/v1/anything?x=1` | `404`; the log line has `/v1/anything` and no `?x=1` | N/A |
| Bind address | the listener is built | bound host is `127.0.0.1`; `0.0.0.0` never appears in the package | test fails otherwise |
| Settings key | `daemon_port = 80`, `= 8747.0`, `= "8747"`, `= true` | each refused with the loader's sentence; `config show` lists `daemon_port` at default | exit 3 |
| Settings round-trip | `Config(daemon_port=9000)` written then read | file says `daemon_port = 9000`; reads back equal | N/A |
| Doctor, up / down / foreign | `pm-ai doctor` | `[ok] daemon: running…` with start time; `[ok] daemon: not running…`; `[warning] daemon: something that is not pm-ai answers…` | N/A |
| Doctor, token rejected | daemon answers `401` | `[warning] daemon: a pm-ai daemon answers but rejects this token file` | N/A |
| Doctor, silent port | the port accepts and never answers | `[warning] daemon: not answering` after two seconds | N/A |
| Doctor, no token file | nothing at `private/api_token` | the line names the missing file | N/A |
| Busy database | a thin client holds a write while the daemon writes | the daemon's write waits up to five seconds; past that, refused by name | named refusal, no hang |
| Encryption off | `PM_AI_DISABLE_ENCRYPTION=1 pm-ai daemon run` | the console warning once, at start; no event-log line until a protected write | N/A |
| Nothing enrolled | fresh home, `daemon run` | starts, watching no project | N/A |
| Stop | Ctrl-C, a stop signal, or the terminal closed | stops listening, closes the writer, exits 0 | N/A |
| Suite without the extra | `starlette` not importable | collection fails naming `uv sync --extra runtime` | no skip |

</frozen-after-approval>

## Code Map

- `pm_ai/surfaces/api/__init__.py` -- the one-line docstring today
- `pm_ai/surfaces/api/app.py` -- new: `create_app(...)`; `tests/architecture/test_domain_invariants.py:394-399` already reads `api.app` and expects `GET /v1/status` → `401`, so `app` is served lazily (module `__getattr__`) and `fastapi` is imported inside the function, as `msal` is
- `pm_ai/core/config.py:195` -- `Config`; its four fields `:211-224`; `ACCEPTED_KEYS` derived at `:319`; readers `:644-704`, per-key dispatch `:351-365`; `_literal` `:472-475` widens every `int` to a float, so the new integer field needs its own reader and render arm
- `pm_ai/domain/scope_model.py:430-468` -- `APPLICATION_TREE`; `logs` at `:452-454` is the diagnostic collection the request log joins; `api_token` is declared beside `operational.db` (`:468`) but as Tier 3; `restricted_mode` (`pm_ai/domain/storage_tiers.py:202`) lands a gitignored declaration at owner-only mode
- `pm_ai/storage/service.py:582` -- `sqlite3.connect(store, check_same_thread=False)` with no `timeout=`, so the standard library's five-second busy wait applies; `:596` `PRAGMA journal_mode=WAL`; `write_artifact` `:1160`; `read_artifact` `:1205`; `exclusive` `:1302`
- `pm_ai/platform/claims.py:46` -- `exclusive()`, the lock `1r` makes the one primitive
- `pm_ai/app/wiring.py:118-181` -- `Daemon` (`clock` `:178`, `config` `:181`); `build` `:223`; `_writer` `:522`, where the encryption-off warning lives
- `pm_ai/app/entry.py:122` -- `main`; injections `:147-185` (`dashboard=` `:177`); `_compose` `:858`; `_diagnose` `:985` appends probes to `run_all`'s report — the `daemon` probe is appended here, so `/v1/status`, which calls `run_all` directly, never carries it
- `pm_ai/surfaces/cli/dispatch.py:107-125` -- exit codes; `Context` `:366` (`dashboard` `:429`); `require_daemon` `:517`; `Command` `:547`; `_application` `:1069`; `TABLE` `:1583`
- `pm_ai/platform/doctor.py:497` -- `run_all`; `git_available` `:294` as the probe pattern; `Probe`/`Report` in `pm_ai/domain/health.py:58-85`
- `tests/conftest.py:38-41` -- the rule that no skip may depend on the environment; `:58-60` why skip counts are stated as deltas
- `tests/surfaces/test_cli_dispatch.py:130` -- a docstring saying CAP-18's REPL is `4e`; it is `4f`
- `.importlinter` -- `http-confined-to-adapters` forbids `httpx`/`requests`/`aiohttp`/`msal` in `surfaces`, `storage`, `platform`; the standard library's `http.client` is not named
- `pyproject.toml:39-40` -- `fastapi==0.141.1`, `uvicorn==0.52.3` in the `runtime` extra; `:133` the mypy overrides for lazily-imported extras
- `tests/architecture/README.md:62-68` -- coverage rows for AD-2, AD-7, AD-8

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/config.py` -- add `daemon_port: int = 8747`; an integer reader (1024–65535, refuses bool, float and string); render an `int` field as an integer; the vocabulary and `config show` follow from the dataclass -- the port is the only new setting
- [ ] `pm_ai/domain/scope_model.py` -- declare `private/api_token` (Tier 3, plaintext, gitignored, owner-only mode) -- the scope-model tests refuse an undeclared file
- [ ] `pm_ai/storage/service.py` -- the token file through `write_artifact` / `read_artifact`, the write with `O_EXCL` semantics and the validity check (length, whitespace, mode) on read; nothing else -- AD-5
- [ ] `pm_ai/surfaces/api/app.py` -- `create_app`: bearer check with `hmac.compare_digest`, `/v1/status` from `run_all` without the daemon probe, a request log (method, path, status) to the `logs/` handler; lazy `fastapi` -- the API
- [ ] `pm_ai/app/daemon.py` -- new: bind, then mint or read the token; uvicorn bound to `127.0.0.1`; the start-time stamp; `SIGTERM` and `SIGHUP` handled as Ctrl-C; shutdown order; refuses a taken port, a bad token file and a missing extra by name; the lifespan hook where `9a`/`9b` attach -- the composition root owns the lifecycle (AD-30)
- [ ] `pm_ai/app/entry.py` / `pm_ai/surfaces/cli/dispatch.py` -- `daemon` group with a `run` leaf, application target, help naming the foreground and the port; `serve` injected like `dashboard`; `_diagnose` appends the `daemon` probe -- the command
- [ ] `pm_ai/platform/doctor.py` -- `daemon_reachable(port, token_path)` over `http.client` on loopback, two-second timeout, the states the matrix names -- the probe
- [ ] `tests/conftest.py` -- fail collection, naming `uv sync --extra runtime`, when `starlette` is missing; `AGENTS.md` "Running and verifying" gains the sentence that the suite needs the extra from this slice on -- no environment-dependent skip
- [ ] `tests/` -- one test per matrix row, including the bind-address assertion (no `0.0.0.0` anywhere under `pm_ai/`, and the listener's host is `127.0.0.1`), the request-log test reading `~/.pm-ai/logs/` in the throwaway home, and a writer wrapper that fakes a busy database for the five-second row; `test_ad8_*` now passes; a slice test that starts the daemon on a free port in a throwaway home and reads `/v1/status` with and without the token -- the matrix is the contract
- [ ] `tests/surfaces/test_cli_dispatch.py:130` -- the docstring names `4f`, not `4e`
- [ ] `tests/architecture/README.md` -- AD-2 row gains the bind test; AD-8 row no longer skips
- [ ] `_bmad-output/planning-artifacts/architecture/…/ARCHITECTURE-SPINE.md` -- AD-5's paragraph: `1r` landed first; the daemon keeps the one lock and is one more writer under it for the thin clients until story 10; the enforcement-gap row "There is no daemon…" closes; the Open Risks row on the two warning-less writers narrows to `setup` and `project add` -- through the architecture skill, not by hand

**Acceptance Criteria:**
- Given a throwaway home with no project enrolled and the runtime extra installed, when `pm-ai daemon run` starts and `pm-ai doctor` runs in another shell, then doctor prints `[ok] daemon: running` with the address and start time, and `pm-ai daemon run` a second time is refused naming the port.
- Given the running daemon, when any process on the machine requests `/v1/status?debug=1` without the token, then it receives `401` and the line in `~/.pm-ai/logs/` shows `GET /v1/status 401` and nothing after the path.
- Given a token file of 16 characters, when `pm-ai daemon run` starts, then it exits 3 naming the file and the file is unchanged.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count falls by one against the count recorded before this slice: the AD-8 check runs.

## Design Notes

- **The lazily-served `app`.** The architecture test does `TestClient(api.app)` with nothing composed, so the module-level `app` is built with no token and no daemon: every request is `401`.
- **Why the probe uses the standard library.** The probe, and later `4q`'s client, speak HTTP from layers the import contracts forbid `httpx` in. `http.client` keeps the contracts untouched; `4q` reuses the same small client.
- **Integer rendering.** `_literal` widens every `int` to a float so `85` and `85.0` render alike for the hourly rate. A port renders as `8747` and its reader refuses `8747.0`, so the round-trip rule holds for the new field on its own terms.
- **Threads and the writer.** `check_same_thread=False` at `service.py:582` lets another thread use the connection; it serializes nothing. This slice has one thread touching the writer. `4r` adds the one executor and the daemon-level lock every worker takes before calling the writer; `9a` and `9b` submit there; nothing else creates threads.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass, one skip fewer than the count recorded before this slice
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
- `HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1 .venv/bin/pm-ai daemon run` in one shell and `… pm-ai doctor` in another -- expected: `[ok] daemon: running`, the token file at owner-only mode (`ls -l`), `lsof -nP -iTCP:8747` showing `127.0.0.1` only, a `GET /v1/status 200` line under `<scratch>/.pm-ai/logs/`
