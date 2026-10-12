# Rubric review — spine amendment for slice 4e (the daemon), 2026-10-12

Reviewed: the diff to `ARCHITECTURE-SPINE.md` (AD-5's interim-writer paragraph, the Enforcement gap row "There is no daemon", the Open Risks row on warning-less writers), against `pm_ai/app/daemon.py`, `pm_ai/surfaces/api/app.py`, `pm_ai/app/entry.py`, `pm_ai/app/wiring.py`, `pm_ai/storage/service.py`, `pm_ai/platform/doctor.py`, and the slice spec `stories/4e-daemon-and-loopback-api.md`. Read-only review; nothing edited.

## Verdict

**Pass with amendments.** Every sentence the amendment writes about the daemon is true of the code. But it misses two real divergence points: what keeps the daemon single, and a status handler that blocks the loop. It also leaves one stale "4e" sentence elsewhere in the spine, and points the sole-writer cutover at a story whose work-queue entry does not include it.

## Checklist

| Check | Result |
| --- | --- |
| Fixes the real divergence points and misses none | **Partial.** It settles AD-5's open question (the daemon keeps the claim). It misses singleness (F1), loop blocking on the request path (F2) and the loopback client's home (F5) |
| Every amended Rule is enforceable and prevents its divergence | **Yes for AD-5.** "Takes the same claim for any whole-file read-modify-write" is enforceable through the one door (`StorageService.exclusive`, `holds_claim` on `claimed=True`). The busy-database clause is enforced in code (`_OperationalConnection`, `OperationalStoreBusy`) |
| Nothing deferred could let two units diverge | **No.** The cutover to "the daemon as sole writer" is deferred to "story 10", and `stories.yaml` story 10 ("Durable job queue and offline replay") does not carry it (F4). Singleness is left implicit (F1) |
| Ratifies rather than contradicts the code | **Yes.** No contradiction found |
| No amended text weakens or contradicts another AD | **Mostly.** The amendment adds no contradiction of its own. It ratifies a handler shape that runs into AD-19/AD-21 (F2), uses "thin client" for processes AD-7 says hold no state (F4), and leaves AD-5's Rule sentence "SQLite … with the storage service as sole writer" standing against two live SQLite writers (pre-existing; noted) |
| Every statement about what the daemon does is true | **Yes, with one incomplete list** (F6) |

## Statements verified true

- Binds `127.0.0.1` only, on `daemon_port`, default 8747 — `daemon.py` `LOOPBACK`, `bind_loopback`; `core/config.py` `daemon_port`.
- Token file `~/.pm-ai/private/api_token`, `0600` — `RESTRICTED_FILE_MODE = 0o600` (`domain/storage_tiers.py:196`). Minted by exclusive create (`create_api_token` → `write_artifact(..., exclusive=True)`). A file with any group or other bit is refused (`check_api_token`, `& 0o077`).
- Token required on every request — `AuthenticatedApp.__call__` checks before routing for `http` and every non-lifespan scope. `hmac.compare_digest` runs over SHA-256 digests.
- Serves `GET /v1/status` only. No SSE, no launchd — true; the gap row names both as later work.
- "its status endpoint only reads" — `_status_probes` → `artifact_state` → `read_artifact` (no claim); `run_all` probes write nothing (`sqlite_extension_support` uses `:memory:`).
- "its one handler runs on the loop's thread and takes no claim" — `read_status` is `async def`, so FastAPI runs it on the loop and never dispatches it to a thread; uvicorn runs on the main thread.
- Request log goes to `logs/` through the storage service — `StorageService.diagnostic_log` (`RotatingFileHandler`, 5 MiB × 3).
- Five-second standard-library busy wait, no pm-ai timeout, WAL; a write that waits it out is refused as `OperationalStoreBusy` — `service.py:735-755`, `:480-560`.
- "owns one writer for its lifetime and prints the warning once, at start" — the enrolled path composes once in `_compose` → `build` (warning at `wiring.py:380`). The unenrolled path's `_compose` builds nothing, then `_serve_unenrolled` calls `build` once. One print either way.
- Shutdown closes the writer — `serve`'s `finally` calls `daemon.storage.close()`.

## Findings

### F1 — Only the port keeps the daemon single, and the spine never says so (missed divergence point; medium)

`daemon.py`'s docstring says the port is "what keeps the daemon single". The spine says only "Single daemon instance" (Deployment) and "A single daemon" (AD-7), and names no mechanism. A second `pm-ai daemon run` started after `daemon_port` was edited binds the new port and runs beside the first. Today both are writers under AD-5's claim, so the damage is bounded. But once `9a` attaches timers in `_lifespan`, the two daemons schedule twice, and their `RotatingFileHandler`s both rotate the same `logs/daemon.log`. Until the mechanism is in the spine, `9a` and the launchd slice have no stated rule to build against.

**Suggested text (AD-7, appended to the Rule):**
> **One daemon is enforced by its port** `[2026-10-12, slice 4e]`: a second `pm-ai daemon run` meets the first one's listener on `daemon_port` and is refused before it touches the token. That holds only while both read the same `daemon_port`. A daemon started after the key changed runs beside the first, so before `9a` attaches any timer, the daemon must hold a per-user exclusive claim for its lifetime that does not depend on the port. Until then, two daemons are two writers under AD-5's claim and nothing more.

### F2 — The status handler blocks the loop and can exceed AD-21's 5 s (missed divergence point; medium)

`GET /v1/status` calls `run_all` synchronously inside an `async` handler, on the loop thread. Two of its probes are slow:

- `keychain_reachable`, a `keychain.fetch`. AD-5 calls this fetch "slow or interactive".
- `git_available`, a subprocess bounded at `GIT_TIMEOUT_SECONDS = 10`.

Everything through the daemon API is on AD-21's request path, so the worst case breaks the 5-second inline bound, and it blocks the one loop AD-19 reserves for I/O. It also meets the other unit: `doctor`'s daemon probe waits 2 s, so a healthy daemon stuck in a slow `git` is reported "not answering". The amendment records "runs on the loop's thread" as a strength for the claim guard and does not mention this cost.

**Suggested text (new Enforcement gap row, or a sentence after AD-5's "the daemon keeps it" clause):**
> `GET /v1/status` answers inline on the loop thread and runs `run_all` there, including the keychain fetch and a `git` subprocess bounded at 10 s. Its worst case exceeds AD-21's 5 s and blocks AD-19's one loop, and `doctor`'s 2-second probe would then report a running daemon as not answering. Accepted for `4e` only because nothing else shares the loop yet. `4r` runs the probes off the loop (reads only, no claim) before `9a` attaches anything to it.

### F3 — A stale "4e" sentence in Open Risks (missed edit; low, but now false)

Open Risks, "A harvest batch is one unbounded write", still says: "Harvest page caps bound it in practice and the daemon (`4e`) makes the question moot". After 4e the thin clients still harvest (`pm-ai dashboard`), and the daemon does not serialize appends. The question stays open until one writer owns harvest.

**Suggested text:**
> Harvest page caps bound it in practice, and the daemon becoming the sole writer (the cutover AD-5 defers past `4e`) makes the question moot. Until then, a batch above 1 MiB is unmeasured.

### F4 — The sole-writer cutover points at a story that does not carry it (deferred item could diverge; medium)

AD-5, the gap row and Open Risks all say "until story 10 makes the daemon the sole writer with the CLI going through it". In `stories.yaml`, story 10 is "Durable job queue and offline replay" (CAP-4), and its description says nothing about routing CLI writes through the daemon. The WAVE 2 note assigns `4q` only the REPL as a client. So the step that restores AD-5's Rule ("a single storage service inside the daemon owns every write") is not in the work queue, and each later slice can assume someone else owns it.

A related wording problem: the amendment calls `dashboard`, `connector add` and the others "thin clients" while they write directly, but AD-7 defines a thin client as one that "holds no state".

**Suggested text (AD-5):**
> … while the commands that still write for themselves (`dashboard`, `connector add`, …) keep writing directly, until the cutover that makes the daemon the sole writer. That cutover is assigned to story 10 and must be recorded in its `stories.yaml` description (through `bmad-spec`); until it is, it has no owner.

Also replace "the thin clients" with "the commands that still write" in the amended AD-5 sentence and in the gap row.

### F5 — The loopback client has no declared home or class (missed divergence point; low)

`doctor`'s daemon probe (`pm_ai.platform.doctor._exchange`) speaks HTTP over a raw socket and parses with `http.client`. `4q` will need a second client, in `surfaces`. AD-1 classifies all traffic (M/H/F/S/L) and has no row for loopback to the daemon. The `http-confined-to-adapters` contract forbids `httpx`/`requests`/`aiohttp`/`msal` but not the standard library, so nothing confines this path to `127.0.0.1:daemon_port`. The claim "loopback is not egress" exists only in the slice spec.

**Suggested text (AD-8, appended):**
> Traffic to the daemon's own `127.0.0.1:daemon_port` is not egress under AD-1. It has one client, the exchange in `pm_ai.platform.doctor`, which `4q` reuses rather than writing a second. It connects only to the loopback address and the configured port.

### F6 — The list of the daemon's writes leaves out `operational.db` (accuracy; low)

AD-5 says "it writes its token by exclusive create and its request log to `logs/`". Composition also opens `operational.db`, which sets WAL mode, runs `CREATE … IF NOT EXISTS` and runs migrations. Those are writes, and they are where the daemon can meet a thin client in this slice. The next sentence covers the meeting, so this list is incomplete rather than false.

**Suggested text:**
> … it writes its token by exclusive create, its request log to `logs/`, and `operational.db` only on opening it (WAL mode, schema, migrations — as every invocation does), and its status endpoint only reads …

### Note — AD-5's Rule sentence (pre-existing, not introduced here)

"SQLite runs in WAL mode with the storage service as sole writer" stands unqualified while the amended paragraph describes two live SQLite writers. The paragraph's "still the rule" framing makes the Rule read as the target and the paragraph as the interim, which is acceptable. A one-clause pointer from the Rule to the interim paragraph would stop a reader taking the Rule as current fact.
