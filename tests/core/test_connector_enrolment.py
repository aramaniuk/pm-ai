"""Story 8b — the credential lifecycle, one test per matrix row.

The write order is the entire point of this story, so the refusal rows assert
the **absence of files** rather than the presence of an error message. An error
message proves the code noticed; an empty `connectors/` proves it left nothing
behind. Every row runs against a real temporary root, because "nothing was
written" is a claim about a filesystem.
"""

from __future__ import annotations

import json
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pm_ai.core.connector_enrolment import (
    MalformedInstanceName,
    OrphanedCredential,
    connector_configurations,
    enrol_connector,
    stored_credentials,
)
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.storage_tiers import RESTRICTED_FILE_MODE
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import DuplicateConnector, KeyNotFound, ProbeFailed, ProbeUnreachable
from pm_ai.storage.crypto import AesGcmCrypto
from pm_ai.storage.service import StorageService

NOW = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
APPLICATION = DataScope(ScopeKind.APPLICATION)
KEY = b"K" * 32
SECRET = "glpat-not-a-real-token-0123456789"


class _NoRepository:
    """Git: no working tree here. The capture guard is not what this is about."""

    def working_tree(self, path):
        return None

    def repository_marker_above(self, path):
        return None

    def tracking(self, path, *, repository):  # pragma: no cover
        raise AssertionError("tracking asked with no working tree")


class _GitCannotAnswer:
    """A `$HOME` that *is* a repository, with git unable to say what it excludes."""

    def working_tree(self, path):
        return Path("/pretend/repository")

    def repository_marker_above(self, path):
        return Path("/pretend/repository/.git")

    def tracking(self, path, *, repository):
        raise OSError("git is not installed on this machine")


def _storage(root: Path, *, crypto=None, vcs=None) -> StorageService:
    return StorageService(
        ScopePaths.rooted(root),
        now=lambda: NOW,
        vcs=vcs or _NoRepository(),
        crypto=crypto or AesGcmCrypto(KEY),
    )


@pytest.fixture
def storage(tmp_path: Path) -> StorageService:
    return _storage(tmp_path)


def accepts(system: str, credential: str) -> str:
    """A provider that answers. Never echoes what it was given."""
    return f"{system} accepted the credential"


def rejects(system: str, credential: str) -> str:
    raise ProbeFailed(f"{system} refused the credential")


def silent(system: str, credential: str) -> str:
    raise ProbeUnreachable(f"{system} did not answer within 10s")


def _connector_files(root: Path) -> list[Path]:
    directory = root / ".pm-ai" / "connectors"
    return sorted(p for p in directory.iterdir() if p.is_file()) if directory.exists() else []


def _sealed_file(root: Path) -> Path:
    return root / ".pm-ai" / "private" / "config.json"


# ── Happy path ───────────────────────────────────────────────────────────────


def test_a_valid_credential_is_probed_sealed_and_configured(storage, tmp_path):
    answer = enrol_connector(
        storage,
        system="gitlab",
        instance="gitlab:alpha",
        credential=SECRET,
        probe=accepts,
    )
    assert "accepted" in answer
    assert stored_credentials(storage)["gitlab:alpha"]["credential"] == SECRET
    assert connector_configurations(storage) == ("gitlab:alpha",)
    assert [p.name for p in _connector_files(tmp_path)] == ["gitlab:alpha.json"]


def test_the_configuration_holds_no_credential_and_lands_at_600(storage, tmp_path):
    """The mode comes from 8f's declaration, so this asserts the declaration."""
    enrol_connector(
        storage, system="gitlab", instance="gitlab:alpha", credential=SECRET, probe=accepts
    )
    (written,) = _connector_files(tmp_path)
    body = written.read_bytes()
    assert SECRET.encode() not in body
    assert b"credential" not in body
    assert stat.S_IMODE(written.stat().st_mode) == RESTRICTED_FILE_MODE


