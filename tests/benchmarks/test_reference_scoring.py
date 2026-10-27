"""Story 7f: the reference-set scorer, row by row of its matrix, with a canned model only.

The modules live in `benchmarks/`, outside the package, so they are loaded by
file path. No test reaches Ollama: a canned model answers, or a fake server
behind `httpx.MockTransport` (`tests/conftest.py` refuses the real one).
Every reference folder is a fresh `0700` folder under `tmp_path`, holding
copies of the fictional `mixed_ru_en.vtt` and its reference.
"""

from __future__ import annotations

import importlib.util
import io
import json
import logging
import re
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from pm_ai.domain import meeting_summaries
from pm_ai.domain.model_calls import ModelAnswer
from pm_ai.ports import LocalModelUnavailable

REPO = Path(__file__).resolve().parents[2]
BENCHMARKS = REPO / "benchmarks"
FIXTURES = BENCHMARKS / "fixtures"
MARKER = "QZXPRIVATEMARKER"


def _load(name: str) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, BENCHMARKS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


scoring = _load("reference_scoring")
runner = _load("score_summaries")

ANSWER = {
    "summary": "Release planning.",
    "decisions": [
        {"statement": "Release 4.2 ships with the ORB-512 flag off", "said_by": "Speaker 1"},
        {"statement": "Speaker 2 writes the billing migration script ORB-533", "said_by": "Speaker 2"},
    ],
    "action_items": [
        {"what": "Write the billing migration script ORB-533", "owner": "Speaker 1", "due": "Friday"}
    ],
}


class Canned:
    """`ModelPort`: each call answered by `reply()` — a string, or an exception raised."""

    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.calls = 0

    @property
    def name(self) -> str:
        return "canned:8b"

    def complete(self, **_: Any) -> ModelAnswer:
        self.calls += 1
        answer = self.reply() if callable(self.reply) else self.reply
        if isinstance(answer, BaseException):
            raise answer
        return ModelAnswer(text=answer, model="canned:8b", local=True, prompt_tokens=100, output_tokens=20)

    def close(self) -> None:
        pass


class Factory:
    """A model per summary; counts how many were built."""

    def __init__(self, reply: Any = None) -> None:
        self.reply = reply if reply is not None else json.dumps(ANSWER, ensure_ascii=False)
        self.built = 0

    def __call__(self, config: Any) -> Canned:
        self.built += 1
        reply = self.reply
        if isinstance(reply, list):
            reply = reply[min(self.built, len(reply)) - 1]
        if isinstance(reply, BaseException) and not isinstance(reply, LocalModelUnavailable):
            raise reply  # building the model raised: the runner's per-meeting guard
        return Canned(reply)


def _never(config: Any) -> Any:
    raise AssertionError("no model may be built here")


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    made = tmp_path / "refs"
    made.mkdir()
    made.chmod(0o700)
    shutil.copy(FIXTURES / "mixed_ru_en.vtt", made / "m01.vtt")
    shutil.copy(FIXTURES / "mixed_ru_en.reference.toml", made / "m01.reference.toml")
    return made


