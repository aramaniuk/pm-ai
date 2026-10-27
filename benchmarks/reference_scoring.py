"""Story 7f's scoring of meeting summaries against a private reference set — standard library only.

Everything here runs without a model and without `pm_ai`: the folder and name
rules, loading and checking `m<NN>.reference.toml`, folding text, matching by
word groups, pairing reference items with returned items by maximum matching,
the per-meeting scores and pooled totals, and comparing two run files.
`score_summaries.py` is the runner that calls it.

No function here puts private text in an exception message or a return value
meant for the screen: a `Refused` names a stem, a reference id and a rule,
never a match word, an `about`, a `title` or transcript text.
"""

from __future__ import annotations

import hashlib
import re
import os
import stat
import tomllib
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FORMAT = 1
"""The run file's format; a run file with another is refused."""

MAX_DECISIONS = 25
"""7b's `MAX_DECISIONS`, repeated here so this module needs no `pm_ai`; a test holds them equal."""
MAX_ACTION_ITEMS = 15
"""7b's `MAX_ACTION_ITEMS`, likewise."""

SHORT_WORD_LETTERS = 4
"""A match word with fewer letters is refused unless it holds a digit or a hyphen."""

STEM = re.compile(r"m\d{2,}")
REFERENCE_SUFFIX = ".reference.toml"
TRANSCRIPT_SUFFIXES = (".vtt", ".txt")
SKIPPED = frozenset({".work", "runs"})
"""The folder's own working entries, never scanned as meetings."""

KINDS = ("decision", "action", "not_an_item")
ID_PATTERNS = {
    "decision": re.compile(r"D\d+"),
    "action": re.compile(r"A\d+"),
    "not_an_item": re.compile(r"X\d+"),
}
TOP_KEYS = frozenset({"transcript", "title", *KINDS})
ITEM_KEYS = {
    "decision": (frozenset({"id", "match"}), frozenset({"about"})),
    "action": (frozenset({"id", "match", "owner"}), frozenset({"about"})),
    "not_an_item": (frozenset({"id", "match"}), frozenset({"about"})),
}


class Refused(ValueError):
    """A rule broken; the message names only a stem, an id and the rule (exit 2)."""


# ── The folder ───────────────────────────────────────────────────────────────


def check_folder(folder: Path, repo_trees: Iterable[Path]) -> Path:
    """The folder, symlinks resolved, or `Refused` naming the rule it breaks."""
    resolved = folder.resolve()
    if not resolved.is_dir():
        raise Refused("the references folder must be an existing directory")
    for tree in repo_trees:
        if not tree.exists():
            continue
        # By identity, not by spelling: APFS is case-insensitive, so a path
        # spelled with other letter case names the same folder.
        if any(os.path.samefile(place, tree) for place in (resolved, *resolved.parents)):
            raise Refused("the references folder must be outside the repository's working tree")
    if stat.S_IMODE(resolved.stat().st_mode) != 0o700:
        raise Refused("the references folder's mode must be exactly 0700")
    return resolved


def inside(path: Path, folder: Path) -> bool:
    resolved = path.resolve()
    return folder in resolved.parents


# ── Folding and matching ─────────────────────────────────────────────────────


def fold(text: str) -> str:
    """Unicode NFC, case-folded, `ё` read as `е`, whitespace collapsed."""
    return " ".join(unicodedata.normalize("NFC", text).casefold().replace("ё", "е").split())


def occurs(word: str, text: str) -> bool:
    """Whether folded `word` starts a word in folded `text`."""
    start = text.find(word)
    while start != -1:
        if start == 0 or not text[start - 1].isalnum():
            return True
        start = text.find(word, start + 1)
    return False


def matches(groups: Sequence[Sequence[str]], text: str) -> bool:
    """Every group has at least one word that occurs in `text` (both already folded)."""
    return all(any(occurs(word, text) for word in group) for group in groups)


