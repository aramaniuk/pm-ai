"""Story 4j — one test per row of the three subcommands' I/O matrix.

`4c` built the table and reached one service through it. These are the other
three leaves: `pm-ai key enrol` over `4b`'s enrolment, `pm-ai config show` over
`4a`'s loader, and `pm-ai connector check` over `8d`'s per-connector probes.

Every assertion is an **exact** exit integer, and every integer is one of the
five `4c` declares — asserted as a property of this file in
`test_this_slice_asserts_only_the_codes_4c_declares`, because a fourth
convention invented here would be invisible to `4c`'s own tests.

`main()` is called with an explicit argument vector throughout, so nothing here
needs a subprocess.
"""

from __future__ import annotations

import dataclasses
import json
import ast
import base64
import threading
import time
from pathlib import Path

import pytest

from pm_ai.core.project_registry import ProjectEntry, render_registry
from pm_ai.platform.doctor import ArtifactState
from pm_ai.app.wiring import Bootstrap, bootstrap
from pm_ai.app import entry
from pm_ai.connectors import registry as connectors
from pm_ai.connectors.registry import ConnectorRegistry
from pm_ai.core.config import ACCEPTED_KEYS, Config, ConfigRefused, load_config
from pm_ai.domain.health import EnrolmentReading, Health, Probe
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.scope_model import ScopeResolutionError
from pm_ai.ports import (
    AES_KEY_BYTES,
    MASTER_KEY_NAME,
    KeyAlreadyEnrolled,
    KeychainBackendMissing,
    KeychainUnavailable,
    KeyNotFound,
    ProbeUnreachable,
)
from pm_ai.surfaces.cli import dispatch as cli
from pm_ai.surfaces.cli.dispatch import (
    EXIT_OK,
    EXIT_REFUSAL,
    EXIT_UNEXPECTED,
    EXIT_UNHEALTHY,
    EXIT_USAGE,
)

# ── Fakes ────────────────────────────────────────────────────────────────────

NOTHING_READ = EnrolmentReading("connectors/", (), (), (), (), None)
"""A `connectors/` reading for a hand-built composition; `connector check` ignores it."""



def enrolled(**projects: Path):
    """A stand-in for `entry._bootstrap`, in its post-`4d` shape.

    It returns the projects *and* an `ArtifactState` for each of the two
    artifacts, because a caller has to be able to tell an absent registry from
    an unreadable one — a stub returning only the mapping would no longer
    type-check and, worse, would let `doctor` report a first run on a machine
    whose registry could not be opened.

    `config` is left `ABSENT`: these fixtures redirect `HOME` at a fresh
    directory, so that is the truth for every test using one, and the rows that
    care about the config write a real file into it.
    """
    entries = {name: ProjectEntry(path=path) for name, path in projects.items()}
    registry = (
        ArtifactState.read(render_registry(entries)) if entries else ArtifactState.absent()
    )

    def read(keychain):
        # The real bootstrap for `config.toml`, the stub for the registry: the
        # fixtures write a config into the redirected `HOME` and expect it to be
        # honoured, and stubbing it flat would make those rows assert nothing.
        return dataclasses.replace(
            bootstrap(keychain), projects=entries, registry=registry
        )

    return read


class Keychain:
    """A keychain that answers, with each of `ports`' failure modes selectable.

    `store_if_absent` is the operation enrolment uses, and this implements it as
    the port describes it: one step, conditional on absence, raising rather than
    replacing. A fake that read then wrote would pass every test here while the
    real refusal — two enrolments racing, the second minting over the first —
    went unexercised.
    """

    def __init__(self, *, secret: bytes | None = None, raises: Exception | None = None):
        self.secret = secret
        self.raises = raises
        self.stored: bytes | None = None

    def store(self, name: str, secret: bytes) -> None:
        self.secret = secret
        self.stored = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        if self.raises is not None:
            raise self.raises
        if self.secret is not None:
            raise KeyAlreadyEnrolled(f"{name!r} already holds {len(self.secret)} bytes")
        self.secret = secret
        self.stored = secret

    def fetch(self, name: str) -> bytes:
        if self.secret is None:
            raise KeyNotFound(name)
        return self.secret

    def delete(self, name: str) -> None:
        self.secret = None


class Connector:
    """A connector that answers a fixed probe, or blocks until it is released.

    Only what the registry touches: `check_health` and a name. `8d` owns the
    probe contract, and a fake that also emitted events would be asserting `8d`'s
    tests from inside `4j`'s.
    """

    def __init__(self, name: str, health: Health | None = None):
        self.name = name
        self.health = health
        self.released = threading.Event()

    def check_health(self) -> Probe:
        if self.health is None:
            # The connector CAP-35's bound exists for: a provider that never
            # answers. Bounded so an abandoned thread cannot outlive the run.
            self.released.wait(30)
            return Probe(self.name, Health.OK, "answered eventually, far too late")
        return Probe(self.name, self.health, f"{self.name} says {self.health.value}")


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def keychain(monkeypatch):
    """Put a fake in the composition root's hand, and hand it back to the test.

    `Daemon.keychain` is whatever `entry` built, so replacing the adapter here is
    what makes every enrolment row reachable — none of them can be provoked
    against a real Keychain, which is the reason `4b` took a port at all.
    """

    def install(**kwargs) -> Keychain:
        fake = Keychain(**kwargs)
        monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: fake)
        return fake

    return install


@pytest.fixture
def registered(tmp_path, monkeypatch, keychain):
    """A machine where composition succeeds: one enrolled project, an empty home.

    `HOME` is redirected rather than `ScopePaths.production` stubbed, so the real
    layout decides where `config.toml` lives and nothing outside `tmp_path` is
    touched.
    """
    keychain()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repository = tmp_path / "repo"
    repository.mkdir()
    monkeypatch.setattr(entry, "_bootstrap", enrolled(alpha=repository))
    return home


@pytest.fixture
def unregistered(tmp_path, monkeypatch, keychain):
    """A machine with no project enrolled — the first run.

    Since story 4o a command aimed at the person or at pm-ai itself still
    composes here, on a daemon that watches no project, so `HOME` is redirected
    as `registered` redirects it: without it those commands would read and
    write the real `~/.pm-ai`. A test that sets `HOME` itself overrides this.
    `connector check` reports that no connectors are registered rather than
    refusing.
    """
    keychain()
    home = tmp_path / "unregistered-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(entry, "_bootstrap", enrolled())


class Inventory:
    """The fakes a test put in the composed daemon, and what `main` did with them."""

    def __init__(self) -> None:
        self.chosen: list[Connector] = []
        self.built: list[object] = []
        self.dispatched: list[object] = []

    def install(self, *connectors_: Connector) -> None:
        self.chosen.extend(connectors_)


@pytest.fixture
def registry(registered, monkeypatch):
    """Put connectors in the daemon `entry` composes — its own inventory, not a copy.

    The real `build()` runs, on `registered`'s machine, and its
    `Daemon.connectors` is then replaced with the fakes: that dict is the one
    list a harvest reads and `connector check` probes (story 8k), so this is
    the only place a test's connectors can live. Nothing is process-wide, so
    there is nothing to put back.

    The daemon `main` hands `dispatch` is recorded too, and checked at teardown
    to be the one carrying the fakes — so a row cannot pass by reaching a
    daemon this fixture never touched.
    """
    inventory = Inventory()
    real_build = entry.build
    real_dispatch = entry.dispatch

    def build(*args, **kwargs):
        daemon = real_build(*args, **kwargs)
        daemon.connectors = {connector.name: connector for connector in inventory.chosen}
        inventory.built.append(daemon)
        return daemon

    def dispatch(*args, **kwargs):
        inventory.dispatched.append(kwargs["daemon"])
        return real_dispatch(*args, **kwargs)

    monkeypatch.setattr(entry, "build", build)
    monkeypatch.setattr(entry, "dispatch", dispatch)
    yield inventory
    for connector in inventory.chosen:
        connector.released.set()
    for daemon in inventory.dispatched:
        if daemon is None:
            continue
        assert any(daemon is built for built in inventory.built), (
            "`main` dispatched a daemon this fixture did not build"
        )
        assert list(daemon.connectors.values()) == inventory.chosen, (
            "the fakes never reached the daemon `main` used"
        )


