"""Pre-parsing sanitization (AD-12, AD-29) — the fold, the matcher, the type, the guard.

Non-destructive by construction: `Sanitized` holds both the untouched raw and
the derived copy, so a caller cannot accidentally overwrite the evidence a
citation resolves against.

**Here rather than in `pm_ai.core` (story 8e).** `ModelPort` has to name this
type — that is what makes an unsanitized prompt a construction error rather than
a review catch — and `.importlinter`'s `ports-depend-only-on-domain` lets
`pm_ai.ports` import nothing but `pm_ai.domain`. The pattern had to travel with
the type rather than stay behind, because `__post_init__` reads it and
`domain-imports-nothing` forbids the reverse edge. `pm_ai.core.sanitize`
re-exports both, so the module path every existing caller uses still resolves.

**Matching moved off the raw bytes (story 8g).** Until then every pattern
covered exactly one spelling of a phrase, so hyphens between the words, a
zero-width space inside one, full-width characters, dots as separators or a soft
hyphen each walked through untouched. Widening the word list cannot close that:
the obfuscation axis is infinite and the vocabulary is not. The matcher now runs
against a *normalised projection* of the text — `_fold`, seven ordered steps —
so one pattern covers its whole family, and the carriers that normalising would
erase are detected before it runs. Redaction still happens against the raw
spans: AD-29 is unchanged and `for_model` is the original text with the matched
spans replaced, never the folded projection, which destroys the casing and
punctuation legitimate text needs to stay readable to a model.
"""

from __future__ import annotations

import hashlib
import re
import typing
import unicodedata
from dataclasses import dataclass

__all__ = [
    "REDACTION",
    "RULE_CHANGELOG",
    "RULE_VERSION",
    "FoldDisagreement",
    "ForgedSanitization",
    "RuleChange",
    "Sanitized",
    "sanitize",
]

# ─────────────────────────────────────────────────────────────────────────────
# The vocabulary. Two patterns, because the two projections mean different
# things — and no new phrases: story 8g's leverage is the fold, not the words.
# ─────────────────────────────────────────────────────────────────────────────

_INJECTION = re.compile(
    r"(ignore\s+(all\s+)?previous\s+instructions?"
    r"|disregard\s+(the\s+)?above"
    r"|system\s*:\s*you\s+are"
    r"|<\s*/?\s*(system|instructions?)\s*>)",
    re.IGNORECASE,
)
"""The literal matcher, run against the carrier-stripped text (steps 1-2 only).

Kept whole and unchanged for two reasons.

**Two of the four families can only be matched here.** `<system>` and
`</instructions>` are defined by their brackets and `system: you are` by its
colon, and step 7 collapses every non-alphanumeric run to a space — so in folded
space those alternatives would have to match a bare `system`, a bare
`instructions`, or the bare words `system you are`. Each is ordinary prose: "the
instructions parser rejects the system prompt template", "the system you are
running is out of date". A rule that reads prose as an attack is worse than the
miss it closes, so both families are matched where their punctuation still
exists.

**The other two alternatives are redundant here and stay anyway**, so that
widening is monotone: every string the matcher refused before story 8g it still
refuses, which is what keeps `8e`'s `__post_init__` from retroactively rejecting
a value for a reason unrelated to the fold.

It runs against the *stripped* text rather than the raw bytes because carrier
removal can only help a literal match — no carrier is `\\s` and none appears in
a literal — so `<sys<ZWSP>tem>` matches here and its span covers the carrier.
Matching the raw bytes instead would let carrier removal alone produce a string
that matches, which is a value `sanitize()` could not construct.
"""

