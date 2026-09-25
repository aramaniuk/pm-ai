# Currency review — ARCHITECTURE-SPINE.md, 2026-09-25 revision

**Scope:** the changed statements only: AD-6's new "two announcements" paragraph, the Stack table's Python row and `watchdog` row, and the closed Enforcement row "Composition is eager…".
**Lens:** is every committed version, technology or behaviour confirmed against the web or the repo, rather than asserted?
**Verdict:** **Approve with one required fix** (the `watchdog` row) and two small corrections. Every Python and Unicode number in the Python row was checked on the interpreters themselves and is correct. The AD-6 paragraph and the closed row match the code and tests. The `watchdog` row was edited in this revision to name 3.14 but was not re-checked for 3.14, and 3.14 changes what installing it involves.

## What was checked, and how

| Claim | Source checked | Result |
| --- | --- | --- |
| `requires-python = ">=3.14,<3.15"`, `.python-version` `3.14` | `pyproject.toml:5`, `.python-version`, `uv.lock:3` (`==3.14.*`) | Matches |
| Unicode 16.0.0 on 3.14 | `uv run python`: 3.14.7, `unicodedata.unidata_version` = 16.0.0 | Matches |
| Unicode 15.1.0 on 3.13 | `uv run --no-project --python 3.13`: 3.13.14, 15.1.0 | Matches |
| Fingerprint includes the Unicode tables | `tests/domain/test_sanitization_rates.py:1113` hashes `unicodedata.unidata_version`. `RULE_CHANGELOG` pins 16.0.0 (`pm_ai/domain/sanitize.py:776`) | Matches |
| 3.14 is current and supported | PEP 745: 3.14.7 released 2026-08-05 (the version installed here), 3.14.8 due 2026-10-06, bugfix releases until 3.14.14 (2027-10), security fixes until about 2030-10 | Current. The pin has about a year of bugfix support left |
| 3.15 status | PEP 790: rc1 2026-08-04, rc2 2026-09-01, **final 2026-10-01**. `uv python list` offers 3.15.0rc1. Measured: 3.15.0rc1 ships Unicode **17.0.0** | Not in the row (see F2) |
| `watchdog==6.0.0` is still the latest release | PyPI JSON, fetched today: latest 6.0.0 (2024-11-01), `requires_python >=3.9` | Still latest. The row's age figure is stale (see F3) |
| `watchdog` on macOS + 3.14 | PyPI file list for 6.0.0; `uv.lock` wheel entries; a scratch venv build | **No cp314 macOS wheel** (see F1) |
| AD-6: log entry just before the first protected write, never at start-up, never on a read; a failed append blocks the write; one entry per writer, flag on the cipher | `pm_ai/app/wiring.py:424-501` (`_RecordedPlaintext.encrypt` records under a lock before returning bytes and refuses if unbound; `decrypt` is inherited and records nothing; `_writer` binds every `_RecordedPlaintext`); tests at `test_cipher.py:383-598` | Matches |
| "every writer the composition root builds is bound this way" | `wiring.py:302, 1058, 1174` all go through `_writer`; `test_every_writer_wiring_builds_goes_through_the_one_that_binds` | Matches |
| Closed row: `--help`, a mistyped command, `project` and `doctor` change no file | `test_a_command_that_writes_nothing_leaves_every_file_alone` (`test_cipher.py:767-798`); `uv run pytest`: 2661 passed, 24 skipped | Matches, with one qualifier (see F4) |

## Findings

### F1 — Required: `watchdog` 6.0.0 has no macOS wheel for Python 3.14, so on the only v1 platform it is compiled from source

The row now reads "requires Python ≥3.9 against this project's 3.14". The metadata does say that. What the row leaves out is that 6.0.0 predates 3.14. Its macOS wheels stop at cp313 (the PyPI file list has cp39–cp313 `macosx` wheels plus PyPy, and nothing for cp314). Its FSEvents backend is a C extension (`_watchdog_fsevents`), so it has no pure-Python fallback wheel for macOS. The lockfile has the same gap: `uv.lock`'s `watchdog` entry lists only `py3-none` Linux and Windows wheels. So `uv sync --extra runtime` on a Mac builds the sdist. That needs a C toolchain (Xcode Command Line Tools).

- Measured: `uv pip install --no-build watchdog==6.0.0` on 3.14 is **unsatisfiable**. A normal install in a scratch 3.14 venv **did** build, and `FSEventsObserver` plus `_watchdog_fsevents.cpython-314-darwin.so` loaded. So it works on this machine, which has a compiler. It is not a blocker, but it is a new install prerequisite.
- This is the same objection the table itself used on 2026-08-27 to remove SQLCipher: "no macOS wheel… meaning a source build on the one platform v1 targets". Pinning 3.14 created the same situation for `watchdog`, and the row does not say so.
- Nothing in the repo has installed the extra yet: `import watchdog` fails in the current `uv run` environment. So no one has hit this.
- **Fix in the row:** say that 6.0.0 has no cp314 macOS wheel, that it is built from the sdist (Xcode CLT required), and that this was measured to build and load on 2026-09-25. Alternatively, record a decision to wait for a cp314 wheel or change backend behind `FileWatcherPort`. The "standing currency risk" sentence should name this as the risk that has now happened.

### F2 — Should fix: the 3.15 sentence omits that 3.15.0 ships in six days, with Unicode 17.0.0

"Moving to 3.15 is its own slice" is the right policy. But 3.15.0 final is scheduled for **2026-10-01** (PEP 790), and 3.15.0rc1 is already offered by `uv python list`. Measured: it runs Unicode **17.0.0**, so the move will require a new rule version. Adding "3.15.0 final 2026-10-01; Unicode 17.0.0 (measured on rc1)" would give the row the same kind of evidence it already gives for 3.13 and 3.14, and would date the pending slice. The 3.14 bugfix window (until 3.14.14 in 2027-10, per PEP 745) is also worth a clause, because it bounds how long the exact pin can stay put.

### F3 — Minor: the `watchdog` row was edited but its age figure was not refreshed

The row was edited this revision but still says "verified on PyPI 2026-08-27" and "~21 months without one". Re-verified today: 6.0.0 is still the latest release, and the gap is now about 23 months. Either update the date and count, or write the age as "since 2024-11-01" so it cannot go stale.

### F4 — Minor: "change no file" holds on a machine already in use, not on a fresh home

The closed row says `--help`, a mistyped command, `project` and `doctor` "change no file". The test that backs it (`test_cipher.py:779-798`) runs each command once first. Its own comment says the first command on a fresh home creates the operational database. Only the second run is asserted to leave the tree unchanged, and the first run is asserted only to write no security line. The row's claim is accurate for the case it cares about, but it is broader than what was measured. Suggested wording: "…change no file on a machine already in use (the first command on a fresh home still creates the operational database), and none writes the security line."

### No finding

- **Python row reasoning.** "Any other minor version fails its fingerprint test" is correct by the code's construction. The hash input includes `unidata_version`, and 3.13 and 3.15 measure differently from the pinned 16.0.0. In practice `requires-python` refuses those interpreters before tests run (`uv run --python 3.13` errors on `==3.14.*`), so the pin itself is the first guard. That is consistent with the row, not a contradiction of it.
- **AD-6 paragraph.** Every behavioural claim traces to `_RecordedPlaintext` and `_writer` and is covered by a named test, including concurrency (`test_concurrent_first_protected_writes_record_one_line`) and retry after a failed append. No version or third-party claim is involved.
- **Consistency with the repo.** `AGENTS.md:19` states the same 3.14-exactly rule and the same Unicode numbers as the row.
