"""Story 7b's measurement of a meeting summary on the local model, run by hand — never by pytest.

    uv run python benchmarks/meeting_summary.py --model qwen3:8b

For each input, in a throwaway storage root this script creates — never
`~/.pm-ai` — a personal meeting is recorded with the input as its Graph
capture and its transcript row `ingested`, then summarised twice through the
slice's own path, with Ollama as `daemon.model` and every stage run inline:

- **from ingestion** — `note_summary_input` then `summarize_meeting`, the
  automatic job. The harvest's interval (240 minutes) is not included: pm-ai
  observes no meeting end in this wave, so the figure starts at ingestion;
- **from the command** — `request_summary`, what `transcript summarize` runs in
  the daemon, timed until the record is written.

The inputs are `7a`'s: the authored fictional meeting
(`fixtures/mixed_ru_en.vtt`) and the letter-redacted recording from PR #72,
whose length and alphabet are real and whose words are not.

Recorded per summary: the outcome, the prompt's characters, each call's prompt
and output tokens and model time, the wall time, the counts of decisions and
action items, and the share of the summary's letters that are Cyrillic. Then
the largest valid answer seen: its characters, its output tokens and their
ratio, and the largest answer the limits allow projected at that ratio against
three quarters of the output cap. No transcript text and no answer text is
printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from pm_ai.app.pipelines import (  # noqa: E402
    SummaryJobs,
    note_summary_input,
    request_summary,
    summarize_meeting,
)
from pm_ai.app.wiring import build  # noqa: E402
from pm_ai.core.config import Config  # noqa: E402
from pm_ai.core.meeting_records import capture_name  # noqa: E402
from pm_ai.domain.identity import Actor, DataScope, ScopeKind  # noqa: E402
from pm_ai.domain.meeting_summaries import (  # noqa: E402
    DUE_LIMIT,
    MAX_ACTION_ITEMS,
    MAX_DECISIONS,
    STATEMENT_LIMIT,
    SUMMARY_LIMIT,
    WHAT_LIMIT,
)
from pm_ai.domain.meetings import Meeting  # noqa: E402
from pm_ai.domain.transcripts import CaptureFormat, TranscriptStateKind  # noqa: E402
from pm_ai.models.local.ollama import NUM_PREDICT, OllamaModel  # noqa: E402

AUTHORED = Path(__file__).resolve().parent / "fixtures" / "mixed_ru_en.vtt"
REDACTED = REPO / "tests" / "connectors" / "fixtures" / "graph" / "transcript_recorded.vtt"
PERSONAL = DataScope(ScopeKind.PERSONAL)
LABEL_CHARACTERS = 40


def largest_valid_answer_characters() -> int:
    """The largest answer the limits allow, as JSON, each label at 40 characters."""
    label = "L" * LABEL_CHARACTERS
    return len(
        json.dumps(
            {
                "summary": "s" * SUMMARY_LIMIT,
                "decisions": [{"statement": "d" * STATEMENT_LIMIT, "said_by": label}]
                * MAX_DECISIONS,
                "action_items": [{"what": "w" * WHAT_LIMIT, "owner": label, "due": "u" * DUE_LIMIT}]
                * MAX_ACTION_ITEMS,
            },
            ensure_ascii=False,
        )
    )


def cyrillic_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum("Ѐ" <= c <= "ӿ" for c in letters) / len(letters)


class Recording:
    """The adapter, with each answer's characters and output tokens noted — never its text."""

    def __init__(self, model: OllamaModel) -> None:
        self.model = model
        self.answers: list[tuple[int, int]] = []

    @property
    def name(self) -> str:
        return self.model.name

    def complete(self, **request: Any) -> Any:
        answer = self.model.complete(**request)
        self.answers.append((len(answer.text), answer.output_tokens))
        return answer

    def close(self) -> None:
        self.model.close()