@pytest.fixture
def quick_bound(monkeypatch):
    """Shorten the wait the CLI hands the registry, without moving CAP-35's bound.

    A blocked probe under the real ten seconds is a correct test that costs ten
    seconds every run, so the *number* is replaced here and pinned separately in
    `test_the_bound_the_cli_uses_is_cap35s_and_is_the_registrys_own`. Only the
    timeout changes: the probe is still bound to the composed daemon, and
    `ConnectorRegistry.check_health` still starts a thread per connector and
    abandons the one that does not answer.
    """

    class Quick(ConnectorRegistry):
        def check_health(self, *, timeout: float = 0.5):
            return super().check_health(timeout=timeout)

    bind = entry._probe_connectors
    monkeypatch.setattr(
        entry, "_probe_connectors", lambda composed: bind(composed, registry=Quick)
    )


# ── `pm-ai key enrol` ────────────────────────────────────────────────────────


def test_enrolment_on_a_clean_keychain_succeeds(registered, keychain, capsys):
    """`4b`'s service, invoked with `Daemon.keychain` — the field `4c` added for it."""
    custody = keychain()
    assert entry.main(["key", "enrol"]) == EXIT_OK
    assert custody.stored is not None and len(custody.stored) == AES_KEY_BYTES
    printed = capsys.readouterr().out
    assert MASTER_KEY_NAME in printed


def test_enrolment_stores_under_the_name_the_daemon_fetches_from(registered, keychain):
    """One name, or the doctor probes for a key the cipher will never find.

    `MASTER_KEY_NAME` is shared between the minter, the lazy cipher and the
    keychain probe precisely so they cannot drift; this asserts the CLI did not
    reintroduce a fourth spelling on the way through.
    """
    custody = keychain()
    assert entry.main(["key", "enrol"]) == EXIT_OK
    assert custody.fetch(MASTER_KEY_NAME) == custody.stored


def test_enrolment_when_a_key_exists_is_refused_and_names_the_consequence(
    registered, keychain, capsys
):
    """Minting over a key makes every artifact sealed under the old one unreadable.

    So this must be a refusal (3) rather than a bug (1): pm-ai understood
    perfectly and declined, and the operator has to be able to tell those apart
    from the far side of a shell.
    """
    custody = keychain(secret=b"x" * 32)
    assert entry.main(["key", "enrol"]) == EXIT_REFUSAL
    printed = capsys.readouterr().err
    assert "already enrolled" in printed
    assert "permanently unreadable" in printed
    assert "Traceback" not in printed
    assert custody.secret == b"x" * 32, "a refused enrolment wrote anyway"


@pytest.mark.parametrize(
    "failure",
    [
        KeychainUnavailable("the OS keychain is locked and refused the request"),
        KeychainBackendMissing("no keyring package is installed, so nothing was asked"),
    ],
    ids=["unreachable", "no-backend"],
)
def test_an_unreachable_keychain_is_a_refusal_not_a_crash(
    registered, keychain, capsys, failure
):
    keychain(raises=failure)
    assert entry.main(["key", "enrol"]) == EXIT_REFUSAL
    printed = capsys.readouterr().err
    assert str(failure) in printed
    assert "Traceback" not in printed


def test_the_three_failure_modes_stay_distinguished(registered, keychain, capsys):
    """`ports` keeps them apart; the surface must not collapse them into one line.

    All three exit `3` — they are all refusals — so the exit code cannot carry
    the distinction and the sentence has to. Collapsing them is how an operator
    gets told to unlock a keychain that is not installed.
    """
    modes = {
        "already enrolled": lambda: keychain(secret=b"y" * 32),
        "unreachable": lambda: keychain(raises=KeychainUnavailable("the OS refused")),
        "no backend": lambda: keychain(
            raises=KeychainBackendMissing("no keyring package is installed")
        ),
    }
    messages = {}
    for label, arrange in modes.items():
        arrange()
        assert entry.main(["key", "enrol"]) == EXIT_REFUSAL, label
        messages[label] = capsys.readouterr().err
    assert len(set(messages.values())) == len(modes), messages


def test_a_key_that_does_not_read_back_is_a_refusal(
    registered, keychain, capsys, monkeypatch
):
    """A write that reports success and stores nothing is the silent failure `4b` reads back for.

    The alternative is finding out months later, on a machine with nobody
    watching, when the first encrypted write cannot find its key.
    """

    class Amnesiac(Keychain):
        def fetch(self, name: str) -> bytes:
            raise KeyNotFound(name)

    fake = Amnesiac()
    # `monkeypatch`, not assignment plus `del`. Deleting the name outright left
    # the module without an attribute it is defined with, and only the
    # `registered` fixture's own undo put it back — so reordering these tests,
    # or dropping that fixture, made every later `entry.main` raise NameError.
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: fake)

    assert entry.main(["key", "enrol"]) == EXIT_REFUSAL
    assert "do not treat this machine as set up" in capsys.readouterr().err


def test_enrolment_with_no_project_enrolled_runs(unregistered, keychain, capsys):
    """Story 4o: `key enrol` is aimed at pm-ai itself, so no project is needed.

    It was refused here for the lack of one until 4o. The key lands in the
    keychain the daemon holds, and the closing line names the entry it went
    under — and never the key.
    """
    custody = keychain()
    assert entry.main(["key", "enrol"]) == EXIT_OK
    captured = capsys.readouterr()
    assert f"a master key is enrolled under {MASTER_KEY_NAME!r}" in captured.out
    assert custody.stored is not None
    assert "no project is enrolled" not in captured.err
    assert "Traceback" not in captured.err


def test_no_stream_carries_the_key_that_was_just_enrolled(registered, keychain, capsys):
    """The surface half of `4b`'s guarantee, which `4b` cannot assert on its own.

    `4b` promises the minted key never leaves the keychain; it has no surface to
    check that against, and this is the surface. Asserted against the *actual*
    bytes the keychain received rather than a planted secret, and in every
    encoding a leak plausibly arrives in — a traceback repr, a hex dump, base64,
    and the raw bytes read back as text.
    """
    custody = keychain()
    assert entry.main(["key", "enrol"]) == EXIT_OK
    key = custody.stored
    assert key is not None
    captured = capsys.readouterr()
    renderings = (
        repr(key),
        str(key),
        key.hex(),
        base64.b64encode(key).decode(),
        key.decode("latin-1"),
    )
    for stream in (captured.out, captured.err):
        for rendering in renderings:
            assert rendering not in stream, "key material reached a stream"


def test_a_refused_enrolment_echoes_no_key_either(registered, keychain, capsys):
    """A refusal is the message most likely to be pasted into a support request."""
    existing = b"z" * 32
    keychain(secret=existing)
    assert entry.main(["key", "enrol"]) == EXIT_REFUSAL
    captured = capsys.readouterr()
    for stream in (captured.out, captured.err):
        assert existing.decode() not in stream
        assert existing.hex() not in stream


# ── `pm-ai config show` ──────────────────────────────────────────────────────


