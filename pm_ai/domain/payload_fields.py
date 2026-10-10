"""The one encode/decode pair between a payload and its `p.` line fields (AD-27).

The writer put payload fields under a `p.` prefix while the dashboard read them
without it, and nothing checked that the two agreed: a missing field reads as
"not recorded", so the first real message would have rendered as an actor and
nothing else, with no error. This module is the only place a payload field's
line name is spelled. The writer encodes through it, every reader decodes
through it and reads the attribute — a misspelled attribute fails before
anything runs, where a misspelled lookup read as empty.
`tests/architecture/test_payload_lookups.py` scans the rest of `pm_ai/` for the
shapes such a lookup takes — a `p.` literal, and a payload field name used as a
key on, or compared against, an entry's `fields` — and names any it finds.

**The grammar, per field type** (resolved from the payload class, never from
annotation text):

- text — the value as it is;
- a count (`int`) — ASCII digits; a negative count is refused at encode, and
  anything but digits is refused at decode;
- a person (`Actor`) — its `actor_id` alone (AD-34). The display name is an
  alias and never reaches a line, so decoding gives a person with that id and
  no name — the one deliberate loss the round trip allows. A person pm-ai could
  not name carries the handle it came with under the reserved spelling
  `unresolved:<system>:<handle>`, which passes through unchanged; the bare
  `actor_unresolved` sentinel and an empty id are refused both ways — at encode
  as an unencodable payload, at decode as a corrupted line — because neither
  keeps anything a later alias entry could resolve;
- a list (`tuple[str, ...] | None`) — its elements joined by `,`, with `,` and
  `\\` inside an element escaped by `\\`. An empty element is refused at encode.
  The list layer sits inside the line layer: the joined text is one value that
  `render_entry` then quotes and escapes by its own rules, and a reader undoes
  the line layer first (`scan_fields`), then splits here.

**Absent and empty differ.** `None` is left off the line — "not recorded". An
empty list is a present, empty value. On decode, a missing field with a default
takes its default, a missing field without one is refused naming it, a field
appearing twice is refused naming it, and a `p.` field the class does not know
is ignored: the grammar only grows (AD-27), so a line from a later build stays
readable here. Every decode refusal also names the entry — its id and its
`ingested_at` — so the line can be found by hand.

Imports `events` and `event_entries`; the reverse would be a cycle. Deliberately
not re-exported from `pm_ai.domain` — that aggregate's docstring says why.
"""

from __future__ import annotations

import re
from dataclasses import MISSING, fields, is_dataclass
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints

from pm_ai.domain.event_entries import (
    UNKNOWN_VALUE,
    EventEntry,
    MalformedEntry,
    SelfActionType,
)
from pm_ai.domain.events import PAYLOAD_FOR, ObservedEventType
from pm_ai.domain.identity import UNRESOLVED_ACTOR, Actor

__all__ = [
    "PREFIX",
    "LIST_SEPARATOR",
    "NoPayload",
    "UnencodablePayload",
    "decode_payload",
    "encode_payload",
    "line_name",
]

PREFIX = "p."
"""What sets a payload field apart from an envelope field on a line.

Spelled here and nowhere else in `pm_ai/`. The envelope names and the payload
names are disjoint today; the prefix is what stops that being luck.
"""

LIST_SEPARATOR = ","
_LIST_ESCAPE = "\\"
_DIGITS = re.compile(r"[0-9]+")


class UnencodablePayload(MalformedEntry):
    """A payload value the line grammar cannot carry, refused before writing.

    A `MalformedEntry`, because it is one: a record that cannot be rendered
    without corrupting the ledger. Names the field it refused.
    """


class NoPayload(ValueError):
    """Decoding was asked of an entry whose category binds no payload.

    A pm-ai action (`SelfActionType`) records named fields, not a payload, so
    there is nothing to decode — and answering with a stray `KeyError` or with
    `None` would make the caller's mistake read as an empty record.
    """


def line_name(field_name: str) -> str:
    """The name a payload field carries on a line."""
    return f"{PREFIX}{field_name}"


# ── Field kinds ──────────────────────────────────────────────────────────────

_TEXT = "text"
_COUNT = "count"
_PERSON = "person"
_LIST = "list"


