"""`projects.toml` — the only way a repository enters the system (AD-11).

Every project pm-ai acts on is here because somebody ran `pm-ai project add`.
Nothing scans for `.project-ai/` directories, because a scan would opt a
colleague's checkout into telemetry harvesting without anyone having asked.

This module parses and renders bytes and **opens nothing**, the same split
`4a` established for `config.toml`: `StorageService` is the single reader and
the single writer (AD-5), `pm_ai.app.wiring` performs the read, and what
arrives here is a buffer. That is why a relative path is *refused* rather than
resolved — resolving one needs a working directory, and `core` has none.

## Losing this file forgets every project

`projects.toml` is Tier 1 and rebuildable from nothing (`scope_model.py:440`).
So the refusals below never degrade to an empty mapping: a registry that parses
to empty is a registry the next `project add` mints over, and the entries it
replaced are not recoverable from any other artifact. Absent and empty are the
two states that legitimately mean "no projects"; everything else raises.

## `render_registry` takes the whole mapping

`write_artifact` replaces a file whole (`service.py:1002`), so an interface
accepting a single entry would invite the write that publishes one project and
silently drops the rest. Taking the mapping means a caller cannot append
without having read.

## Scope

No command and no filesystem — `4k` owns `pm-ai project add`, id derivation,
directory creation and the exclusive read-modify-write. No removal, and no id
rename: the id is the scope directory name, so changing it would move every
artifact and stale every `SourceRef`. Only the alias may change, and it is a
display label.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DuplicateProject",
    "ProjectEntry",
    "ProjectPathUnusable",
    "RegistryMalformed",
    "RegistryRefused",
    "parse_registry",
    "render_registry",
]

_TABLE = "projects"
_PATH_KEY = "path"
_ALIAS_KEY = "alias"
_ENTRY_KEYS = frozenset({_PATH_KEY, _ALIAS_KEY})
_BOM = b"\xef\xbb\xbf"

HEADER = (
    "# pm-ai project registry. Written by `pm-ai project add` (AD-11).\n"
    "#\n"
    "# Every project pm-ai acts on is listed here, and nothing else is. This\n"
    "# file is not rebuildable from any other artifact: lose it and every\n"
    "# enrolled project is forgotten, so it is safe to read and to back up,\n"
    "# and unsafe to edit into a state that will not parse.\n"
    "#\n"
    "# `path` is the repository, always absolute. `alias` is a display label\n"
    "# and may be omitted.\n"
)


@dataclass(frozen=True, slots=True)
class ProjectEntry:
    """One enrolled project: where its repository is, and what to call it.

    The id is not a field — it is the mapping key, and it is the scope directory
    name, which is what makes it unrenameable. `alias` is `None` rather than
    `""` when unset, because "no label" and "an empty label" would otherwise
    render identically and only one of them is a state `4k` can produce.
    """

    path: Path
    alias: str | None = None


class RegistryRefused(ValueError):
    """`projects.toml` says something this daemon will not act on.

    A `ValueError`, matching `ConfigRefused`, so a caller catching one catches
    these. The three subclasses below are the distinctions an operator needs:
    the file did not parse, a path in it cannot be used, or two projects claim
    one label. They have three different fixes.
    """


class RegistryMalformed(RegistryRefused):
    """The file did not parse, or parsed into a shape that is not a registry."""


class ProjectPathUnusable(RegistryRefused):
    """A path parsed but cannot locate a repository — today, a relative one."""


class DuplicateProject(RegistryRefused):
    """Two projects claim one alias.

    The only duplicate this module can see. Ids are the mapping's keys and are
    unique by construction, and a *second path under one id* cannot be expressed
    as input at all — that is a move, which `4k`'s read-modify-write meets.
    """


# ── Parsing ──────────────────────────────────────────────────────────────────


def parse_registry(raw: bytes | None) -> Mapping[str, ProjectEntry]:
    """The enrolled projects, from the bytes a caller read.

    `None` is an absent file and parses to an empty mapping — a first run, not a
    failure. So does a present file with no entries. Every other disagreement
    raises, for the reason in this module's docstring.

    Absence is the *caller's* distinction to report: `doctor` separates "no file"
    from "reachable, nothing stored" because they read differently to an
    operator, while to the parser they are one answer.
    """
    if raw is None:
        return {}
    document = _parse(_decode(raw))
    table = document.get(_TABLE, {})
    if not isinstance(table, dict):
        raise RegistryMalformed(
            f"projects.toml's `[{_TABLE}]` must be a table of projects, not "
            f"{_type_name(table)}. Each project is its own table: "
            f"`[{_TABLE}.<id>]`."
        )
    return {project_id: _entry(project_id, body) for project_id, body in table.items()}


def _entry(project_id: str, body: object) -> ProjectEntry:
    """One `[projects.<id>]` table, checked key by key.

    Unknown keys are refused rather than ignored, which is `4a`'s rule on the
    other TOML file and exists for the same reason: a typo'd `pathh` beside no
    `path` reads as a project with no location, and a key from a future version
    of this file reads as a setting that silently has no effect.
    """
    if not isinstance(body, dict):
        raise RegistryMalformed(
            f"project {project_id!r} in projects.toml must be a table with a "
            f"`{_PATH_KEY}`, not {_type_name(body)}."
        )
    unknown = sorted(set(body) - _ENTRY_KEYS)
    if unknown:
        raise RegistryMalformed(
            f"project {project_id!r} in projects.toml has no use for "
            f"{', '.join(repr(k) for k in unknown)}. Accepted: "
            f"{', '.join(sorted(_ENTRY_KEYS))}."
        )
    if _PATH_KEY not in body:
        raise RegistryMalformed(
            f"project {project_id!r} in projects.toml has no `{_PATH_KEY}`, so "
            f"nothing locates its repository."
        )
    raw_path = body[_PATH_KEY]
    if not isinstance(raw_path, str):
        raise RegistryMalformed(
            f"project {project_id!r}'s `{_PATH_KEY}` must be a string, not "
            f"{_type_name(raw_path)}."
        )
    alias = body.get(_ALIAS_KEY)
    if alias is not None and not isinstance(alias, str):
        raise RegistryMalformed(
            f"project {project_id!r}'s `{_ALIAS_KEY}` must be a string, not "
            f"{_type_name(alias)}."
        )
    return ProjectEntry(path=_absolute(project_id, Path(raw_path)), alias=alias)


def _absolute(project_id: str, path: Path) -> Path:
    """Refused, never resolved — the one rule both directions share.

    `ScopePaths._absolute_map` takes what it is given (`paths.py:479`), so a
    relative path here would be joined against whatever directory the daemon
    happened to start in. Resolving it would need a working directory, which is
    exactly the ambient state `core` is forbidden.
    """
    if not path.is_absolute():
        raise ProjectPathUnusable(
            f"project {project_id!r} names a relative path ({str(path)!r}). A "
            f"registry entry is absolute or it means a different directory to "
            f"every process that reads it — pm-ai will not guess which one was "
            f"meant."
        )
    return path


def _decode(raw: bytes) -> str:
    """UTF-8, with an editor-added BOM tolerated.

    Stripped before parsing rather than passed through: `tomllib` reads a BOM as
    part of the first key, so a perfectly valid file would otherwise be refused
    as malformed. Reported against the file rather than the sliced buffer, since
    dropping the BOM shifts every offset by three.
    """
    stripped = raw.startswith(_BOM)
    if stripped:
        raw = raw[len(_BOM) :]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        offset = exc.start + (len(_BOM) if stripped else 0)
        raise RegistryMalformed(
            f"projects.toml is not valid UTF-8 — byte {offset} is not part of a "
            f"legal sequence ({exc.reason}). This is an encoding problem, not a "
            f"syntax one: re-save the file as UTF-8."
        ) from exc


def _parse(text: str) -> Mapping[str, object]:
    """`tomllib`, with its message kept.

    The duplicate-id rule is enforced here and nowhere else, deliberately.
    Measured 2026-09-15: `tomllib` raises `Cannot declare ('projects', 'alpha')
    twice` for a repeated table and `Cannot overwrite a value` for a repeated
    key, so a duplicate can never reach the mapping as a last-wins overwrite. A
    check of our own would be dead code asserting what the parser guarantees;
    `tests/core/test_project_registry.py` pins the behaviour instead.
    """
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RegistryMalformed(f"projects.toml is not valid TOML: {exc}") from exc
    except RecursionError as exc:
        raise RegistryMalformed(
            f"projects.toml nests too deeply for the parser to read it ({exc})."
        ) from exc


def _type_name(value: object) -> str:
    return type(value).__name__


# ── Rendering ────────────────────────────────────────────────────────────────


def render_registry(projects: Mapping[str, ProjectEntry]) -> bytes:
    """The whole file's bytes for the whole mapping, for a caller that writes it.

    Returns bytes and opens nothing, the mirror of `parse_registry`. The two are
    a drift pair — `parse_registry(render_registry(m)) == m` for every
    admissible mapping — which is the guarantee `4g` established for
    `config.toml` and the only one that makes a hand-editable Tier-1 file safe
    to rewrite.

    The refusals run *here as well as* in the parser. A rule checked only on the
    way in is a rule the writer can break, and `4k` will call this with a path
    it derived rather than one a human typed.
    """
    _refuse_colliding_aliases(projects)
    body = "".join(
        _table(project_id, projects[project_id]) for project_id in sorted(projects)
    )
    # An empty registry renders the preamble and nothing else, so "no projects
    # are enrolled" is visible in the bytes rather than inferred from silence.
    document = HEADER if not body else f"{HEADER}{body}"
    return document.encode("utf-8")


def _table(project_id: str, entry: ProjectEntry) -> str:
    _absolute(project_id, entry.path)
    lines = [f"\n[{_TABLE}.{_key(project_id)}]", f"{_PATH_KEY} = {_string(str(entry.path))}"]
    if entry.alias is not None:
        lines.append(f"{_ALIAS_KEY} = {_string(entry.alias)}")
    return "\n".join(lines) + "\n"


def _key(project_id: str) -> str:
    """A bare key where TOML allows one, a quoted key otherwise.

    Ids are directory names, so the bare form covers every id `4k` can derive.
    The quoted fallback is what keeps the round trip total rather than true only
    for the ids we expect.
    """
    bare = project_id and all(c.isalnum() and c.isascii() or c in "-_" for c in project_id)
    return project_id if bare else _string(project_id)


# The TOML basic-string escapes, and U+007F — a control character TOML forbids
# raw and which has no short escape, so it reaches the file as `\u007F` or not
# at all.
#
# A second copy of `config.py:397-410`, deliberately and unhappily. Sharing them
# would mean either importing a private name across modules or moving both to a
# third module that `config.py` then imports — and `4a`'s import sweep names
# every module `config.py` may reach, so that move is an edit to `4a`'s guard
# inside `4d`'s slice. Recorded in `deferred-work.md` instead: the escape set is
# fixed by the TOML specification and both directions are covered by a
# round-trip test, so the drift this risks is small and visible.
_ESCAPES = {
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
    '"': '\\"',
    "\\": "\\\\",
}
_DELETE = "\x7f"


def _string(value: str) -> str:
    """A TOML basic string, with every character the format forbids escaped.

    Written out rather than borrowed from `json.dumps`, for `4a`'s reason: JSON
    escapes every non-ASCII character by default, and a project alias spelled
    back as `\\u00e9` in a file whose whole promise is that a human can edit it
    is a worse file for no gain. Everything at U+0080 and above passes through
    as itself — the file is UTF-8.

    Built by accumulation rather than by chained `str.replace`, which is not
    style: `4d`'s no-filesystem sweep matches the *name* `replace`, because
    `os.replace` is how a file is published, and it cannot tell that one from
    this one. The loop is what makes the guard able to stay strict.
    """
    out = ['"']
    for character in value:
        escape = _ESCAPES.get(character)
        if escape is not None:
            out.append(escape)
        elif character < " " or character == _DELETE:
            out.append(f"\\u{ord(character):04X}")
        else:
            out.append(character)
    out.append('"')
    return "".join(out)


def _refuse_colliding_aliases(projects: Mapping[str, ProjectEntry]) -> None:
    """Two projects answering to one label, named in both directions.

    Refused rather than resolved by precedence: an alias is how a human will
    select a project, and a rule like "the first one wins" makes the selection
    depend on mapping order, which is not something anybody can see in the file.
    """
    claimed: dict[str, str] = {}
    for project_id in sorted(projects):
        alias = projects[project_id].alias
        if alias is None:
            continue
        held = claimed.get(alias)
        if held is not None:
            raise DuplicateProject(
                f"projects {held!r} and {project_id!r} both claim the alias "
                f"{alias!r}. An alias is how a project is named to a human, so "
                f"two projects cannot share one — rename one of them; the id is "
                f"unaffected."
            )
        claimed[alias] = project_id
