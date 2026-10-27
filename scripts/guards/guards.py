#!/usr/bin/env python3
"""The hard rules of .agents/WORKFLOW.md §4, as checks any executor runs.

Claude Code calls this from `.claude/settings.json` hooks; git calls it from
`scripts/git-hooks/` (`git config core.hooksPath scripts/git-hooks`), so the
rules hold for an executor that has no Claude hooks. Standard library only:
the hooks run under whatever `python3` is on PATH, not the project's 3.14.

    guards.py claude-pre      PreToolUse (Edit, Write, MultiEdit, NotebookEdit, Bash)
    guards.py claude-stop     Stop: lint-imports + mypy when pm_ai/ changed
    guards.py git-pre-commit  grant + leak check on the staged diff
    guards.py git-pre-push    no push to main, no non-fast-forward

A refusal exits 2 with the reason on stderr. A guard that blocks is reported
to the orchestrator, never routed around.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

GRANTS_ENV = "PM_AI_GRANTS"
DEFAULT_GRANTS = Path("~/.claude/orchestrator/pm-ai/grants.json")
PRIVATE_HOMES = (".pm-ai", ".manager-ai")
SPECS_DIR = ("_bmad-output", "specs")
ZERO_SHA = "0" * 40


class Refused(Exception):
    pass


# --- git helpers -------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _existing_dir(path: Path) -> Path:
    p = path if path.is_dir() else path.parent
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def repo_of(path: Path) -> tuple[Path, Path] | None:
    """(worktree root, shared git dir) for the repo holding `path`, or None."""
    d = _existing_dir(path)
    top = _git(d, "rev-parse", "--show-toplevel")
    common = _git(d, "rev-parse", "--git-common-dir")
    if top is None or common is None:
        return None
    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = d / common_path
    return Path(top).resolve(), common_path.resolve()


# --- the checks --------------------------------------------------------------


def grants_file() -> Path:
    return Path(os.environ.get(GRANTS_ENV) or DEFAULT_GRANTS).expanduser()


def check_grant(worktree: Path) -> None:
    """Edits in a pm-ai worktree need a grant keyed by its folder name."""
    key = worktree.name
    path = grants_file()
    try:
        grants = json.loads(path.read_text())
    except FileNotFoundError:
        raise Refused(f"no grants file at {path}; the orchestrator grants worktrees there") from None
    except (OSError, ValueError) as exc:
        raise Refused(f"grants file {path} is unreadable: {exc}") from None
    if not isinstance(grants, dict) or key not in grants:
        raise Refused(
            f"worktree '{key}' has no grant in {path}. Ask the orchestrator for one "
            f'(shape: {{"{key}": {{"slice": "<id>", "branch": "<branch>", "granted": "YYYY-MM-DD"}}}})'
        )


def check_derived(rel: Path, transcript: str | None) -> None:
    """SPEC.md, its companions and .memlog.md are written by their skills only."""
    if rel.name == ".memlog.md":
        raise Refused(f"{rel} is append-only through _bmad/scripts/memlog.py; never edit it by hand")
    parts = rel.parts
    in_spec_root = len(parts) == 4 and parts[:2] == SPECS_DIR and rel.suffix == ".md"
    if in_spec_root and not _skill_ran("bmad-spec", transcript):
        raise Refused(
            f"{rel} is derived from .memlog.md by bmad-spec; a hand-edit is overwritten on the next "
            "derive. Change the contract by running bmad-spec"
        )


def _skill_ran(skill: str, transcript: str | None) -> bool:
    if not transcript:
        return False
    try:
        text = Path(transcript).read_text(errors="replace")
    except OSError:
        return False
    return bool(
        re.search(rf'"skill"\s*:\s*"{re.escape(skill)}"', text)
        or f"<command-name>/{skill}</command-name>" in text
    )


def real_home_dirs(home: Path | None = None) -> list[Path]:
    base = (home or Path.home()).resolve()
    return [base / name for name in PRIVATE_HOMES]


def check_path_not_private_home(path: Path, home: Path | None = None) -> None:
    resolved = path.expanduser().resolve()
    for private in real_home_dirs(home):
        if resolved == private or private in resolved.parents:
            raise Refused(f"{private} is the PM's real pm-ai home; nothing writes there")


_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|&\n]")
_PM_AI_KEYCHAIN = re.compile(r"(?:^|\s)(?:\S*/)?pm-ai\s+(?:setup|key\s+enrol)\b")


def check_command(command: str, cwd: Path, home: Path | None = None) -> None:
    """Bash: keychain commands, the real home, skipped gates, pushes to main."""
    home_path = (home or Path.home()).resolve()
    read_refusal: str | None = None  # computed once, on the first segment naming the home
    segments = [s.strip() for s in _SEGMENT_SPLIT.split(_without_heredoc_bodies(command)) if s.strip()]
    if any(_PM_AI_KEYCHAIN.search(seg) for seg in segments):  # first, so a home refusal never masks it
        raise Refused("`pm-ai setup` and `pm-ai key enrol` write the login keychain; only Andrei runs them")
    for seg in segments:
        sets_home = re.search(r"(?:^|\s)HOME=", seg) is not None
        hit = _home_hit(seg, home_path, with_var=not sets_home)
        if hit:
            if read_refusal is None:
                read_refusal = _home_read_refusal(command, home_path) or ""
            if read_refusal:
                raise Refused(
                    f"{hit} is the PM's real pm-ai home; {read_refusal}. A command naming it is read-only "
                    "end to end (ls, cat, head, tail, wc, stat, file, du, grep, cut, find, and cp/rsync out to an "
                    "absolute path outside the home and outside any git checkout); for anything else use a "
                    "throwaway HOME"
                )
        if "--no-verify" in seg:
            raise Refused("--no-verify skips a gate; if a hook blocks, stop and report")
        try:
            tokens = shlex.split(seg)
        except ValueError:
            tokens = seg.split()
        _check_git(tokens, cwd)


_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")


def _without_heredoc_bodies(command: str) -> str:
    """Drop heredoc bodies: they are text on stdin (a PR body, a commit
    message that names ~/.pm-ai), not commands. A script fed to an
    interpreter this way is not checked; the guard is a floor, not a sandbox."""
    out: list[str] = []
    ends: list[str] = []
    for line in command.split("\n"):
        if ends:
            if line.strip() == ends[0]:
                ends.pop(0)
            continue
        out.append(line)
        ends.extend(m.group(2) for m in _HEREDOC.finditer(line))
    return "\n".join(out)


# --- reading the real home ----------------------------------------------------------
#
# Andrei's transcripts live in the real home; the orchestrator copies them out to
# a private folder for the reference sets. A command that names the home is
# read-only from end to end: every stage of every pipeline on every line is a
# read, and every copy lands outside the home and outside any git checkout (the
# repo is public). Anything the rule cannot read with certainty is refused.

_READERS = frozenset({"ls", "cat", "head", "tail", "wc", "stat", "file", "du", "grep", "cut", "find", "cp", "rsync"})
_LIST_OPS = frozenset({";", "&&", "||", "&"})
_FIND_WRITES = frozenset({"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"})
_CP_FLAGS = re.compile(r"-[aRrpPLHfinvX]+")
_RSYNC_FLAGS = re.compile(r"-[avrlptgoDhPnicuLH]+")
_RSYNC_LONG = frozenset(
    {"--archive", "--recursive", "--verbose", "--progress", "--dry-run", "--times", "--perms",
     "--human-readable", "--itemize-changes", "--checksum", "--update", "--links", "--copy-links"}
)  # fmt: skip
_UNSAFE_DEST = re.compile(r"[$`*?\[\]{}]")


def _home_hit(text: str, home_path: Path, with_var: bool = True) -> str | None:
    for name in PRIVATE_HOMES:
        forms = [f"{home_path}/{name}", f"~/{name}"]
        if with_var:
            forms += [f"$HOME/{name}", f"${{HOME}}/{name}"]
        hit = next((f for f in forms if re.search(re.escape(f) + r"(?![\w.-])", text)), None)
        if hit:
            return hit
    return None


def _is_op(token: str) -> bool:
    return bool(token) and all(c in "();<>|&" for c in token)


def _split_on(tokens: list[str], ops: frozenset[str]) -> list[list[str]]:
    parts: list[list[str]] = [[]]
    for t in tokens:
        if t in ops:
            parts.append([])
        else:
            parts[-1].append(t)
    return parts


def _home_read_refusal(command: str, home_path: Path) -> str | None:
    """Why `command` is not a plain read of the real home, or None when it is."""
    if re.search(r"<<|\$\(|`|<\(|>\(", command):
        return "heredocs and command or process substitution are not plain reads"
    if re.search(r"\bHOME=", command):
        return "the command changes HOME"
    saw_home = False
    for line in command.replace("\\\n", " ").split("\n"):
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            return "the command does not parse"
        for pipeline in _split_on(tokens, _LIST_OPS):
            if not pipeline:
                continue  # a trailing `;` or `&`
            for stage in _split_on(pipeline, frozenset({"|"})):
                saw_home = saw_home or any(_home_hit(t, home_path) for t in stage)
                reason = _stage_refusal(stage, home_path)
                if reason:
                    return reason
    return None if saw_home else "the guard cannot tell how the command uses it"


def _stage_refusal(tokens: list[str], home_path: Path) -> str | None:
    args: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.isdigit() and i + 1 < len(tokens) and tokens[i + 1][:1] in "<>":
            i += 1  # the fd number of a redirection, `2>`
            continue
        if _is_op(t):
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if not target or _is_op(target):
                return f"`{t}` is not a plain read"
            if t in (">", ">>", "&>", "&>>") and target != "/dev/null":
                return "it redirects output to a file"
            if t == ">&" and not target.isdigit():
                return "it redirects output to a file"
            if t not in ("<", ">", ">>", "&>", "&>>", ">&"):
                return f"`{t}` is not a plain read"
            i += 2
            continue
        args.append(t)
        i += 1
    if not args or "=" in args[0]:
        return "it sets a variable" if args else "it has no command"
    prog, rest = args[0], args[1:]
    if prog not in _READERS:
        return f"`{prog}` is not a read command"
    if prog == "find" and _FIND_WRITES.intersection(rest):
        return "find with -delete, -exec, -ok or -fprint is not a read"
    if prog == "file" and any(a == "-C" or a.startswith("--compile") for a in rest):
        return "`file -C` writes a file"
    if prog in ("cp", "rsync"):
        return _copy_refusal(prog, rest, home_path)
    return None


def _copy_refusal(prog: str, args: list[str], home_path: Path) -> str | None:
    operands: list[str] = []
    options_end = False
    for a in args:
        if options_end or not a.startswith("-") or a == "-":
            operands.append(a)
        elif a == "--":
            options_end = True
        elif not (_CP_FLAGS.fullmatch(a) if prog == "cp" else (_RSYNC_FLAGS.fullmatch(a) or a in _RSYNC_LONG)):
            return f"`{prog} {a}` is not on the read-only option list"
    if len(operands) < 2:
        return f"`{prog}` needs a source and a destination"
    if prog == "rsync" and any(":" in op for op in operands):
        return "rsync to or from a remote host is not a local copy"
    dest = operands[-1]
    if dest == "~" or dest.startswith("~/"):
        dest = str(home_path) + dest[1:]
    if _UNSAFE_DEST.search(dest):
        return "the copy destination has variables or patterns in it"
    if not dest.startswith("/"):
        return "the copy destination is not an absolute path"
    target = Path(dest).resolve()
    for private in real_home_dirs(home_path):
        if target == private or private in target.parents:
            return "the copy writes into the home"
    if repo_of(target) is not None:
        return "the copy destination is inside a git checkout, and the repo is public"
    return None


def _check_git(tokens: list[str], cwd: Path) -> None:
    while tokens and "=" in tokens[0] and not tokens[0].startswith("-"):
        tokens = tokens[1:]  # leading VAR=value assignments
    if len(tokens) < 2 or Path(tokens[0]).name != "git":
        return
    i = 1
    while i < len(tokens) and tokens[i] in ("-C", "-c"):
        if tokens[i] == "-C" and i + 1 < len(tokens):
            cwd = (cwd / tokens[i + 1]).resolve()
        i += 2
    if i >= len(tokens):
        return
    sub, args = tokens[i], tokens[i + 1 :]
    if sub == "commit" and any(re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", a) for a in args if not a.startswith("--")):
        raise Refused("`git commit -n` skips the hooks; if a hook blocks, stop and report")
    if sub != "push":
        return
    flags = [a for a in args if a.startswith("-")]
    if any(f in ("-f", "--force", "--mirror") or f.startswith("--force-with-lease") for f in flags) or any(
        re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", f) for f in flags
    ):
        raise Refused("force-push is not allowed")
    positional = [a for a in args if not a.startswith("-")]
    refspecs = positional[1:]
    if any(r.startswith("+") for r in refspecs):
        raise Refused("a '+' refspec is a force-push; not allowed")
    for r in refspecs:
        dest = r.split(":")[-1]
        if dest in ("main", "refs/heads/main") or (r == "HEAD" and _git(cwd, "rev-parse", "--abbrev-ref", "HEAD") == "main"):
            raise Refused("nobody pushes to main; open a PR and Andrei approves the merge")
    if not refspecs and _git(cwd, "rev-parse", "--abbrev-ref", "HEAD") == "main":
        raise Refused("this checkout is on main; nobody pushes to main")


def leak_patterns(home: Path | None = None, user: str | None = None) -> list[str]:
    home_s = str((home or Path.home()))
    user = user if user is not None else getpass.getuser()
    pats = [home_s]
    if len(user) >= 4:
        pats.append(user)
    return pats


def check_leaks(added_lines: list[tuple[str, str]], patterns: list[str]) -> None:
    """Refuse added lines naming this machine's home path or user name."""
    hits = []
    for file, line in added_lines:
        low = line.lower()
        for p in patterns:
            if p.lower() in low:
                hits.append(f"{file}: …{p}…")
                break
    if hits:
        shown = "\n  ".join(hits[:10])
        raise Refused(
            "the repo is public and these staged lines name this machine's home path or user name:\n  "
            + shown
            + ("\n  …" if len(hits) > 10 else "")
        )


