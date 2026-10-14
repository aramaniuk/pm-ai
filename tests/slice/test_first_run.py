"""`pm-ai setup` — story 4h's matrix, driven through `entry.main` over a real root.

Two things are stood in and nothing else. The keychain, because the real one
is the developer's login keychain, and because two of the criteria are
assertions about what reached it. And the operator: `input` is scripted and
`sys.stdin.isatty` is set, so every prompt the sequence asks is an answer this
file wrote down. The registry, `config.toml` and the probes are the real ones
over a redirected `HOME`, because every row here is about what is on disk
afterwards.

The packages probe is FAILING in this repo by design (`test_doctor.py`'s
`test_a_fully_healthy_machine_reports_healthy` records why), so the rows that
need an exit of `0` stand it in exactly as that test does — and the criterion
that needs "ready" asserts the three probes setup configures, by name.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from pm_ai.app import entry
from pm_ai.core.config import load_config
from pm_ai.core.project_registry import parse_registry
from pm_ai.domain.health import Health, Probe, Report
from pm_ai.platform import doctor
from pm_ai.platform.environment import DISABLE_ENCRYPTION_VAR
from pm_ai.ports import ArtifactBusy, KeyAlreadyEnrolled, KeychainUnavailable, KeyNotFound
from pm_ai.storage.service import StorageService
from pm_ai.surfaces.cli.dispatch import (
    EXIT_OK,
    EXIT_REFUSAL,
    EXIT_UNHEALTHY,
    EXIT_USAGE,
    SETUP_ATTEMPTS,
    usage,
)

SETUP_PROBES = ("keychain", "project registry", "config.toml")


class Keychain:
    """A `KeychainPort` that records every write it accepts and every one it refuses."""

    def __init__(
        self, secret: bytes | None = None, *, refuses: Exception | None = None
    ) -> None:
        self.secret = secret
        self.refuses = refuses
        self.calls: list[str] = []
        self.writes = 0

    def store(self, name: str, secret: bytes) -> None:
        self.calls.append("store")
        self.writes += 1
        self.secret = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        self.calls.append("store_if_absent")
        if self.refuses is not None:
            raise self.refuses
        if self.secret is not None:
            raise KeyAlreadyEnrolled(name)
        self.writes += 1
        self.secret = secret

    def fetch(self, name: str) -> bytes:
        self.calls.append("fetch")
        if self.refuses is not None:
            raise self.refuses
        if self.secret is None:
            raise KeyNotFound(name)
        return self.secret

    def delete(self, name: str) -> None:
        self.calls.append("delete")
        self.writes += 1
        self.secret = None


class Operator:
    """Scripted answers. Running out is Ctrl-D, which is what a closed stdin is."""

    def __init__(self) -> None:
        self.answers: list[str] = []
        self.asked: list[str] = []

    def script(self, *answers: str) -> None:
        self.answers = list(answers)
        self.asked = []

    def __call__(self, prompt: str = "") -> str:
        self.asked.append(prompt)
        if not self.answers:
            raise EOFError
        answer = self.answers.pop(0)
        if answer == "^C":
            raise KeyboardInterrupt
        return answer


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.delenv(DISABLE_ENCRYPTION_VAR, raising=False)
    return root


@pytest.fixture
def keychain(monkeypatch: pytest.MonkeyPatch) -> Keychain:
    fake = Keychain()
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: fake)
    return fake


@pytest.fixture
def operator(monkeypatch: pytest.MonkeyPatch) -> Operator:
    scripted = Operator()
    monkeypatch.setattr("builtins.input", scripted)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    return scripted


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runtime extra stood in, as `test_doctor.py` stands it in."""
    monkeypatch.setattr(doctor, "missing_distributions", lambda _names: ())


@pytest.fixture
def reports(monkeypatch: pytest.MonkeyPatch) -> list[Report]:
    """Every closing report setup produced, whole, as setup's verdict reads it.

    Captured on its way out of `_FirstRun.diagnose` rather than `run_all`, so it
    carries the `connectors` line `run_all` does not (story 4n).
    """
    seen: list[Report] = []
    real = entry._FirstRun.diagnose

    def recording(self) -> Report:
        report = real(self)
        seen.append(report)
        return report

    monkeypatch.setattr(entry._FirstRun, "diagnose", recording)
    return seen


def pm_ai_root(home: Path) -> Path:
    return home / ".pm-ai"


def config_path(home: Path) -> Path:
    return pm_ai_root(home) / "config.toml"


def registry_of(home: Path) -> dict:
    return dict(parse_registry((pm_ai_root(home) / "projects.toml").read_bytes()))


