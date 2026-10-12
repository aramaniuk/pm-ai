"""Person handles: what a provider said about a person, as written to a line (AD-34).

A provider's account is not a person. One person has a Graph account, a commit
email and a calendar address, and the pm-ai person is what joins them. So an
observed event's line records **what the provider said** — a handle
`<system>:<handle>` — and a reader decides **who that is**, through the one
mapping function over the alias table, `person_for` (story 2o). The line is
never rewritten, so an alias added or corrected later applies to every past line.
Which handles are the PM is the one function `pm_identity`, below.

This module is the only place a `PersonHandle` is constructed. Before it, two
connectors called `resolve_actor` in three places, each choosing its own system
name and key, and every actor reached the line as the bare `actor_unresolved`
because only tests filled the alias table — the provider's address was lost.
`tests/architecture/test_handle_construction.py` scans the rest of `pm_ai/` for a
`PersonHandle(...)` call or a `dataclasses.replace` on one.

**The handle systems are closed**, each with its own shape check:

- `graph` — a Graph user id, a GUID;
- `graph_app` — a Graph application id, a GUID: a bot sender, never mapped to a
  person;
- `email` — an address: surrounding whitespace stripped, then exactly one `@`
  with text on both sides; no whitespace, comma, colon, double quote or control
  character; ASCII only, until a slice meets a non-ASCII address;
- `none` — the empty handle only, written `none:`. The one absence value, for an
  observed event with no person behind it. Legal only as an envelope actor.

Every handle is lowercased. For `email` that lowercases the local part too,
which the standard makes case-sensitive and practice does not.

`parse_person_handle` is what the payload decoder reads a person field
through. The envelope `actor=` column of an observed line is deliberately not
parsed back through it when a line is read (story 2n): a reader gets the text.

The set is kept apart from the scopeless reference systems (`meeting`, `goal`),
checked at import by a `raise` so the guard holds under `python -O`.

Imports only sibling `pm_ai.domain` modules (AD-30), so connector rows arrive
here as plain mappings.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from pm_ai.domain.connector_rows import USER_ID_KEY
from pm_ai.domain.identity import SCOPELESS_SYSTEMS, Actor
from pm_ai.domain.invariants import InconsistentModel

__all__ = [
    "ALIASES",
    "EMAIL",
    "GRAPH",
    "GRAPH_APP",
    "HANDLE_SYSTEMS",
    "NEVER_A_PERSON",
    "NONE",
    "NO_PERSON",
    "UNRESOLVED_SENTINEL",
    "AliasRefused",
    "InconsistentHandleModel",
    "InvalidPersonHandle",
    "PersonHandle",
    "own_handle_set",
    "parse_person_handle",
    "person_for",
    "person_handle",
    "pm_identity",
    "register_alias",
    "usable_handle",
    "usable_user_id",
]

GRAPH = "graph"
GRAPH_APP = "graph_app"
EMAIL = "email"
NONE = "none"

HANDLE_SYSTEMS: frozenset[str] = frozenset({GRAPH, GRAPH_APP, EMAIL, NONE})
"""Every system a person handle may name. Closed: adding one is a deliberate
change here, with its handle kind and case rule stated (AD-34)."""

_SEPARATOR = ":"

UNRESOLVED_SENTINEL = "actor_unresolved"
"""The bare sentinel `pm_ai.domain.identity` once resolved an unknown handle to.

