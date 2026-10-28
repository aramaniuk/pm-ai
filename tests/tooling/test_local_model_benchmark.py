"""The local-model benchmark still imports and takes only the two windows (story 7g).

`benchmarks/local_model.py` once set the adapter's `NUM_CTX` by assignment;
`7g` removed the constant, so the window now reaches `OllamaModel` as an
argument and `--num-ctx` is one of `LOCAL_MODEL_WINDOWS`. Nothing here reaches
an Ollama server.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from pm_ai.domain.model_calls import DEFAULT_LOCAL_MODEL_WINDOW

_PATH = Path(__file__).resolve().parents[2] / "benchmarks" / "local_model.py"


@pytest.fixture(scope="module")
def benchmark():
    spec = importlib.util.spec_from_file_location("pm_ai_local_model_benchmark", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def test_the_benchmark_parses_the_smaller_window(benchmark):
    assert benchmark.build_parser().parse_args(["--num-ctx", "16384"]).num_ctx == 16384


def test_the_benchmark_defaults_to_the_settings_default(benchmark):
    assert benchmark.build_parser().parse_args([]).num_ctx == DEFAULT_LOCAL_MODEL_WINDOW


def test_the_benchmark_refuses_any_other_window(benchmark, capsys):
    with pytest.raises(SystemExit):
        benchmark.build_parser().parse_args(["--num-ctx", "8192"])
    assert "8192" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["--num-ctx", "16384"]])
def test_main_builds_every_adapter_at_the_parsed_window(benchmark, monkeypatch, tmp_path, argv):
    """The window `main` parses is the one each `OllamaModel` is built with,
    on both paths; nothing is asked of a server."""
    built: list[int] = []

    class Recorded:
        def __init__(self, name: str, *, window: int) -> None:
            built.append(window)

        def close(self) -> None:
            pass

    class Stop(Exception):
        pass

    def stop(*args, **kwargs):
        raise Stop

    monkeypatch.setattr(benchmark, "OllamaModel", Recorded)
    monkeypatch.setattr(benchmark, "machine", lambda: {"python_arch": "arm64", "rosetta": "no"})
    monkeypatch.setattr(benchmark, "fragments", stop)
    window = int(argv[1]) if argv else DEFAULT_LOCAL_MODEL_WINDOW
    transcript = tmp_path / "fictional.vtt"
    transcript.write_text("WEBVTT\n", encoding="utf-8")
    with pytest.raises(Stop):
        benchmark.main([*argv, "--model", "qwen3:8b", "--private", str(transcript)])
    with pytest.raises(Stop):
        benchmark.main([*argv, "--model", "qwen3:8b"])
    assert built == [window, window]
