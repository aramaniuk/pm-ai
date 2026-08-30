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

from ledger_fixtures import entry as _entry, mask_ids
from pm_ai.domain import (
    CAPTURES,
    EVENT_LOG,
    GITIGNORED,
    gitignore_rule_for,
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

from pm_ai.storage.crypto import AesGcmCrypto

# A real cipher with a fixed key: these tests never touch an encrypted
# artifact, and passing `PlaintextCrypto` would wire them as though the
# debug flag were on — a difference that would matter the day one does.
TEST_CIPHER = AesGcmCrypto(b"0" * 32)

NOW = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)

PROJECT = DataScope(ScopeKind.PROJECT, "alpha")
PERSONAL = DataScope(ScopeKind.PERSONAL)
PEOPLE = DataScope(ScopeKind.PEOPLE, person_id="alex")

# Derived exactly as the write path derives it, rather than carried as a
# literal: moving `transcripts/` changes the rule, and a test with its own copy
# of the string would keep passing while protecting a directory nothing writes
# to. `GITIGNORED` holds no rule text at all — it names, per scope, which
# artifacts need the guard, and the rule depends on the working tree.
RULE = gitignore_rule_for(
    Path("repo/.project-ai/transcripts"), repository=Path("repo")
)
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
        storage=StorageService(
            paths, now=lambda: NOW, vcs=vcs or GitVcs(), crypto=TEST_CIPHER
        ),
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
    root: Path | None = None
    marker: Path | None = None
    tracking_failure: str | None = None
    asked: list[tuple[Path, Path]] = field(default_factory=list)
    trees_asked: list[Path] = field(default_factory=list)

    def working_tree(self, path: Path) -> Path | None:
        """`root` is the answer to give; `None` means "no working tree here".

        Recorded separately from `asked`, because the two questions now fail
        differently: not being in a repository permits the write, while being
        unable to ask refuses it, and a test that conflated them would pass
        either way.
        """
        self.trees_asked.append(path)
        if self.failure is not None:
            raise VcsUnavailable(self.failure)
        return self.root

    def repository_marker_above(self, path: Path) -> Path | None:
        """`marker` is what the filesystem would have found: a `.git`, or nothing.

        Separate from `root` because the two are asked in different situations —
        `root` when git answered, `marker` only when it could not — and because
        the pair "git unavailable, repository present" is the one combination
        that refuses.
        """
        return self.marker

    def tracking(self, path: Path, *, repository: Path) -> TrackingVerdict:
        self.asked.append((path, repository))
        if self.tracking_failure is not None:
            raise VcsUnavailable(self.tracking_failure)
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


