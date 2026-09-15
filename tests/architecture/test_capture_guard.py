"""AD-23/AD-38 — a declared-excluded write is refused unless git already excludes it.

`transcripts/` sits inside a scope that is shared, so what keeps verbatim meeting
minutes out of the employer's repository is the state of that repository — not a
directory boundary, and not the text of a file.

Story 1n (2026-09-15) put the project scope's whole `memory/` under the same
guard: it is machine-local now, because a `git pull` rewrites Tier 1 underneath
the Tier-2 and Tier-3 state derived from it. So the rows below are about a
mechanism with several subjects rather than about captures, and the ones that
still say `transcripts/` do so because the capture directory is where the
consequence of getting it wrong is worst, not because it is the only subject.

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

import shutil
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

# Every row in this file either runs `git` or asserts what a row that ran `git`
# produced, which the module docstring above states as the point. Without the
# binary the calls raise `FileNotFoundError` from inside a fixture — a failure
# that reads as a broken guard rather than a missing tool. Skipping says which it
# is. Applied at module level rather than per row precisely because the answer is
# the same for all of them.
pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="this module asks real git about real repositories; no binary, no answer",
)


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


# ── Rows 9, 10, 11: which artifacts the guard applies to, and which not ──────
#
# The old header said "where the guard does not apply". Story 1n put refusals
# in this section — four project artifacts that used to be exempt now are not —
# so the section is about the *keying*, both directions of it, rather than about
# exemption.


def test_a_non_excluded_artifact_in_a_repository_is_unaffected(tmp_path):
    """Row 10 — the guard is keyed on the artifact, not on the scope.

    Re-derived 2026-09-15 with story 1n, which flipped the project scope's whole
    `memory/` to gitignored. This row used to read the project event log inside a
    repository with no rule and assert the write went through; that artifact is
    now guarded, and the row below asserts the refusal instead.

    The property itself is unchanged and still needs a subject, so it is read
    where the asymmetry now lives: the **personal** `memory/event_log/` is
    committed by declaration, inside a repository Deployment itself tells the PM
    to keep — and a guard keyed on the scope, or on "is there a repository here",
    would refuse it forever over a rule nobody asked for. The fake would refuse
    if it were consulted, so `asked == []` is the assertion that the artifact's
    own declaration is what ended the question.
    """
    vcs = FakeVcs(verdict=TrackingVerdict(ignored=False))  # would refuse if asked
    fixture = _fixture(tmp_path, gitignore="node_modules/\n", vcs=vcs)
    # A fake that claims a working tree and no exclusion — the worst answer the
    # guard could get. Real `git init` would be theatre here: the fake is what
    # the guard would consult, and the point is that it never does.
    vcs.root = _scope_repository_root(fixture, PERSONAL)

    fixture.storage.append_event_log(_entry("entry"), scope=PERSONAL)

    segment = fixture.paths.resolve(PERSONAL, EVENT_LOG) / f"{NOW:%Y-%m}.md"
    body = mask_ids(segment.read_text(encoding="utf-8"))
    assert body == (
        f"- [evt_ID] security actor=test ingested_at={NOW.isoformat()} protection=encryption-at-rest disabled_by=env-var detail=entry\n"
    )
    assert vcs.asked == [], "git was consulted about an artifact that has no rule"
    assert vcs.trees_asked == [], (
        "the working tree was asked about an artifact whose declaration already "
        "answered — the question costs a subprocess per artifact and the answer "
        "could not change the outcome"
    )


# ── Story 1n: `memory/` is machine-local, so the guard has four more subjects ─
#
# Every row below is parametrized over all four, through the writer each one is
# actually reached by in production, because they do NOT share a call site:
# `event_log/` enters at `_writable_dir` (`service.py:860`), `meetings/` and
# `daily_dashboard.md` at `write_artifact` (`:1123`), and `commitments_log.md`
# at the `assert_writable` pre-flight (`:1213`) — it is append-only, so
# `write_artifact` refuses it before the guard and no append path for it exists
# yet. Proving `event_log/` alone would have proven one of three call sites, and
# `write_artifact`'s was reached by no refusal row in this file at all: replacing
# its guard call with `pass` left the whole suite green.


def _write_event_log(fixture: Fixture) -> None:
    fixture.storage.append_event_log(_entry("entry"), scope=PROJECT)


def _write_meeting(fixture: Fixture) -> None:
    fixture.storage.write_artifact(
        b"# standup\n", scope=PROJECT, artifact="meetings/", name="mtg_01HX.md"
    )


def _write_dashboard(fixture: Fixture) -> None:
    fixture.storage.write_artifact(b"# today\n", scope=PROJECT, artifact="daily_dashboard.md")


def _preflight_commitments(fixture: Fixture) -> None:
    fixture.storage.assert_writable(scope=PROJECT, artifact="commitments_log.md")


MACHINE_LOCAL = [
    ("event_log/", _write_event_log),
    ("meetings/", _write_meeting),
    ("daily_dashboard.md", _write_dashboard),
    ("commitments_log.md", _preflight_commitments),
]


@pytest.mark.parametrize(
    ("artifact", "write"), MACHINE_LOCAL, ids=[a.strip("/") for a, _ in MACHINE_LOCAL]
)
def test_a_machine_local_project_artifact_is_guarded(tmp_path, artifact, write):
    """Story 1n — the four that stopped being the team's, refused without a rule.

    They were committed on purpose until 2026-09-15: Tier-1 truth the team reads.
    They are not, because a `git pull` rewrites them underneath the Tier-2 and
    Tier-3 state derived from them, and every mechanism that makes a segment
    trustworthy — single writer, one open segment, sealed immutability, arrival
    order, a per-machine dedup set — is false under a merge.

    Being gitignored is what makes the write a question for git, and these rows
    prove the flag reaches the write path rather than only the derived table.
    Driven against real git: the refusal has to come from the repository's state.

    Both halves of the message are pinned. The rule, because an operator given
    the capture rule for a dashboard is sent to protect the wrong directory — and
    the *artifact*, because the message said "holds raw captures" until this
    slice, which is prose a rule-only assertion cannot see being wrong.
    """
    fixture = _fixture(tmp_path, gitignore="node_modules/\n")
    target = fixture.paths.resolve(PROJECT, artifact)

    with pytest.raises(UnprotectedCaptureDir) as refusal:
        write(fixture)

    message = str(refusal.value)
    assert gitignore_rule_for(target, repository=fixture.repository) in message, (
        "the refusal must name the rule for THIS artifact — these are four "
        "different paths and the capture rule protects none of them"
    )
    assert artifact in message, (
        f"the refusal does not name {artifact!r}, so an operator cannot tell "
        f"which write refused"
    )
    assert "raw captures" not in message and "transcript" not in message, (
        f"the refusal calls {artifact!r} a capture: {message!r}. Three of these "
        f"four are ledgers, and repair advice that misnames the artifact is how "
        f"an operator edits the wrong rule."
    )
    assert not target.exists(), "the refusal created the path it refused to write"


@pytest.mark.parametrize(
    ("artifact", "write"), MACHINE_LOCAL, ids=[a.strip("/") for a, _ in MACHINE_LOCAL]
)
def test_one_rule_on_memory_protects_all_four(tmp_path, artifact, write):
    """The repair prescribed above has to work, and `memory/` is meant to be one rule.

    Story 1n put the exclusion on `memory/` itself as well as on each of its four
    children, so that story 4k can generate one rule instead of four — git will
    not re-include a child of an excluded directory, so the parent rule is both
    sufficient and the only shape that works. These rows are what make that a
    tested property rather than an intention: one line in `.gitignore`, and all
    four writes proceed.

    The literal `/.project-ai/memory/` below is **this slice's expectation of 4k,
    not a fact about it** — 4k is unbuilt, and nothing here can hold it to
    anything. What this row does guarantee is the half that is in this slice's
    hands: if the rule 4k eventually emits is this one, the writes it has to
    permit are permitted. Should 4k choose differently, this is the row that has
    to be re-derived with it rather than a claim that will silently go stale.
    """
    parent = gitignore_rule_for(Path("repo/.project-ai/memory"), repository=Path("repo"))
    assert parent == "/.project-ai/memory/", (
        "the rule this slice expects story 4k to generate is not the one being "
        "written here, so this row no longer tests what it claims"
    )
    fixture = _fixture(tmp_path, gitignore=f"{parent}\n")

    write(fixture)  # no refusal is the assertion

    if artifact != "commitments_log.md":  # the pre-flight writes nothing by design
        assert fixture.paths.resolve(PROJECT, artifact).exists()


@pytest.mark.parametrize(
    ("artifact", "write"), MACHINE_LOCAL, ids=[a.strip("/") for a, _ in MACHINE_LOCAL]
)
def test_a_project_outside_any_repository_still_writes(tmp_path, artifact, write):
    """The one direction story 1n must not break.

    `working_tree` returning `None` is an answer, not an unanswered question: a
    project directory in no repository has nothing that could commit these files,
    and refusing there would take the daemon offline for every non-git project —
    which is now most of what the guard covers, since `memory/` is where the
    daemon writes on every harvest.

    The premise is asserted first, because `tmp_path` nested inside a repository
    would have git answering about *that* one.
    """
    fixture = _fixture(tmp_path, gitignore=None, init=False)
    outside = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=fixture.repository, capture_output=True, text=True, check=False,
    )
    assert outside.returncode != 0, (
        f"premise changed: {fixture.repository} is inside a git repository "
        f"({outside.stdout.strip()}), so this row cannot test 'not a repository'"
    )

    write(fixture)  # no refusal is the assertion

    if artifact != "commitments_log.md":
        assert fixture.paths.resolve(PROJECT, artifact).exists()


@pytest.mark.parametrize("artifact", ["rules/", "skills/", "persona.md"])
def test_the_shared_project_artifacts_write_in_a_repository_without_a_rule(
    tmp_path, artifact
):
    """The promise story 1n makes and nothing else asserts.

    "`rules/` and `skills/` stay shared, deliberately" is the slice's central
    claim, and the shape of its failure is not a leak but a daemon that will not
    write the team's own files: if the flip had reached one node too far, or if
    the guard had ever been keyed on the scope rather than the artifact, a project
    repository with no `.gitignore` rule would refuse them forever, and adding a
    rule would be the wrong repair because nobody wants these excluded.

    The fake would refuse if it were consulted — it claims a working tree and no
    exclusion, the worst answer available — so `asked == []` is the assertion that
    the artifact's own declaration ended the question. Held through the public
    pre-flight, which is the one entry point every declared artifact has: three of
    these have no writer of their own (`skills/` holds the team's `.py` files and
    `rules/` is hand-edited), and a row that could only be written for the
    artifacts with writers would leave the promise half-covered.
    """
    vcs = FakeVcs(verdict=TrackingVerdict(ignored=False))
    fixture = _fixture(tmp_path, gitignore="node_modules/\n", vcs=vcs)
    vcs.root = fixture.repository

    fixture.storage.assert_writable(scope=PROJECT, artifact=artifact)

    assert vcs.asked == [], (
        f"git was consulted about {artifact!r}, which story 1n promises stays "
        f"shared — a committed artifact has no rule to look for"
    )
    assert vcs.trees_asked == [], (
        f"the working tree was asked about {artifact!r}, whose declaration "
        f"already answered"
    )


@pytest.mark.parametrize("scope", [PERSONAL, PEOPLE], ids=["personal", "people"])
def test_a_capture_outside_any_working_tree_is_unaffected(tmp_path, scope):
    """Rows 9 and 11 — no working tree, so nothing can commit it.

    Re-derived 2026-08-22. The outcome is unchanged and the *reason* is not. This
    read `is_git_committed` (since retired), so the write proceeded because the
    scope was not the project one; it now proceeds because git reports no working
    tree here. The
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
    committable, and the guard never even asked, because it gated on a
    scope-kind predicate true for PROJECT alone. (That predicate was
    `is_git_committed`, retired with story 1n on 2026-09-15 — named here in the
    past tense because it no longer exists to be read.)

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