def test_the_application_root_mode_is_untouched(storage, tmp_path):
    """A restricted file must not drag its parents into an enclave (8f's B13)."""
    root = tmp_path / ".pm-ai"
    root.mkdir(parents=True, exist_ok=True)
    before = stat.S_IMODE(root.stat().st_mode)
    enrol_connector(
        storage, system="gitlab", instance="gitlab:alpha", credential=SECRET, probe=accepts
    )
    assert stat.S_IMODE(root.stat().st_mode) == before


def test_the_first_enrolment_ever_succeeds_with_no_sealed_store(storage, tmp_path):
    """`private/config.json` does not exist; absence reads as an empty mapping."""
    assert not _sealed_file(tmp_path).exists()
    enrol_connector(
        storage, system="gitlab", instance="gitlab:alpha", credential=SECRET, probe=accepts
    )
    assert _sealed_file(tmp_path).exists()


# ── Read-modify-write ────────────────────────────────────────────────────────


def test_a_second_enrolment_keeps_the_first_credential(storage):
    """`write_artifact` replaces whole, so the obvious implementation loses one."""
    enrol_connector(
        storage, system="gitlab", instance="gitlab:alpha", credential="first", probe=accepts
    )
    enrol_connector(
        storage, system="gitlab", instance="gitlab:beta", credential="second", probe=accepts
    )
    held = stored_credentials(storage)
    assert held["gitlab:alpha"]["credential"] == "first"
    assert held["gitlab:beta"]["credential"] == "second"


def test_a_second_enrolment_keeps_unrelated_keys_in_the_sealed_store(storage):
    """The sealed file is not this story's private property."""
    storage.write_artifact(
        json.dumps({"unrelated": {"keep": True}}).encode(),
        scope=APPLICATION,
        artifact="config.json",
    )
    enrol_connector(
        storage, system="gitlab", instance="gitlab:alpha", credential=SECRET, probe=accepts
    )
    raw = storage.read_artifact(scope=APPLICATION, artifact="config.json")
    assert json.loads(raw.decode())["unrelated"] == {"keep": True}


# ── Refusals that must write nothing ─────────────────────────────────────────


def test_a_rejected_credential_writes_neither_half(storage, tmp_path):
    with pytest.raises(ProbeFailed):
        enrol_connector(
            storage, system="gitlab", instance="gitlab:alpha",
            credential=SECRET, probe=rejects,
        )
    assert _connector_files(tmp_path) == []
    assert not _sealed_file(tmp_path).exists()


def test_a_silent_provider_is_refused_distinctly_from_a_rejection(storage, tmp_path):
    with pytest.raises(ProbeUnreachable):
        enrol_connector(
            storage, system="gitlab", instance="gitlab:alpha",
            credential=SECRET, probe=silent,
        )
    assert _connector_files(tmp_path) == []
    assert not _sealed_file(tmp_path).exists()


@pytest.mark.parametrize("name", ["../graph", ".hidden", "a/b", "", "with space", "x" * 200])
def test_a_path_unsafe_instance_name_is_refused_before_the_probe(tmp_path, name):
    """Refused *before* the probe, so no orphan is possible."""
    asked = []

    def recording(system, credential):
        asked.append(system)
        return "answered"

    storage = _storage(tmp_path)
    with pytest.raises((MalformedInstanceName, ValueError)):
        enrol_connector(
            storage, system="gitlab", instance=name, credential=SECRET, probe=recording
        )
    assert asked == [], "the provider was asked before the name was judged"
    assert _connector_files(tmp_path) == []


def test_an_absent_master_key_leaves_connectors_empty(tmp_path):
    """The acceptance criterion: asserted on the filesystem, not on a message.

    This is what proves the ordering rather than describing it — the sealed
    write is the one that can refuse, and it goes first.
    """

    class NoKey:
        def encrypt(self, plaintext: bytes) -> bytes:
            raise KeyNotFound("master")

        def decrypt(self, blob: bytes) -> bytes:
            raise KeyNotFound("master")

    storage = _storage(tmp_path, crypto=NoKey())
    with pytest.raises(KeyNotFound):
        enrol_connector(
            storage, system="gitlab", instance="gitlab:alpha",
            credential=SECRET, probe=accepts,
        )
    assert _connector_files(tmp_path) == [], (
        "a connector was configured while its credential could not be sealed — "
        "which reads as a working connector harvesting nothing"
    )


