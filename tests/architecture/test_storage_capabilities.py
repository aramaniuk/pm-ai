"""Story 8f — the three capabilities `StoragePort` declares, exercised.

The port named nine methods and neither the single writer nor the single reader,
so anything typed against it could reach every ledger and no artifact. It also
named no way to enumerate a `Collection`, which `8b`'s duplicate check and
`11a`'s `for_day` both need, and a declared-unencrypted artifact had no way to
ask for a restricted file mode — `connectors/`, one file per connector instance
beside the credential store, landed at the umask.

Every row here runs against a **real temporary root**. A fake reader returning
`b""` proves nothing about absence, and a mode is only a mode once `stat` has
seen it.
"""

from __future__ import annotations

import errno
import os
import select
import signal
import stat
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ledger_fixtures import entry as _entry
from claim_fakes import InMemoryClaims, held_by_another_thread
from pm_ai.domain.claims import LOCK_SUFFIX, ClaimHeld, ClaimReentered
from pm_ai.domain.disclosure import DISCLOSURE_LEDGER_ARTIFACT, DisclosureRecord
from pm_ai.domain.events import CommitPayload, NormalizedEvent, ObservedEventType
from pm_ai.domain.identity import Actor, DataScope, ScopeKind, SourceRef
from pm_ai.domain.storage_tiers import (
    EVENT_LOG,
    NotACollection,
    RESTRICTED_FILE_MODE,
    restricted_mode,
)
from pm_ai.platform import claims as platform_claims
from pm_ai.platform.claims import FlockClaims
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import ArtifactBusy
from pm_ai.storage.crypto import ENCLAVE_DIR_MODE, ENCRYPTED_FILE_MODE, AesGcmCrypto
from pm_ai.storage.service import AppendToSealedArtifact, StorageService

NOW = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)
APPLICATION = DataScope(ScopeKind.APPLICATION)
PERSONAL = DataScope(ScopeKind.PERSONAL)
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")
PEOPLE = DataScope(ScopeKind.PEOPLE, person_id="bob")
KEY = b"K" * 32


class _NoRepository:
    """Git says: no working tree anywhere near this path.

    The capture guard is not what this module is about, and a real repository
    would make every row depend on git's answer as well as on the trees'.
    """

    def working_tree(self, path):
        return None

    def repository_marker_above(self, path):
        return None

    def tracking(self, path, *, repository):  # pragma: no cover — never reached
        raise AssertionError("tracking asked with no working tree")


