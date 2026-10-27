"""Story 7a's measurement of the local model, run by hand — never by pytest.

    uv run python benchmarks/local_model.py --model qwen3:8b
    uv run python benchmarks/local_model.py --model qwen3:8b --model llama3.1:8b
    uv run python benchmarks/local_model.py --model qwen3:8b --private PATH

For each model: three runs alone and three while a pm-ai process persists the
largest batch a GitLab harvest can return (50,000 events, `9a`'s figure)
through `StorageService`, in a loop, in a throwaway storage root this script
creates — never `~/.pm-ai`. Each run makes two calls through the local adapter
(`pm_ai.models.local.ollama.OllamaModel.complete`, the same request every pm-ai
call makes):

- **timing** — the letter-redacted recording from PR #72
  (`tests/connectors/fixtures/graph/transcript_recorded.vtt`): its length and
  alphabet are real, so its token counts and timings are; its words are not
  words, so it measures nothing about quality;
- **quality** — the authored fictional meeting `fixtures/mixed_ru_en.vtt`,
  scored against `fixtures/mixed_ru_en.key.toml`.

Recorded per call: cold load, prompt tokens and rate, output tokens and rate,
wall time, peak resident memory (`ps` RSS) of the Ollama processes and of
pm-ai (this process, and the load process when it runs), swap used before and
after, page-outs during the call, the highest memory-pressure level seen, and
the input's characters per token. Recorded once: the machine's model, memory
and macOS version, the Ollama version, and the CPU architecture this Python
runs as — under Rosetta the script stops before its first run and asks.

No transcript text is printed. The quality call's items that match no key are
printed for reading by hand — they are the model's words about a fictional
meeting. `--private PATH` runs one real transcript and prints counts and
outcomes only — prompt tokens, redactions the filter made, items returned,
validation — never a word of the text or of the answer.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from pm_ai.core.transcripts import read_transcript  # noqa: E402
from pm_ai.domain.identity import DataScope, ScopeKind  # noqa: E402
from pm_ai.domain.model_calls import (  # noqa: E402
    DEFAULT_LOCAL_MODEL_WINDOW,
    LOCAL_MODEL_WINDOWS,
    ModelAnswer,
    ScopeFlow,
)
from pm_ai.domain.sanitize import REDACTION, Sanitized, sanitize  # noqa: E402
from pm_ai.domain.task_classes import TaskClass  # noqa: E402
from pm_ai.models.local.ollama import BASE_URL, OllamaModel  # noqa: E402

REDACTED = REPO / "tests" / "connectors" / "fixtures" / "graph" / "transcript_recorded.vtt"
AUTHORED = Path(__file__).resolve().parent / "fixtures" / "mixed_ru_en.vtt"
KEY = Path(__file__).resolve().parent / "fixtures" / "mixed_ru_en.key.toml"

CANDIDATES = ("llama3.1:8b", "qwen3:8b")
"""The spine's two verified candidates, at Q4_K_M. `qwen3:8b` breaks a full tie."""

RUNS = 3
HARVEST_CAP_EVENTS = 50_000
ASK_WALL_SECONDS = 300.0
LOAD_START_SECONDS = 120.0
"""How long the load process has to persist its first batch before the run stops."""
SAMPLE_SECONDS = 0.2

BENCH_PROJECT = "bench"
FLOW = ScopeFlow.of(
    [DataScope(ScopeKind.PROJECT, project_id=BENCH_PROJECT)],
    DataScope(ScopeKind.PROJECT, project_id=BENCH_PROJECT),
)

# ── The first extraction prompt, kept with the script (`7b` re-runs with its own) ──

INSTRUCTIONS = """\
You read a meeting transcript and list what the meeting decided and who agreed to do what.
The transcript mixes Russian and English; answer in the language each item was said in.
Each line is `Speaker N: words`. Speakers are labelled `Speaker 1` to `Speaker 4`.

Return JSON only, with two lists:
- "decisions": what the meeting agreed. Each item: {"text": "..."}.
  A proposal that was raised and then rejected is NOT a decision — leave it out.
- "action_items": a task one person took on. Each item: {"owner": "Speaker N", "text": "..."}.
Keep ticket ids (like ABC-123) and dates exactly as written. Do not invent items."""

SHAPE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"owner": {"type": "string"}, "text": {"type": "string"}},
                "required": ["owner", "text"],
            },
        },
    },
    "required": ["decisions", "action_items"],
}