def _main(folder: Path, *argv: str, factory: Any = None, running: bool = False) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = runner.main(
        ["--references", str(folder), *argv],
        model_factory=factory if factory is not None else Factory(),
        processes=lambda: running,
        out=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


def _runs(folder: Path) -> list[Path]:
    runs = folder / "runs"
    return sorted(runs.glob("*.json")) if runs.exists() else []


def _rows(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines()]


def _reference(folder: Path, stem: str = "m01") -> Any:
    return scoring.load_reference(folder / f"{stem}.reference.toml", folder)


def _edit(folder: Path, old: str, new: str, stem: str = "m01") -> None:
    path = folder / f"{stem}.reference.toml"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ── The rules, without a model ───────────────────────────────────────────────


def test_the_limits_are_7bs():
    assert scoring.MAX_DECISIONS == meeting_summaries.MAX_DECISIONS
    assert scoring.MAX_ACTION_ITEMS == meeting_summaries.MAX_ACTION_ITEMS


def test_folding_and_word_start():
    assert scoring.fold("  Ёлка\tИ  FLAG ") == "елка и flag"
    text = scoring.fold("Я напишу скрипт миграции, ORB-533; e-mail")
    assert scoring.occurs("миграц", text)
    assert scoring.occurs("orb-533", text)
    assert not scoring.occurs("играц", text), "inside a word is not a word start"
    assert scoring.occurs("mail", text), "a hyphen is not a letter or digit, so a word starts after it"


def test_a_group_needs_one_word_and_every_group_must_hold():
    text = scoring.fold("We ship ORB-512 with the flag off")
    assert scoring.matches((("orb-512",), ("флаг", "flag")), text)
    assert not scoring.matches((("orb-512",), ("флаг",)), text)


@pytest.mark.parametrize("word, refused", [("off", True), ("4.2", False), ("e-2", False), ("flag", False), ("ab c", True)])
def test_a_short_word_is_refused_unless_it_holds_a_digit_or_a_hyphen(folder, word, refused):
    _edit(folder, '["ORB-512"], ["flag", "флаг"]', f'["ORB-512"], [{json.dumps(word)}]')
    if refused:
        with pytest.raises(scoring.Refused, match="m01 D1: group 2 holds a word under 4 letters"):
            _reference(folder)
    else:
        _reference(folder)


def test_maximum_matching_pairs_more_than_greedy_file_order():
    first = scoring.Item("decision", "D1", (("alpha",),))
    second = scoring.Item("decision", "D2", (("alpha",), ("beta",)))
    reference = scoring.Reference("m01", Path("m01.vtt"), None, (first, second), (), (), "0")
    returned = [{"statement": "alpha beta"}, {"statement": "alpha"}]
    # Greedy in file order: D1 takes "alpha beta", and D2 is left with "alpha", which lacks beta.
    assert scoring.score(reference, returned, [])["decisions_paired"] == 2
    swapped = scoring.Reference("m01", Path("m01.vtt"), None, (second, first), (), (), "0")
    assert scoring.score(swapped, returned, [])["decisions_paired"] == 2


def test_a_returned_item_counts_for_one_reference_item_only():
    one = scoring.Item("decision", "D1", (("alpha",),))
    two = scoring.Item("decision", "D2", (("alpha",),))
    reference = scoring.Reference("m01", Path("m01.vtt"), None, (one, two), (), (), "0")
    scores = scoring.score(reference, [{"statement": "alpha"}], [])
    assert scores["decisions_paired"] == 1
    assert scoring.rates(scores)["decision_recall"] == 0.5
    assert scoring.rates(scores)["decision_precision"] == 1.0


def test_acceptance_d1_paired_a1_owner_wrong_and_a1_restated_as_a_decision(folder):
    scores = scoring.score(_reference(folder), ANSWER["decisions"], ANSWER["action_items"])
    assert scores["decisions_paired"] == 1
    assert scores["actions_paired"] == 1
    assert scores["owner_correct"] == 0
    assert scores["decisions_restating_actions"] == 1
    assert scores["actions_restating_decisions"] == 0
    assert scores["not_an_item_hits"] == 0


def test_owner_correct_missing_owner_restated_action_and_not_an_item_hits(folder):
    reference = _reference(folder)
    actions = [
        {"what": "Update the runbook ORB-548", "owner": "speaker  4", "due": None},
        {"what": "Call legal", "owner": None, "due": "2026-11-10"},
        {"what": "Ship ORB-512 with the flag off", "owner": "Speaker 1", "due": None},
        {"what": "Review the new mockups", "owner": "Speaker 1", "due": None},
    ]
    scores = scoring.score(reference, [], actions)
    assert scores["actions_paired"] == 2
    assert scores["owner_correct"] == 1, "case and spaces fold; a missing owner is wrong"
    assert scores["actions_restating_decisions"] == 1
    assert scores["not_an_item_hits"] == 1


def test_the_fictional_reference_warns_about_no_group(folder):
    reference = _reference(folder)
    assert scoring.group_warnings(reference, (folder / "m01.vtt").read_text(encoding="utf-8")) == []
    assert len(reference.decisions) == 9 and len(reference.actions) == 6 and len(reference.not_items) == 1


def test_a_group_absent_from_the_transcript_is_warned_by_id_and_number(folder):
    _edit(folder, '[["ORB-575"]]', f'[["ORB-575"], ["{MARKER.lower()}"]]')
    code, out, err = _main(folder)
    assert code == 0
    assert "warning: m01 D7 group 2" in err
    assert MARKER.lower() not in err + out


# ── The folder ───────────────────────────────────────────────────────────────


def test_a_folder_whose_mode_is_not_0700_is_refused(folder):
    folder.chmod(0o750)
    factory = Factory()
    code, _, err = _main(folder, factory=factory)
    assert code == 2 and "mode must be exactly 0700" in err
    assert factory.built == 0


def test_a_folder_inside_the_repository_is_refused_after_resolving_symlinks(tmp_path):
    tree = tmp_path / "tree"
    inner = tree / "refs"
    inner.mkdir(parents=True)
    inner.chmod(0o700)
    link = tmp_path / "outside"
    link.symlink_to(inner)
    with pytest.raises(scoring.Refused, match="outside the repository"):
        scoring.check_folder(link, [tree])
    with pytest.raises(scoring.Refused, match="outside the repository"):
        scoring.check_folder(tree, [tree])


def test_the_repositorys_own_fixtures_folder_is_refused():
    code, _, err = _main(FIXTURES, factory=_never)
    assert code == 2 and "outside the repository" in err


# ── Bad references ───────────────────────────────────────────────────────────


BAD = {
    "missing key": ('transcript = "m01.vtt"', "", "missing key transcript"),
    "unknown key": ('title = "Release 4.2 planning"', 'colour = "x"', "unknown key colour"),
    "unknown item key": ('id = "D1"', 'id = "D1"\nweight = 2', "m01 D1: unknown key weight"),
    "item missing match": ('match = [["ORB-575"]]', "", "m01 D7: missing key match"),
    "empty group": ('[["ORB-575"]]', '[["ORB-575"], []]', "m01 D7: group 2 must be a non-empty list"),
    "empty match": ('[["ORB-575"]]', "[]", "m01 D7: match must be a non-empty list"),
    "duplicate id": ('id = "D2"', 'id = "D1"', "m01 D1: duplicate id"),
    "misnamed id": ('id = "D2"', 'id = "A9"', r"id must match D\\d\+"),
    "action without owner": ('owner = ["Speaker 2"]\nmatch = [["ORB-533"', 'match = [["ORB-533"', "m01 A1: missing key owner"),
    "empty owner": ('owner = ["Speaker 2"]', "owner = []", "m01 A1: owner must list"),
    "transcript outside": ('transcript = "m01.vtt"', 'transcript = "../m01.vtt"', "transcript must be m01.vtt or m01.txt"),
    "transcript not vtt": ('transcript = "m01.vtt"', 'transcript = "m01.md"', "transcript must be m01.vtt or m01.txt"),
    "transcript missing": ('transcript = "m01.vtt"', 'transcript = "m01.txt"', "transcript must be m01.vtt or m01.txt"),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_a_bad_reference_is_refused_before_any_model_call(folder, case):
    old, new, rule = BAD[case]
    _edit(folder, old, new)
    factory = Factory()
    code, out, err = _main(folder, factory=factory)
    assert code == 2, err
    assert factory.built == 0
    assert "refused:" in err
    assert re.search(rule, err), err
    assert out == ""


def _items(kind: str, prefix: str, count: int, extra: str = "") -> str:
    return "".join(
        f'\n[[{kind}]]\nid = "{prefix}{n}"\n{extra}match = [["ORB-575"]]\n' for n in range(100, 100 + count)
    )


def test_more_decisions_than_25_or_actions_than_15_are_refused(folder):
    path = folder / "m01.reference.toml"
    base = path.read_text(encoding="utf-8")
    path.write_text(base + _items("decision", "D", 17), encoding="utf-8")
    code, _, err = _main(folder, factory=_never)
    assert code == 2 and "m01: more than 25 decisions" in err
    path.write_text(base + _items("action", "A", 10, 'owner = ["Speaker 1"]\n'), encoding="utf-8")
    code, _, err = _main(folder, factory=_never)
    assert code == 2 and "m01: more than 15 actions" in err


def test_a_bad_stem_is_refused(folder):
    (folder / "m01.reference.toml").rename(folder / "meeting.reference.toml")
    code, _, err = _main(folder, factory=_never)
    assert code == 2 and r"stem must match m\d{2,}" in err
    (folder / "meeting.reference.toml").rename(folder / "m01.reference.toml")
    shutil.copy(folder / "m01.vtt", folder / "notes.txt")
    code, _, err = _main(folder, factory=_never)
    assert code == 2 and "a transcript's stem must match" in err


def test_a_transcript_without_reference_is_warned_and_not_run(folder):
    shutil.copy(folder / "m01.vtt", folder / "m04.vtt")
    factory = Factory()
    code, out, err = _main(folder, factory=factory)
    assert code == 0
    assert "warning: m04 has no reference; not run" in err
    assert factory.built == 1
    assert [r["meeting"] for r in _rows(out)] == ["m01", "total"]


# ── Runs ─────────────────────────────────────────────────────────────────────


def test_a_scored_folder_prints_rows_and_writes_private_files(folder):
    code, out, err = _main(folder, running=True)
    assert code == 0, err
    rows = _rows(out)
    assert [r["meeting"] for r in rows] == ["m01", "total"]
    meeting, total = rows
    assert meeting["results"] == ["summarized"]
    assert meeting["decisions_paired"] == 1 and meeting["owner_correct"] == 0
    assert meeting["decisions_restating_actions"] == 1
    assert total["decisions_reference"] == 9 and total["actions_reference"] == 6
    assert total["meetings_not_summarised"] == 0
    assert "warning: a pm-ai daemon is running" in err
    (run_path,) = _runs(folder)
    log = run_path.with_suffix(".log")
    assert stat.S_IMODE(run_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert not (folder / ".work").exists()
    run = json.loads(run_path.read_text(encoding="utf-8"))
    assert run["format"] == 1 and run["daemon_running"] is True
    assert len(run["summary_commit"]) == 40 and run["scorer_commit"] == run["summary_commit"]
    entry = run["meetings"]["m01"]
    assert entry["reference_sha256"] == _reference(folder).sha256
    (repeat,) = entry["repeats"]
    assert repeat["decisions"][0] == ANSWER["decisions"][0]
    assert repeat["action_items"] == ANSWER["action_items"]
    assert repeat["calls"] == 1 and repeat["prompt_tokens"] == 100 and repeat["output_tokens"] == 20
    assert repeat["prompt_characters"] > 0 and repeat["reason"]


def test_repeat_records_each_repeat_and_whether_they_agree(folder):
    code, out, _ = _main(folder, "--repeat", "2")
    assert code == 0
    (meeting, total) = _rows(out)
    assert meeting["results"] == ["summarized", "summarized"]
    assert meeting["answers_identical"] is True and meeting["scores_identical"] is True
    assert total["summaries"] == 2 and total["decisions_reference"] == 18
    run = json.loads(_runs(folder)[0].read_text(encoding="utf-8"))
    assert [r["repeat"] for r in run["meetings"]["m01"]["repeats"]] == [1, 2]


def test_answers_that_differ_across_repeats_are_recorded_as_differing(folder):
    other = dict(ANSWER, decisions=ANSWER["decisions"][:1])
    factory = Factory([json.dumps(ANSWER), json.dumps(other)])
    code, out, _ = _main(folder, "--repeat", "2", factory=factory)
    assert code == 0
    meeting = _rows(out)[0]
    assert meeting["answers_identical"] is False and meeting["scores_identical"] is False


def test_a_summary_not_made_misses_every_reference_item(folder):
    code, out, _ = _main(folder, factory=Factory("not json at all"))
    assert code == 0
    meeting, total = _rows(out)
    assert meeting["results"] == ["failed"]
    assert meeting["decisions_paired"] == 0 and meeting["decisions_returned"] == 0
    assert meeting["decision_recall"] == 0.0 and meeting["decision_precision"] is None
    assert total["meetings_not_summarised"] == 1 and total["decisions_reference"] == 9


@pytest.mark.parametrize("later", [False, True])
def test_waiting_at_any_meeting_stops_and_writes_nothing(folder, later):
    shutil.copy(folder / "m01.vtt", folder / "m02.vtt")
    shutil.copy(folder / "m01.reference.toml", folder / "m02.reference.toml")
    _edit(folder, 'transcript = "m01.vtt"', 'transcript = "m02.vtt"', stem="m02")
    waiting = LocalModelUnavailable("nothing on 127.0.0.1:11434")
    replies = [json.dumps(ANSWER), waiting] if later else [waiting]
    code, out, err = _main(folder, factory=Factory(replies))
    assert code == 1
    assert out == ""
    assert "waiting for the local model" in err and "nothing was written" in err
    assert not (folder / "runs").exists()
    assert not (folder / ".work").exists()


def test_an_exception_names_its_type_and_stem_and_counts_as_not_summarised(folder):
    code, out, err = _main(folder, factory=Factory(RuntimeError(MARKER)))
    assert code == 0
    assert "m01: RuntimeError" in err
    meeting, total = _rows(out)
    assert meeting["results"] == ["exception"] and meeting["exceptions"] == ["RuntimeError"]
    assert meeting["prompt_tokens"] == [None] and meeting["wall_s"] == [None], "not measured"
    assert total["meetings_not_summarised"] == 1 and total["prompt_tokens"] is None
    log = _runs(folder)[0].with_suffix(".log").read_text(encoding="utf-8")
    assert MARKER in log, "the message goes to the private log"
    assert MARKER not in out + err


def test_nothing_private_reaches_the_screen(folder):
    _edit(folder, 'title = "Release 4.2 planning"', f'title = "{MARKER} title"')
    _edit(folder, 'about = "release 4.2 ships with the ORB-512 flag off"', f'about = "{MARKER} about"')
    path = folder / "m01.reference.toml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + f'\n[[not_an_item]]\nid = "X2"\nmatch = [["{MARKER.lower()}"]]\n',
        encoding="utf-8",
    )
    answer = dict(ANSWER, summary=f"{MARKER} summary")
    answer["decisions"] = ANSWER["decisions"] + [{"statement": f"{MARKER} decision", "said_by": None}]

    def reply() -> str:
        logging.getLogger("pm_ai.test").warning("%s logged", MARKER)
        return json.dumps(answer)

    class Logged(Factory):
        def __call__(self, config: Any) -> Canned:
            self.built += 1
            return Canned(reply)

    code, out, err = _main(folder, factory=Logged())
    assert code == 0
    assert MARKER not in out + err
    assert MARKER.lower() not in out + err
    run_path = _runs(folder)[0]
    assert MARKER in run_path.with_suffix(".log").read_text(encoding="utf-8")
    run = json.loads(run_path.read_text(encoding="utf-8"))
    assert run["meetings"]["m01"]["repeats"][0]["scores"]["not_an_item_hits"] == 1
    # A failure's reason sentence and an exception message stay off the screen too.
    code, out, err = _main(folder, factory=Factory([f"{MARKER} not json", RuntimeError(MARKER)]))
    assert MARKER not in out + err


def test_an_unknown_setting_is_refused_before_any_model_call(folder):
    code, out, err = _main(folder, "--setting", "no_such_key=1", factory=_never)
    assert code == 2 and "refused: --setting no_such_key" in err
    assert not (folder / "runs").exists()
    code, _, err = _main(folder, "--setting", "local_model=x", factory=_never)
    assert code == 2
    code, _, err = _main(folder, "--setting", "daemon_port=80", factory=_never)
    assert code == 2 and "config.toml refuses" in err


def test_a_known_setting_reaches_the_config_and_the_run_file(folder):
    seen: list[Any] = []

    class Seeing(Factory):
        def __call__(self, config: Any) -> Canned:
            seen.append(config)
            return super().__call__(config)

    code, _, err = _main(folder, "--setting", "verbose_logging=true", factory=Seeing())
    assert code == 0, err
    assert seen[0].verbose_logging is True and seen[0].local_model == "qwen3:8b"
    run = json.loads(_runs(folder)[0].read_text(encoding="utf-8"))
    assert run["settings"] == {"verbose_logging": True}


# ── The requests actually sent ───────────────────────────────────────────────


def test_the_window_recorded_is_the_one_sent_to_ollama(folder, monkeypatch):
    from pm_ai.models.local import ollama

    chats: list[dict[str, Any]] = []

    def server(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "thinking"]})
        if request.url.path == "/api/chat":
            chats.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "model": "qwen3:8b",
                    "message": {"role": "assistant", "content": json.dumps(ANSWER)},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 900,
                    "eval_count": 80,
                    "total_duration": 1_000_000,
                },
            )
        return httpx.Response(404, json={"error": "no such path"})

    def fake(_backend: Any = None) -> httpx.BaseTransport:
        return httpx.MockTransport(server)

    original = ollama._default_transport
    monkeypatch.setattr(ollama, "_default_transport", fake)
    out, err = io.StringIO(), io.StringIO()
    code = runner.main(["--references", str(folder)], processes=lambda: False, out=out, err=err)
    assert code == 0, err.getvalue()
    assert ollama._default_transport is not original, "recording only while the run lasts"
    assert ollama._default_transport is fake
    run = json.loads(_runs(folder)[0].read_text(encoding="utf-8"))
    (chat,) = chats
    expected = {"options": chat["options"], "think": chat["think"]}
    assert run["sent"] == [expected]
    assert run["meetings"]["m01"]["repeats"][0]["sent"] == [expected]
    assert "num_ctx" in chat["options"] and chat["think"] is False
    assert "messages" not in json.dumps(run["sent"])
    assert run["meetings"]["m01"]["repeats"][0]["result"] == "summarized"