def staged_added_lines(top: Path) -> list[tuple[str, str]]:
    diff = _git(top, "diff", "--cached", "--no-color", "--unified=0", "--diff-filter=ACMR", "--no-ext-diff", "--text") or ""
    out: list[tuple[str, str]] = []
    file = ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            file = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("+") and not line.startswith("+++"):
            out.append((file, line[1:]))
    return out


def check_push_updates(lines: list[str], top: Path) -> None:
    """git pre-push stdin: `<local ref> <local sha> <remote ref> <remote sha>`."""
    for raw in lines:
        parts = raw.split()
        if len(parts) != 4:
            continue
        _local_ref, local_sha, remote_ref, remote_sha = parts
        if remote_ref == "refs/heads/main":
            raise Refused("nobody pushes to main; open a PR and Andrei approves the merge")
        if local_sha == ZERO_SHA or remote_sha == ZERO_SHA:
            continue  # a deletion or a new branch
        if _git(top, "merge-base", "--is-ancestor", remote_sha, local_sha) is None:
            raise Refused(f"{remote_ref} would not fast-forward; force-push is not allowed")


# --- quick check at the end of a builder turn ---------------------------------


def _uv() -> str:
    return shutil.which("uv") or str(Path("~/.local/bin/uv").expanduser())


def quick_check(top: Path) -> str | None:
    """lint-imports + mypy when this worktree changed pm_ai/; the failure text, or None."""
    changed = []
    for args in (
        ("diff", "--name-only", "main...HEAD"),  # committed on this branch
        ("diff", "--name-only", "HEAD"),  # staged and unstaged
        ("ls-files", "--others", "--exclude-standard"),  # new, untracked
    ):
        changed += (_git(top, *args) or "").splitlines()
    if not any(c.startswith("pm_ai/") and c.endswith(".py") for c in changed):
        return None
    failures = []
    for cmd in (["lint-imports"], ["mypy"]):
        try:
            r = subprocess.run([_uv(), "run", *cmd], cwd=top, capture_output=True, text=True, timeout=240)
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"`uv run {cmd[0]}` did not run: {exc}")
            continue
        if r.returncode != 0:
            tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-15:])
            failures.append(f"`uv run {cmd[0]}` failed (exit {r.returncode}):\n{tail}")
    return "\n\n".join(failures) or None