Nothing produces it since story 2o removed that code; it is kept so a line still
carrying it is refused by name (`parse_person_handle`) rather than as text with
no system, and so tests can name the value the parser refuses."""


class InvalidPersonHandle(ValueError):
    """A value the producer will not build a handle from. Names the value."""


class InconsistentHandleModel(InconsistentModel):
    """The handle systems contradict another declared set, at import."""


# ── Shapes ───────────────────────────────────────────────────────────────────

_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_EMAIL_REFUSED = frozenset(',:"')


def _guid(system: str, raw: str) -> str:
    handle = raw.lower()
    if not raw.isascii() or not _GUID.fullmatch(handle):
        raise InvalidPersonHandle(
            f"{system}:{raw!r} is not a GUID. A `{system}` handle is the id "
            f"Microsoft Graph generates, `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` "
            f"in hex."
        )
    return handle


def _email(system: str, raw: str) -> str:
    address = raw.strip()
    if not address.isascii():
        raise InvalidPersonHandle(
            f"{system}:{raw!r} is not ASCII. Non-ASCII addresses are refused "
            f"until a slice meets one and decides how they are compared."
        )
    bad = sorted(
        {ch for ch in address if ch.isspace() or ord(ch) < 32 or ord(ch) == 127}
        | (set(address) & _EMAIL_REFUSED)
    )
    if bad:
        raise InvalidPersonHandle(
            f"{system}:{raw!r} carries {bad!r}. An address handle holds no "
            f"whitespace, comma, colon, double quote or control character — a "
            f"comma would split an attendee list and a colon would split the "
            f"handle from its system."
        )
    local, at, domain = address.partition("@")
    if not at or "@" in domain or not local or not domain:
        raise InvalidPersonHandle(
            f"{system}:{raw!r} is not an address: it needs exactly one `@` with "
            f"text on both sides."
        )
    return address.lower()


def _none(system: str, raw: str) -> str:
    if raw != "":
        raise InvalidPersonHandle(
            f"{system}:{raw!r} — the absence value is `none:` with an empty "
            f"handle. A `none` handle that names something is not absence."
        )
    return ""


_SHAPES: dict[str, Callable[[str, str], str]] = {
    GRAPH: _guid,
    GRAPH_APP: _guid,
    EMAIL: _email,
    NONE: _none,
}


def _assert_handle_systems_agree() -> None:
    """A `raise`, not an `assert`, so `python -O` cannot switch it off.

    Reads the module globals, so a test can doctor `HANDLE_SYSTEMS` and re-run
    it — the only way to prove the guard fires under `-O`.
    """
    overlap = HANDLE_SYSTEMS & SCOPELESS_SYSTEMS
    if overlap:
        raise InconsistentHandleModel(
            f"{sorted(overlap)} name both a person-handle system and a scopeless "
            f"reference system. `meeting:m1` must never read as a person, and a "
            f"person must never read as a citation (AD-34)."
        )
    unshaped = sorted(HANDLE_SYSTEMS ^ set(_SHAPES))
    if unshaped:
        raise InconsistentHandleModel(
            f"{unshaped} are not both a handle system and a shape check. Every "
            f"system states the shape of its handle, and no shape exists for a "
            f"system that is not declared."
        )


_assert_handle_systems_agree()


# ── The type and its producer ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PersonHandle:
    """What a provider said about a person: `<system>:<handle>`, canonical.

    Built only by `person_handle` and `parse_person_handle` in this module. The
    construction check re-runs the shape check, so a value built any other way
    is still refused when it is not canonical.
    """

    system: str
    handle: str

    def __post_init__(self) -> None:
        if self.system not in HANDLE_SYSTEMS:
            raise InvalidPersonHandle(_unknown_system(f"{self.system}:{self.handle}", self.system))
        if _SHAPES[self.system](self.system, self.handle) != self.handle:
            raise InvalidPersonHandle(
                f"{self.system}:{self.handle!r} is not in canonical form. Build "
                f"handles through `person_handle`, which lowercases and strips."
            )

    @property
    def is_absent(self) -> bool:
        """True for `none:` — an observed event with no person behind it."""
        return self.system == NONE

    def __str__(self) -> str:
        return f"{self.system}{_SEPARATOR}{self.handle}"


def _unknown_system(value: str, system: str) -> str:
    if system in SCOPELESS_SYSTEMS:
        return (
            f"{value!r} is a `{system}:` reference, not a person. Handle systems "
            f"are {sorted(HANDLE_SYSTEMS)}."
        )
    return (
        f"{value!r} names the system {system!r}, which is not a handle system. "
        f"The set is closed: {sorted(HANDLE_SYSTEMS)}. A new one is added in "
        f"`pm_ai.domain.handles` with its handle kind stated (AD-34)."
    )


def person_handle(system: str, handle: str) -> PersonHandle:
    """The producer: one provider value as a canonical handle, or a refusal.

    Lowercases every handle; strips surrounding whitespace from an address.
    Refuses an unknown system, a `:` inside the handle, and a handle of the
    wrong shape, each with an `InvalidPersonHandle` naming the value.
    """
    if not isinstance(system, str) or not isinstance(handle, str):
        raise InvalidPersonHandle(
            f"({system!r}, {handle!r}) is not a system and a handle, both text."
        )
    if system not in HANDLE_SYSTEMS:
        raise InvalidPersonHandle(_unknown_system(f"{system}:{handle}", system))
    if _SEPARATOR in handle:
        raise InvalidPersonHandle(
            f"{system}:{handle!r} has a `:` inside the handle. The first `:` "
            f"separates the system, so a second one is a handle nothing can read "
            f"back unambiguously."
        )
    return PersonHandle(system, _SHAPES[system](system, handle))


NO_PERSON: PersonHandle = person_handle(NONE, "")
"""`none:` — the producer's absence value. Legal only as an envelope actor."""