_FOLDED_INJECTION = re.compile(
    r"ignore(?: all)? previous instructions?"
    r"|disregard(?: the)? above"
)
"""The two phrase families whose folded spelling is still theirs alone.

**`system: you are` is not among them, and the colon is why.** Folded, it reads
`system you are`, which is the opening of ordinary prose: "The system you are
running is out of date" and "In the system you are describing, the cursor
advances per connector" were both redacted while that alternative was here, and
neither matched before this slice. The literal pattern required a colon between
the two words and the fold erases it, so the family goes where its separator
still exists — the same treatment `_INJECTION` gives the delimiter family, and
for the same reason. Dropping the colon was a widening of the vocabulary, which
the frozen `Never` forbids.

The cost is named rather than hidden: a full-width `Ｓｙｓｔｅｍ：ｙｏｕ　ａｒｅ`
is not detected, because nothing folds it before the literal matcher sees it.
That is exactly the pre-slice behaviour for this family — no worse — and closing
it needs vocabulary this story may not add.

No `re.IGNORECASE`: step 4 casefolds, so a flag here would be describing work
already done. Separators are single literal spaces because step 7 collapses
every run to exactly one, which is what turns `Ignore-all-previous-instructions`,
`Ignore.previous.instructions` and `Ig<ZWSP>nore previous in<ZWSP>structions`
into the same string. The barrier character step 6 leaves behind is deliberately
not in any separator position — that is the whole of step 6's purpose.

**A family belongs here only if its folded spelling is not also ordinary
prose.** Two of the four fail that test, for the same underlying reason: the
delimiter family is defined by brackets and the `system:` family by a colon, and
step 7 erases both. Neither is a phrase the fold can carry.
"""

REDACTION = "[redacted-injection]"
"""What a matched span becomes.

Two properties make `sanitize()`'s output a fixed point, and the guard needs
both:

1. **Neither projection matches it.** Its fold is ` redacted injection `, which
   no phrase family covers, and its literal form carries no delimiter pair.
2. **It is non-empty, and it folds to words.** A substitution therefore cannot
   join what stood on either side of the removed span into a new match. An empty
   replacement would delete text and close the gap — `ignore <system> previous
   instructions` would become `ignore  previous instructions`, a match the first
   pass never saw — and one more pass would then change the result, which is
   exactly what `__post_init__` refuses. With the fold in place the same
   argument covers more ground: `ignoredisregard the aboveprevious instructions`
   redacts to `ignore[redacted-injection]previous instructions`, whose fold is
   `ignore redacted injection previous instructions` — two words wide of a hit.
"""

# ─────────────────────────────────────────────────────────────────────────────
# The fold: seven ordered steps, and the order is the specification.
# ─────────────────────────────────────────────────────────────────────────────

_ZERO_WIDTH = "\u200b\u200c\u200d\u2060\ufeff"
"""Zero-width space, non-joiner, joiner, word joiner, and the BOM as ZWNBSP.

Removed from the fold unconditionally — the fold is a matching projection and
nothing is read off it. Whether one is removed from `for_model` is a separate
question, answered by `_unconditional_carriers`.
"""

_BIDI_CONTROLS = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
"""The bidi embeddings, overrides and isolates (U+202A-202E, U+2066-2069)."""

_CONTEXTUAL_CARRIERS = "\u200e\u200f\u00ad"
"""RLM, LRM and the soft hyphen — removed from the fold, not from `for_model`.

They arrive legitimately: in Arabic and Hebrew names, in Persian text, in emoji
sequences, and in anything pasted out of a PDF. A rule that strips every
directional mark from a name is an i18n regression wearing a security badge, so
these are only removed where a phrase hit already covers them — which the span
substitution does on its own, since the fold removes them before matching and a
match that needed one therefore contains it.
"""

_CARRIERS = frozenset(_ZERO_WIDTH + _BIDI_CONTROLS + _CONTEXTUAL_CARRIERS)

_BARRIER = "\x00"
"""Step 6's sentence barrier: a character no pattern matches and step 7 keeps.

It has to be a character rather than a marker string, because step 7 works on
runs and anything spellable in letters would become part of a word. NUL is the
choice because nothing in the vocabulary contains it and it is not alphanumeric,
so it cannot fuse two words into one match either. An input carrying a literal
NUL would otherwise *be* a barrier it did not earn — a free separator an author
could paste between two words — so `_STRIP_TABLE` turns an incoming NUL into a
space before step 6 can read it.
"""

_STRIP_TABLE: dict[int, str | None] = {ord(ch): None for ch in _CARRIERS}
_STRIP_TABLE[ord(_BARRIER)] = " "

_TERMINATORS = ".!?"
_CLOSERS = "\"'”’»›)]}」』〉》"
"""Closing quotes, brackets and parentheses a terminator may be trailed by.

Step 6 runs after NFKC, so the full-width and compatibility spellings have
already become these — `）` is `)` and `＂` is `"` by the time this is read. The
straight quote and apostrophe are ambiguous between opening and closing and are
included anyway: an opening quote never follows a terminator, so admitting them
costs nothing and refusing them would miss the commonest spelling of all.
"""