# `.db.lock` since story 4r: the store is prepared under its own claim, whose
# empty sidecar is declared structure beside it (AD-5), never an artifact.
SQLITE_SIDECARS = (".db", ".db-wal", ".db-shm", ".db.lock")


def digests(root: Path) -> dict[str, str]:
    """Every artifact under `root` by content hash, sqlite's own files excluded.

    `StorageService.__init__` opens `operational.db` on every construction, so
    its bytes change whether or not anything was written — the same exclusion
    `test_project_onboarding.py` makes, for the same reason.
    """
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.name.endswith(SQLITE_SIDECARS)
    }


def by_name(report: Report) -> dict[str, Probe]:
    return {probe.name: probe for probe in report.probes}


def answers_for(repository: Path, *, project_id: str = "") -> tuple[str, ...]:
    """A full first run: path, id, rate, zone, verbose, and "n" to a Graph connector."""
    return (str(repository), project_id, "85", "Europe/Warsaw", "n", "n")


GRAPH_QUESTION = "add a Microsoft Graph connector now?"
"""How step 4's question begins (story 4p)."""


def setup() -> int:
    return entry.main(["setup"])


CONNECTORS_LINE = "] connectors: "
"""How the `connectors` probe's line reads in a printed report (story 4n)."""


def connectors_line(printed: str) -> str:
    """The printed `connectors` line, with its remediation line if it has one."""
    lines = printed.splitlines()
    (at,) = [i for i, line in enumerate(lines) if CONNECTORS_LINE in line]
    following = lines[at + 1 : at + 2]
    remedy = [line for line in following if line.lstrip().startswith("→")]
    return "\n".join([lines[at], *remedy])


def assert_after_registry(printed: str) -> None:
    assert printed.index("] project registry: ") < printed.index(CONNECTORS_LINE), printed


def write_row(home: Path, instance: str, **row: object) -> Path:
    """A `connectors/` row as `connector add` writes one, by hand."""
    directory = pm_ai_root(home) / "connectors"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{instance}.json"
    path.write_text(
        json.dumps({"instance": instance, "enabled": True, **row}), encoding="utf-8"
    )
    return path


# ── Clean machine ────────────────────────────────────────────────────────────


def test_a_clean_machine_reaches_healthy_keychain_registry_and_config_probes(
    home, tmp_path, keychain, operator, reports, capsys
):
    repository = tmp_path / "alpha"
    operator.script(*answers_for(repository))

    code = setup()

    assert keychain.secret is not None and keychain.writes == 1
    assert set(registry_of(home)) == {"alpha"}
    config = load_config(config_path(home).read_bytes())
    assert config.blended_hourly_rate == 85.0
    assert config.display_timezone == "Europe/Warsaw"
    (report,) = reports
    probes = by_name(report)
    for name in SETUP_PROBES:
        assert probes[name].health is Health.OK, str(probes[name])
    # The packages probe is FAILING in this repo, so the verdict is honest.
    assert code == (EXIT_OK if report.healthy else EXIT_UNHEALTHY)
    printed = capsys.readouterr().out
    for step in ("[1/4]", "[2/4]", "[3/4]", "[4/4]"):
        assert step in printed, step
    assert "/3]" not in printed
    # Story 4r: a key and a project enrolled — each says a running daemon sees
    # it only after a restart.
    from pm_ai.surfaces.cli.dispatch import RESTART_SENTENCE

    assert printed.count(RESTART_SENTENCE) == 2


def test_a_clean_machine_with_the_runtime_installed_exits_zero(
    home, tmp_path, keychain, operator, installed, reports
):
    operator.script(*answers_for(tmp_path / "alpha"))

    assert setup() == EXIT_OK
    assert reports[-1].healthy


def test_an_unhealthy_probe_after_a_full_run_exits_four(
    home, tmp_path, keychain, operator, installed, monkeypatch
):
    """`sqlite-vec` unavailable: every configuration step completes regardless."""
    monkeypatch.setattr(
        doctor,
        "sqlite_extension_support",
        lambda: Probe("sqlite extension support", Health.FAILING, "stood in", "fix it"),
    )
    operator.script(*answers_for(tmp_path / "alpha"))

    assert setup() == EXIT_UNHEALTHY
    assert keychain.secret is not None
    assert set(registry_of(home)) == {"alpha"}
    assert load_config(config_path(home).read_bytes()).display_timezone == "Europe/Warsaw"