# ── Rescore and compare ──────────────────────────────────────────────────────


def test_rescore_uses_no_model_and_keeps_the_summary_sha(folder):
    assert _main(folder)[0] == 0
    (first,) = _runs(folder)
    run = json.loads(first.read_text(encoding="utf-8"))
    run["summary_commit"] = "a" * 40
    first.write_text(json.dumps(run), encoding="utf-8")
    _edit(folder, 'match = [["ORB-533", "скрипт", "script"]]', 'match = [["ORB-999"]]')
    code, out, err = _main(folder, "--rescore", str(first), factory=_never)
    assert code == 0, err
    second = [p for p in _runs(folder) if p != first][0]
    again = json.loads(second.read_text(encoding="utf-8"))
    assert again["summary_commit"] == "a" * 40
    assert again["scorer_commit"] != "a" * 40 and len(again["scorer_commit"]) == 40
    assert again["rescored_from"] == first.name
    assert again["meetings"]["m01"]["reference_sha256"] == _reference(folder).sha256
    assert _rows(out)[0]["actions_paired"] == 0, "A1 no longer matches"
    assert stat.S_IMODE(second.stat().st_mode) == 0o600


def test_rescore_refuses_a_run_file_outside_the_folder_or_of_another_format(folder, tmp_path):
    assert _main(folder)[0] == 0
    (first,) = _runs(folder)
    outside = tmp_path / "elsewhere.json"
    shutil.copy(first, outside)
    code, _, err = _main(folder, "--rescore", str(outside), factory=_never)
    assert code == 2 and "inside the references folder" in err
    run = json.loads(first.read_text(encoding="utf-8"))
    run["format"] = 99
    first.write_text(json.dumps(run), encoding="utf-8")
    code, _, err = _main(folder, "--rescore", str(first), factory=_never)
    assert code == 2 and "not a run file of format 1" in err


