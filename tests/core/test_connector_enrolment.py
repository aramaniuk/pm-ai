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

from claim_fakes import InMemoryClaims, held_by_another_thread
from gitlab_fakes import Wire, page
from pm_ai.core.connector_enrolment import (
    USER_ID_KEY,
    ClaimNotHeld,
    ConnectorNotConfigured,
    CredentialNotHeld,
    MalformedInstanceName,
    MalformedSettings,
    NotAGraphRow,
    OrphanedCredential,
    assert_enrollable,
    connector_configurations,
    enrol_connector,
    record_user_id,
    recorded_user_id,
    redacted,
    replace_credential,
    stored_credentials,
)
from pm_ai.domain.claims import ClaimReentered
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
        claims=InMemoryClaims(),
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
    """A write never changes an existing folder's mode (8f's B13).

    Since story 1s the daemon's start-up walk does tighten an existing
    `~/.pm-ai`; a write still leaves every folder it did not create alone.
    """
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
    """The only assertion that makes the success message true — and story 8k's row.

    `pm-ai connector add` tells the operator the connector activates at the
    next start. Nothing read `connectors/` until story 8b wired it into
    `build()`, so that sentence described behaviour no code performed: two files
    were written and no later run ever looked at either.

    Since 8k the connector `connector check` probes is the *same object* a
    harvest resolves. Until then the check read a process-wide copy, and the
    copy once listed an instance `run_harvest` raised `KeyError` for.
    """
    from pm_ai.app import entry
    from pm_ai.app.wiring import build
    from pm_ai.connectors.registry import ConnectorRegistry
    from pm_ai.domain.health import EnrolmentReading, Report
    from pm_ai.platform.doctor import ArtifactState

    storage = _storage(tmp_path)
    enrol_connector(
        storage, system="gitlab", instance="gitlab:enrolled",
        credential=SECRET, probe=accepts,
    )

    daemon = build(tmp_path, "demo")
    # `run_harvest(daemon, instance)` resolves `daemon.connectors[instance]`, so
    # this membership is what "reachable by a harvest" means.
    assert "gitlab:enrolled" in _instances(daemon), (
        "an enrolled connector was absent from a freshly composed daemon, so "
        "`active at the next start` never becomes true"
    )

    # What `connector check` would probe, captured without contacting GitLab:
    # the enrolled adapter holds a credential, so a real probe would go out.
    probed: dict[str, object] = {}

    class Recording(ConnectorRegistry):
        def check_health(self, **kwargs) -> Report:
            probed.update(zip(self.instances(), self.all_connectors()))
            return Report(())

    composed = entry._Composition(
        daemon,
        None,
        ArtifactState.absent(),
        ArtifactState.absent(),
        EnrolmentReading("connectors/", (), (), (), (), None),
    )
    probe = entry._probe_connectors(composed, registry=Recording)
    assert probe is not None
    probe()

    assert set(probed) == set(daemon.connectors), (
        "`connector check` and the daemon disagree about which connectors exist"
    )
    assert probed["gitlab:enrolled"] is daemon.connectors["gitlab:enrolled"], (
        "`connector check` probed a different object from the one a harvest "
        "would use — two lists again, however equal they look today"
    )


def _instances(daemon) -> tuple[str, ...]:
    """The instances a daemon holds — the one list a harvest and the check read."""
    return tuple(daemon.connectors)


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
    # A recorded answer stands in for GitLab: since story 8n a health check
    # with a credential asks the real host, and no test opens a socket.
    wire = Wire(page())
    connector.transport = wire
    assert connector.check_health().health is Health.OK
    assert wire.sent[0].headers["PRIVATE-TOKEN"] == SECRET, "the sealed token is the one sent"


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
    connector.transport = Wire(page())
    assert connector.check_health().health is Health.OK


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
            {"instance": "gitlab:crossed", "system": "gitlab", "enabled": True, "project": "demo"}
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
        json.dumps({"instance": "gitlab:odd", "system": "gitlab", "enabled": True, "project": "demo"}).encode(),
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
            {"instance": "gitlab:handwritten", "system": "gitlab", "enabled": True, "project": "demo"}
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
    daemon = build(tmp_path, "demo")
    assert "gitlab:off" not in _instances(daemon)


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


# ── Story 8j: replacing one enrolled credential ──────────────────────────────