def test_config_shown_on_a_clean_machine_marks_every_key_a_default(
    registered, capsys
):
    """Absence is a first run, and an unmarked default reads as a setting.

    Both halves matter: exit `0`, because no `config.toml` is the ordinary state
    of a fresh install; and every row marked, because a value the operator set
    and one they inherited are indistinguishable in a bare dump.
    """
    assert not (registered / ".pm-ai" / "config.toml").exists()
    assert entry.main(["config", "show"]) == EXIT_OK
    printed = capsys.readouterr().out
    for key in ACCEPTED_KEYS:
        assert key in printed
    rows = [line for line in printed.splitlines() if line.split(" ")[0] in ACCEPTED_KEYS]
    assert len(rows) == len(ACCEPTED_KEYS)
    for row in rows:
        assert "(default)" in row, row
        assert "(set)" not in row, row


def test_config_shown_with_a_file_marks_what_the_file_set(registered, capsys):
    """One key written, one key inherited, and the output has to tell them apart."""
    path = registered / ".pm-ai" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('display_timezone = "Europe/Warsaw"\n', encoding="utf-8")
    assert entry.main(["config", "show"]) == EXIT_OK
    printed = capsys.readouterr().out
    rows = {line.split(" ")[0]: line for line in printed.splitlines() if line.split(" ")[0] in ACCEPTED_KEYS}
    assert set(rows) == set(ACCEPTED_KEYS)
    assert "(set)" in rows["display_timezone"]
    assert "Europe/Warsaw" in rows["display_timezone"]
    assert "(default)" in rows["blended_hourly_rate"]
    assert "(default)" in rows["verbose_logging"]


def test_every_field_config_carries_is_printed(registered, capsys):
    """The row set is derived from `Config`, so a field added later cannot go unshown.

    A hand-written list of three keys would still print three after `Config`
    grew a fourth, and the missing setting would read as one that does not
    exist. It did grow one — `display_timezone`, story 4g — and this test is
    why nothing here had to be edited for it.
    """
    from dataclasses import fields

    assert entry.main(["config", "show"]) == EXIT_OK
    printed = capsys.readouterr().out
    for field_ in fields(Config):
        assert field_.name in printed


def test_the_marked_defaults_are_the_values_config_actually_defaults_to(
    registered, capsys
):
    """"Default" means `Config()`'s own field default and nothing else."""
    assert entry.main(["config", "show"]) == EXIT_OK
    printed = capsys.readouterr().out
    defaults = Config()
    for key in ACCEPTED_KEYS:
        assert repr(getattr(defaults, key)) in printed


def test_a_refused_config_exits_3_with_the_loaders_own_message(registered, capsys):
    """The loader's sentence names the offending key; a paraphrase would not.

    Compared against the message `load_config` itself produces for these exact
    bytes, so the assertion cannot drift into "some refusal was printed".
    """
    raw = b"blended_hourly_rate = true\n"
    path = registered / ".pm-ai" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    with pytest.raises(ConfigRefused) as refused:
        load_config(raw)

    assert entry.main(["config", "show"]) == EXIT_REFUSAL
    printed = capsys.readouterr().err
    assert str(refused.value) in printed
    assert "Traceback" not in printed


def test_an_encryption_key_in_config_is_refused_through_the_surface_too(
    registered, capsys
):
    """The one refusal that is a deliberate act rather than a typo (AD-6)."""
    path = registered / ".pm-ai" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("encryption = false\n", encoding="utf-8")
    assert entry.main(["config", "show"]) == EXIT_REFUSAL
    assert "PM_AI_DISABLE_ENCRYPTION" in capsys.readouterr().err


# ── `pm-ai connector check` ──────────────────────────────────────────────────


def test_connector_check_with_every_connector_healthy_exits_0(registry, capsys):
    registry.install(
        Connector("gitlab:alpha", Health.OK), Connector("gitlab:beta", Health.OK)
    )
    assert entry.main(["connector", "check"]) == EXIT_OK
    printed = capsys.readouterr().out
    assert "gitlab:alpha" in printed
    assert "gitlab:beta" in printed
    assert "2 connectors probed" in printed


def test_a_silent_connector_is_failing_and_its_siblings_still_report(
    registry, quick_bound, capsys
):
    """One broken connector hiding another is what `8d`'s report-never-raise rule is for.

    All three claims at once, because they fail independently: the silent one is
    `FAILING`, every sibling is still printed, and the exit code is `4` rather
    than a success that would let a dead connector read forever as "no coverage
    yet" (AD-39).
    """
    registry.install(
        Connector("gitlab:alpha", Health.OK),
        Connector("gitlab:silent"),
        Connector("gitlab:beta", Health.OK),
    )
    started = time.monotonic()
    assert entry.main(["connector", "check"]) == EXIT_UNHEALTHY
    elapsed = time.monotonic() - started
    assert elapsed < connectors.HEALTH_PROBE_SECONDS, (
        f"the probe ran for {elapsed:.1f}s, past CAP-35's bound"
    )
    printed = capsys.readouterr().out
    assert Health.FAILING.value in printed
    silent = next(line for line in printed.splitlines() if "gitlab:silent" in line)
    assert Health.FAILING.value in silent
    for sibling in ("gitlab:alpha", "gitlab:beta"):
        assert sibling in printed, "a silent connector hid a sibling"
    assert "not every connector is healthy" in printed


def test_a_connector_whose_probe_raises_does_not_hide_the_rest(registry, capsys):
    """A bug in one adapter is reported as that adapter's row, not as exit 1."""

    class Exploding(Connector):
        def check_health(self):
            raise RuntimeError("a bug in this adapter")

    registry.install(
        Exploding("gitlab:broken", Health.OK), Connector("gitlab:fine", Health.OK)
    )
    assert entry.main(["connector", "check"]) == EXIT_UNHEALTHY
    printed = capsys.readouterr().out
    assert "gitlab:broken" in printed
    assert "gitlab:fine" in printed


def test_nothing_enrolled_says_no_connectors_are_registered_and_exits_0(
    unregistered, capsys
):
    """No project, no daemon: a first-run state, and no claim of reachability is false."""
    assert entry.main(["connector", "check"]) == EXIT_OK
    printed = capsys.readouterr().out
    assert "no connectors are registered" in printed


def _assert_refused_naming(capsys, *reason: str) -> None:
    """Exit 3 already asserted; the refusal is `require_daemon`'s, carrying `reason`."""
    captured = capsys.readouterr()
    assert "could not build a daemon" in captured.err
    for words in reason:
        assert words in captured.err, f"the refusal does not name {words!r}"
    assert "no connectors are registered" not in captured.out


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (
            ArtifactState.unreadable("projects.toml: [Errno 13] Permission denied"),
            ("could not be read", "projects.toml"),
        ),
        (
            ArtifactState.read(b"[[[ this is not toml"),
            ("projects.toml cannot be read as a registry",),
        ),
    ],
    ids=["unreadable", "unparseable"],
)
def test_a_projects_toml_that_cannot_be_read_is_refused_not_a_first_run(
    keychain, monkeypatch, state, reason, capsys
):
    """Story 8k's new row: a broken registry is not an empty one.

    Both leave no project and no daemon, and before 8k both printed the
    first-run message. The states are faked rather than produced with file
    modes, which root and some filesystems ignore.
    """
    keychain()
    monkeypatch.setattr(
        entry, "_bootstrap", lambda chain: Bootstrap(
            {}, state, ArtifactState.absent(), bootstrap(chain).connectors
        ),
    )
    assert entry.main(["connector", "check"]) == EXIT_REFUSAL
    _assert_refused_naming(capsys, *reason)


@pytest.mark.parametrize(
    ("raised", "reason"),
    [
        (OSError("the root went away"), ("pm-ai's own directory", "the root went away")),
        (
            ScopeResolutionError("alpha points nowhere"),
            ("cannot be resolved to a directory", "alpha points nowhere"),
        ),
    ],
    ids=["oserror", "unresolvable"],
)
def test_build_raising_is_refused_naming_it(registered, monkeypatch, raised, reason, capsys):
    """Story 8k's new row, for the two failures `_compose` catches from `build()`."""

    def build(*args, **kwargs):
        raise raised

    monkeypatch.setattr(entry, "build", build)
    assert entry.main(["connector", "check"]) == EXIT_REFUSAL
    _assert_refused_naming(capsys, *reason)