def test_compare_prints_deltas_and_refuses_what_cannot_be_compared(folder):
    assert _main(folder)[0] == 0
    better = dict(ANSWER, action_items=[dict(ANSWER["action_items"][0], owner="Speaker 2")])
    assert _main(folder, factory=Factory(json.dumps(better)))[0] == 0
    old, new = _runs(folder)
    code, out, err = _main(folder, "--compare", str(old), str(new), factory=_never)
    assert code == 0, err
    setup, meeting, total = _rows(out)
    assert setup == {
        "meeting": "setup", "same_model": True, "same_settings": True, "same_sent": True, "repeats": [1, 1]
    }
    assert meeting["meeting"] == "m01" and total["meeting"] == "total"
    assert meeting["owner_correct"] == 1 and meeting["owner_correct_rate"] == 1.0
    assert meeting["decisions_paired"] == 0

    run = json.loads(new.read_text(encoding="utf-8"))
    run["meetings"]["m01"]["reference_sha256"] = "0" * 64
    new.write_text(json.dumps(run), encoding="utf-8")
    code, _, err = _main(folder, "--compare", str(old), str(new), factory=_never)
    assert code == 2 and "references differ for m01" in err

    run["meetings"] = {"m02": run["meetings"]["m01"]}
    new.write_text(json.dumps(run), encoding="utf-8")
    code, _, err = _main(folder, "--compare", str(old), str(new), factory=_never)
    assert code == 2 and "different meetings: m01, m02" in err

    run["format"] = 2
    new.write_text(json.dumps(run), encoding="utf-8")
    code, _, err = _main(folder, "--compare", str(old), str(new), factory=_never)
    assert code == 2 and "format 1" in err


