# Tech-currency review — wave-3 amendments `[2026-10-16]`

Reviewer lens: every committed decision checked against the live web (PyPI, vendor docs), the worktree's `uv.lock`, and the installed package source in `.venv` (Python 3.14). Read-only; nothing installed, no API calls made.

Scope: the Stack rows `httpx2`, `requests`, `prompt_toolkit`, `Ollama`, `Ollama client` (`httpx`); AD-1 class F and class LM; AD-15's tiers; AD-16's no-tool Tool Runner; AD-19's Ollama server limits.

## Verdict

The library pins and most of the behaviour claims hold up against PyPI, the lock and the installed source. There are two real currency problems. First, AD-15 re-asserted `claude-sonnet-5` / `claude-opus-5` on 2026-10-16, three weeks after their successors shipped, and the successors cost the same or less. Second, AD-1 class F's "no environment read" cannot be achieved by passing the key and the base URL, which is all the spine prescribes, because the pinned SDK reads `ANTHROPIC_CUSTOM_HEADERS` unconditionally. Class LM's "remote models refused" has no named mechanism, although Ollama's local server forwards `:cloud` models off the machine.

## Findings

### F1 — AD-15 / Stack "Claude models": the tiers name models that are no longer current (High)

Checked on platform.claude.com on 2026-10-16:

- The **Models overview** lists the current lineup as Fable 5.1, **Opus 5.5 (`claude-opus-5-5`)**, **Sonnet 5.5 (`claude-sonnet-5-5`)** and Haiku 5.5. It lists **Claude Opus 5 and Claude Sonnet 5 under "Legacy models (still available)"**.
- The **Model deprecations** table still shows `claude-opus-5` (not before 2027-07-24) and `claude-sonnet-5` (not before 2027-06-30) as Active, so nothing breaks. But Anthropic's recommended replacement for every retired Sonnet is now `claude-sonnet-5-5`.
- **Pricing:** Sonnet 5.5 costs **$2/$10**, the same as Sonnet 5. Its cache hits cost 0.05x the input price, where Sonnet 5's cost 0.1x. Opus 5.5 costs **$4/$20**, against **$5/$25** for Opus 5. Moving to the newer models costs nothing extra and lowers the price of the Opus tier.
- Going by their retirement floors, the successors launched around 2026-09-22 (Opus 5.5) and 2026-09-28 (Sonnet 5.5). The 2026-10-16 amendment that added `meeting_summary` to the Sonnet tier did not re-check the tier itself.
- The pinned SDK, `anthropic==1.2.0`, does not list `claude-*-5-5` in `ModelParam`; its literals stop at `claude-sonnet-5` and `claude-opus-5`. The parameter also accepts `str`, so the new IDs still work. The CHANGELOG adds `claude-opus-5-5` in 1.8.0 and `claude-sonnet-5-5` in 1.9.0. The latest SDK is **1.15.0** (2026-10-15).
- The Cost-model note's Sonnet 5 figure ($2/$10, made permanent, with the $3/$15 increase cancelled) is still correct, as footnote 3 of the pricing page confirms. "Opus 5 remains $5/$25" is also correct, but Opus 5 is no longer the current Opus.
- Smaller point: the Stack row "Claude models" says Sonnet serves "briefings, drafts, inquiry". AD-15 now also routes `meeting_summary` there, and the row does not say so.

**Ask:** re-decide the tier IDs, or record why the legacy IDs are kept. If they move, `7c`'s dated price table needs rows for `claude-*-5-5`, since AD-17 refuses a model that has no price row. Bumping the SDK is not needed to use the new IDs.

### F2 — AD-1 class F: "no environment or profile read" is not achievable just by passing the key and base URL (Medium-High)

From the installed `anthropic/_client.py` (1.2.0), `Anthropic.__init__`:

- Credential and `ANTHROPIC_BASE_URL` reads are skipped when the caller passes `api_key` and `base_url`. The spine's prescription holds for those.
- **`ANTHROPIC_CUSTOM_HEADERS` is read unconditionally.** Its lines are merged into `default_headers`, and explicit `default_headers` only override matching keys. `_base_client.default_headers` builds `{..., **self.auth_headers, **self._custom_headers}`, so headers taken from the environment are sent on every request and can **override `x-api-key`**. The host cannot change, so prompt text does not leave for another host, but the environment still shapes every frontier request. The sentence "no environment … read" is therefore false as written.
- `ANTHROPIC_WEBHOOK_SIGNING_KEY` is read unconditionally. This is harmless.
- `ANTHROPIC_LOG=debug|info` (`_utils/_logs.py`) calls `logging.basicConfig` and logs **"Request options"**, which include the message bodies (the sanitized meeting text), at DEBUG level on stderr. Whether that reaches a file depends on the daemon's log handlers.
- With an explicit key, `_warn_env_shadow` still reads `ANTHROPIC_PROFILE`, `ANTHROPIC_CONFIG_DIR` and the `~/.config/anthropic/active_config` pointer file. The result only decides whether to log a warning; it is not used for routing. "No profile read" is false in letter only.

**Ask:** AD-1 class F should name the mechanism. For example, the adapter builds the client with `ANTHROPIC_CUSTOM_HEADERS` and `ANTHROPIC_LOG` removed from the environment it sees, or it asserts after construction that `client._custom_headers` holds only what pm-ai set. A test should pin this, so that a future SDK bump that adds another environment read (1.14.0 added `ANTHROPIC_OPEN_TELEMETRY` and global-tracer trace headers) fails loudly.

Confirmed correct for class F:

- `max_retries=0` really disables retries. `x-should-retry: true` is honoured only while `remaining_retries > 0` (`_base_client.py:1142,1158`).
- The SDK's own default client sets `follow_redirects=True` (`_DefaultHttpxClient`), so a separate `httpx2` client without redirects is needed, as the spine says.
- `httpx2.Client` defaults to `trust_env=True` (proxies, `SSL_CERT_FILE`/`SSL_CERT_DIR`, `.netrc`) and `follow_redirects=False`. Passing `trust_env=False` is required, and "without environment" covers it.
- Passing your own `httpx2.Client` gives up the SDK's connection limits. The timeout still falls back to the SDK's 600 s default unless the client sets a non-default one.

### F3 — Stack rows `anthropic` and `httpx2`: the provenance of `httpx2` is misstated (Low)

Both rows say the `[mcp]` extra pulls in `httpx2>=2`. The installed METADATA for `anthropic-1.2.0` shows **`Requires-Dist: httpx2<3,>=2.0.0` as a core dependency**, and `uv.lock` lists `httpx2` under `anthropic`'s plain `dependencies`. `mcp 2.0.0` also requires `httpx2>=2.5.0`. Declaring `httpx2` directly is still the right call, but the stated reason should be the SDK's own `<3,>=2.0.0` range, not the extra.

The versions check out: lock `httpx2 2.12.0`; PyPI latest **2.13.1** (uploaded 2026-09-23); requires Python ≥3.10. The row's claims are correct.

### F4 — AD-1 class LM: "remote models refused" names no mechanism, and Ollama forwards cloud models off the machine (Medium)

From Ollama's docs (`docs/faq.mdx`, `docs/cloud.mdx` on main, 2026-10-16):

- The local server at `127.0.0.1:11434` serves **cloud models** (`<name>:cloud`) by forwarding prompts to ollama.com. The privacy FAQ says plainly that cloud prompts are processed by Ollama. So the class LM loopback is not enough on its own; the spine's own phrase "could forward what it is sent" is accurate.
- `api/types.go` exposes **`remote_host` and `remote_model`** on the show, list and generate/chat responses. The response field is only seen after the data has already left, so a refusal has to happen **before the request**, for example from `/api/show` or `/api/tags`.
- Cloud features can be turned off server-side with `OLLAMA_NO_CLOUD=1` or `"disable_ollama_cloud": true` in `~/.ollama/server.json`.

**Ask:** state how "remote models refused" works: a check before the request on `remote_host` from `/api/show`, plus the same check on the answer as a backstop. Have `setup`/`doctor` name `OLLAMA_NO_CLOUD=1` next to the two limits in AD-19.