@pytest.mark.parametrize("config", ["unreadable", "refused"])
def test_a_config_that_will_not_load_is_refused_rather_than_probed(
    registered, registry, monkeypatch, config, capsys
):
    """Story 8k's new row: refused, rather than probing connectors nothing will use.

    `build()` succeeds and the daemon is then discarded over `config.toml`, so
    its connectors exist and are not this machine's running set. Before 8k
    `connector check` probed them anyway, out of the process-wide copy.
    """
    asked: list[str] = []

    class Counted(Connector):
        def check_health(self) -> Probe:
            asked.append(self.name)
            return super().check_health()

    registry.install(Counted("gitlab:alpha", Health.OK))
    if config == "unreadable":
        # Faked: `chmod(0)` is ignored by root and by some filesystems, and the
        # row is about pm-ai's answer, not about producing the state.
        stub = enrolled(alpha=registered.parent / "repo")

        def read(keychain_):
            projects = stub(keychain_)
            return Bootstrap(
                projects.projects,
                projects.registry,
                ArtifactState.unreadable("config.toml: [Errno 13] Permission denied"),
                projects.connectors,
            )

        monkeypatch.setattr(entry, "_bootstrap", read)
    else:
        path = registered / ".pm-ai" / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("encryption = false\n", encoding="utf-8")
    assert entry.main(["connector", "check"]) == EXIT_REFUSAL
    _assert_refused_naming(capsys, "config.toml")
    assert asked == [], "a connector the running system will not use was probed"
    assert registry.built, "the row needs a daemon that was built and then discarded"


def test_a_daemon_built_earlier_still_probes_only_its_own(tmp_path):
    """Two daemons in one process: the first one's check is not the second's list.

    The order is the point. Before 8k `build()` replaced a process-wide list, so
    probing daemon A *after* daemon B was built reported B's connectors.
    """
    from pm_ai.app import wiring

    first = wiring.build(tmp_path / "one", "alpha")
    wiring.build(tmp_path / "two", "beta")
    probe = entry._probe_connectors(
        entry._Composition(
            first, None, ArtifactState.absent(), ArtifactState.absent(), NOTHING_READ
        )
    )
    assert probe is not None
    assert [row.name for row in probe().probes] == ["gitlab:alpha"]


def test_a_connector_with_no_credential_is_not_a_pass(registry, capsys):
    """`ABSENT` is setup outstanding, and setup outstanding is not health.

    Distinct from the first-run row above, deliberately: a connector that is
    registered and has no credential is silently skipping harvests, which is
    exactly the state AD-39 says must not read as "no activity".
    """
    registry.install(Connector("gitlab:alpha", Health.ABSENT))
    assert entry.main(["connector", "check"]) == EXIT_UNHEALTHY
    assert Health.ABSENT.value in capsys.readouterr().out


def test_the_bound_the_cli_uses_is_cap35s_and_is_the_registrys_own(tmp_path):
    """CAP-35's ten seconds, named once — in `8d`, where the waiting happens.

    `quick_bound` shortens the number for the blocked-probe test, so something
    has to pin what production actually passes. The CLI supplies no timeout at
    all, which is the property being asserted: a surface holding its own copy of
    the bound is how the two drift.
    """
    import inspect

    from pm_ai.app import wiring

    assert connectors.HEALTH_PROBE_SECONDS == 10.0
    timeout = inspect.signature(ConnectorRegistry.check_health).parameters["timeout"]
    assert timeout.default == connectors.HEALTH_PROBE_SECONDS
    factory = inspect.signature(entry._probe_connectors).parameters["registry"]
    assert factory.default is ConnectorRegistry, (
        "production builds a plain ConnectorRegistry, whose default is the bound"
    )

    passed: list[dict] = []

    class Spy(ConnectorRegistry):
        def check_health(self, **kwargs):
            passed.append(kwargs)
            return super().check_health(**kwargs)

    daemon = wiring.build(tmp_path, "alpha")
    probe = entry._probe_connectors(
        entry._Composition(
            daemon, None, ArtifactState.absent(), ArtifactState.absent(), NOTHING_READ
        ),
        registry=Spy,
    )
    assert probe is not None
    probe()
    assert passed == [{}], "the CLI supplied its own timeout instead of CAP-35's"


# ── The table these leaves hang on ───────────────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [["key", "enrol"], ["config", "show"], ["connector", "check"], ["connector", "sign-in"]],
    ids=lambda argv: " ".join(argv),
)
def test_every_leaf_is_reachable_from_the_table(argv):
    """Three mappings on `4c`'s table, not a second table beside it."""
    group, leaf = argv
    assert group in cli.TABLE
    assert leaf in cli.TABLE[group].leaves
    assert cli.TABLE[group].leaves[leaf].run is not None
    assert cli.TABLE[group].run is None, "a group with leaves must not also run"


def test_the_top_level_usage_lists_all_three_groups(capsys):
    assert entry.main(["--help"]) == EXIT_OK
    printed = capsys.readouterr().out
    for group in ("key", "config", "connector"):
        assert group in printed


@pytest.mark.parametrize(
    "argv",
    [["key", "enrol", "--force"], ["config", "show", "extra"], ["connector", "check", "x"]],
    ids=lambda argv: " ".join(argv),
)
def test_a_leaf_refuses_trailing_arguments_rather_than_ignoring_them(
    registered, argv, capsys
):
    """A dropped `--force` runs the command the operator was being careful with."""
    assert entry.main(argv) == EXIT_USAGE
    assert "takes no arguments" in capsys.readouterr().err


def test_this_slice_asserts_only_the_codes_4c_declares():
    """A fourth convention invented here would be invisible to `4c`'s own tests.

    Two claims. Every `EXIT_*` this file names is one `4c` exports, and its value
    is one of the five integers `4c` declares. And no assertion in this file
    compares `main()` against a bare integer — which is how a fifth code would
    enter without touching a name at all.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    declared = {EXIT_OK, EXIT_UNEXPECTED, EXIT_USAGE, EXIT_REFUSAL, EXIT_UNHEALTHY}
    assert declared == {0, 1, 2, 3, 4}

    named = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id.startswith("EXIT_")
    }
    assert named, "this file asserts no exit code at all"
    assert named <= set(cli.__all__), sorted(named - set(cli.__all__))
    assert {getattr(cli, name) for name in named} <= declared

    literals = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Call)
        and isinstance(node.left.func, ast.Attribute)
        and node.left.func.attr == "main"
        for comparator in node.comparators
        if isinstance(comparator, ast.Constant) and isinstance(comparator.value, int)
    ]
    assert not literals, f"a bare exit integer is asserted at lines {literals}"


# ── Story 8b — `pm-ai connector add` ─────────────────────────────────────────


def test_connector_add_declares_its_arity_and_refuses_the_wrong_count(capsys):
    """4j refused every trailing word; 8b is the slice that needed some.

    The arity is declared on the table, so the refusal and the usage line cannot
    disagree — and a leaf taking arguments does not reopen the silent-drop hole
    4j closed.
    """
    from pm_ai.surfaces.cli.dispatch import TABLE

    assert TABLE["connector"].leaves["add"].takes == ("system", "instance")
    assert entry.main(["connector", "add"]) == EXIT_USAGE
    assert entry.main(["connector", "add", "gitlab"]) == EXIT_USAGE
    assert "<system> <instance>" in capsys.readouterr().err


def test_connector_add_refuses_when_stdin_is_not_a_terminal(
    registered, monkeypatch, capsys
):
    """`getpass` falls back to an echoing read with no TTY, which is history."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert entry.main(["connector", "add", "gitlab", "gitlab:alpha"]) == EXIT_REFUSAL
    assert "TTY" in capsys.readouterr().err