def test_two_runs_rescored_against_the_same_references_compare(folder):
    assert _main(folder)[0] == 0
    (old,) = _runs(folder)
    _edit(folder, 'match = [["ORB-575"]]', 'match = [["ORB-575", "спринт"]]')
    assert _main(folder)[0] == 0
    new = [p for p in _runs(folder) if p != old][0]
    code, _, err = _main(folder, "--compare", str(old), str(new), factory=_never)
    assert code == 2 and "rescore both" in err
    assert _main(folder, "--rescore", str(old), factory=_never)[0] == 0
    rescored = [p for p in _runs(folder) if p not in (old, new)][0]
    code, out, err = _main(folder, "--compare", str(rescored), str(new), factory=_never)
    assert code == 0, err


# ── Added after review ───────────────────────────────────────────────────────


def test_folding_normalises_to_nfc():
    decomposed = "Ёлка"  # Ё as E plus a combining diaeresis
    assert scoring.fold(decomposed) == "елка"


def test_a_tie_between_two_returned_actions_leans_to_the_right_owner(folder):
    reference = _reference(folder)
    wrong = {"what": "Billing migration script ORB-533", "owner": "Speaker 1", "due": None}
    right = {"what": "Migration script ORB-533", "owner": "Speaker 2", "due": None}
    assert scoring.score(reference, [], [wrong, right])["owner_correct"] == 1
    assert scoring.score(reference, [], [right, wrong])["owner_correct"] == 1