def test_a_git_repository_home_with_no_git_is_refused_before_the_provider_is_asked(
    tmp_path,
):
    """What the pre-flight actually buys, measured rather than assumed.

    The spec's fear was an orphaned credential: `connectors/` is gitignored, so
    its write refuses when git cannot answer, and that refusal would land after
    the seal. In this tree it cannot — `private/` is declared gitignored too, so
    the *sealed* write refuses on the same question and no orphan is reachable
    with or without the pre-flight. Asserting "nothing was sealed" therefore
    passes either way, which is a test that proves nothing; removing
    `assert_writable` entirely left this file green until this row was rewritten.

    What the pre-flight does buy is checkable: the question is asked before the
    provider is, so a machine that cannot write does not spend a network round
    trip — and a credential is never put on the wire for an enrolment that was
    always going to refuse.
    """
    asked = []

    def recording(system: str, credential: str) -> str:
        asked.append(system)
        return "answered"

    storage = _storage(tmp_path, vcs=_GitCannotAnswer())
    with pytest.raises(OSError) as refused:
        enrol_connector(
            storage, system="gitlab", instance="gitlab:alpha",
            credential=SECRET, probe=recording,
        )
    assert not isinstance(refused.value, OrphanedCredential)
    assert asked == [], (
        "the credential was sent to the provider before pm-ai asked whether it "
        "could write the result anywhere"
    )
    assert not _sealed_file(tmp_path).exists()


# ── Duplicates, across both stores ───────────────────────────────────────────


def test_a_configured_instance_is_refused_and_its_credential_untouched(storage):
    enrol_connector(
        storage, system="gitlab", instance="gitlab:alpha", credential="first", probe=accepts
    )
    with pytest.raises(DuplicateConnector):
        enrol_connector(
            storage, system="gitlab", instance="gitlab:alpha",
            credential="second", probe=accepts,
        )
    assert stored_credentials(storage)["gitlab:alpha"]["credential"] == "first"


def test_an_orphaned_credential_is_seen_by_the_duplicate_check(storage, tmp_path):
    """`connectors/` alone misses a half-finished enrolment."""
    storage.write_artifact(
        json.dumps({"connectors": {"gitlab:alpha": {"system": "gitlab", "credential": "x"}}}).encode(),
        scope=APPLICATION,
        artifact="config.json",
    )
    assert _connector_files(tmp_path) == []
    with pytest.raises(DuplicateConnector) as refused:
        enrol_connector(
            storage, system="gitlab", instance="gitlab:alpha",
            credential=SECRET, probe=accepts,
        )
    assert "half-finished" in str(refused.value)


def test_the_unencrypted_half_is_checked_first_so_a_keyless_machine_says_so(tmp_path):
    """The refusal must be about the key, not a spurious duplicate."""

    class NoKey:
        def encrypt(self, plaintext: bytes) -> bytes:
            raise KeyNotFound("master")

        def decrypt(self, blob: bytes) -> bytes:
            raise KeyNotFound("master")

    storage = _storage(tmp_path, crypto=NoKey())
    with pytest.raises(KeyNotFound):
        enrol_connector(
            storage, system="gitlab", instance="gitlab:alpha",
            credential=SECRET, probe=accepts,
        )


# ── The credential never appears ─────────────────────────────────────────────