# --- entry points --------------------------------------------------------------


def claude_pre(payload: dict) -> None:
    tool = payload.get("tool_name", "")
    tin = payload.get("tool_input") or {}
    cwd = Path(payload.get("cwd") or os.getcwd())
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or cwd)
    if tool == "Bash":
        check_command(str(tin.get("command", "")), cwd)
        return
    raw = tin.get("file_path") or tin.get("notebook_path")
    if not raw:
        return
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cwd / path
    check_path_not_private_home(path)
    here, ours = repo_of(path), repo_of(project)
    if here is None or ours is None or here[1] != ours[1]:
        return  # not a pm-ai worktree: other repos, scratch, memory, the ledger
    top = here[0]
    check_grant(top)
    check_derived(path.resolve().relative_to(top), payload.get("transcript_path"))


def claude_stop(payload: dict) -> int:
    if payload.get("stop_hook_active"):
        return 0  # already blocked once this turn; do not loop
    cwd = Path(payload.get("cwd") or os.getcwd())
    repo = repo_of(cwd)
    if repo is None or not (repo[0] / "pm_ai").is_dir():
        return 0
    failure = quick_check(repo[0])
    if failure:
        print(f"quick check failed in {repo[0].name}; fix before reporting READY:\n\n{failure}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else ""
    try:
        if mode == "claude-pre":
            claude_pre(json.load(sys.stdin))
        elif mode == "claude-stop":
            return claude_stop(json.load(sys.stdin))
        elif mode == "git-pre-commit":
            top = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel") or Path.cwd())
            check_grant(top)
            check_leaks(staged_added_lines(top), leak_patterns())
        elif mode == "git-pre-push":
            top = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel") or Path.cwd())
            check_push_updates(sys.stdin.read().splitlines(), top)
        else:
            print(__doc__, file=sys.stderr)
            return 64
    except Refused as exc:
        print(f"pm-ai guard: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