def _kind(owner: type, name: str, hint: object) -> str:
    """Which of the four grammars a resolved annotation takes."""
    if get_origin(hint) in (Union, UnionType):
        members = [a for a in get_args(hint) if a is not type(None)]
    else:
        members = [hint]
    if len(members) == 1:
        (member,) = members
        if member is str:
            return _TEXT
        if member is int:
            return _COUNT
        if member is Actor:
            return _PERSON
        if get_origin(member) is tuple and get_args(member) == (str, ...):
            return _LIST
    raise TypeError(
        f"{owner.__name__}.{name} is typed {hint!r}, which the event-log line "
        f"grammar has no form for. A new field type is a grammar change: give it "
        f"a form here and a row in the pin table (AD-27)."
    )


_LAYOUTS: dict[type, tuple[tuple[str, str, bool], ...]] = {}


def _layout(payload: type) -> tuple[tuple[str, str, bool], ...]:
    """Each field as `(name, kind, required)`, in declaration order."""
    if payload not in _LAYOUTS:
        if not is_dataclass(payload):
            raise TypeError(f"{payload.__name__} is not a payload dataclass.")
        hints = get_type_hints(payload)
        _LAYOUTS[payload] = tuple(
            (
                f.name,
                _kind(payload, f.name, hints[f.name]),
                f.default is MISSING and f.default_factory is MISSING,
            )
            for f in fields(payload)
        )
    return _LAYOUTS[payload]


# ── Encode ───────────────────────────────────────────────────────────────────


def encode_payload(payload: object) -> tuple[tuple[str, str], ...]:
    """The payload as `p.`-prefixed line fields, in declaration order.

    `None` is left off the line. Everything else is rendered by its field's
    grammar; a value the grammar refuses raises `UnencodablePayload` naming the
    field.
    """
    out: list[tuple[str, str]] = []
    for name, kind, _required in _layout(type(payload)):
        value = getattr(payload, name)
        if value is None:
            continue
        out.append((line_name(name), _encode_value(name, kind, value)))
    return tuple(out)


def _encode_value(name: str, kind: str, value: object) -> str:
    if kind == _TEXT:
        if not isinstance(value, str):
            raise UnencodablePayload(f"`{name}` holds {value!r}, which is not text.")
        return value
    if kind == _COUNT:
        if not isinstance(value, int) or isinstance(value, bool):
            raise UnencodablePayload(f"`{name}` holds {value!r}, which is not a count.")
        if value < 0:
            raise UnencodablePayload(
                f"`{name}` is {value}. A count is never negative, and a line "
                f"carries digits only."
            )
        return str(value)
    if kind == _PERSON:
        if not isinstance(value, Actor):
            raise UnencodablePayload(f"`{name}` holds {value!r}, which is not an Actor.")
        if not value.actor_id:
            raise UnencodablePayload(
                f"`{name}` holds a person with an empty id. A person on a line is "
                f"its id alone (AD-34), so an empty one records nobody."
            )
        _refuse_sentinel(name, value.actor_id)
        return value.actor_id
    # _LIST
    if not isinstance(value, tuple):
        raise UnencodablePayload(
            f"`{name}` holds {type(value).__name__}; a list field is a tuple of text."
        )
    return LIST_SEPARATOR.join(_encode_element(name, element) for element in value)


def _encode_element(name: str, element: object) -> str:
    if not isinstance(element, str):
        raise UnencodablePayload(
            f"`{name}` holds the element {element!r}, which is not text."
        )
    if not element:
        raise UnencodablePayload(
            f"`{name}` holds an empty element. An empty list and a list of one "
            f"empty string must never share bytes (AD-27), so an element may "
            f"not be empty."
        )
    _refuse_sentinel(name, element)
    return element.replace(_LIST_ESCAPE, _LIST_ESCAPE * 2).replace(
        LIST_SEPARATOR, _LIST_ESCAPE + LIST_SEPARATOR
    )


def _refuse_sentinel(name: str, identity: str) -> None:
    if identity == UNRESOLVED_ACTOR:
        raise UnencodablePayload(
            f"`{name}` holds the bare `{UNRESOLVED_ACTOR}` sentinel. A person "
            f"pm-ai could not name keeps the handle it came with, as "
            f"`unresolved:<system>:<handle>` (AD-27 / AD-34), so a later alias "
            f"entry can resolve it; the sentinel keeps nothing."
        )


# ── Decode ───────────────────────────────────────────────────────────────────