Confirmed for class LM:

- **Default bind `127.0.0.1:11434`** (FAQ: "Ollama binds 127.0.0.1 port 11434 by default").
- The `ollama` package claim: the installed `ollama 0.6.2` `_client.py` reads `OLLAMA_HOST` (line 113) and `OLLAMA_API_KEY` (line 108) and defaults to `follow_redirects=True` (line 85). PyPI latest is `ollama 0.6.3` (2026-09-29), not re-checked, which does not matter since the package is dropped.
- `httpx==0.28.1`: the lock version and still the PyPI latest (released 2024-12-06; no release in about 22 months, an upstream-staleness note). `python-telegram-bot 22.8` requires `httpx<0.29,>=0.27`, so the lock gains no package and the pin cannot float past it anyway. `httpx.Client` defaults to `trust_env=True` (proxies and `.netrc` credentials, which matters for "no authorization header") and `follow_redirects=False`. `trust_env=False` is required, and "the environment … ignored" covers it.
- Housekeeping: the worktree's `pyproject.toml` still lists `"ollama"` (line 37) and `uv.lock` still carries `ollama 0.6.2`. The spine says "dropped", so `7a` owes the removal. `httpx`, `httpx2`, `requests` and `prompt_toolkit` are not yet declared in `pyproject.toml`. That matches the rows' wording ("declared" is a build commitment), but the build should declare them.

### F5 — AD-19: the Ollama server limits are correctly named; one claim relies on a default (Low)

From the Ollama FAQ (main, 2026-10-16):

- `OLLAMA_MAX_LOADED_MODELS` defaults to **3 × GPUs, or 3 on CPU**, so naming `=1` is meaningful.
- `OLLAMA_NUM_PARALLEL` now **defaults to 1**, so setting it is redundant against a current server. It is harmless, and `doctor` could say so.
- `keep_alive: 0` "unload[s] the model immediately after generating a response", and the request parameter overrides `OLLAMA_KEEP_ALIVE`. The claim is confirmed.
- On macOS the app reads server settings only through **`launchctl setenv`** followed by an app restart. `setup`/`doctor`'s "how to set them" should say this, because shell `export` has no effect on the menubar app.
- `OLLAMA_MAX_QUEUE` defaults to 512, and an overloaded server answers 503. The adapter should treat a 503 as refused, not retry it.
- The "Ollama | latest" row is the person's install. The current release is v0.40.5 (GitHub, 2026-10-14). `doctor` could record `/api/version`.

### F6 — AD-16: the no-tool Tool Runner (Low; the assumption is correctly flagged)

From the installed `anthropic/resources/beta/messages/messages.py` and `lib/tools/_beta_runner.py` (1.2.0):

- `tool_runner(..., tools=..., max_iterations=...)` exists, and `tools` is **required**. An empty iterable is sent as `"tools": []`; the SDK does not drop it.
- `max_iterations=1` sends exactly one request, whatever the stop reason. `_should_stop` is checked before every request, and `_iteration_count` increments after the first. A `pause_turn` or `compaction` stop would be returned unfinished rather than resumed. With no tools, `pause_turn` is not expected, but the adapter should treat any stop reason other than `end_turn` (`max_tokens`, `refusal`, `pause_turn`) as an incomplete answer.
- **Server-side fallback is opt-in.** The `fallbacks` parameter docstring reads "Opt-in server-side retry on one or more substitute models…". Leaving it out means no fallback, and the runner has no client-side refusal fallback (`"refusal": "stop"`). "No server-side fallback" therefore holds by omission. A test should assert that `fallbacks` and `fallback_credit_token` are absent from the request.
- **Whether the API accepts `tools: []` could not be confirmed on the web.** The API reference marks `tools` optional and gives no `minItems`, but it does not say whether an empty array is allowed, and searches found nothing either way. The spine's "Assumed, checked when `7c` is built" is the right status. Keep it.
- `tool_runner` in 1.15.0: the CHANGELOG from 1.2.0 to 1.15.0 shows runner additions (`compact_before_next_turn`, streaming tool calls, `add_tools`). It mentions no change to `max_iterations` and no move out of beta. Re-check on any bump.

