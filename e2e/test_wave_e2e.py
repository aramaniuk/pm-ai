"""The wave-end end-to-end check: `e2e/scenario.md`, replayed.

Drives the real `pm-ai` binary (`.venv/bin/pm-ai`) as a subprocess against a
throwaway home, with `PM_AI_DISABLE_ENCRYPTION=1` and a daemon on a free port,
and the real local model (Ollama, qwen3:8b). Run it at the end of every wave:

    uv run pytest e2e/ -v

It lives outside `tests/` on purpose: it needs Ollama and takes minutes, so the
default suite (and its skip ratchet) never collects it.

Nothing here touches `~/.pm-ai`, `~/.manager-ai` or the login keychain. Only
`setup` and `key enrol` write the keychain, and neither is run. Every input is
fictional (the repo is public).

The tests run in file order and share one home. A finding still open is an
`xfail(strict=True)` test: when the fix lands it passes, the run turns red, and
the marker comes off in the fixing commit.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BINARY = REPO / ".venv" / "bin" / "pm-ai"
VTT = REPO / "benchmarks" / "fixtures" / "mixed_ru_en.vtt"
OLLAMA = "http://127.0.0.1:11434/api/tags"
MODEL = "qwen3:8b"
LIST_BOUND = 1.0
VERB_BOUND = 2.0
SUMMARY_BOUND = 180.0
STANDUP = (
    "Alex: I will send the draft release notes by Friday.\n"
    "Jamie: I will fix the Redis cache issue by next Tuesday.\n"
    "Alex: We decided to freeze the API on November 2.\n"
)


@dataclass
class Run:
    code: int
    out: str
    seconds: float

    def json(self) -> dict:
        return json.loads(self.out)


@dataclass
class World:
    root: Path
    home: Path
    work: Path
    port: int
    daemon: subprocess.Popen | None = None
    ids: dict[str, str] = field(default_factory=dict)

    def env(self) -> dict[str, str]:
        return {
            "HOME": str(self.home),
            "PM_AI_DISABLE_ENCRYPTION": "1",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "en_US.UTF-8",
        }

    def pm(self, *args: str, timeout: float = 60.0) -> Run:
        started = time.monotonic()
        done = subprocess.run(
            [str(BINARY), *args],
            cwd=self.work,
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # The encryption warning is printed on every command; it is not output.
        out = "\n".join(
            line
            for line in (done.stdout + done.stderr).splitlines()
            if not line.startswith("WARNING: encryption is disabled")
        )
        return Run(done.returncode, out, time.monotonic() - started)

    def start_daemon(self) -> None:
        self.daemon = subprocess.Popen(
            [str(BINARY), "daemon", "run"],
            cwd=self.work,
            env=self.env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 20
        assert self.daemon.stdout is not None
        while time.monotonic() < deadline:
            line = self.daemon.stdout.readline()
            if "listening on 127.0.0.1" in line:
                return
            if self.daemon.poll() is not None:
                break
        pytest.fail("the daemon did not report listening within 20 s")

    def stop_daemon(self) -> None:
        if self.daemon is None or self.daemon.poll() is not None:
            return
        self.daemon.send_signal(signal.SIGINT)
        try:
            self.daemon.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.daemon.kill()
        self.daemon = None


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _ollama_has_model() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA, timeout=3) as answer:
            names = [m["name"] for m in json.load(answer)["models"]]
    except OSError:
        return False
    return MODEL in names


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    assert BINARY.exists(), f"{BINARY} is missing: run `uv sync --extra runtime`"
    # A wave-end check that silently skips is not a check: fail loudly.
    assert _ollama_has_model(), f"Ollama is not answering with {MODEL} on 127.0.0.1:11434"
    root = tmp_path_factory.mktemp("pm-ai-e2e")
    w = World(root=root, home=root / "home", work=root / "work", port=_free_port())
    real_home = Path(os.path.expanduser("~")).resolve()
    assert not w.home.resolve().is_relative_to(real_home / ".pm-ai")
    (w.home / ".pm-ai").mkdir(parents=True)
    w.work.mkdir()
    (w.home / ".pm-ai" / "config.toml").write_text(
        f'display_timezone = "Europe/Warsaw"\ndaemon_port = {w.port}\nlocal_model = "{MODEL}"\n',
        encoding="utf-8",
    )
    yield w
    w.stop_daemon()
    shutil.rmtree(root, ignore_errors=True)


def _proposals(w: World, scope: str, state: str = "staged") -> list[dict]:
    run = w.pm("proposal", "list", "--scope", scope, "--state", state, "--json")
    return run.json()["proposals"]


def _commitment(w: World, cid: str, scope: str = "personal") -> dict:
    return w.pm("commitment", "show", cid, "--scope", scope, "--json").json()["commitment"]


# ── Setup ────────────────────────────────────────────────────────────────────


def test_s2_doctor_before_the_daemon(world):
    run = world.pm("doctor")
    assert "[     ok] local model: qwen3:8b is pulled" in run.out
    assert "[ absent] project registry" in run.out
    assert "daemon: not running" in run.out


def test_s3_project_add(world):
    repo = world.work / "proj-alpha"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@example.com",
         "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=repo, check=True,
    )
    run = world.pm("project", "add", str(repo), "alpha")
    assert run.code == 0, run.out
    assert "alpha is onboarded" in run.out
    assert (repo / ".gitignore").exists()


def test_s4_daemon_starts(world):
    world.start_daemon()
    run = world.pm("doctor")
    assert f"daemon: running on 127.0.0.1:{world.port}" in run.out


# ── Transcripts and summaries ───────────────────────────────────────────────


def test_t1_dry_run_writes_nothing(world):
    shutil.copy(VTT, world.work / "release-planning.vtt")
    run = world.pm("transcript", "drop", "release-planning.vtt", "--scope", "personal",
                   "--dry-run", "--json")
    assert run.code == 0, run.out
    seen = run.json()
    assert seen["duration_minutes"]["value"] == 15
    assert len(seen["participants"]) == 4
    assert not (world.home / ".manager-ai" / "transcripts").exists()


def test_t2_drop_and_t3_list(world):
    run = world.pm("transcript", "drop", "release-planning.vtt", "--scope", "personal",
                   "--title", "Release planning (fictional)")
    assert run.code == 0, run.out
    meeting = re.search(r"new manual-only record (mtg_\w+)", run.out)
    assert meeting, run.out
    world.ids["personal_meeting"] = meeting.group(1)
    listing = world.pm("transcript", "list", "--scope", "personal")
    assert listing.seconds < LIST_BOUND + 0.5
    assert "Release planning (fictional)" in listing.out
    assert "transcript ingested" in listing.out


def test_t4_local_summary(world):
    run = world.pm("transcript", "summarize", "--scope", "personal",
                   "--meeting", world.ids["personal_meeting"], timeout=SUMMARY_BOUND)
    assert "summarised by the local model qwen3:8b" in run.out, run.out
    found = re.search(r"(\d+) decision\(s\), (\d+) action item\(s\) staged", run.out)
    assert found and int(found.group(2)) >= 1, run.out
    assert run.seconds < SUMMARY_BOUND


def test_t5_project_drop_with_a_given_start(world):
    (world.work / "standup.txt").write_text(STANDUP, encoding="utf-8")
    # A new project is seen by a daemon only after a restart (finding F10).
    world.stop_daemon()
    world.start_daemon()
    run = world.pm("transcript", "drop", "standup.txt", "--scope", "project:alpha",
                   "--title", "Alpha standup (fictional)",
                   "--start", "2026-10-26T10:00+01:00", "--duration", "15")
    assert run.code == 0, run.out
    meeting = re.search(r"new manual-only record (mtg_\w+)", run.out)
    world.ids["project_meeting"] = meeting.group(1)
    summary = world.pm("transcript", "summarize", "--scope", "project:alpha",
                       "--meeting", world.ids["project_meeting"], timeout=SUMMARY_BOUND)
    assert "summarised by the local model" in summary.out, summary.out
    for proposal in _proposals(world, "project:alpha"):
        world.pm("proposal", "approve", proposal["proposal_id"], "--scope", "project:alpha")


def test_t6_markdown_participants(world):
    (world.work / "standup.md").write_text(STANDUP, encoding="utf-8")
    run = world.pm("transcript", "drop", "standup.md", "--scope", "project:alpha",
                   "--dry-run", "--json")
    names = sorted(p["name"] for p in run.json()["participants"])
    assert names == ["Alex", "Jamie"]


# ── Proposals ────────────────────────────────────────────────────────────────


def test_p1_to_p6_proposal_verbs(world):
    listing = world.pm("proposal", "list", "--scope", "personal")
    assert listing.seconds < LIST_BOUND + 0.5
    items = [p for p in _proposals(world, "personal") if p["type"] == "action_item"]
    assert len(items) >= 3, "the summary staged fewer than three action items"
    edit, reject, approve = items[0], items[1], items[2]

    run = world.pm("proposal", "edit", edit["proposal_id"], "--scope", "personal",
                   "--owner", "Alex Morgan", "--due", "friday")
    edited = edit["proposal_id"] + "_e1"
    assert f"replaced by {edited}" in run.out, run.out

    run = world.pm("proposal", "reject", reject["proposal_id"], "--scope", "personal")
    assert "rejected" in run.out, run.out
    run = world.pm("proposal", "approve", reject["proposal_id"], "--scope", "personal")
    assert run.code != 0 and "nothing was changed" in run.out

    for pid in (approve["proposal_id"], edited):
        run = world.pm("proposal", "approve", pid, "--scope", "personal")
        assert "recorded as a commitment" in run.out, run.out
        assert run.seconds < VERB_BOUND + 0.5

    run = world.pm("proposal", "reject", approve["proposal_id"], "--scope", "personal")
    assert run.code != 0 and "commitment withdrawn" in run.out

    world.ids["rejected"] = reject["proposal_id"]
    world.ids["c_edited"] = edit["proposal_id"]
    world.ids["c_plain"] = approve["proposal_id"]


# ── Commitments ──────────────────────────────────────────────────────────────


def test_c1_list_and_json(world):
    run = world.pm("commitment", "list", "--json")
    assert run.seconds < LIST_BOUND + 0.5
    body = run.json()
    assert body["format"] == 1
    ids = [c["commitment_id"] for c in body["commitments"]]
    assert world.ids["c_plain"] in ids and world.ids["c_edited"] in ids
    assert world.ids["rejected"] not in ids


def test_c2_due_by_words(world):
    cid = world.ids["c_plain"]
    run = world.pm("commitment", "due", cid, "2026-11-09", "--scope", "personal")
    assert run.seconds < VERB_BOUND + 0.5
    assert _commitment(world, cid)["due"]["date"] == "2026-11-09"
    run = world.pm("commitment", "due", cid, "friday", "--scope", "personal")
    assert _commitment(world, cid)["due"]["date"] is not None, run.out


def test_c3_c4_owner_name_and_refused_address(world):
    cid = world.ids["c_plain"]
    world.pm("commitment", "owner", cid, "Jamie Lee", "--scope", "personal")
    assert _commitment(world, cid)["owner"] == "Jamie Lee"
    run = world.pm("commitment", "owner", cid, "jamie@example.com", "--scope", "personal")
    assert run.code != 0 and "never a handle" in run.out
    assert _commitment(world, cid)["owner"] == "Jamie Lee"


def test_c6_done_and_undo(world):
    cid = world.ids["c_edited"]
    world.pm("commitment", "done", cid, "--scope", "personal")
    assert _commitment(world, cid)["state"] == "fulfilled"
    world.pm("commitment", "undo", cid, "--scope", "personal")
    assert _commitment(world, cid)["state"] == "pending"
    again = world.pm("commitment", "undo", cid, "--scope", "personal")
    assert "already undone" in again.out


def test_c7_stale_version_refused(world):
    cid = world.ids["c_plain"]
    run = world.pm("commitment", "broken", cid, "--scope", "personal",
                   "--if-version", "cml_000000000000", "--json")
    assert run.json()["outcome"] == "refused"
    assert _commitment(world, cid)["state"] == "pending"


def test_c8_altered_and_withdrawn(world):
    cid = world.ids["c_plain"]
    world.pm("commitment", "altered", cid, "--what", "Reworded (fictional)", "--scope", "personal")
    shown = _commitment(world, cid)
    assert shown["state"] == "altered" and shown["what"] == "Reworded (fictional)"
    world.pm("commitment", "withdrawn", world.ids["c_edited"], "--note", "e2e", "--scope", "personal")
    open_ids = [c["commitment_id"] for c in world.pm("commitment", "list", "--json").json()["commitments"]]
    assert world.ids["c_edited"] not in open_ids


def test_c9_unknown_id_refused_in_json(world):
    run = world.pm("commitment", "show", "prp_nope", "--scope", "personal", "--json")
    body = run.json()
    assert body["outcome"] == "refused" and body["code"] == "not_found"


# ── Goals, members, pages, logs ─────────────────────────────────────────────


def test_g1_goal_round_trip(world):
    world.pm("goal", "set", "billing", "--domain", "project", "--horizon", "medium",
             "--title", "Ship the billing migration without downtime")
    world.pm("goal", "update", "billing", "--title", "Ship billing with zero downtime")
    assert "Ship billing with zero downtime" in world.pm("goal", "show", "billing").out
    world.pm("goal", "set", "spare", "--domain", "team", "--horizon", "long", "--title", "Spare")
    world.pm("goal", "delete", "spare", "--yes")
    listing = world.pm("goal", "list").out
    assert "billing" in listing and "spare" not in listing
    world.ids["goal_title"] = "Ship billing with zero downtime"


def test_m1_member_needs_a_project_scope(world):
    run = world.pm("member", "unmatched", "--scope", "personal")
    assert run.code != 0 and "--scope project:" in run.out


def test_d1_dashboards(world):
    run = world.pm("dashboard")
    assert run.code == 0, run.out
    page = (world.home / ".manager-ai" / "memory" / "daily_dashboard.md").read_text(encoding="utf-8")
    assert "Open commitments, soonest due first:" in page
    assert "Reworded (fictional)" in page
    assert "commitments from this meeting" in page
    assert world.ids["goal_title"] in page
    run = world.pm("dashboard", "--scope", "project:alpha")
    project_page = (world.work / "proj-alpha" / ".project-ai" / "memory" / "daily_dashboard.md")
    assert project_page.exists(), run.out
    assert "## Team Activity" in project_page.read_text(encoding="utf-8")


def test_l1_logs_hold_no_error(world):
    run = world.pm("logs", "--level", "error")
    assert "Traceback" not in run.out
    assert " error " not in run.out


# ── Findings still open: each passes once its fix lands ─────────────────────


@pytest.mark.xfail(strict=True, reason="F1: an absolute due is unresolved when the meeting day was estimated")
def test_f1_absolute_due_resolves_on_a_dropped_file(world):
    dated = [p for p in _proposals(world, "personal") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p["fields"].get("due") or "")]
    assert dated, "the summary staged no action item with an ISO due"
    pid = dated[0]["proposal_id"]
    world.pm("proposal", "approve", pid, "--scope", "personal")
    assert _commitment(world, pid)["due"]["date"] == dated[0]["fields"]["due"]


@pytest.mark.xfail(strict=True, reason="F7: an owner given as @handle is accepted")
def test_f7_owner_handle_refused(world):
    cid = world.ids["c_plain"]
    run = world.pm("commitment", "owner", cid, "@jamie", "--scope", "personal")
    assert run.code != 0


@pytest.mark.xfail(strict=True, reason="F6: 'next friday' names no date")
def test_f6_next_weekday_resolves(world):
    listed = world.pm("commitment", "list", "--scope", "project:alpha", "--json").json()["commitments"]
    assert listed, "no project commitment to set a due on"
    run = world.pm("commitment", "due", listed[0]["commitment_id"], "next friday",
                   "--scope", "project:alpha", "--json")
    assert run.json()["outcome"] != "refused", run.out
