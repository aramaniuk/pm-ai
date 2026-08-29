"""A write becomes visible only when it is complete (AD-47, story 1m).

Two failures with one cause. A crash mid-rotation truncating `config.json` —
which AES-GCM makes *unreadable* rather than partly readable, losing every
connector credential — and, once AD-46's watcher exists, a job triggered on a
capture that is still growing, appending a meeting summary and its commitments
to append-only ledgers and then appending both again when the file completes,
with no undo because AD-5 forbids rewriting a ledger.

Nothing here waits. Completeness is never inferred from elapsed time, so no test
in this file sleeps, polls, or tunes an interval — the kernel operation *is* the
synchronisation point.
"""

from __future__ import annotations

import re
import errno
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pm_ai.domain.event_entries import EventEntry, SelfActionType
from pm_ai.domain import CAPTURE_STAGING, DataScope, ScopeKind
from pm_ai.platform.paths import ScopePaths
from pm_ai.storage import service as service_module
from pm_ai.storage.crypto import AesGcmCrypto, PlaintextCrypto
from pm_ai.storage.service import CaptureAlreadyExists, StorageService

NOW = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)
APPLICATION = DataScope(ScopeKind.APPLICATION)
PERSONAL = DataScope(ScopeKind.PERSONAL)
BODY = "WEBVTT\n\n00:00.000 --> 00:02.000\nthe whole recording\n"
KEY = bytes(range(32))


class _AlwaysUntracked:
    """Git says the capture directory is excluded. The guard is not under test here."""

    def tracking(self, path, *, repository):
        from pm_ai.domain.vcs import TrackingVerdict

        return TrackingVerdict(ignored=True)

    def working_tree(self, path):
        return None

    def repository_marker_above(self, path):
        return None


def _writer(tmp_path: Path, *, crypto=None) -> StorageService:
    return StorageService(
        ScopePaths.rooted(tmp_path),
        now=lambda: NOW,
        vcs=_AlwaysUntracked(),
        crypto=crypto or PlaintextCrypto(),
    )


def _staging(storage: StorageService, scope: DataScope) -> Path:
    return storage.paths.resolve(scope, CAPTURE_STAGING)


# ── Captures: the name appears only when the content is complete ─────────────


def _entry(marker: str):
    """A minimal typed entry, standing in for the old free-string call (2e)."""
    return EventEntry(
        category=SelfActionType.SECURITY, actor="test", fields=(("detail", marker),)
    )


def test_a_capture_written_normally_leaves_no_staged_file(tmp_path):
    """Row 1 — the whole body under the final name, and nothing left behind."""
    storage = _writer(tmp_path)
    written = storage.write_capture(BODY, scope=PERSONAL, name="2026-08-28.vtt")

    assert written.read_text(encoding="utf-8") == BODY
    staging = _staging(storage, PERSONAL)
    assert list(staging.iterdir()) == [], f"{staging} still holds {list(staging.iterdir())}"


def test_a_taken_name_is_refused_and_the_first_recording_is_untouched(tmp_path):
    """Row 2 — `os.link` refuses `EEXIST`, so exclusivity stays kernel-enforced.

    `os.replace` would publish atomically and silently destroy the first
    recording, making this refusal unreachable. That is the whole reason the two
    publications differ.
    """
    storage = _writer(tmp_path)
    first = storage.write_capture(BODY, scope=PERSONAL, name="dup.vtt")
    before = first.read_bytes()

    with pytest.raises(CaptureAlreadyExists):
        storage.write_capture("a different recording\n", scope=PERSONAL, name="dup.vtt")

    assert first.read_bytes() == before
    assert list(_staging(storage, PERSONAL).iterdir()) == []


def test_a_write_that_fails_partway_leaves_the_name_free(tmp_path, monkeypatch):
    """Rows 3 and 4 — the failure the old exception handler could not cover.

    `write_capture` used to unlink a partly-written capture in an `except` block,
    which does nothing for `SIGKILL` or a power loss. Its own docstring named the
    result: a zero-length file owns the name, and every retry — including the one
    carrying the content — is refused as a duplicate. Simulated here by failing
    at the publish step, which is the same state a kill would leave.
    """
    storage = _writer(tmp_path)

    def _die(*args, **kwargs):
        raise OSError(errno.EIO, "disk went away")

    monkeypatch.setattr(os, "link", _die)
    with pytest.raises(OSError):
        storage.write_capture(BODY, scope=PERSONAL, name="retry.vtt")

    monkeypatch.undo()
    written = storage.write_capture(BODY, scope=PERSONAL, name="retry.vtt")
    assert written.read_text(encoding="utf-8") == BODY, (
        "the retry carrying the real content was refused — the exact failure "
        "staging exists to make impossible"
    )
    assert list(_staging(storage, PERSONAL).iterdir()) == [], "the staged file leaked"