def test_no_refusal_message_or_traceback_carries_the_credential(storage):
    """Every refusal path, searched for five spellings of the secret."""
    spellings = [SECRET, repr(SECRET), SECRET.encode().hex(), SECRET[:8], SECRET[-8:]]

    def check(raised: BaseException) -> None:
        text = f"{raised!r} {raised}"
        cause = raised.__cause__
        while cause is not None:
            text += f" {cause!r} {cause}"
            cause = cause.__cause__
        for spelling in spellings:
            assert spelling not in text, f"{spelling!r} reached a refusal"

    with pytest.raises(ProbeFailed) as rejected:
        enrol_connector(
            storage, system="gitlab", instance="gitlab:alpha",
            credential=SECRET, probe=rejects,
        )
    check(rejected.value)

    enrol_connector(
        storage, system="gitlab", instance="gitlab:alpha", credential=SECRET, probe=accepts
    )
    with pytest.raises(DuplicateConnector) as duplicate:
        enrol_connector(
            storage, system="gitlab", instance="gitlab:alpha",
            credential=SECRET, probe=accepts,
        )
    check(duplicate.value)


def test_the_returned_sentence_never_carries_the_credential(storage):
    """A provider that echoes what it was given must not be relayed verbatim."""

    def echoing(system: str, credential: str) -> str:
        return f"{system} accepted {credential}"

    answer = enrol_connector(
        storage, system="gitlab", instance="gitlab:alpha",
        credential=SECRET, probe=echoing,
    )
    assert SECRET not in answer


# ── The orphan, reported rather than rolled back ─────────────────────────────


def test_a_failed_configuration_write_reports_the_orphan(tmp_path, monkeypatch):
    """Reported, never silent — it is only findable if this is raised."""
    storage = _storage(tmp_path)
    real = storage.write_artifact
    calls = []

    def failing(payload, *, scope, artifact, name=None):
        calls.append(artifact)
        if artifact == "connectors/":
            raise OSError("no space left on device")
        return real(payload, scope=scope, artifact=artifact, name=name)

    monkeypatch.setattr(storage, "write_artifact", failing)

    with pytest.raises(OrphanedCredential) as orphaned:
        enrol_connector(
            storage, system="gitlab", instance="gitlab:alpha",
            credential=SECRET, probe=accepts,
        )
    assert "gitlab:alpha" in str(orphaned.value)
    assert "NOT enrolled" in str(orphaned.value)
    assert SECRET not in str(orphaned.value)


# ── "Active at the next start", made to mean something ───────────────────────


def test_an_enrolled_connector_is_registered_by_a_fresh_composition(tmp_path):
    """The only assertion that makes the success message true.

    `pm-ai connector add` tells the operator the connector activates at the
    next start. Nothing read `connectors/` until story 8b wired it into
    `build()`, so that sentence described behaviour no code performed: two files
    were written and no later run ever looked at either.
    """
    from pm_ai.app.wiring import build
    from pm_ai.connectors.registry import all_connectors

    storage = _storage(tmp_path)
    enrol_connector(
        storage, system="gitlab", instance="gitlab:enrolled",
        credential=SECRET, probe=accepts,
    )

    daemon = build(tmp_path, "demo")
    assert "gitlab:enrolled" in _instances(), (
        "an enrolled connector was absent from a freshly composed registry, so "
        "`active at the next start` never becomes true"
    )
    # The registry is what `connector check` reads; `Daemon.connectors` is what
    # `run_harvest` resolves against. Registering into one and not the other
    # listed an instance that harvesting raised `KeyError` for — visible in the
    # report, unreachable by the only code that fetches anything.
    assert "gitlab:enrolled" in daemon.connectors, (
        "the connector is in the registry but not the daemon, so `connector "
        "check` lists it and `run_harvest` cannot resolve it"
    )
    assert set(_instances()) == set(daemon.connectors), (
        "the registry and the daemon disagree about which connectors exist"
    )
    assert all_connectors()


def _instances() -> tuple[str, ...]:
    from pm_ai.connectors.registry import default_registry

    return default_registry().instances()