@pytest.fixture
def storage(tmp_path: Path) -> StorageService:
    return StorageService(
        ScopePaths.rooted(tmp_path),
        now=lambda: NOW,
        vcs=_NoRepository(),
        crypto=AesGcmCrypto(KEY),
        claims=InMemoryClaims(),
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ── Reading an artifact that is not there ────────────────────────────────────


def test_reading_an_absent_artifact_is_a_value_not_an_exception(storage):
    """A first run has written nothing, and that is not a failure.

    `read_artifact` ended in `path.read_bytes()`, so every caller that needed
    "not there yet" had to translate `FileNotFoundError` itself — and the one
    that forgets aborts on a machine that is merely new.
    """
    assert storage.read_artifact(scope=APPLICATION, artifact="config.toml") is None


def test_an_absent_member_of_a_collection_is_absent_too(storage):
    """The `name` form takes the same answer; `8b` reads one connector by name."""
    assert (
        storage.read_artifact(scope=APPLICATION, artifact="connectors/", name="slack.json")
        is None
    )


def test_a_directory_in_the_way_is_refused_rather_than_reported_absent(storage):
    """Absence and unreadable are two sentences with two different repairs."""
    target = storage.paths.resolve(APPLICATION, "config.toml", create=True)
    target.mkdir()
    with pytest.raises(OSError) as refused:
        storage.read_artifact(scope=APPLICATION, artifact="config.toml")
    assert not isinstance(refused.value, FileNotFoundError), (
        "a directory where a file belongs was reported as absence, which reads "
        "as a clean machine"
    )


def test_an_unreadable_artifact_propagates(storage):
    """EACCES is not absence. Root ignores the mode, so the row is skipped there."""
    if os.geteuid() == 0:  # pragma: no cover — the suite does not run as root
        pytest.skip("root bypasses the permission bits this row is about")
    written = storage.write_artifact(b"x", scope=APPLICATION, artifact="projects.toml")
    written.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            storage.read_artifact(scope=APPLICATION, artifact="projects.toml")
    finally:
        written.chmod(0o644)


def test_what_was_written_comes_back(storage):
    """The absence form must not have cost the ordinary one."""
    storage.write_artifact(b"pm_handle = 'x'\n", scope=APPLICATION, artifact="config.toml")
    assert storage.read_artifact(scope=APPLICATION, artifact="config.toml") == (
        b"pm_handle = 'x'\n"
    )


# ── Listing a collection ─────────────────────────────────────────────────────


def test_a_populated_collection_lists_its_members(storage):
    storage.write_artifact(b"{}", scope=APPLICATION, artifact="connectors/", name="slack.json")
    storage.write_artifact(b"{}", scope=APPLICATION, artifact="connectors/", name="jira.json")

    assert storage.list_collection(scope=APPLICATION, artifact="connectors/") == (
        "jira.json",
        "slack.json",
    )


def test_a_listing_hands_out_no_paths(storage):
    """`1a` made the resolver the only thing that knows where an artifact lives.

    Asserted by shape rather than by inspection: a member carrying a separator,
    a scope root, or anything but a bare name would let a caller in `core`
    compose a path and open the file itself — AD-5's single writer, routed
    around.
    """
    storage.write_artifact(b"{}", scope=APPLICATION, artifact="connectors/", name="slack.json")
    root = str(storage.paths.resolve(APPLICATION, "connectors/"))

    for member in storage.list_collection(scope=APPLICATION, artifact="connectors/"):
        assert isinstance(member, str)
        assert not isinstance(member, Path)
        assert "/" not in member and "\\" not in member, member
        assert root not in member, member


def test_an_empty_collection_lists_nothing_and_creates_nothing(storage):
    """Declared, never written to. Asking must not bring the directory about."""
    directory = storage.paths.resolve(APPLICATION, "connectors/")
    assert not directory.exists()

    assert storage.list_collection(scope=APPLICATION, artifact="connectors/") == ()
    assert not directory.exists(), (
        "asking what a collection holds created it, so the next reader finds an "
        "empty directory and believes in it"
    )


def test_the_staged_part_of_a_write_is_not_a_member(storage):
    """`_publish` stages a dot-prefixed `.part` beside the target."""
    directory = storage.paths.resolve(APPLICATION, "connectors/", create=True)
    (directory / ".slack.json.01JABC.part").write_bytes(b"half")

    assert storage.list_collection(scope=APPLICATION, artifact="connectors/") == ()


def test_listing_a_file_is_refused(storage):
    """A collection listing over a single declared file is a caller error."""
    with pytest.raises(NotACollection):
        storage.list_collection(scope=APPLICATION, artifact="config.toml")


def test_listing_a_declared_directory_is_refused(storage):
    """A `Dir`'s members are in the trees; discovering them on disk is a second copy."""
    with pytest.raises(NotACollection):
        storage.list_collection(scope=PERSONAL, artifact="rules/")


def test_listing_an_unknown_artifact_is_the_resolvers_refusal(storage):
    """Refused, and by the component that owns the answer."""
    from pm_ai.domain.scope_model import ScopeResolutionError

    with pytest.raises(ScopeResolutionError):
        storage.list_collection(scope=APPLICATION, artifact="not_an_artifact.md")


# ── The declared mode ────────────────────────────────────────────────────────


def test_a_declared_restricted_artifact_lands_at_0600(storage):
    """`connectors/` is unencrypted by design and gitignored by declaration.

    Its members sit beside the credential store and used to land at the umask,
    typically 0644, for a file whose whole content is a credential's
    neighbourhood.
    """
    written = storage.write_artifact(
        b'{"token": "s3cret"}', scope=APPLICATION, artifact="connectors/", name="slack.json"
    )

    assert _mode(written) == RESTRICTED_FILE_MODE


def test_a_restricted_write_leaves_every_parent_mode_alone(storage):
    """The naive implementation reuses the enclave path and chmods `~/.pm-ai` to 0700.

    A file-only assertion cannot see that, which is why the modes are read
    before and after: `_publish` inferred *enclave* from "a mode was asked for"
    until story 8f, so one connector file would have tightened the application
    root as a side effect nobody asked for and nobody would notice.
    """
    directory = storage.paths.resolve(APPLICATION, "connectors/", create=True)
    parents = [directory, *directory.parents][: 3]
    before = {p: _mode(p) for p in parents}

    storage.write_artifact(
        b"{}", scope=APPLICATION, artifact="connectors/", name="slack.json"
    )

    after = {p: _mode(p) for p in parents}
    assert after == before, (
        "a restricted file write changed a directory mode: "
        + ", ".join(
            f"{p} {oct(before[p])} -> {oct(after[p])}" for p in parents if before[p] != after[p]
        )
    )


def test_an_encrypted_artifact_keeps_story_1fs_modes_exactly(storage):
    """This slice adds a case; it does not reopen one.

    `private/config.json` is 0600 inside 0700 directories, because a 0600 file
    in a listable directory still publishes its name, size and mtime.
    """
    written = storage.write_artifact(
        b'{"token": "abc"}', scope=APPLICATION, artifact="config.json"
    )

    assert written.parent.name == "private"
    assert _mode(written) == ENCRYPTED_FILE_MODE
    assert _mode(written.parent) == ENCLAVE_DIR_MODE


def test_the_declared_mode_is_read_off_the_trees_not_a_second_table():
    """The rule is the `gitignored` declaration, per scope, per qualified key."""
    assert restricted_mode(ScopeKind.APPLICATION, "connectors/") == RESTRICTED_FILE_MODE
    assert restricted_mode(ScopeKind.APPLICATION, "config.json") == RESTRICTED_FILE_MODE
    assert restricted_mode(ScopeKind.APPLICATION, "config.toml") is None
    assert restricted_mode(ScopeKind.PERSONAL, "persona.md") is None


def test_a_declared_plaintext_artifact_still_answers_to_the_umask(storage):
    """`None` rather than `0o644`: an artifact that never asked keeps the operator's answer."""
    previous = os.umask(0o022)
    try:
        written = storage.write_artifact(
            b"pm_handle = 'x'\n", scope=APPLICATION, artifact="config.toml"
        )
    finally:
        os.umask(previous)

    assert _mode(written) == 0o644


# ── The exclusive claim (stories 8b and 1r) ───────────────────────────────────
#
# Every row here runs the real kernel lock (`FlockClaims`), because these are the
# tests about the lock. Everything else in the suite uses `InMemoryClaims`.


def _locked(root: Path) -> StorageService:
    return StorageService(
        ScopePaths.rooted(root),
        now=lambda: NOW,
        vcs=_NoRepository(),
        crypto=AesGcmCrypto(KEY),
        claims=FlockClaims(),
    )


@pytest.fixture
def locked(tmp_path: Path) -> StorageService:
    return _locked(tmp_path)


def _sidecar_for(storage, artifact: str) -> Path:
    target = storage.paths.resolve(APPLICATION, artifact).resolve()
    return target.with_name(target.name + LOCK_SUFFIX)


def _marker_for(storage, artifact: str) -> Path:
    target = storage.paths.resolve(APPLICATION, artifact)
    return target.parent / f".{target.name}.claim"


def test_a_second_claim_on_one_artifact_is_refused_naming_no_file_to_delete(locked):
    """The whole reason `exclusive` exists: two enrolments racing both read
    `private/config.json`, both merge, and one credential is lost.

    The refusal names the artifact and its scope and nothing to remove — a kill
    releases a kernel lock, so there is no orphan for an operator to find.

    The holder is another thread: the same thread asking again is a bug, not a
    busy file (AD-5, story 8q — the re-entry tests below).
    """
    with held_by_another_thread(
        lambda: locked.exclusive(scope=APPLICATION, artifact="config.json")
    ):
        with pytest.raises(ArtifactBusy) as held:
            with locked.exclusive(scope=APPLICATION, artifact="config.json"):
                raise AssertionError("a second claim was granted")
        # The holder is unaffected: its claim is still held.
        with pytest.raises(ArtifactBusy):
            with locked.exclusive(scope=APPLICATION, artifact="config.json"):
                raise AssertionError("the holder's claim was lost")
    message = str(held.value)
    assert message == (
        "config.json in application is claimed by another pm-ai process; "
        "nothing was changed. Run the command again."
    )
    for named in (".claim", LOCK_SUFFIX, "remov", "delete"):
        assert named not in message, f"the refusal names a file to delete: {message}"


def test_the_claim_is_released_on_the_way_out_and_the_sidecar_kept(locked):
    sidecar = _sidecar_for(locked, "config.json")
    with locked.exclusive(scope=APPLICATION, artifact="config.json"):
        assert sidecar.is_file()
    assert sidecar.is_file(), "the sidecar was deleted, which races a waiting process"
    assert sidecar.stat().st_size == 0
    with locked.exclusive(scope=APPLICATION, artifact="config.json"):
        pass  # claimable again


def test_the_claim_is_released_even_when_the_body_raises(locked):
    """A refused enrolment must not wedge every later one."""
    with pytest.raises(RuntimeError, match="the body failed"):
        with locked.exclusive(scope=APPLICATION, artifact="config.json"):
            raise RuntimeError("the body failed")
    assert _sidecar_for(locked, "config.json").is_file()
    with locked.exclusive(scope=APPLICATION, artifact="config.json"):
        pass  # claimable again


def test_a_sidecar_already_present_and_unheld_is_claimed(locked):
    sidecar = _sidecar_for(locked, "projects.toml")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.touch()
    with locked.exclusive(scope=APPLICATION, artifact="projects.toml"):
        pass


def test_two_different_artifacts_do_not_block_each_other(locked):
    with locked.exclusive(scope=APPLICATION, artifact="config.json"):
        with locked.exclusive(scope=APPLICATION, artifact="projects.toml"):
            pass


def test_two_spellings_of_one_file_share_one_lock(tmp_path):
    """Keyed on the file: a symlinked root and the plain one are one claim."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    plain, linked = _locked(real), _locked(link)

    with held_by_another_thread(
        lambda: plain.exclusive(scope=APPLICATION, artifact="config.json")
    ):
        with pytest.raises(ArtifactBusy):
            with linked.exclusive(scope=APPLICATION, artifact="config.json"):
                raise AssertionError("two spellings took two locks")


def test_the_platform_lock_on_the_resolved_path_is_the_same_claim(locked):
    """Two doors, one file: the platform lock held directly refuses the façade."""
    target = locked.paths.resolve(APPLICATION, "projects.toml")
    with held_by_another_thread(lambda: platform_claims.exclusive(target.resolve())):
        with pytest.raises(ArtifactBusy):
            with locked.exclusive(scope=APPLICATION, artifact="projects.toml"):
                raise AssertionError("the façade took a second lock")


def test_a_refused_inner_claim_names_the_inner_file(locked):
    """A refusal inside a claimed body is the inner file's, never re-reported as
    the outer one being busy: `config.json` claimed inside a `meetings/` claim."""
    config = locked.paths.resolve(APPLICATION, "config.json")
    with held_by_another_thread(lambda: platform_claims.exclusive(config)):
        with pytest.raises(ArtifactBusy) as inner:
            with locked.exclusive(scope=PERSONAL, artifact="meetings/"):
                with locked.exclusive(scope=APPLICATION, artifact="config.json"):
                    raise AssertionError("the inner claim was granted")
    assert str(inner.value).startswith("config.json in application")
    assert "meetings" not in str(inner.value)


# ── A claim is never taken twice by one process (AD-5, story 8q) ─────────────


def test_a_second_claim_by_the_holder_is_a_programming_error_naming_the_path(locked):
    """Re-entry is a bug, never `ArtifactBusy`: a busy refusal would blame a
    process that does not exist, and a retry would meet itself forever."""
    target = locked.paths.resolve(APPLICATION, "config.json")
    with locked.exclusive(scope=APPLICATION, artifact="config.json"):
        with pytest.raises(ClaimReentered) as reentered:
            with locked.exclusive(scope=APPLICATION, artifact="config.json"):
                raise AssertionError("a second claim was granted")
        assert not isinstance(reentered.value, (ArtifactBusy, ClaimHeld))
        # The holder's claim survives the refused re-entry: another thread is busy.
        with pytest.raises(ArtifactBusy):
            with held_by_another_thread(
                lambda: locked.exclusive(scope=APPLICATION, artifact="config.json")
            ):
                raise AssertionError("the holder's claim was lost")
    assert str(target.parent.resolve() / target.name) in str(reentered.value)
    assert not target.exists(), "nothing was written"
    with locked.exclusive(scope=APPLICATION, artifact="config.json"):
        pass  # released on the way out, and claimable again


def test_a_re_entry_through_another_writer_is_the_same_programming_error(tmp_path):
    """Keyed on the file across every writer in the process, not per writer."""
    first, second = _locked(tmp_path), _locked(tmp_path)
    with first.exclusive(scope=APPLICATION, artifact="projects.toml"):
        with pytest.raises(ClaimReentered):
            with second.exclusive(scope=APPLICATION, artifact="projects.toml"):
                raise AssertionError("granted")


def test_a_re_entry_is_refused_whichever_claim_port_is_wired(storage):
    """The façade checks before the port is asked, so an in-memory port — which
    answers a second claim with `ClaimHeld` — cannot turn re-entry into busy."""
    with storage.exclusive(scope=APPLICATION, artifact="config.json"):
        with pytest.raises(ClaimReentered):
            with storage.exclusive(scope=APPLICATION, artifact="config.json"):
                raise AssertionError("granted")


def test_the_platform_lock_taken_twice_by_one_thread_names_the_path(tmp_path):
    target = tmp_path / "projects.toml"
    with platform_claims.exclusive(target):
        with pytest.raises(ClaimReentered) as reentered:
            with platform_claims.exclusive(tmp_path / "." / "projects.toml"):
                raise AssertionError("granted")
    assert str(target.parent.resolve() / target.name) in str(reentered.value)
    with platform_claims.exclusive(target):
        pass  # released on the way out


def test_the_platform_lock_held_by_the_facade_refuses_a_direct_re_entry(locked):
    """Two doors, one file, one thread: the platform lock knows its own holder."""
    target = locked.paths.resolve(APPLICATION, "projects.toml")
    with locked.exclusive(scope=APPLICATION, artifact="projects.toml"):
        with pytest.raises(ClaimReentered):
            with platform_claims.exclusive(target):
                raise AssertionError("granted")


def test_a_re_entry_creates_no_directory(storage, tmp_path):
    """Checked before anything is made, so "nothing was changed" is true —
    through the façade and through the platform door alike."""
    import shutil

    target = storage.paths.resolve(APPLICATION, "config.json")
    with storage.exclusive(scope=APPLICATION, artifact="config.json"):
        shutil.rmtree(target.parent)
        with pytest.raises(ClaimReentered):
            with storage.exclusive(scope=APPLICATION, artifact="config.json"):
                raise AssertionError("granted")
        assert not target.parent.exists(), "the re-entry recreated the enclave"

    guarded = tmp_path / "elsewhere" / "nested" / "file.toml"
    with platform_claims.exclusive(guarded):
        shutil.rmtree(tmp_path / "elsewhere")
        with pytest.raises(ClaimReentered):
            with platform_claims.exclusive(guarded):
                raise AssertionError("granted")
        assert not (tmp_path / "elsewhere").exists(), "the re-entry made a directory"


def test_holds_claim_answers_for_this_thread_only(storage):
    assert not storage.holds_claim(scope=APPLICATION, artifact="config.json")
    with storage.exclusive(scope=APPLICATION, artifact="config.json"):
        assert storage.holds_claim(scope=APPLICATION, artifact="config.json")
        assert not storage.holds_claim(scope=APPLICATION, artifact="projects.toml")
    assert not storage.holds_claim(scope=APPLICATION, artifact="config.json")
    with held_by_another_thread(
        lambda: storage.exclusive(scope=APPLICATION, artifact="config.json")
    ):
        assert not storage.holds_claim(scope=APPLICATION, artifact="config.json")


# ── A marker left by an older build ──────────────────────────────────────────


def test_an_older_builds_marker_is_reported_and_not_obeyed(locked, capsys):
    marker = _marker_for(locked, "config.json")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()

    with locked.exclusive(scope=APPLICATION, artifact="config.json"):
        pass

    err = capsys.readouterr().err
    assert str(marker) in err and "delete" in err
    assert err.count("\n") == 1
    assert marker.exists(), "the marker is reported, never removed"


def test_the_marker_is_reported_before_a_refused_claim_too(locked, tmp_path, capsys):
    marker = _marker_for(locked, "config.json")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    other = _locked(tmp_path)
    config = locked.paths.resolve(APPLICATION, "config.json")

    with held_by_another_thread(lambda: platform_claims.exclusive(config)):
        with pytest.raises(ArtifactBusy):
            with other.exclusive(scope=APPLICATION, artifact="config.json"):
                raise AssertionError("granted")

    err = capsys.readouterr().err
    assert str(marker) in err and "delete" in err


def test_the_marker_is_reported_once_per_writer(locked, tmp_path, capsys):
    """Once per writer, not per run: `setup` builds a second writer and may say it twice."""
    marker = _marker_for(locked, "config.json")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()

    for _ in range(3):
        with locked.exclusive(scope=APPLICATION, artifact="config.json"):
            pass
    assert capsys.readouterr().err.count(str(marker)) == 1

    second = _locked(tmp_path)
    with second.exclusive(scope=APPLICATION, artifact="config.json"):
        pass
    assert capsys.readouterr().err.count(str(marker)) == 1


# ── Faults that are not "busy" ───────────────────────────────────────────────


class _Faulting:
    """A `ClaimPort` whose claim fails with one operating-system error."""

    def __init__(self, error: OSError) -> None:
        self.error = error

    @contextmanager
    def exclusive(self, guarded: Path):
        raise self.error
        yield  # pragma: no cover


@pytest.mark.parametrize(
    "error",
    [
        OSError(errno.ENOTSUP, "the filesystem mounted at /Volumes/x has no locks"),
        OSError(errno.EOPNOTSUPP, "the filesystem mounted at /Volumes/x has no locks"),
        PermissionError(errno.EACCES, "Permission denied"),
        IsADirectoryError(errno.EISDIR, "Is a directory"),
    ],
    ids=["ENOTSUP", "EOPNOTSUPP", "EACCES", "directory"],
)
def test_a_fault_taking_the_claim_is_the_operating_systems_and_not_busy(tmp_path, error):
    storage = StorageService(
        ScopePaths.rooted(tmp_path),
        now=lambda: NOW,
        vcs=_NoRepository(),
        crypto=AesGcmCrypto(KEY),
        claims=_Faulting(error),
    )
    with pytest.raises(OSError) as raised:
        with storage.exclusive(scope=APPLICATION, artifact="config.json"):
            raise AssertionError("claimed through a fault")
    assert raised.value is error
    assert not isinstance(raised.value, ArtifactBusy)


def test_the_platform_refuses_a_filesystem_without_locks_naming_the_mount(
    tmp_path, monkeypatch
):
    def unsupported(descriptor, operation):
        if operation & platform_claims.fcntl.LOCK_UN:
            return None
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(platform_claims.fcntl, "flock", unsupported)
    with pytest.raises(OSError) as refused:
        with platform_claims.exclusive(tmp_path / "config.json"):
            raise AssertionError("claimed without a lock")
    assert not isinstance(refused.value, ClaimHeld)
    assert refused.value.errno == errno.ENOTSUP
    assert "mounted at /" in str(refused.value)


def test_the_platform_refuses_a_sidecar_that_is_a_directory(tmp_path):
    (tmp_path / f"config.json{LOCK_SUFFIX}").mkdir()
    with pytest.raises(OSError) as refused:
        with platform_claims.exclusive(tmp_path / "config.json"):
            raise AssertionError("claimed a directory")
    assert not isinstance(refused.value, ClaimHeld)


def test_the_platform_refuses_a_sidecar_that_is_a_link(tmp_path):
    (tmp_path / "elsewhere").touch()
    (tmp_path / f"config.json{LOCK_SUFFIX}").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(OSError) as refused:
        with platform_claims.exclusive(tmp_path / "config.json"):
            raise AssertionError("followed a link")
    assert refused.value.errno == errno.ELOOP


def test_the_platform_refuses_a_sidecar_that_is_a_fifo_without_hanging(tmp_path):
    """Opening a FIFO for reading waits for a writer; `O_NONBLOCK` stops that,
    and the plain-file check then refuses it by name."""
    sidecar = tmp_path / f"config.json{LOCK_SUFFIX}"
    os.mkfifo(sidecar)
    with pytest.raises(OSError) as refused:
        with platform_claims.exclusive(tmp_path / "config.json"):
            raise AssertionError("claimed through a FIFO")
    assert not isinstance(refused.value, ClaimHeld)
    assert str(sidecar) in str(refused.value)


def test_the_platform_refuses_a_filesystem_without_a_lock_daemon_naming_the_mount(
    tmp_path, monkeypatch
):
    """`ENOLCK` is NFS with no lock daemon: the same explanation as no `flock`."""

    def no_lock_daemon(descriptor, operation):
        if operation & platform_claims.fcntl.LOCK_UN:
            return None
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(platform_claims.fcntl, "flock", no_lock_daemon)
    with pytest.raises(OSError) as refused:
        with platform_claims.exclusive(tmp_path / "config.json"):
            raise AssertionError("claimed without a lock")
    assert not isinstance(refused.value, ClaimHeld)
    assert refused.value.errno == errno.ENOLCK
    assert "mounted at /" in str(refused.value)


def test_a_symlinked_artifact_keeps_its_sidecar_in_its_own_directory(locked, tmp_path):
    """The name is not followed: the sidecar sits beside the artifact as named,
    never beside a target that may be outside the scope tree."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "creds.json").touch()
    artifact = locked.paths.resolve(APPLICATION, "config.json")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.symlink_to(outside / "creds.json")

    with locked.exclusive(scope=APPLICATION, artifact="config.json"):
        pass

    assert (artifact.parent.resolve() / f"config.json{LOCK_SUFFIX}").is_file()
    assert not (outside / f"creds.json{LOCK_SUFFIX}").exists()


def test_a_sidecar_inside_a_collection_is_not_listed_as_a_member(locked):
    """A claim on a member leaves `<member>.lock` beside it for good; it is
    structure, and a listing that reported it would name a member nobody wrote."""
    locked.write_artifact(
        b"# a record\n", scope=PERSONAL, artifact="meetings/", name="2026-09-03-a.md"
    )
    member = locked.paths.resolve(PERSONAL, "meetings/") / "2026-09-03-a.md"
    with platform_claims.exclusive(member):
        pass
    assert (member.parent.resolve() / f"2026-09-03-a.md{LOCK_SUFFIX}").is_file()

    assert locked.list_collection(scope=PERSONAL, artifact="meetings/") == (
        "2026-09-03-a.md",
    )


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_an_unreadable_sidecar_is_a_permission_fault_not_busy(locked):
    sidecar = _sidecar_for(locked, "config.json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.touch()
    sidecar.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            with locked.exclusive(scope=APPLICATION, artifact="config.json"):
                raise AssertionError("claimed an unreadable sidecar")
    finally:
        sidecar.chmod(0o600)


# ── A kill releases the claim ────────────────────────────────────────────────


HELD_WITHIN_SECONDS = 30


def test_a_holder_killed_with_sigkill_leaves_the_claim_free(tmp_path):
    """The property the marker file never had: a dead holder holds nothing."""
    child = textwrap.dedent(
        f"""
        import sys, time
        from datetime import datetime, timezone
        from pathlib import Path
        from pm_ai.domain.identity import DataScope, ScopeKind
        from pm_ai.platform.claims import FlockClaims
        from pm_ai.platform.paths import ScopePaths
        from pm_ai.storage.crypto import PlaintextCrypto
        from pm_ai.storage.service import StorageService

        class NoRepository:
            def working_tree(self, path): return None
            def repository_marker_above(self, path): return None
            def tracking(self, path, *, repository): raise AssertionError

        storage = StorageService(
            ScopePaths.rooted(Path({str(tmp_path)!r})),
            now=lambda: datetime.now(timezone.utc),
            vcs=NoRepository(),
            crypto=PlaintextCrypto(),
            claims=FlockClaims(),
        )
        with storage.exclusive(
            scope=DataScope(ScopeKind.APPLICATION), artifact="config.json"
        ):
            print("held", flush=True)
            time.sleep(60)
        """
    )
    process = subprocess.Popen(
        [sys.executable, "-c", child],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None and process.stderr is not None
        # Bounded, so a child that failed to start or stalled is reported with
        # its own stderr rather than hanging the suite on `readline`.
        ready, _, _ = select.select([process.stdout], [], [], HELD_WITHIN_SECONDS)
        line = process.stdout.readline().strip() if ready else ""
        if line != "held":
            process.kill()
            _, err = process.communicate(timeout=10)
            pytest.fail(
                f"the child never reported holding the claim (read {line!r}, "
                f"exit {process.returncode}); its stderr:\n{err}"
            )
        storage = _locked(tmp_path)
        with pytest.raises(ArtifactBusy):
            with storage.exclusive(scope=APPLICATION, artifact="config.json"):
                raise AssertionError("claimed while the child held it")
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    with storage.exclusive(scope=APPLICATION, artifact="config.json"):
        pass
    assert _sidecar_for(storage, "config.json").is_file()


def test_claiming_a_sealed_artifact_does_not_create_a_world_listable_enclave(locked):
    """A claim must not open `private/` a moment before the write would seal it.

    Stated as *create*, not *is*: `_mkdir_enclave` leaves an existing directory
    alone on purpose — its mode is its owner's decision — and `ScopePaths.rooted`
    has already made `private/` at the umask by the time any test runs. So the
    enclave is removed first, and the claim is what brings it back. The platform
    lock's own `mkdir` runs at the umask, which is why the writer makes the
    enclave first.
    """
    enclave = locked.paths.resolve(APPLICATION, "config.json").parent
    for leftover in enclave.iterdir():
        leftover.unlink()
    enclave.rmdir()
    assert not enclave.exists()

    with locked.exclusive(scope=APPLICATION, artifact="config.json"):
        assert _mode(enclave) == ENCLAVE_DIR_MODE, (
            "the claim created `private/` at the umask, so it was world-listable "
            "for the window between claiming a credential store and sealing it"
        )


# ── The declared mode reaches the other writer too ───────────────────────────


def test_a_raw_capture_lands_at_the_declared_restricted_mode(storage):
    """`transcripts/` is gitignored in every scope that holds it.

    Honouring the declaration in `write_artifact` and not in `write_capture`
    would be the selective enforcement this codebase keeps refusing — and
    dropping the mode here left every verbatim recording group- and
    world-readable with the suite still green.
    """
    written = storage.write_capture("hello world", scope=PERSONAL, name="a.txt")
    assert _mode(written) == RESTRICTED_FILE_MODE


def test_the_append_path_adopts_the_declared_mode(storage):
    """Story 1q flipped this row; story 1n's change log records the flip.

    `restricted_mode` derives from the `gitignored` declaration, so story 1n made
    it answer `0600` for the project scope's `memory/`. Until 1q, `_append` opened
    with a bare `path.open("a")` and a segment landed at the umask — this row
    pinned that, written to fail the day `_append` honoured the declaration. It
    now honours it, like `_replace` and `_create_exclusively`.

    The umask is pinned, because the assertion is about what the writer imposed
    and an ambient 0077 would make the two indistinguishable.
    """
    previous = os.umask(0o022)
    try:
        storage.append_event_log(_entry("mode"), scope=PROJECT)
    finally:
        os.umask(previous)

    segment = storage.paths.resolve(PROJECT, EVENT_LOG) / f"{NOW:%Y-%m}.md"
    assert restricted_mode(ScopeKind.PROJECT, EVENT_LOG) == RESTRICTED_FILE_MODE, (
        "the declaration is what makes this row interesting; without it there is "
        "nothing for the append path to adopt"
    )
    assert _mode(segment) == RESTRICTED_FILE_MODE, (
        "the append path no longer applies the declared mode, so a project's "
        "event-log segment lands at the umask's answer. The code is wrong, and "
        "story 1n's change-log line saying the append path adopts the "
        "declaration since 1q is now false."
    )
    assert segment.read_text(encoding="utf-8").count("\n") == 1


# ── Story 1q: every append-only ledger is created at its declared mode ───────
#
# One row per line of 1q's I/O matrix. Each sets a *real* process umask and
# restores it in `finally`: monkeypatching `os.umask` changes nothing the kernel
# applies (story 1m), so it cannot fail.


def _segment(storage, scope: DataScope) -> Path:
    return storage.paths.resolve(scope, EVENT_LOG) / f"{NOW:%Y-%m}.md"


def _under_umask(mask: int, write) -> None:
    previous = os.umask(mask)
    try:
        write()
    finally:
        os.umask(previous)


def test_a_second_entry_leaves_the_segment_at_its_declared_mode(storage):
    _under_umask(0o022, lambda: storage.append_event_log(_entry("one"), scope=PROJECT))
    _under_umask(0o022, lambda: storage.append_event_log(_entry("two"), scope=PROJECT))

    segment = _segment(storage, PROJECT)
    assert _mode(segment) == RESTRICTED_FILE_MODE
    assert segment.read_text(encoding="utf-8").count("\n") == 2


def test_a_segment_from_before_1q_keeps_its_mode(storage):
    """No `chmod` of an existing file: its mode may be the operator's decision."""
    segment = _segment(storage, PROJECT)
    segment.parent.mkdir(parents=True, exist_ok=True)
    segment.write_text("", encoding="utf-8")
    segment.chmod(0o644)

    _under_umask(0o022, lambda: storage.append_event_log(_entry("old"), scope=PROJECT))

    assert _mode(segment) == 0o644, "the append changed an existing segment's mode"
    assert segment.read_text(encoding="utf-8").count("\n") == 1


def _event(n: int) -> NormalizedEvent:
    return NormalizedEvent(
        scope=PROJECT,
        type=ObservedEventType.COMMIT_PUSHED,
        source_ref=SourceRef.parse(f"gitlab:alpha:commit:{n:08x}"),
        actor=Actor(actor_id="u_42", display_name="Ada"),
        occurred_at=datetime(2026, 9, 2, 17, 30, tzinfo=timezone.utc),
        payload=CommitPayload(sha=f"{n:08x}", message=f"change {n}", branch="main"),
    )


def test_a_segment_removed_between_create_and_reopen_is_created_again(
    storage, monkeypatch
):
    """The exclusive create is refused, then the file vanishes before the reopen.

    The old `path.open("a")` created it; the reopen alone would raise
    `FileNotFoundError`, so the writer tries the exclusive create again.
    """
    segment = _segment(storage, PROJECT)
    segment.parent.mkdir(parents=True, exist_ok=True)
    segment.write_text("", encoding="utf-8")
    segment.chmod(0o644)

    real_open = os.open
    vanished: list[bool] = []

    def _racing(path, flags, *args, **kwargs):
        if not flags & os.O_CREAT and not vanished and str(path) == str(segment):
            vanished.append(True)
            os.unlink(segment)
            raise FileNotFoundError(2, "No such file or directory", str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", _racing)
    _under_umask(0o022, lambda: storage.append_event_log(_entry("race"), scope=PROJECT))
    monkeypatch.undo()

    assert vanished == [True], "the reopen branch was never reached"
    assert _mode(segment) == RESTRICTED_FILE_MODE
    assert segment.read_text(encoding="utf-8").count("\n") == 1


@pytest.mark.parametrize("directory_exists", [False, True], ids=["no-dir", "dir"])
def test_a_harvest_batch_creates_the_segment_at_the_declared_mode(
    storage, directory_exists
):
    segment = _segment(storage, PROJECT)
    if directory_exists:
        segment.parent.mkdir(parents=True, exist_ok=True)
    else:
        assert not segment.parent.exists()
    events = tuple(_event(n) for n in range(3))

    observed: list[int] = []

    def _persist() -> None:
        observed.append(storage.persist_events(events, scope=PROJECT).persisted)

    _under_umask(0o022, _persist)

    assert observed == [3]
    assert _mode(segment) == RESTRICTED_FILE_MODE
    assert segment.read_text(encoding="utf-8").count("\n") == 3


def test_the_disclosure_ledger_is_created_at_its_declared_mode(storage):
    record = DisclosureRecord(
        at=NOW,
        task_class="summarize",
        model="claude-opus-5",
        contributing_scopes=frozenset({PERSONAL}),
        input_tokens=100,
        output_tokens=50,
        estimated_cost_usd=0.01,
    )
    _under_umask(0o022, lambda: storage.append_disclosure(record))

    ledger = storage.paths.resolve(APPLICATION, DISCLOSURE_LEDGER_ARTIFACT)
    assert ledger.name == "disclosure.md"
    assert restricted_mode(ScopeKind.APPLICATION, DISCLOSURE_LEDGER_ARTIFACT) == 0o600
    assert _mode(ledger) == RESTRICTED_FILE_MODE
    assert ledger.read_text(encoding="utf-8").count("\n") == 1


def test_a_team_members_segment_is_created_at_its_declared_mode(storage):
    _under_umask(0o022, lambda: storage.append_event_log(_entry("1:1"), scope=PEOPLE))

    assert restricted_mode(ScopeKind.PEOPLE, EVENT_LOG) == RESTRICTED_FILE_MODE
    assert _mode(_segment(storage, PEOPLE)) == RESTRICTED_FILE_MODE


def test_a_ledger_declared_without_a_mode_answers_to_the_umask(storage):
    """The personal event log's declaration names no mode; that is not an oversight."""
    _under_umask(0o022, lambda: storage.append_event_log(_entry("me"), scope=PERSONAL))

    assert restricted_mode(ScopeKind.PERSONAL, EVENT_LOG) is None
    assert _mode(_segment(storage, PERSONAL)) == 0o644


def test_an_owner_stripping_umask_cannot_lock_pm_ai_out_of_its_ledger(storage):
    """At umask 200 the creation mode alone yields 0400 and the second append fails.

    The mask is set only after the segment's directory exists, since
    `_writable_dir` creates it under the same mask, and 0200 would strip
    owner-write from the directory — a failure this row is not about.
    """
    segment = _segment(storage, PROJECT)
    segment.parent.mkdir(parents=True, exist_ok=True)

    def _twice() -> None:
        storage.append_event_log(_entry("one"), scope=PROJECT)
        storage.append_event_log(_entry("two"), scope=PROJECT)

    _under_umask(0o200, _twice)

    assert _mode(segment) == RESTRICTED_FILE_MODE
    assert segment.read_text(encoding="utf-8").count("\n") == 2


def test_a_read_only_segment_refuses_with_the_operating_systems_error(storage):
    if os.geteuid() == 0:  # pragma: no cover — the suite does not run as root
        pytest.skip("root bypasses the permission bits this row is about")
    segment = _segment(storage, PROJECT)
    segment.parent.mkdir(parents=True, exist_ok=True)
    segment.write_text("by hand\n", encoding="utf-8")
    segment.chmod(0o400)
    try:
        with pytest.raises(PermissionError) as refused:
            _under_umask(
                0o022, lambda: storage.append_event_log(_entry("no"), scope=PROJECT)
            )
        assert refused.value.filename == str(segment)
        assert segment.read_text(encoding="utf-8") == "by hand\n"
        assert _mode(segment) == 0o400
    finally:
        segment.chmod(0o644)


def test_a_ledger_declared_encrypted_is_refused_before_any_file_exists(storage):
    """`telegram_cache/` is sealed; no ledger is, so the declaration is borrowed."""
    target = storage.paths.resolve(PERSONAL, "telegram_cache/", create=True) / "x.md"
    with pytest.raises(AppendToSealedArtifact):
        storage._append(target, "line\n", declared_mode=ENCRYPTED_FILE_MODE)
    assert not target.exists()