def test_replacing_one_credential_keeps_every_other_entry_and_key(storage):
    """The file holds every connector's credential; a write-back touches one."""
    storage.write_artifact(
        json.dumps({"unrelated": {"keep": True}}).encode(),
        scope=APPLICATION,
        artifact="config.json",
    )
    enrol_connector(
        storage, system="gitlab", instance="gitlab:alpha", credential="first", probe=accepts
    )
    enrol_connector(
        storage, system="graph", instance="graph:work", credential="old", probe=accepts
    )

    assert replace_credential(
        storage, system="graph", instance="graph:work", credential="new"
    ) is True

    document = json.loads(
        storage.read_artifact(scope=APPLICATION, artifact="config.json").decode()
    )
    assert document["unrelated"] == {"keep": True}
    assert document["connectors"] == {
        "gitlab:alpha": {"system": "gitlab", "credential": "first"},
        "graph:work": {"system": "graph", "credential": "new"},
    }


def test_replacing_with_the_same_credential_does_not_rewrite_the_file(storage, tmp_path):
    enrol_connector(
        storage, system="graph", instance="graph:work", credential="same", probe=accepts
    )
    before = _sealed_file(tmp_path).read_bytes()

    assert replace_credential(
        storage, system="graph", instance="graph:work", credential="same"
    ) is False
    assert _sealed_file(tmp_path).read_bytes() == before


def test_replacing_a_credential_nobody_enrolled_is_refused_and_writes_nothing(
    storage, tmp_path
):
    enrol_connector(
        storage, system="gitlab", instance="gitlab:alpha", credential="first", probe=accepts
    )
    before = _sealed_file(tmp_path).read_bytes()

    with pytest.raises(CredentialNotHeld) as refused:
        replace_credential(storage, system="graph", instance="graph:work", credential="new")

    assert "graph:work" in str(refused.value)
    assert _sealed_file(tmp_path).read_bytes() == before


def test_replacing_a_credential_sealed_under_another_system_is_refused(storage, tmp_path):
    enrol_connector(
        storage, system="gitlab", instance="shared", credential="first", probe=accepts
    )
    before = _sealed_file(tmp_path).read_bytes()

    with pytest.raises(CredentialNotHeld) as refused:
        replace_credential(storage, system="graph", instance="shared", credential="new")

    assert "shared" in str(refused.value)
    assert _sealed_file(tmp_path).read_bytes() == before


def test_replacing_takes_the_claim_enrolment_takes(storage):
    """One lock for the file: a held claim refuses the write-back as well."""
    from pm_ai.ports import ArtifactBusy

    enrol_connector(
        storage, system="graph", instance="graph:work", credential="old", probe=accepts
    )
    with held_by_another_thread(
        lambda: storage.exclusive(scope=APPLICATION, artifact="config.json")
    ):
        with pytest.raises(ArtifactBusy):
            replace_credential(
                storage, system="graph", instance="graph:work", credential="new"
            )
    assert stored_credentials(storage)["graph:work"]["credential"] == "old"


# ── Story 8m — a sign-in writes an absent entry for a configured instance ────


def _row_only(storage, instance: str = "graph:work") -> None:
    storage.write_artifact(
        json.dumps({"instance": instance, "system": "graph"}).encode(),
        scope=APPLICATION,
        artifact="connectors/",
        name=f"{instance}.json",
    )


def test_a_configured_instance_with_no_credentials_file_gets_one(storage, tmp_path):
    _row_only(storage)
    assert not _sealed_file(tmp_path).exists()

    assert replace_credential(
        storage, system="graph", instance="graph:work", credential="new", configured=True
    ) is True
    assert stored_credentials(storage) == {
        "graph:work": {"system": "graph", "credential": "new"}
    }


def test_a_configured_instance_missing_from_the_file_is_written_beside_the_others(
    storage, tmp_path
):
    enrol_connector(
        storage, system="gitlab", instance="gitlab:alpha", credential="first", probe=accepts
    )
    _row_only(storage)

    replace_credential(
        storage, system="graph", instance="graph:work", credential="new", configured=True
    )
    assert stored_credentials(storage) == {
        "gitlab:alpha": {"system": "gitlab", "credential": "first"},
        "graph:work": {"system": "graph", "credential": "new"},
    }


def test_an_absent_entry_with_no_row_is_still_refused(storage, tmp_path):
    with pytest.raises(ConnectorNotConfigured):
        replace_credential(
            storage, system="graph", instance="graph:work", credential="new", configured=True
        )
    assert not _sealed_file(tmp_path).exists()


