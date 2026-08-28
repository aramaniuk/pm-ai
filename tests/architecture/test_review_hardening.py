"""Regressions from the 2026-08-28 story-1 code review, pinned.

Each test here exists because the behaviour it asserts was demonstrated absent:
a traversal through the artifact entry points, a git guard answering by
spelling rather than by node, a ledger replaceable whole, a refused write
leaving its directory behind, a staged file surviving a failed write, a settle
that never checked it settled anything, and a cipher whose `repr` printed the
master key. They live together because they share one review, not one module —
each names the code it pins.
"""

from __future__ import annotations

import errno
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.scope_model import ADDRESS
from pm_ai.domain.storage_tiers import (
    UnprotectedCaptureDir,
    gitignore_rule_for,
    is_append_only,
    requires_git_exclusion,
)
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import KeyNotFound
from pm_ai.storage.crypto import AesGcmCrypto, LazyKeyCrypto
from pm_ai.storage.service import (
    AppendOnlyArtifact,
    MalformedCaptureName,
    ReconciliationRequired,
    StorageService,
)

NOW = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)
PERSONAL = DataScope(ScopeKind.PERSONAL)
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")
KEY = b"K" * 32


class _NoRepository:
    """Git says: no working tree anywhere near this path."""

    def working_tree(self, path):
        return None

    def repository_marker_above(self, path):
        return None

    def tracking(self, path, *, repository):  # pragma: no cover — never reached
        raise AssertionError("tracking asked with no working tree")


def _writer(tmp_path: Path, *, crypto=None) -> StorageService:
    return StorageService(
        ScopePaths.rooted(tmp_path),
        now=lambda: NOW,
        vcs=_NoRepository(),
        crypto=crypto or AesGcmCrypto(KEY),
    )


# ── The artifact entry points validate their `name` ───────────────────────────


@pytest.mark.parametrize("name", ["../escape.md", "a/b.md", "a\\b.md", ".hidden", "x\ny"])
def test_write_artifact_refuses_a_name_that_is_not_one_component(tmp_path, name):
    """`target / name` with a traversal writes outside the directory every
    guard above just answered for — the same reason `_capture_name` exists,
    applied to the entry point that skipped it."""
    storage = _writer(tmp_path)
    with pytest.raises(MalformedCaptureName):
        storage.write_artifact(b"x", scope=PERSONAL, artifact="telegram_cache/", name=name)


def test_read_artifact_refuses_the_same_names(tmp_path):
    storage = _writer(tmp_path)
    with pytest.raises(MalformedCaptureName):
        storage.read_artifact(scope=PERSONAL, artifact="telegram_cache/", name="../../secret")


def test_nothing_landed_outside_the_scope_during_the_refusals(tmp_path):
    storage = _writer(tmp_path)
    with pytest.raises(MalformedCaptureName):
        storage.write_artifact(b"x", scope=PERSONAL, artifact="telegram_cache/", name="../leak.md")
    stray = [p for p in tmp_path.rglob("leak.md")]
    assert not stray, f"the refused name reached the filesystem: {stray}"


# ── The git guard answers by node, not by spelling ────────────────────────────


def test_every_spelling_of_one_node_gets_one_git_answer():
    """The resolver accepts several spellings per node; the guard used to know
    only the canonical one, so `private/telegram_cache/` skipped a check that
    `telegram_cache/` received — the same directory, two verdicts."""
    for kind, index in ADDRESS.items():
        answers_by_node: dict[int, set[bool]] = {}
        for spelling in index:
            node = id(index[spelling].node)
            answers_by_node.setdefault(node, set()).add(requires_git_exclusion(kind, spelling))
        disagreeing = {n for n, answers in answers_by_node.items() if len(answers) > 1}
        assert not disagreeing, f"{kind}: one node, two git answers, by spelling alone"


def test_the_demonstrated_bypasses_are_closed():
    assert requires_git_exclusion(ScopeKind.PERSONAL, "private/telegram_cache/")
    assert requires_git_exclusion(ScopeKind.PERSONAL, "transcripts/temp/")
    assert requires_git_exclusion(ScopeKind.PROJECT, "transcripts/temp/")


# ── Ledgers cannot be replaced whole ──────────────────────────────────────────