def parse_person_handle(text: str) -> PersonHandle:
    """A handle's text form back into the handle — canonical form only.

    What the decoder reads a person field through. Refuses a display name, the
    bare `actor_unresolved`, text with no system, an unknown system
    (`unresolved:` and the scopeless references included), and any text that is
    not exactly the canonical form the producer writes — `email:Dana@X.com` is a
    corrupted line, so a round trip stays byte-stable.
    """
    if not isinstance(text, str):
        raise InvalidPersonHandle(f"{text!r} is not text, so it is not a handle.")
    if text == UNRESOLVED_SENTINEL:
        raise InvalidPersonHandle(
            f"{text!r} is the bare unresolved sentinel. A line records what the "
            f"provider said, as `<system>:<handle>`, or `none:` when it said "
            f"nothing (AD-34)."
        )
    system, separator, handle = text.partition(_SEPARATOR)
    if not separator:
        raise InvalidPersonHandle(
            f"{text!r} names no system. A person on a line is `<system>:<handle>` "
            f"— a raw string used as identity is how one person becomes several "
            f"(AD-34)."
        )
    built = person_handle(system, handle)
    if str(built) != text:
        raise InvalidPersonHandle(
            f"{text!r} is not in canonical form; the producer writes "
            f"{str(built)!r}."
        )
    return built


def usable_handle(system: str, raw: str | None) -> PersonHandle | None:
    """The handle for a provider value, or `None` when there is no usable one.

    For connectors: an address the producer refuses counts as no address, so a
    malformed provider value costs the person, never the row or the harvest.
    Never answers `none:` — the caller decides what absence means where it is.
    """
    if raw is None:
        return None
    try:
        handle = person_handle(system, raw)
    except InvalidPersonHandle:
        return None
    return None if handle.is_absent else handle


def own_handle_set(handles: Iterable[PersonHandle]) -> frozenset[PersonHandle]:
    """pm-ai's own bot handles (AD-36 mechanism 3), checked.

    Each member is a `PersonHandle` and none is `none:` — absence is nobody, and
    marking every unattributed event `pm_ai` would hide real activity.
    """
    if isinstance(handles, str):
        raise InvalidPersonHandle(
            f"own_handles is the string {handles!r}, not a set of handles. A "
            f"string iterates as its characters, so it is refused by name."
        )
    out: set[PersonHandle] = set()
    for handle in handles:
        if not isinstance(handle, PersonHandle):
            raise InvalidPersonHandle(
                f"own_handles holds {handle!r}, which is not a PersonHandle."
            )
        if handle.is_absent:
            raise InvalidPersonHandle(
                "own_handles holds `none:`. The absence value is nobody, so it "
                "cannot be one of pm-ai's own accounts."
            )
        out.add(handle)
    return frozenset(out)


# ── People: who a handle is, decided when a line is read (story 2o) ─────────
#
# AD-34: a person is resolved when a line is read, never when it is written.
# Two functions are the only way from a handle to a person: `person_for` maps
# one handle through the alias table, and `pm_identity` answers which handles
# are the PM. `tests/architecture/test_people_readers.py` keeps `register_alias`
# the one writer of `ALIASES`, `person_for` its one reader, and
# `pm_ai.app.wiring.read_pm_identity` the one caller of `pm_identity`.

NEVER_A_PERSON: frozenset[str] = frozenset({GRAPH_APP, NONE})
"""Handle systems no alias maps to a person: a bot sender, and absence."""

ALIASES: dict[PersonHandle, Actor] = {}
"""Handle to pm-ai person. A module dict that only tests fill.

Not saved anywhere: the slice that saves the table owns its home, its row
shape, and correcting an alias (Open Risks, AD-34). Until then nothing in
`pm_ai/` registers an alias, so every handle reads as written.
"""


class AliasRefused(ValueError):
    """`register_alias` will not store this mapping. Names the reason."""