def test_a_configured_instance_sealed_under_another_system_is_still_refused(
    storage, tmp_path
):
    enrol_connector(
        storage, system="gitlab", instance="shared", credential="first", probe=accepts
    )
    before = _sealed_file(tmp_path).read_bytes()
    with pytest.raises(CredentialNotHeld):
        replace_credential(
            storage, system="graph", instance="shared", credential="new", configured=True
        )
    assert _sealed_file(tmp_path).read_bytes() == before


def test_a_configured_instance_whose_row_is_another_system_is_refused(storage, tmp_path):
    storage.write_artifact(
        json.dumps({"instance": "graph:work", "system": "gitlab"}).encode(),
        scope=APPLICATION,
        artifact="connectors/",
        name="graph:work.json",
    )
    with pytest.raises(CredentialNotHeld) as refused:
        replace_credential(
            storage, system="graph", instance="graph:work", credential="new", configured=True
        )
    assert "'gitlab'" in str(refused.value)
    assert not _sealed_file(tmp_path).exists()


def test_the_replaceable_check_accepts_no_entry_and_no_file(storage, tmp_path):
    from pm_ai.core.connector_enrolment import assert_replaceable

    assert_replaceable(storage, system="graph", instance="graph:work")
    assert not _sealed_file(tmp_path).exists()
    assert _connector_files(tmp_path) == []


def test_the_replaceable_check_accepts_this_system_s_entry(storage):
    from pm_ai.core.connector_enrolment import assert_replaceable

    enrol_connector(
        storage, system="graph", instance="graph:work", credential="old", probe=accepts
    )
    assert_replaceable(storage, system="graph", instance="graph:work")


def test_the_replaceable_check_refuses_another_system_s_entry(storage, tmp_path):
    from pm_ai.core.connector_enrolment import assert_replaceable

    enrol_connector(storage, system="gitlab", instance="shared", credential="x", probe=accepts)
    before = _sealed_file(tmp_path).read_bytes()
    with pytest.raises(CredentialNotHeld) as refused:
        assert_replaceable(storage, system="graph", instance="shared")
    assert "'gitlab'" in str(refused.value) and "'graph'" in str(refused.value)
    assert _sealed_file(tmp_path).read_bytes() == before


def test_the_replaceable_check_refuses_a_name_that_cannot_be_a_row(tmp_path):
    from pm_ai.core.connector_enrolment import assert_replaceable

    storage = _storage(tmp_path)
    with pytest.raises(MalformedInstanceName):
        assert_replaceable(storage, system="graph", instance="../graph")


def test_connector_row_reads_one_row(storage):
    from pm_ai.core.connector_enrolment import connector_row

    _row_only(storage)
    assert connector_row(storage, instance="graph:work") == {
        "instance": "graph:work",
        "system": "graph",
    }


@pytest.mark.parametrize(
    ("raw", "refusal"),
    [(None, ConnectorNotConfigured), (b"{not json", MalformedSettings), (b"[1]", MalformedSettings)],
    ids=["absent", "not json", "not an object"],
)
def test_connector_row_refuses_what_is_not_a_row(storage, raw, refusal):
    from pm_ai.core.connector_enrolment import connector_row

    if raw is not None:
        storage.write_artifact(
            raw, scope=APPLICATION, artifact="connectors/", name="graph:work.json"
        )
    with pytest.raises(refusal) as refused:
        connector_row(storage, instance="graph:work", purpose="it judged")
    assert "graph:work" in str(refused.value) and "it judged" in str(refused.value)


@pytest.mark.parametrize(
    "document", [b"[1]", b'{"connectors": []}', b'{"connectors": {"a": 1}}', b"{nope"]
)
def test_a_malformed_sealed_store_is_a_named_refusal_naming_the_file(storage, document):
    storage.write_artifact(document, scope=APPLICATION, artifact="config.json")
    with pytest.raises(MalformedSettings) as refused:
        stored_credentials(storage)
    assert "config.json" in str(refused.value)


# ── Story 8l — the duplicate check on its own, and a row's own settings ──────


def test_the_duplicate_check_runs_on_its_own_and_writes_nothing(storage, tmp_path):
    """A Graph add refuses a name in use before it asks anything (story 8l)."""
    assert_enrollable(storage, system="graph", instance="graph:work")
    assert _connector_files(tmp_path) == []
    assert not _sealed_file(tmp_path).exists()

    enrol_connector(
        storage, system="graph", instance="graph:work", credential=SECRET, probe=accepts
    )
    with pytest.raises(DuplicateConnector):
        assert_enrollable(storage, system="graph", instance="graph:work")
    with pytest.raises(MalformedInstanceName):
        assert_enrollable(storage, system="graph", instance="../graph")


