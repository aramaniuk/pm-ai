"""Story 4p — `pm-ai setup`'s step 4 answered "yes", one test per matrix row.

The "no" rows are in `test_first_run.py`. These run `entry.main(["setup"])`
over a real temporary home, reusing story 8l's fakes: the keychain (a master
key already enrolled, so step 1 is done) and Microsoft, the MSAL client
injected through the composition root's `GraphEnrolment` — which step 4's
sign-in goes through, as `connector add graph`'s does. The sealed store, its
encryption and the `connectors/` row are the real ones.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path

import pytest

from pm_ai.app import entry
from pm_ai.app.wiring import GraphEnrolment, build
from pm_ai.connectors.graph import GraphConnector
from pm_ai.connectors.probe import CREDENTIAL_PROBE_SECONDS
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.platform import doctor
from pm_ai.platform.environment import DISABLE_ENCRYPTION_VAR
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import ArtifactBusy
from pm_ai.storage.service import StorageService
from pm_ai.surfaces.cli import dispatch as cli
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, EXIT_UNHEALTHY, SETUP_ATTEMPTS
from tests.slice.test_connector_add_graph import (  # noqa: F401 — `machine` is a fixture
    CLIENT_ID,
    FLOW_MESSAGE,
    INSTANCE,
    Keychain,
    ROTATED,
    SIGNED_IN,
    VALID,
    Microsoft,
    Terminal,
    _microsoft,
    _nothing_saved,
    _token,
    machine,
)
from tests.slice.test_first_run import GRAPH_QUESTION, digests, step_four, write_row

APPLICATION = DataScope(ScopeKind.APPLICATION)

FIRST_STEPS = ["85", "Europe/Warsaw", "n"]
"""Rate, zone and verbose: step 3 on a first run."""

SHARED_INTERRUPT_SENTENCES = (
    "interrupted before every question was answered",
    "interrupted; nothing was saved.",
    "pm-ai: interrupted",
)
"""What `connector add graph` and `main` say when interrupted — never step 4."""

STOPPED = "setup stopped at step 4"
DONE = "Steps 1–3 stay done"
NOTHING = "nothing was saved for the connector"
ENCRYPTION_WARNING = "WARNING: encryption is disabled"


@pytest.fixture(autouse=True)
def installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runtime extra stood in, so a healthy machine's setup exits 0."""
    monkeypatch.setattr(doctor, "missing_distributions", lambda _names: ())


def _setup(
    monkeypatch, repository: Path, *step_four: str | BaseException, again: bool = False
) -> tuple[int, Terminal]:
    """Run setup; `again` is a second run, whose step 3 asks nothing."""
    terminal = Terminal([str(repository), "", *([] if again else FIRST_STEPS), *step_four])
    monkeypatch.setattr("builtins.input", terminal)
    return entry.main(["setup"]), terminal


def _row_path(home: Path, instance: str = INSTANCE) -> Path:
    return home / ".pm-ai" / "connectors" / f"{instance}.json"


def _sealed(home: Path) -> Path:
    return home / ".pm-ai" / "private" / "config.json"


def _steps_one_to_three_done(home: Path, keychain) -> None:
    assert keychain.secret is not None
    assert (home / ".pm-ai" / "projects.toml").exists()
    assert (home / ".pm-ai" / "config.toml").exists()


def _after(terminal: Terminal, question: str) -> list[str]:
    """The prompts shown after the first one starting with `question`."""
    at = [i for i, p in enumerate(terminal.prompts) if p.startswith(question)]
    assert at, f"{question!r} was never asked; the prompts were {terminal.prompts}"
    return terminal.prompts[at[0] + 1 :]


def _names_asked(terminal: Terminal) -> list[str]:
    return [p for p in terminal.prompts if p.startswith("connector name")]


def _failing_row_write(monkeypatch, error: BaseException) -> None:
    """Make writing a `connectors/` row raise `error`; every other write is real."""
    real = StorageService.write_artifact

    def failing(self, payload, *, scope, artifact, name=None):
        if artifact == "connectors/":
            raise error
        return real(self, payload, scope=scope, artifact=artifact, name=name)

    monkeypatch.setattr(StorageService, "write_artifact", failing)