def test_a_clean_machine_closes_by_saying_no_connector_is_enrolled_and_how_to_add_one(
    home, tmp_path, keychain, operator, reports, capsys
):
    """Story 4n: setup cannot end "healthy" without saying nothing will be harvested."""
    operator.script(*answers_for(tmp_path / "alpha"))

    code = setup()

    printed = capsys.readouterr().out
    line = connectors_line(printed)
    assert "[     ok] connectors: none enrolled" in line, line
    assert "pm-ai connector add graph <name>" in line
    assert "pm-ai connector add gitlab <name>" in line
    assert_after_registry(printed)
    # The exit still comes from the probe verdict, and the line is OK here.
    (report,) = reports
    assert by_name(report)["connectors"].health is Health.OK
    assert code == (EXIT_OK if report.healthy else EXIT_UNHEALTHY)


def test_a_clean_machine_with_the_runtime_installed_still_exits_zero_with_no_connector(
    home, tmp_path, keychain, operator, installed
):
    """Nothing enrolled is an ordinary state: the appended line does not fail setup."""
    operator.script(*answers_for(tmp_path / "alpha"))

    assert setup() == EXIT_OK


def test_setup_exits_unhealthy_when_the_connectors_line_warns(
    home, tmp_path, keychain, operator, installed, reports, capsys
):
    """The line is a probe like the others: a row that cannot be used fails setup."""
    repository = tmp_path / "alpha"
    operator.script(*answers_for(repository))
    assert setup() == EXIT_OK
    (pm_ai_root(home) / "connectors" / "broken.json").parent.mkdir(exist_ok=True)
    (pm_ai_root(home) / "connectors" / "broken.json").write_text("{", encoding="utf-8")
    capsys.readouterr()

    operator.script(str(repository), "", "n")
    assert setup() == EXIT_UNHEALTHY

    report = reports[-1]
    printed = capsys.readouterr()
    # Step 4 closes with this machine's own `connectors` remedy, word for word.
    closing = step_four(printed.out).splitlines()[-1].strip()
    assert closing == by_name(report)["connectors"].remediation
    assert closing.startswith("Repair or remove each row named")
    assert by_name(report)["connectors"].health is Health.WARNING
    assert [p.name for p in report.probes if not p.health.is_healthy] == ["connectors"]
    assert "connectors/broken.json is not valid JSON" in connectors_line(printed.out)


def test_setup_re_run_with_a_connector_enrolled_lists_it(
    home, tmp_path, keychain, operator, installed, capsys
):
    repository = tmp_path / "alpha"
    operator.script(*answers_for(repository))
    assert setup() == EXIT_OK
    write_row(home, "graph:work", system="graph")
    capsys.readouterr()

    operator.script(str(repository), "")
    assert setup() == EXIT_OK

    line = connectors_line(capsys.readouterr().out)
    assert "[     ok] connectors: 1 enrolled: graph:work" in line, line


# ── Second runs ──────────────────────────────────────────────────────────────


def test_a_second_run_with_the_same_answers_changes_no_bytes_and_exits_zero(
    home, tmp_path, keychain, operator, installed
):
    repository = tmp_path / "alpha"
    operator.script(*answers_for(repository))
    assert setup() == EXIT_OK
    before = digests(home)
    secret = keychain.secret

    operator.script(*answers_for(repository))
    assert setup() == EXIT_OK

    assert digests(home) == before
    assert keychain.secret == secret and keychain.writes == 1


def test_a_second_run_asks_nothing_about_a_config_that_is_already_complete(
    home, tmp_path, keychain, operator, installed, capsys
):
    repository = tmp_path / "alpha"
    operator.script(*answers_for(repository))
    setup()
    capsys.readouterr()

    operator.script(str(repository), "", "n")
    assert setup() == EXIT_OK

    # The project's two questions, and step 4's: nothing is enrolled yet.
    assert len(operator.asked) == 3, operator.asked
    assert operator.asked[2].startswith(GRAPH_QUESTION)
    printed = capsys.readouterr().out
    assert "already enrolled" in printed
    assert "already registered" in printed
    assert "already configured" in printed


def test_an_enrolled_key_is_a_completed_step_and_nothing_reaches_the_keychain(
    home, tmp_path, keychain, operator
):
    keychain.secret = b"\x07" * 32

    operator.script(*answers_for(tmp_path / "alpha"))
    setup()

    assert keychain.secret == b"\x07" * 32
    assert keychain.writes == 0
    assert "store" not in keychain.calls and "delete" not in keychain.calls
    # The step completed, so the sequence carried on past it.
    assert set(registry_of(home)) == {"alpha"}