class _Keychain:
    """Custody that answers, so composition never reaches the real keychain.

    A whole `KeychainPort`, not the one method `LazyKeyCrypto` calls: the
    parameter is typed, and a partial fake asserts a conformance it does not
    have.
    """

    def __init__(self, secret: bytes | None = KEY) -> None:
        self._secret = secret

    def store(self, name: str, secret: bytes) -> None:
        self._secret = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        from pm_ai.ports import KeyAlreadyEnrolled

        if self._secret is not None:
            raise KeyAlreadyEnrolled(name)
        self._secret = secret

    def fetch(self, name: str) -> bytes:
        if self._secret is None:
            raise KeyNotFound(name)
        return self._secret

    def delete(self, name: str) -> None:
        if self._secret is None:
            raise KeyNotFound(name)
        self._secret = None


def test_a_composed_adapter_holds_the_credential_the_sealed_store_keeps(tmp_path):
    """Story 33a — the read-back that makes "active at the next start" true.

    `8b` sealed the credential and nothing ever read it: `_enrolled_connectors`
    built every adapter with `credential=None`, so `pm-ai connector check`
    reported a connector enrolled ten seconds earlier as `ABSENT` — a fresh
    install, on a machine somebody had just finished setting up. That is
    precisely the state `8b`'s own success message promised away.

    Asserted through the health probe as well as the field, because `ABSENT` is
    what an operator actually sees and the field is only how it gets there.
    """
    from pm_ai.app.wiring import build
    from pm_ai.domain.health import Health

    storage = _storage(tmp_path)
    enrol_connector(
        storage, system="gitlab", instance="gitlab:sealed",
        credential=SECRET, probe=accepts,
    )

    daemon = build(tmp_path, "demo", keychain=_Keychain())
    connector = daemon.connectors["gitlab:sealed"]

    assert connector.credential == SECRET, (
        "the adapter was built with no credential, so a freshly enrolled "
        "connector reports ABSENT on the very next start"
    )
    assert connector.check_health().health is not Health.ABSENT


def test_the_enrolled_adapter_wins_at_the_name_the_builtin_already_holds(tmp_path):
    """The collision is the ordinary case, not a corner.

    `build()` seeds `connectors` with `gitlab:<project>`, and `gitlab.py`'s own
    ABSENT remediation tells the operator to run `pm-ai connector add gitlab
    gitlab:<project>` — the exact instance name that collides. `setdefault` then
    kept the credential-less built-in and dropped the adapter holding the sealed
    credential, so the connector they had just enrolled went on reporting ABSENT
    and printing the remedy they had already followed. Every other row here uses
    a name that cannot collide, so none of them could fail on this.
    """
    from pm_ai.app.wiring import build
    from pm_ai.domain.health import Health

    storage = _storage(tmp_path)
    enrol_connector(
        storage, system="gitlab", instance="gitlab:demo",
        credential=SECRET, probe=accepts,
    )

    daemon = build(tmp_path, "demo", keychain=_Keychain())
    connector = daemon.connectors["gitlab:demo"]

    assert connector.credential == SECRET, (
        "the built-in adapter for this project shadowed the enrolled one, so the "
        "sealed credential never reached a connector"
    )
    assert connector.check_health().health is not Health.ABSENT
    assert set(_instances()) == set(daemon.connectors)


def test_a_credential_sealed_under_another_system_is_not_handed_to_gitlab(tmp_path):
    """One instance name, two systems, is not a credential to pass along.

    A Microsoft refresh token handed to the GitLab adapter would be sent to the
    wrong provider, and the row would claim to be configured while it was not.
    `ABSENT` is the honest answer: this instance has no credential *for this
    system*.
    """
    from pm_ai.app.wiring import build
    from pm_ai.domain.health import Health

    storage = _storage(tmp_path)
    storage.write_artifact(
        json.dumps(
            {"connectors": {"gitlab:crossed": {"system": "graph", "credential": SECRET}}}
        ).encode(),
        scope=APPLICATION,
        artifact="config.json",
    )
    storage.write_artifact(
        json.dumps(
            {"instance": "gitlab:crossed", "system": "gitlab", "enabled": True}
        ).encode(),
        scope=APPLICATION,
        artifact="connectors/",
        name="gitlab:crossed.json",
    )

    daemon = build(tmp_path, "demo", keychain=_Keychain())
    connector = daemon.connectors["gitlab:crossed"]

    assert connector.credential is None
    assert connector.check_health().health is Health.ABSENT


