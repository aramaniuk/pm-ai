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
    """Every closing report setup produced, captured on its way out of `run_all`."""
    seen: list[Report] = []
    real = entry.run_all

    def recording(*args, **kwargs) -> Report:
        report = real(*args, **kwargs)
        seen.append(report)
        return report

    monkeypatch.setattr(entry, "run_all", recording)
    return seen


def pm_ai_root(home: Path) -> Path:
    return home / ".pm-ai"


def config_path(home: Path) -> Path:
    return pm_ai_root(home) / "config.toml"


def registry_of(home: Path) -> dict:
    return dict(parse_registry((pm_ai_root(home) / "projects.toml").read_bytes()))


SQLITE_SIDECARS = (".db", ".db-wal", ".db-shm")


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


def answers_for(
    repository: Path, *, project_id: str = "", handle: str = "pm@example.com"
) -> tuple[str, ...]:
    """A full first run: path, id, handle, rate, zone, verbose."""
    return (str(repository), project_id, handle, "85", "Europe/Warsaw", "n")


def setup() -> int:
    return entry.main(["setup"])


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
    assert config.pm_handle == "pm@example.com"
    assert config.blended_hourly_rate == 85.0
    assert config.display_timezone == "Europe/Warsaw"
    (report,) = reports
    probes = by_name(report)
    for name in SETUP_PROBES:
        assert probes[name].health is Health.OK, str(probes[name])
    # The packages probe is FAILING in this repo, so the verdict is honest.
    assert code == (EXIT_OK if report.healthy else EXIT_UNHEALTHY)
    printed = capsys.readouterr().out
    assert "[1/3]" in printed and "[2/3]" in printed and "[3/3]" in printed


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
    assert load_config(config_path(home).read_bytes()).pm_handle == "pm@example.com"


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

    operator.script(str(repository), "")
    assert setup() == EXIT_OK

    assert len(operator.asked) == 2, operator.asked
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

    operator.script(str(tmp_path / "beta"), "")
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
    operator.script(str(tmp_path / "beta"), "")
    assert setup() == EXIT_OK
    assert set(registry_of(home)) == {"alpha", "beta"}
    capsys.readouterr()

    operator.script(str(tmp_path / "alpha"), "")
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
    config_path(home).write_bytes(b"pm_handle = \"pm\"\n")
    config_path(home).chmod(0)
    operator.script(*answers_for(tmp_path / "alpha"))
    try:
        assert setup() == EXIT_REFUSAL
    finally:
        config_path(home).chmod(0o600)

    assert keychain.writes == 0
    assert config_path(home).read_bytes() == b"pm_handle = \"pm\"\n"


def test_an_empty_handle_is_re_asked_and_setup_never_completes_without_one(
    home, tmp_path, keychain, operator, capsys
):
    operator.script(str(tmp_path / "alpha"), "", *([""] * SETUP_ATTEMPTS))

    code = setup()

    assert code != EXIT_OK and code == EXIT_REFUSAL
    handle_prompts = [p for p in operator.asked if p.startswith("pm_handle")]
    assert len(handle_prompts) == SETUP_ATTEMPTS
    assert not config_path(home).exists()
    err = capsys.readouterr().err
    assert "pm_handle is required" in err and "step 3" in err
    # What was done stays done.
    assert keychain.secret is not None
    assert set(registry_of(home)) == {"alpha"}


@pytest.mark.parametrize(
    ("bad_rate", "named"),
    [("-5", "blended_hourly_rate"), ("true", "not a number"), ("nan", "blended_hourly_rate")],
)
def test_an_inadmissible_rate_is_named_and_re_asked(
    home, tmp_path, keychain, operator, capsys, bad_rate, named
):
    operator.script(str(tmp_path / "alpha"), "", "pm", bad_rate, "40", "", "")

    setup()

    assert load_config(config_path(home).read_bytes()).blended_hourly_rate == 40.0
    assert named in capsys.readouterr().err


def test_a_whitespace_handle_is_refused_by_config_itself_and_re_asked(
    home, tmp_path, keychain, operator, capsys
):
    operator.script(str(tmp_path / "alpha"), "", "   ", "pm", "", "", "maybe", "y")

    setup()

    config = load_config(config_path(home).read_bytes())
    assert config.pm_handle == "pm"
    assert config.verbose_logging is True
    err = capsys.readouterr().err
    assert "whitespace" in err
    assert "'maybe' is not y or n" in err
    assert len([p for p in operator.asked if p.startswith("verbose_logging")]) == 2


def test_an_unknown_zone_is_named_and_re_asked(home, tmp_path, keychain, operator, capsys):
    operator.script(str(tmp_path / "alpha"), "", "pm", "", "Europe/Warsav", "Europe/Warsaw", "")

    setup()

    assert load_config(config_path(home).read_bytes()).display_timezone == "Europe/Warsaw"
    assert "Europe/Warsav" in capsys.readouterr().err


def test_inadmissible_answers_to_the_end_exit_three_and_not_a_traceback(
    home, tmp_path, keychain, operator, capsys
):
    operator.script(str(tmp_path / "alpha"), "", "pm", *(["-1"] * SETUP_ATTEMPTS))

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


def test_a_config_without_a_handle_is_completed_and_keeps_what_it_held(
    home, tmp_path, keychain, operator, capsys
):
    """The partial-config path: read, kept as the defaults, and written over
    only because the file did not change underneath."""
    config_path(home).parent.mkdir(parents=True)
    config_path(home).write_bytes(b"blended_hourly_rate = 50\n")
    operator.script(str(tmp_path / "alpha"), "", "pm", "", "", "")

    code = setup()

    assert code != EXIT_REFUSAL
    assert "changed while setup" not in capsys.readouterr().err
    config = load_config(config_path(home).read_bytes())
    assert config.pm_handle == "pm"
    assert config.blended_hourly_rate == 50.0
    (rate_prompt,) = [p for p in operator.asked if p.startswith("blended_hourly_rate")]
    assert "[50.0]" in rate_prompt and "keep" in rate_prompt


def test_a_config_that_changed_during_the_prompts_is_not_written_over(
    home, tmp_path, keychain, operator, monkeypatch, capsys
):
    written_meanwhile = b'pm_handle = "someone-else"\n'
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
    operator.script(str(repository), "")  # stdin closes at the handle prompt

    assert setup() == EXIT_REFUSAL
    assert keychain.writes == 1
    assert set(registry_of(home)) == {"alpha"}
    assert not config_path(home).exists()

    operator.script(*answers_for(repository))
    assert setup() == EXIT_OK

    assert keychain.writes == 1, "the rerun minted a second key"
    assert load_config(config_path(home).read_bytes()).pm_handle == "pm@example.com"


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
        if prompt.startswith("pm_handle") and not failed:
            failed.append(prompt)
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return next(answers)

    monkeypatch.setattr("builtins.input", undecodable_once)

    setup()

    assert load_config(config_path(home).read_bytes()).pm_handle == "pm@example.com"
    assert "not valid text" in capsys.readouterr().err
