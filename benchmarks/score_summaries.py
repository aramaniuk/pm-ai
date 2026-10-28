"""Story 7f: score the local meeting summary against a private reference set, run by hand — never by pytest.

    uv run python benchmarks/score_summaries.py --references DIR [--model qwen3:8b]
        [--setting key=value ...] [--repeat N]
    uv run python benchmarks/score_summaries.py --references DIR --rescore RUN
    uv run python benchmarks/score_summaries.py --references DIR --compare OLD NEW

`DIR` is a `0700` folder outside the repository holding `m<NN>.vtt` or
`m<NN>.txt` transcripts and one `m<NN>.reference.toml` each (the format is in
story 7f's spec; `fixtures/mixed_ru_en.reference.toml` is a fictional
example). Every referenced meeting is summarised through 7b's own path
(`summarize_meeting`) in a throwaway storage root at `DIR/.work/` — never
`~/.pm-ai` — and its returned decisions and action items are paired with the
reference items by the word groups each reference item lists. No model judges
a match.

Only stems, reference ids, numbers, enum values and exception type names
reach the screen. Python logging goes to `DIR/runs/<stamp>.log`, and the run —
including each returned item's text, so misses can be read — to
`DIR/runs/<stamp>.json`, both `0600`.

Exit 0 when scored, 1 when the local model is waiting (nothing is written)
or something unexpected stopped the run, 2 when an input is refused, 130 when
interrupted (nothing is written).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

import reference_scoring as scoring  # noqa: E402
from meeting_summary import PERSONAL, prepare  # noqa: E402

from pm_ai.app.pipelines import SummaryResult, note_summary_input, summarize_meeting  # noqa: E402
from pm_ai.core.config import ACCEPTED_KEYS, Config, ConfigRefused, load_config  # noqa: E402

LOG = logging.getLogger("score_summaries")
DEFAULT_TITLE = "Benchmark meeting"
DAEMON = re.compile(r"pm[-_]ai\b.*\bdaemon\s+run\b")

ModelFactory = Callable[[Config], Any]


class Waiting(Exception):
    """The local model is not there (Ollama absent, model not pulled): the run stops."""

    def __init__(self, stem: str) -> None:
        super().__init__(stem)
        self.stem = stem


# ── Small helpers ────────────────────────────────────────────────────────────


def say(stream: IO[str], line: str) -> None:
    print(line, file=stream, flush=True)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def commit() -> tuple[str, bool]:
    """The repository's full HEAD sha and whether its working tree is dirty."""
    try:
        return git("rev-parse", "HEAD"), bool(git("status", "--porcelain"))
    except (OSError, subprocess.CalledProcessError):
        return "unknown", True


def repo_trees() -> list[Path]:
    """This checkout's working tree and, for a linked worktree, the main one."""
    trees = [REPO]
    with contextlib.suppress(OSError, subprocess.CalledProcessError):
        common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
        trees.append(common.parent)
    return trees