def decode_payload(entry: EventEntry) -> object:
    """The payload an observed-event entry carries, rebuilt from its `p.` fields.

    Refuses a pm-ai action, or a category with no registered payload, with
    `NoPayload` naming it; refuses a corrupted line with `MalformedEntry` naming
    the field and the entry (its id and `ingested_at`), so it can be found by
    hand in the segment.
    """
    if isinstance(entry.category, SelfActionType):
        raise NoPayload(
            f"`{entry.category.value}` is a pm-ai action and carries no payload; "
            f"its fields are read by name from `SELF_ACTION_FIELDS`."
        )
    payload = PAYLOAD_FOR.get(entry.category)
    if payload is None:
        raise NoPayload(
            f"`{entry.category.value}` has no payload registered in PAYLOAD_FOR, "
            f"so there is no shape to decode its fields into."
        )
    try:
        return _decode(entry, payload)
    except MalformedEntry as refusal:
        raise MalformedEntry(f"{_where(entry)}: {refusal}") from refusal


def _where(entry: EventEntry) -> str:
    """The entry, named well enough to find its line in a segment by hand."""
    ingested = next(
        (value for key, value in entry.fields if key == "ingested_at"), UNKNOWN_VALUE
    )
    return (
        f"entry {entry.entry_id or '<no id>'} ({entry.category.value}, "
        f"ingested_at={ingested})"
    )


def _decode(entry: EventEntry, payload: type) -> object:
    seen: dict[str, str] = {}
    for key, value in entry.fields:
        if not key.startswith(PREFIX):
            continue
        if key in seen:
            raise MalformedEntry(
                f"`{key}` appears twice on one `{entry.category.value}` line. A "
                f"later value would silently win over the earlier one."
            )
        seen[key] = value

    arguments: dict[str, object] = {}
    for name, kind, required in _layout(payload):
        key = line_name(name)
        if key not in seen:
            if required:
                raise MalformedEntry(
                    f"a `{entry.category.value}` line has no `{key}`, which "
                    f"{payload.__name__} cannot be built without."
                )
            continue
        arguments[name] = _decode_value(key, kind, seen[key])
    return payload(**arguments)


def _decode_value(key: str, kind: str, raw: str) -> object:
    if kind == _TEXT:
        return raw
    if kind == _COUNT:
        if not _DIGITS.fullmatch(raw):
            raise MalformedEntry(
                f"`{key}` is {raw!r}. A count is written as digits only."
            )
        try:
            return int(raw)
        except ValueError as refusal:
            # Python refuses to convert a digit string past its length limit;
            # a hand edit that long is a corrupted line like any other.
            raise MalformedEntry(
                f"`{key}` holds a count of {len(raw)} digits, which cannot be "
                f"read as a number."
            ) from refusal
    if kind == _PERSON:
        if not raw:
            raise MalformedEntry(
                f"`{key}` is empty. A person on a line is its id, and the writer "
                f"never writes an empty one."
            )
        _corrupt_if_sentinel(key, raw)
        return Actor(raw)
    elements = _split(key, raw)
    for element in elements:
        _corrupt_if_sentinel(key, element)
    return elements


def _corrupt_if_sentinel(key: str, identity: str) -> None:
    if identity == UNRESOLVED_ACTOR:
        raise MalformedEntry(
            f"`{key}` holds the bare `{UNRESOLVED_ACTOR}` sentinel, which the "
            f"writer never writes: an unresolved person is "
            f"`unresolved:<system>:<handle>` (AD-27 / AD-34)."
        )


def _split(key: str, raw: str) -> tuple[str, ...]:
    """Undo the list layer: split on unescaped `,`, then unescape each element."""
    if raw == "":
        return ()
    elements: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == _LIST_ESCAPE:
            if index + 1 >= len(raw):
                raise MalformedEntry(
                    f"`{key}` ends inside an escape: {raw!r} has a dangling `\\`."
                )
            following = raw[index + 1]
            if following not in (_LIST_ESCAPE, LIST_SEPARATOR):
                raise MalformedEntry(
                    f"`{key}` escapes {following!r} in {raw!r}. Only `,` and `\\` "
                    f"are escaped inside a list element."
                )
            current.append(following)
            index += 2
            continue
        if char == LIST_SEPARATOR:
            elements.append(_element(key, raw, current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    elements.append(_element(key, raw, current))
    return tuple(elements)


def _element(key: str, raw: str, chars: list[str]) -> str:
    if not chars:
        raise MalformedEntry(
            f"`{key}` holds an empty element in {raw!r} — a stray or trailing "
            f"separator. The writer never writes one."
        )
    return "".join(chars)