def test_settings_are_written_into_the_row_beside_the_keys_enrolment_owns(
    storage, tmp_path
):
    settings = {"client_id": "an-app-id", "window_width_minutes": 1440}
    enrol_connector(
        storage,
        system="graph",
        instance="graph:work",
        credential=SECRET,
        probe=accepts,
        settings=settings,
    )
    (written,) = _connector_files(tmp_path)
    row = json.loads(written.read_text())
    assert row == {
        "instance": "graph:work",
        "system": "graph",
        "enabled": True,
        "project": "work",
        **settings,
    }
    assert SECRET not in written.read_text()


@pytest.mark.parametrize("key", ["instance", "system", "enabled", "project"])
def test_a_setting_naming_a_key_enrolment_owns_is_refused_before_anything(
    storage, tmp_path, key
):
    asked: list[str] = []

    def probe(system: str, credential: str) -> str:
        asked.append(system)
        return "accepted"

    with pytest.raises(MalformedSettings, match=key):
        enrol_connector(
            storage,
            system="graph",
            instance="graph:work",
            credential=SECRET,
            probe=probe,
            settings={key: "something else"},
        )
    assert asked == []
    assert _connector_files(tmp_path) == []
    assert not _sealed_file(tmp_path).exists()


def test_the_project_parameter_is_the_rows_project_and_settings_still_cannot_name_it(
    storage, tmp_path
):
    """Story 8o: a GitLab path travels as a parameter, so the clash rule holds.

    `project` stays reserved against `settings`; the parameter is the one way
    a caller writes a path the instance name cannot hold.
    """
    enrol_connector(
        storage,
        system="gitlab",
        instance="gitlab:alpha",
        credential=SECRET,
        probe=accepts,
        settings={"host": "https://git.internal/gitlab"},
        project="acme/web",
    )
    (written,) = _connector_files(tmp_path)
    assert json.loads(written.read_text()) == {
        "instance": "gitlab:alpha",
        "system": "gitlab",
        "enabled": True,
        "project": "acme/web",
        "host": "https://git.internal/gitlab",
    }

    with pytest.raises(MalformedSettings, match="project"):
        enrol_connector(
            storage,
            system="gitlab",
            instance="gitlab:beta",
            credential=SECRET,
            probe=accepts,
            settings={"project": "acme/other"},
            project="acme/web",
        )


@pytest.mark.parametrize("value", [object(), float("nan"), float("inf")])
def test_a_setting_that_cannot_be_written_is_refused_before_the_seal(
    storage, tmp_path, value
):
    """Encoded first, so a value the row cannot hold orphans no credential."""
    asked: list[str] = []

    def probe(system: str, credential: str) -> str:
        asked.append(system)
        return "accepted"

    with pytest.raises(MalformedSettings):
        enrol_connector(
            storage,
            system="graph",
            instance="graph:work",
            credential=SECRET,
            probe=probe,
            settings={"client_id": value},
        )
    assert asked == []
    assert _connector_files(tmp_path) == []
    assert not _sealed_file(tmp_path).exists()


def test_the_up_front_check_asks_for_the_master_key(tmp_path):
    """A fresh machine has no sealed store to read, so nothing asked for the key."""

    class Keyless:
        def fetch(self, name):
            raise KeyNotFound(name)

    from pm_ai.storage.crypto import LazyKeyCrypto

    storage = _storage(tmp_path, crypto=LazyKeyCrypto(Keyless(), "master"))
    with pytest.raises(KeyNotFound):
        assert_enrollable(storage, system="graph", instance="graph:work")
    assert _connector_files(tmp_path) == []
    assert not _sealed_file(tmp_path).exists()


def test_redaction_can_say_something_other_than_accepted():
    assert redacted(f"refused: {SECRET}", SECRET, instead="withheld") == "withheld"
    assert redacted("refused plainly", SECRET, instead="withheld") == "refused plainly"


# ── Story 8q — the row records which user signed in ──────────────────────────

GRAPH = "graph:work"
PM = "0d9e3c4f-1a2b-4c3d-8e9f-a0b1c2d3e4f5"  # a Graph user id is a GUID


def _graph_row_file(root: Path) -> Path:
    return root / ".pm-ai" / "connectors" / f"{GRAPH}.json"


def _enrol_graph(storage, root: Path, **settings: object) -> Path:
    enrol_connector(
        storage,
        system="graph",
        instance=GRAPH,
        credential="sealed",
        probe=accepts,
        settings={"client_id": "an-app", "tenant": "organizations", **settings},
    )
    return _graph_row_file(root)