def test_a_txt_transcript_is_summarised_as_a_manual_text_capture(folder):
    lines = re.findall(r"<v (Speaker \d)>(.*?)</v>", (folder / "m01.vtt").read_text(encoding="utf-8"))
    (folder / "m01.vtt").unlink()
    (folder / "m01.txt").write_text("".join(f"{who}: {said}\n" for who, said in lines), encoding="utf-8")
    _edit(folder, 'transcript = "m01.vtt"', 'transcript = "m01.txt"')
    code, out, err = _main(folder)
    assert code == 0, err
    assert _rows(out)[0]["results"] == ["summarized"]
    assert _rows(out)[0]["decisions_paired"] == 1


class Prompted(Factory):
    """Keeps every prompt the canned model was given."""

    prompts: list[str]

    def __call__(self, config: Any) -> Canned:
        self.built += 1
        prompts = self.prompts
        reply = self.reply

        class Keeping(Canned):
            def complete(self, **request: Any) -> ModelAnswer:
                prompts.append(
                    request["instructions"]
                    + "\n".join(fragment.for_model for fragment in request["external"])
                )
                return super().complete(**request)

        return Keeping(reply)


def test_the_references_title_is_the_meeting_title_the_prompt_sees(folder):
    factory = Prompted()
    factory.prompts = []
    assert _main(folder, factory=factory)[0] == 0
    assert any("Release 4.2 planning" in p for p in factory.prompts)
    _edit(folder, 'title = "Release 4.2 planning"\n', "")
    factory.prompts = []
    assert _main(folder, factory=factory)[0] == 0
    assert any("Benchmark meeting" in p for p in factory.prompts)
    assert not any("Release 4.2 planning" in p for p in factory.prompts)


