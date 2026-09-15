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
from pm_ai.domain.scope_model import (
    ENCRYPTION,
    GITIGNORED,
    File,
    Tier,
    _assert_declarations_agree,
)
from pm_ai.domain.storage_tiers import (
    _assert_code_keys_are_declared,
    requires_git_exclusion,
)
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
    (APPLICATION, "event_index.db", None, False),
    (APPLICATION, "commitment_index.db", None, False),
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
    # AD-47's staging area, in all three capture-holding scopes. This row is the
    # one that matters most in this file: `is_encrypted` fails closed, so an
    # UNDECLARED `temp/` would answer True, the staged bytes would be sealed, and
    # `os.link` would publish ciphertext under a name every reader downstream
    # treats as plaintext. Declaring it plaintext is what makes staging safe, and
    # these rows are what keep the declaration honest.
    (PROJECT, "temp/", "01J9Q4T.vtt", False),
    (PEOPLE, "temp/", "01J9Q4T.vtt", False),
    (PERSONAL, "temp/", "01J9Q4T.vtt", False),
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
    demonstrates the need — and the need did not go away with it.

    The pair being read moved on 2026-09-15 with story 1n, which made the
    project scope's `memory/` machine-local. `event_log/` used to split
    people-excluded against project-committed; both are excluded now, and the
    same one-name-two-answers problem holds against the **personal** scope,
    whose `memory/` is committed to the PM's own private repository. The
    asymmetry is what this test is for, not any particular pair, so it is read
    off wherever it currently lives — and `daily_dashboard.md` is added beside
    it because one surviving pair is one deletion away from this design going
    unjustified again.

    Asserted here rather than in the encryption matrix so a future re-tightening
    of encryption does not quietly become the only thing justifying the design.
    """
    assert requires_git_exclusion(ScopeKind.PEOPLE, "event_log/") is True
    assert requires_git_exclusion(ScopeKind.PROJECT, "event_log/") is True
    assert requires_git_exclusion(ScopeKind.PERSONAL, "event_log/") is False

    assert requires_git_exclusion(ScopeKind.PROJECT, "daily_dashboard.md") is True
    assert requires_git_exclusion(ScopeKind.PERSONAL, "daily_dashboard.md") is False


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
    """AD-6 — plaintext Markdown is a product property, with one carve-out.

    Since the 2026-08-23 narrowing no declared Markdown is encrypted anywhere —
    a report's records rest on the gitignored enclave and file permissions, not
    on the cipher. The team-member scope is still exempted from this rule's
    scan, deliberately: it is the one enclave whose contents *may* be sealed
    wholesale if that decision is ever taken, and stating the rule this way is
    what keeps a future encrypted `.md` in a project scope from looking normal
    without re-litigating the enclave.
    """
    offenders = [
        (kind.value, key)
        for kind, answers in ENCRYPTION.items()
        if kind is not ScopeKind.PEOPLE
        for key, encrypted in answers.items()
        if encrypted and key.endswith(".md")
    ]
    assert not offenders, f"Markdown encrypted outside the team-member scope: {offenders}"


# ── AD-47 staging (2026-08-28) ───────────────────────────────────────────────


@pytest.mark.parametrize("scope", [PROJECT, PEOPLE, PERSONAL])
def test_the_staging_area_is_excluded_from_version_control(scope):
    """AD-43 — `temp/` holds capture bytes, so it answers git the way captures do.

    The derived rule is a *directory* rule covering `transcripts/` and everything
    beneath it, so declaring this changes no rule text. It is declared anyway:
    a node answering "no" here would contradict the parent it lives inside, and
    the contradiction would be invisible until someone moved staging out of that
    parent.
    """
    assert requires_git_exclusion(scope.kind, "temp/"), (
        "AD-47 stages verbatim capture bytes here; a staging directory git would "
        "carry into a commit is the same leak as an unprotected transcripts/."
    )


@pytest.mark.parametrize("scope", [PROJECT, PEOPLE, PERSONAL])
def test_the_staging_area_sits_inside_the_capture_directory(paths, scope):
    """`os.link` cannot cross a filesystem, and neither can `os.replace`.

    Staging beside the captures rather than in a sibling tree is what keeps the
    publish atomic — and it is also why no second `.gitignore` rule is needed.
    """
    staging = paths.resolve(scope, "temp/")
    captures = paths.resolve(scope, "transcripts/")
    assert staging.parent == captures, (
        f"{staging} is not inside {captures}; a link across filesystems fails, "
        f"and the directory rule that excludes captures would stop covering it."
    )


# ── Which artifacts are excluded, per scope (story 1n, 2026-09-15) ───────────


def test_the_project_scope_excludes_all_of_memory():
    """Story 1n — the whole derived set, as a set.

    A spot-check on one member is what this deliberately is not: the point of
    the slice is a *derived* answer, and `event_log/` alone would pass while
    `commitments_log.md` stayed committed. So the set is written out, and both
    directions fail — a member lost and a member gained.

    `memory/` is here beside its four children rather than instead of them. The
    parent is what makes story 4k's generated `.gitignore` one rule, and git will
    not re-include a child of an excluded directory, so the rule has to name
    `memory/` itself; the children are declared because the write guard asks
    about the artifact it is about to write.

    A child answering "no" inside an excluded parent is refused outright by
    `_assert_declarations_agree`, added with this slice — it is an invariant
    rather than something this set happens to embody, because this set is a
    literal and a future developer re-shares a child by editing both in one
    commit. This row asserts the *content*; that guard asserts the *relation*.

    `rules/` and `skills/` are absent, deliberately: they are human-authored,
    hand-edited, nothing local is derived from them, and they are the whole of
    what a project still shares.
    """
    assert GITIGNORED[ScopeKind.PROJECT] == frozenset(
        {
            "memory/",
            "memory/commitments_log.md",
            "memory/daily_dashboard.md",
            "memory/event_log/",
            "memory/meetings/",
            "transcripts/",
            "transcripts/temp/",
        }
    )


def test_no_scope_but_the_project_excludes_anything_under_memory():
    """The half of "untouched" that is a property rather than a copy.

    `memory/`, `event_log/` and `meetings/` are spelled in three trees each, so
    the obvious wrong implementation of story 1n — reaching for the shared node,
    or setting the flag by basename — flips the **personal** scope's `memory/`
    along with the project's. That scope is the PM's own sovereign hub, committed
    to their own private repository on purpose, and nothing about 1n is an
    argument for excluding it.

    Stated as a relation over the derived answers rather than as a copy of them:
    the personal hub shares its `memory/`, the two scopes that hold other
    people's material do not, and which artifacts happen to sit under each is not
    this test's business. A flip that reached one tree too far fails here without
    anyone updating a literal.
    """
    under_memory = {
        kind: sorted(k for k in keys if k.startswith("memory/") or k == "memory/")
        for kind, keys in GITIGNORED.items()
    }
    assert under_memory[ScopeKind.PERSONAL] == [], (
        "the personal scope's memory/ is excluded. It is the PM's own hub, kept "
        "as a private repository on their own terms, and story 1n is about a "
        "*project's* memory being rewritten by a teammate's pull."
    )
    assert under_memory[ScopeKind.APPLICATION] == [], (
        "the application scope's memory/ is excluded, and nothing asked for that"
    )
    assert under_memory[ScopeKind.PROJECT] and under_memory[ScopeKind.PEOPLE], (
        "the two scopes whose memory/ must not be shared no longer exclude it"
    )


def test_the_other_three_scopes_exclusion_sets_are_pinned():
    """A pin on current content — deliberately NOT a proof that nothing changed.

    Said plainly, because the obvious name for this test is a lie: these three
    literals were read off the model *after* the flip, so a wrong value copied in
    would pass, and nothing here can tell "unchanged" from "changed and
    re-copied". Proving absence of change needs the previous values, which no
    runtime has.

    What it is good for is the next edit rather than this one. A later change that
    touches a shared node, or widens a helper, now has to come here and say so in
    the same diff — which is a review surface, not a proof. The property that IS
    proved lives in the test above, and the guards that make it structural live in
    `_assert_declarations_agree`.
    """
    assert GITIGNORED[ScopeKind.PERSONAL] == frozenset(
        {
            "private/",
            "private/personal_analytics.db",
            "private/telegram_cache/",
            "transcripts/",
            "transcripts/temp/",
        }
    )
    assert GITIGNORED[ScopeKind.PEOPLE] == frozenset(
        {
            "memory/",
            "memory/event_log/",
            "memory/meetings/",
            "transcripts/",
            "transcripts/temp/",
        }
    )
    assert GITIGNORED[ScopeKind.APPLICATION] == frozenset(
        {
            "connectors/",
            "disclosure.md",
            "private/",
            "private/commitment_index.db",
            "private/config.json",
            "private/event_index.db",
            "private/operational.db",
            "private/people/",
            "private/vector_index/",
        }
    )


def test_the_exclusion_guards_still_hold_after_the_flip():
    """`scope_model`'s and `storage_tiers`' own guards, re-run rather than assumed.

    Both run at import, so the suite already cannot start if either fails — which
    is exactly why they are called again here. "It imported" is not a sentence
    anyone reads as "every gitignored artifact names a node in the tree that
    declares it, no node inside an excluded parent claims to be shared, and the
    three ways an artifact is accounted for stayed pairwise disjoint". A flag flip
    is the change most likely to break the first two.

    Nothing is re-asserted inline afterwards. An earlier draft restated the
    stray-key and disjointness checks here in the test's own words, which is the
    same rule written twice in two places free to drift — and the copy would have
    been the one a reader trusted.
    """
    _assert_code_keys_are_declared()
    _assert_declarations_agree()
