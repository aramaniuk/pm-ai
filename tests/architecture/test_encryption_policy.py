"""Which artifacts are encrypted at rest, asked of real resolved paths (AD-6).

Encryption is declared on the node beside the tier, and `is_encrypted` answers
from those declarations. Three properties are worth separating:

- the **answers** themselves, one per artifact the storage contract names;
- **fail-closed** on anything no tree declares, which is what makes a forgotten
  declaration a grep-able file rather than a leak;
- **per-scope** resolution, which is the reason this axis could not join the
  basename-keyed tier tables.

Every path comes from `resolve(scope, artifact)`. A test that hardcoded
`~/.pm-ai/private/people/p1/dossier.md` would assert against this file's belief
about the layout rather than the layout, and would keep passing after the
resolver moved the artifact.
"""

from __future__ import annotations

import pytest

from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.scope_model import ENCRYPTION, File, Tier
from pm_ai.domain.storage_tiers import requires_git_exclusion
from pm_ai.platform.paths import ScopePaths
from pm_ai.storage.crypto import is_encrypted, scope_of

APPLICATION = DataScope(ScopeKind.APPLICATION)
PERSONAL = DataScope(ScopeKind.PERSONAL)
PEOPLE = DataScope(ScopeKind.PEOPLE, person_id="p1")
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")


@pytest.fixture
def paths(tmp_path):
    return ScopePaths.rooted(tmp_path)


# (scope, artifact, filename inside it if it is a directory, expected answer)
MATRIX = [
    (PERSONAL, "coaching_1on1_history.md", None, False),
    (PERSONAL, "strategic_goals.md", None, False),
    (PROJECT, "commitments_log.md", None, False),
    (APPLICATION, "vector_index/", "index.bin", False),
    (APPLICATION, "derived.db", None, False),
    (APPLICATION, "operational.db", None, False),
    (APPLICATION, "config.json", None, True),
    # Configuration, not credentials: the token it is enrolled with goes to
    # config.json, which is encrypted. The split was always in the layout.
    (APPLICATION, "connectors/", "jira.toml", False),
    (PERSONAL, "telegram_cache/", "state.json", True),
    # Dropped 2026-08-23. Also SQLite and also Tier 2, and left encrypted it
    # would have been the only reason `sqlcipher3` stayed a dependency — a
    # source build on the one platform v1 targets, for one file.
    (PERSONAL, "personal_analytics.db", None, False),
    # Captures are plaintext in all three scopes as of 2026-08-22. What keeps a
    # verbatim transcript out of a repository is the git guard, not a cipher.
    (PROJECT, "transcripts/", "2026-08-18.vtt", False),
    (PEOPLE, "transcripts/", "2026-08-18.vtt", False),
    (PERSONAL, "transcripts/", "2026-08-18.vtt", False),
    (PEOPLE, "meetings/", "1on1.md", False),
    (PROJECT, "meetings/", "standup.md", False),
    (PEOPLE, "event_log/", "2026-08.md", False),
]


@pytest.mark.parametrize(
    ("scope", "artifact", "inside", "expected"),
    MATRIX,
    ids=[f"{s.kind.value}-{a.strip('/')}" for s, a, _, _ in MATRIX],
)
def test_each_declared_artifact_answers_as_the_contract_says(
    paths, scope, artifact, inside, expected
):
    resolved = paths.resolve(scope, artifact)
    target = resolved / inside if inside else resolved
    assert is_encrypted(str(target)) is expected


def test_two_artifacts_sharing_a_parent_answer_differently(paths):
    """The reason classification cannot read a path prefix.

    Both sit directly under `private/`. A rule keyed on that directory gets one
    of them wrong, whichever way it is written.
    """
    enclave = paths.resolve(APPLICATION, "config.json").parent
    assert enclave == paths.resolve(APPLICATION, "vector_index/").parent
    assert is_encrypted(str(paths.resolve(APPLICATION, "config.json"))) is True
    assert is_encrypted(str(paths.resolve(APPLICATION, "vector_index/"))) is False