def test_a_different_path_for_a_registered_id_is_refused_as_a_move(
    home, tmp_path, keychain, operator, capsys
):
    operator.script(*answers_for(tmp_path / "alpha"))
    setup()
    before = digests(home)
    capsys.readouterr()

    operator.script(str(tmp_path / "elsewhere"), "alpha")
    code = setup()

    assert code == EXIT_REFUSAL
    assert digests(home) == before
    err = capsys.readouterr().err
    assert "step 2" in err and "already registered" in err


def test_a_new_project_id_on_a_second_run_joins_the_registry_additively(
    home, tmp_path, keychain, operator, installed
):
    operator.script(*answers_for(tmp_path / "alpha"))
    setup()
    config_before = config_path(home).read_bytes()

    operator.script(str(tmp_path / "beta"), "", "n")
    assert setup() == EXIT_OK

    assert set(registry_of(home)) == {"alpha", "beta"}
    assert config_path(home).read_bytes() == config_before


def test_setup_and_doctor_agree_on_a_machine_with_two_projects(
    home, tmp_path, keychain, operator, installed, capsys
):
    """Story 4l — the disagreement that revealed the bug, asserted from both sides.

    Measured on 2026-09-24: with two projects registered, `setup` reported
    healthy and exited 0 while `doctor` reported `FAILING` and exited 4, because
    `setup` calls `run_all()` and `doctor` calls `_diagnose()`, which appended
    the composition failure — and composition failed for no reason other than a
    second project being enrolled.

    Both commands are run, rather than one being run and the other reasoned
    about. The exit codes are compared *and* both are pinned to `EXIT_OK`: two
    commands that agree on 4 would satisfy a comparison alone, which is the
    weaker claim and not the one this row makes.

    The working directory is this repository, which is enrolled as neither
    project — so this is also the machine where no command can act, and it is
    still not a machine with anything wrong with it.
    """
    operator.script(*answers_for(tmp_path / "alpha"))
    assert setup() == EXIT_OK
    operator.script(str(tmp_path / "beta"), "", "n")
    assert setup() == EXIT_OK
    assert set(registry_of(home)) == {"alpha", "beta"}
    capsys.readouterr()

    operator.script(str(tmp_path / "alpha"), "", "n")
    from_setup = setup()
    setup_said = capsys.readouterr().out

    from_doctor = entry.main(["doctor"])
    doctor_said = capsys.readouterr().out

    assert from_setup == from_doctor == EXIT_OK
    for said in (setup_said, doctor_said):
        assert "NOT healthy" not in said
        assert Health.FAILING.value not in said
    # And the second project is not mentioned as a problem by either of them.
    assert "no way to choose" not in setup_said + doctor_said
    assert "2 project(s) enrolled: alpha, beta" in doctor_said
    # Story 4n: both print the same `connectors` line, after the registry line
    # and, in `doctor`, before `project selection`.
    assert connectors_line(setup_said) == connectors_line(doctor_said)
    assert "connectors: none enrolled" in connectors_line(doctor_said)
    assert_after_registry(setup_said)
    assert_after_registry(doctor_said)
    assert doctor_said.index(CONNECTORS_LINE) < doctor_said.index("project selection")


# ── Refusals ─────────────────────────────────────────────────────────────────


def test_a_detached_process_with_no_stdin_is_the_same_refusal(
    home, tmp_path, keychain, operator, monkeypatch, capsys
):
    monkeypatch.setattr("sys.stdin", None)
    operator.script(*answers_for(tmp_path / "alpha"))

    assert setup() == EXIT_REFUSAL

    assert "store_if_absent" not in keychain.calls and keychain.writes == 0
    assert keychain.secret is None and operator.asked == []
    assert "TTY" in capsys.readouterr().err


def test_without_a_tty_nothing_reaches_the_keychain_or_the_disk(
    home, tmp_path, keychain, operator, monkeypatch, capsys
):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    operator.script(*answers_for(tmp_path / "alpha"))

    assert setup() == EXIT_REFUSAL

    assert "store_if_absent" not in keychain.calls and keychain.writes == 0
    assert keychain.secret is None
    assert operator.asked == []
    assert digests(home) == {}
    assert not (tmp_path / "alpha").exists()
    assert "TTY" in capsys.readouterr().err


def test_an_unreachable_keychain_stops_setup_at_the_first_step(
    home, tmp_path, keychain, operator, capsys
):
    keychain.refuses = KeychainUnavailable("the keychain is locked")
    operator.script(*answers_for(tmp_path / "alpha"))

    assert setup() == EXIT_REFUSAL

    assert operator.asked == []
    assert not (pm_ai_root(home) / "projects.toml").exists()
    assert not config_path(home).exists()
    err = capsys.readouterr().err
    assert "step 1" in err and "the keychain is locked" in err


