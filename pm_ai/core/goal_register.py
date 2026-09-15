"""`strategic_goals.md`, interpreted — the register the alignment machinery never had.

`domain/goals.py` carries the whole of AD-41: `Goal`, the two closed enums, and
`resolve`/`alignment_tag`/`rank_key`, every one of which takes a
`register: dict[str, Goal]`. Nothing built that register. `strategic_goals.md`
is declared Tier-1 in exactly one tree — the personal one
(`scope_model.py:544`) — and is a member of `PERSONAL_SUBJECT_ARTIFACTS`
(`:1052`) precisely so that no shared scope may hold it (`:1044-1047`), but
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

## What is a goal, what is never one, and what is refused

**Inside a domain section a list item is a goal.** That is the rule that makes
"an unreadable goal is surfaced, never dropped" mean something. The natural
implementation — parse what matches, ignore what does not — satisfies every
other rule while silently dropping a goal whose horizon has a typo in it, and a
dropped goal is a citation that cannot resolve and a dashboard section that is
quietly short. So a list item under `## Project` that does not parse is a
refusal, not prose. The marker is optional and may be ordered: `1. [g_a] ...`
is a goal, and so is a bare `[g_a] (short) A` on its own line, because losing
the `-` in an edit is a slip rather than a decision.

Four shapes are **never** goals, wherever they sit, because a hand-written
markdown file is full of them and refusing them would refuse the file:

* a task-list checkbox — `- [ ] ask Dana`, `- [x] done`;
* a bullet opening with a markdown link — `- [budget doc](http://x)`;
* a thematic break — `* * *`, `---`, `___`;
* a line indented past the goal above it that is not itself goal-shaped, which
  is that goal's own detail bullet; and anything indented four spaces or more,
  which markdown reads as code.

The first two are narrowed rather than blunt, because an exclusion that ate a
*malformed* goal would put the silent drop back one bullet shape at a time:
`- [g_a](short) A` is a goal written without the space, `- [g_a](quarterly) A`
is a goal with a bad horizon and is refused, and `- [x] (short) Ship it` is a
goal whose id happens to be `x`.

Outside every domain section the rule inverts: prose is the default and only a
**goal-shaped** line is refused. Goal-shaped means it opens with `[...]` or with
a parenthesised horizon spelling — enough that `- (medium) Cut latency` under
`## Marketing` is refused for having no domain instead of vanishing, while
`- ask Dana` under `## Notes` is read as what it looks like.

A fenced code block is skipped whole, which is what lets the file document its
own grammar in its own header without the worked example parsing as a goal. The
fence has to actually close: a closer matches its opener's character and is at
least as long as it, and a fence still open at end of file is a refusal naming
the line it opened on — an unterminated fence swallowing the second half of the
file is the largest silent drop this parser could commit.

Every closed vocabulary is case-folded and padding-tolerant: headings, horizon
spellings. **Goal ids are not**, deliberately — `[g_a]` and `[G_a]` are two
goals. An id is a citation key, and folding it would change what `goal:<id>`
resolves to.

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
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import NamedTuple

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
# a fourth domain cannot be added to `GoalDomain` without appearing here. Frozen
# with `MappingProxyType` because the tests call these sets closed and an
# exported mutable `dict` lets any importer widen one from the outside.
DOMAIN_SPELLINGS: Mapping[str, GoalDomain] = MappingProxyType(
    {domain.value: domain for domain in GoalDomain}
)

# The horizon spellings accepted, closed the same way. The three synonyms are
# not invented here: `GoalHorizon`'s own docstring names operational, tactical
# and strategic against SHORT, MEDIUM and LONG, and UJ-9 uses that vocabulary,
# so a PM typing the word the PRD taught them is not making a mistake. Nothing
# beyond those six is accepted — `short-term` is a refusal that lists all six,
# which teaches the file's vocabulary at the moment it is needed.
HORIZON_SPELLINGS: Mapping[str, GoalHorizon] = MappingProxyType(
    {
        **{horizon.value: horizon for horizon in GoalHorizon},
        "operational": GoalHorizon.SHORT,
        "tactical": GoalHorizon.MEDIUM,
        "strategic": GoalHorizon.LONG,
    }
)

# An id's charset, stated here rather than inferred from `SourceRef`.
# `SourceRef.parse` only checks that a scopeless ref has two colon-separated
# parts and a non-empty second (`identity.py:223-227`), so `goal:my id` parses
# happily — a citation with a space in it, unparseable by anything that splits
# on whitespace. This is the gate that actually rejects it. Case is *not*
# folded: an id is a citation key, not a closed vocabulary.
GOAL_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")

# ATX headings only, at any level, with markdown's three-space indent allowance
# and its optional closing hashes. A `#` title and a `###` subsection both name
# a domain if their text is one — level carries no meaning for *naming* one, and
# requiring `##` would refuse a file whose author nested the sections one
# deeper. Level does carry meaning for *ending* a section: see `parse_goals`.
_HEADING = re.compile(r"^ {0,3}(?P<hashes>#{1,6})\s+(?P<text>.*?)\s*#*\s*$")

# A list item — bulleted or ordered — within markdown's three-space indent
# allowance. Four or more spaces is a code block or a continuation, not an item.
_ITEM = re.compile(r"^(?P<indent> {0,3})(?:[-*+]|\d{1,9}[.)])[ \t]+(?P<content>.+)$")

# Any other non-blank line, which is a goal only if it is goal-shaped: a bullet
# marker lost in an edit must not lose the goal with it.
_BARE = re.compile(r"^(?P<indent> {0,3})(?P<content>\S.*)$")

# `---`, `* * *`, `___`. Three or more of one character, spaces between allowed,
# nothing else on the line. A rule is never a bullet, whatever `_ITEM` thinks.
_THEMATIC = re.compile(r"^ {0,3}(?P<char>[-*_])[ \t]*(?:(?P=char)[ \t]*){2,}$")

_FENCE_OPEN = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_FENCE_CLOSE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})[ \t]*$")

# A task-list checkbox. `[ ]` is never a goal — an empty id is not an id. `[x]`
# is a goal only if the rest of the line is one, so an id of `x` is not lost.
_CHECKBOX = re.compile(r"^\[(?P<mark>[ xX])\](?:[ \t]|$)")

# A markdown inline link at the head of the line. Distinguished from a goal by
# the missing space and by the target: `[g_a](short) A` is a goal, because
# `short` is a horizon, and `[budget doc](http://x)` is a link.
_LINK = re.compile(r"^\[[^\]]*\]\((?P<target>[^()]*)\)")

# The goal line itself, after the marker. Fixed order: the two structured tokens
# lead and everything after them is the title.
_GOAL = re.compile(r"^\[(?P<goal_id>[^\]]*)\]\s*\((?P<horizon>[^)]*)\)\s*(?P<title>.*)$")

# A line that names a horizon but no id. Goal-shaped enough to refuse rather
# than drop, which is what a goal whose `[id]` was deleted looks like.
_HORIZON_FIRST = re.compile(r"^\((?P<horizon>[^)]*)\)")

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


class _Fence(NamedTuple):
    """An open code fence, remembered well enough to close it and to name it."""

    char: str
    length: int
    number: int
    line: str


def parse_goals(raw: bytes | None, *, scope: DataScope) -> GoalRegister:
    """Interpret `strategic_goals.md`'s bytes into a register.

    `None` is the caller reporting no file and yields an absent register; `b""`
    is a file someone created and has not filled in, and yields a present one.
    Both are empty and neither is an error.

    `scope` is required and comes from the caller, never from the file.
    `Goal.scope` is required for the same reason (`goals.py:49`): it is the
    field that decides whether the project scope may hold this data, and a
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
    domain_level = 0
    goal_indent: int | None = None
    fence: _Fence | None = None
    for number, line in enumerate(_split(_decode(raw)), start=1):
        if fence is not None:
            closer = _FENCE_CLOSE.match(line)
            if closer is not None and _closes(closer["fence"], fence):
                fence = None
            continue
        opener = _FENCE_OPEN.match(line)
        if opener is not None and not (
            opener["fence"].startswith("`") and "`" in opener["info"]
        ):
            fence = _Fence(opener["fence"][0], len(opener["fence"]), number, line)
            continue
        heading = _HEADING.match(line)
        if heading is not None:
            # A heading that names a domain opens that section at its own level.
            # One that names none — `# Strategic Goals`, `## Notes` — ends the
            # section only if it is at or above that level; a `### Q3` *inside*
            # `## Project` is a subsection of it, not the end of it. What the
            # tolerance costs is paid below, where a goal with no domain in
            # effect is refused instead of dropped.
            named = DOMAIN_SPELLINGS.get(_fold(heading["text"]))
            if named is not None:
                domain, domain_level = named, len(heading["hashes"])
            elif len(heading["hashes"]) <= domain_level:
                domain = None
            goal_indent = None
            continue
        if _THEMATIC.match(line) is not None:
            continue
        item = _ITEM.match(line)
        listed = item is not None
        candidate = item if item is not None else _BARE.match(line)
        if candidate is None:
            continue
        content = candidate["content"].strip()
        if _never_a_goal(content):
            continue
        indent = len(candidate["indent"])
        # A list item inside a domain section is a goal whether or not it looks
        # like one — that is what "surfaced, never dropped" costs. Three kinds
        # of line are held to the stricter goal-shaped test instead, because
        # each is far more often prose: one with no list marker, one outside
        # every domain section, and one indented past the goal above it, which
        # is that goal's own detail bullet. A *goal-shaped* nested line is still
        # a goal, so nothing structured is dropped by the indent rule.
        nested = goal_indent is not None and indent > goal_indent
        if (not listed or domain is None or nested) and not _goal_shaped(content):
            continue
        if domain is None:
            raise MalformedGoals(
                _at(
                    number,
                    line,
                    f"is goal-shaped — it opens with an `[id]` or a "
                    f"`(horizon)` — but no {_headings()} heading is in effect "
                    f"above it. A goal's domain comes from the section it sits "
                    f"under, so a goal outside all three has none. Move it "
                    f"under one of them, or drop the leading token if the line "
                    f"is prose.",
                )
            )
        goal_indent = indent
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
    if fence is not None:
        raise MalformedGoals(
            _at(
                fence.number,
                fence.line,
                f"opens a code fence that is never closed, so everything below "
                f"it was skipped as a worked example — silently, which is the "
                f"one thing this parser must not do. A fence closes on a line "
                f"holding nothing but at least {fence.length} `{fence.char}` "
                f"characters; `{'~' if fence.char == '`' else '`'}` does not "
                f"close it and a shorter run does not either.",
            )
        )
    return GoalRegister(goals, present=True)