def test_one_basename_answers_differently_in_two_scopes(paths):
    """Per-scope keying, now carried by git-exclusion rather than by encryption.

    `meetings/` motivated the move: it was encrypted under `people/` and
    plaintext in a project, which a basename-keyed table cannot express. The
    2026-08-22 loosening made both plaintext, so encryption no longer
    demonstrates the need — and the need did not go away with it. `event_log/`
    is excluded from version control inside the team-member enclave and
    committed in a project, so the same one-name-two-answers problem holds on
    the axis that is still asymmetric. Asserted here rather than in the
    encryption matrix so a future re-tightening does not quietly become the only
    thing justifying the design.
    """
    assert requires_git_exclusion(ScopeKind.PEOPLE, "event_log/") is True
    assert requires_git_exclusion(ScopeKind.PROJECT, "event_log/") is False


def test_captures_agree_across_every_scope_that_holds_them():
    """One answer, reached by three declarations agreeing rather than by one slot.

    Per-scope keying makes disagreement *possible*, so agreement has to be
    asserted rather than assumed the way a global value would have guaranteed it.
    The answer itself is plaintext as of 2026-08-22; what this holds is that the
    three cannot drift apart, whichever way it is later set.
    """
    holders = [k for k, answers in ENCRYPTION.items() if "transcripts/" in answers]
    assert {k.value for k in holders} == {"personal", "people", "project"}
    assert len({ENCRYPTION[k]["transcripts/"] for k in holders}) == 1


@pytest.mark.parametrize(
    "undeclared",
    ["event_telemetry.db", "chat_history/2026-08-18.vtt", "something_nobody_declared.db"],
)
def test_an_undeclared_path_fails_closed(paths, undeclared):
    """A forgotten declaration becomes an unreadable file, never a leaked one.

    `event_telemetry.db` and `chat_history/` are former spellings an older test
    still asserts. Answering from the trees alone would report both plaintext,
    which is the wrong direction; failing closed satisfies the old assertions and
    the current names together, without reviving either name in a tree.
    """
    enclave = paths.resolve(APPLICATION, "config.json").parent
    assert is_encrypted(str(enclave / undeclared)) is True


def test_a_path_in_no_scope_at_all_fails_closed():
    assert is_encrypted("/etc/passwd") is True
    assert scope_of("/etc/passwd") is None


def test_the_team_member_scope_is_recognised_inside_the_application_scope(paths):
    """PEOPLE nests inside APPLICATION, so marker order decides the answer.

    Checking the outer marker first would file every report's record under the
    scope documented as holding no personal records — and that scope answers
    plaintext for `meetings/`.
    """
    assert scope_of(str(paths.resolve(PEOPLE, "meetings/"))) is ScopeKind.PEOPLE
    assert scope_of(str(paths.resolve(APPLICATION, "config.toml"))) is ScopeKind.APPLICATION


def test_a_node_without_an_encryption_answer_cannot_be_constructed():
    """The same bar the required tier sets: forgetting is impossible, not caught.

    A late assert would let the artifact exist unanswered until something read
    it. A required field means there is nowhere to add an artifact that does not
    ask.
    """
    with pytest.raises(TypeError, match="encrypted"):
        File("invented.db", Tier.OPERATIONAL)  # type: ignore[call-arg]

    with pytest.raises(TypeError, match="gitignored"):
        File("invented.db", Tier.OPERATIONAL, encrypted=True)  # type: ignore[call-arg]


def test_no_declared_markdown_is_encrypted_outside_the_team_member_enclave():
    """AD-6 — plaintext Markdown is a product property, with one exception.

    A report's records are encrypted wholesale, and `people/` holds Markdown. So
    the rule is not "no .md is ever encrypted" but "none is, outside the enclave
    whose entire contents are" — and stating it that way is what keeps a future
    encrypted `.md` in a project scope from looking normal.
    """
    offenders = [
        (kind.value, key)
        for kind, answers in ENCRYPTION.items()
        if kind is not ScopeKind.PEOPLE
        for key, encrypted in answers.items()
        if encrypted and key.endswith(".md")
    ]
    assert not offenders, f"Markdown encrypted outside the team-member scope: {offenders}"