def test_replacing_a_ledger_whole_is_refused(tmp_path):
    storage = _writer(tmp_path)
    with pytest.raises(AppendOnlyArtifact):
        storage.write_artifact(b"one line\n", scope=PROJECT, artifact="commitments_log.md")
    with pytest.raises(AppendOnlyArtifact):
        storage.write_artifact(b"x", scope=PERSONAL, artifact="event_log/", name="2026-08.md")


def test_the_append_set_is_the_contract_verbatim():
    assert is_append_only(ScopeKind.PROJECT, "commitments_log.md")
    assert is_append_only(ScopeKind.PERSONAL, "event_log/")
    assert not is_append_only(ScopeKind.PERSONAL, "telegram_cache/")


# ── A refused sealed write leaves nothing behind ──────────────────────────────


class _EmptyKeychain:
    def fetch(self, name: str) -> bytes:
        raise KeyNotFound(f"no secret is stored under {name!r}")

    def store(self, name: str, secret: bytes) -> None:  # pragma: no cover
        raise AssertionError("nothing here stores")

    def delete(self, name: str) -> None:  # pragma: no cover
        raise AssertionError("nothing here deletes")


def test_a_write_refused_for_a_missing_key_creates_no_directory(tmp_path):
    """Story 1f's AC verbatim: neither the file nor its directory is created.

    The cipher runs before the first `mkdir` now; resolving with `create=True`
    first used to leave `private/telegram_cache/` behind on every refusal."""
    storage = _writer(tmp_path, crypto=LazyKeyCrypto(_EmptyKeychain(), "master"))
    target_dir = storage.paths.resolve(PERSONAL, "telegram_cache/")
    with pytest.raises(KeyNotFound):
        storage.write_artifact(b"x", scope=PERSONAL, artifact="telegram_cache/", name="state.json")
    assert not target_dir.exists(), "the refused write left its directory behind"
    assert not target_dir.parent.exists(), "the refused write left the enclave behind"


def test_every_directory_created_for_an_enclave_write_is_0700(tmp_path):
    """1f's matrix row: `mkdir(parents=True, mode=0o700)` sets the mode on the
    last directory only — the `private/` created along the way used to land at
    umask default, publishing the names and mtimes the enclave hides."""
    storage = _writer(tmp_path)
    written = storage.write_artifact(
        b"x", scope=PERSONAL, artifact="telegram_cache/", name="state.json"
    )
    for directory in (written.parent, written.parent.parent):
        mode = stat.S_IMODE(directory.stat().st_mode)
        assert mode == 0o700, f"{directory} is {oct(mode)}, not 0o700"


# ── A failed write phase leaves no staged file ────────────────────────────────


def test_a_write_that_fails_before_publishing_unlinks_its_staged_file(tmp_path, monkeypatch):
    """ENOSPC mid-write used to orphan a dot-prefixed `.part` no listing shows
    and no implemented sweep reaches — the cleanup only wrapped the publish."""
    storage = _writer(tmp_path)

    def refuse(_descriptor):
        raise OSError(errno.ENOSPC, "no space left on device")

    monkeypatch.setattr("pm_ai.storage.service.os.fsync", refuse)
    with pytest.raises(OSError):
        storage.write_capture("09:01 alex: hello\n", scope=PERSONAL, name="meet.md")
    leftovers = [p for p in tmp_path.rglob(".*.part")]
    assert not leftovers, f"staged files survived the failure: {leftovers}"


# ── Settling is checked, and the key never reaches a repr ─────────────────────


def test_settling_an_unclaimed_key_is_refused(tmp_path):
    storage = _writer(tmp_path)
    with pytest.raises(ReconciliationRequired):
        storage.settle_execution("never-claimed", "note_1")


def test_the_master_key_never_appears_in_a_repr():
    """A dataclass's generated `__repr__` includes every field: any traceback
    or pytest failure rendering the cipher printed the AES key."""
    cipher = AesGcmCrypto(KEY)
    assert "KKKK" not in repr(cipher)

    class _Keychain:
        def fetch(self, name: str) -> bytes:
            return KEY

    lazy = LazyKeyCrypto(_Keychain(), "master")
    lazy.encrypt(b"prime the cache")
    assert "KKKK" not in repr(lazy), "the cached cipher's repr leaks through the wrapper"


# ── The rule derivation cannot escape as a bare ValueError ────────────────────


def test_an_unrelatable_target_is_a_typed_refusal():
    with pytest.raises(UnprotectedCaptureDir, match="does not sit under"):
        gitignore_rule_for(Path("/somewhere/else"), repository=Path("/repo"))