def measured(
    label: str, kind: str, started: float, outcome: Any, model: Recording, first: int
) -> dict[str, Any]:
    wall = time.monotonic() - started
    answer = outcome.answer
    seen = model.answers[first:]
    return {
        "input": label,
        "path": kind,
        "result": outcome.result.value,
        "reason": outcome.reason,
        "prompt_characters": outcome.prompt_characters,
        "calls": [
            {
                "prompt_tokens": c.prompt_tokens,
                "output_tokens": c.output_tokens,
                "model_s": None if c.total_ns is None else round(c.total_ns / 1e9, 1),
            }
            for c in outcome.calls
        ],
        "wall_s": round(wall, 1),
        "decisions": None if answer is None else len(answer.decisions),
        "action_items": None if answer is None else len(answer.action_items),
        "cyrillic_share": None if answer is None else round(cyrillic_share(answer.summary), 2),
        # The raw answer the validator accepted is the last one this summary got.
        "answer_characters": seen[-1][0] if answer is not None and seen else None,
        "answer_output_tokens": seen[-1][1] if answer is not None and seen else None,
    }


def run(model_name: str, label: str, path: Path, root: Path) -> list[dict[str, Any]]:
    daemon = build(root, PERSONAL, config=Config())
    model = Recording(OllamaModel(model_name))
    daemon.model = model  # type: ignore[assignment]
    daemon.summaries = SummaryJobs()
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    meeting = Meeting(
        meeting_id=f"mtg_bench_{label}",
        title="Benchmark meeting",
        start=start,
        duration_minutes=60,
        attendees=(Actor("email:pm@example.com"),),
        scope=PERSONAL,
    )
    daemon.meetings.put(meeting)
    daemon.storage.write_capture(
        path.read_text(encoding="utf-8"),
        scope=PERSONAL,
        name=capture_name(meeting.meeting_id, start, CaptureFormat.GRAPH),
    )
    daemon.storage.record_transcript_state(
        PERSONAL, meeting.meeting_id, TranscriptStateKind.INGESTED, "ingested", retryable=False
    )
    rows = []
    started = time.monotonic()
    note_summary_input(daemon, PERSONAL, meeting.meeting_id, ingested_now=False)
    outcome = summarize_meeting(daemon, PERSONAL, meeting.meeting_id)
    rows.append(measured(label, "from ingestion", started, outcome, model, 0))

    done: list[Any] = []
    first = len(model.answers)
    started = time.monotonic()
    request_summary(
        daemon, scope=PERSONAL, meeting_id=meeting.meeting_id, job="bench", on_done=done.append
    )
    if done and not isinstance(done[0], BaseException):
        rows.append(measured(label, "from the command", started, done[0], model, first))
    else:
        rows.append({"input": label, "path": "from the command", "error": repr(done[:1])})
    model.close()
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--json", type=Path, help="also write the results here")
    args = parser.parse_args(argv)
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="pm-ai-7b-bench-") as tmp:
        for label, path in (("authored", AUTHORED), ("redacted", REDACTED)):
            root = Path(tmp) / label
            root.mkdir()
            for row in run(args.model, label, path, root):
                results.append(row)
                print(json.dumps(row, ensure_ascii=False))
    valid = [
        r for r in results if r.get("answer_characters") and r.get("answer_output_tokens")
    ]
    report: dict[str, Any] = {"model": args.model, "output_cap": NUM_PREDICT}
    if valid:
        top = max(valid, key=lambda r: r["answer_characters"])
        ratio = top["answer_characters"] / top["answer_output_tokens"]
        bound = largest_valid_answer_characters()
        report.update(
            largest_seen_characters=top["answer_characters"],
            largest_seen_output_tokens=top["answer_output_tokens"],
            characters_per_token=round(ratio, 2),
            limits_largest_characters=bound,
            limits_largest_tokens_at_that_ratio=round(bound / ratio),
            three_quarters_of_cap=NUM_PREDICT * 3 // 4,
        )
    print(json.dumps(report))
    if args.json:
        args.json.write_text(json.dumps({"runs": results, "report": report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