def _gitlab_answers(monkeypatch, token: str = "glpat-typed-at-the-prompt-99", status: int = 200):
    """`connector add gitlab`'s questions answered and GitLab faked (story 8o).

    Enter for the host, `acme/web` for the path, `token` at the hidden prompt,
    and every request answered `status` — a commit list for a 2xx. No socket.
    """
    import functools

    from pm_ai.app.wiring import GitLabEnrolment
    from pm_ai.connectors.gitlab.client import GitLabResponse

    answers = ["", "acme/web"]
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": answers.pop(0))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": token)
    body = [] if 200 <= status < 300 else {"message": str(status)}
    monkeypatch.setattr(
        entry,
        "GitLabEnrolment",
        functools.partial(
            GitLabEnrolment, transport=lambda request: GitLabResponse(status, body)
        ),
    )


def test_connector_add_never_echoes_the_credential(
    registered, keychain, monkeypatch, capsys
):
    """The prompt is `getpass`, and nothing prints what it returned."""
    keychain(secret=b"K" * 32)
    secret = "glpat-typed-at-the-prompt-99"
    _gitlab_answers(monkeypatch, secret)

    assert entry.main(["connector", "add", "gitlab", "gitlab:alpha"]) == EXIT_OK
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    # Since 4s the closing message says when a running daemon picks it up,
    # not "the next start".
    assert cli.HARVESTED_WHEN in captured.out, "the activation promise 8d requires"


@pytest.mark.parametrize("system", ["jira", "nosuch", "GitLab", "GRAPH"])
def test_an_unsupported_system_is_refused_by_name_before_any_prompt(
    registered, keychain, monkeypatch, capsys, system
):
    """Story 8o: only `graph` and `gitlab`, spelled exactly; nothing is asked."""
    keychain(secret=b"K" * 32)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    asked: list[str] = []
    monkeypatch.setattr("getpass.getpass", lambda prompt="": asked.append(prompt) or "x")
    monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt) or "x")

    assert entry.main(["connector", "add", system, "one:alpha"]) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert asked == [], "a prompt was shown for a system pm-ai cannot enrol"
    assert repr(system) in err
    assert "'gitlab'" in err and "'graph'" in err, "the supported systems are named"


def test_a_keyless_machine_gets_a_refusal_not_a_traceback(
    registered, monkeypatch, capsys
):
    """The most common first-run state, and it printed a stack trace.

    Enrolment reads the sealed store for its duplicate check, which needs the
    master key. `_run` re-raises everything it does not name, so `KeyNotFound`
    reached the operator as a traceback and exit 1 — indistinguishable from a
    bug — instead of the one instruction that fixes it. `registered` enrols
    no key.
    """
    _gitlab_answers(monkeypatch)
    assert entry.main(["connector", "add", "gitlab", "gitlab:alpha"]) == EXIT_REFUSAL
    assert "pm-ai key enrol" in capsys.readouterr().err


def test_a_silent_provider_is_not_reported_as_a_bad_credential(
    registered, keychain, monkeypatch, capsys
):
    """Otherwise the operator reissues a token that was never the problem."""
    keychain(secret=b"K" * 32)
    _gitlab_answers(monkeypatch, status=503)
    assert entry.main(["connector", "add", "gitlab", "gitlab:alpha"]) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "the token was not judged" in err
    assert "refused the token" not in err


# ── Story 1r — a busy file, refused by name at every command that claims one ──

BUSY_CREDENTIALS = (
    "config.json in application is claimed by another pm-ai process; nothing "
    "was changed. Run the command again."
)
BUSY_REGISTRY = (
    "projects.toml in application is claimed by another pm-ai process; nothing "
    "was changed. Run the command again."
)


APPLICATION_SCOPE = DataScope(ScopeKind.APPLICATION)


def _held(home: Path, artifact: str):
    """The platform lock on `artifact`, held directly — the same lock the façade takes.

    Held by another thread: this thread taking a claim it already holds is a
    programming error, not a busy file (AD-5, story 8q).
    """
    from claim_fakes import held_by_another_thread
    from pm_ai.platform.claims import exclusive
    from pm_ai.platform.paths import ScopePaths

    target = ScopePaths.production().resolve(APPLICATION_SCOPE, artifact).resolve()
    return held_by_another_thread(lambda: exclusive(target))


def test_connector_add_with_the_credentials_file_held_is_refused_by_name(
    registered, keychain, monkeypatch, capsys
):
    keychain(secret=b"K" * 32)
    _gitlab_answers(monkeypatch)

    with _held(registered, "config.json"):
        code = entry.main(["connector", "add", "gitlab", "gitlab:alpha"])

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert BUSY_CREDENTIALS in err
    assert ".claim" not in err and ".lock" not in err, "names a file to delete"


def test_project_add_with_the_registry_held_is_refused_by_name(
    unregistered, tmp_path, monkeypatch, capsys
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (tmp_path / "alpha").mkdir()

    with _held(home, "projects.toml"):
        code = entry.main(["project", "add", str(tmp_path / "alpha")])

    assert code == EXIT_REFUSAL
    assert BUSY_REGISTRY in capsys.readouterr().err
    assert not (home / ".pm-ai" / "projects.toml").exists()


def test_project_add_with_an_unusable_sidecar_is_refused_by_name(
    unregistered, tmp_path, monkeypatch, capsys
):
    """An operating-system fault on the lock file is a refusal, not a traceback."""
    home = tmp_path / "home"
    (home / ".pm-ai" / "projects.toml.lock").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (tmp_path / "alpha").mkdir()

    code = entry.main(["project", "add", str(tmp_path / "alpha")])

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "projects.toml.lock" in err and "Traceback" not in err
    assert "claimed by another" not in err, "a fault reported as busy"
    assert "registry could not be written" not in err, "names a file it did not check"


def test_connector_add_with_an_unusable_sidecar_is_refused_by_name(
    registered, keychain, monkeypatch, capsys
):
    """`config.json.lock` a directory: the OS fault is a refusal, not a traceback."""
    keychain(secret=b"K" * 32)
    (registered / ".pm-ai" / "private" / "config.json.lock").mkdir(parents=True)
    _gitlab_answers(monkeypatch)

    code = entry.main(["connector", "add", "gitlab", "gitlab:alpha"])

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "connector add stopped" in err and "config.json.lock" in err
    assert "Traceback" not in err
    assert "claimed by another" not in err, "a fault reported as busy"


def _setup_answers(monkeypatch, *answers: str) -> None:
    script = list(answers)

    def answer(prompt: str = "") -> str:
        if not script:
            raise EOFError
        return script.pop(0)

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)