_TERMINATOR = re.compile(
    rf"[{re.escape(_TERMINATORS)}](?=[{re.escape(_CLOSERS)}]*(?:\s|$))"
)
"""Step 6 — a sentence terminator, optionally trailed by closing punctuation,
*followed by whitespace or end of input*.

Step 6 exists because step 7 would otherwise invent a false positive the literal
matcher does not have: collapsing all punctuation alike makes `"Please ignore.
Previous instructions from Jira are stale."` fold to `ignore previous
instructions` and match. A terminator that is *not* followed by whitespace stays
an ordinary separator, so `Ignore.previous.instructions` still folds through and
is still caught. Both halves are pinned in the test matrix; neither is optional,
and that is why the fold is specified as an ordered sequence rather than a set
of transformations.

**The closing-punctuation clause is the same false positive wearing a quotation
mark.** `He said "Please ignore." Previous instructions are stale.` puts a `"`
between the terminator and the whitespace, and a rule reading whitespace alone
does not fire — so the two sentences folded into a hit. The trailing closers are
looked *past* rather than consumed: only the terminator becomes the barrier, and
the closers fall to step 7 like any other punctuation. That keeps the clause's
limit intact, which the matrix pins with `Ignore."previous".instructions` — no
whitespace follows either terminator there, so step 6 does not fire and the
obfuscation still folds through.
"""

_SEPARATOR_RUN = re.compile(rf"(?:[^\w{re.escape(_BARRIER)}]|_)+")
_SEPARATOR_CHAR = re.compile(rf"[^\w{re.escape(_BARRIER)}]|_")
"""Step 7 — every other non-alphanumeric run becomes a single space.

`\\w` rather than `[0-9a-z]` so that Hebrew, Arabic and CJK text keeps its
letters instead of folding to one long space; `_` is excluded from it by hand,
because `ignore_all_previous_instructions` is the same obfuscation as the
hyphenated spelling and `\\w` would have kept the underscores inside the word.

Two spellings of one rule: the run form is what the whole-string fold substitutes
with, the single-character form is what the index-mapped fold tests per
character. Sharing the character class is what keeps the two folds from
disagreeing over the definition of a separator rather than over normalisation.
"""

_WHITESPACE = re.compile(r"\s")

_JAMO_CONTINUATION = frozenset(
    chr(cp) for cp in [*range(0x1160, 0x1200), *range(0xD7B0, 0xD800)]
)
"""Conjoining jamo V and T, which compose with the syllable *before* them.

The only canonical composition that crosses a starter boundary. Everything else
NFKC joins is a starter plus a combining mark, which `_clusters` already keeps
together — so with these, a cluster's fold equals the whole string's fold
restricted to it, and the two folds agree on Korean text written in jamo instead
of failing loudly over it.
"""

_MAPPED_FOLD_LIMIT = 1 << 20
"""Characters past which the index-mapped projection is not built.

The mapped fold costs a per-character Python loop and an integer pair per folded
character, so it is the one part of sanitization whose memory grows with a
hostile field rather than with a real one. Past the cap the slice fails closed
rather than degrading quietly: a field this large that matches at all has its
whole `for_model` replaced by `REDACTION`, which is a worse answer for the text
and the only safe one for the model. The raw is retained either way (AD-29), so
nothing is lost — and the behaviour is asserted in the matrix rather than
discovered by whoever first pastes a megabyte.

**Applied to the folded length as well as the input length**, because the map
has one entry per *folded* character and NFKC expands: `ﷺ` is one character
that folds to eighteen, so a field well under the input cap could fold to many
times it and build the map anyway. Either length past the cap fails closed in
the same way.

What this bounds is the mapped projection, not the whole-string fold. Pass 1
still has to fold the field to learn whether it matches, and that cost is linear
in the folded length — bounded by NFKC's worst expansion times the input, and
not otherwise capped here.
"""