def test_a_malformed_config_is_refused_by_name_and_left_byte_identical(
    home, tmp_path, keychain, operator, capsys
):
    config_path(home).parent.mkdir(parents=True)
    malformed = b"verbose_loging = true\n# hand-edited\n"
    config_path(home).write_bytes(malformed)
    operator.script(*answers_for(tmp_path / "alpha"))

    assert setup() == EXIT_REFUSAL

    assert config_path(home).read_bytes() == malformed
    # Refused before anything was written, the keychain included.
    assert keychain.writes == 0 and "store_if_absent" not in keychain.calls
    assert not (pm_ai_root(home) / "projects.toml").exists()
    assert "verbose_loging" in capsys.readouterr().err


def test_an_unreadable_config_is_refused_before_anything_is_written(
    home, tmp_path, keychain, operator, capsys
):
    config_path(home).parent.mkdir(parents=True)
    config_path(home).write_bytes(b"verbose_logging = true\n")
    config_path(home).chmod(0)
    operator.script(*answers_for(tmp_path / "alpha"))
    try:
        assert setup() == EXIT_REFUSAL
    finally:
        config_path(home).chmod(0o600)

    assert keychain.writes == 0
    assert config_path(home).read_bytes() == b"verbose_logging = true\n"


def test_setup_on_a_fresh_home_never_asks_who_the_pm_is(
    home, tmp_path, keychain, operator, installed, capsys
):
    """Story 2o: who the PM is comes from the Graph connector rows, so a fresh
    home is set up without being asked, and every config answer may be empty."""
    operator.script(str(tmp_path / "alpha"), "", "", "", "", "")

    assert setup() == EXIT_OK

    assert not any("pm_handle" in prompt for prompt in operator.asked), operator.asked
    assert len(operator.asked) == 6, operator.asked
    assert load_config(config_path(home).read_bytes()) == load_config(None)
    assert "pm_handle" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("bad_rate", "named"),
    [("-5", "blended_hourly_rate"), ("true", "not a number"), ("nan", "blended_hourly_rate")],
)
def test_an_inadmissible_rate_is_named_and_re_asked(
    home, tmp_path, keychain, operator, capsys, bad_rate, named
):
    operator.script(str(tmp_path / "alpha"), "", bad_rate, "40", "", "", "n")

    setup()

    assert load_config(config_path(home).read_bytes()).blended_hourly_rate == 40.0
    assert named in capsys.readouterr().err


def test_an_inadmissible_flag_is_named_and_re_asked(
    home, tmp_path, keychain, operator, capsys
):
    operator.script(str(tmp_path / "alpha"), "", "", "", "maybe", "y", "n")

    setup()

    config = load_config(config_path(home).read_bytes())
    assert config.verbose_logging is True
    err = capsys.readouterr().err
    assert "'maybe' is not y or n" in err
    assert len([p for p in operator.asked if p.startswith("verbose_logging")]) == 2


def test_an_unknown_zone_is_named_and_re_asked(home, tmp_path, keychain, operator, capsys):
    operator.script(
        str(tmp_path / "alpha"), "", "", "Europe/Warsav", "Europe/Warsaw", "", "n"
    )

    setup()

    assert load_config(config_path(home).read_bytes()).display_timezone == "Europe/Warsaw"
    assert "Europe/Warsav" in capsys.readouterr().err


def test_inadmissible_answers_to_the_end_exit_three_and_not_a_traceback(
    home, tmp_path, keychain, operator, capsys
):
    operator.script(str(tmp_path / "alpha"), "", *(["-1"] * SETUP_ATTEMPTS))

    assert setup() == EXIT_REFUSAL

    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert not config_path(home).exists()


def test_a_refused_config_write_names_the_step_and_leaves_the_earlier_ones_done(
    home, tmp_path, keychain, operator, monkeypatch, capsys
):
    real = StorageService.write_artifact

    def refusing(self, payload, *, scope, artifact, name=None):
        if artifact == "config.toml":
            raise PermissionError(13, "Permission denied", artifact)
        return real(self, payload, scope=scope, artifact=artifact, name=name)

    monkeypatch.setattr(StorageService, "write_artifact", refusing)
    operator.script(*answers_for(tmp_path / "alpha"))

    assert setup() == EXIT_REFUSAL

    assert keychain.secret is not None
    assert set(registry_of(home)) == {"alpha"}
    assert not config_path(home).exists()
    err = capsys.readouterr().err
    assert "step 3" in err and "Permission denied" in err


