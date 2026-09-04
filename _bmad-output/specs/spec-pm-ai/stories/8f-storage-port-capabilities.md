---
title: 'The storage port declares what storage does'
type: 'feature'
created: '2026-09-03'
status: 'ready-for-dev'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `StoragePort` declares nine methods (`ports/__init__.py:286-314`) and **neither `write_artifact` nor `read_artifact`**, while `StorageService` implements both (`service.py:1022,1065`). So the Protocol under-declares its own implementation: anything typed against the port cannot reach the single writer or the single reader, which is the whole of AD-3's tiering contract as far as a port consumer can see. The wave-1 review filed this as blocking; it is not, because `Daemon.storage` is the concrete class — but four slices need artifact I/O through the port and one needs to enumerate a collection, and nothing can.

Two mechanisms are missing beside it. Nothing lists a Tier-1 `Collection`, so `8b`'s orphan-aware duplicate check and `11a`'s `for_day` have no way to enumerate `connectors/` or `meetings/`. And a declared-unencrypted artifact cannot ask for a restricted mode: `_replace` passes `mode=ENCRYPTED_FILE_MODE if sealed else None` (`service.py:880,903`), so `connectors/` lands at the umask, typically 0644, for a file whose whole content is a credential's neighbourhood.

**Approach:** Declare the three capabilities on `StoragePort` and add the mode mechanism, so the slices that need them are specified against an interface rather than around one.

## Boundaries & Constraints

**Always:**
- **The port declares what the service already does.** `write_artifact` and `read_artifact` exist; this slice adds no behaviour to either, only the declaration — the shape story `2h` used when it added the event-log methods for exactly this reason.
- **The listing method returns names, not paths.** A caller in `core` may not learn where an artifact lives; `1a` made the resolver the only thing that knows, and a port handing out paths would route around it.
- **A restricted mode is declared, never passed.** The caller names *what* it is writing and the scope trees decide *how* — the rule `write_artifact`'s own docstring states. So the mode comes from the declaration, and no signature grows a `mode` argument.
- **A restricted mode applies to the file, never to its parents.** `_publish` treats a non-`None` mode as *enclave* and chmods every parent directory to 0700 (`service.py:941-953`), including `~/.pm-ai`. That is correct for the encrypted set and wrong here: tightening the application root as a side effect of one connector file is a change nobody asked for and nobody would notice.
- **Absence is a value, not an exception.** `read_artifact` ends in `path.read_bytes()` (`service.py:1079`), so a first run raises where every caller needs "not there yet". The port declares the form that can say so.

**Ask First:** Nothing.

**Never:** No new artifact, no new tier, no scope-model change. No credential handling, no probe, no CLI — `8b`. No change to how `write_artifact` decides sealed-or-not: the declaration decides, and this slice does not touch that.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Port conformance | `StorageService` against `StoragePort` | `isinstance` holds with the three new members | N/A |
| Read an absent artifact | nothing written yet | absence reported as a value | never `FileNotFoundError` |
| Read an unreadable artifact | a directory, or EACCES | refused, distinctly from absent | propagated |
| List a populated collection | `connectors/` with two members | both names returned, no paths | N/A |
| List an empty collection | declared, nothing written | empty, not an error | N/A |
| List a collection that is not one | a `File` key | refused — a collection listing over a single file is a caller error | propagated |
| Write a declared-restricted artifact | `connectors/<name>.json` | file at 0600 | N/A |
| Parent modes after that write | `~/.pm-ai` and the collection directory | **unchanged** | N/A |
| Write an encrypted artifact | `private/config.json` | 0600 file inside 0700 directories, exactly as today | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/ports/__init__.py:286-314` -- `StoragePort`'s nine methods, and the two absences
- `pm_ai/storage/service.py:1022,1065` -- `write_artifact` and `read_artifact` as they stand
- `pm_ai/storage/service.py:880,903,941-953` -- `_replace`'s mode argument and `_publish`'s enclave behaviour, which is what must not reach the parents here
- `pm_ai/domain/scope_model.py:451,484` -- `connectors/` (unencrypted, gitignored) and `config.json` (encrypted): the two cases the mode rule has to separate
- `pm_ai/domain/storage_tiers.py` -- where a declared mode belongs, beside the tier and exclusion answers
- `tests/architecture/test_domain_invariants.py:793-826` -- `test_adapters_satisfy_the_ports_they_are_declared_against`, which this slice's conformance criterion extends
- `_bmad-output/implementation-artifacts/deferred-work.md` -- the A1 entry this slice closes

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/ports/__init__.py` -- declare `write_artifact`, `read_artifact` in its absence-tolerant form, and the collection listing on `StoragePort`
- [ ] `pm_ai/domain/storage_tiers.py` -- declare the restricted mode where the tier and exclusion answers already live, so it derives from the trees rather than a second table
- [ ] `pm_ai/storage/service.py` -- honour the declared mode for the file alone, and give `read_artifact` the absence-tolerant form
- [ ] `tests/architecture/test_domain_invariants.py:793-826` -- extend port conformance to assert the three new members
- [ ] `tests/architecture/test_storage_capabilities.py` -- the matrix, with the parent-mode case asserted by `stat`

**Acceptance Criteria:**
- Given `StorageService`, then `isinstance(service, StoragePort)` holds and the port names `write_artifact`, `read_artifact` and the listing — asserted in the conformance test, because a Protocol that omits a method its implementation has is invisible to `isinstance`.
- Given a declared-restricted artifact written to a temporary root, then `stat` reports 0600 on the file **and** the mode of every parent directory is what it was before — the naive implementation reuses the enclave path and chmods `~/.pm-ai` to 0700, which a file-only assertion cannot see.
- Given an encrypted artifact written after this change, then its file and directory modes are byte-for-byte what story `1f` established — this slice adds a case, it does not reopen one.
- Given a first run with nothing written, when an artifact is read, then absence is returned rather than raised — asserted against a real temporary root, because a fake that returns `b""` proves nothing.
- Given a collection listing, then no member of the result is a path — asserted by shape, since a path would let `core` route around the resolver `1a` established.

## Spec Change Log

- **2026-09-03, split from `8b` at the sizing gate.** Amending `8b` against the second multi-lens review took it to 2906 body tokens against wave 1's 1600. Three of its tasks were storage capabilities rather than credential lifecycle — the two port declarations, the collection listing its duplicate check needs, and the restricted-mode mechanism — and `11a` needs the same listing for `for_day`. Specifying them once, against the port, is what lets both slices be written against an interface instead of around one.
  This also closes the review's **A1**, which had no owner. It was filed as blocking, downgraded because `Daemon.storage` is the concrete class, and recorded in `deferred-work.md` on 2026-09-03 precisely because a downgrade is how a real inconsistency becomes invisible.
  From the review's edge cases: `read_artifact` raising `FileNotFoundError` where every caller needs absence as a value (B4's storage half), and `_publish`'s enclave chmod reaching `~/.pm-ai` (B13).

## Verification

**Commands:**
- `uv run pytest tests/architecture/test_storage_capabilities.py tests/architecture/test_domain_invariants.py -q` -- expected: all matrix rows and conformance pass
- `uv run pytest -q` -- expected: no new failures; story `1f`'s cipher and mode tests unchanged
- `uv run lint-imports` -- expected: contracts kept, AD-30 among them
- `uv run mypy` -- expected: clean