def test_from_a_linked_worktree_the_main_checkout_is_refused_too():
    trees = runner.repo_trees()
    common = runner.git("rev-parse", "--path-format=absolute", "--git-common-dir")
    assert REPO in trees and Path(common).parent in trees


def test_a_folder_spelled_with_other_letter_case_is_still_inside(tmp_path):
    tree = tmp_path / "Tree"
    inner = tree / "refs"
    inner.mkdir(parents=True)
    inner.chmod(0o700)
    other = tmp_path / "tree" / "refs"
    if not other.exists():
        pytest.skip("this file system is case-sensitive")
    with pytest.raises(scoring.Refused, match="outside the repository"):
        scoring.check_folder(other, [tree])


def test_a_folder_without_references_is_refused(folder):
    (folder / "m01.reference.toml").unlink()
    code, _, err = _main(folder, factory=_never)
    assert code == 2 and "holds no m<NN>.reference.toml" in err


def test_a_transcript_that_is_not_utf8_is_refused_naming_its_stem(folder):
    (folder / "m01.vtt").write_bytes(b"WEBVTT\n\n\xff\xfe")
    code, _, err = _main(folder, factory=_never)
    assert code == 2 and "m01: the transcript is not UTF-8" in err


@pytest.mark.parametrize("name", ["runs", ".work"])
def test_a_symlinked_runs_or_work_folder_is_refused(folder, tmp_path, name):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (folder / name).symlink_to(elsewhere)
    code, _, err = _main(folder, factory=Factory())
    assert code == 2 and f"{name} in the references folder must be a plain directory" in err
    assert list(elsewhere.iterdir()) == []