def test_a_non_string_sealed_credential_does_not_reach_a_probe(tmp_path):
    """`private/config.json` is decrypted JSON, so the value can be anything.

    `GitLabConnectorAdapter.check_health` calls `.strip()` on it, and a number or
    a list would have raised `AttributeError` inside a probe that is required
    never to raise — which takes the whole `connector check` report down with
    it rather than reporting one bad row.
    """
    from pm_ai.app.wiring import build
    from pm_ai.domain.health import Health

    storage = _storage(tmp_path)
    storage.write_artifact(
        json.dumps(
            {"connectors": {"gitlab:odd": {"system": "gitlab", "credential": 12345}}}
        ).encode(),
        scope=APPLICATION,
        artifact="config.json",
    )
    storage.write_artifact(
        json.dumps({"instance": "gitlab:odd", "system": "gitlab", "enabled": True}).encode(),
        scope=APPLICATION,
        artifact="connectors/",
        name="gitlab:odd.json",
    )

    daemon = build(tmp_path, "demo", keychain=_Keychain())
    connector = daemon.connectors["gitlab:odd"]

    assert connector.credential is None
    assert connector.check_health().health is Health.ABSENT  # must not raise


def test_a_machine_with_no_connectors_never_opens_the_sealed_store(tmp_path, monkeypatch):
    """Opening it costs a master-key fetch from the keychain.

    Read before the loop, every `build()` on every machine paid that — including
    every machine before the first `connector add` — to produce a mapping nothing
    then consulted.
    """
    from pm_ai.app import wiring

    opened = []

    def counting(storage):
        opened.append(storage)
        return {}

    monkeypatch.setattr(wiring, "_stored_credentials", counting)

    wiring.build(tmp_path, "demo", keychain=_Keychain())
    assert opened == [], "the sealed store was opened with no connector enrolled"

    storage = _storage(tmp_path)
    enrol_connector(
        storage, system="gitlab", instance="gitlab:one",
        credential=SECRET, probe=accepts,
    )
    wiring.build(tmp_path, "demo", keychain=_Keychain())
    assert len(opened) == 1, "the sealed store was not read once a connector existed"


def test_a_connector_with_no_sealed_credential_still_reports_absent(tmp_path):
    """The read-back must not invent one for a configuration written by hand.

    `connectors/` is unencrypted and hand-editable on purpose, so an entry with
    nothing sealed against it is reachable — and it is exactly the half-finished
    state `ABSENT` exists to name.
    """
    from pm_ai.app.wiring import build
    from pm_ai.domain.health import Health

    storage = _storage(tmp_path)
    storage.write_artifact(
        json.dumps(
            {"instance": "gitlab:handwritten", "system": "gitlab", "enabled": True}
        ).encode(),
        scope=APPLICATION,
        artifact="connectors/",
        name="gitlab:handwritten.json",
    )

    daemon = build(tmp_path, "demo", keychain=_Keychain())
    connector = daemon.connectors["gitlab:handwritten"]

    assert connector.credential is None
    assert connector.check_health().health is Health.ABSENT


def test_an_unopenable_sealed_store_does_not_stop_the_daemon_composing(tmp_path):
    """`doctor` diagnoses a locked keychain, and cannot if `build()` raises.

    The cost is stated rather than hidden: the connector reports `ABSENT` rather
    than "I could not read your credential". The keychain probe is what reports
    the cause, directly, instead of through a connector row.
    """
    from pm_ai.app.wiring import build
    from pm_ai.domain.health import Health

    storage = _storage(tmp_path)
    enrol_connector(
        storage, system="gitlab", instance="gitlab:locked",
        credential=SECRET, probe=accepts,
    )

    daemon = build(tmp_path, "demo", keychain=_Keychain(secret=None))
    connector = daemon.connectors["gitlab:locked"]

    assert connector.credential is None
    assert connector.check_health().health is Health.ABSENT


