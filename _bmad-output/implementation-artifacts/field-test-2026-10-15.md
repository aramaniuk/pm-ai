# Field test of wave 2 on real accounts — 2026-10-15/16

The first run of pm-ai against a real Microsoft 365 work tenant and a real self-hosted GitLab, from `main` at `7a00270` (wave 2 complete: #66–#69). Andrei drove the interactive steps; Claude measured between them. This repository is public, so the report names no tenant, host, team, channel, chat, meeting or person: meetings are described by kind, and every figure is a count, a time or a state.

## What ran

- **Machine:** macOS on Apple silicon, Python 3.14.7 (`uv`, the repository's `.venv`), the clock checked against Apple's time server (+0.15 s) and two providers' HTTP `Date` headers (equal to the second).
- **Home:** an existing `~/.pm-ai` holding only test leftovers, kept on purpose so setup also met an upgrade path.
- **Setup:** `pm-ai setup` — master key, one project (a git repository whose remote is a self-hosted GitLab), `display_timezone` (UTC+3), `verbose_logging`, and a Microsoft Graph connector through the device-code sign-in with the tenant's own app registration; then `pm-ai connector add gitlab` for that project (group/project path, a `read_api` token).
- **Listings:** one Teams channel filed into the project and thirteen chats (seven meeting chats, five one-to-one, one more meeting chat) filed into personal, written into the Graph connector's settings file by hand.
- **Daemon:** `pm-ai daemon run` in the foreground, restarted three times during the evening (two of them to re-run a harvest), then left running for 10.2 hours overnight.
- **Prompt:** `pm-ai` with no arguments, inside the project's folder.

## Results, by slice

| Area | Slice | Result | Measured |
|---|---|---|---|
| `doctor` before setup | `9b` | exit 4; the `renders` line names `display_timezone`, as decided in #69 | 1.20 s |
| `setup` | `4p`, `8q` | all four steps; `doctor` healthy at its end | interactive |
| GitLab enrolment | `8o` | probe answered for the group/project path | interactive |
| `connector check` | `4n` | GitLab ok; Graph `[warning]` from finding 1 → exit 4 | 1.96 s for both |
| `dashboard` by hand | `23b`, `9b` | both scopes written, both instants recorded | 2.87 s personal, 2.40 s project |
| Day's meetings on the page | `23b` | 5 = exactly the meetings accepted or organised; two unanswered invitations and one cancellation left out | — |
| First harvests | `9a` | Graph 3 s after start; GitLab 7 min 31 s after start, the planned spread for two instances | — |
| Harvest cycle overnight | `9a` | Graph +253, +226 min; GitLab +246, +234 min (240 ± 15) | no failure rows |
| 07:00 render | `9b` | personal at 04:00:00.02 UTC (07:00 local), project at 04:00:04 | — |
| Request path | `4r` | inline lines exit 0/2/3 as designed; jobs `running` → `done` | `GET /v1/status` 238–274 ms; inline 1.4–4.2 ms; job acknowledged 2–6 ms; dashboard job ≈ 2.5–3.1 s; `connector check` job 1.28 s; `doctor` job 0.21 s |
| Prompt through the daemon | `4q` | `config show` identical to the terminal; a job's result printed with its id at the next prompt; a relative path made absolute; `doctor` and `goal set` local | `transcript drop` jobs 15–20 ms |
| Shutdown | `4r` | Ctrl-C: process gone in ≈ 2 s, WAL and shared-memory files removed (clean close); restart re-harvested only what was new | — |
| Transcripts | `33e`, `11b` | after re-running the first harvest with a 900-minute reach-back: one project sync-up's transcript stored and ingested (27.7 KB, 198 cues, every cue speaker-tagged, 4 speakers, 16 min 52 s, file 0600); 0 proposals, as `11b`'s named limit predicts | — |
| Teams messages | `33d`, `33f`, `23c` | 9 `message_posted` events (8 personal from chats, 1 project from the channel), 3 of the 14 listings having posts inside the reach-back; the Proactive Enablement section lists them | harvest with 1 channel + 13 chats ≈ 12 s |
| GitLab events | `8n` | 38 commits on the first run, 189 on the second (finding 19) — 227 in all, every committer date matching the clone | — |

## Findings

Severity is by consequence for the person using pm-ai. "Where" is the code the finding lives in on `7a00270`.

| # | Finding | Severity | Where |
|---|---|---|---|
| 1 | **The clock-skew warning is false for every Microsoft sign-in.** pm-ai measured "5:00.5 ahead" while the clock was within 0.15 s: the skew is computed from the ID token's `iat`, which Microsoft's identity platform backdates by five minutes. It turns the Graph connector's health into `[warning]`, so `connector check` exits 4 on a healthy machine. | Bug — high | `connectors/graph/auth.py:1620` `_measured_skew` |
| 2 | Graph enrolment ends "nothing harvests from it until scheduled harvesting exists" — scheduled harvesting exists since `9a`. | Stale text | `surfaces/cli/dispatch.py:1224` |
| 6 | GitLab enrolment ends "It becomes active at the next start — … nothing is harvesting from it yet": both false — rows are read fresh per job (`4r`) and the timer picks the instance up at its next read (`9a`); it was harvested 7.5 min after the daemon started. Also in two docstrings. | Stale text | `surfaces/cli/dispatch.py:1330`; `core/connector_enrolment.py:51`; `app/wiring.py:868` |
| 3, 8, 14 | **File modes.** An existing `~/.pm-ai` keeps 0755 (only directories pm-ai creates get 0700); `connectors/`, `logs/` 0755 and `daemon.log`, `config.toml`, `projects.toml` 0644; the personal dashboard and its folders under `~/.manager-ai` 0644/0755 — the day's meeting list readable by other local users — while the project copy is 0600; `transcripts/` and its declared `temp/` staging folder 0755 (the transcript file itself is 0600). `operational.db` 0644 is already in deferred work (9a). | Privacy — medium | `storage/service.py` `_mkdir_enclave` (new directories only) and the dashboard publish |
| 15 | **A message signal shows when pm-ai harvested it, not when it was posted.** Every signal read `22:51` (the harvest) for messages posted 11:20–15:41. Filtering the window on harvest time is right (finding 24); labelling with it is not. | Bug — medium | `core/rendering.py:708`, `_signal_line` `:892` |
| 16 | Senders render as raw handles (`graph:<user id>`), so the section cannot be read without looking ids up. `33d` stores ids by decision and nothing resolves them for display. | Usability — medium | `core/rendering.py:900` (no alias table) |
| 17 | A multi-line message renders with a visible `\n`: the excerpt is escaped to one line on purpose, but the escape is the two characters `\` `n`. Three of nine lines showed it. | Readability — low | `core/rendering.py:1124` |
| 18 | A listed channel can only file into an enrolled project; a channel that belongs to no project cannot be listed at all. | Product limit → requirement R2 | `connectors/graph/channels.py:132` `ChannelListing.scope` |
| 19 | GitLab's first run reaches back 7 days (`FIRST_RUN_REACH_BACK`) and every later run 30 days before the cursor (`MERGE_LAG_DAYS`), so the second run quietly back-fills three more weeks (189 commits here). Nothing is lost or duplicated; the first-run window is simply narrower than every later one. | Design inconsistency — low | `connectors/gitlab/__init__.py:106`, `:124` |
| 7 | A cancelled meeting that has ended is recorded and its transcript looked up, while the dashboard leaves cancellations out. Deliberate (`graph/__init__.py:1600` keeps an ended cancellation), but the two surfaces disagree. | Design question — low | `connectors/graph/__init__.py:1600` |
| 11 | **A meeting whose transcription was stopped and started again keeps no transcript.** A recurring management meeting held two parts, 08:33:21–08:39:41 and 08:46:21–09:02:20 UTC (non-overlapping, 6 min 40 s apart); `33e` saw two transcripts fit and stored neither, permanently ("a recording restarted — so none is stored"). | Product → requirement R1 | `connectors/transcripts/graph_transcripts.py` |
| 12 | One accepted meeting from a recurring series answers the join-link lookup (`/me/onlineMeetings?$filter=JoinWebUrl eq …`) with 404, so pm-ai records "not the PM's to read". Another meeting where the PM is likewise an attendee — invitation unanswered — resolves. Likely an organiser in another tenant or an invitation through a group; not established. | External limit — investigate | `connectors/transcripts/graph_transcripts.py:432` |
| 13 | The daemon prints nothing when it stops on Ctrl-C. | Cosmetic | `app/daemon.py` |
| 21 | At the prompt, an unquoted path with spaces fails as "`transcript drop` takes <file>" — shell-consistent, with no hint to quote it. | Cosmetic | `surfaces/cli/dispatch.py` usage refusal |
| 22 | `transcript drop` reads `.vtt` and `Speaker: words` text only; a transcript exported from Teams as `.docx` (and converted to Markdown) is refused. | Open question | `core/transcripts.py` readers |
| 23 | No way to list or show goals: `goal update`, `goal create` and `goal list` were all tried and refused as usage errors; only `goal set` exists. | Usability — low | `surfaces/cli/dispatch.py` command table |
| — | A GitLab host typed without `https://` is refused (clear message) rather than assumed https. | Cosmetic | `connectors/gitlab/client.py` `host_refusal` |

### Withdrawn during the test

- **"Sync" lost its Cyrillic words** — not a bug: Outlook shows a meeting's location after its subject; the subject was the one word.
- **Broken characters at the cut-off of a signal line** — caused by the measuring tool's byte-wise `cut`; the dashboard file holds none.
- **GitLab's second run "re-read old commits"** — explained by finding 19; nothing duplicated.
- **Coverage windows a fraction of a millisecond wide** — by design: a window is the time the fetch itself took, from the provider's answer (`domain/lifecycle.py:158`).

### Corrected: a claim made in #69

24. **#69 and its `deferred-work.md` entry said Proactive Enablement filters on each message's own timestamp, so a message harvested after a render could be lost. That was wrong.** The window filters on `ingested_at` (`core/rendering.py:708`): a message posted before a render and harvested after it appears on the next page — measured here, where messages posted at 11:20–15:41 appeared on a page rendered after a 22:20 render. The decision #69 asked for is not needed; the entry is withdrawn. What the test found instead is finding 15.

## Requirements decided during the test (Andrei, 2026-10-15)

- **R1 — Every part of a meeting's transcript is kept.** When a meeting's transcription is stopped and started again, all parts are stored and ingested in time order, so the meeting is seen whole. Parts that overlap in time stay refused as duplicates, named as such. Amends `33e`'s frozen block.
- **R2 — A listed channel may file into the personal scope.** Amends `33d`'s frozen block (`ChannelListing.scope`: "never the personal scope").

## Not covered

- The AD-19 load test (four long jobs filling the pool) — still owed from `4r`.
- More than one night; the seven-day look-back clamp; a laptop asleep across 07:00 (the machine stayed awake).
- `pm-ai connector sign-in` (no sign-in needed redoing), key rotation, schema-version stops.
- The real-tenant fixtures owed by `33d`, `33e` and `33f` were captured during this test and land in their own PR.