def test_setup_step_two_with_the_registry_held_is_refused_by_name(
    unregistered, tmp_path, monkeypatch, capsys
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _setup_answers(monkeypatch, str(tmp_path / "alpha"), "")

    with _held(home, "projects.toml"):
        code = entry.main(["setup"])

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "setup stopped at step 2 (project); step 1 stays done" in err
    assert BUSY_REGISTRY in err


def test_setup_step_two_with_an_unusable_sidecar_is_refused_by_name(
    unregistered, tmp_path, monkeypatch, capsys
):
    home = tmp_path / "home"
    (home / ".pm-ai" / "projects.toml.lock").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    _setup_answers(monkeypatch, str(tmp_path / "alpha"), "")

    code = entry.main(["setup"])

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "setup stopped at step 2 (project); step 1 stays done" in err
    assert "projects.toml.lock" in err and "Traceback" not in err
    assert "registry could not be written" not in err, "names a file it did not check"


# ── Story 11c — `pm-ai transcript drop` ──────────────────────────────────────
#
# The surface's rows, against a stand-in pipeline that records what it was
# handed and, when told to, asks the questions the real one asks. The rows
# that need the real read, bind, capture and ingest are in
# `tests/slice/test_transcript_drop.py`, through `main()`.

import os  # noqa: E402
import socket  # noqa: E402
from datetime import datetime as _datetime, timezone as _timezone  # noqa: E402

from pm_ai.domain.identity import Actor  # noqa: E402
from pm_ai.domain.transcripts import CaptureNotIngested, DropInterrupted  # noqa: E402

@pytest.fixture(autouse=True)
def _throwaway_home(tmp_path_factory, monkeypatch):
    """Every test here runs against a throwaway `HOME`, never the developer's own.

    Commands compose through `ScopePaths.production()`, which anchors on
    `Path.home()`; without this a test opened the real `~/.pm-ai` (story 4r).
    A test that sets `HOME` itself overrides this one.
    """
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))


DROP_VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n<v Andrei>we ship on Friday\n"
DROP_START = _datetime(2026, 10, 9, 11, 0, tzinfo=_timezone.utc)


@dataclasses.dataclass
class _Field:
    value: object
    source: str
    estimated: bool = False


@dataclasses.dataclass
class _Dropped:
    capture: Path
    meeting_id: str
    scope: DataScope
    bound_by: str
    title: str = "Weekly Sync"
    start: _datetime = DROP_START
    staged: int = 1
    left: int = 0
    title_field: _Field | None = None
    start_field: _Field | None = None
    duration: _Field | None = None
    participants: tuple = (("Andrei", None),)
    candidates: tuple[str, ...] = ()
    found_by: str = ""
    day_unknown: bool = False
    zone: str = ""
    from_docx: bool = False
    notices: tuple[str, ...] = ()


@dataclasses.dataclass
class _Preview:
    title: _Field
    start: _Field
    duration: _Field
    participants: tuple
    outcome: str
    meeting_id: str | None = None
    meeting_title: str | None = None
    candidates: tuple[str, ...] = ()
    reason: str = ""
    zone: str = ""
    notices: tuple[str, ...] = ()
    found_by: str = ""
    day_unknown: bool = False


class _Drop:
    """A pipeline stand-in: records each call and answers as the real one is shaped."""

    def __init__(self, *, raises=None, answer=None) -> None:
        self.calls: list[dict] = []
        self.raises = raises
        self.answer = answer

    def __call__(self, *, text, file_name, scope, meeting_id, from_docx=False,
                 modified=None, created=None, title=None, start=None, duration=None,
                 attendees=(), dry_run=False):
        self.calls.append(
            {"text": text, "file_name": file_name, "scope": scope, "meeting_id": meeting_id,
             "from_docx": from_docx, "modified": modified, "created": created,
             "title": title, "start": start, "duration": duration, "attendees": attendees,
             "dry_run": dry_run}
        )
        if self.raises is not None:
            raise self.raises
        if self.answer is not None:
            return self.answer
        if meeting_id is not None:
            return _Dropped(Path("/captures/x-manual.vtt"), meeting_id, scope, "named")
        return _Dropped(
            Path("/captures/x-manual.vtt"),
            "mtg_new",
            scope,
            "minted",
            title_field=_Field("Weekly Sync", "file name"),
            start_field=_Field(DROP_START, "file modification time", True),
            duration=_Field(1, "last cue"),
        )


def _drop(argv: list[str], drop: _Drop | None = None) -> int:
    return cli.dispatch(
        ["transcript", "drop", *argv],
        daemon=object(),  # type: ignore[arg-type]
        diagnose=lambda: None,  # type: ignore[arg-type,return-value]
        probe_connectors=None,
        transcript_drop=drop or _Drop(),
    )


@pytest.fixture
def dropped_file(tmp_path):
    path = tmp_path / "sync.vtt"
    path.write_text(DROP_VTT, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "argv",
    [
        ["{file}"],
        ["{file}", "--scope", "alpha"],
        ["{file}", "--scope", "project:"],
        ["--scope", "personal"],
        ["{file}", "--scope"],
        ["{file}", "--scope", "personal", "--meeting"],
        ["{file}", "--scope", "personal", "--bogus", "x"],
        ["{file}", "extra", "--scope", "personal"],
        ["{file}", "--scope", "personal", "--scope", "personal"],
    ],
    ids=["no-scope", "scope-alpha", "project-no-id", "no-file", "scope-no-value",
         "meeting-no-value", "unknown-option", "two-files", "scope-twice"],
)
def test_a_drop_command_line_pm_ai_cannot_read_is_the_syntax_reminder(
    argv, dropped_file, capsys
):
    drop = _Drop()
    words = [word.replace("{file}", str(dropped_file)) for word in argv]
    assert _drop(words, drop) == EXIT_USAGE
    assert "drop <file> --scope <scope> [--meeting <meeting>]" in capsys.readouterr().err
    assert drop.calls == []


def test_the_options_may_come_before_the_file(dropped_file):
    drop = _Drop()
    assert _drop(["--scope", "personal", str(dropped_file)], drop) == EXIT_OK
    assert drop.calls[0]["scope"] == DataScope(ScopeKind.PERSONAL)


def test_a_bare_double_dash_ends_the_options(tmp_path, monkeypatch):
    path = tmp_path / "--odd.vtt"
    path.write_text(DROP_VTT, encoding="utf-8")
    drop = _Drop()
    monkeypatch.chdir(tmp_path)
    assert _drop(["--scope", "personal", "--", "--odd.vtt"], drop) == EXIT_OK
    # Story 11d: the drop names the absolute path it read.
    assert drop.calls[0]["file_name"] == str(Path.cwd() / "--odd.vtt")


def test_a_required_option_is_required_on_a_command_without_leaves(monkeypatch, capsys):
    row = cli.Command("demo", lambda context: EXIT_OK, options=("scope",), requires=("scope",))
    monkeypatch.setitem(cli.TABLE, "demo", row)  # type: ignore[arg-type]
    code = cli.dispatch(
        ["demo"],
        daemon=None,
        diagnose=lambda: None,  # type: ignore[arg-type,return-value]
        probe_connectors=None,
    )
    assert code == EXIT_USAGE
    assert "needs `--scope <scope>`" in capsys.readouterr().err


def test_a_named_meeting_is_handed_on_and_nothing_is_asked(dropped_file, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("asked"))
    drop = _Drop()
    code = _drop([str(dropped_file), "--scope", "project:alpha", "--meeting", "mtg_x"], drop)
    assert code == EXIT_OK
    assert drop.calls[0]["meeting_id"] == "mtg_x"
    assert drop.calls[0]["scope"] == DataScope(ScopeKind.PROJECT, "alpha")
    out = capsys.readouterr().out
    assert "mtg_x" in out and "1 staged" in out and "executed" not in out


def test_proposals_left_as_acted_on_are_counted(dropped_file, capsys):
    answer = _Dropped(Path("/c.vtt"), "mtg_m", DataScope(ScopeKind.PERSONAL), "named",
                      staged=0, left=2)
    assert _drop([str(dropped_file), "--scope", "personal"], _Drop(answer=answer)) == EXIT_OK
    assert "2 left as they were, already approved, rejected or expired" in (
        capsys.readouterr().out
    )


@pytest.mark.parametrize("scope", ["people:bob", "application"])
def test_a_scope_a_drop_cannot_go_to_is_refused_by_name(scope, dropped_file, capsys):
    drop = _Drop()
    assert _drop([str(dropped_file), "--scope", scope], drop) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert scope in err and "personal" in err and "project:<id>" in err
    assert drop.calls == []