def test_a_filesystem_without_hardlinks_still_records_the_meeting(tmp_path, monkeypatch):
    """Row 5 — declining to record a meeting because of the filesystem is worse.

    Reachable only through an enrolled project repository, never through the two
    home-directory scopes. The fallback rests on writer serialization (AD-5,
    AD-19) rather than the kernel, which covers the real case: a duplicate name
    arrives as a later retry, not a concurrent write.
    """
    storage = _writer(tmp_path)
    attempted: list[str] = []

    def _unsupported(source, target):
        attempted.append(str(target))
        raise OSError(errno.EPERM, "hardlinks unsupported")

    monkeypatch.setattr(os, "link", _unsupported)

    written = storage.write_capture(BODY, scope=PERSONAL, name="exfat.vtt")
    assert attempted, "the fallback ran without ever attempting a link"
    assert written.read_text(encoding="utf-8") == BODY

    # And the fallback keeps the refusal, which is the property most at risk when
    # a guarantee moves from the kernel into a check.
    with pytest.raises(CaptureAlreadyExists):
        storage.write_capture("second\n", scope=PERSONAL, name="exfat.vtt")


def test_the_filesystem_is_never_inspected_to_choose_the_publish_path(tmp_path):
    """The fallback is chosen by attempting the link and reading the error.

    Sniffing a filesystem type would be a second source of truth about what the
    kernel will allow, and wrong on exactly the mounts that motivated it.
    """
    source = Path(service_module.__file__).read_text()
    for sniff in ("statvfs", "f_fstypename", "psutil", "disk_partitions", "/proc/mounts"):
        assert sniff not in source, f"{sniff} suggests the publish path is chosen by inspection"


def test_staging_never_re_asks_git(tmp_path):
    """AD-43 is asked about the final capture directory, once.

    Asking again about `transcripts/temp/` is the more literal reading and the
    wrong one: two questions can get two answers, and then "which directory was
    the verdict about" has no fixed answer. A `.gitignore` negation re-including
    a subdirectory is exactly the case AD-43's rationale calls out.
    """
    trees_asked: list[Path] = []
    asked: list[Path] = []

    class _Counting(_AlwaysUntracked):
        # `working_tree` answers with a repository rather than inheriting the
        # `None` that short-circuits the guard: with `None`, `tracking` was
        # never reached and the counter below could not move however many times
        # the guard ran — the assertion held vacuously even with staging
        # rerouted through it (review 2026-08-28). Counting both calls is what
        # makes "asked once" a claim that can fail.
        def working_tree(self, path):
            trees_asked.append(path)
            return tmp_path

        def tracking(self, path, *, repository):
            asked.append(path)
            return super().tracking(path, repository=repository)

    storage = StorageService(
        ScopePaths.rooted(tmp_path), now=lambda: NOW, vcs=_Counting(), crypto=PlaintextCrypto()
    )
    storage.write_capture(BODY, scope=PERSONAL, name="once.vtt")
    assert len(trees_asked) == 1, f"git was asked about {len(trees_asked)} trees: {trees_asked}"
    assert len(asked) == 1, f"git tracking was asked {len(asked)} times: {asked}"


# ── Whole-file replacement: the credential store ─────────────────────────────


def test_a_sealed_artifact_is_never_left_truncated(tmp_path, monkeypatch):
    """Row 6 — `O_TRUNC` destroyed the old ciphertext before writing the new.

    A crash between the two left `config.json` empty, and an AES-GCM file cut
    part-way fails its tag rather than degrading: every connector credential
    unreadable, with the daemon having done nothing wrong.
    """
    storage = _writer(tmp_path, crypto=AesGcmCrypto(KEY))
    target = storage.write_artifact(b'{"token": "first"}', scope=APPLICATION, artifact="config.json")
    original = target.read_bytes()

    def _die(source, destination):
        raise OSError(errno.EIO, "crash at the publish step")

    monkeypatch.setattr(os, "replace", _die)
    with pytest.raises(OSError):
        storage.write_artifact(b'{"token": "second"}', scope=APPLICATION, artifact="config.json")

    assert target.read_bytes() == original, "the old ciphertext was destroyed before the new landed"
    monkeypatch.undo()
    assert storage.read_artifact(scope=APPLICATION, artifact="config.json") == b'{"token": "first"}'


def test_rotation_still_overwrites(tmp_path):
    """The counterpart to row 2, and why the two publications differ.

    A rotated token *must* land on top of the old one. Refusing a taken name here
    — right for a capture — would make rotation impossible.
    """
    storage = _writer(tmp_path, crypto=AesGcmCrypto(KEY))
    storage.write_artifact(b"old", scope=APPLICATION, artifact="config.json")
    storage.write_artifact(b"new", scope=APPLICATION, artifact="config.json")
    assert storage.read_artifact(scope=APPLICATION, artifact="config.json") == b"new"


