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

import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.storage_tiers import (
    NotACollection,
    RESTRICTED_FILE_MODE,
    restricted_mode,
)
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import ArtifactBusy
from pm_ai.storage.crypto import ENCLAVE_DIR_MODE, ENCRYPTED_FILE_MODE, AesGcmCrypto
from pm_ai.storage.service import StorageService

NOW = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)
APPLICATION = DataScope(ScopeKind.APPLICATION)
PERSONAL = DataScope(ScopeKind.PERSONAL)
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


# ── The exclusive claim (story 8b's read-modify-write) ───────────────────────


def _claim_for(storage, artifact: str) -> Path:
    target = storage.paths.resolve(APPLICATION, artifact)
    return target.parent / f".{target.name}.claim"


def test_a_second_claim_on_one_artifact_is_refused(storage):
    """The whole reason `exclusive` exists, and nothing asserted it.

    Two enrolments racing both read `private/config.json`, both merge, and one
    credential is lost. Dropping `O_EXCL` made the claim decorative and left the
    entire suite green, because every other test claims sequentially.
    """
    with storage.exclusive(scope=APPLICATION, artifact="config.json"):
        with pytest.raises(ArtifactBusy) as held:
            with storage.exclusive(scope=APPLICATION, artifact="config.json"):
                raise AssertionError("a second claim was granted")
    message = str(held.value)
    assert "config.json" in message
    assert ".claim" in message, "the claim path must be named, or a kill wedges the machine"


def test_the_claim_is_released_on_the_way_out(storage):
    claim = _claim_for(storage, "config.json")
    with storage.exclusive(scope=APPLICATION, artifact="config.json"):
        assert claim.exists()
    assert not claim.exists()


def test_the_claim_is_released_even_when_the_body_raises(storage):
    """A refused enrolment must not wedge every later one."""
    claim = _claim_for(storage, "config.json")
    with pytest.raises(RuntimeError):
        with storage.exclusive(scope=APPLICATION, artifact="config.json"):
            raise RuntimeError("the body failed")
    assert not claim.exists()
    with storage.exclusive(scope=APPLICATION, artifact="config.json"):
        pass  # claimable again


def test_two_different_artifacts_do_not_block_each_other(storage):
    with storage.exclusive(scope=APPLICATION, artifact="config.json"):
        with storage.exclusive(scope=APPLICATION, artifact="projects.toml"):
            pass


def test_claiming_a_sealed_artifact_does_not_create_a_world_listable_enclave(storage):
    """A claim must not open `private/` a moment before the write would seal it.

    Stated as *create*, not *is*: `_mkdir_enclave` leaves an existing directory
    alone on purpose — its mode is its owner's decision — and `ScopePaths.rooted`
    has already made `private/` at the umask by the time any test runs. So the
    enclave is removed first, and the claim is what brings it back.
    """
    enclave = _claim_for(storage, "config.json").parent
    for leftover in enclave.iterdir():
        leftover.unlink()
    enclave.rmdir()
    assert not enclave.exists()

    with storage.exclusive(scope=APPLICATION, artifact="config.json"):
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