def test_a_disabled_entry_is_not_registered(tmp_path):
    """`enabled: false` is the off switch the file format already declares."""
    from pm_ai.app.wiring import build

    storage = _storage(tmp_path)
    storage.write_artifact(
        json.dumps({"instance": "gitlab:off", "system": "gitlab", "enabled": False}).encode(),
        scope=APPLICATION,
        artifact="connectors/",
        name="gitlab:off.json",
    )
    build(tmp_path, "demo")
    assert "gitlab:off" not in _instances()


def test_a_malformed_entry_does_not_stop_the_daemon_composing(tmp_path):
    """`doctor` diagnoses a broken machine, and cannot if `build()` raises."""
    from pm_ai.app.wiring import build

    storage = _storage(tmp_path)
    storage.write_artifact(
        b"{ this is not json",
        scope=APPLICATION,
        artifact="connectors/",
        name="broken.json",
    )
    build(tmp_path, "demo")  # must not raise


def test_an_unrecognised_sibling_entry_is_refused_not_silently_dropped(storage):
    """Writing the mapping back would have deleted it.

    The module preserves unrelated top-level keys deliberately, and would have
    destroyed sibling *credential* entries it merely could not interpret — while
    also hiding them from both duplicate checks, so the instance would be
    overwritten rather than refused.
    """
    storage.write_artifact(
        json.dumps({"connectors": {"gitlab:odd": "a bare string", "gitlab:ok": {}}}).encode(),
        scope=APPLICATION,
        artifact="config.json",
    )
    with pytest.raises(ValueError) as refused:
        enrol_connector(
            storage, system="gitlab", instance="gitlab:new",
            credential=SECRET, probe=accepts,
        )
    assert "gitlab:odd" in str(refused.value)
    raw = storage.read_artifact(scope=APPLICATION, artifact="config.json")
    assert json.loads(raw.decode())["connectors"]["gitlab:odd"] == "a bare string"


def test_the_configuration_records_the_project_rather_than_implying_it(storage, tmp_path):
    """An instance name cannot express `group/project`; the config can."""
    enrol_connector(
        storage, system="gitlab", instance="gitlab:alpha", credential=SECRET, probe=accepts
    )
    (written,) = _connector_files(tmp_path)
    assert json.loads(written.read_text())["project"] == "alpha"


def test_a_grouped_project_is_reachable_by_editing_the_plaintext_config(tmp_path):
    """The instance stays a safe path component; the project need not be.

    A real GitLab project is `group/project`, which `_assert_nameable` refuses
    as an instance name — correctly, since it is interpolated into a path. The
    project is a separate declared field, so the two are no longer the same
    string, and `connectors/` is unencrypted precisely so this line can be
    corrected by hand.
    """
    from pm_ai.app.wiring import build

    storage = _storage(tmp_path)
    storage.write_artifact(
        json.dumps(
            {
                "instance": "gitlab:platform",
                "system": "gitlab",
                "enabled": True,
                "project": "acme/platform",
            }
        ).encode(),
        scope=APPLICATION,
        artifact="connectors/",
        name="gitlab:platform.json",
    )
    daemon = build(tmp_path, "demo")
    assert daemon.connectors["gitlab:platform"].project == "acme/platform", (
        "the adapter was built for the project the instance name implied "
        "rather than the one the configuration declared"
    )


def test_a_slash_is_still_refused_in_an_instance_name(storage):
    """The path component must stay one; only the project may hold a separator."""
    with pytest.raises(MalformedInstanceName):
        enrol_connector(
            storage, system="gitlab", instance="gitlab:acme/platform",
            credential=SECRET, probe=accepts,
        )


def test_a_malformed_system_name_says_system_not_instance(storage):
    with pytest.raises(MalformedInstanceName) as refused:
        enrol_connector(
            storage, system="git lab", instance="gitlab:alpha",
            credential=SECRET, probe=accepts,
        )
    assert "system name" in str(refused.value)
