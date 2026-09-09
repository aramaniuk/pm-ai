"""`strategic_goals.md`, interpreted — the register the alignment machinery never had.

`domain/goals.py` carries the whole of AD-41: `Goal`, the two closed enums, and
`resolve`/`alignment_tag`/`rank_key`, every one of which takes a
`register: dict[str, Goal]`. Nothing built that register. `strategic_goals.md`
is declared Tier-1 in exactly one tree — the personal one
(`scope_model.py:544`) — and is a member of `PERSONAL_SUBJECT_ARTIFACTS`
(`:1052`) precisely so that no committed scope may hold it (`:1044-1047`), but
it had no reader.

And the consequence was not a degraded tag. `resolve` returns `UNALIGNED` only
when a recommendation cites nothing (`goals.py:87-88`); a recommendation that
*does* cite a goal **raises `UnresolvedGoal`** against an empty register
(`:90-95`). So `alignment_tag` could not be called on real data at all. This
module is what makes it callable.

**It parses bytes and never opens a file.** Structural rather than a promise —
`parse_goals` takes `bytes | None` and there is nothing here to open. `core` is
I/O-free by contract and `StorageService.read_artifact` is already the single
reader (`storage/service.py:1065`), so the caller reads and this module
interprets. Same shape as `pm_ai.core.config`, for the same reason.

## The grammar

Markdown-native, decided 2026-09-03: **the domain comes from the section
heading, `[id]` and `(horizon)` are the only structured tokens, and the title is
free text.**

    ## Project

    - [g_payments_latency] (medium) Cut payment latency below 200ms

The `key=value` grammar `11a` uses for meeting records was rejected here. Four
lines per goal is right for a machine-written record and wrong for a file a
human revises, and D-8 already made `domain` the grouping axis, which maps onto
headings for free. This file's real failure mode is not a parse error, it is the
PM quietly stopping updating it.

## Edit-tolerant, not lenient about meaning

Extra whitespace, reordered sections, prose between goals, a title heading, a
`## Notes` section, cased or padded enum spellings, an editor's BOM: all fine.
Order in the file carries no meaning. What is not fine is a goal missing its id,
its horizon or its title, because every one of those is load-bearing — the id is
what a citation resolves to, and both enums are closed sets.

**Inside a domain section every list item is a goal.** That is the rule that
makes "an unreadable goal is surfaced, never dropped" mean something. The
natural implementation — parse what matches, ignore what does not — satisfies
every other rule while silently dropping a goal whose horizon has a typo in it,
and a dropped goal is a citation that cannot resolve and a dashboard section
that is quietly short. So a bullet under `## Project` that does not parse is a
refusal, not prose. Prose is paragraphs, and bullets that live outside the three
domain sections.

A fenced code block is skipped whole, which is what lets the file document its
own grammar in its own header without the worked example parsing as a goal.

## Refusing the file, not the goal

One bad goal refuses the whole file. A partial register is indistinguishable to
every consumer from a complete one, so a typo in the middle would quietly demote
every citation below it — exactly the drift CAP-11 exists to make visible.
Refusing loudly costs one fix and hides nothing.

`MalformedGoals` is that refusal, and it is **not** `UnresolvedGoal`
(`goals.py:57`). A goal that failed to parse and a goal that was never written
are different facts and this module does not blur them: nothing here raises or
widens `UnresolvedGoal`.

## What this module does not do

No ranking, no tagging, no recommendation handling — `goals.py` holds all of it
and is unchanged. No writer and no command (`22b`). No write path at all: this
file is authored by hand (AD-3) and pm-ai does not edit it. And no goal
invention — an absent file yields an absent register, a state the renderer
states rather than one the parser papers over.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pm_ai.domain.goals import Goal, GoalDomain, GoalHorizon
from pm_ai.domain.identity import DataScope, MalformedReference, SourceRef

__all__ = [
    "ARTIFACT",
    "DOMAIN_SPELLINGS",
    "GOAL_ID",
    "HORIZON_SPELLINGS",
    "GoalRegister",
    "MalformedGoals",
    "parse_goals",
]

ARTIFACT = "strategic_goals.md"

_BOM = b"\xef\xbb\xbf"

# The three headings a goal may sit under. Closed, and spelled from the enum so
# a fourth domain cannot be added to `GoalDomain` without appearing here.
DOMAIN_SPELLINGS: dict[str, GoalDomain] = {domain.value: domain for domain in GoalDomain}

# The horizon spellings accepted, closed the same way. The three synonyms are
# not invented here: `GoalHorizon`'s own docstring names operational, tactical
# and strategic against SHORT, MEDIUM and LONG, and UJ-9 uses that vocabulary,
# so a PM typing the word the PRD taught them is not making a mistake. Nothing
# beyond those six is accepted — `short-term` is a refusal that lists all six,
# which teaches the file's vocabulary at the moment it is needed.
HORIZON_SPELLINGS: dict[str, GoalHorizon] = {
    **{horizon.value: horizon for horizon in GoalHorizon},
    "operational": GoalHorizon.SHORT,
    "tactical": GoalHorizon.MEDIUM,
    "strategic": GoalHorizon.LONG,
}

# An id's charset, stated here rather than inferred from `SourceRef`.
# `SourceRef.parse` only checks that a scopeless ref has two colon-separated
# parts and a non-empty second (`identity.py:223-227`), so `goal:my id` parses
# happily — a citation with a space in it, unparseable by anything that splits
# on whitespace. This is the gate that actually rejects it.
GOAL_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")

# ATX headings only, at any level, with markdown's three-space indent allowance
# and its optional closing hashes. A `#` title and a `###` subsection both name
# a domain if their text is one — level carries no meaning, and requiring `##`
# would refuse a file whose author nested the sections one deeper.
_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(?P<text>.*?)\s*#*\s*$")

# A list item. The trailing whitespace is required, so a `---` rule and a setext
# underline are not bullets.
_BULLET = re.compile(r"^\s*[-*+]\s+(?P<content>.+)$")

_FENCE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})")

# The goal line itself, after the bullet. Fixed order: the two structured tokens
# lead and everything after them is the title.
_GOAL = re.compile(r"^\[(?P<goal_id>[^\]]*)\]\s*\((?P<horizon>[^)]*)\)\s*(?P<title>.*)$")

_SHAPE = "- [id] (horizon) Title"


class MalformedGoals(ValueError):
    """`strategic_goals.md` cannot be read as written.

    Deliberately distinct from `UnresolvedGoal` (`goals.py:57`), which means a
    citation names a goal the register does not hold. A goal that failed to
    parse and a goal that was never written are different facts, and this
    exception does not widen that one's meaning to cover them both.
    """


class GoalRegister(dict[str, Goal]):
    """The goals `strategic_goals.md` declares, keyed by `goal_id`.

    A `dict` subclass rather than a wrapper around one, because `resolve` and
    `alignment_tag` take `dict[str, Goal]` and this story does not change them
    (`goals.py:85,101`). A register that had to be unwrapped at every call site
    would be one more thing for a caller to get wrong, and getting it wrong
    raises `UnresolvedGoal` on a goal that is right there.

    `present` is the one thing a mapping cannot carry: whether the file existed.
    An absent file and a present file with no goals in it are both empty
    registers and need different sentences from a renderer — "author one" versus
    "no goals declared" — and telling a PM to write a file they have already
    written is how a dashboard loses their trust.

    Equality is `dict` equality and ignores `present` on purpose: a register is
    its goals, and a caller comparing two of them means their contents. Assert
    `present` directly when that is the question.
    """

    __slots__ = ("present",)

    present: bool

    def __init__(self, goals: dict[str, Goal] | None = None, *, present: bool) -> None:
        super().__init__(goals or {})
        self.present = present

    def __repr__(self) -> str:
        return f"GoalRegister({dict(self)!r}, present={self.present!r})"


def parse_goals(raw: bytes | None, *, scope: DataScope) -> GoalRegister:
    """Interpret `strategic_goals.md`'s bytes into a register.

    `None` is the caller reporting no file and yields an absent register; `b""`
    is a file someone created and has not filled in, and yields a present one.
    Both are empty and neither is an error.

    `scope` is required and comes from the caller, never from the file.
    `Goal.scope` is required for the same reason (`goals.py:49`): it is the
    field that decides whether a git-committed scope may hold this data, and a
    parser that defaulted it would be a parser taking a decision that is not
    its — the AD-38 hole the required field closed. Which scopes may hold this
    file is the scope model's question (`PERSONAL_SUBJECT_ARTIFACTS`), not this
    module's, so nothing here second-guesses what it is handed.

    Raises `MalformedGoals` — and nothing else — for anything it cannot read.
    """
    if raw is None:
        return GoalRegister(present=False)
    goals: dict[str, Goal] = {}
    lines: dict[str, int] = {}
    domain: GoalDomain | None = None
    fence: str | None = None
    for number, line in enumerate(_decode(raw).splitlines(), start=1):
        opener = _FENCE.match(line)
        if opener is not None:
            token = opener["fence"][0]
            fence = token if fence is None else (None if token == fence else fence)
            continue
        if fence is not None:
            continue
        heading = _HEADING.match(line)
        if heading is not None:
            # A heading that names no domain ends the section rather than
            # refusing: `# Strategic Goals` and `## Notes` are ordinary in a
            # hand-written file. What it costs is paid below, where a goal with
            # no domain in effect is refused instead of dropped.
            domain = DOMAIN_SPELLINGS.get(_fold(heading["text"]))
            continue
        bullet = _BULLET.match(line)
        if bullet is None:
            continue
        content = bullet["content"].strip()
        if domain is None:
            if not content.startswith("["):
                continue
            raise MalformedGoals(
                _at(
                    number,
                    line,
                    f"opens with an `[id]`, so it is a goal, but no "
                    f"{_headings()} heading is in effect above it. A goal's "
                    f"domain comes from the section it sits under, so a goal "
                    f"outside all three has none. Move it under one of them, or "
                    f"remove the `[...]` if the line is prose.",
                )
            )
        goal = _goal(content, domain=domain, scope=scope, number=number, line=line)
        first = lines.get(goal.goal_id)
        if first is not None:
            raise MalformedGoals(
                _at(
                    number,
                    line,
                    f"repeats the id {goal.goal_id!r}, already declared on line "
                    f"{first}. A citation resolves to exactly one goal, so a "
                    f"register that kept one of these would hand every "
                    f"`goal:{goal.goal_id}` an arbitrary answer. Rename one.",
                )
            )
        lines[goal.goal_id] = number
        goals[goal.goal_id] = goal
    return GoalRegister(goals, present=True)


def _goal(
    content: str, *, domain: GoalDomain, scope: DataScope, number: int, line: str
) -> Goal:
    """One list item inside a domain section, which is therefore a goal."""
    match = _GOAL.match(content)
    if match is None:
        raise MalformedGoals(
            _at(
                number,
                line,
                f"is a list item under the `{domain.value}` heading, so it is a "
                f"goal, and a goal is `{_SHAPE}` — the id in square brackets and "
                f"the horizon in round ones, both before the title. It is "
                f"refused rather than read as prose, because a goal this parser "
                f"skipped is a citation that stops resolving and a section that "
                f"is quietly short.",
            )
        )
    goal_id = match["goal_id"].strip()
    if not goal_id:
        raise MalformedGoals(
            _at(number, line, "has an empty `[]` — the id is what a citation resolves to.")
        )
    if GOAL_ID.match(goal_id) is None:
        raise MalformedGoals(
            _at(
                number,
                line,
                f"has the id {goal_id!r}, which is not citation-safe. A goal is "
                f"cited as `goal:<id>`, so an id may hold letters, digits, `_`, "
                f"`-` and `.` only, and must start with a letter, a digit or "
                f"`_`. A space would break any reader that splits a citation on "
                f"whitespace and a colon would break the reference grammar "
                f"itself (AD-34).",
            )
        )
    try:
        # The second gate the story asks for, ordered after the charset because
        # the charset is the one that actually rejects `goal:my id`. Reachable
        # only if `GOAL_ID` is ever widened — which is the point of keeping it.
        SourceRef.parse(f"goal:{goal_id}")
    except MalformedReference as exc:
        raise MalformedGoals(
            _at(number, line, f"has an id that is not a citable reference: {exc}")
        ) from exc
    spelling = match["horizon"].strip().casefold()
    horizon = HORIZON_SPELLINGS.get(spelling)
    if horizon is None:
        raise MalformedGoals(
            _at(
                number,
                line,
                f"has the horizon {spelling!r}. The set is closed: "
                f"{_listed(sorted(HORIZON_SPELLINGS))}. The horizon is when a goal "
                f"lands and is a separate axis from its domain, which the "
                f"heading above already gave.",
            )
        )
    title = match["title"].strip()
    if not title:
        raise MalformedGoals(
            _at(
                number,
                line,
                "has an id and a horizon but no title. `Goal.title` is required: "
                "an id is what a citation resolves to, and the title is the only "
                "part a PM reads on a dashboard.",
            )
        )
    return Goal(
        goal_id=goal_id, title=title, domain=domain, horizon=horizon, scope=scope
    )


def _decode(raw: bytes) -> str:
    """UTF-8, with an editor-added BOM tolerated.

    Refused distinctly from a grammar failure: a file saved in the wrong
    encoding is a different fix from a goal with a typo in it.
    """
    stripped = raw.startswith(_BOM)
    if stripped:
        raw = raw[len(_BOM) :]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        # Reported against the file, not the buffer this function sliced —
        # dropping the BOM shifts every position three bytes.
        offset = exc.start + (len(_BOM) if stripped else 0)
        raise MalformedGoals(
            f"{ARTIFACT} is not valid UTF-8 — byte {offset} is not part of a legal "
            f"sequence ({exc.reason}). This is an encoding problem, not a grammar "
            f"one: re-save the file as UTF-8."
        ) from exc


def _fold(text: str) -> str:
    """A heading's text, as typed, reduced to what it says.

    Emphasis and a trailing colon are decoration a hand-writer adds and a closed
    set should not turn into an unknown domain.
    """
    return text.strip().strip("*_:#").strip().casefold()


def _headings() -> str:
    return _listed(f"## {spelling.capitalize()}" for spelling in sorted(DOMAIN_SPELLINGS))


def _listed(spellings: Iterable[str]) -> str:
    names = [f"`{spelling}`" for spelling in spellings]
    return f"{', '.join(names[:-1])} or {names[-1]}"


def _at(number: int, line: str, message: str) -> str:
    return f"{ARTIFACT} line {number}, {line.strip()!r}: {message}"