def test_the_user_id_key_is_spelled_once_and_the_graph_package_uses_it():
    from pm_ai.connectors.graph import USER_ID_KEY as graphs

    assert USER_ID_KEY == graphs == "user_id"


def test_recording_the_user_keeps_a_hand_added_mapping_and_every_other_key(
    storage, tmp_path
):
    row_file = _enrol_graph(storage, tmp_path)
    row = json.loads(row_file.read_text())
    row["categories"] = {"Alpha": "alpha"}
    row_file.write_text(json.dumps(row))  # a hand edit, in the PM's own formatting

    assert record_user_id(storage, instance=GRAPH, user_id=PM) is True

    after = json.loads(row_file.read_text())
    assert after == {**row, USER_ID_KEY: PM}, "a key other than user_id changed"
    assert after["categories"] == {"Alpha": "alpha"}


def test_recording_the_same_user_again_changes_no_bytes(storage, tmp_path):
    row_file = _enrol_graph(storage, tmp_path)
    assert record_user_id(storage, instance=GRAPH, user_id=PM) is True
    before = row_file.read_bytes()

    assert record_user_id(storage, instance=GRAPH, user_id=PM) is False
    assert row_file.read_bytes() == before


def test_recording_another_user_replaces_only_that_key(storage, tmp_path):
    row_file = _enrol_graph(storage, tmp_path, user_id="the-first-user")
    before = json.loads(row_file.read_text())

    assert record_user_id(storage, instance=GRAPH, user_id=PM) is True
    assert json.loads(row_file.read_text()) == {**before, USER_ID_KEY: PM}


def test_recording_the_user_of_an_unconfigured_instance_writes_nothing(storage, tmp_path):
    with pytest.raises(ConnectorNotConfigured) as refused:
        record_user_id(storage, instance=GRAPH, user_id=PM)
    assert GRAPH in str(refused.value)
    assert _connector_files(tmp_path) == []


@pytest.mark.parametrize("blank", ["", "   ", None, 7])
def test_a_blank_user_id_is_not_recorded(storage, tmp_path, blank):
    row_file = _enrol_graph(storage, tmp_path)
    before = row_file.read_bytes()
    with pytest.raises(ValueError):
        record_user_id(storage, instance=GRAPH, user_id=blank)  # type: ignore[arg-type]
    assert row_file.read_bytes() == before


def test_a_row_that_is_not_an_object_is_refused_rather_than_replaced(storage, tmp_path):
    row_file = _enrol_graph(storage, tmp_path)
    row_file.write_text("[1, 2]")
    with pytest.raises(MalformedSettings):
        record_user_id(storage, instance=GRAPH, user_id=PM)
    assert row_file.read_text() == "[1, 2]"


def test_recording_takes_the_claim_enrolment_takes(storage, tmp_path):
    row_file = _enrol_graph(storage, tmp_path)
    before = row_file.read_bytes()
    from pm_ai.ports import ArtifactBusy

    with held_by_another_thread(
        lambda: storage.exclusive(scope=APPLICATION, artifact="config.json")
    ):
        with pytest.raises(ArtifactBusy):
            record_user_id(storage, instance=GRAPH, user_id=PM)
    assert row_file.read_bytes() == before


def test_a_caller_holding_the_claim_records_under_it_and_never_takes_it_twice(
    storage, tmp_path
):
    """AD-5: `connector sign-in` (8m) holds the claim while it updates the row."""
    row_file = _enrol_graph(storage, tmp_path)
    before = row_file.read_bytes()

    with storage.exclusive(scope=APPLICATION, artifact="config.json"):
        with pytest.raises(ClaimReentered) as reentered:
            record_user_id(storage, instance=GRAPH, user_id=PM)
        assert "config.json" in str(reentered.value)
        assert row_file.read_bytes() == before, "written despite the re-entry"

        assert record_user_id(storage, instance=GRAPH, user_id=PM, claimed=True) is True
    assert json.loads(row_file.read_text())[USER_ID_KEY] == PM


def test_replacing_a_credential_under_a_held_claim_is_a_programming_error(storage):
    enrol_connector(storage, system="graph", instance=GRAPH, credential="old", probe=accepts)
    with storage.exclusive(scope=APPLICATION, artifact="config.json"):
        with pytest.raises(ClaimReentered):
            replace_credential(storage, system="graph", instance=GRAPH, credential="new")
    assert stored_credentials(storage)[GRAPH]["credential"] == "old"