def _fold(text: str) -> str:
    """The cheap whole-string fold — pass 1, which decides *whether* anything matches.

    Seven steps, in order. (1) scan for carriers and (2) remove them, both done
    by `_STRIP_TABLE`; (3) NFKC; (4) casefold; (5) NFD and drop combining marks;
    (6) a sentence terminator — optionally trailed by closing quotes, brackets
    or parentheses — followed by whitespace or end of input becomes the barrier;
    (7) every other non-alphanumeric run collapses to one space.

    No index map, because the common case is benign and the map is what costs.
    `_fold_with_spans` builds the same string with one, and only when this one
    has already found a hit.
    """
    return _fold_stripped(text.translate(_STRIP_TABLE))


def _fold_stripped(stripped: str) -> str:
    """Steps 3-7 over text whose carriers are already gone."""
    folded = unicodedata.normalize("NFKC", stripped).casefold()
    if not folded.isascii():
        # Skipped for ASCII deliberately, and it is the common case: NFD leaves
        # ASCII alone and ASCII holds no combining marks, so the shortcut cannot
        # change the answer — it only avoids a per-character Python loop over
        # the whole field.
        folded = unicodedata.normalize("NFD", folded)
        folded = "".join(
            ch for ch in folded if not unicodedata.category(ch).startswith("M")
        )
    return _SEPARATOR_RUN.sub(" ", _TERMINATOR.sub(_BARRIER, folded))


def _clusters(text: str) -> list[tuple[int, int]]:
    """Index ranges the mapped fold may normalise independently.

    A cluster is a starter plus everything that composes *onto* it: combining
    marks and conjoining jamo. NFKC never joins anything across such a boundary,
    so folding cluster by cluster gives the same string as folding the whole —
    which is what makes an index map possible at all. `_fold_with_spans` still
    checks the result against `_fold`, because "never" here is a claim about
    Unicode's composition rules and not about this function.
    """
    bounds: list[tuple[int, int]] = []
    for index, ch in enumerate(text):
        if bounds and _is_continuation(ch):
            bounds[-1] = (bounds[-1][0], index + 1)
        else:
            bounds.append((index, index + 1))
    return bounds


def _is_continuation(ch: str) -> bool:
    if ch.isascii():
        # No ASCII character is a mark or a jamo, and the check below is two
        # `unicodedata` lookups per character of the field.
        return False
    return (
        unicodedata.combining(ch) != 0
        or unicodedata.category(ch).startswith("M")
        or ch in _JAMO_CONTINUATION
    )


def _fold_with_spans(text: str) -> tuple[str, list[tuple[int, int]]]:
    """The index-mapped fold — pass 2, which says *where* in the raw a hit was.

    Returns the folded string and, per folded character, the half-open raw span
    it came from. The fold is not length-preserving: `ß`->`ss`, `ﬁ`->`fi` and
    `İ`->`i` change one character into two or one, and `…`->`...` into three, so
    a folded match boundary can land inside one raw character's expansion.
    Reporting a span per folded character rather than an offset is what lets
    `_match_spans` widen to the smallest raw span whose fold *covers* the match,
    never to a narrower one that would split a character or leave matched text
    behind.
    """
    chars: list[str] = []
    spans: list[tuple[int, int]] = []
    if text.isascii():
        # The same steps, done in bulk. ASCII has no carrier but NUL, which
        # `_STRIP_TABLE` maps to a space, and ASCII casefolding is one character
        # for one — so the projection is length-preserving and every folded
        # character comes from the raw index it sits at. NFKC and NFD leave
        # ASCII alone and ASCII holds no combining marks, so steps 3 and 5 are
        # no-ops rather than skipped work.
        chars = list(text.translate(_STRIP_TABLE).casefold())
        spans = [(index, index + 1) for index in range(len(chars))]
    else:
        for start, end in _clusters(text):
            for ch in _fold_cluster(text[start:end]):
                chars.append(ch)
                spans.append((start, end))

    # Step 6, on the post-step-5 characters, exactly where the whole-string fold
    # applies it. The probe walks past closing punctuation the same way the
    # pattern's lookahead does, and it cannot collide with a barrier written by
    # an earlier iteration: a barrier is neither a terminator nor a closer, and
    # earlier iterations only wrote at lower indices.
    for index, ch in enumerate(chars):
        if ch not in _TERMINATORS:
            continue
        probe = index + 1
        while probe < len(chars) and chars[probe] in _CLOSERS:
            probe += 1
        if probe == len(chars) or _WHITESPACE.fullmatch(chars[probe]):
            chars[index] = _BARRIER

    # Step 7. A collapsed run reports the span of the whole run, so a match that
    # ends on a separator widens rather than narrows.
    folded: list[str] = []
    mapped: list[tuple[int, int]] = []
    index = 0
    while index < len(chars):
        if not _SEPARATOR_CHAR.fullmatch(chars[index]):
            folded.append(chars[index])
            mapped.append(spans[index])
            index += 1
            continue
        run_start = index
        while index < len(chars) and _SEPARATOR_CHAR.fullmatch(chars[index]):
            index += 1
        folded.append(" ")
        mapped.append((spans[run_start][0], spans[index - 1][1]))
    return "".join(folded), mapped