FOUR_FORMATS = ".vtt, .txt, .md, .docx"


@pytest.mark.parametrize(
    ("name", "said"),
    [
        ("minutes.pdf", "a PDF"),
        ("minutes.doc", "a Word document in the old .doc format"),
        ("minutes.RTF", "a rich-text document"),
    ],
)
def test_a_document_format_is_refused_with_the_way_out(name, said, tmp_path, capsys):
    """Story 11d: `.doc`, `.pdf` and `.rtf` are still refused by name, naming the four."""
    path = tmp_path / name
    path.write_bytes(b"PK\x03\x04")
    drop = _Drop()
    assert _drop([str(path), "--scope", "personal"], drop) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert str(path) in err and said in err and FOUR_FORMATS in err
    assert drop.calls == []


def test_a_docx_is_read_for_its_text_and_handed_on_as_text(tmp_path):
    """Story 11d: the deferral is gone; the pipeline gets the text, marked as from a `.docx`."""
    path = tmp_path / "minutes.docx"
    path.write_bytes(
        (Path(__file__).parents[1] / "core" / "fixtures" / "transcripts"
         / "teams_export_recorded.docx").read_bytes()
    )
    drop = _Drop()
    assert _drop([str(path), "--scope", "personal", "--meeting", "mtg_x"], drop) == EXIT_OK
    (call,) = drop.calls
    assert call["from_docx"] is True
    assert "Alex Morgan   0:32\nPlaceholder line 1 of turn 1.\n" in call["text"]


def test_a_missing_docx_is_refused_as_missing(tmp_path, capsys):
    assert _drop([str(tmp_path / "absent.docx"), "--scope", "personal"]) == EXIT_REFUSAL
    assert "does not exist" in capsys.readouterr().err


def _unusable(tmp_path, kind: str) -> Path:
    if kind == "missing":
        return tmp_path / "absent.vtt"
    if kind == "directory":
        path = tmp_path / "folder.vtt"
        path.mkdir()
        return path
    if kind == "dangling link":
        path = tmp_path / "link.vtt"
        path.symlink_to(tmp_path / "gone.vtt")
        return path
    if kind == "unreadable":
        path = tmp_path / "locked.vtt"
        path.write_text(DROP_VTT)
        path.chmod(0)
        return path
    if kind == "fifo":
        path = tmp_path / "pipe.vtt"
        os.mkfifo(path)
        return path
    if kind == "socket":
        path = Path("/tmp") / f"pm-ai-11c-{os.getpid()}.vtt"
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path))
        listener.close()
        return path
    if kind == "device":
        return Path("/dev/null")
    if kind == "too large":
        path = tmp_path / "huge.vtt"
        with path.open("wb") as handle:
            handle.truncate(cli.MAX_DROP_BYTES + 1)
        return path
    path = tmp_path / "latin1.txt"
    path.write_bytes("Andrei: caf\xe9 at noon\n".encode("latin-1"))
    return path


@pytest.mark.parametrize(
    ("kind", "said"),
    [
        ("missing", "does not exist"),
        ("directory", "is a directory"),
        ("dangling link", "dangling link"),
        ("unreadable", "could not be read"),
        ("fifo", "not a regular file"),
        ("socket", "not a regular file"),
        ("device", "not a regular file"),
        ("too large", "16 MiB"),
        ("not utf-8", "not UTF-8"),
    ],
)
def test_an_unusable_path_is_refused_by_name_not_a_traceback(kind, said, tmp_path, capsys):
    path = _unusable(tmp_path, kind)
    drop = _Drop()
    try:
        assert _drop([str(path), "--scope", "personal"], drop) == EXIT_REFUSAL
    finally:
        if kind == "unreadable":
            path.chmod(0o600)
        if kind == "socket":
            path.unlink()
    err = capsys.readouterr().err
    assert str(path) in err and said in err
    assert "Traceback" not in err
    assert drop.calls == []


