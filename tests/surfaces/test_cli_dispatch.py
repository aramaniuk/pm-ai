"""Story 4c — one test per row of the CLI's I/O matrix.

Every assertion is an **exact** exit integer rather than "non-zero". A
dispatcher that collapsed every failure onto 1 would satisfy a truthiness check
and destroy the only thing the exit-code table is for: telling `pm-ai refused`
apart from `pm-ai broke`, from the far side of a shell.

`main()` is called with an explicit argument vector throughout, so none of this
needs a subprocess and every branch is reachable.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pm_ai.app import entry
from pm_ai.platform.doctor import Health, Probe, Report
from pm_ai.ports import MASTER_KEY_NAME, KeyNotFound
from pm_ai.surfaces.cli import dispatch as cli
from pm_ai.surfaces.cli.dispatch import (
    EXIT_OK,
    EXIT_REFUSAL,
    EXIT_UNEXPECTED,
    EXIT_UNHEALTHY,
    EXIT_USAGE,
    Command,
)

PACKAGE_ROOT = Path(entry.__file__).resolve().parent.parent


def report(*probes: Probe) -> Report:
    return Report(probes)


HEALTHY = (
    Probe("runtime packages", Health.OK, "all 8 present"),
    Probe("sqlite extension support", Health.OK, "the interpreter exposes it"),
    Probe("git", Health.OK, "git version 2.0 answers exclusion queries"),
)


@pytest.fixture
def probes(monkeypatch):
    """Replace the real probes, so an exit code says something about the CLI.

    `run_all` touches this machine's git, sqlite and keychain, and a test that
    asserted `doctor` exits 0 through it would be asserting a property of the
    developer's laptop. One test below deliberately does use the real probes —
    to prove they are reachable at all, which is this story's whole point.
    """

    def install(*results: Probe) -> None:
        monkeypatch.setattr(entry, "run_all", lambda keychain: report(*results))

    install(*HEALTHY)
    return install


@pytest.fixture
def registered(tmp_path, monkeypatch):
    """A machine where composition succeeds: one enrolled project, an empty home.

    `HOME` is redirected rather than `ScopePaths.production` stubbed, so the real
    layout code decides where `config.toml` lives and the test still touches
    nothing outside `tmp_path`.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repository = tmp_path / "repo"
    repository.mkdir()
    monkeypatch.setattr(entry, "_registered_projects", lambda: {"alpha": repository})
    return home


# ── Bare invocation, help, and the names that are not commands ───────────────


def test_a_bare_invocation_prints_usage_and_exits_2(capsys):
    """CAP-18's REPL is `4e`; until then a silent success reads as a working install."""
    assert entry.main([]) == EXIT_USAGE
    assert "usage: pm-ai" in capsys.readouterr().err


def test_an_unknown_subcommand_exits_2(capsys):
    assert entry.main(["frobnicate"]) == EXIT_USAGE
    printed = capsys.readouterr().err
    assert "frobnicate" in printed
    assert "usage: pm-ai" in printed


@pytest.mark.parametrize("group", ["key", "config", "connector"])
def test_a_group_named_without_a_leaf_prints_its_leaves_and_exits_2(group, capsys):
    """A group does nothing on its own; naming one is an incomplete command line.

    Rewritten in `4j`, which gave all three groups their leaves. Until then this
    asserted the "no subcommand is implemented yet" branch — the honest answer
    while the leaves were declared and empty, and a claim that would have gone on
    reading green after they landed had this test not moved with them. The
    branch itself is still reachable and still tested, by
    `test_a_group_with_genuinely_no_leaves_says_so` below.
    """
    assert entry.main([group]) == EXIT_USAGE
    printed = capsys.readouterr().err
    assert f"usage: pm-ai {group}" in printed
    assert "subcommand is implemented yet" not in printed
    for leaf in cli.TABLE[group].leaves:
        assert leaf in printed


def test_a_group_with_genuinely_no_leaves_says_so(monkeypatch, capsys):
    """"Not implemented yet" and "you typed the wrong leaf" are different mistakes.

    No group in the table is empty any more, so the branch is exercised against
    one planted here. Deleting the branch instead would remove the answer the
    *next* group to be declared ahead of its leaf needs.
    """
    monkeypatch.setattr(cli, "TABLE", {**cli.TABLE, "later": Command("a group `4x` fills in")})
    assert entry.main(["later"]) == EXIT_USAGE
    assert "subcommand is implemented yet" in capsys.readouterr().err