def _closes(run: str, fence: _Fence) -> bool:
    """A fence closes on its own character, at least as long as it opened."""
    return run[0] == fence.char and len(run) >= fence.length


def _never_a_goal(content: str) -> bool:
    """The two bullet shapes a markdown file is full of and a goal never is.

    Both are narrowed so that they exclude notes without swallowing goals — an
    exclusion that quietly ate a malformed goal would reintroduce, one bullet
    shape at a time, the silent drop this whole module is arranged against.
    """
    checkbox = _CHECKBOX.match(content)
    if checkbox is not None:
        # `[ ]` can never be a goal: an empty id is not an id. `[x]`/`[X]` is a
        # checkbox unless the rest of the line carries a `(...)` token, so a
        # goal whose id is `x` — even one with a broken horizon — still lands.
        if checkbox["mark"] == " " or _GOAL.match(content) is None:
            return True
    link = _LINK.match(content)
    if link is None:
        return False
    target = link["target"].strip()
    if _token(target) in HORIZON_SPELLINGS:
        # `[g_a](short) A` is a goal written without the space, not a link.
        return False
    label = content[1 : content.index("]")].strip()
    # A goal's id is citation-safe and a horizon is one bare word, so a line is
    # a link when either half says so: a label no id could be, or a target that
    # is a destination. `- [g_a](quarterly) A` is neither, and is refused as the
    # broken goal it is rather than skipped as prose.
    return GOAL_ID.match(label) is None or _destination(target)