def fragments(path: Path) -> list[Sanitized]:
    """One cleaned fragment per utterance, `Speaker: words` — the port's input."""
    text = path.read_text(encoding="utf-8")
    return [sanitize(f"{u.speaker_handle}: {u.text}") for u in read_transcript(text, name=path.name)]


def validate(text: str) -> dict[str, Any] | None:
    """The answer parsed and checked against `SHAPE`, or `None`."""
    try:
        body = json.loads(text)
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    decisions, actions = body.get("decisions"), body.get("action_items")
    if not isinstance(decisions, list) or not isinstance(actions, list):
        return None
    if not all(isinstance(d, dict) and isinstance(d.get("text"), str) for d in decisions):
        return None
    if not all(
        isinstance(a, dict) and isinstance(a.get("text"), str) and isinstance(a.get("owner"), str)
        for a in actions
    ):
        return None
    return body


# ── The machine ──────────────────────────────────────────────────────────────


def _sysctl(name: str) -> str:
    try:
        return subprocess.run(
            ["/usr/sbin/sysctl", "-n", name], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def machine() -> dict[str, str]:
    import httpx

    try:
        with httpx.Client(base_url=BASE_URL, trust_env=False, timeout=5) as http:
            version = http.get("/api/version").json().get("version", "?")
    except Exception as unreachable:  # noqa: BLE001
        version = f"unreachable ({type(unreachable).__name__})"
    macos = subprocess.run(
        ["/usr/bin/sw_vers", "-productVersion"], capture_output=True, text=True
    ).stdout.strip()
    memory = _sysctl("hw.memsize")
    return {
        "machine": _sysctl("hw.model") or platform.machine(),
        "chip": _sysctl("machdep.cpu.brand_string"),
        "memory_gb": f"{int(memory) / 2**30:.0f}" if memory.isdigit() else "?",
        "macos": macos,
        "ollama": version,
        "python_arch": platform.machine(),
        "rosetta": "yes" if _sysctl("sysctl.proc_translated") == "1" else "no",
        "python": platform.python_version(),
    }


def swap_used_mb() -> float:
    match = re.search(r"used = ([\d.]+)M", _sysctl("vm.swapusage"))
    return float(match.group(1)) if match else float("nan")


def pageouts() -> int:
    out = subprocess.run(["/usr/bin/vm_stat"], capture_output=True, text=True).stdout
    match = re.search(r"Pageouts:\s+(\d+)", out)
    return int(match.group(1)) if match else -1


def pressure_level() -> int:
    """`kern.memorystatus_vm_pressure_level`: 1 normal, 2 warning, 4 critical."""
    value = _sysctl("kern.memorystatus_vm_pressure_level")
    return int(value) if value.isdigit() else -1


def _rss_kb(pids: list[int]) -> int:
    if not pids:
        return 0
    out = subprocess.run(
        ["/bin/ps", "-o", "rss=", "-p", ",".join(map(str, pids))], capture_output=True, text=True
    ).stdout
    return sum(int(line) for line in out.split() if line.strip().isdigit())


def ollama_pids() -> list[int]:
    out = subprocess.run(["/bin/ps", "-axo", "pid=,comm="], capture_output=True, text=True).stdout
    pids = []
    for line in out.splitlines():
        pid, _, command = line.strip().partition(" ")
        if "ollama" in command.lower():
            pids.append(int(pid))
    return pids


class Sampler:
    """Peak RSS and memory pressure, sampled every `SAMPLE_SECONDS` during a call."""

    def __init__(self, pm_ai_pids: list[int]) -> None:
        self._pm_ai = pm_ai_pids
        self.ollama_peak_kb = 0
        self.pm_ai_peak_kb = 0
        self.pressure = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while True:
            self.ollama_peak_kb = max(self.ollama_peak_kb, _rss_kb(ollama_pids()))
            self.pm_ai_peak_kb = max(self.pm_ai_peak_kb, _rss_kb(self._pm_ai))
            self.pressure = max(self.pressure, pressure_level())
            if self._stop.wait(SAMPLE_SECONDS):
                return

    def __enter__(self) -> Sampler:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()


# ── The background load: 50,000 events persisted in a loop ───────────────────


def load_worker(root: Path) -> None:
    """Persist `HARVEST_CAP_EVENTS` fresh commit events per batch until killed."""
    from datetime import datetime, timezone

    from pm_ai.app.wiring import build
    from pm_ai.domain.events import CommitPayload, NormalizedEvent, ObservedEventType
    from pm_ai.domain.handles import person_handle
    from pm_ai.domain.identity import SourceRef

    now = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
    daemon = build(root, BENCH_PROJECT, now=lambda: now, encryption_disabled=False)
    scope = DataScope(ScopeKind.PROJECT, project_id=BENCH_PROJECT)
    actor = person_handle("email", "bench@example.com")
    batch = 0
    while True:
        batch += 1
        events = tuple(
            NormalizedEvent(
                scope=scope,
                type=ObservedEventType.COMMIT_PUSHED,
                source_ref=SourceRef.parse(f"gitlab:{BENCH_PROJECT}:commit:{batch:06x}{n:06x}"),
                actor=actor,
                occurred_at=now,
                payload=CommitPayload(sha=f"{batch:06x}{n:06x}", message="bench"),
            )
            for n in range(HARVEST_CAP_EVENTS)
        )
        daemon.storage.persist_events(events, scope=scope)
        print(f"batch {batch} persisted", flush=True)


# ── One call, measured ───────────────────────────────────────────────────────


@dataclass
class Call:
    input: str
    wall_s: float = 0.0
    error: str = ""
    answer: ModelAnswer | None = None
    chars: int = 0
    redactions: int = 0
    swap_before_mb: float = 0.0
    swap_after_mb: float = 0.0
    pageouts: int = 0
    ollama_peak_mb: float = 0.0
    pm_ai_peak_mb: float = 0.0
    pressure: int = 0
    valid: bool = False
    score: dict[str, Any] = field(default_factory=dict)

    def row(self) -> dict[str, Any]:
        a = self.answer
        rate = (lambda n, ns: round(n / (ns / 1e9), 1) if n and ns else None)
        return {
            "input": self.input,
            "error": self.error or None,
            "wall_s": round(self.wall_s, 2),
            "cold_load_s": round(a.load_ns / 1e9, 2) if a and a.load_ns else None,
            "prompt_tokens": a.prompt_tokens if a else None,
            "prompt_tok_per_s": rate(a.prompt_tokens, a.prompt_ns) if a else None,
            "output_tokens": a.output_tokens if a else None,
            "output_tok_per_s": rate(a.output_tokens, a.output_ns) if a else None,
            "chars_per_token": round(self.chars / a.prompt_tokens, 2) if a and a.prompt_tokens else None,
            "ollama_peak_rss_mb": round(self.ollama_peak_mb),
            "pm_ai_peak_rss_mb": round(self.pm_ai_peak_mb),
            "swap_before_mb": self.swap_before_mb,
            "swap_after_mb": self.swap_after_mb,
            "pageouts": self.pageouts,
            "pressure_level": self.pressure,
            "valid_first_time": self.valid,
            **({"score": self.score} if self.score else {}),
        }


def measured_call(model: OllamaModel, label: str, parts: list[Sanitized], pm_ai_pids: list[int]) -> Call:
    call = Call(input=label)
    call.chars = len(INSTRUCTIONS) + sum(len(p.for_model) for p in parts)
    call.redactions = sum(p.for_model.count(REDACTION) for p in parts)
    call.swap_before_mb = swap_used_mb()
    before = pageouts()
    with Sampler(pm_ai_pids) as sampler:
        started = time.monotonic()
        try:
            call.answer = model.complete(
                task_class=TaskClass.EXTRACTION,
                instructions=INSTRUCTIONS,
                external=parts,
                flow=FLOW,
                shape=SHAPE,
            )
        except Exception as refused:  # noqa: BLE001 — recorded, the run goes on
            call.error = f"{type(refused).__name__}: {refused}"
        call.wall_s = time.monotonic() - started
    after = pageouts()
    call.pageouts = after - before if before >= 0 and after >= 0 else -1
    call.swap_after_mb = swap_used_mb()
    call.ollama_peak_mb = sampler.ollama_peak_kb / 1024
    call.pm_ai_peak_mb = sampler.pm_ai_peak_kb / 1024
    call.pressure = sampler.pressure
    call.valid = call.answer is not None and validate(call.answer.text) is not None
    return call


def score(body: dict[str, Any], key: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Planted items found, distractors returned, and the items matching no key."""
    decisions = [d["text"] for d in body["decisions"]]
    actions = [(a["owner"], a["text"]) for a in body["action_items"]]
    every = [*decisions, *(f"{o} {t}" for o, t in actions)]
    tokens = [k["token"] for group in ("decision", "action", "distractor") for k in key[group]]
    found_d = [k["id"] for k in key["decision"] if any(k["token"] in d for d in decisions)]
    found_a = [
        k["id"]
        for k in key["action"]
        if any(k["token"] in f"{o} {t}" and k["owner"] in f"{o} {t}" for o, t in actions)
    ]
    distractors = [k["id"] for k in key["distractor"] if any(k["token"] in d for d in decisions)]
    unmatched = [item for item in every if not any(t in item for t in tokens)]
    planted = len(key["decision"]) + len(key["action"])
    return (
        {
            "decisions_found": found_d,
            "actions_found": found_a,
            "recall": round((len(found_d) + len(found_a)) / planted, 3),
            "distractors_returned": distractors,
            "items_returned": len(every),
            "items_matching_no_key": len(unmatched),
        },
        unmatched,
    )


# ── Runs ─────────────────────────────────────────────────────────────────────


def run_model(
    name: str,
    root: Path,
    key: dict[str, Any],
    log: list[str],
    *,
    window: int = DEFAULT_LOCAL_MODEL_WINDOW,
) -> dict[str, Any]:
    model = OllamaModel(name, window=window)
    timing, quality = fragments(REDACTED), fragments(AUTHORED)
    runs: list[dict[str, Any]] = []
    for loaded in (False, True):
        worker: subprocess.Popen[str] | None = None
        if loaded:
            worker = subprocess.Popen(
                [sys.executable, __file__, "--load-worker", str(root)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            first = _first_line(worker, LOAD_START_SECONDS)
            if "persisted" not in first:
                worker.kill()
                worker.wait()
                raise SystemExit(
                    f"the load process did not persist a batch within "
                    f"{LOAD_START_SECONDS:g} s: {first.strip() or 'no output'}"
                )
        try:
            for n in range(1, RUNS + 1):
                pids = [os.getpid(), *([worker.pid] if worker else [])]
                t = measured_call(model, "redacted-recording", timing, pids)
                q = measured_call(model, "authored-meeting", quality, pids)
                if q.answer is not None and (body := validate(q.answer.text)) is not None:
                    q.score, unmatched = score(body, key)
                    for item in unmatched:
                        log.append(f"{name} run {n} {'loaded' if loaded else 'alone'}: {item}")
                runs.append({"run": n, "loaded": loaded, "calls": [t.row(), q.row()]})
                print(json.dumps(runs[-1], ensure_ascii=False), flush=True)
        finally:
            if worker is not None:
                worker.kill()
                worker.wait()
    model.close()
    return {"model": name, "runs": runs}


def _first_line(worker: subprocess.Popen[str], seconds: float) -> str:
    """The load process's first line, or `""` when none came within `seconds`."""
    lines: list[str] = []
    reader = threading.Thread(
        target=lambda: lines.append(worker.stdout.readline() if worker.stdout else ""),
        daemon=True,
    )
    reader.start()
    reader.join(seconds)
    return lines[0] if lines else ""


def summary(result: dict[str, Any]) -> dict[str, Any]:
    calls = [c for r in result["runs"] for c in r["calls"]]
    quality = [c for c in calls if c["input"] == "authored-meeting"]
    walls = [c["wall_s"] for c in calls if c["error"] is None]
    alone = [c for r in result["runs"] if not r["loaded"] for c in r["calls"]]
    return {
        "model": result["model"],
        "validated_every_run": all(c["valid_first_time"] for c in calls),
        "recall_median": statistics.median(
            [c["score"]["recall"] if c.get("score") else 0.0 for c in quality]
        ),
        "distractors_returned": sum(len(c["score"]["distractors_returned"]) for c in quality if c.get("score")),
        "ollama_peak_rss_mb": max(c["ollama_peak_rss_mb"] for c in calls),
        "wall_median_s": statistics.median(walls) if walls else None,
        "wall_max_s": max(walls) if walls else None,
        # Errored calls are left out of the medians, never out of this: a call
        # abandoned at its deadline is the one the 300-second Ask First is about.
        "reached_deadline": sum(
            1
            for c in calls
            if c["wall_s"] >= ASK_WALL_SECONDS or (c["error"] or "").startswith("ModelTimedOut")
        ),
        "swap_grew_alone": any(c["swap_after_mb"] > c["swap_before_mb"] for c in alone),
        "prompt_tokens_max": max((c["prompt_tokens"] or 0) for c in calls),
    }


def choose(summaries: list[dict[str, Any]]) -> str | None:
    """Story 7a's rule: validated every run; recall; lower peak; shorter median; qwen3:8b."""
    eligible = [s for s in summaries if s["validated_every_run"]]
    if not eligible:
        return None
    eligible.sort(
        key=lambda s: (
            -s["recall_median"],
            s["ollama_peak_rss_mb"],
            s["wall_median_s"] if s["wall_median_s"] is not None else float("inf"),
            0 if s["model"] == "qwen3:8b" else 1,
        )
    )
    return str(eligible[0]["model"])


def private(name: str, path: Path, *, window: int = DEFAULT_LOCAL_MODEL_WINDOW) -> None:
    """A real transcript: counts and outcomes only, never a word of text."""
    model = OllamaModel(name, window=window)
    parts = fragments(path)
    call = measured_call(model, "private", parts, [os.getpid()])
    body = validate(call.answer.text) if call.answer else None
    print(
        json.dumps(
            {
                "model": name,
                "utterances": len(parts),
                "prompt_tokens": call.answer.prompt_tokens if call.answer else None,
                "redactions_by_the_filter": call.redactions,
                "validated": body is not None,
                "decisions_returned": len(body["decisions"]) if body else None,
                "action_items_returned": len(body["action_items"]) if body else None,
                "wall_s": round(call.wall_s, 2),
                # The refusal's type only: a sentence could quote the server.
                "refused": call.error.split(":", 1)[0] if call.error else None,
            }
        )
    )
    model.close()


def build_parser() -> argparse.ArgumentParser:
    """The command line, built apart from `main` so a test can parse with it (story 7g)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", action="append", choices=CANDIDATES)
    parser.add_argument("--private", type=Path, help="a real transcript: counts only")
    parser.add_argument("--json", type=Path, help="also write the results here")
    parser.add_argument(
        "--num-ctx",
        type=int,
        choices=LOCAL_MODEL_WINDOWS,
        default=DEFAULT_LOCAL_MODEL_WINDOW,
        help="the window to measure at: one of the two `local_model_window` takes "
        f"(default {DEFAULT_LOCAL_MODEL_WINDOW})",
    )
    parser.add_argument("--load-worker", type=Path, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.load_worker:
        load_worker(args.load_worker)
        return 0
    window: int = args.num_ctx
    facts = machine()
    print(json.dumps({"machine": facts, "num_ctx": window}), flush=True)
    if facts["python_arch"] == "x86_64" and facts["rosetta"] == "yes":
        print(
            "ASK FIRST: pm-ai's Python runs as x86_64 under Rosetta. Story 7a stops "
            "here and asks rather than switching interpreters inside the slice.",
            file=sys.stderr,
        )
        return 2
    models = args.model or list(CANDIDATES)
    if args.private:
        for name in models:
            private(name, args.private, window=window)
        return 0
    root = Path(tempfile.mkdtemp(prefix="pm-ai-bench-"))
    home = Path.home() / ".pm-ai"
    if root == home or home in root.parents:
        shutil.rmtree(root, ignore_errors=True)
        raise SystemExit(f"refused: the throwaway root {root} is inside {home}, the real store.")
    key = tomllib.loads(KEY.read_text(encoding="utf-8"))
    unmatched: list[str] = []
    try:
        results = [run_model(name, root, key, unmatched, window=window) for name in models]
    finally:
        shutil.rmtree(root, ignore_errors=True)
    summaries = [summary(r) for r in results]
    print("\nsummaries:")
    for s in summaries:
        print(json.dumps(s))
        if s["reached_deadline"] or (s["wall_max_s"] and s["wall_max_s"] > ASK_WALL_SECONDS):
            print(
                f"ASK FIRST: {s['model']}: {s['reached_deadline']} call(s) reached "
                f"{ASK_WALL_SECONDS:g} s or the deadline; longest answered call "
                f"{s['wall_max_s']} s."
            )
        if s["swap_grew_alone"]:
            print(f"ASK FIRST: swap grew during a run of {s['model']} alone.")
        if not s["validated_every_run"]:
            print(f"note: {s['model']} did not validate on every run.")
    if len(summaries) > 1:
        chosen = choose(summaries)
        print(f"\nchosen: {chosen}" if chosen else "\nASK FIRST: no candidate validated on every run.")
    if unmatched:
        print("\nitems matching no key (read by hand: invented or legitimate):")
        for line in unmatched:
            print(f"  {line}")
    if args.json:
        args.json.write_text(
            json.dumps({"machine": facts, "results": results, "summaries": summaries,
                        "unmatched": unmatched}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