def test_a_project_root_that_is_not_a_repository_permits_the_capture(tmp_path):
    """Row 8, re-derived 2026-08-22 — no repository means nothing can commit it.

    This asserted a refusal until the guard stopped keying on scope. The old
    reasoning read "not a repository" as an inability to consult git, and
    therefore as unknown. It is not unknown: git answered, and the answer was
    that this path is in no working tree. Nothing can carry the capture into a
    commit, so refusing would only stop pm-ai recording a meeting.

    The trade is stated rather than hidden: a project whose checkout was deleted
    now accepts captures instead of complaining. That is a broken configuration,
    not a leak, and `pm-ai doctor` is where a broken configuration belongs.

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

    written = fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert written.read_text(encoding="utf-8") == BODY


def test_a_repository_that_has_been_moved_away_permits_the_capture(tmp_path):
    """A stale registry is not a missing rule, and must not be refused as one.

    `pm-ai project add` writes a path; the directory can be renamed afterwards.
    With the repository gone, `working_tree` anchors on the nearest existing
    ancestor, finds no working tree, and answers `None` — which is an answer:
    nothing can commit this capture, so the write proceeds. (The previous name
    promised the write "says so"; it does not — `GitVcs._git`'s stale-registry
    refusal lives on the `tracking` path, which a vanished repository never
    reaches, and no message is emitted on this one. What is pinned here is the
    verdict, deliberately: recording the meeting beats blocking on a rename.)
    """
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n")
    for child in sorted(fixture.repository.rglob("*"), reverse=True):
        child.unlink() if child.is_file() else child.rmdir()
    fixture.repository.rmdir()

    written = fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert written.read_text(encoding="utf-8") == BODY, (
        "a registry pointing at a directory that no longer exists is a "
        "configuration fault, not a leak: with the repository gone there is "
        "nothing that could commit the capture"
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

    fixture.storage.append_event_log(_entry("entry"), scope=PROJECT)

    segment = fixture.paths.resolve(PROJECT, EVENT_LOG) / f"{NOW:%Y-%m}.md"
    body = mask_ids(segment.read_text(encoding="utf-8"))
    assert body == (
        f"- [evt_ID] security actor=test ingested_at={NOW.isoformat()} detail=entry\n"
    )
    assert vcs.asked == [], "git was consulted about an artifact that has no rule"


@pytest.mark.parametrize("scope", [PERSONAL, PEOPLE], ids=["personal", "people"])
def test_a_capture_outside_any_working_tree_is_unaffected(tmp_path, scope):
    """Rows 9 and 11 — no working tree, so nothing can commit it.

    Re-derived 2026-08-22. The outcome is unchanged and the *reason* is not. This
    read `is_git_committed`, so the write proceeded because the scope was not the
    project one; it now proceeds because git reports no working tree here. The
    old docstring argued that keying on the artifact name alone would refuse
    every personal capture forever, since no `.gitignore` excludes
    `~/.manager-ai/transcripts/` — true, and not an argument against keying on
    the working tree, which is the option it did not consider.
    """
    vcs = FakeVcs(root=None)
    fixture = _fixture(tmp_path, gitignore=None, init=False, vcs=vcs)

    written = fixture.storage.write_capture(BODY, scope=scope, name=NAME)

    assert written.read_text(encoding="utf-8") == BODY
    assert vcs.trees_asked, "the working-tree question was never asked"
    assert vcs.asked == [], "tracking was consulted for a path in no repository"


@pytest.mark.parametrize("scope", [PERSONAL, PEOPLE], ids=["personal", "people"])
def test_a_capture_inside_a_private_repository_is_guarded(tmp_path, scope):
    """The leak this story closes, driven against real `git init`.

    Deployment tells the PM to keep the sovereign personal scope as a private git
    repository with `private/` gitignored — and `transcripts/` sits at that
    scope's *root*, outside `private/`. So a verbatim coaching transcript was
    committable, and the guard never even asked, because `is_git_committed` is
    true for PROJECT alone.

    No row in this file covered it: every personal and team-member case was built
    with `init=False`, so the repository-backed case was not wrong here, it was
    absent.
    """
    fixture = _fixture(tmp_path, gitignore=None, init=False)
    capture = fixture.capture_dir(scope)
    root = _scope_repository_root(fixture, scope)
    _git("init", "-q", "--initial-branch=main", ".", cwd=root)
    before = _snapshot(fixture, scope)

    with pytest.raises(UnprotectedCaptureDir) as refusal:
        fixture.storage.write_capture(BODY, scope=scope, name=NAME)

    expected = gitignore_rule_for(capture, repository=root)
    assert expected in str(refusal.value), (
        f"the refusal must name the rule for THIS repository ({expected}), not "
        f"the project one — an operator sent to edit {RULE} in {root} is being "
        f"sent to a file that is already correct"
    )
    assert str(root / ".gitignore") in str(refusal.value)
    _assert_nothing_written(fixture, scope, before)


@pytest.mark.parametrize("scope", [PERSONAL, PEOPLE], ids=["personal", "people"])
def test_a_private_repository_that_excludes_its_captures_permits_them(tmp_path, scope):
    """The repair the row above prescribes has to actually work.

    A guard that refuses whatever the operator does is worse than no guard: they
    add the rule it named, nothing changes, and they learn to ignore it.
    """
    fixture = _fixture(tmp_path, gitignore=None, init=False)
    capture = fixture.capture_dir(scope)
    root = _scope_repository_root(fixture, scope)
    _git("init", "-q", "--initial-branch=main", ".", cwd=root)
    rule = gitignore_rule_for(capture, repository=root)
    (root / ".gitignore").write_text(f"{rule}\n", encoding="utf-8")

    written = fixture.storage.write_capture(BODY, scope=scope, name=NAME)

    assert written.read_text(encoding="utf-8") == BODY


def _scope_repository_root(fixture: Fixture, scope: DataScope) -> Path:
    """The directory to `git init` so that this scope's captures sit inside it.

    Derived from the resolver rather than assembled from the scope's known
    layout: the personal scope holds captures at its root while the team-member
    scope holds them under a person's directory, and a test that hardcoded either
    would stop testing the guard the day the resolver moved one.
    """
    root = fixture.capture_dir(scope).parent
    root.mkdir(parents=True, exist_ok=True)
    return root


# ── Storage's own reaction to a verdict it was handed ─────────────────────────


def test_an_unanswerable_question_refuses_only_when_a_repository_exists(tmp_path):
    """git is optional; a repository pm-ai cannot interrogate is not.

    Re-derived 2026-08-22. This asserted that any unanswered question refuses.
    That made git a hard requirement of recording a meeting, which it is not: on
    a machine with no git, or in a directory that is no checkout, nothing exists
    that could commit a capture.

    What survives is the narrow case that genuinely leaks. "pm-ai cannot find
    git" is not the fact "no repository exists" — the daemon runs under `launchd`
    with a minimal PATH, so it can miss a `git` the developer's shell uses every
    day, and the capture would land in a genuinely tracked directory. Answering
    "am I inside a repository" needs no binary, so the refusal narrows to
    repository-present-and-unaskable.
    """
    marker = tmp_path / "elsewhere" / ".git"
    marker.mkdir(parents=True)
    vcs = FakeVcs(failure="no `git` on PATH", marker=marker)
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n", init=False, vcs=vcs)
    before = _snapshot(fixture, PROJECT)

    with pytest.raises(UnprotectedCaptureDir) as refusal:
        fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert "no `git` on PATH" in str(refusal.value), "the cause must survive the refusal"
    assert str(marker) in str(refusal.value), (
        "the refusal must name the repository it found, or the operator cannot "
        "tell this from a missing rule"
    )
    _assert_nothing_written(fixture, PROJECT, before)


def test_an_unanswerable_question_with_no_repository_permits_the_capture(tmp_path):
    """The other half, and the reason the case above had to narrow.

    No git and no repository: there is nothing that could ever commit this, so
    refusing would stop pm-ai doing its job to protect against a risk that does
    not exist. Asserted separately from the row above because the two differ by
    one fact, and a single test could satisfy either reading.
    """
    vcs = FakeVcs(failure="no `git` on PATH", marker=None)
    fixture = _fixture(tmp_path, gitignore=None, init=False, vcs=vcs)

    written = fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert written.read_text(encoding="utf-8") == BODY
    assert vcs.asked == [], "tracking was consulted after git had already failed"


def test_the_directory_git_is_asked_about_is_the_one_written_to(tmp_path):
    """The verdict has to be about the capture directory, inside its repository.

    A guard that asked about the repository root, or about a sibling, would be
    perfectly green and would answer a question about somewhere else.
    """
    vcs = FakeVcs(verdict=TrackingVerdict(ignored=True))
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n", vcs=vcs)
    # The fake now answers both questions, and the working-tree root is what
    # `tracking` is asked *from* — so it has to be the repository, or this test
    # would assert against a root the guard invented.
    vcs.root = fixture.repository

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
    vcs.root = fixture.repository

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


def test_a_working_tree_found_but_unaskable_still_refuses(tmp_path):
    """The second unanswered-question branch, which the first cannot reach.

    Two things can fail, and they fail in sequence. `working_tree` answers first;
    if *that* is what breaks, the fallback asks the filesystem whether a `.git`
    exists at all and the refusal names it. But git can answer the working-tree
    question and then fail the exclusion query — a timeout on a network
    filesystem, an exit code this adapter does not recognise — and at that point
    a repository is known to exist, so there is nothing left to fall back on.

    Covered here because it was not. Before story 1j the fake's single failure
    flag landed on this branch; 1j made `working_tree` the first call, so the same
    flag now stops one step earlier and this refusal went bare. A coverage sweep
    on 2026-08-24 found it — the branch had a test, and re-deriving that test
    moved the coverage without moving the assertion.
    """
    vcs = FakeVcs(tracking_failure="`git check-ignore` timed out after 10s")
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n", vcs=vcs)
    vcs.root = fixture.repository
    before = _snapshot(fixture, PROJECT)

    with pytest.raises(UnprotectedCaptureDir) as refusal:
        fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert "timed out" in str(refusal.value), "the cause must survive the refusal"
    assert vcs.asked, "tracking was never reached, so this proves nothing"
    _assert_nothing_written(fixture, PROJECT, before)


def test_a_refused_capture_is_asked_again_on_the_next_attempt(tmp_path):
    """The memo must not cache a refusal.

    `_git_checked` exists so the guard costs one subprocess per artifact per
    daemon rather than one per write. Recording a *failed* check would mean an
    operator who fixes nothing sees the second write succeed — the worst possible
    reading of a security guard.
    """
    vcs = FakeVcs(tracking_failure="`git check-ignore` timed out after 10s")
    fixture = _fixture(tmp_path, gitignore=f"{RULE}\n", vcs=vcs)
    vcs.root = fixture.repository

    for _ in range(2):
        with pytest.raises(UnprotectedCaptureDir):
            fixture.storage.write_capture(BODY, scope=PROJECT, name=NAME)

    assert len(vcs.asked) == 2, "the refusal was cached and the second write let through"


# ── The marker walk itself, on the real adapter ───────────────────────────────
# `repository_marker_above` decides the one fallback that can leak a transcript:
# with git unreachable, `None` permits the write and a marker refuses it. Every
# test of that fallback injects a fake and supplies the marker answer itself, so
# until 2026-08-28 the real walk was never executed by any test — `exists()`
# could regress to `is_dir()`, or the walk could skip `path` itself, with the
# whole capture-guard suite green.


def test_the_marker_walk_finds_a_git_directory_above(tmp_path):
    (tmp_path / ".git").mkdir()
    below = tmp_path / "a" / "b"
    assert GitVcs().repository_marker_above(below) == tmp_path / ".git"


def test_the_marker_walk_finds_a_git_file_too(tmp_path):
    """A worktree or submodule spells `.git` as a *file* — the reason the walk
    uses `exists()` rather than `is_dir()`, and the natural tidy-up regression."""
    (tmp_path / ".git").write_text("gitdir: /somewhere/else\n", encoding="utf-8")
    assert GitVcs().repository_marker_above(tmp_path / "deep") == tmp_path / ".git"


def test_the_marker_walk_includes_the_path_itself(tmp_path):
    inside = tmp_path / "repo"
    (inside / ".git").mkdir(parents=True)
    assert GitVcs().repository_marker_above(inside) == inside / ".git"


def test_an_unmarked_tree_has_no_marker(tmp_path):
    assert GitVcs().repository_marker_above(tmp_path / "plain" / "dir") is None
