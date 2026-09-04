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

import ast
import base64
import threading
import time
from pathlib import Path

import pytest

from pm_ai.app import entry
from pm_ai.connectors import registry as connectors
from pm_ai.core.config import ACCEPTED_KEYS, Config, ConfigRefused, load_config
from pm_ai.domain.health import Health, Probe
from pm_ai.ports import (
    AES_KEY_BYTES,
    MASTER_KEY_NAME,
    KeyAlreadyEnrolled,
    KeychainBackendMissing,
    KeychainUnavailable,
    KeyNotFound,
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
    monkeypatch.setattr(entry, "_registered_projects", lambda: {"alpha": repository})
    return home


@pytest.fixture
def unregistered(monkeypatch, keychain):
    """A machine with no project enrolled, so `build()` never runs.

    Which is the arrangement the connector rows want: `build()` *installs* the
    process registry, so a composed daemon would overwrite whatever a test
    registered. It also exercises the claim that `connector check` needs no
    daemon — the registry is a property of the process.
    """
    keychain()
    monkeypatch.setattr(entry, "_registered_projects", dict)


@pytest.fixture
def registry():
    """Install connectors as the process default, and put back what was there.

    The registry is module-level state that `build()` replaces, so a test that
    installed and walked away would describe its own fakes to every architecture
    gate that asks "for every connector, ...".
    """
    previous = connectors.default_registry()
    live: list[Connector] = []

    def install(*connectors_: Connector) -> None:
        fresh = connectors.ConnectorRegistry()
        for connector in connectors_:
            fresh.register(connector)
            live.append(connector)
        connectors.install(fresh)

    yield install
    for connector in live:
        connector.released.set()
    connectors.install(previous)


@pytest.fixture
def quick_bound(monkeypatch):
    """Shorten the wait the CLI hands the registry, without moving CAP-35's bound.

    A blocked probe under the real ten seconds is a correct test that costs ten
    seconds every run, so the *number* is replaced here and pinned separately in
    `test_the_bound_the_cli_uses_is_cap35s_and_is_the_registrys_own`. What is not
    replaced is any of the behaviour under test: the same registry starts the
    same threads and abandons the same probe.
    """
    monkeypatch.setattr(entry, "probe_connectors", lambda: connectors.check_health(timeout=0.5))


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


def test_enrolment_without_a_daemon_is_a_refusal_that_names_the_reason(
    unregistered, capsys
):
    """Key custody belongs to the daemon, so an uncomposed machine has none to offer."""
    assert entry.main(["key", "enrol"]) == EXIT_REFUSAL
    printed = capsys.readouterr().err
    assert "no project is enrolled" in printed
    assert "Traceback" not in printed


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
    path.write_text('pm_handle = "pm@example.com"\n', encoding="utf-8")
    assert entry.main(["config", "show"]) == EXIT_OK
    printed = capsys.readouterr().out
    rows = {line.split(" ")[0]: line for line in printed.splitlines() if line.split(" ")[0] in ACCEPTED_KEYS}
    assert set(rows) == set(ACCEPTED_KEYS)
    assert "(set)" in rows["pm_handle"]
    assert "pm@example.com" in rows["pm_handle"]
    assert "(default)" in rows["blended_hourly_rate"]
    assert "(default)" in rows["verbose_logging"]


def test_every_field_config_carries_is_printed(registered, capsys):
    """The row set is derived from `Config`, so a field added later cannot go unshown.

    A hand-written list of three keys would still print three after `Config`
    grew a fourth, and the missing setting would read as one that does not exist.
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


def test_connector_check_with_every_connector_healthy_exits_0(
    unregistered, registry, capsys
):
    registry(Connector("gitlab:alpha", Health.OK), Connector("gitlab:beta", Health.OK))
    assert entry.main(["connector", "check"]) == EXIT_OK
    printed = capsys.readouterr().out
    assert "gitlab:alpha" in printed
    assert "gitlab:beta" in printed
    assert "2 connectors probed" in printed


def test_a_silent_connector_is_failing_and_its_siblings_still_report(
    unregistered, registry, quick_bound, capsys
):
    """One broken connector hiding another is what `8d`'s report-never-raise rule is for.

    All three claims at once, because they fail independently: the silent one is
    `FAILING`, every sibling is still printed, and the exit code is `4` rather
    than a success that would let a dead connector read forever as "no coverage
    yet" (AD-39).
    """
    registry(
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


def test_a_connector_whose_probe_raises_does_not_hide_the_rest(
    unregistered, registry, capsys
):
    """A bug in one adapter is reported as that adapter's row, not as exit 1."""

    class Exploding(Connector):
        def check_health(self):
            raise RuntimeError("a bug in this adapter")

    registry(Exploding("gitlab:broken", Health.OK), Connector("gitlab:fine", Health.OK))
    assert entry.main(["connector", "check"]) == EXIT_UNHEALTHY
    printed = capsys.readouterr().out
    assert "gitlab:broken" in printed
    assert "gitlab:fine" in printed


def test_an_empty_registry_says_so_and_exits_0(unregistered, registry, capsys):
    """Nothing registered is a first-run state: no claim of reachability is false."""
    registry()
    assert entry.main(["connector", "check"]) == EXIT_OK
    printed = capsys.readouterr().out
    assert "no connectors are registered" in printed


def test_a_connector_with_no_credential_is_not_a_pass(unregistered, registry, capsys):
    """`ABSENT` is setup outstanding, and setup outstanding is not health.

    Distinct from the empty registry above, deliberately: a connector that is
    registered and has no credential is silently skipping harvests, which is
    exactly the state AD-39 says must not read as "no activity".
    """
    registry(Connector("gitlab:alpha", Health.ABSENT))
    assert entry.main(["connector", "check"]) == EXIT_UNHEALTHY
    assert Health.ABSENT.value in capsys.readouterr().out


def test_the_bound_the_cli_uses_is_cap35s_and_is_the_registrys_own():
    """CAP-35's ten seconds, named once — in `8d`, where the waiting happens.

    `quick_bound` shortens the number for the blocked-probe test, so something
    has to pin what production actually passes. The CLI supplies no timeout at
    all, which is the property being asserted: a surface holding its own copy of
    the bound is how the two drift.
    """
    assert connectors.HEALTH_PROBE_SECONDS == 10.0
    assert entry.probe_connectors is connectors.check_health


# ── The table these leaves hang on ───────────────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [["key", "enrol"], ["config", "show"], ["connector", "check"]],
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


def test_connector_add_never_echoes_the_credential(
    registered, monkeypatch, capsys
):
    """The prompt is `getpass`, and nothing prints what it returned."""
    secret = "glpat-typed-at-the-prompt-99"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": secret)
    monkeypatch.setattr(
        cli, "enrol_connector", lambda *a, **k: "gitlab accepted the credential"
    )

    assert entry.main(["connector", "add", "gitlab", "gitlab:alpha"]) == EXIT_OK
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    assert "next start" in captured.out, "the activation promise 8d requires"


def test_an_unprobeable_system_is_refused_rather_than_sealed(
    registered, monkeypatch, capsys
):
    """The default probe refuses; a system pm-ai cannot ask about is not enrolled."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "a-token")
    assert entry.main(["connector", "add", "nosuch", "nosuch:one"]) == EXIT_REFUSAL
    assert "no credential probe" in capsys.readouterr().err.lower()
