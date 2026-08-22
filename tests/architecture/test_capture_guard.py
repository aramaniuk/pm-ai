"""AD-23/AD-38 — a raw capture is refused unless git already excludes it.

`transcripts/` is the one gitignored directory inside the one committed scope, so
what keeps verbatim meeting minutes out of the employer's repository is the state
of that repository — not a directory boundary, and not the text of a file.

**These tests run real `git` against real temporary repositories, and that is the
point.** The first implementation of this guard matched the required rule against
`.gitignore` text, and a text matcher answers the opposite of git in both
directions:

- `/.project-ai/transcripts/` followed by `!/.project-ai/transcripts/` — the rule
  is present, and git tracks the directory. The matcher allowed the write.
- `.project-ai/` alone excludes the whole enclave, naming no child. Git ignores
  the capture directory; the matcher refused a correctly protected repository.
- A directory committed before the rule was added stays tracked. No text can see
  the index at all.

A faked port would have re-encoded whatever this suite believed about git, which
is exactly the belief that was wrong. So the rows that assert *git's* behaviour
use `git init`, and a fake port appears only where the subject is storage's
reaction to a verdict it has already been given.

One test per row of story 1c's amended I/O matrix, then the refusals that
protect the path the verdict is about.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pm_ai.domain import (
    CAPTURES,
    EVENT_LOG,
    GITIGNORE_REQUIRED,
    DataScope,
    ScopeKind,
    TrackingVerdict,
    UnprotectedCaptureDir,
    VcsUnavailable,
)
from pm_ai.platform.paths import ScopePaths
from pm_ai.platform.vcs import GitVcs
from pm_ai.storage.service import (
    CAPTURE_NAME_LIMIT,
    CaptureAlreadyExists,
    EmptyCapture,
    MalformedCaptureName,
    StorageService,
)

NOW = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)

PROJECT = DataScope(ScopeKind.PROJECT, "alpha")
PERSONAL = DataScope(ScopeKind.PERSONAL)
PEOPLE = DataScope(ScopeKind.PEOPLE, person_id="alex")

# The rule as the scope model declares it, rather than as a literal: moving
# `transcripts/` changes the rule, and a test carrying its own copy of the string
# would keep passing while protecting a directory nothing writes to.
RULE = GITIGNORE_REQUIRED[CAPTURES]
ENCLAVE_RULE = ".project-ai/"

BODY = "09:01 alex: the migration slips a week\n"
NAME = "meet_7a1b.md"


# ── A real repository to ask ──────────────────────────────────────────────────


def _git(*arguments: str, cwd: Path) -> str:
    """Run git in `cwd` for setup, failing the test loudly if it cannot.

    Identity is passed with `-c` rather than written to a config file so the run
    cannot depend on, or disturb, the developer's own git configuration.
    """
    completed = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"test setup failed: git {' '.join(arguments)} in {cwd} exited "
        f"{completed.returncode}: {completed.stderr}"
    )
    return completed.stdout


@dataclass
class Fixture:
    """A writer over a rooted layout whose `alpha` repository is on disk."""

    storage: StorageService
    repository: Path

    @property
    def paths(self):
        return self.storage.paths

    def capture_dir(self, scope: DataScope) -> Path:
        """Where a capture would land, resolved without creating anything."""
        return self.paths.resolve(scope, CAPTURES)


def _fixture(
    tmp_path: Path,
    *,
    gitignore: str | None = None,
    init: bool = True,
    committed_capture: bool = False,
    vcs=None,
) -> Fixture:
    """A writer whose project repository is in the state a matrix row describes.

    `gitignore=None` means the repository has no `.gitignore` at all — a fresh
    clone, or a project whose excludes live only in a global file this daemon
    cannot see. That is a distinct row from a file that omits the rule, and it
    has to produce the same refusal.

    `committed_capture` commits a capture *before* the rule is written, which is
    the state no `.gitignore` can undo and no text check can detect.
    """
    paths = ScopePaths.rooted(tmp_path)
    repository = paths.repository("alpha")
    repository.mkdir(parents=True, exist_ok=True)
    if init:
        _git("init", "-q", "--initial-branch=main", ".", cwd=repository)
    if committed_capture:
        already = repository / ".project-ai" / "transcripts"
        already.mkdir(parents=True)
        (already / "committed_before_the_rule.md").write_text("old\n", encoding="utf-8")
        _git("add", "-A", cwd=repository)
        _git("commit", "-q", "--no-gpg-sign", "-m", "captures, regrettably", cwd=repository)
    if gitignore is not None:
        paths.gitignore("alpha").write_text(gitignore, encoding="utf-8")
    return Fixture(
        storage=StorageService(paths, now=lambda: NOW, vcs=vcs or GitVcs()),
        repository=repository,
    )


@dataclass
class FakeVcs:
    """A port that answers without asking git, for the rows about storage.

    Records what it was asked, so "git is not consulted at all" is assertable
    rather than merely plausible.
    """

    verdict: TrackingVerdict = TrackingVerdict(ignored=True)
    failure: str | None = None
    asked: list[tuple[Path, Path]] = field(default_factory=list)

    def tracking(self, path: Path, *, repository: Path) -> TrackingVerdict:
        self.asked.append((path, repository))
        if self.failure is not None:
            raise VcsUnavailable(self.failure)
        return self.verdict


def _snapshot(fixture: Fixture, scope: DataScope) -> tuple[bool, frozenset[str]]:
    """Whether the capture directory exists, and what is in it, before the call."""
    directory = fixture.capture_dir(scope)
    if not directory.exists():
        return False, frozenset()
    return True, frozenset(child.name for child in directory.iterdir())


def _assert_nothing_written(
    fixture: Fixture, scope: DataScope, before: tuple[bool, frozenset[str]]
) -> None:
    """The refusal changed nothing — and did not create the directory either.

    The "did not create" half is why this takes a snapshot rather than asserting
    an empty directory. A guard reordered to resolve with `create=True` first and
    check second leaves a freshly made, empty directory behind, so "empty" was
    true either way and the whole suite stayed green through the reordering.

    The contents half matters for the already-tracked row, where the directory
    legitimately holds a capture committed before the rule existed. "Empty" is the
    wrong assertion there; "unchanged" is the right one everywhere.
    """
    existed, contents = before
    directory = fixture.capture_dir(scope)
    if not existed:
        assert not directory.exists(), (
            f"the refusal created {directory}, so the check ran after the "
            f"resolver rather than before it"
        )
        return
    assert frozenset(child.name for child in directory.iterdir()) == contents, (
        f"the refusal changed the contents of {directory} — a capture git can see"
    )


# ── Rows 1 and 4: git excludes the directory, however the rule is spelled ─────


def test_a_capture_is_written_when_the_rule_is_present(tmp_path):
    """Row 1 — the ordinary case, and the one that proves the guard is not a wall."""
    fixture = _fixture(tmp_path, gitignore=f"node_modules/\n{RULE}\n")

    written = fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert written == fixture.capture_dir(PROJECT) / NAME
    assert written.read_text(encoding="utf-8") == BODY


def test_the_rule_is_accepted_without_its_leading_slash(tmp_path):
    """Row 4 — git anchors a rule containing a slash either way, so this excludes.

    Refusing it would refuse a repository that is in fact protected. Asked of git
    rather than of a matcher that was written to accept both forms: the question
    is what git does, not what the matcher intends.
    """
    fixture = _fixture(tmp_path, gitignore=f"{RULE.strip('/')}\n")

    written = fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert written.read_text(encoding="utf-8") == BODY


def test_excluding_the_whole_enclave_protects_the_capture_directory(tmp_path):
    """Row 6 — a parent-directory exclude protects a child it never names.

    `.project-ai/` is a perfectly ordinary thing for a team to write, and it
    excludes strictly more than the required rule does. The text matcher looked
    for its own rule and refused this repository, which is a daemon that will not
    write a capture in a repository that could not possibly leak one.
    """
    fixture = _fixture(tmp_path, gitignore=f"{ENCLAVE_RULE}\n")

    written = fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert written.read_text(encoding="utf-8") == BODY
    assert not GitVcs().tracking(fixture.capture_dir(PROJECT), repository=fixture.repository).tracked


# ── Rows 2, 3, 5, 7, 8: every way git would commit it ─────────────────────────


def test_a_gitignore_without_the_rule_refuses_the_capture(tmp_path):
    """Row 2 — a real `.gitignore`, real rules, and not this one."""
    fixture = _fixture(tmp_path, gitignore="node_modules/\n*.pyc\n.venv/\n")
    before = _snapshot(fixture, PROJECT)

    with pytest.raises(UnprotectedCaptureDir) as refusal:
        fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert RULE in str(refusal.value), "the refusal must name the rule to add"
    assert str(fixture.paths.gitignore("alpha")) in str(refusal.value), (
        "the refusal must name the file the rule belongs in"
    )
    _assert_nothing_written(fixture, PROJECT, before)


def test_a_repository_with_no_gitignore_refuses_the_capture(tmp_path):
    """Row 3 — fail closed. A missing file is a missing rule, not a pass."""
    fixture = _fixture(tmp_path, gitignore=None)
    assert not fixture.paths.gitignore("alpha").exists()
    before = _snapshot(fixture, PROJECT)

    with pytest.raises(UnprotectedCaptureDir):
        fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    _assert_nothing_written(fixture, PROJECT, before)


def test_a_negation_line_after_the_rule_refuses_the_capture(tmp_path):
    """Row 5 — the rule is present and git tracks the directory anyway.

    This is the case that made the text matcher unsafe rather than merely
    imprecise: it found its rule, reported "protected", and the next write put a
    verbatim transcript in a directory `git status` lists as untracked and
    `git add -A` commits. Verified against real git, not against a belief about
    negation lines.
    """
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n!{RULE}\n")
    before = _snapshot(fixture, PROJECT)

    with pytest.raises(UnprotectedCaptureDir):
        fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    _assert_nothing_written(fixture, PROJECT, before)


def test_an_already_tracked_capture_directory_refuses_the_capture(tmp_path):
    """Row 7 — a rule does not untrack what is already in the index.

    The repository here is what a team has after committing captures once and
    then adding the rule: the rule is correct, the file is correct, and git still
    carries every capture in that directory into the next commit. The refusal has
    to say `git rm --cached`, because "add the rule" is advice about a file that
    is already right.
    """
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n", committed_capture=True)
    before = _snapshot(fixture, PROJECT)
    assert before == (True, frozenset({"committed_before_the_rule.md"})), (
        "the setup did not commit a capture, so this row tests nothing"
    )

    with pytest.raises(UnprotectedCaptureDir) as refusal:
        fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert "rm" in str(refusal.value) and "cached" in str(refusal.value), (
        "the refusal must name the repair, which is not adding a rule"
    )
    assert "committed_before_the_rule.md" in str(refusal.value)
    _assert_nothing_written(fixture, PROJECT, before)


def test_a_project_root_that_is_not_a_repository_refuses_the_capture(tmp_path):
    """Row 8 — git could not be consulted, so the answer is no.

    The premise is asserted first: were `tmp_path` inside a git repository, git
    would answer about *that* repository and this test would prove nothing.
    """
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n", init=False)
    outside = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=fixture.repository, capture_output=True, text=True, check=False,
    )
    assert outside.returncode != 0, (
        f"premise changed: {fixture.repository} is inside a git repository "
        f"({outside.stdout.strip()}), so this row cannot test 'not a repository'"
    )
    before = _snapshot(fixture, PROJECT)

    with pytest.raises(UnprotectedCaptureDir) as refusal:
        fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert "could not be consulted" in str(refusal.value)
    _assert_nothing_written(fixture, PROJECT, before)


def test_a_repository_that_has_been_moved_away_says_so(tmp_path):
    """A stale registry is not a missing rule, and must not be reported as one.

    `pm-ai project add` writes a path; the directory can be renamed afterwards.
    Telling the operator to add a `.gitignore` rule sends them to a repository
    that is not there.
    """
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n")
    for child in sorted(fixture.repository.rglob("*"), reverse=True):
        child.unlink() if child.is_file() else child.rmdir()
    fixture.repository.rmdir()

    with pytest.raises(UnprotectedCaptureDir) as refusal:
        fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert "not a directory on disk" in str(refusal.value)
    assert RULE not in str(refusal.value), (
        "a repository that is not on disk was reported as a missing rule"
    )


# ── Rows 9, 10, 11: where the guard does not apply ────────────────────────────


def test_a_non_capture_artifact_in_the_same_scope_is_unaffected(tmp_path):
    """Row 10 — the guard is keyed on the artifact, not on the scope.

    `memory/event_log/` is committed *on purpose*: it is Tier-1 truth the team
    reads. A guard that refused every project-scope write whenever the capture
    rule was missing would take the daemon offline over a directory nobody asked
    to exclude.
    """
    vcs = FakeVcs(verdict=TrackingVerdict(ignored=False))  # would refuse if asked
    fixture = _fixture(tmp_path, gitignore="node_modules/\n", vcs=vcs)

    fixture.storage.append_event_log("- [test] entry", scope=PROJECT)

    segment = fixture.paths.resolve(PROJECT, EVENT_LOG) / f"{NOW:%Y-%m}.md"
    assert segment.read_text(encoding="utf-8") == "- [test] entry\n"
    assert vcs.asked == [], "git was consulted about an artifact that has no rule"


@pytest.mark.parametrize("scope", [PERSONAL, PEOPLE], ids=["personal", "people"])
def test_a_capture_in_an_uncommitted_scope_is_unaffected(tmp_path, scope):
    """Rows 9 and 11 — no repository, so there is nothing to ask and no rule.

    `transcripts/` is homed in every scope at the same relative path, so the
    artifact key alone cannot decide this: `is_git_committed` does. Keying on the
    key alone would refuse every personal and team-member capture forever, since
    no `.gitignore` anywhere excludes `~/.manager-ai/transcripts/` — and it would
    run git on every 1:1 recording, which is the row the amended matrix adds.
    """
    vcs = FakeVcs(failure="git must not be consulted for an uncommitted scope")
    fixture = _fixture(tmp_path, gitignore=None, init=False, vcs=vcs)

    written = fixture.storage.write_capture(BODY, scope=scope, name=NAME)

    assert written.read_text(encoding="utf-8") == BODY
    assert vcs.asked == [], "git was consulted about a scope with no repository"


# ── Storage's own reaction to a verdict it was handed ─────────────────────────


def test_any_unanswered_question_is_a_refusal(tmp_path):
    """Unknown is not permission — whatever the adapter could not do.

    Driven through a fake because the *cause* is what varies and the reaction must
    not: a missing binary, a timeout, a path outside the repository. Row 8 proves
    the real adapter raises this for a real repository-shaped failure; this proves
    storage refuses on it regardless of which failure it was.
    """
    vcs = FakeVcs(failure="no `git` on PATH")
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n", init=False, vcs=vcs)

    with pytest.raises(UnprotectedCaptureDir) as refusal:
        fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert "no `git` on PATH" in str(refusal.value), "the cause must survive the refusal"
    assert vcs.asked, "the fake was never consulted, so this proves nothing"


def test_the_directory_git_is_asked_about_is_the_one_written_to(tmp_path):
    """The verdict has to be about the capture directory, inside its repository.

    A guard that asked about the repository root, or about a sibling, would be
    perfectly green and would answer a question about somewhere else.
    """
    vcs = FakeVcs(verdict=TrackingVerdict(ignored=True))
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n", vcs=vcs)

    written = fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert vcs.asked == [(fixture.capture_dir(PROJECT), fixture.repository)]
    assert written.parent == fixture.capture_dir(PROJECT)
    assert written.is_relative_to(fixture.repository)


def test_a_tracked_directory_is_refused_even_when_the_rules_exclude_it(tmp_path):
    """The two halves of the verdict are independent, and either one refuses.

    Stated against the verdict directly as well as through git (row 7), because
    a caller that read only `ignored` would pass every real-git test that also
    reports `ignored=False` for a tracked path — and git reports both.
    """
    vcs = FakeVcs(verdict=TrackingVerdict(ignored=True, tracked=("t/old.md",)))
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n", vcs=vcs)

    with pytest.raises(UnprotectedCaptureDir) as refusal:
        fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert "t/old.md" in str(refusal.value)


# ── The guard is only as good as the path and the payload it guards ───────────


@pytest.mark.parametrize(
    "bad",
    [
        "../memory/leak.md",
        "sub/leak.md",
        "/absolute.md",
        "back\\slash.md",
        "new\nline.md",
        "tab\there.md",
        "nul\0byte.md",
        "..",
        ".",
        ".hidden.md",
        "",
        "   ",
        " padded.md",
        "padded.md ",
        "x" * (CAPTURE_NAME_LIMIT + 1),
    ],
)
def test_a_capture_name_must_be_one_reportable_component(tmp_path, bad):
    """A name is one path component, or the checked directory is not the one written.

    `../memory/leak.md` passes the git check — `transcripts/` really is excluded
    here — and then writes into a directory git tracks. The guard is satisfied and
    the leak happens anyway, which is why this is a refusal and not a nicety.

    Parametrized rather than looped: a loop reports only its first failure, so a
    validator that lost four of these branches would look like one defect.
    """
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n")

    with pytest.raises(MalformedCaptureName):
        fixture.storage.write_capture(BODY, scope=PROJECT, name=bad)

    assert not fixture.capture_dir(PROJECT).exists(), (
        "a name that was never valid still created the capture directory"
    )
    assert not (fixture.paths.resolve(PROJECT, "memory/") / "leak.md").exists()


def test_a_name_at_the_limit_is_still_accepted(tmp_path):
    """The bound must not have been bought by refusing ordinary names."""
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n")
    name = "m" * (CAPTURE_NAME_LIMIT - len(".md")) + ".md"

    written = fixture.storage.write_capture(BODY, scope=PROJECT, name=name)

    assert len(name.encode("utf-8")) == CAPTURE_NAME_LIMIT
    assert written.read_text(encoding="utf-8") == BODY


@pytest.mark.parametrize("empty", ["", "   ", "\n\n", "\t"], ids=["none", "spaces", "newlines", "tab"])
def test_an_empty_capture_is_refused_before_it_spends_the_name(tmp_path, empty):
    """A zero-length transcript reads downstream as a meeting nobody spoke in.

    And it takes the name, so the retry carrying the real content is refused as a
    duplicate — a failure that surfaces nowhere near its cause.
    """
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n")

    with pytest.raises(EmptyCapture):
        fixture.storage.write_capture(empty, scope=PROJECT, name=NAME)

    assert not (fixture.capture_dir(PROJECT) / NAME).exists()


def test_a_second_capture_under_one_name_is_refused_rather_than_merged(tmp_path):
    """Verbatim input is never amended: two recordings are two files.

    Appending would splice them into one transcript that reads as a single
    meeting; truncating would destroy the first. Both are silent, and a capture is
    the evidence a meeting summary was derived from.
    """
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n")
    fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    with pytest.raises(CaptureAlreadyExists) as refusal:
        fixture.storage.write_capture("a different meeting\n", scope=PROJECT, name=NAME)

    assert NAME in str(refusal.value)
    assert isinstance(refusal.value, FileExistsError), "the builtin stays catchable"
    assert (fixture.capture_dir(PROJECT) / NAME).read_text(encoding="utf-8") == BODY


def test_a_write_that_fails_does_not_leave_the_name_taken(tmp_path):
    """Exclusive creation claims the name before the content is written.

    A failure in between would otherwise leave a zero-length file owning that
    name for good, and every retry — including the one carrying the transcript —
    refused as a duplicate. An unencodable surrogate is a real mid-write failure
    rather than a mocked one.
    """
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n")

    with pytest.raises(UnicodeEncodeError):
        fixture.storage.write_capture("lone surrogate: \ud800\n", scope=PROJECT, name=NAME)

    assert not (fixture.capture_dir(PROJECT) / NAME).exists(), (
        "a partial write left the name permanently claimed"
    )
    retried = fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)
    assert retried.read_text(encoding="utf-8") == BODY