@pytest.mark.parametrize(
    "row",
    [{}, {USER_ID_KEY: ""}, {USER_ID_KEY: "  "}, {USER_ID_KEY: 42}, {USER_ID_KEY: None}],
    ids=["missing", "empty", "whitespace", "a number", "null"],
)
def test_a_row_without_a_usable_user_id_records_no_user(row):
    assert recorded_user_id(row) is None


@pytest.mark.parametrize("not_a_guid", ["a-graph-user-id", "pm@example.com", "0d9e3c4f"])
def test_a_user_id_the_reader_ignores_is_not_recorded(storage, tmp_path, not_a_guid):
    """Story 2o: the writer refuses what `recorded_user_id` reads as absent, or a
    row would record a user id no reader can use."""
    row_file = _enrol_graph(storage, tmp_path)
    before = row_file.read_bytes()
    with pytest.raises(ValueError) as refused:
        record_user_id(storage, instance=GRAPH, user_id=not_a_guid)
    assert repr(not_a_guid) in str(refused.value)
    assert row_file.read_bytes() == before


def test_a_recorded_value_that_is_not_a_guid_reads_as_no_recorded_user():
    """A hand-edited or pre-2o row whose `user_id` is not a Graph user id reads as
    no recorded user, by `domain`'s one rule."""
    assert recorded_user_id({USER_ID_KEY: "a-graph-user-id"}) is None


def test_a_recorded_user_id_is_read_back():
    assert recorded_user_id({USER_ID_KEY: PM}) == PM


def test_recording_another_user_replaces_the_recorded_one(storage, tmp_path):
    """pm-ai trusts any authenticated sign-in (2026-10-12): the row records whoever
    signed in last, and nothing compares."""
    row_file = _enrol_graph(storage, tmp_path)
    assert record_user_id(storage, instance=GRAPH, user_id=PM) is True
    somebody_else = "6f1c2b3a-0000-4000-8000-000000000002"
    assert record_user_id(storage, instance=GRAPH, user_id=somebody_else) is True
    assert json.loads(row_file.read_text())[USER_ID_KEY] == somebody_else


def test_a_repeat_sign_in_in_another_case_changes_no_bytes(storage, tmp_path):
    row_file = _enrol_graph(storage, tmp_path)
    assert record_user_id(storage, instance=GRAPH, user_id=PM) is True
    before = row_file.read_bytes()

    assert record_user_id(storage, instance=GRAPH, user_id=PM.upper()) is False
    assert row_file.read_bytes() == before


def test_a_row_that_is_not_graphs_records_no_graph_user(storage, tmp_path):
    enrol_connector(
        storage, system="gitlab", instance="gitlab:alpha", credential=SECRET, probe=accepts
    )
    row_file = tmp_path / ".pm-ai" / "connectors" / "gitlab:alpha.json"
    before = row_file.read_bytes()

    with pytest.raises(NotAGraphRow) as refused:
        record_user_id(storage, instance="gitlab:alpha", user_id=PM)

    assert "gitlab:alpha" in str(refused.value)
    assert row_file.read_bytes() == before


def test_saying_the_claim_is_held_when_it_is_not_is_a_programming_error(
    storage, tmp_path
):
    row_file = _enrol_graph(storage, tmp_path)
    before = row_file.read_bytes()

    with pytest.raises(ClaimNotHeld):
        record_user_id(storage, instance=GRAPH, user_id=PM, claimed=True)
    with held_by_another_thread(
        lambda: storage.exclusive(scope=APPLICATION, artifact="config.json")
    ):
        with pytest.raises(ClaimNotHeld):  # another thread's claim is not this one's
            record_user_id(storage, instance=GRAPH, user_id=PM, claimed=True)
    assert row_file.read_bytes() == before


@pytest.mark.parametrize("project", ["", "   ", "web"])
def test_a_blank_or_slashless_project_is_refused_before_anything(storage, tmp_path, project):
    """Story 8o: no caller can write a row whose `project` is not a GitLab path."""
    asked: list[str] = []

    def probe(system: str, credential: str) -> str:
        asked.append(system)
        return "accepted"

    with pytest.raises(MalformedSettings, match="group/project"):
        enrol_connector(
            storage,
            system="gitlab",
            instance="gitlab:alpha",
            credential=SECRET,
            probe=probe,
            project=project,
        )
    assert asked == []
    assert _connector_files(tmp_path) == []
    assert not _sealed_file(tmp_path).exists()