def _require_handle(value: object, *, where: str) -> PersonHandle:
    if not isinstance(value, PersonHandle):
        raise TypeError(
            f"{where} takes a PersonHandle, not {value!r}. A person value read "
            f"from a line is parsed with `parse_person_handle` first."
        )
    return value


def register_alias(handle: PersonHandle, person: Actor) -> None:
    """Map `handle` to the pm-ai person `person`.

    Refused, storing nothing, with `AliasRefused` for a `graph_app` or `none`
    handle (naming the system: a bot sender and absence are never a person)
    and for a handle already mapped to a different person (naming both).
    Registering the mapping the table already holds changes nothing.
    Correcting an alias belongs to the slice that saves the table.
    """
    handle = _require_handle(handle, where="register_alias")
    if not isinstance(person, Actor):
        raise TypeError(f"register_alias maps a handle to an Actor, not {person!r}.")
    if handle.system in NEVER_A_PERSON:
        raise AliasRefused(
            f"{handle} is a `{handle.system}` handle, which never maps to a "
            f"person: `graph_app` is a bot sender and `none` is absence (AD-34). "
            f"Nothing was stored."
        )
    held = ALIASES.get(handle)
    if held is not None and held != person:
        raise AliasRefused(
            f"{handle} is already mapped to {held.actor_id!r}; mapping it to "
            f"{person.actor_id!r} would change who every past line names. "
            f"Correcting an alias belongs to the slice that saves the table. "
            f"{held.actor_id!r} was kept."
        )
    ALIASES[handle] = person


def person_for(handle: PersonHandle) -> Actor | None:
    """The pm-ai person `handle` is, or `None` for "no known person".

    A reader given `None` shows the handle as written. `graph_app` and `none`
    handles answer `None` whatever the table holds — this does not rely on
    `register_alias` having refused them. Nothing rewrites a line, so an alias
    added later applies to every past line.

    A reader holding a `mentions` member parses it with `parse_person_handle`
    before asking. Self-action lines (actor `pm-ai`) are never asked about: a
    reader tells them apart by the entry's vocabulary, not by this function.
    """
    handle = _require_handle(handle, where="person_for")
    if handle.system in NEVER_A_PERSON:
        return None
    return ALIASES.get(handle)


# ── The PM ───────────────────────────────────────────────────────────────────

def usable_user_id(row: Mapping[str, object]) -> str | None:
    """The user id a connector row records, or `None` when it records none.

    The one rule every reader of `USER_ID_KEY` applies (story 2o): a value that
    is not text, is blank, or that the producer refuses as a Graph user id is
    absent. Surrounding whitespace is stripped before the producer is asked,
    and the stripped text is returned as the provider cased it.
    """
    value = row.get(USER_ID_KEY)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or usable_handle(GRAPH, stripped) is None:
        return None
    return stripped


def pm_identity(
    rows: Iterable[Mapping[str, object]], aliased: Iterable[PersonHandle]
) -> frozenset[PersonHandle]:
    """The handles that are the PM (AD-34).

    `graph:<user_id>` for every row whose `system` is exactly `"graph"`,
    enabled or disabled — disabling a row does not unmark past lines — plus
    every handle in `aliased`. A row with no usable user id, and any row that
    is not a Graph row, adds nothing. An `aliased` handle of system `graph_app`
    or `none` is refused with `AliasRefused`, naming it.

    `aliased` is the alias table's handles for the PM's person once that table
    is saved; until then the composition root passes an empty set. Called only
    by `pm_ai.app.wiring.read_pm_identity`, which reads the rows.
    """
    if isinstance(rows, Mapping):
        raise TypeError(
            "pm_identity takes the connector rows, not one row: a mapping "
            "iterates as its keys."
        )
    out: set[PersonHandle] = set()
    for handle in aliased:
        handle = _require_handle(handle, where="pm_identity's aliased")
        if handle.system in NEVER_A_PERSON:
            raise AliasRefused(
                f"pm_identity's aliased holds {handle}, a `{handle.system}` "
                f"handle: a bot sender or absence is never the PM (AD-34)."
            )
        out.add(handle)
    for row in rows:
        # Exactly `"graph"`: a row's `system` names its connector family, which
        # is spelled like the handle system it signs in as. `"Graph"` is not it.
        if row.get("system") != GRAPH:
            continue
        user_id = usable_user_id(row)
        if user_id is None:
            continue
        out.add(person_handle(GRAPH, user_id))
    return frozenset(out)