def test_without_a_terminal_a_drop_asks_nothing_and_is_handed_on(
    dropped_file, monkeypatch, capsys
):
    """Story 11e: nothing is asked, and nothing refused for missing details."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("asked"))
    drop = _Drop()
    assert _drop([str(dropped_file), "--scope", "personal"], drop) == EXIT_OK
    (call,) = drop.calls
    assert call["title"] is None and call["start"] is None and call["duration"] is None
    assert call["modified"] is not None and call["modified"].tzinfo is not None
    out = capsys.readouterr().out
    assert "Title 'Weekly Sync', from the file name" in out
    assert "estimated from the file modification time" in out
    assert "1 minutes, from the last cue" in out
    assert "Participants Andrei (not linked)." in out


@pytest.mark.parametrize(
    ("typed", "utc"),
    [
        ("2026-10-09T11:00Z", DROP_START),
        ("2026-10-09T14:00:30.250+03:00", DROP_START.replace(second=30, microsecond=250000)),
    ],
)
def test_a_start_in_utc_or_with_fractional_seconds_is_handed_on_as_utc(
    typed, utc, dropped_file
):
    drop = _Drop()
    assert _drop([str(dropped_file), "--scope", "personal", "--start", typed], drop) == EXIT_OK
    assert drop.calls[0]["start"] == utc and drop.calls[0]["start"].tzinfo == _timezone.utc


@pytest.mark.parametrize(
    ("option", "typed", "said"),
    [
        ("--start", "2026-10-09 14:00", "2026-10-10T14:00+03:00"),
        ("--start", "2026-10-09T14:00", "2026-10-10T11:00Z"),
        ("--title", "Weekly\x07Sync", "the title typed"),
        ("--title", "x" * 20000, "the title typed"),
        ("--attendees", "Smith, Bob", "separated by commas with no spaces"),
        ("--attendees", "ana,,bo", "separated by commas with no spaces"),
        ("--attendees", "ana,ana", "separated by commas with no spaces"),
        ("--duration", "0", "give 1 to 1440"),
        ("--duration", "1441", "give 1 to 1440"),
    ],
    ids=["start-space", "start-no-offset", "title-control", "title-long", "attendees-comma",
         "attendees-empty", "attendees-twice", "duration-0", "duration-1441"],
)
def test_an_option_its_rule_refuses_is_a_usage_error(option, typed, said, dropped_file, capsys):
    drop = _Drop()
    assert _drop([str(dropped_file), "--scope", "personal", option, typed], drop) == EXIT_USAGE
    assert said in capsys.readouterr().err
    assert drop.calls == []


def test_the_options_are_handed_on_as_given(dropped_file):
    drop = _Drop()
    code = _drop(
        [str(dropped_file), "--scope", "personal", "--title", "Other", "--duration", "45",
         "--attendees", "email:ana@example.com,email:bo@example.com"],
        drop,
    )
    assert code == EXIT_OK
    (call,) = drop.calls
    assert call["title"] == "Other" and call["duration"] == 45
    assert call["attendees"] == (Actor("email:ana@example.com"), Actor("email:bo@example.com"))


def test_json_without_dry_run_is_a_usage_error_naming_dry_run(dropped_file, capsys):
    drop = _Drop()
    assert _drop([str(dropped_file), "--scope", "personal", "--json"], drop) == EXIT_USAGE
    assert "--dry-run" in capsys.readouterr().err
    assert drop.calls == []


_PREVIEW = _Preview(
    _Field("Weekly Sync", "file header"),
    _Field(DROP_START, "file header", True),
    _Field(None, "unknown"),
    (("Alex Morgan", "email:alex@example.com"), ("Sam Rivera", None)),
    "new",
    candidates=("mtg_a", "mtg_b"),
    zone="Europe/Warsaw",
)


def test_a_dry_run_prints_each_field_its_source_and_the_outcome(dropped_file, capsys):
    drop = _Drop(answer=_PREVIEW)
    assert _drop([str(dropped_file), "--scope", "personal", "--dry-run"], drop) == EXIT_OK
    assert drop.calls[0]["dry_run"] is True
    out = capsys.readouterr().out
    assert "nothing was written" in out
    assert "title: Weekly Sync — from the file header" in out
    assert "2026-10-09T11:00:00+00:00 (2026-10-09 13:00 in Europe/Warsaw)" in out
    assert "estimated from the file header" in out
    assert "length: unknown" in out
    assert "Alex Morgan (email:alex@example.com), Sam Rivera (not linked)" in out
    assert "outcome: new meeting" in out and "mtg_a, mtg_b" in out


def test_a_dry_run_with_json_prints_one_object(dropped_file, capsys):
    drop = _Drop(answer=_PREVIEW)
    code = _drop([str(dropped_file), "--scope", "personal", "--dry-run", "--json"], drop)
    assert code == EXIT_OK
    found = json.loads(capsys.readouterr().out)
    assert found == {
        "title": {"value": "Weekly Sync", "source": "file header", "estimated": False},
        "start": {"value": "2026-10-09T11:00:00+00:00", "source": "file header",
                  "estimated": True},
        "duration_minutes": {"value": None, "source": "unknown", "estimated": False},
        "participants": [
            {"name": "Alex Morgan", "handle": "email:alex@example.com"},
            {"name": "Sam Rivera", "handle": None},
        ],
        "outcome": {"kind": "new", "candidates": ["mtg_a", "mtg_b"]},
    }


def test_a_joined_meeting_is_named_in_the_closing_line(dropped_file, capsys):
    answer = _Dropped(
        Path("/c.vtt"), "mtg_m", DataScope(ScopeKind.PERSONAL), "matched",
        title_field=_Field("Weekly Sync", "calendar"),
        start_field=_Field(DROP_START, "calendar"),
        duration=_Field(30, "calendar"),
    )
    assert _drop([str(dropped_file), "--scope", "personal"], _Drop(answer=answer)) == EXIT_OK
    out = capsys.readouterr().out
    assert "joined the existing meeting 'Weekly Sync' (mtg_m), found by title on the same day" in out
    assert "start 2026-10-09T11:00:00+00:00, from the calendar" in out


@pytest.mark.parametrize(
    "refused",
    [
        CaptureNotIngested(
            "/c/x-manual.vtt is stored and ingesting it failed: MalformedMeeting."
        ),
        DropInterrupted(
            "interrupted during ingestion. /c/x-manual.vtt was written and stays;"
        ),
        OSError(28, "No space left on device"),
    ],
    ids=["not-ingested", "interrupted", "os-error"],
)
def test_a_late_refusal_is_exit_3_with_its_own_sentence(refused, dropped_file, capsys):
    code = _drop([str(dropped_file), "--scope", "personal", "--meeting", "m"],
                 _Drop(raises=refused))
    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert (refused.strerror if isinstance(refused, OSError) else str(refused)) in err
    assert "Traceback" not in err


def test_a_pipeline_refusal_is_exit_3_with_its_own_sentence(dropped_file, capsys):
    from pm_ai.core.meeting_records import MeetingNotFound

    refused = MeetingNotFound("no meeting 'mtg_x' is recorded in personal.")
    assert _drop([str(dropped_file), "--scope", "personal", "--meeting", "mtg_x"],
                 _Drop(raises=refused)) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "no meeting 'mtg_x' is recorded in personal." in err
    assert "\"no meeting" not in err


# ── Story 4t — the `goal` subcommands, flags and subcommand help ─────────────

from pm_ai.surfaces.cli import repl as _repl  # noqa: E402


def test_the_goal_usage_lists_the_five_subcommands_in_order():
    listed = cli.usage(group="goal")
    order = [listed.index(f"  {leaf}") for leaf in ("set", "update", "delete", "list", "show")]
    assert order == sorted(order)
    assert "create" not in cli.TABLE["goal"].leaves


def test_a_flag_is_spelled_in_square_brackets_in_the_usage():
    assert "delete <id> [--yes]" in cli.usage(group="goal")
    assert "set [id] [--domain <domain>] [--horizon <horizon>] [--title <title>]" in cli.usage(
        group="goal"
    )


@pytest.mark.parametrize(
    "argv",
    [["goal", "delete", "g_a", "--yes"], ["goal", "delete", "--yes", "g_a"]],
    ids=["after", "before"],
)
def test_a_flag_never_takes_the_word_after_it(argv):
    parsed = cli.parse(argv)
    assert isinstance(parsed, cli.Parsed)
    assert parsed.arguments == ("g_a",)
    assert parsed.flags == frozenset({"yes"})
    assert parsed.options == {}


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (["goal", "delete", "g_a", "--yes=1"], "takes no value"),
        (["goal", "delete", "g_a", "--yes", "--yes"], "twice"),
        (["goal", "delete", "g_a", "--force"], "has no `--force` option"),
        (["goal", "list", "--yes"], "takes no arguments"),
    ],
    ids=["valued", "twice", "undeclared", "none-declared"],
)
def test_a_misused_flag_is_a_usage_error(argv, said):
    answered = cli.parse(argv)
    assert isinstance(answered, cli.Answered)
    assert answered.code == EXIT_USAGE
    assert said in answered.text


@pytest.mark.parametrize("word", ["--help", "-h"])
def test_a_subcommand_answers_help_with_its_usage_line_and_summary(word):
    answered = cli.parse(["goal", "update", word])
    assert isinstance(answered, cli.Answered)
    assert answered.code == EXIT_OK
    assert answered.text == (
        "usage: pm-ai goal update <id> [--domain <domain>] [--horizon <horizon>] "
        "[--title <title>]\n\nrevise one goal on file; never creates one"
    )


def test_subcommand_help_works_for_a_leaf_without_options():
    answered = cli.parse(["config", "show", "--help"])
    assert isinstance(answered, cli.Answered) and answered.code == EXIT_OK
    assert answered.text.startswith("usage: pm-ai config show\n")


def test_list_and_show_compose_nothing_and_the_writers_need_a_terminal():
    for leaf in ("list", "show"):
        assert cli.TABLE["goal"].leaves[leaf].composes is False
        assert ("goal", leaf) not in cli.TERMINAL_BOUND
    for leaf in ("set", "update", "delete"):
        assert cli.TABLE["goal"].leaves[leaf].composes is True
        assert ("goal", leaf) in cli.TERMINAL_BOUND


def test_the_words_sent_to_the_daemon_keep_their_flags(monkeypatch, tmp_path):
    """`_absolute_paths` rebuilds words from the parse; a flag must survive it."""
    drop = cli.TABLE["transcript"].leaves["drop"]
    monkeypatch.setitem(
        cli.TABLE["transcript"].leaves, "drop", dataclasses.replace(drop, flags=("yes",))
    )
    monkeypatch.chdir(tmp_path)
    words = _repl._absolute_paths(  # noqa: SLF001
        ["transcript", "drop", "--yes", "notes.vtt", "--scope", "personal"]
    )
    assert words == [
        "transcript",
        "drop",
        str(tmp_path / "notes.vtt"),
        "--scope",
        "personal",
        "--yes",
    ]


def test_goal_list_reaches_no_daemon_and_no_writer():
    """Through `dispatch` with only a reader: a broken machine still lists."""

    class Reader:
        path = Path("/nowhere/strategic_goals.md")

        def read(self):
            from pm_ai.core.goal_register import GoalRegister

            return GoalRegister(present=False)

    assert (
        cli.dispatch(
            ["goal", "list"],
            daemon=None,
            diagnose=lambda: (_ for _ in ()).throw(AssertionError("diagnosed")),
            probe_connectors=None,
            goal_reader=Reader(),
        )
        == EXIT_OK
    )