def maximum_matching(adjacent: Sequence[Sequence[int]], right: int) -> dict[int, int]:
    """Left index → right index, a maximum matching by augmenting paths, left tried in order."""
    owner: list[int | None] = [None] * right

    def augment(left: int, seen: set[int]) -> bool:
        for candidate in adjacent[left]:
            if candidate in seen:
                continue
            seen.add(candidate)
            held = owner[candidate]
            if held is None or augment(held, seen):
                owner[candidate] = left
                return True
        return False

    for left in range(len(adjacent)):
        augment(left, set())
    return {held: index for index, held in enumerate(owner) if held is not None}


# ── References ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Item:
    kind: str
    id: str
    groups: tuple[tuple[str, ...], ...]
    """Folded match words, one tuple per group."""
    owners: tuple[str, ...] = ()
    """Folded accepted owner spellings; actions only."""


@dataclass(frozen=True, slots=True)
class Reference:
    stem: str
    transcript: Path
    title: str | None
    decisions: tuple[Item, ...]
    actions: tuple[Item, ...]
    not_items: tuple[Item, ...]
    sha256: str

    def items(self) -> tuple[Item, ...]:
        return self.decisions + self.actions + self.not_items


def _words(stem: str, ident: str, value: object) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list) or not value:
        raise Refused(f"{stem} {ident}: match must be a non-empty list of groups")
    groups = []
    for number, group in enumerate(value, 1):
        if not isinstance(group, list) or not group:
            raise Refused(f"{stem} {ident}: group {number} must be a non-empty list of words")
        words = []
        for word in group:
            if not isinstance(word, str) or not fold(word):
                raise Refused(f"{stem} {ident}: group {number} holds an empty or non-text word")
            folded = fold(word)
            letters = sum(c.isalpha() for c in folded)
            if (
                letters < SHORT_WORD_LETTERS
                and not any(c.isdigit() for c in folded)
                and "-" not in folded
            ):
                raise Refused(
                    f"{stem} {ident}: group {number} holds a word under "
                    f"{SHORT_WORD_LETTERS} letters without a digit or a hyphen"
                )
            words.append(folded)
        groups.append(tuple(words))
    return tuple(groups)


def _item(stem: str, kind: str, raw: object, seen: set[str]) -> Item:
    if not isinstance(raw, dict):
        raise Refused(f"{stem}: every [[{kind}]] must be a table")
    ident = raw.get("id")
    if not isinstance(ident, str) or not ID_PATTERNS[kind].fullmatch(ident):
        raise Refused(f"{stem}: a [[{kind}]] id must match {ID_PATTERNS[kind].pattern}")
    if ident in seen:
        raise Refused(f"{stem} {ident}: duplicate id")
    seen.add(ident)
    required, optional = ITEM_KEYS[kind]
    keys = set(raw)
    if not required <= keys:
        raise Refused(f"{stem} {ident}: missing key {', '.join(sorted(required - keys))}")
    if not keys <= required | optional:
        raise Refused(f"{stem} {ident}: unknown key {', '.join(sorted(keys - required - optional))}")
    if "about" in raw and not isinstance(raw["about"], str):
        raise Refused(f"{stem} {ident}: about must be text")
    owners: tuple[str, ...] = ()
    if kind == "action":
        owner = raw["owner"]
        if (
            not isinstance(owner, list)
            or not owner
            or not all(isinstance(o, str) and fold(o) for o in owner)
        ):
            raise Refused(f"{stem} {ident}: owner must list at least one non-empty spelling")
        owners = tuple(fold(o) for o in owner)
    return Item(kind, ident, _words(stem, ident, raw["match"]), owners)