def test_a_setting_that_is_a_date_or_a_table_is_refused(folder):
    for value in ("2026-01-01", "{a = 1}"):
        code, _, err = _main(folder, "--setting", f"display_timezone={value}", factory=_never)
        assert code == 2 and "a value must be text, a number or true/false" in err


def test_no_local_adapter_is_refused_before_any_meeting(folder, monkeypatch):
    from pm_ai.app import wiring

    monkeypatch.setattr(wiring, "local_model", lambda config: None)
    out, err = io.StringIO(), io.StringIO()
    code = runner.main(["--references", str(folder)], processes=lambda: False, out=out, err=err)
    assert code == 2 and "uv sync --extra runtime" in err.getvalue()
    assert not (folder / "runs").exists()


def test_an_unreadable_process_list_is_recorded_as_not_checked(folder):
    out, err = io.StringIO(), io.StringIO()
    assert runner.main(
        ["--references", str(folder)], model_factory=Factory(), processes=lambda: None, out=out, err=err
    ) == 0
    run = json.loads(_runs(folder)[0].read_text(encoding="utf-8"))
    assert run["daemon_running"] is None


def test_a_run_file_out_of_shape_is_refused(folder):
    assert _main(folder)[0] == 0
    (first,) = _runs(folder)
    run = json.loads(first.read_text(encoding="utf-8"))
    run["meetings"]["m01"]["repeats"] = []
    first.write_text(json.dumps(run), encoding="utf-8")
    code, _, err = _main(folder, "--rescore", str(first), factory=_never)
    assert code == 2 and "m01 is not in the run file's shape" in err


def test_rescore_refuses_a_meeting_without_a_current_reference(folder):
    assert _main(folder)[0] == 0
    (first,) = _runs(folder)
    run = json.loads(first.read_text(encoding="utf-8"))
    run["meetings"]["m07"] = run["meetings"]["m01"]
    first.write_text(json.dumps(run), encoding="utf-8")
    code, _, err = _main(folder, "--rescore", str(first), factory=_never)
    assert code == 2 and "no current reference for m07" in err


def test_totals_carry_time_and_tokens(folder):
    code, out, _ = _main(folder, "--repeat", "2")
    assert code == 0
    total = _rows(out)[-1]
    assert total["prompt_tokens"] == 200 and total["output_tokens"] == 40
    assert total["wall_s"] is not None