def _destination(target: str) -> bool:
    """A link target, told from a mistyped horizon by what a URL or path holds."""
    return not target or any(character in target for character in ":/.#?") or " " in target


def _goal_shaped(content: str) -> bool:
    """Enough of a goal that dropping it would be dropping a goal.

    An `[id]` opener or a `(horizon)` opener. Not a full parse — the point is to
    tell `- [g_a] short A` and `- (medium) Cut latency`, both broken goals, from
    `- ask Dana`, which is a note.
    """
    if content.startswith("["):
        return True
    horizon = _HORIZON_FIRST.match(content)
    return horizon is not None and _token(horizon["horizon"]) in HORIZON_SPELLINGS


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
    spelling = match["horizon"].strip()
    horizon = HORIZON_SPELLINGS.get(_token(spelling))
    if horizon is None:
        raise MalformedGoals(
            _at(
                number,
                line,
                # Quoted as the PM typed it. Folding it first told them their
                # file said `'quarterly'` when what they wrote was `Quarterly`.
                f"has the horizon {spelling!r}. The set is closed: "
                f"{_horizons()}. The horizon is when a goal lands and is a "
                f"separate axis from its domain, which the heading above "
                f"already gave.",
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


def _split(text: str) -> list[str]:
    """Lines as an editor counts them, and as a refusal message names them.

    Not `str.splitlines`, which also breaks on `\\x0b`, `\\x0c`, `\\x85`,
    `\\u2028` and `\\u2029`. One of those pasted into a title would shift every
    line number below it in every refusal message this module writes, and the
    line number is the one thing those messages have to get right.
    """
    return [line.removesuffix("\r") for line in text.split("\n")]


def _fold(text: str) -> str:
    """A heading's text, as typed, reduced to what it says.

    Emphasis, code ticks and a trailing colon are decoration a hand-writer adds
    and a closed set should not turn into an unknown domain.
    """
    return _token(text.strip().strip("*_:#`"))


def _token(text: str) -> str:
    return text.strip().casefold()


def _headings() -> str:
    return _listed(f"## {spelling.capitalize()}" for spelling in sorted(DOMAIN_SPELLINGS))


def _horizons() -> str:
    """The six, grouped as they mean rather than sorted as they spell.

    An alphabetical run of six reads like six tiers. Three plus three synonyms
    reads like the three tiers `GoalHorizon` actually has.
    """
    canonical = _listed(horizon.value for horizon in GoalHorizon)
    synonyms = _listed(
        spelling
        for spelling, horizon in HORIZON_SPELLINGS.items()
        if spelling != horizon.value
    )
    return f"{canonical}, or the synonyms {synonyms}"


def _listed(spellings: Iterable[str]) -> str:
    """`a`, `b` or `c` — and still correct at two, one and none of them."""
    names = [f"`{spelling}`" for spelling in spellings]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} or {names[-1]}"


def _at(number: int, line: str, message: str) -> str:
    return f"{ARTIFACT} line {number}, {line.strip()!r}: {message}"