# ── Yes ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("yes", ["y", "yes", "Y"])
def test_yes_and_enter_at_the_name_saves_graph_work_as_connector_add_graph_does(
    machine, tmp_path, monkeypatch, capsys, yes
):
    home, keychain = machine
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _setup(monkeypatch, tmp_path / "alpha", yes, "", *VALID)

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "[4/4]" in captured.out
    assert any(p.startswith("connector name [graph:work]") for p in terminal.prompts)
    said = step_four(captured.out)
    assert FLOW_MESSAGE in said, "the sign-in is part of step 4's output"
    assert "graph:work is enrolled and healthy" in said
    assert _row_path(home).exists() and _sealed(home).exists()
    # The closing report is 4n's, and lists it.
    assert "connectors: 1 enrolled: graph:work" in captured.out

    # A fresh build holds a Graph connector of that name.
    daemon = build(
        None,
        "alpha",
        paths=ScopePaths.production(home=home, projects={"alpha": tmp_path / "alpha"}),
        keychain=keychain,
    )
    assert isinstance(daemon.connectors.get(INSTANCE), GraphConnector)


def test_enter_at_the_reach_back_through_setup_keeps_five_days(
    machine, tmp_path, monkeypatch, capsys
):
    """Story 8s: step 4 runs `connector add graph`'s own questions, so Enter at
    the reach-back keeps the same 7200 minutes, saved by the same path."""
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _setup(monkeypatch, tmp_path / "alpha", "y", "", CLIENT_ID, "", "240", "", "")

    assert code == EXIT_OK, capsys.readouterr().err
    (reach,) = [p for p in terminal.prompts if p.startswith("First-run reach-back")]
    assert "[Enter keeps 7200]" in reach
    row = json.loads(_row_path(home).read_text())
    assert (row["window_width_minutes"], row["first_run_reach_back_minutes"]) == (240, 7200)


def test_a_typed_name_is_saved_under_that_name(machine, tmp_path, monkeypatch, capsys):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())

    code, _ = _setup(monkeypatch, tmp_path / "alpha", "y", "graph:acme", *VALID)

    assert code == EXIT_OK, capsys.readouterr().err
    assert _row_path(home, "graph:acme").exists()
    assert not _row_path(home).exists()


def test_yes_on_a_second_run_whose_first_three_steps_ask_nothing(
    machine, tmp_path, monkeypatch, capsys
):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())
    assert _setup(monkeypatch, tmp_path / "alpha", "n")[0] == EXIT_OK
    capsys.readouterr()

    code, terminal = _setup(monkeypatch, tmp_path / "alpha", "y", "", *VALID, again=True)

    assert code == EXIT_OK, capsys.readouterr().err
    assert len(terminal.prompts) == 2 + 1 + 1 + len(VALID), terminal.prompts
    assert _row_path(home).exists()


def test_the_sign_in_token_appears_nowhere_on_screen(machine, tmp_path, monkeypatch, capsys):
    _microsoft(monkeypatch, Microsoft())

    code, _ = _setup(monkeypatch, tmp_path / "alpha", "y", "", *VALID)

    captured = capsys.readouterr()
    assert code == EXIT_OK
    for token in (SIGNED_IN, ROTATED, "an-access-token"):
        assert token not in captured.out and token not in captured.err