### F7 — Stack rows `requests` and `prompt_toolkit` (both confirmed)

- `requests==2.34.2`: the lock version and the PyPI latest (2026-05-14); requires Python ≥3.10. `msal 1.38.0` depends on it, so the lock gains no package. MSAL takes a caller-built `http_client`; it creates its own `requests.Session()` only when none is passed (`msal/application.py:702–707`). The installed version backs the row's claim. Note that `requests.Session` defaults to `trust_env=True` (env proxies, `REQUESTS_CA_BUNDLE`, `.netrc`). If the Graph adapter's session should ignore the environment the way class F and class LM do, it must set this explicitly. The spine says nothing either way.
- `msal`: lock 1.38.0, PyPI 1.39.0 (2026-09-17). Outside this wave's rows; for information only.
- `prompt_toolkit 3.0.53`: PyPI latest (2026-07-26), `requires_python >=3.10`, `requires_dist ['wcwidth>=0.1.4']`. The only dependency is `wcwidth`, as the row says. Neither package is in the lock yet, consistent with "exact pin at build".

## Checked and holding

| Claim | Source | Result |
| --- | --- | --- |
| `httpx2==2.12.0` is the lock version; 2.13.1 is newest | `uv.lock:415`; PyPI | Holds |
| `requests==2.34.2` is the lock version; MSAL's dependency | `uv.lock:1018`; lock dependency graph | Holds |
| `prompt_toolkit` 3.0.53, Python ≥3.10, one dependency `wcwidth` | PyPI JSON | Holds |
| `httpx==0.28.1` is the lock version | `uv.lock:400`; PyPI latest | Holds; PTB caps it below 0.29 |
| `ollama 0.6.2` reads `OLLAMA_HOST`/`OLLAMA_API_KEY` and follows redirects | installed source | Holds |
| Ollama binds 127.0.0.1:11434 | Ollama FAQ | Holds |
| `keep_alive: 0` unloads after the response | Ollama FAQ | Holds |
| `OLLAMA_MAX_LOADED_MODELS` / `OLLAMA_NUM_PARALLEL` exist | Ollama FAQ | Hold (NUM_PARALLEL now defaults to 1) |
| `max_retries=0` means no retries | `_base_client.py` | Holds |
| No server-side fallback unless asked | `message_create_params.py` docstring | Holds (opt-in) |
| `max_iterations=1` means one request | `_beta_runner.py` | Holds |
| Empty tool set accepted by the API | API reference, web | **Unverified**; correctly flagged as assumed |
| SDK ignores the environment with an explicit key and base URL | `_client.py` | **Fails**: `ANTHROPIC_CUSTOM_HEADERS`, `ANTHROPIC_LOG` (F2) |
| `claude-sonnet-5` / `claude-opus-5` are the tiers to use | Models overview, pricing | **Stale**: legacy since about 2026-09-22/28 (F1) |
| `httpx2` comes from the `[mcp]` extra | anthropic METADATA | **Wrong**: core dependency (F3) |

## Sources

- PyPI JSON API, queried 2026-10-16: anthropic 1.15.0, httpx2 2.13.1, httpx 0.28.1, requests 2.34.2, prompt_toolkit 3.0.53, ollama 0.6.3, python-telegram-bot 22.8, msal 1.39.0.
- GitHub releases: ollama/ollama v0.40.5 (2026-10-14).
- https://platform.claude.com/docs/en/about-claude/models/overview
- https://platform.claude.com/docs/en/about-claude/model-deprecations
- https://platform.claude.com/docs/en/about-claude/pricing
- https://platform.claude.com/docs/en/api/messages (the `tools` parameter)
- https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/CHANGELOG.md
- https://raw.githubusercontent.com/ollama/ollama/main/docs/faq.mdx, `docs/cloud.mdx`, `api/types.go`
- Installed source in `.venv/lib/python3.14/site-packages`: `anthropic` 1.2.0, `httpx2` 2.12.0, `httpx` 0.28.1, `ollama` 0.6.2, `msal`, and METADATA for each.
- `uv.lock`, `pyproject.toml`.