def test_a_busy_config_write_is_a_refusal_naming_step_three(
    home, tmp_path, keychain, operator, monkeypatch, capsys
):
    real = StorageService.write_artifact

    def busy(self, payload, *, scope, artifact, name=None):
        if artifact == "config.toml":
            raise ArtifactBusy("config.toml is claimed by another writer")
        return real(self, payload, scope=scope, artifact=artifact, name=name)

    monkeypatch.setattr(StorageService, "write_artifact", busy)
    operator.script(*answers_for(tmp_path / "alpha"))

    assert setup() == EXIT_REFUSAL

    assert "step 3" in capsys.readouterr().err
    assert not config_path(home).exists()


def test_a_partial_config_is_completed_and_keeps_what_it_held(
    home, tmp_path, keychain, operator, capsys
):
    """The partial-config path: only the unset keys are asked, what the file sets
    is kept, and it is written over only because it did not change underneath."""
    config_path(home).parent.mkdir(parents=True)
    config_path(home).write_bytes(b"blended_hourly_rate = 50\n")
    operator.script(str(tmp_path / "alpha"), "", "Europe/Warsaw", "n")

    code = setup()

    assert code != EXIT_REFUSAL
    assert "changed while setup" not in capsys.readouterr().err
    config = load_config(config_path(home).read_bytes())
    assert config.blended_hourly_rate == 50.0
    assert config.display_timezone == "Europe/Warsaw"
    asked = [p.split(" ", 1)[0] for p in operator.asked[2:-1]]
    assert asked == ["display_timezone,"], operator.asked
    assert operator.asked[-1].startswith(GRAPH_QUESTION)
    assert not any(p.startswith("blended_hourly_rate") for p in operator.asked)


def test_a_complete_config_is_a_completed_step_and_left_byte_identical(
    home, tmp_path, keychain, operator, capsys
):
    """Rate and zone both set: nothing asked, nothing written."""
    config_path(home).parent.mkdir(parents=True)
    body = b'blended_hourly_rate = 50\ndisplay_timezone = "Europe/Warsaw"\n'
    config_path(home).write_bytes(body)
    operator.script(str(tmp_path / "alpha"), "", "n")

    assert setup() != EXIT_REFUSAL

    assert config_path(home).read_bytes() == body
    assert len(operator.asked) == 3, operator.asked
    assert "already configured" in capsys.readouterr().out


@pytest.mark.parametrize(
    "body",
    [b"\n", b"   \n\t\n", b"\xef\xbb\xbf", b"# only a comment\n# and another\n"],
    ids=["newline", "whitespace", "bom", "comments"],
)
def test_a_config_holding_no_setting_is_a_first_run_file(
    home, tmp_path, keychain, operator, capsys, body
):
    """Decided from what `load_config` reads, not from the bytes: every key is asked."""
    config_path(home).parent.mkdir(parents=True)
    config_path(home).write_bytes(body)
    operator.script(*answers_for(tmp_path / "alpha"))

    assert setup() != EXIT_REFUSAL

    assert "changed while setup" not in capsys.readouterr().err
    assert len(operator.asked) == 6, operator.asked
    config = load_config(config_path(home).read_bytes())
    assert config.blended_hourly_rate == 85.0
    assert config.display_timezone == "Europe/Warsaw"


def test_a_config_that_changed_during_the_prompts_is_not_written_over(
    home, tmp_path, keychain, operator, monkeypatch, capsys
):
    written_meanwhile = b"verbose_logging = true\n"
    answers = iter(answers_for(tmp_path / "alpha"))

    def racing(prompt: str = "") -> str:
        if prompt.startswith("verbose_logging"):
            config_path(home).write_bytes(written_meanwhile)
        return next(answers)

    monkeypatch.setattr("builtins.input", racing)

    assert setup() == EXIT_REFUSAL

    assert config_path(home).read_bytes() == written_meanwhile
    assert "changed while setup" in capsys.readouterr().err


# ── Interruption ─────────────────────────────────────────────────────────────


def test_an_interrupted_setup_keeps_what_it_did_and_a_rerun_continues(
    home, tmp_path, keychain, operator, installed
):
    repository = tmp_path / "alpha"
    operator.script(str(repository), "")  # stdin closes at the rate prompt

    assert setup() == EXIT_REFUSAL
    assert keychain.writes == 1
    assert set(registry_of(home)) == {"alpha"}
    assert not config_path(home).exists()

    operator.script(*answers_for(repository))
    assert setup() == EXIT_OK

    assert keychain.writes == 1, "the rerun minted a second key"
    assert load_config(config_path(home).read_bytes()).display_timezone == "Europe/Warsaw"