def test_with_encryption_off_the_warning_is_printed_once_before_the_seal(
    tmp_path, monkeypatch, capsys
):
    """A fresh home: no project, so `main` composes no daemon and prints nothing."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(DISABLE_ENCRYPTION_VAR, "1")
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: Keychain(secret=None))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    _microsoft(monkeypatch, Microsoft())
    seen_at_the_seal: list[int] = []
    real_write = StorageService.write_artifact

    def counting(self, payload, *, scope, artifact, name=None):
        if artifact == "config.json":
            seen_at_the_seal.append(capsys.readouterr().err.count(ENCRYPTION_WARNING))
        return real_write(self, payload, scope=scope, artifact=artifact, name=name)

    monkeypatch.setattr(StorageService, "write_artifact", counting)

    code, _ = _setup(monkeypatch, tmp_path / "alpha", "y", "", *VALID)

    err = capsys.readouterr().err
    # Encryption off is a WARNING probe, so the verdict is unhealthy, not a refusal.
    assert code == EXIT_UNHEALTHY, err
    assert seen_at_the_seal == [1], "the warning was not printed once before the seal"
    assert err.count(ENCRYPTION_WARNING) == 0, "printed again after the seal"
    assert _sealed(home).exists()


# ── Names ────────────────────────────────────────────────────────────────────


def test_one_refused_name_is_asked_again_and_the_good_one_is_saved(
    machine, tmp_path, monkeypatch, capsys
):
    home, _ = machine
    write_row(home, INSTANCE, system="graph", enabled=False)
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _setup(
        monkeypatch, tmp_path / "alpha", "y", "graph/bad", "graph:acme", *VALID
    )

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "graph/bad" in captured.err
    assert len(_names_asked(terminal)) == 2
    assert _row_path(home, "graph:acme").exists()


def test_the_default_is_withheld_when_graph_work_is_in_use(
    machine, tmp_path, monkeypatch, capsys
):
    home, _ = machine
    disabled = write_row(home, INSTANCE, system="graph", enabled=False)
    before = disabled.read_bytes()
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _setup(monkeypatch, tmp_path / "alpha", "y", "", "graph:acme", *VALID)

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    names = _names_asked(terminal)
    assert len(names) == 2 and all("[graph:work]" not in p for p in names), names
    assert "graph:<name>" in names[0]
    assert "a name is required" in captured.err
    assert _row_path(home, "graph:acme").exists()
    assert disabled.read_bytes() == before


@pytest.mark.parametrize(
    "fault", [ArtifactBusy("config.json is claimed"), PermissionError(13, "Permission denied")]
)
def test_a_default_that_cannot_be_checked_is_withheld_not_a_stop(
    machine, tmp_path, monkeypatch, capsys, fault
):
    home, _ = machine
    real = cli.assert_enrollable

    def unanswerable_for_the_default(storage, *, system, instance):
        if instance == INSTANCE:
            raise fault
        return real(storage, system=system, instance=instance)

    monkeypatch.setattr(cli, "assert_enrollable", unanswerable_for_the_default)
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _setup(monkeypatch, tmp_path / "alpha", "y", "", "graph:acme", *VALID)

    assert code == EXIT_OK, capsys.readouterr().err
    names = _names_asked(terminal)
    assert len(names) == 2 and "could not be checked" in names[0], names
    assert _row_path(home, "graph:acme").exists()


def test_three_refused_names_stop_setup_at_step_4(machine, tmp_path, monkeypatch, capsys):
    home, keychain = machine
    write_row(home, INSTANCE, system="graph", enabled=False)
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _setup(
        monkeypatch, tmp_path / "alpha", "y", "graph/bad", INSTANCE, "../up", *VALID
    )

    assert code == EXIT_REFUSAL
    assert len(_names_asked(terminal)) == SETUP_ATTEMPTS
    assert _after(terminal, "connector name")[SETUP_ATTEMPTS - 1 :] == []
    err = capsys.readouterr().err
    assert STOPPED in err and DONE in err and NOTHING in err
    assert "'connector name" in err, "the refusal quotes the question"
    _steps_one_to_three_done(home, keychain)


# ── The shared path refuses ──────────────────────────────────────────────────


def _partial() -> dict:
    partial = _token(SIGNED_IN)
    partial["scope"] = "https://graph.microsoft.com/Calendars.Read"
    return partial


@pytest.mark.parametrize(
    ("provider", "reason"),
    [
        (
            lambda: Microsoft(
                device={"error": "access_denied", "error_description": "the user declined"}
            ),
            "declined",
        ),
        (
            lambda: Microsoft(
                device={"error": "expired_token", "error_description": "code expired"}
            ),
            "expired",
        ),
        (lambda: Microsoft(device=_partial()), "Chat.Read"),
        (
            lambda: Microsoft(
                refresh={"error": "invalid_grant", "error_description": "AADSTS70000: revoked"}
            ),
            "health check failed",
        ),
    ],
    ids=["declined", "expired", "partial", "failed-check"],
)
def test_a_refused_sign_in_or_check_stops_at_step_4_with_nothing_saved(
    machine, tmp_path, monkeypatch, capsys, provider, reason
):
    home, keychain = machine
    _microsoft(monkeypatch, provider())

    code, _ = _setup(monkeypatch, tmp_path / "alpha", "y", "", *VALID)

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert reason in err
    assert STOPPED in err and DONE in err and NOTHING in err
    assert err.index(reason) < err.index(DONE), "the cause comes first"
    _nothing_saved(home)
    _steps_one_to_three_done(home, keychain)


def test_the_check_is_bounded_at_ten_seconds():
    """The matrix's 10 s: setup's sign-in is built with the probe's own bound."""
    assert CREDENTIAL_PROBE_SECONDS == 10.0
    assert GraphEnrolment.timeout == CREDENTIAL_PROBE_SECONDS


