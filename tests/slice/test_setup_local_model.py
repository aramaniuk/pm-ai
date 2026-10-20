"""Story 7a through `main`: setup's `local_model` question and doctor's `local model` line.

The presence check is the local adapter's, reached through wiring. Here its
transport is a `httpx.MockTransport` standing in for Ollama, installed where
`tests/conftest.py` already replaces the real one — no test reaches a real
Ollama.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from pm_ai.app import entry
from pm_ai.core.config import load_config
from pm_ai.models.local import ollama
from tests.slice.test_first_run import (  # noqa: F401 — fixtures
    EXIT_OK,
    EXIT_UNHEALTHY,
    config_path,
    home,
    installed,
    keychain,
    operator,
    setup,
)

FIRST = ("85", "Europe/Warsaw", "n")
"""Rate, zone and verbose: the step-3 answers before `local_model`."""


def _ollama(monkeypatch: pytest.MonkeyPatch, *, pulled: list[str], show: dict | None = None) -> list[str]:
    """Ollama stood in: `pulled` listed by /api/tags; records the paths asked."""
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        assert request.url.host == "127.0.0.1" and request.url.port == 11434
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in pulled]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json=show or {"capabilities": ["completion"]})
        raise AssertionError(f"the presence check sends no prompt: {request.url.path}")

    monkeypatch.setattr(ollama, "_default_transport", lambda _backend=None: httpx.MockTransport(answer))
    return asked


def _first_run(operator, tmp_path: Path, *model_answers: str) -> int:
    operator.model_answer = None
    operator.script(str(tmp_path / "alpha"), "", *FIRST, *model_answers, "n")
    return setup()


def test_enter_takes_the_offered_model_and_says_it_is_pulled(
    home, tmp_path, keychain, operator, installed, monkeypatch, capsys
):
    asked = _ollama(monkeypatch, pulled=["qwen3:8b"])
    _first_run(operator, tmp_path, "")
    assert load_config(config_path(home).read_bytes()).local_model == "qwen3:8b"
    printed = capsys.readouterr().out
    assert "[     ok] local model: qwen3:8b is pulled" in printed
    assert "ollama pull" not in printed.split("local model:")[1].split("\n")[0]
    assert "OLLAMA_MAX_LOADED_MODELS=1" in printed and "launchctl setenv" in printed
    assert asked == ["/api/tags", "/api/show"]
    (prompt,) = [p for p in operator.asked if p.startswith("local_model")]
    assert "[qwen3:8b]" in prompt, "the offered model is shown"


def test_a_model_not_pulled_is_written_and_setup_prints_ollama_pull(
    home, tmp_path, keychain, operator, installed, monkeypatch, capsys
):
    _ollama(monkeypatch, pulled=[])
    _first_run(operator, tmp_path, "llama3.1:8b")
    assert load_config(config_path(home).read_bytes()).local_model == "llama3.1:8b"
    printed = capsys.readouterr().out
    assert "pull it with: ollama pull llama3.1:8b" in printed
    assert printed.count("ollama pull") == 1, "printed once, not again in the remedy"
    assert "OLLAMA_MAX_LOADED_MODELS=1" in printed


def test_a_name_without_a_tag_is_kept_as_written_and_compared_as_latest(
    home, tmp_path, keychain, operator, installed, monkeypatch, capsys
):
    _ollama(monkeypatch, pulled=["qwen3:latest"])
    _first_run(operator, tmp_path, "qwen3")
    assert load_config(config_path(home).read_bytes()).local_model == "qwen3"
    assert "qwen3:latest is pulled" in capsys.readouterr().out


def test_none_leaves_it_unset(home, tmp_path, keychain, operator, installed, monkeypatch):
    asked = _ollama(monkeypatch, pulled=["qwen3:8b"])
    _first_run(operator, tmp_path, "none")
    assert load_config(config_path(home).read_bytes()).local_model == ""
    assert asked == [], "nothing is asked of Ollama for no model"


def test_a_cloud_tag_is_refused_at_the_prompt_and_asked_again(
    home, tmp_path, keychain, operator, installed, monkeypatch, capsys
):
    asked = _ollama(monkeypatch, pulled=["gpt-oss:120b-cloud", "qwen3:8b"])
    _first_run(operator, tmp_path, "gpt-oss:120b-cloud", "qwen3:8b")
    assert load_config(config_path(home).read_bytes()).local_model == "qwen3:8b"
    err = capsys.readouterr().err
    assert "cloud tag" in err and "OLLAMA_NO_CLOUD=1" in err
    assert asked == ["/api/tags", "/api/show"], "the cloud name was never sent"


def test_a_model_the_server_says_runs_elsewhere_is_refused_and_asked_again(
    home, tmp_path, keychain, operator, installed, monkeypatch, capsys
):
    _ollama(monkeypatch, pulled=["mirror:latest"], show={"remote_host": "https://ollama.com:443"})
    _first_run(operator, tmp_path, "mirror", "none")
    assert load_config(config_path(home).read_bytes()).local_model == ""
    assert "remote_host" in capsys.readouterr().err


def test_ollama_not_running_writes_the_name_and_says_how_to_start_it(
    home, tmp_path, keychain, operator, installed, capsys
):
    # `tests/conftest.py`'s transport: nothing is listening.
    _first_run(operator, tmp_path, "qwen3:8b")
    assert load_config(config_path(home).read_bytes()).local_model == "qwen3:8b"
    printed = capsys.readouterr().out
    assert "[warning] local model: nothing is listening on 127.0.0.1:11434" in printed
    assert "Start Ollama" in printed
    assert "ollama pull" not in printed, "nothing says the model is not pulled"


def test_a_server_answering_an_error_prints_no_pull_hint(
    home, tmp_path, keychain, operator, installed, monkeypatch, capsys
):
    import httpx as _httpx

    monkeypatch.setattr(
        ollama,
        "_default_transport",
        lambda _backend=None: _httpx.MockTransport(
            lambda request: _httpx.Response(500, json={"error": "boom"})
        ),
    )
    _first_run(operator, tmp_path, "qwen3:8b")
    printed = capsys.readouterr().out
    assert "500: boom" in printed
    assert "ollama pull" not in printed


# ── doctor ───────────────────────────────────────────────────────────────────


def _doctor_line(printed: str) -> str:
    (line,) = [line for line in printed.splitlines() if "] local model: " in line]
    return line


def _configured(home: Path, text: str) -> None:
    config_path(home).parent.mkdir(parents=True, exist_ok=True)
    config_path(home).write_text(text)


def test_doctor_is_ok_when_no_model_is_named_and_names_setup(home, keychain, installed, capsys):
    """Andrei, 2026-10-19: an unset local model is not a failure; the line says how."""
    entry.main(["doctor"])
    printed = capsys.readouterr().out
    assert "[     ok] local model: none named" in _doctor_line(printed)
    assert "pm-ai setup" in printed


def test_doctor_is_ok_when_the_named_model_is_pulled(home, keychain, installed, monkeypatch, capsys):
    _configured(home, 'local_model = "qwen3:8b"\n')
    _ollama(monkeypatch, pulled=["qwen3:8b"])
    entry.main(["doctor"])
    printed = capsys.readouterr().out
    assert "[     ok] local model: qwen3:8b is pulled" in _doctor_line(printed)
    assert "cannot read or set the Ollama server's own settings" in printed


def test_doctor_warns_with_how_to_start_ollama_when_it_is_quit(home, keychain, installed, capsys):
    _configured(home, 'local_model = "qwen3:8b"\n')
    entry.main(["doctor"])
    printed = capsys.readouterr().out
    assert "[warning] local model: nothing is listening" in _doctor_line(printed)
    assert "Start Ollama" in printed and "OLLAMA_NUM_PARALLEL=1" in printed


def test_doctor_warns_naming_a_cloud_model(home, keychain, installed, capsys):
    _configured(home, 'local_model = "gpt-oss:120b-cloud"\n')
    entry.main(["doctor"])
    assert "cloud tag" in _doctor_line(capsys.readouterr().out)


def test_a_host_key_in_config_is_refused_as_unknown_through_doctor(home, keychain, installed, capsys):
    _configured(home, 'ollama_host = "http://elsewhere.example:11434"\n')
    entry.main(["doctor"])
    printed = capsys.readouterr().out
    assert "ollama_host" in printed
    assert "[warning] local model: config.toml could not be read" in _doctor_line(printed)


# ── doctor: a missing local model is not a failure (Andrei, 2026-10-19) ──────


def test_doctor_with_no_local_model_says_how_to_set_one_up_and_exits_healthy(
    home, tmp_path, keychain, operator, installed, monkeypatch, capsys
):
    asked = _ollama(monkeypatch, pulled=["qwen3:8b"])
    assert _first_run(operator, tmp_path, "none") == EXIT_OK
    capsys.readouterr()

    code = entry.main(["doctor"])
    said = capsys.readouterr().out
    assert code == EXIT_OK
    line = said.split("local model:")[1].split("\n")
    assert line[0].startswith(" none named") and "not a failure" in line[0]
    assert "pm-ai setup" in line[1] and "ollama pull" in line[1]
    assert "NOT healthy" not in said
    assert asked == [], "nothing is asked of Ollama for no model"


def test_doctor_with_a_named_model_ollama_cannot_answer_for_is_not_healthy(
    home, tmp_path, keychain, operator, installed, capsys
):
    # `tests/conftest.py`'s transport: nothing is listening.
    _first_run(operator, tmp_path, "qwen3:8b")
    capsys.readouterr()

    code = entry.main(["doctor"])
    said = capsys.readouterr().out
    assert code == EXIT_UNHEALTHY
    assert "[warning] local model: nothing is listening on 127.0.0.1:11434" in said


def test_doctor_with_a_named_model_not_pulled_is_not_healthy(
    home, tmp_path, keychain, operator, installed, monkeypatch, capsys
):
    _ollama(monkeypatch, pulled=[])
    _first_run(operator, tmp_path, "qwen3:8b")
    capsys.readouterr()

    assert entry.main(["doctor"]) == EXIT_UNHEALTHY
    assert "ollama pull qwen3:8b" in capsys.readouterr().out