def test_an_unknown_leaf_is_a_usage_error_not_a_crash(capsys):
    assert entry.main(["key", "frobnicate"]) == EXIT_USAGE
    printed = capsys.readouterr().err
    assert "usage: pm-ai key" in printed
    assert "enrol" in printed


def test_help_prints_usage_and_exits_0(capsys):
    assert entry.main(["--help"]) == EXIT_OK
    printed = capsys.readouterr().out
    assert "usage: pm-ai" in printed
    assert "doctor" in printed


def test_a_system_exit_from_below_becomes_a_returned_code(probes, monkeypatch):
    """`main()` returns; it does not unwind past its caller.

    Nothing in the dispatcher raises `SystemExit` today — the table is
    hand-rolled precisely so `--help` does not — but a library below might, and
    a `main()` that sometimes returns an int and sometimes exits is not the
    thing an explicit `argv` bought.
    """

    def exits(keychain):
        raise SystemExit(7)

    monkeypatch.setattr(entry, "run_all", exits)
    assert entry.main(["doctor"]) == 7


# ── doctor ───────────────────────────────────────────────────────────────────


def test_doctor_runs_the_real_probes_and_prints_them(capsys):
    """Story 1g's diagnostics become reachable for the first time since they were built.

    Deliberately *not* using the `probes` fixture: the claim is that the console
    script reaches `pm_ai.platform.doctor`, and a stubbed `run_all` would prove
    only that the stub is reachable. The exit code is left unasserted here — it
    is a fact about this machine — and asserted exactly everywhere else.
    """
    entry.main(["doctor"])
    printed = capsys.readouterr().out
    for probe in ("sqlite extension support", "keychain", "encryption", "git"):
        assert probe in printed
    assert "pm-ai is" in printed


def test_a_healthy_machine_exits_0(registered, probes, capsys):
    assert entry.main(["doctor"]) == EXIT_OK
    assert "pm-ai is healthy." in capsys.readouterr().out


def test_an_absent_probe_exits_4_rather_than_0(registered, probes, capsys):
    """`ABSENT` is not success: setup is incomplete and encrypted writes will refuse.

    The failure this rules out is the summary an operator trusts while the
    morning briefing quietly cannot decrypt anything.
    """
    probes(
        *HEALTHY,
        Probe("keychain", Health.ABSENT, f"no key named {MASTER_KEY_NAME!r} is stored"),
    )
    assert entry.main(["doctor"]) == EXIT_UNHEALTHY
    assert "pm-ai is NOT healthy." in capsys.readouterr().out


def test_a_warning_alone_exits_4(registered, probes):
    """Encryption disabled is not a failure and is not a pass either."""
    probes(*HEALTHY, Probe("encryption", Health.WARNING, "DISABLED by an env var"))
    assert entry.main(["doctor"]) == EXIT_UNHEALTHY


def test_a_failing_probe_exits_4(registered, probes):
    probes(*HEALTHY, Probe("git", Health.FAILING, "no `git` on this process's PATH"))
    assert entry.main(["doctor"]) == EXIT_UNHEALTHY


def test_every_health_state_maps_somewhere_explicit(registered, probes):
    """All four states, each asserted against the code it is supposed to produce."""
    expected = {
        Health.OK: EXIT_OK,
        Health.WARNING: EXIT_UNHEALTHY,
        Health.ABSENT: EXIT_UNHEALTHY,
        Health.FAILING: EXIT_UNHEALTHY,
    }
    assert set(expected) == set(Health), "a new Health state has no exit code"
    for state, code in expected.items():
        probes(Probe("probe", state, "detail"))
        assert entry.main(["doctor"]) == code, state


# ── A machine that cannot be composed ────────────────────────────────────────


def test_doctor_survives_an_unregistered_machine(probes, capsys):
    """No enrolled project must not make the broken-machine command unreachable.

    `build()` resolves the project scope eagerly and an unregistered project
    raises `UnknownProject`, so without this the first `pm-ai doctor` anyone ran
    would have died before dispatch.
    """
    assert entry.main(["doctor"]) == EXIT_UNHEALTHY
    printed = capsys.readouterr().out
    for probe in HEALTHY:
        assert probe.name in printed, "a probe that needs no daemon stopped running"
    assert "no project is enrolled" in printed