def test_ctrl_c_at_a_prompt_is_a_refusal(home, tmp_path, keychain, operator, capsys):
    operator.script(str(tmp_path / "alpha"), "", "^C")

    assert setup() == EXIT_REFUSAL
    assert not config_path(home).exists()
    assert "stays done" in capsys.readouterr().err


# ── Surface ──────────────────────────────────────────────────────────────────


def test_setup_is_in_the_table_and_takes_no_arguments(home, keychain, operator, capsys):
    assert "setup" in usage()
    assert entry.main(["setup", "--now"]) == EXIT_USAGE
    assert keychain.writes == 0


def test_the_absent_key_remediation_names_pm_ai_setup():
    probe = doctor.keychain_reachable(Keychain())

    assert probe.health is Health.ABSENT
    assert "pm-ai setup" in probe.remediation


def test_setup_never_asks_about_encryption(home, tmp_path, keychain, operator):
    operator.script(*answers_for(tmp_path / "alpha"))

    setup()

    assert not any("encrypt" in prompt.lower() for prompt in operator.asked)


def test_an_undecodable_answer_is_re_asked_rather_than_a_traceback(
    home, tmp_path, keychain, operator, monkeypatch, capsys
):
    answers = iter(answers_for(tmp_path / "alpha"))
    failed = []

    def undecodable_once(prompt: str = "") -> str:
        if prompt.startswith("blended_hourly_rate") and not failed:
            failed.append(prompt)
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return next(answers)

    monkeypatch.setattr("builtins.input", undecodable_once)

    setup()

    assert load_config(config_path(home).read_bytes()).display_timezone == "Europe/Warsaw"
    assert "not valid text" in capsys.readouterr().err


# ── Step 4: an optional Microsoft Graph connector (story 4p) ─────────────────


_REPORT_LINE = re.compile(r"^\[\s*(ok|warning|absent|failing)\] ", re.MULTILINE)


def step_four(printed: str) -> str:
    """What setup printed for step 4, up to the closing report's first line.

    Not up to the first blank line: a "yes" prints one before the sign-in.
    """
    start = printed.index("[4/4]")
    report = _REPORT_LINE.search(printed, start)
    end = report.start() if report else len(printed)
    return printed[start:end].rstrip()


def test_step_four_says_it_is_optional_and_why(home, tmp_path, keychain, operator, capsys):
    operator.script(*answers_for(tmp_path / "alpha"))

    setup()

    heading = step_four(capsys.readouterr().out).splitlines()[0]
    assert "optional" in heading
    assert "browser" in heading and "app (client) id" in heading


@pytest.mark.parametrize("declined", ["n", "no", "N", ""], ids=["n", "no", "N", "enter"])
def test_no_or_enter_skips_the_step_closing_with_the_connectors_lines_remedy(
    home, tmp_path, keychain, operator, installed, reports, capsys, declined
):
    repository = tmp_path / "alpha"
    operator.script(str(repository), "", "85", "Europe/Warsaw", "n", declined)

    assert setup() == EXIT_OK

    assert sum(p.startswith(GRAPH_QUESTION) for p in operator.asked) == 1
    assert len(operator.asked) == 6, "something about Graph was asked after the no"
    assert not (pm_ai_root(home) / "connectors").exists()
    assert not (pm_ai_root(home) / "private" / "config.json").exists()
    (report,) = reports
    connectors = by_name(report)["connectors"]
    assert "none enrolled" in connectors.detail
    # The step's closing sentence is 4n's remedy, word for word.
    closing = step_four(capsys.readouterr().out).splitlines()[-1].strip()
    assert closing == connectors.remediation
    assert closing.startswith("Nothing will be harvested until one is added")


def test_neither_y_nor_n_is_asked_again_twice_then_setup_stops_at_step_four(
    home, tmp_path, keychain, operator, capsys
):
    repository = tmp_path / "alpha"
    operator.script(*answers_for(repository)[:-1], *(["maybe"] * SETUP_ATTEMPTS))

    assert setup() == EXIT_REFUSAL

    assert sum(p.startswith(GRAPH_QUESTION) for p in operator.asked) == SETUP_ATTEMPTS
    err = capsys.readouterr().err
    assert "'maybe' is not y or n" in err
    assert "setup stopped at step 4" in err and "Steps 1–3 stay done" in err
    # The refusal quotes the question as asked, without its default.
    assert repr("add a Microsoft Graph connector now? (y/n)") in err
    # Steps 1–3 stay done.
    assert keychain.secret is not None
    assert set(registry_of(home)) == {"alpha"}
    assert load_config(config_path(home).read_bytes()).display_timezone == "Europe/Warsaw"


