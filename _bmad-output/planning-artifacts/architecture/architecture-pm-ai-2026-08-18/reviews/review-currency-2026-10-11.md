# Currency / reality-check review — spine update 2026-10-11

Lens: was each committed technical claim confirmed against the web, the code, or a run on this machine, rather than asserted from memory? Scope: only the changed lines in the 2026-10-11 diff (AD-5 single-writer paragraph, AD-9 `answered_at`, AD-27 enforcement and mentions rule, AD-48 list declaration, the "no daemon" gap row).

Verdict: **Mostly sound, with one real portability bug hidden behind the errno list, and three claims that hold only on Darwin or are worded more strongly than the evidence.** The Python and Graph claims hold. The POSIX claims are right for Darwin/APFS, which is where they were checked, but the spine states them as if they held on every platform.

## Sources checked

- Linux `flock(2)` (man.archlinux.org mirror of man-pages): locks belong to the open file description; they are released by `LOCK_UN` or when the last duplicate descriptor closes; a `fork` shares the lock. On NFS (Linux 2.6.12 and later) `flock` is emulated as an `fcntl` byte-range lock, and **an exclusive lock requires the file to be opened for writing**. `local_lock` makes the locks client-local. ENOLCK is documented only as "kernel ran out of memory for lock records". ENOTSUP and EOPNOTSUPP are not in the error list.
- Darwin `flock(2)` (Xcode man pages): `ENOTSUP` means "descriptor is not of the correct type". Descriptors duplicated by `fork` or `dup` share one lock. The page does not say the lock is released when the process exits.
- Darwin `mount_nfs(8)`: with `nolocks`/`nolockd`, every lock operation returns `ENOTSUP`. With `locallocks`, locks are enforced on the client only, and no error is returned.
- Microsoft Graph v1.0: `chatMessageMentionedIdentitySet` has `user`, `application` and `conversation` (team, channel or chat). `device` exists but is unused. `teamworkUserIdentity` has `id`, `displayName`, `userIdentityType` (`aadUser`, `anonymousGuest`, `federatedUser`, `personalMicrosoftAccountUser`, `phoneUser`, `emailUser`, …) and an optional `tenantId`.
- Run on this machine (Python 3.14.7 x86_64 under uv; the shell is arm64, so the interpreter runs under Rosetta, which confirms the spine's "x86_64 under Rosetta"):
  - `os.open(..., O_RDONLY|O_CREAT|O_NOFOLLOW|O_NONBLOCK)` on an existing directory **opens it**; on a FIFO **opens at once**; on a symlink fails with **ELOOP (62)**. This matches the comments in `platform/claims.py`.
  - `tuple[str, ...] | None == Optional[Tuple[str, ...]]` is **False**. `None | tuple[str, ...]` equals `tuple[str, ...] | None`.
- Code: `pm_ai/platform/claims.py`, `pm_ai/domain/events.py:_is_list`, `pm_ai/domain/payload_fields.py:_kind`, `pm_ai/domain/event_entries.py` (the 1q measurement docstring), `pm_ai/storage/service.py` (append path and the `ClaimHeld` translation at :1413), `pm_ai/core/project_scaffold.py:69` (the `.gitignore` line).

## Findings

### F1 — HIGH — On Linux NFS, the "filesystem without locks" path is unreachable, and the refusal is not the named one

The spine says a fault that is not "busy" is named, and gives as an example "a filesystem without locks (`ENOTSUP`, `EOPNOTSUPP`, `ENOLCK`, naming the mount)". `claims.py` opens the sidecar `O_RDONLY` and asks for `LOCK_EX`. Linux `flock(2)` says that on NFS an exclusive lock requires the file to be open for writing. So on a Linux NFS client the call fails with `EBADF`. `EBADF` is neither in `_UNSUPPORTED` nor EAGAIN, so the code re-raises a bare `OSError` that does not name the mount — the opposite of what the spine promises for exactly the network-mount case it calls out. Darwin does not have this restriction, which is why nothing caught it.
**Fix:** open the sidecar `O_RDWR` (it is pm-ai's own empty file at `0600`, so nothing is lost), or add `EBADF` to the "no usable lock here" set. Either way, pin it in a test that fakes the errno. Change the spine only if the code is not fixed.

### F2 — MEDIUM — The errno list is presented as universal, but the evidence is per-platform, and the silent cases are not mentioned

- `ENOLCK` = "NFS without a lock daemon" is not what Linux `flock(2)` documents (it says out of memory for lock records). The NFS meaning is folklore for `fcntl` over `lockd`. On Darwin the documented answer for a `nolocks` NFS mount is `ENOTSUP`.
- The case that actually defeats the claim gives no error at all: Darwin `locallocks` and Linux `local_lock`/`nolock` mounts accept the lock but enforce it only on this client. Two machines sharing a repository over such a mount each get a "successful" exclusive claim. Same-host serialization still holds, which is what AD-5 needs before the daemon exists, but the spine's "never silently unlocked" (the `claims.py` docstring) and "a filesystem without locks … is named" read as broader than that.
- The `exFAT` example in the `claims.py` docstring is unverified: no source was found for what `flock` returns on Darwin exFAT.

**Fix:** replace the parenthetical with what was confirmed: "`ENOTSUP` (Darwin's documented answer, including NFS mounted `nolocks`), `EOPNOTSUPP`, `ENOLCK` (Linux NFS/lockd)". Add one sentence: a mount that enforces locks client-locally passes silently, so the claim serializes processes on one host, not across hosts. Drop exFAT, or measure it.

### F3 — MEDIUM — "Darwin promises no atomicity for a regular-file write above a page" implies a promise below a page that does not exist

Neither POSIX nor Darwin's `write(2)` documents interleaving atomicity for regular files at any size. POSIX's `PIPE_BUF` guarantee covers pipes and FIFOs only, and `O_APPEND` makes only the offset positioning atomic. "Above a page" invents a threshold. The 1q measurement is properly hedged — an APFS run up to 1 MiB, "the largest size tried, not a ceiling found" — which is good evidence handling. Two problems remain:
1. The spine sentence that leads into it overstates the documented baseline.
2. The spine leaves out the point the code docstring admits (`event_entries.py`, end of the 1q block): **one harvest batch is a single write of many records with no size bound of its own**, so a batch above 1 MiB is outside what was measured. The spine says the rule "holds only while one record is one write", but the write that is actually exposed is a batch, not a record.

**Fix:** "Neither POSIX nor Darwin documents atomicity for concurrent regular-file writes of any size, so the bound is measured…". Then add: "a harvest batch is one write with no bound of its own; a batch over 1 MiB is outside the measurement (the code docstring says so)". Either cap the batch write at the measured size or record it as an open item.

### F4 — LOW — "A kill releases the lock" holds only while no forked child shares the descriptor

Correct for Linux: the lock belongs to the open file description, and the kernel releases it on the last close. Darwin's man page does not state release on exit, but says `fork` and `dup` share one lock, which implies the same model. The caveat: a child created by `fork` while the claim is held keeps the lock alive after the parent is killed. On Darwin, a child that unlocks also drops the parent's lock. `os.open` descriptors are non-inheritable (PEP 446), so `subprocess`/`exec` is safe, but a bare `os.fork` or `multiprocessing` with the fork start method is not. pm-ai does not fork today.
**Fix:** one clause: "…released when the last descriptor sharing it closes; no code forks while holding a claim."

### F5 — LOW — `get_type_hints` does not by itself make the spellings "one type"; the code's normalisation does

The spine says the check "resolves the annotation (`typing.get_type_hints`) and compares the resolved type, so `Optional[tuple[str, ...]]`, the reversed union and the `typing.Tuple` spelling are one declaration". Verified on 3.14.7: `Optional[Tuple[str, ...]] == tuple[str, ...] | None` is **False**, because `typing.Tuple[str, ...]` stays a `typing._GenericAlias`. The rule holds only because `_is_list` (events.py:324) and `_kind` (payload_fields.py) compare `get_origin(...) is tuple` and `get_args(...) == (str, ...)` over a set of union members. The behaviour is right. The stated mechanism ("compares the resolved type") is not, and a builder who writes `hint == tuple[str, ...] | None` following the spine would refuse the `typing.Tuple` spelling.
**Fix:** "resolves the annotation (`typing.get_type_hints`) and compares the union's members by `get_origin`/`get_args`, so … are one declaration". No code change.

### F6 — LOW — Graph mentions: `mentioned.user.id` confirmed, but non-user mentions and non-AAD identities have no rule

`mentioned.user.id` exists (v1.0, `teamworkUserIdentity.id`). It is present only when a *user* was mentioned. A mention of a channel, team or chat (`conversation`) or of a bot (`application`) has no `user`, and the spine says "each `mentions` member is the mentioned person's actor id … else `unresolved:graph:<Graph user id>`", which has no answer for those. Also, `id` belongs to the user's home tenant. For `federatedUser`/guest identities, the optional `tenantId` is what keeps two tenants' ids from colliding, and for `anonymousGuest`/`phoneUser`/`emailUser` the `id` is not an Entra object id that an alias entry is likely to know. The Graph connector does not read mentions yet (slice `33d`), so nothing on disk is affected.
**Fix:** add to the AD-27 sentence: "a mention with no `mentioned.user` (a channel, team, chat or app) is not a person and is not a `mentions` member." Either state that the handle for a non-`aadUser` identity is out of scope until `33d`, or include `tenantId` in the handle.

## Confirmed without change

- `flock` is per open file description, and the sidecar is never unlinked (the unlink-race reasoning in `claims.py` is correct per `flock(2)` semantics).
- On Darwin, `O_NOFOLLOW` refuses a symlinked sidecar (ELOOP), and `O_NONBLOCK` lets a FIFO open immediately (POSIX `open`: O_RDONLY|O_NONBLOCK on a FIFO returns without delay). The `S_ISREG` check then refuses the FIFO and the directory. Measured here. The spine's list "directory, FIFO or unreadable" omits the symlink case, which the code also refuses (ELOOP, a plain `OSError`). Consider naming it.
- `ClaimHeld` is caught only inside storage (`service.py:1413`); no surface catches it. `CLAIM_SIDECAR_SUFFIX` is declared in the scope model, collection listings hide it (`service.py:1333`), and the scaffold writes the `.gitignore` line (`project_scaffold.py:69`).
- The 1q measurement environment (macOS 26.6 / Darwin 25.6.0, APFS, x86_64 interpreter under Rosetta on an arm64 host) matches this machine.
- `answered_at` (AD-9): the changed lines make no external-library claim. The provenance limit ("checks presence and shape, not provenance") is stated honestly.