def load_reference(path: Path, folder: Path) -> Reference:
    """One `m<NN>.reference.toml`, checked; `Refused` naming the stem, id and rule."""
    stem = path.name[: -len(REFERENCE_SUFFIX)]
    if not STEM.fullmatch(stem):
        raise Refused(f"a reference file's stem must match {STEM.pattern}")
    raw_bytes = path.read_bytes()
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise Refused(f"{stem}: the reference is not UTF-8 TOML") from None
    keys = set(data)
    if "transcript" not in keys:
        raise Refused(f"{stem}: missing key transcript")
    if not keys <= TOP_KEYS:
        raise Refused(f"{stem}: unknown key {', '.join(sorted(keys - TOP_KEYS))}")
    name = data["transcript"]
    if (
        not isinstance(name, str)
        or "/" in name
        or "\\" in name
        or Path(name).suffix not in TRANSCRIPT_SUFFIXES
        or Path(name).stem != stem
    ):
        raise Refused(f"{stem}: transcript must be {stem}.vtt or {stem}.txt in the folder")
    transcript = folder / name
    if not transcript.is_file() or transcript.resolve().parent != folder:
        raise Refused(f"{stem}: transcript must be {stem}.vtt or {stem}.txt in the folder")
    try:
        transcript.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise Refused(f"{stem}: the transcript is not UTF-8") from None
    title = data.get("title")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        raise Refused(f"{stem}: title must be non-empty text")
    seen: set[str] = set()
    items: dict[str, tuple[Item, ...]] = {}
    for kind in KINDS:
        raw = data.get(kind, [])
        if not isinstance(raw, list):
            raise Refused(f"{stem}: {kind} must be an array of tables")
        items[kind] = tuple(_item(stem, kind, entry, seen) for entry in raw)
    if len(items["decision"]) > MAX_DECISIONS:
        raise Refused(f"{stem}: more than {MAX_DECISIONS} decisions")
    if len(items["action"]) > MAX_ACTION_ITEMS:
        raise Refused(f"{stem}: more than {MAX_ACTION_ITEMS} actions")
    return Reference(
        stem=stem,
        transcript=transcript,
        title=title,
        decisions=items["decision"],
        actions=items["action"],
        not_items=items["not_an_item"],
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


@dataclass(frozen=True, slots=True)
class Scan:
    references: tuple[Reference, ...]
    unreferenced: tuple[str, ...]
    """Stems of transcripts no reference names: warned about, not run."""


def scan(folder: Path) -> Scan:
    """Every reference in the folder, checked, and the transcripts left without one."""
    references = []
    transcripts = set()
    for entry in sorted(folder.iterdir()):
        if entry.name in SKIPPED:
            continue
        if entry.name.endswith(REFERENCE_SUFFIX):
            references.append(load_reference(entry, folder))
        elif entry.suffix in TRANSCRIPT_SUFFIXES:
            if not STEM.fullmatch(entry.stem):
                raise Refused(f"a transcript's stem must match {STEM.pattern}")
            transcripts.add(entry.stem)
    if not references:
        raise Refused("the references folder holds no m<NN>.reference.toml")
    named = {r.stem for r in references}
    return Scan(tuple(references), tuple(sorted(transcripts - named)))


def group_warnings(reference: Reference, transcript_text: str) -> list[str]:
    """`m01 D1 group 2` for each group none of whose words occurs in the transcript."""
    text = fold(transcript_text)
    return [
        f"{reference.stem} {item.id} group {number}"
        for item in reference.items()
        for number, group in enumerate(item.groups, 1)
        if not any(occurs(word, text) for word in group)
    ]


# ── Scoring ──────────────────────────────────────────────────────────────────


def decision_text(decision: Mapping[str, Any]) -> str:
    return fold(decision.get("statement") or "")


def action_text(action: Mapping[str, Any]) -> str:
    return fold(" ".join(part for part in (action.get("what"), action.get("due")) if part))


def _pairs(
    references: Sequence[Item], texts: Sequence[str], owners: Sequence[str] | None = None
) -> dict[int, int]:
    """Reference index → returned index. With `owners`, each reference item tries the
    returned actions naming one of its owners first, so a tie leans to the right owner."""
    adjacent = []
    for item in references:
        found = [j for j, text in enumerate(texts) if matches(item.groups, text)]
        if owners is not None:
            found.sort(key=lambda j: owners[j] not in item.owners)
        adjacent.append(found)
    return maximum_matching(adjacent, len(texts))


def score(
    reference: Reference,
    decisions: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """Counts for one summary against one reference; no answer is empty lists."""
    decision_texts = [decision_text(d) for d in decisions]
    action_texts = [action_text(a) for a in actions]
    decision_pairs = _pairs(reference.decisions, decision_texts)
    action_owners = [fold(a.get("owner") or "") for a in actions]
    action_pairs = _pairs(reference.actions, action_texts, action_owners)
    owner_correct = sum(
        1
        for ref_index, returned in action_pairs.items()
        if action_owners[returned] in reference.actions[ref_index].owners
    )
    paired_decisions = set(decision_pairs.values())
    paired_actions = set(action_pairs.values())
    unpaired_decisions = [t for j, t in enumerate(decision_texts) if j not in paired_decisions]
    unpaired_actions = [t for j, t in enumerate(action_texts) if j not in paired_actions]
    return {
        "decisions_reference": len(reference.decisions),
        "decisions_returned": len(decisions),
        "decisions_paired": len(decision_pairs),
        "actions_reference": len(reference.actions),
        "actions_returned": len(actions),
        "actions_paired": len(action_pairs),
        "owner_correct": owner_correct,
        "decisions_restating_actions": sum(
            any(matches(item.groups, t) for item in reference.actions) for t in unpaired_decisions
        ),
        "actions_restating_decisions": sum(
            any(matches(item.groups, t) for item in reference.decisions) for t in unpaired_actions
        ),
        "not_an_item_hits": sum(
            any(matches(item.groups, t) for item in reference.not_items)
            for t in unpaired_decisions + unpaired_actions
        ),
    }


COUNTS = (
    "decisions_reference",
    "decisions_returned",
    "decisions_paired",
    "actions_reference",
    "actions_returned",
    "actions_paired",
    "owner_correct",
    "decisions_restating_actions",
    "actions_restating_decisions",
    "not_an_item_hits",
)


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 3)


def rates(counts: Mapping[str, int]) -> dict[str, float | None]:
    return {
        "decision_recall": _rate(counts["decisions_paired"], counts["decisions_reference"]),
        "decision_precision": _rate(counts["decisions_paired"], counts["decisions_returned"]),
        "action_recall": _rate(counts["actions_paired"], counts["actions_reference"]),
        "action_precision": _rate(counts["actions_paired"], counts["actions_returned"]),
        "owner_correct_rate": _rate(counts["owner_correct"], counts["actions_paired"]),
    }


def pooled(scores: Iterable[Mapping[str, int]]) -> dict[str, int]:
    """Counts summed: each reference item weighs the same, across meetings and repeats."""
    total = dict.fromkeys(COUNTS, 0)
    for one in scores:
        for key in COUNTS:
            total[key] += one[key]
    return total


# ── The run file ─────────────────────────────────────────────────────────────


def summarised(result: str) -> bool:
    return result == "summarized"


def meeting_row(stem: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    """One printed row: numbers, enum values and the stem only."""
    repeats = entry["repeats"]
    counts = pooled(r["scores"] for r in repeats)
    return {
        "meeting": stem,
        "results": [r["result"] for r in repeats],
        "exceptions": [r["exception"] for r in repeats if r.get("exception")],
        "wall_s": [r["wall_s"] for r in repeats],
        "calls": [r["calls"] for r in repeats],
        "prompt_tokens": [r["prompt_tokens"] for r in repeats],
        "output_tokens": [r["output_tokens"] for r in repeats],
        **counts,
        **rates(counts),
        "answers_identical": entry["answers_identical"],
        "scores_identical": entry["scores_identical"],
    }


MEASURED = ("wall_s", "prompt_tokens", "output_tokens")


def measured_sum(repeats: Sequence[Mapping[str, Any]], key: str) -> float | None:
    """The sum over summaries that measured it; `None` when any did not (an exception)."""
    values = [r.get(key) for r in repeats]
    if not values or any(v is None for v in values):
        return None
    return round(sum(values), 1)


def totals(meetings: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    repeats = [r for entry in meetings.values() for r in entry["repeats"]]
    counts = pooled(r["scores"] for r in repeats)
    return {
        "meeting": "total",
        "meetings": len(meetings),
        "summaries": len(repeats),
        "meetings_not_summarised": sum(not summarised(r["result"]) for r in repeats),
        **{key: measured_sum(repeats, key) for key in MEASURED},
        **counts,
        **rates(counts),
    }


def identical(values: Sequence[Any]) -> bool:
    return all(v == values[0] for v in values[1:])


REPEAT_KEYS = frozenset({"result", "scores", "decisions", "action_items"})


def check_run(data: object, name: str) -> Mapping[str, Any]:
    """A run file of this format whose shape the scorer can read, or `Refused`."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise Refused(f"{name}: not a run file of format {FORMAT}")
    meetings = data.get("meetings")
    if not isinstance(meetings, dict):
        raise Refused(f"{name}: no meetings")
    for stem, entry in meetings.items():
        label = stem if isinstance(stem, str) and STEM.fullmatch(stem) else "a meeting"
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("reference_sha256"), str)
            or not isinstance(entry.get("repeats"), list)
            or not entry["repeats"]
            or not all(isinstance(r, dict) and REPEAT_KEYS <= set(r) for r in entry["repeats"])
        ):
            raise Refused(f"{name}: {label} is not in the run file's shape")
    return data


def rescored(
    run: Mapping[str, Any],
    references: Mapping[str, Reference],
    scorer_commit: str,
    scorer_dirty: bool,
    source: str,
) -> dict[str, Any]:
    """The stored answers scored against the current references; no model."""
    missing = sorted(set(run["meetings"]) - set(references))
    if missing:
        raise Refused(f"no current reference for {', '.join(missing)}")
    meetings: dict[str, Any] = {}
    for stem, entry in run["meetings"].items():
        reference = references[stem]
        repeats = []
        for repeat in entry["repeats"]:
            again = dict(repeat)
            again["scores"] = score(reference, repeat["decisions"], repeat["action_items"])
            repeats.append(again)
        meetings[stem] = {
            **entry,
            "reference_sha256": reference.sha256,
            "repeats": repeats,
            "scores_identical": identical([r["scores"] for r in repeats]),
        }
    out = {
        **run,
        "scorer_commit": scorer_commit,
        "scorer_dirty": scorer_dirty,
        "rescored_from": source,
        "meetings": meetings,
    }
    out["totals"] = totals(meetings)
    return out


def compare(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Per-meeting and total deltas (new − old): rates, and counts per summary made."""
    old_set, new_set = set(old["meetings"]), set(new["meetings"])
    if old_set != new_set:
        raise Refused(
            "the run files hold different meetings: "
            + ", ".join(sorted(old_set ^ new_set))
        )
    changed = sorted(
        stem
        for stem in old_set
        if old["meetings"][stem]["reference_sha256"] != new["meetings"][stem]["reference_sha256"]
    )
    if changed:
        raise Refused(
            "the references differ for "
            + ", ".join(changed)
            + "; rescore both run files against the current references first"
        )

    def per_summary(repeats: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        counts = pooled(r["scores"] for r in repeats)
        made = len(repeats) or 1
        spent = {key: measured_sum(repeats, key) for key in MEASURED}
        return {
            **{k: counts[k] / made for k in COUNTS},
            **{k: None if v is None else v / made for k, v in spent.items()},
            **rates(counts),
            "not_summarised": sum(not summarised(r["result"]) for r in repeats) / made,
        }

    def delta(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, float | None]:
        return {
            k: None if a[k] is None or b[k] is None else round(b[k] - a[k], 3) for k in a
        }

    rows: list[dict[str, Any]] = [
        {
            "meeting": "setup",
            "same_model": old.get("model") == new.get("model"),
            "same_settings": old.get("settings") == new.get("settings"),
            "same_sent": old.get("sent") == new.get("sent"),
            "repeats": [old.get("repeat"), new.get("repeat")],
        }
    ]
    for stem in sorted(old_set):
        rows.append(
            {
                "meeting": stem,
                **delta(
                    per_summary(old["meetings"][stem]["repeats"]),
                    per_summary(new["meetings"][stem]["repeats"]),
                ),
            }
        )
    every_old = [r for e in old["meetings"].values() for r in e["repeats"]]
    every_new = [r for e in new["meetings"].values() for r in e["repeats"]]
    rows.append({"meeting": "total", **delta(per_summary(every_old), per_summary(every_new))})
    return rows