def _fold_cluster(chunk: str) -> str:
    """Steps 1-5 over one cluster. Steps 6 and 7 are not per-cluster rules."""
    stripped = chunk.translate(_STRIP_TABLE)
    if not stripped:
        return ""
    folded = unicodedata.normalize("NFKC", stripped).casefold()
    if folded.isascii():
        return folded
    folded = unicodedata.normalize("NFD", folded)
    return "".join(ch for ch in folded if not unicodedata.category(ch).startswith("M"))


class FoldDisagreement(AssertionError):
    """The two folds produced different strings for one input.

    An `AssertionError` because it is the failure of an internal claim rather
    than a bad argument: `_fold` and `_fold_with_spans` are two implementations
    of one specification, and a pass-1 hit that pass 2 cannot locate must be a
    loud failure and never a silent return of unmodified text. It can only be
    raised over text that already matched, because pass 2 is built only then.
    """


# ─────────────────────────────────────────────────────────────────────────────
# Carriers
# ─────────────────────────────────────────────────────────────────────────────

_CARRIER_RUN = re.compile(
    f"[{re.escape(_ZERO_WIDTH + _BIDI_CONTROLS + _CONTEXTUAL_CARRIERS)}]+"
)
"""A maximal run of carriers of *any* class, which is the unit the rule judges.

Runs are taken over every carrier rather than over the zero-width ones alone
because the flank test has to look past the others. Reading the immediate
neighbours of a zero-width run instead made an adjacent bidi control or soft
hyphen hide the zero-width character on the first pass and expose it on the
second: `re<RLO><ZWSP>lease` reported only the override, removing it brought the
`l` up against the `e`, and `sanitize()` then raised `ForgedSanitization` over
its own output. Judging the whole run against the nearest non-carrier character
on each side makes the answer the same before and after any removal, which is
what a one-pass fixed point requires.
"""


def _unconditional_carriers(text: str) -> list[tuple[int, int]]:
    """Carrier spans removed from `for_model` whether or not a phrase matched.

    **Detection is removal.** A carrier reported but left in place would make
    `for_model` fail `8e`'s fixed-point check, so `sanitize()` could not
    construct its own return value and every field holding a stray zero-width
    space would raise at the boundary AD-12 says every payload must cross.

    Two classes, because carriers occur legitimately. Unconditional, here: a
    zero-width character *inside a word*, and every bidi embedding, override and
    isolate. Contextual, not here: RLM, LRM and the soft hyphen, which arrive in
    real names and real PDFs — they are removed only where a phrase hit already
    covers them, which the span substitution does by itself.

    "Inside a word" is measured against the nearest character that is **not a
    carrier of any class**, on each side of a maximal carrier run — see
    `_CARRIER_RUN` for what reading the immediate neighbours instead cost. It is
    also what keeps a ZWJ family emoji intact: its nearest non-carrier
    neighbours are emoji, which are not alphanumeric.

    This check runs on the raw text, before the fold. It has to: normalising is
    defined by what it erases, so the step that strips zero-width characters
    cannot also be the step that reports them. Run late and it would not merely
    miss them, it would pass silently and permanently.
    """
    # Scanned with a compiled pattern rather than by iterating the field, so
    # that ordinary prose — which holds none of these — costs one pass in C
    # instead of one Python loop per character on every sanitized field.
    spans: list[tuple[int, int]] = []
    for run in _CARRIER_RUN.finditer(text):
        before = text[run.start() - 1] if run.start() else ""
        after = text[run.end()] if run.end() < len(text) else ""
        inside_a_word = before.isalnum() and after.isalnum()
        for offset, ch in enumerate(run.group(), start=run.start()):
            if ch in _BIDI_CONTROLS or (inside_a_word and ch in _ZERO_WIDTH):
                spans.append((offset, offset + 1))
    return spans