def daemon_running() -> bool | None:
    """Whether a pm-ai daemon is in the process list — nothing under `~/.pm-ai` is opened.

    `None` when the process list could not be read: not checked, not "no daemon".
    """
    try:
        listing = subprocess.run(
            ["ps", "-axo", "command="], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return any(DAEMON.search(line) for line in listing.splitlines())


def private_file(path: Path, text: str = "") -> IO[str]:
    """A new file, mode exactly 0600, open for writing."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.fchmod(descriptor, 0o600)
    handle = os.fdopen(descriptor, "w", encoding="utf-8")
    if text:
        handle.write(text)
    return handle


def private_dir(path: Path) -> None:
    """A directory of mode 0700, never through a symlink — it would lead out of the folder."""
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise scoring.Refused(f"{path.name} in the references folder must be a plain directory")
    path.mkdir(mode=0o700, exist_ok=True)
    path.chmod(0o700)


def stamp(runs: Path) -> str:
    base = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    name, n = base, 1
    while (runs / f"{name}.json").exists() or (runs / f"{name}.log").exists():
        n += 1
        name = f"{base}-{n}"
    return name


def settings_config(model: str, settings: Sequence[str]) -> tuple[Config, dict[str, Any]]:
    """The `Config` the throwaway daemon is built with: `--setting`s through `config.toml`'s own rules."""
    given: dict[str, Any] = {}
    for setting in settings:
        key, sep, value = setting.partition("=")
        key = key.strip()
        if not sep or key not in ACCEPTED_KEYS or key == "local_model" or key in given:
            raise scoring.Refused(
                f"--setting {key or '(empty)'}: an unknown key, a repeated key, "
                f"local_model (use --model) or no '='"
            )
        try:
            given[key] = tomllib.loads(f"v = {value.strip()}")["v"]
        except tomllib.TOMLDecodeError:
            given[key] = value.strip()
        if not isinstance(given[key], (str, int, float, bool)):
            raise scoring.Refused(f"--setting {key}: a value must be text, a number or true/false")
    lines = [f"local_model = {json.dumps(model)}"]
    lines += [f"{key} = {json.dumps(value)}" for key, value in given.items()]
    try:
        config = load_config("\n".join(lines).encode("utf-8"))
    except ConfigRefused:
        raise scoring.Refused("a --model or --setting value is one config.toml refuses") from None
    return config, given


@contextlib.contextmanager
def private_logging(path: Path) -> Iterator[None]:
    """Every log record of the run to `path` (0600), none to the terminal."""
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    handle = private_file(path)
    handler = logging.StreamHandler(handle)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    logging.captureWarnings(True)
    try:
        yield
    finally:
        logging.captureWarnings(False)
        root.handlers, level = saved
        root.setLevel(level)
        handler.close()
        handle.close()


# ── The requests actually sent ───────────────────────────────────────────────


@contextlib.contextmanager
def recording_requests(sent: list[dict[str, Any]]) -> Iterator[None]:
    """Wrap the adapter's own loopback transport, noting each chat request's `options` and `think`.

    The adapter's watchdog cuts the same backend, so the deadline still
    applies. The messages are never read into `sent`.
    """
    import httpx

    from pm_ai.models.local import ollama

    class Recorder(httpx.BaseTransport):
        def __init__(self, inner: httpx.BaseTransport) -> None:
            self.inner = inner

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/chat":
                body = json.loads(request.content)
                sent.append({"options": body.get("options"), "think": body.get("think")})
            return self.inner.handle_request(request)

        def close(self) -> None:
            self.inner.close()

    original = ollama._default_transport  # noqa: SLF001 — the seam the adapter offers
    ollama._default_transport = lambda backend: Recorder(original(backend))  # type: ignore[assignment]  # noqa: SLF001
    try:
        yield
    finally:
        ollama._default_transport = original  # noqa: SLF001


def local_model(config: Config) -> Any:
    """The adapter wiring builds for `config` — today from `local_model` alone.

    No `--setting` changes the window until wiring passes one to the adapter
    (7g); the window recorded is the one read from each request sent.
    """
    from pm_ai.app.wiring import local_model as wired

    model = wired(config)
    if model is None:
        raise scoring.Refused("no local adapter could be built: run `uv sync --extra runtime`")
    return model


def distinct(entries: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for entry in entries:
        seen.setdefault(json.dumps(entry, sort_keys=True), entry)
    return list(seen.values())


# ── One summary ──────────────────────────────────────────────────────────────


def summarise_once(
    reference: scoring.Reference,
    repeat: int,
    config: Config,
    model_factory: ModelFactory,
    work: Path,
    sent: list[dict[str, Any]],
    err: IO[str],
) -> dict[str, Any]:
    """One summary of one meeting, scored; an exception is its type name, the stem, and the log."""
    root = work / f"{reference.stem}-{repeat}"
    private_dir(root)
    first = len(sent)
    started = time.monotonic()
    model = None
    daemon = None
    try:
        model = model_factory(config)
        daemon, meeting = prepare(
            model,
            reference.stem,
            reference.transcript,
            root,
            config=config,
            title=reference.title or DEFAULT_TITLE,
        )
        note_summary_input(daemon, PERSONAL, meeting.meeting_id, ingested_now=False)
        outcome = summarize_meeting(daemon, PERSONAL, meeting.meeting_id)
    except Exception as raised:  # noqa: BLE001 — named by type; the message goes to the log only
        LOG.exception("%s repeat %d raised", reference.stem, repeat)
        say(err, f"{reference.stem}: {type(raised).__name__}")
        return {
            "repeat": repeat,
            "result": "exception",
            "exception": type(raised).__name__,
            "reason": None,
            # Not measured: calls may have run before the exception.
            "wall_s": None,
            "calls": None,
            "prompt_tokens": None,
            "output_tokens": None,
            "prompt_characters": None,
            "sent": distinct(sent[first:]),
            "scores": scoring.score(reference, [], []),
            "decisions": [],
            "action_items": [],
        }
    finally:
        if model is not None:
            with contextlib.suppress(Exception):
                model.close()
        if daemon is not None:
            with contextlib.suppress(Exception):
                daemon.storage.close()
    if outcome.result is SummaryResult.WAITING:
        raise Waiting(reference.stem)
    answer = outcome.answer if outcome.result is SummaryResult.SUMMARIZED else None
    decisions = (
        [{"statement": d.statement, "said_by": d.said_by} for d in answer.decisions]
        if answer is not None
        else []
    )
    actions = (
        [{"what": a.what, "owner": a.owner, "due": a.due} for a in answer.action_items]
        if answer is not None
        else []
    )
    return {
        "repeat": repeat,
        "result": outcome.result.value,
        "exception": None,
        "reason": outcome.reason,
        "wall_s": round(time.monotonic() - started, 1),
        "calls": len(outcome.calls),
        "prompt_tokens": sum(c.prompt_tokens for c in outcome.calls),
        "output_tokens": sum(c.output_tokens for c in outcome.calls),
        "prompt_characters": outcome.prompt_characters,
        "sent": distinct(sent[first:]),
        "scores": scoring.score(reference, decisions, actions),
        "decisions": decisions,
        "action_items": actions,
    }


# ── Modes ────────────────────────────────────────────────────────────────────


def write_run(runs: Path, name: str, run: dict[str, Any]) -> Path:
    path = runs / f"{name}.json"
    with private_file(path) as handle:
        json.dump(run, handle, ensure_ascii=False, indent=2)
    return path


def rows(run: dict[str, Any]) -> list[str]:
    """Every printed line, built before anything is written, so a failure leaves no file."""
    lines = [json.dumps(scoring.meeting_row(stem, entry)) for stem, entry in run["meetings"].items()]
    return [*lines, json.dumps(run["totals"])]


def print_rows(lines: Sequence[str], out: IO[str]) -> None:
    for line in lines:
        say(out, line)


def read_run(path: Path, folder: Path) -> dict[str, Any]:
    if not scoring.inside(path, folder) or not path.resolve().is_file():
        raise scoring.Refused("a run file must be a file inside the references folder")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise scoring.Refused("a run file is not UTF-8 JSON") from None
    return dict(scoring.check_run(data, "run file"))


def warn_about(found: scoring.Scan, err: IO[str]) -> None:
    for stem in found.unreferenced:
        say(err, f"warning: {stem} has no reference; not run")
    for stem in found.second:
        say(err, f"warning: {stem} has a second transcript its reference does not name; ignored")
    for reference in found.references:
        text = reference.transcript.read_text(encoding="utf-8")
        for line in scoring.group_warnings(reference, text):
            say(err, f"warning: {line}: no word of this group occurs in the transcript")


def score_run(
    args: argparse.Namespace,
    folder: Path,
    found: scoring.Scan,
    model_factory: ModelFactory | None,
    processes: Callable[[], bool | None],
    out: IO[str],
    err: IO[str],
) -> int:
    if args.repeat < 1:
        raise scoring.Refused("--repeat must be at least 1")
    config, given = settings_config(args.model, args.setting)
    if model_factory is None:
        local_model(config).close()  # refused here, before any meeting, when none can be built
    running = processes()
    if running:
        say(err, "warning: a pm-ai daemon is running; it shares Ollama with this run (recorded)")
    summary_commit, dirty = commit()
    work = folder / ".work"
    shutil.rmtree(work, ignore_errors=True)  # refuses a symlink, which private_dir then names
    private_dir(work)
    runs = folder / "runs"
    created_runs = not runs.exists()
    private_dir(runs)
    name = stamp(runs)
    log = runs / f"{name}.log"
    sent: list[dict[str, Any]] = []
    meetings: dict[str, Any] = {}
    factory = model_factory if model_factory is not None else local_model
    recording = recording_requests(sent) if model_factory is None else contextlib.nullcontext()
    try:
        with private_logging(log), recording:
            for reference in found.references:
                repeats = []
                for repeat in range(1, args.repeat + 1):
                    repeats.append(
                        summarise_once(reference, repeat, config, factory, work, sent, err)
                    )
                    say(err, f"{reference.stem} {repeat}/{args.repeat} {repeats[-1]['result']}")
                meetings[reference.stem] = {
                    "reference_sha256": reference.sha256,
                    "repeats": repeats,
                    "answers_identical": scoring.identical(
                        [(r["result"], r["decisions"], r["action_items"]) for r in repeats]
                    ),
                    "scores_identical": scoring.identical([r["scores"] for r in repeats]),
                }
    except (Waiting, KeyboardInterrupt) as stopped:
        log.unlink(missing_ok=True)
        if created_runs:
            with contextlib.suppress(OSError):
                runs.rmdir()
        if not isinstance(stopped, Waiting):
            raise
        waiting = stopped
        say(
            err,
            f"stopped: {waiting.stem} is waiting for the local model (Ollama not answering "
            f"or the model not pulled); nothing was written",
        )
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    run: dict[str, Any] = {
        "format": scoring.FORMAT,
        "summary_commit": summary_commit,
        "summary_dirty": dirty,
        "scorer_commit": summary_commit,
        "scorer_dirty": dirty,
        "model": args.model,
        "settings": given,
        "sent": distinct([s for e in meetings.values() for r in e["repeats"] for s in r["sent"]]),
        "daemon_running": running,
        "repeat": args.repeat,
        "meetings": meetings,
        "totals": scoring.totals(meetings),
    }
    lines = rows(run)
    path = write_run(runs, name, run)
    print_rows(lines, out)
    say(err, f"run file: runs/{path.name}")
    return 0


def rescore_run(args: argparse.Namespace, folder: Path, found: scoring.Scan, out: IO[str], err: IO[str]) -> int:
    old = read_run(args.rescore, folder)
    scorer_commit, dirty = commit()
    run = scoring.rescored(
        old,
        {r.stem: r for r in found.references},
        scorer_commit,
        dirty,
        args.rescore.resolve().name,
    )
    lines = rows(run)
    runs = folder / "runs"
    private_dir(runs)
    path = write_run(runs, stamp(runs), run)
    print_rows(lines, out)
    say(err, f"run file: runs/{path.name}")
    return 0


def compare_runs(args: argparse.Namespace, folder: Path, out: IO[str]) -> int:
    old_path, new_path = args.compare
    for row in scoring.compare(read_run(old_path, folder), read_run(new_path, folder)):
        say(out, json.dumps(row))
    return 0


def parser() -> argparse.ArgumentParser:
    made = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    made.add_argument("--references", type=Path, required=True)
    made.add_argument("--model", help="default qwen3:8b")
    made.add_argument("--setting", action="append", metavar="KEY=VALUE")
    made.add_argument("--repeat", type=int, help="default 1")
    mode = made.add_mutually_exclusive_group()
    mode.add_argument("--rescore", type=Path, metavar="RUN")
    mode.add_argument("--compare", type=Path, nargs=2, metavar=("OLD", "NEW"))
    return made


def main(
    argv: Sequence[str] | None = None,
    *,
    model_factory: ModelFactory | None = None,
    processes: Callable[[], bool | None] = daemon_running,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
) -> int:
    """The script; `model_factory` is the tests' canned model (default: the wired Ollama adapter)."""
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    args = parser().parse_args(argv)
    root = logging.getLogger()
    saved = root.handlers[:]
    # Until the run's private log exists, no record reaches the terminal.
    root.handlers = [logging.NullHandler()]
    try:
        if (args.rescore or args.compare) and (
            args.model is not None or args.setting is not None or args.repeat is not None
        ):
            raise scoring.Refused("--model, --setting and --repeat do not apply to --rescore or --compare")
        args.model = args.model if args.model is not None else "qwen3:8b"
        args.setting = args.setting if args.setting is not None else []
        args.repeat = args.repeat if args.repeat is not None else 1
        folder = scoring.check_folder(args.references, repo_trees())
        if args.compare:
            return compare_runs(args, folder, out)
        found = scoring.scan(folder)
        warn_about(found, err)
        if args.rescore:
            return rescore_run(args, folder, found, out, err)
        return score_run(args, folder, found, model_factory, processes, out, err)
    except scoring.Refused as refused:
        say(err, f"refused: {refused}")
        return 2
    except KeyboardInterrupt:
        say(err, "stopped: interrupted; no run file was written")
        return 130
    except Exception as raised:  # noqa: BLE001 — never a message on screen: it may quote a file
        say(err, f"stopped: {type(raised).__name__}")
        return 1
    finally:
        root.handlers = saved


if __name__ == "__main__":
    raise SystemExit(main())