def test_setup_builds_its_sign_in_with_the_ten_second_bound(machine):
    _, keychain = machine
    assert entry._FirstRun(keychain).graph_sign_in.timeout == 10.0


def test_a_check_past_its_bound_stops_at_step_4_with_nothing_saved(
    machine, tmp_path, monkeypatch, capsys
):
    home, _ = machine
    hold = threading.Event()
    _microsoft(monkeypatch, Microsoft(hold_refresh=hold), timeout=0.2)
    try:
        code, _ = _setup(monkeypatch, tmp_path / "alpha", "y", "", *VALID)
    finally:
        hold.set()

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "did not answer within 0.2s" in err and STOPPED in err and NOTHING in err
    _nothing_saved(home)


def test_missing_runtime_packages_are_named_before_any_connector_question(
    machine, tmp_path, monkeypatch, capsys
):
    home, keychain = machine
    _microsoft(monkeypatch, None)
    monkeypatch.setitem(sys.modules, "msal", None)

    code, terminal = _setup(monkeypatch, tmp_path / "alpha", "y", "", *VALID)

    assert code == EXIT_REFUSAL
    assert _after(terminal, GRAPH_QUESTION) == [], "a connector question was asked"
    err = capsys.readouterr().err
    assert "uv sync --extra runtime" in err and STOPPED in err
    # One remedy: the extra's, not also "run `pm-ai setup` again".
    assert "Run `pm-ai setup` again, or" not in err
    _nothing_saved(home)
    _steps_one_to_three_done(home, keychain)


def test_a_non_zero_return_from_the_shared_path_is_a_step_4_refusal(
    machine, tmp_path, monkeypatch, capsys
):
    _microsoft(monkeypatch, Microsoft())
    monkeypatch.setattr(cli, "_connector_add_graph", lambda storage, graph, instance: 3)

    code, _ = _setup(monkeypatch, tmp_path / "alpha", "y", "", *VALID)

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert STOPPED in err and "exit code 3" in err


# ── Interruptions ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), EOFError()], ids=["ctrl-c", "ctrl-d"])
@pytest.mark.parametrize("at", ["name", "tenant"])
def test_an_interruption_at_a_connector_question_is_the_step_4_refusal(
    machine, tmp_path, monkeypatch, capsys, interruption, at
):
    home, keychain = machine
    _microsoft(monkeypatch, Microsoft())
    answers = [interruption] if at == "name" else ["", CLIENT_ID, interruption]

    code, _ = _setup(monkeypatch, tmp_path / "alpha", "y", *answers)

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "interrupted at step 4" in err and DONE in err and NOTHING in err
    for sentence in SHARED_INTERRUPT_SENTENCES:
        assert sentence not in err
    _nothing_saved(home)
    _steps_one_to_three_done(home, keychain)


def test_ctrl_c_during_the_sign_in_wait_is_the_step_4_refusal(
    machine, tmp_path, monkeypatch, capsys
):
    home, keychain = machine
    _microsoft(monkeypatch, Microsoft(device=KeyboardInterrupt()))

    code, _ = _setup(monkeypatch, tmp_path / "alpha", "y", "", *VALID)

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "interrupted at step 4" in err and NOTHING in err
    for sentence in SHARED_INTERRUPT_SENTENCES:
        assert sentence not in err
    _nothing_saved(home)
    _steps_one_to_three_done(home, keychain)


def test_ctrl_c_between_the_seal_and_the_row_says_the_sign_in_is_sealed(
    machine, tmp_path, monkeypatch, capsys
):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())
    _failing_row_write(monkeypatch, KeyboardInterrupt())

    code, _ = _setup(monkeypatch, tmp_path / "alpha", "y", "", *VALID)

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "interrupted at step 4" in err
    assert "graph:work's sign-in is sealed" in err and "NOT enrolled" in err
    assert NOTHING not in err
    assert _sealed(home).exists() and not _row_path(home).exists()