@pytest.mark.parametrize("interruption", ["^C", None], ids=["ctrl-c", "ctrl-d"])
def test_an_interruption_at_the_question_stops_at_step_four_with_steps_one_to_three_done(
    home, tmp_path, keychain, operator, capsys, interruption
):
    answers = answers_for(tmp_path / "alpha")[:-1]
    # Running out of answers is Ctrl-D.
    operator.script(*answers, *([interruption] if interruption else []))

    assert setup() == EXIT_REFUSAL

    err = capsys.readouterr().err
    assert "interrupted at step 4" in err and "Steps 1–3 stay done" in err
    assert "nothing was saved for the connector" in err
    assert "pm-ai: interrupted" not in err
    assert set(registry_of(home)) == {"alpha"} and config_path(home).exists()
    assert not (pm_ai_root(home) / "connectors").exists()


def test_a_second_run_with_a_graph_connector_enrolled_asks_nothing_and_changes_no_bytes(
    home, tmp_path, keychain, operator, installed, capsys
):
    repository = tmp_path / "alpha"
    operator.script(*answers_for(repository))
    assert setup() == EXIT_OK
    write_row(home, "graph:work", system="graph")
    before = digests(home)
    capsys.readouterr()

    operator.script(str(repository), "")
    assert setup() == EXIT_OK

    assert not any(p.startswith(GRAPH_QUESTION) for p in operator.asked), operator.asked
    assert digests(home) == before
    said = step_four(capsys.readouterr().out)
    assert "already enrolled: graph:work" in said and "nothing was asked or written" in said


def test_a_second_run_with_two_graph_connectors_names_both_and_asks_nothing(
    home, tmp_path, keychain, operator, installed, capsys
):
    repository = tmp_path / "alpha"
    operator.script(*answers_for(repository))
    assert setup() == EXIT_OK
    write_row(home, "graph:work", system="graph")
    write_row(home, "graph:home", system="graph")
    before = digests(home)
    capsys.readouterr()

    operator.script(str(repository), "")
    assert setup() == EXIT_OK

    assert not any(p.startswith(GRAPH_QUESTION) for p in operator.asked)
    assert digests(home) == before
    said = step_four(capsys.readouterr().out)
    assert "already enrolled: graph:home, graph:work" in said, said


@pytest.mark.parametrize(
    ("instance", "row"),
    [
        ("gitlab:alpha", {"system": "gitlab"}),
        ("graph:work", {"system": "graph", "enabled": False}),
    ],
    ids=["gitlab-only", "disabled-graph"],
)
def test_a_second_run_with_no_enabled_graph_connector_is_asked(
    home, tmp_path, keychain, operator, installed, instance, row
):
    repository = tmp_path / "alpha"
    operator.script(*answers_for(repository))
    assert setup() == EXIT_OK
    write_row(home, instance, **row)

    operator.script(str(repository), "", "n")
    setup()

    assert operator.asked[-1].startswith(GRAPH_QUESTION), operator.asked


def test_a_second_run_after_answering_no_asks_again(
    home, tmp_path, keychain, operator, installed
):
    repository = tmp_path / "alpha"
    operator.script(*answers_for(repository))
    assert setup() == EXIT_OK

    operator.script(str(repository), "", "n")
    assert setup() == EXIT_OK

    assert operator.asked[-1].startswith(GRAPH_QUESTION)


@pytest.mark.parametrize(
    ("fault", "said"),
    [
        (KeyboardInterrupt(), "interrupted at step 4"),
        (PermissionError(13, "Permission denied", "connectors"), "Permission denied"),
    ],
    ids=["ctrl-c", "unreadable"],
)
def test_a_fault_reading_the_connectors_is_the_step_four_refusal(
    home, tmp_path, keychain, operator, monkeypatch, capsys, fault, said
):
    def failing(self):
        raise fault

    monkeypatch.setattr(entry._FirstRun, "connectors", failing)
    operator.script(*answers_for(tmp_path / "alpha"))

    assert setup() == EXIT_REFUSAL

    err = capsys.readouterr().err
    assert said in err and "step 4" in err and "Steps 1–3 stay done" in err
    assert "Traceback" not in err and "pm-ai: interrupted" not in err
    assert set(registry_of(home)) == {"alpha"} and config_path(home).exists()