# ─────────────────────────────────────────────────────────────────────────────
# Matching, and the substitution
# ─────────────────────────────────────────────────────────────────────────────


def _match_spans(text: str) -> list[tuple[int, int]]:
    """Raw spans to redact — two passes, because the common case is benign.

    Pass 1 folds the whole string once and asks whether anything matches at all.
    Nothing does for ordinary prose, and that answer costs no index map. Only
    when a projection has hit does pass 2 build the mapping that says where in
    the raw the hit lies.
    """
    stripped = text.translate(_STRIP_TABLE)
    folded = _fold_stripped(stripped)
    literal_hits = [match.span() for match in _INJECTION.finditer(stripped)]
    phrase_hits = [match.span() for match in _FOLDED_INJECTION.finditer(folded)]
    if not literal_hits and not phrase_hits:
        return []

    if len(text) > _MAPPED_FOLD_LIMIT or len(folded) > _MAPPED_FOLD_LIMIT:
        # Fail closed. See `_MAPPED_FOLD_LIMIT` — both lengths, because NFKC
        # expands, so a field under the input cap can still fold past it.
        return [(0, len(text))]

    spans: list[tuple[int, int]] = []
    if literal_hits:
        raw_of = _strip_index(text)
        spans += [(raw_of[start], raw_of[end - 1] + 1) for start, end in literal_hits]
    if phrase_hits:
        mapped, per_character = _fold_with_spans(text)
        if mapped != folded:
            raise FoldDisagreement(_fold_disagreement_message(text, folded, mapped))
        spans += [
            (per_character[start][0], per_character[end - 1][1])
            for start, end in phrase_hits
        ]
    return spans


def _strip_index(text: str) -> list[int]:
    """Per carrier-stripped position, the raw index it came from."""
    return [index for index, ch in enumerate(text) if ch not in _CARRIERS]


def _fold_disagreement_message(text: str, fast: str, mapped: str) -> str:
    """Names the input without quoting it.

    The input is provider prose by definition, and this message travels into
    tracebacks, logs and error reporters that AD-31's disclosure rules never
    covered — the same reason `ForgedSanitization` quotes nothing. A digest names
    it well enough to match a second occurrence to the first, and the offset says
    where to look once someone has the value in hand.
    """
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return (
        f"the whole-string fold and the index-mapped fold disagree on input "
        f"sha256:{digest} of {len(text)} characters, first differing at folded "
        f"offset {_first_difference(fast, mapped)} ({len(fast)} against "
        f"{len(mapped)} folded characters). A hit the mapped fold cannot locate "
        f"must not be reported as clean text, so this is raised rather than "
        f"swallowed. Its contents are deliberately not quoted here."
    )


def _first_difference(left: str, right: str) -> int:
    """The first index at which two strings differ; their common length if not."""
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return index
    return min(len(left), len(right))


def _redact(text: str) -> str:
    """The sanitizing transform, written once.

    **Both `sanitize()` and `__post_init__` go through here, and that is the
    point.** The guard's rule is "one more pass changes nothing", so if the
    producer and the checker each spelled the substitution themselves, a story
    that rewrote one would leave the other validating against the old rule — the
    guard would still pass, over text the current sanitizer would have altered.
    Story 8g rewrote the matcher underneath it and this seam is why that took no
    change at the boundary.
    """
    edits = [_Edit(start, end, REDACTION) for start, end in _match_spans(text)]
    edits += [_Edit(start, end, "") for start, end in _unconditional_carriers(text)]
    if not edits:
        return text
    return _substitute(text, edits)


class _Edit(typing.NamedTuple):
    """One substitution: the raw span `[start, end)` becomes `replacement`."""

    start: int
    end: int
    replacement: str