def test_two_registered_projects_are_reported_rather_than_guessed_between(
    tmp_path, monkeypatch, probes, capsys
):
    """Choosing between enrolled projects belongs to the slice that owns the registry."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        entry,
        "_registered_projects",
        lambda: {"alpha": tmp_path / "a", "beta": tmp_path / "b"},
    )
    assert entry.main(["doctor"]) == EXIT_UNHEALTHY
    assert "cannot be resolved" in capsys.readouterr().out


def test_an_unwritable_root_is_a_probe_result_not_a_traceback(
    registered, probes, monkeypatch, capsys
):
    def refuses(self, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(
        "pm_ai.storage.service.StorageService.read_artifact", refuses, raising=True
    )
    assert entry.main(["doctor"]) == EXIT_UNHEALTHY
    captured = capsys.readouterr()
    assert "Permission denied" in captured.out
    assert "Traceback" not in captured.err


# ── config.toml ──────────────────────────────────────────────────────────────


def test_an_absent_config_is_a_first_run_not_an_error(registered, probes, capsys):
    """No `config.toml` yet is the ordinary state of a clean machine."""
    assert not (registered / ".pm-ai" / "config.toml").exists()
    assert entry.main(["doctor"]) == EXIT_OK
    assert "pm-ai is healthy." in capsys.readouterr().out


def test_read_optional_turns_absence_into_a_value(registered, tmp_path):
    """`read_artifact` ends in `read_bytes()` and has no `bytes | None` form."""
    daemon = entry._compose(_Keychain())[0]
    assert daemon is not None
    assert entry.read_optional(
        daemon.storage, scope=entry.APPLICATION, artifact="config.toml"
    ) is None
    path = registered / ".pm-ai" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('pm_handle = "pm@example.com"\n', encoding="utf-8")
    assert (
        entry.read_optional(
            daemon.storage, scope=entry.APPLICATION, artifact="config.toml"
        )
        == b'pm_handle = "pm@example.com"\n'
    )


def test_a_malformed_config_does_not_stop_the_probes(registered, probes, capsys):
    """`doctor` is the command that must survive a broken config.

    The guarantee this slice can make on its own is that dispatch does not load
    `config.toml` before running the probes, so a file that will not parse
    cannot hide a machine that will not work. What the *report* says about the
    config is `4i`'s probe and `4i`'s criterion.
    """
    path = registered / ".pm-ai" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is not = = toml\n", encoding="utf-8")
    assert entry.main(["doctor"]) == EXIT_UNHEALTHY
    printed = capsys.readouterr().out
    for probe in HEALTHY:
        assert probe.name in printed, "a broken config suppressed a probe"
    assert "config.toml" in printed


def test_a_config_reaches_the_daemon_when_it_parses(registered):
    path = registered / ".pm-ai" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('pm_handle = "pm@example.com"\n', encoding="utf-8")
    daemon, failure = entry._compose(_Keychain())
    assert failure is None
    assert daemon is not None
    assert daemon.pm_handle == "pm@example.com"


# ── Bugs, refusals, and the secret that must never be printed ────────────────


def test_an_unexpected_exception_exits_1_with_a_traceback(registered, monkeypatch, capsys):
    """Distinct from 2 and from 3, which is the whole point of having three codes."""

    def explodes(keychain):
        raise RuntimeError("a bug anywhere below")

    monkeypatch.setattr(entry, "run_all", explodes)
    assert entry.main(["doctor"]) == EXIT_UNEXPECTED
    printed = capsys.readouterr().err
    assert "Traceback" in printed
    assert "a bug anywhere below" in printed


def test_a_refusal_exits_3(registered, monkeypatch, capsys):
    """The code `4j`'s leaves and `8b`'s refusals will use, exercised through the table.

    A stated no is not a crash and not a usage error: it is pm-ai understanding
    the request and declining, and an operator's `||` branch has to be able to
    tell the three apart.
    """
    monkeypatch.setattr(
        cli,
        "TABLE",
        {**cli.TABLE, "needy": Command("needs a daemon", lambda c: c.require_daemon() and 0)},
    )
    monkeypatch.setattr(entry, "_registered_projects", dict)
    assert entry.main(["needy"]) == EXIT_REFUSAL
    printed = capsys.readouterr().err
    assert "could not build a daemon" in printed
    assert "Traceback" not in printed


def test_a_leaf_that_has_a_daemon_gets_it(registered, monkeypatch):
    seen: list[object] = []
    monkeypatch.setattr(
        cli,
        "TABLE",
        {**cli.TABLE, "needy": Command("needs a daemon", lambda c: seen.append(c.require_daemon()) or 0)},
    )
    assert entry.main(["needy"]) == EXIT_OK
    assert seen and seen[0].scope.project_id == "alpha"


def test_no_invocation_echoes_anything_about_the_master_key(registered, monkeypatch, capsys):
    """A diagnostic that prints a secret turns a support request into a disclosure.

    Run against the *real* probes, because the keychain probe is the one code
    path in this story that holds a secret at all — it fetches the master key to
    find out whether one is stored, and must report only that it is.
    """
    secret = b"a-master-key-nobody-should-see"
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: _Keychain(secret))
    for argv in (
        [], ["--help"], ["doctor"], ["key"], ["config"], ["connector"], ["frobnicate"]
    ):
        entry.main(list(argv))
        captured = capsys.readouterr()
        for stream in (captured.out, captured.err):
            assert secret.decode() not in stream
            assert repr(secret) not in stream


# ── The table itself ─────────────────────────────────────────────────────────


def test_the_exit_code_table_is_five_distinct_values():
    """Declared in one module so `8b` and `23b` reuse rather than reinvent."""
    table = {
        "success": EXIT_OK,
        "unexpected": EXIT_UNEXPECTED,
        "usage": EXIT_USAGE,
        "refusal": EXIT_REFUSAL,
        "unhealthy": EXIT_UNHEALTHY,
    }
    assert list(table.values()) == [0, 1, 2, 3, 4]
    assert len(set(table.values())) == len(table)


def test_the_cli_names_no_module_from_the_composition_root():
    """`surfaces` sits below `app`, which is why the entry point cannot live here."""
    offenders = []
    for path in (PACKAGE_ROOT / "surfaces").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("pm_ai.app"):
                offenders.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Import) and any(
                alias.name.startswith("pm_ai.app") for alias in node.names
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, offenders


class _Keychain:
    """A keychain that answers, so a test never reaches the real one."""

    def __init__(self, secret: bytes | None = None) -> None:
        self._secret = secret

    def store(self, name: str, secret: bytes) -> None:
        self._secret = secret

    def fetch(self, name: str) -> bytes:
        if self._secret is None:
            raise KeyNotFound(name)
        return self._secret

    def delete(self, name: str) -> None:
        self._secret = None


# ── Findings from the 4c review, pinned ────────────────────────────────────────


def test_the_declared_console_script_resolves_to_main():
    """`[project.scripts]` is the one thing installing pm-ai puts on PATH.

    Every other test calls `entry.main` in process, so renaming it or moving the
    module would break only the installed command — with the suite still green.
    """
    from importlib import metadata

    scripts = [
        point
        for point in metadata.entry_points(group="console_scripts")
        if point.name == "pm-ai"
    ]
    assert scripts, "pyproject declares no `pm-ai` console script"
    assert scripts[0].load() is entry.main


def test_a_system_exit_carrying_a_message_keeps_it_and_is_not_a_usage_error(
    monkeypatch, capsys
):
    """`sys.exit("...")` from a library below is fatal, not a typo.

    Reporting it as exit 2 would tell the operator they mistyped the command,
    and the sentence explaining the real failure would never be printed.
    """

    def explode(*_args, **_kwargs):
        raise SystemExit("the database is on fire")

    monkeypatch.setattr(entry, "_compose", explode)
    assert entry.main(["doctor"]) == EXIT_UNEXPECTED
    assert "the database is on fire" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["-h", "help"])
def test_every_help_form_prints_usage_and_exits_0(flag, capsys):
    """`--help` is asserted elsewhere; the other two forms were declared, untested."""
    assert entry.main([flag]) == EXIT_OK
    assert "usage: pm-ai" in capsys.readouterr().out


def test_a_command_that_takes_no_arguments_says_so(capsys):
    assert entry.main(["doctor", "extra"]) == EXIT_USAGE
    assert "takes no arguments" in capsys.readouterr().err


def test_usage_for_an_unknown_group_falls_back_rather_than_raising():
    """`usage` is exported, so an unknown group must not surface as a KeyError."""
    assert "usage: pm-ai" in cli.usage(group="frobnicate")


def test_the_daemon_carries_the_very_keychain_it_was_built_with(tmp_path):
    """The `keychain` field exists so key custody stays in one place.

    Without this, `4j` or `4h` could be handed a daemon whose keychain is a
    second adapter and nothing in the suite would notice.
    """
    from pm_ai.app.wiring import build

    class Custody:
        def fetch(self, name): raise KeyNotFound(name)
        def store(self, name, secret): return None

    custody = Custody()
    daemon = build(tmp_path, "demo", keychain=custody)
    assert daemon.keychain is custody