def test_a_staged_credential_carries_its_mode_before_it_is_visible(tmp_path, monkeypatch):
    """Row 7 — the mode is set on the staged file, so the window is zero.

    Two corrections are folded into this test, both found by making it real.

    The first is the hazard itself. The rationale in the code said `umask 000`
    would leave a credential "briefly world-readable". Measured: it does not.
    umask only *removes* bits and `0o600` requests none for group or other, so
    `umask 000` yields exactly `0o600`. What a mask can do is strip **owner**
    bits — at `umask 200` the same open yields `0o400` — so the explicit set is
    for determinism, never for confidentiality.

    The second is the test. An earlier version monkeypatched `os.umask`, which
    replaces the function without changing the mask the kernel applies, so
    removing the `fchmod` left it green. The mask here is real, set on the
    process and restored after — and set only *after* the enclave exists,
    because `0o200` strips owner-write from directory creation too and would
    otherwise fail this test on a `mkdir` that is not what it is about.
    """
    storage = _writer(tmp_path, crypto=AesGcmCrypto(KEY))
    storage.write_artifact(b"first", scope=APPLICATION, artifact="config.json")

    observed: list[int] = []
    real_replace = os.replace

    def _observing(source, destination):
        observed.append(stat.S_IMODE(os.stat(source).st_mode))
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", _observing)
    previous = os.umask(0o200)
    try:
        storage.write_artifact(b"rotated", scope=APPLICATION, artifact="config.json")
    finally:
        os.umask(previous)

    assert observed, "the publish step never ran"
    assert observed[0] == 0o600, (
        f"the staged credential was mode {observed[0]:o} at publish time — the "
        f"umask stripped owner bits from os.open's mode and nothing put them "
        f"back before the file became visible."
    )


def test_a_short_write_does_not_truncate(tmp_path, monkeypatch):
    """Row 8 — `os.write`'s return value used to be discarded.

    Rare on a regular file, not impossible, and for a sealed artifact the
    consequence is total: AES-GCM fails its tag rather than degrading, so the
    loss is silent until the next read.
    """
    real_write = os.write

    def _one_byte_at_a_time(descriptor, payload):
        return real_write(descriptor, payload[:1])

    monkeypatch.setattr(os, "write", _one_byte_at_a_time)
    storage = _writer(tmp_path, crypto=AesGcmCrypto(KEY))
    storage.write_artifact(b'{"token": "abcdefghij"}', scope=APPLICATION, artifact="config.json")
    monkeypatch.undo()

    assert storage.read_artifact(scope=APPLICATION, artifact="config.json") == (
        b'{"token": "abcdefghij"}'
    )


def test_the_staged_file_is_fsynced_before_it_becomes_visible(tmp_path, monkeypatch):
    """An ORDERING test, and deliberately not a durability one.

    Whether the bytes truly reached stable storage cannot be observed from
    inside the process — it takes a power loss — so this asserts the only thing
    that is observable: the `fsync` happens *before* the publish, never after.
    That ordering is the whole point. Reversed, a crash can leave a visible,
    complete-looking name whose content never landed, which is the same problem
    one layer down from the one staging solves.

    Weak on its own, and worth having: without it, deleting the `fsync` leaves
    the suite entirely green.
    """
    order: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def _fsync(descriptor):
        order.append("fsync")
        return real_fsync(descriptor)

    def _replace(source, destination):
        order.append("publish")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "fsync", _fsync)
    monkeypatch.setattr(os, "replace", _replace)
    storage = _writer(tmp_path, crypto=AesGcmCrypto(KEY))
    storage.write_artifact(b"durable", scope=APPLICATION, artifact="config.json")

    assert "publish" in order, "the publish step never ran"
    assert order.index("fsync") < order.index("publish"), (
        f"the staged file was published before it was fsynced: {order}"
    )


# ── The append path is deliberately unchanged ────────────────────────────────


def test_appends_are_still_appends(tmp_path):
    """Row 9 — rename means rewriting a ledger, which AD-5 forbids.

    A reader landing mid-flush sees whole records plus a fragment; the rule is a
    parser rule, and it belongs to whoever reads a segment. What this story must
    not do is change the shape of the write.
    """
    storage = _writer(tmp_path)
    storage.append_event_log(_entry("first"), scope=PERSONAL)
    storage.append_event_log(_entry("second"), scope=PERSONAL)

    segment = storage.paths.resolve(PERSONAL, "event_log/") / f"{NOW:%Y-%m}.md"
    body = re.sub(r"evt_[0-9a-f]+", "evt_ID", segment.read_text(encoding="utf-8"))
    assert body == (
        "- [evt_ID] security actor=test detail=first\n"
        "- [evt_ID] security actor=test detail=second\n"
    )
    assert not list(segment.parent.glob("*.part")), "an append was staged; it must not be"