def _substitute(text: str, edits: list[_Edit]) -> str:
    """Merge overlapping spans, then substitute in reverse order.

    Reverse order because an earlier replacement would otherwise shift every
    later offset, and the fold's expansions mean the shift is not a constant.
    Merging first because a phrase hit, a delimiter hit and a carrier can all
    land on overlapping ranges of one string — a carrier inside a matched phrase
    is the ordinary case — and two substitutions into one range would corrupt
    both. A merged range redacts if any of its parts did: a carrier swallowed by
    a phrase hit needs no deletion of its own.
    """
    merged: list[_Edit] = []
    for edit in sorted(edits):
        if merged and edit.start < merged[-1].end:
            previous = merged[-1]
            merged[-1] = previous._replace(
                end=max(previous.end, edit.end),
                replacement=(
                    REDACTION
                    if REDACTION in (previous.replacement, edit.replacement)
                    else previous.replacement
                ),
            )
            continue
        merged.append(edit)

    out = text
    for edit in reversed(merged):
        out = out[: edit.start] + edit.replacement + out[edit.end :]
    return out


class ForgedSanitization(ValueError):
    """`for_model` is not a fixed point of the sanitizer, so it was never sanitized.

    A `ValueError` because it is a bad argument to a constructor, and the
    constructor is the only place it can be raised.
    """


@typing.final
@dataclass(frozen=True, slots=True)
class Sanitized:
    """AD-29 — the raw is retained; only `for_model` is ever put in a prompt.

    **`__post_init__` is what makes the type worth having.** Until story 8e this
    was a public frozen dataclass with two `str` fields and no validation, so
    `Sanitized(raw=t, for_model=t)` type-checked while bypassing `sanitize()`
    entirely: a `ModelPort` typed against it would have refused a bare `str` and
    accepted the same text behind one keyword argument.

    The check is that `for_model` is a **fixed point of the sanitizer** — one
    more pass of `_redact` leaves it unchanged — and deliberately not that it
    equals `sanitize(raw).for_model`. Equality would refuse every honest
    derivation a caller makes from an already-clean string, truncation among
    them. Truncation cannot smuggle an injection back in: neither pattern is
    anchored, so a match in a prefix is a match in the whole.

    Forging a *clean* `for_model` — `Sanitized(raw=<anything>, for_model="hi")`
    — stays possible, and is harmless. Clean text is all a model was ever going
    to receive; what the guard exists to stop is untouched provider prose
    wearing the type.

    **`@final` closes the other way round it.** A `__post_init__` on a subclass
    overrides this one, so `class Forged(Sanitized): def __post_init__(self):
    pass` constructed over an injection string and still satisfied
    `Sequence[Sanitized]` at the port — the guard bypassed by inheritance rather
    than by a keyword argument. Static rather than runtime, because that is what
    `ModelPort`'s protection is: a subclass is refused where the mistyped call
    is, by the same checker, and there is no honest reason to specialise a pair
    of strings.

    **Story 8g widened what the fixed point means**, and the widening is the
    reason `sanitize()` had to remove carriers rather than only report them. A
    `for_model` still holding a zero-width space inside a word is not a fixed
    point, so a field cleaned by the old matcher can fail construction now. That
    is accepted on purpose: `Sanitized` is never persisted, and the one place a
    `for_model`-derived value *is* — the `[:80]` proposal summary
    `pipelines.py` stages — is left as it stands rather than re-derived. A
    staged proposal is a record of what was proposed, AD-5 supersedes rather
    than mutates, and rewriting stored summaries to match a newer filter would
    edit history to look like it always agreed.
    """

    raw: str
    for_model: str

    def __post_init__(self) -> None:
        cleaned = _redact(self.for_model)
        if cleaned != self.for_model:
            # **The value is not quoted.** It is provider prose by definition —
            # that is what makes it untrusted — and an exception message travels
            # into tracebacks, logs and error reporters that AD-31's disclosure
            # rules never covered. Reporting where the sanitizer would have
            # changed it keeps the refusal debuggable without copying the text
            # somewhere new.
            #
            # The offset is where the first edit starts, including when the
            # cleaned string is a prefix of the value: removing a trailing
            # carrier leaves exactly that, and the common length
            # `_first_difference` then returns *is* the carrier's index. It
            # cannot stop short of the first edit either: no replacement starts
            # with the character it replaces, since every span starts on `<` or
            # a letter while `REDACTION` starts with `[`, and a deleted carrier
            # is never followed by an identical carrier that survives.
            where = _first_difference(self.for_model, cleaned)
            raise ForgedSanitization(
                f"for_model is not a fixed point of the sanitizer: one more "
                f"pass would change it, first at offset {where} of "
                f"{len(self.for_model)} characters, so it did not come through "
                f"sanitize(). Either an injection pattern still matches there "
                f"or a carrier the sanitizer removes, such as a zero-width "
                f"space inside a word or a bidi override, is still present. "
                f"Its contents are deliberately not quoted here. Build it with "
                f"`sanitize(raw)` and derive from `.for_model` — truncating or "
                f"slicing that value is fine, re-using the raw is not."
            )

    @property
    def was_modified(self) -> bool:
        """Whether the two halves differ — **meaningful only for a pair `sanitize()` built.**

        It answers "do these two strings differ", not "did the sanitizer change
        something", and on a hand-built pair those come apart:
        `Sanitized(raw="anything", for_model="harmless")` reports True while no
        sanitizer touched either string. That construction is permitted on
        purpose — clean text is all a model was ever going to receive — so the
        caveat is the honest way to keep both. Read it only off `sanitize(t)`,
        where the two halves have the derivation that makes the answer mean what
        it says.
        """
        return self.raw != self.for_model


