"""The workflow guards (scripts/guards/guards.py, .agents/WORKFLOW.md §4).

Each rule is shown refusing what it exists to refuse and letting through the
nearest legitimate neighbour, so a guard that silently allows everything, or
blocks routine work, fails here.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

GUARDS = Path(__file__).resolve().parents[2] / "scripts" / "guards" / "guards.py"
_spec = importlib.util.spec_from_file_location("pm_ai_workflow_guards", GUARDS)
assert _spec is not None and _spec.loader is not None
guards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guards)
Refused = guards.Refused


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "pm-ai"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "dev@example.invalid")
    _git(root, "config", "user.name", "Dev")
    (root / "README.md").write_text("x\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return root


# --- grants ----------------------------------------------------------------------


def test_an_ungranted_worktree_is_refused_and_a_granted_one_allowed(tmp_path, monkeypatch):
    grants = tmp_path / "grants.json"
    monkeypatch.setenv(guards.GRANTS_ENV, str(grants))
    with pytest.raises(Refused, match="no grants file"):
        guards.check_grant(tmp_path / "agent-1")
    grants.write_text(json.dumps({"agent-1": {"slice": "16a"}}))
    guards.check_grant(tmp_path / "agent-1")
    with pytest.raises(Refused, match="'agent-2' has no grant"):
        guards.check_grant(tmp_path / "agent-2")


def test_an_edit_outside_the_project_repo_needs_no_grant(tmp_path, repo, monkeypatch):
    monkeypatch.setenv(guards.GRANTS_ENV, str(tmp_path / "missing.json"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "scratch" / "n.md")}, "cwd": str(repo)}
    guards.claude_pre(payload)
    payload["tool_input"]["file_path"] = str(repo / "pm_ai" / "new.py")
    with pytest.raises(Refused, match="no grants file"):
        guards.claude_pre(payload)


# --- skill-derived files ------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    ["_bmad-output/specs/spec-pm-ai/SPEC.md", "_bmad-output/specs/spec-pm-ai/glossary.md"],
)
def test_the_spec_and_its_companions_are_written_only_by_bmad_spec(tmp_path, rel):
    with pytest.raises(Refused, match="bmad-spec"):
        guards.check_derived(Path(rel), None)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"name":"Skill","input":{"skill":"bmad-spec"}}\n')
    guards.check_derived(Path(rel), str(transcript))


def test_the_memlog_is_never_edited_by_hand_and_slice_specs_are_free(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"skill": "bmad-spec"}\n')
    with pytest.raises(Refused, match="memlog.py"):
        guards.check_derived(Path("_bmad-output/specs/spec-pm-ai/.memlog.md"), str(transcript))
    guards.check_derived(Path("_bmad-output/specs/spec-pm-ai/stories/16a-x.md"), None)
    guards.check_derived(Path("_bmad-output/specs/spec-pm-ai/stories.yaml"), None)


# --- the real home and the keychain ---------------------------------------------------


def test_the_real_pm_ai_home_is_refused_and_a_throwaway_one_allowed(tmp_path):
    home = tmp_path / "me"
    with pytest.raises(Refused, match="real pm-ai home"):
        guards.check_path_not_private_home(home / ".pm-ai" / "config.toml", home)
    guards.check_path_not_private_home(tmp_path / "scratch" / ".pm-ai" / "config.toml", home)


@pytest.mark.parametrize(
    "command",
    [
        "uv run pm-ai setup",
        ".venv/bin/pm-ai key enrol",
        "cat ~/.pm-ai/config.toml",
        "ls $HOME/.manager-ai",
        "git commit --no-verify -m x",
        "git commit -nm x",
        "git push --force origin claude/feat-x",
        "git push -f",
        "git push origin +claude/feat-x",
        "git push origin main",
        "git push origin HEAD:main",
        "cat <<'EOF' > notes.md\ntext\nEOF\ncat ~/.pm-ai/config.toml",
    ],
)
def test_refused_commands(tmp_path, command):
    with pytest.raises(Refused):
        guards.check_command(command, tmp_path, home=tmp_path)


@pytest.mark.parametrize(
    "command",
    [
        "HOME=/tmp/x .venv/bin/pm-ai doctor",
        "HOME=/tmp/x ls $HOME/.pm-ai",
        'grep -n "pm-ai setup" AGENTS.md',
        "git push -u origin claude/feat-16a-main-thing",
        "git commit -m 'note -n'",
        "uv run pytest -q",
        "gh pr create --body-file - <<'EOF'\nnever touches ~/.pm-ai or `pm-ai setup`\nEOF",
        "git commit -F - <<EOF\nthe real ~/.manager-ai stays untouched\nEOF\n",
    ],
)
def test_allowed_commands(tmp_path, command):
    guards.check_command(command, tmp_path, home=tmp_path)


def test_a_bare_push_from_main_is_refused(repo):
    with pytest.raises(Refused, match="on main"):
        guards.check_command("git push", repo, home=repo)
    _git(repo, "checkout", "-q", "-b", "claude/feat-x")
    guards.check_command("git push", repo, home=repo)


# --- git hooks ----------------------------------------------------------------------------


def test_staged_lines_naming_this_machine_are_refused(repo):
    (repo / "fixture.md").write_text("path /home/devuser/.pm-ai\nfine\n")
    _git(repo, "add", "fixture.md")
    lines = guards.staged_added_lines(repo)
    assert ("fixture.md", "fine") in lines
    with pytest.raises(Refused, match="fixture.md"):
        guards.check_leaks(lines, guards.leak_patterns(home=Path("/home/devuser"), user="devuser"))
    guards.check_leaks(lines, guards.leak_patterns(home=Path("/home/other"), user="other"))


def test_pre_push_refuses_main_and_non_fast_forward(repo):
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "a")
    ahead = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    with pytest.raises(Refused, match="main"):
        guards.check_push_updates([f"refs/heads/main {ahead} refs/heads/main {base}"], repo)
    guards.check_push_updates([f"refs/heads/x {ahead} refs/heads/x {base}"], repo)
    guards.check_push_updates([f"refs/heads/x {ahead} refs/heads/x {guards.ZERO_SHA}"], repo)
    with pytest.raises(Refused, match="fast-forward"):
        guards.check_push_updates([f"refs/heads/x {base} refs/heads/x {ahead}"], repo)


def test_the_stop_check_skips_when_pm_ai_is_untouched_and_never_loops(repo, monkeypatch):
    ran: list[Path] = []
    monkeypatch.setattr(guards.subprocess, "run", _recording_run(ran))
    (repo / "pm_ai").mkdir()
    assert guards.claude_stop({"cwd": str(repo)}) == 0
    assert ran == []
    (repo / "pm_ai" / "new.py").write_text("x = 1\n")  # untracked, uncommitted
    assert guards.claude_stop({"cwd": str(repo), "stop_hook_active": True}) == 0
    assert ran == []


def test_the_stop_check_runs_on_an_uncommitted_pm_ai_change(repo, monkeypatch):
    (repo / "pm_ai").mkdir()
    (repo / "pm_ai" / "mod.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "mod")
    (repo / "pm_ai" / "mod.py").write_text("x = 2\n")  # modified, not staged
    ran: list[Path] = []
    monkeypatch.setattr(guards.subprocess, "run", _recording_run(ran, fail="mypy"))
    assert guards.claude_stop({"cwd": str(repo)}) == 2
    assert [c for c in ran] == ["lint-imports", "mypy"]


def _recording_run(ran: list, fail: str | None = None):
    real = subprocess.run

    def run(cmd, *args, **kwargs):
        if cmd[0] == "git":
            return real(cmd, *args, **kwargs)
        ran.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, 1 if cmd[-1] == fail else 0, "out", "")

    return run