def test_ctrl_c_after_the_row_is_written_says_the_connector_is_enrolled(
    machine, tmp_path, monkeypatch, capsys
):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())

    def interrupted(self, instance):
        raise KeyboardInterrupt

    # Called by the shared path's closing message, after both writes.
    monkeypatch.setattr(GraphEnrolment, "settings_file", interrupted)

    code, _ = _setup(monkeypatch, tmp_path / "alpha", "y", "", *VALID)

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "interrupted at step 4" in err
    assert "graph:work WAS saved" in err and "enrolled" in err
    assert NOTHING not in err
    assert _sealed(home).exists() and _row_path(home).exists()


# ── A credential sealed, its row not written ─────────────────────────────────


def test_a_sealed_credential_whose_row_could_not_be_written_is_named_and_refused_next_time(
    machine, tmp_path, monkeypatch, capsys
):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())
    with monkeypatch.context() as failing:
        _failing_row_write(failing, PermissionError(13, "Permission denied", "graph:work.json"))
        code, _ = _setup(monkeypatch, tmp_path / "alpha", "y", "", *VALID)

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert STOPPED in err and "Permission denied" in err
    assert "graph:work's sign-in is sealed" in err and "NOT enrolled" in err
    assert "No pm-ai command removes a sealed entry yet" in err
    assert "different name" in err
    assert "nothing was saved" not in err
    assert "once more" not in err, "the add's own remedy would be refused as in use"
    assert _sealed(home).exists() and not _row_path(home).exists()

    # The next run refuses that name as in use: no default, and typed, refused.
    before = digests(home / ".pm-ai")
    code, terminal = _setup(
        monkeypatch, tmp_path / "alpha", "y", INSTANCE, INSTANCE, INSTANCE, again=True
    )

    assert code == EXIT_REFUSAL
    names = _names_asked(terminal)
    assert names and all("[graph:work]" not in p for p in names)
    err = capsys.readouterr().err
    assert "a credential is already sealed for 'graph:work'" in err
    assert digests(home / ".pm-ai") == before


def test_step_four_output_runs_to_the_closing_report():
    printed = "[4/4] heading\n\nsign in here\n\n[     ok] keychain: fine\n"
    assert step_four(printed) == "[4/4] heading\n\nsign in here"
    assert re.search("sign in here", step_four(printed))


# ── Story 4s — step 4 asks the Outlook category questions ────────────────────


def test_step_4_asks_the_category_of_the_project_step_2_just_registered(
    tmp_path, monkeypatch, capsys
):
    """A fresh home: the registry is read when the questions start, so the
    project step 2 wrote is offered, and its answer is saved in the same row."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: Keychain())
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    _microsoft(monkeypatch, Microsoft())
    (tmp_path / "alpha").mkdir()

    code, terminal = _setup(monkeypatch, tmp_path / "alpha", "y", "", *VALID[:-1], "Alpha")

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    asked = [p for p in terminal.prompts if p.startswith("Outlook category")]
    assert asked == ["Outlook category that marks alpha's meetings [Enter for none]: "]
    row = json.loads(_row_path(home).read_text())
    assert row["categories"] == {"Alpha": "alpha"}
    assert "user_id" in row, "one row, written once with everything"


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), EOFError()], ids=["ctrl-c", "ctrl-d"])
def test_an_interruption_at_a_category_question_is_the_step_4_refusal(
    machine, tmp_path, monkeypatch, capsys, interruption
):
    home, keychain = machine
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _setup(monkeypatch, tmp_path / "alpha", "y", "", *VALID[:-1], interruption)

    assert code == EXIT_REFUSAL
    assert terminal.prompts[-1].startswith("Outlook category"), terminal.prompts
    err = capsys.readouterr().err
    assert "interrupted at step 4" in err and DONE in err and NOTHING in err
    for sentence in SHARED_INTERRUPT_SENTENCES:
        assert sentence not in err
    _nothing_saved(home)
    _steps_one_to_three_done(home, keychain)