def sanitize(raw: str) -> Sanitized:
    """Derive the model-facing copy, leaving the raw untouched (AD-29).

    `for_model` is the *original* text with the matched spans replaced by
    `REDACTION` and the unconditional carriers deleted — never the folded
    projection. Folding destroys casing, punctuation and non-ASCII characters
    that legitimate text needs to stay readable to a model; it exists to decide
    what matches, not to be sent anywhere.
    """
    return Sanitized(raw=raw, for_model=_redact(raw))


# ─────────────────────────────────────────────────────────────────────────────
# Which rule ran (story 8h)
# ─────────────────────────────────────────────────────────────────────────────


class RuleChange(typing.NamedTuple):
    """One entry in `RULE_CHANGELOG`: a version of the rule and what it hashed to.

    `unidata_version` is recorded beside the fingerprint because it is one of
    the fingerprint's inputs, and the one most likely to move without anyone
    editing this module: a different Python can ship different Unicode data.
    """

    version: int
    date: str
    fingerprint: str
    unidata_version: str
    change: str


RULE_VERSION = 1
"""The name of the rule `sanitize()` applies.

**It names the rule. It does not link any output to it.** Nothing records which
version cleaned a given text, so this constant cannot answer "which rule did
this prompt pass?" on its own. That needs the disclosure-ledger field the
spec's Ask First deferred, below. What the constant does guarantee is that the
name is honest: the rule cannot change while the name stays the same.

**Fingerprinted, not remembered.** `SCHEMA_VERSION` in `pm_ai.storage.service`
has the same shape and a different discipline: a human bumps it, and a missing
migration makes forgetting loud. Nothing here would be loud. A widened pattern
with a stale version gives correct-looking output and an audit trail that
quietly names the wrong rule. So
`tests/domain/test_sanitization_rates.py` hashes the rule and compares the
hash with the latest `RULE_CHANGELOG` entry. The hash covers the patterns, the
carrier sets, the source of every function that decides `for_model`, and
`unicodedata.unidata_version`, because NFKC, casefolding, `\\w` and `\\s` all
read the Unicode data. Any change to those, including a Python upgrade that
moves the Unicode data under unchanged code, fails that test until this
constant and a new changelog entry move together.

A module constant only. The disclosure ledger does not record it: that would
widen the Tier-1 entry grammar, which story 8e declined to do, and the spec
defers the question to the slice that settles that grammar.
"""

RULE_CHANGELOG: tuple[RuleChange, ...] = (
    RuleChange(
        version=1,
        date="2026-09-23",
        fingerprint="sha256:51e386406ea9c0cf31674efc45503d706715a0d89f5f5f28387570acbe633e13",
        unidata_version="16.0.0",
        change=(
            "First fingerprint, taken by story 8h over the rule as 8e and 8g "
            "left it at b6b49b1. It covers the literal matcher over "
            "carrier-stripped text for all four families, the folded matcher "
            "for the ignore and disregard families, the seven-step fold with "
            "step 6's closing-punctuation clause, and the split into "
            "unconditional and contextual carriers. Unicode data 16.0.0. The "
            "rule itself did not change."
        ),
    ),
)
"""Every version of the rule, oldest first. Append only.

The test pins each past entry, so a fingerprint rewritten in place fails as
surely as a rule changed without one.
"""
