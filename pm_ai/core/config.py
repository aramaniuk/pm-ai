"""`~/.pm-ai/config.toml`, interpreted and written — the only module that does either.

`config.toml` was declared as an application-scope Tier-1 artifact
(`scope_model.py:432`) and read by nothing: a promise the layout made and the
code did not keep. This module is the reader, and since story 4g the writer.

**It moves bytes and never opens a file, in either direction.** That is
structural rather than a promise — `load_config` takes `bytes | None`,
`render_config` returns `bytes`, and there is nothing here to open. `core` is
I/O-free by contract and `StorageService` is already the single reader and the
single writer, so the caller does the I/O and this module interprets and
serializes. Worth stating because no *inherited* gate would catch a file access
here: the single-writer AST sweep exempts read-mode opens, the import contracts
list only network and database clients, and the file-I/O rule is scoped to
`pm_ai.storage`. So this module has one of its own —
`test_static_rules.py::test_story_4a_the_config_module_neither_reads_nor_writes_a_file`
allowlists what it may import and names the read *and* write verbs, which is
why adding an import here is a deliberate act rather than an ordinary one.

Two refusals shape everything below.

**The encryption toggle may never live here.** `pm_ai.platform.environment` is
the only channel, deliberately: an environment variable dies with the process,
so restarting restores encryption unconditionally, while a config key is the
persistent switch somebody forgets. A key that looks like one is refused *by
name* and told where the setting does live — a separate clause from the
unknown-key sweep, because the two failures teach different things. An unknown
key is a typo; an encryption key is a deliberate attempt at the thing the
architecture forbids, and answering that with "unknown key" would be a shrug.

**An unknown key is refused, not ignored.** TOML readers usually ignore extras,
which is how `verbose_loging = true` reads as configured forever. The accepted
vocabulary is four keys, so a closed set costs nothing.

Absent and empty are both ordinary first-run states returning defaults — a
missing optional config is not a failure.

**`render_config` is the mirror of `load_config`, and neither opens a file.**
The renderer returns bytes and something above it writes them, exactly as the
loader takes bytes and opens nothing: `core` is I/O-free by contract, and a
second surface (Telegram, story 5) reaches adapters only through core (AD-30),
so serialization cannot live on either surface. Hand-editing stays supported —
AD-3's Tier-1 promise is that the file *can* be hand-edited, not that only a
human may write it — but a rewrite replaces the file whole (`config.toml` is
absent from `storage_tiers._APPEND_ONLY_KEYS`), so **comments in a hand-edited
file are not preserved**. `tomllib` reads and cannot write, round-tripping
comments needs a third-party parser this module refuses to add, and the
generated header says so in the file itself.

Two rules hold the pair together. `load_config(render_config(c)) == c` for
every admissible `Config` — a renderer and a parser are the classic drift pair,
which is why `ACCEPTED_KEYS` is derived from the dataclass rather than written
out twice. And **a key at its unset default is omitted, never emitted**: that
is policy rather than something the loader forces, because `_flag` accepts
`verbose_logging = false` quite happily, so only the renderer stands between an
operator and a file stating a setting they expect an effect from.
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "ACCEPTED_KEYS",
    "ENCRYPTION_KEY_FAMILY",
    "HEADER",
    "Config",
    "ConfigRefused",
    "load_config",
    "render_config",
]

# The environment variable's name is spelled out in the refusal message below,
# in prose, and deliberately *not* bound to a constant here.
# `pm_ai.platform.environment` is the only module permitted to name it in code —
# `test_doctor.py::test_the_environment_is_read_in_exactly_one_place` enforces
# that by AST — and this module could not import it anyway, since `platform`
# sits above `core` in the layer stack. `tests/core/test_config.py` asserts the
# refusal message still contains `environment.DISABLE_ENCRYPTION_VAR`, so a
# rename cannot leave this pointing at a variable nothing reads.

# Matched as a substring of any segment of a key's dotted path, case-folded, so
# `encryption_mode`, `[encryption]`, `settings.encrypt` and `disable_cipher` are
# all caught. Deliberately a family rather than an exact name: the point is to
# refuse the *attempt*, and someone reaching for a config key to turn encryption
# off will not guess the one spelling a denylist happened to hold.
ENCRYPTION_KEY_FAMILY = frozenset({"encrypt", "cipher", "crypto", "plaintext"})

_BOM = b"\xef\xbb\xbf"

# `config.toml`'s vocabulary is four flat scalars, so anything nested is
# refused by the sweeps below anyway — but only if the walk survives long enough
# to say so. `a.a.a…b = 1` is legal TOML that `tomllib` accepts up to 1000
# dotted parts, and walking that recursed until Python gave up: a
# `RecursionError` out of a module whose entire contract is refuse-or-return.
# Two orders of magnitude more than any accepted key needs.
_MAX_DEPTH = 8


class ConfigRefused(ValueError):
    """`config.toml` says something this daemon will not act on.

    Always raised in preference to ignoring a line. A setting that reads as
    configured while having no effect is the failure this module exists to
    prevent.

    Every message names the offending key *where there is a key to name*. Two
    refusals happen before any key exists — a file that is not UTF-8 and a file
    that is not TOML — and those can only report the position that failed.

    A `ValueError`, so a caller that catches one catches these too; `4c` maps it
    to a refusal exit code.
    """


def _has_surrogates(value: str) -> bool:
    """Whether `value` holds an unpaired surrogate, and so has no UTF-8 encoding.

    Asked by encoding rather than by code-point range, because the range is the
    easy half to get wrong: a valid astral character such as U+1F600 is a
    *pair* of surrogates in UTF-16 and a perfectly encodable single code point
    in Python, and a range check written from memory refuses it.
    """
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def _timezone_database_available() -> bool:
    """Whether this machine can resolve any IANA zone at all.

    Probed with `UTC`, which every timezone database holds, so a `False` here
    means the database is missing rather than that one name is wrong. It is the
    difference between two refusals an operator acts on completely differently:
    "install `tzdata`" and "you typed the zone wrong". `tzdata` is a `runtime`
    extra rather than a default dependency, so the first is an ordinary state
    on a base install and not an exotic one.

    Every exception is one fact — the database is unusable — which is the same
    judgement `connectors.graph.calendar.zone_of` makes at its own lookup.
    """
    try:
        ZoneInfo("UTC")
    except Exception:  # noqa: BLE001 — any failure here means the same thing
        return False
    return True


def _refuse_zone(value: object, cause: Exception) -> ConfigRefused:
    """The refusal for a `display_timezone` `ZoneInfo` would not accept.

    Three different failures wear the same exception, and the remedy differs
    for each, so the message is chosen rather than templated:

    - a non-string, which is an argument of the wrong type and not a zone at
      all — saying the database "does not hold" it would be false;
    - a name no database resolves *on a machine that has one*, which is a typo;
    - anything at all *on a machine with no timezone database*, where the
      advice "use an IANA zone name such as `Europe/Warsaw`" is already
      satisfied and useless. `tzdata` is a `runtime` extra, so this is the
      ordinary state of a base install.
    """
    if isinstance(cause, TypeError):
        return ConfigRefused(
            f"display_timezone must be a string naming an IANA zone, but it was "
            f"given {_type_name(value)} ({value!r}). Omit the key to leave it "
            f"unset."
        )
    if not _timezone_database_available():
        return ConfigRefused(
            f"display_timezone is {value!r}, and this machine has no timezone "
            f"database to resolve it against — `UTC` does not resolve either, so "
            f"this is a missing database rather than a wrong name. Install the "
            f"`tzdata` extra (`uv sync --extra runtime`). Refused rather than "
            f"assumed: a zone that cannot be resolved cannot decide which "
            f"meetings count as today."
        )
    return ConfigRefused(
        f"display_timezone is {value!r}, which this machine's timezone database "
        f"does not hold ({type(cause).__name__}). It must be an IANA zone name "
        f"such as `Europe/Warsaw` — the database is present and does not have "
        f"this one, so it is a name rather than a missing install. A typo here "
        f"does not fail, it silently shifts which meetings count as today. Omit "
        f"the key to leave it unset."
    )


@dataclass(frozen=True, slots=True)
class Config:
    """The wave-1 vocabulary. Every field's default is its unconfigured state.

    `blended_hourly_rate` at `0.0` and `pm_handle` at `""` are *unset*, not
    values: no `config.toml` may set either to those (see `_number`/`_text`),
    so "absent" and "explicitly zero" cannot be confused. Both fail in the safe
    direction — an unset rate reports a cost of zero rather than a plausible
    wrong figure, and an unset handle matches no speaker, so nothing spoken
    auto-executes (AD-32).

    `__post_init__` is what makes the paragraph above true of the class rather
    than only of the loader.
    """

    # CAP-3's Man-Hour Cost: `Meeting.man_hour_cost` takes this and, until now,
    # nothing supplied it.
    blended_hourly_rate: float = 0.0
    # `extract()` takes this. Hardcoded as a literal in `wiring.py` until this
    # module existed.
    pm_handle: str = ""
    # The one setting `pm_ai.platform.environment`'s docstring explicitly
    # sanctions for this file, in the same breath as refusing the encryption
    # toggle: "Verbose logging may live there; encryption may not."
    verbose_logging: bool = False
    # The fourth and last key, reserved to the human by `4a` and answered on
    # 2026-09-03. `23a`'s renderer and the live read that selects the day both
    # take a timezone, and nothing in wave 1 supplied one. `""` is the unset
    # state: a caller needing a day boundary refuses rather than assuming UTC,
    # because assuming is how a meeting lands on the wrong day silently.
    display_timezone: str = ""

    def __post_init__(self) -> None:
        """Refuse a value no `Config` may hold, however it was constructed.

        `load_config` is not the only way in and never was: `build(config=...)`
        takes one directly, tests construct them, and `4c` will too. Leaving
        these rules in the loader would have made them true of files and false
        of the class — `Config(pm_handle="   ")` constructed happily, and a
        whitespace handle both defeats `extract()`'s `bool(pm_handle)` guard and
        matches any speaker carrying the same whitespace. Checked here for the
        same reason `File`/`Dir`/`Collection` check themselves: everything a
        value can know about itself, it knows at construction.

        The two unset states are permitted, deliberately. `0.0` and `""` are
        what the defaults are; it is only *writing* them in `config.toml` that
        is refused, because a key someone typed is a key they expect an effect
        from.
        """
        rate = self.blended_hourly_rate
        # `bool` before number, as everywhere else here: `True` is an `int`.
        if isinstance(rate, bool):
            raise ConfigRefused(
                "blended_hourly_rate was given a boolean. A rate of `True` "
                "arrives at Meeting.man_hour_cost as 1.0 and prices every "
                "meeting at one currency unit per attendee-hour."
            )
        if not math.isfinite(rate) or rate < 0:
            raise ConfigRefused(
                f"blended_hourly_rate must be a finite number of 0 or more, not "
                f"{rate!r}. 0 is the unconfigured state; a negative or "
                f"non-finite rate would propagate into every cost this daemon "
                f"reports and look like a measurement."
            )
        # The mirror of the rate's `bool` guard above, and missing until story
        # 4g gave the class a renderer. `Config(verbose_logging=1)` constructed
        # happily and rendered `verbose_logging = 1.0` — a file pm-ai wrote and
        # its own `_flag` then refused, which is the drift the round-trip rule
        # exists to make impossible. A flag is a boolean or it is not a flag.
        if not isinstance(self.verbose_logging, bool):
            raise ConfigRefused(
                f"verbose_logging is {self.verbose_logging!r}, which is not a "
                f"boolean. `1` and `\"true\"` are refused rather than "
                f"interpreted here for the same reason `_flag` refuses them in "
                f"the file: TOML has a real boolean type, and a stand-in is an "
                f"author guessing."
            )
        if self.pm_handle and not self.pm_handle.strip():
            raise ConfigRefused(
                f"pm_handle is {self.pm_handle!r} — whitespace, which is neither "
                f"unset nor a handle. `\"\"` is the unset state and matches "
                f"nobody; whitespace matches a speaker whose handle is the same "
                f"whitespace, which would hand that speaker the PM's execution "
                f"authority (AD-32)."
            )
        # A lone surrogate has no UTF-8 encoding, so a handle carrying one
        # could be *held* by a `Config` and never written to `config.toml` —
        # `render_config`'s final `.encode("utf-8")` raised `UnicodeEncodeError`
        # out of a module that promises `ConfigRefused` and nothing else.
        # Refused here rather than escaped there, because escaping is not
        # available: no TOML string can carry an unpaired surrogate, so the
        # round-trip rule holds only if such a handle is inadmissible.
        #
        # Not a theoretical input. POSIX argv is decoded with `surrogateescape`,
        # so any byte sequence the shell hands `pm-ai` that is not valid UTF-8
        # arrives as surrogates. `4h` reads `pm_handle` from a terminal prompt,
        # and a stdin decoded with `surrogateescape` delivers such bytes the
        # same way.
        if _has_surrogates(self.pm_handle):
            raise ConfigRefused(
                f"pm_handle is {self.pm_handle!r}, which contains an unpaired "
                f"surrogate and therefore has no UTF-8 encoding — config.toml "
                f"could not be written with it at all. This is what a command "
                f"line argument looks like when the bytes the shell passed were "
                f"not valid UTF-8; re-enter the handle."
            )
        if self.display_timezone:
            # Validated against the zone database rather than by shape. A
            # typo'd zone is not a syntax error — it is a silent shift in which
            # meetings count as today, in whichever query selects the day.
            #
            # `ZoneInfoNotFoundError` is a `KeyError`, **not** a `ValueError`,
            # so a bare `except ValueError` here would let `Europe/Warsav`
            # through; it is named deliberately. `ValueError` covers the keys
            # `zoneinfo` rejects before it looks anything up (an absolute path,
            # `..`, an unnormalized key), `TypeError` the non-string a direct
            # construction can pass, and `OSError` a database file this machine
            # has but cannot read or cannot parse. All four are the same fact to
            # a caller, and every way in must leave with `ConfigRefused`.
            try:
                ZoneInfo(self.display_timezone)
            except (ZoneInfoNotFoundError, ValueError, TypeError, OSError) as unknown:
                raise _refuse_zone(self.display_timezone, unknown) from unknown


ACCEPTED_KEYS = frozenset(field.name for field in fields(Config))


def load_config(raw: bytes | None) -> Config:
    """Interpret `config.toml`'s bytes, or return defaults when there are none.

    `None` is the caller reporting no file, `b""` an empty one. Both are
    ordinary first-run states.

    Raises `ConfigRefused` — and nothing else — for anything it cannot act on,
    in a fixed order: decode, parse, depth, the encryption family, the closed
    vocabulary, types, admissible values, and finally `Config.__post_init__`.
    The encryption check precedes the unknown-key sweep on purpose:
    `encryption_mode` is both, and the refusal that names
    `PM_AI_DISABLE_ENCRYPTION` is the useful one.
    """
    if raw is None or not raw.strip():
        return Config()
    table = _parse(_decode(raw))
    paths = tuple(_walk(table))
    _refuse_encryption(paths)
    _refuse_unknown(paths)
    supplied = {path[0]: value for path, value in paths if len(path) == 1}
    defaults = Config()
    rate = supplied.get("blended_hourly_rate")
    handle = supplied.get("pm_handle")
    verbose = supplied.get("verbose_logging")
    zone = supplied.get("display_timezone")
    return Config(
        blended_hourly_rate=(
            defaults.blended_hourly_rate
            if rate is None
            else _number("blended_hourly_rate", rate)
        ),
        pm_handle=defaults.pm_handle if handle is None else _text("pm_handle", handle),
        verbose_logging=(
            defaults.verbose_logging
            if verbose is None
            else _flag("verbose_logging", verbose)
        ),
        display_timezone=(
            defaults.display_timezone
            if zone is None
            # A string first, so `display_timezone = 5` reports the type rather
            # than the zone database's opinion of it; `__post_init__` then does
            # the lookup, so the class holds the rule and not only the loader.
            else _text("display_timezone", zone)
        ),
    )


HEADER = """\
# config.toml — pm-ai's daemon settings and global defaults.
#
# Written by the `pm-ai` command line, which is the primary channel: it rewrites
# this file whole, every time. Hand-editing is supported and read back exactly
# as written — but **comments are not preserved**. The next write replaces the
# file and only this header survives it: pm-ai's TOML reader cannot write, and
# no comment-preserving parser is a dependency of this project.
#
# A setting left at its default is omitted rather than written out, so a file
# holding nothing but this header is a valid and fully unconfigured one.
"""
"""The generated preamble every rendered `config.toml` opens with.

Comments only, so it contributes no key: the file this module renders for a
`Config()` parses back to `Config()`, which is what makes a first-run write
readable by its own loader.

Deliberately names no setting. Every word here is a word `_refuse_encryption`
would have to sweep past if the header ever grew a key list, and the point of
the vocabulary being derived from the dataclass is that no second copy of it
exists to drift — `pm-ai config show` prints the live one.
"""

# TOML basic-string escapes, verbatim from the specification's table. `\b` and
# `\f` are in it and are easy to forget: `Config.__post_init__` admits any
# non-blank handle, so a control character in one is admissible input, and an
# unescaped one produces a file the loader on the other side of this module
# cannot parse.
_ESCAPES = {
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
    '"': '\\"',
    "\\": "\\\\",
}

# U+007F is a control character TOML forbids raw in a basic string, and it has
# no short escape — it reaches the file as the six characters `\u007F` or
# not at all.
_DELETE = "\x7f"


def render_config(config: Config) -> bytes:
    """`config.toml`'s bytes for a `Config`, for a caller that will write them.

    Returns bytes and opens nothing, the mirror of `load_config`. The caller
    writes them through `StorageService`, which replaces the file whole.

    Two guarantees, and everything here exists to keep them:

    **Round trip.** `load_config(render_config(c)) == c` for every admissible
    `Config`. The keys are walked from `fields(Config)` — the same derivation
    `ACCEPTED_KEYS` uses — so a field added to the dataclass is emitted without
    anything here being edited, and cannot become a key the loader refuses as
    unknown.

    **A key at its unset default is omitted.** Compared against `Config()`'s own
    field defaults, which is the definition `pm-ai config show` already uses for
    the word. For the rate and the handle the loader would refuse the explicit
    unset value anyway; for `verbose_logging` it would not — `false` is a
    perfectly acceptable flag — so this rule is the only thing that keeps a
    first-run file from stating a setting nobody chose.

    Values are emitted at their declared TOML types: a flag as `true`/`false`
    and never `1` or `"true"`, a rate through `repr(float(...))` so an integral
    `85.0` reads back as a float rather than an int, and a string as a basic
    string with every escape the specification requires.
    """
    defaults = Config()
    body = [
        f"{field.name} = {_literal(field.name, getattr(config, field.name))}"
        for field in fields(Config)
        if getattr(config, field.name) != getattr(defaults, field.name)
    ]
    # A fully unset `Config` renders the header and nothing else — not the
    # header plus a stray blank line — so the first-run file is byte-for-byte
    # the preamble and the "no keys" claim is visible in the bytes.
    document = HEADER if not body else f"{HEADER}\n" + "\n".join(body) + "\n"
    return document.encode("utf-8")


def _literal(key: str, value: object) -> str:
    """One `Config` value as the TOML literal its loader accepts back.

    `bool` is tested before the numbers for the reason it is everywhere else in
    this module: `True` is an `int`, and reaching the numeric arm would write
    `1.0` where the file must say `true`.

    An `int` is widened rather than emitted as one. `_number` widens on the way
    in, but `Config(blended_hourly_rate=85)` is constructible directly and
    equals `Config(blended_hourly_rate=85.0)`, so the two must render the same
    bytes or the round trip holds for one and not the other.

    The final refusal is unreachable today and is the point: a field added to
    `Config` at a type this function does not know would otherwise be dropped
    silently, which is the same "reads as configured while having no effect"
    failure the closed vocabulary exists to prevent, arriving from the writing
    side instead of the reading one.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(float(value))
    if isinstance(value, str):
        return _basic_string(value)
    raise ConfigRefused(
        f"`{key}` holds {_type_name(value)}, which config.toml has no way to "
        f"write. Every accepted key is a string, a number or a boolean; a field "
        f"added at another type needs a literal here before it can be rendered, "
        f"or it would be omitted from the file and read back as unset."
    )


def _basic_string(value: str) -> str:
    """A TOML basic string: quoted, with every character the format forbids escaped.

    Written out rather than borrowed from `json.dumps`. The escapes overlap
    almost entirely, but JSON's encoder escapes every non-ASCII character by
    default, and a handle spelled back as `\\u00e9` in a file whose whole promise
    is that a human can edit it is a worse file for no gain.

    U+007F earns its own branch: `tomllib` refuses it raw — `Illegal character
    '\\x7f'` — and it is the one control character with no short escape.

    Everything else passes through as itself, U+0080 and above included: the
    file is UTF-8 and a `\\u` escape of a legal character only makes it harder
    to hand-edit, which is a promise this file keeps.
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


def _decode(raw: bytes) -> str:
    """UTF-8, with an editor-added BOM tolerated.

    The BOM is stripped before parsing rather than passed through, because
    `tomllib` reads it as part of the first key and a perfectly valid file would
    otherwise be refused as malformed. Refused distinctly from malformed TOML: a
    file saved in the wrong encoding is a different fix from a file with a
    syntax error in it.
    """
    stripped = raw.startswith(_BOM)
    if stripped:
        raw = raw[len(_BOM) :]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        # Reported against the file, not against the buffer this function
        # sliced. Dropping the BOM shifts every position three bytes, and an
        # offset three short of the truth misdirects precisely the files this
        # function went out of its way to tolerate.
        offset = exc.start + (len(_BOM) if stripped else 0)
        raise ConfigRefused(
            f"config.toml is not valid UTF-8 — byte {offset} is not part of a "
            f"legal sequence ({exc.reason}). This is an encoding problem, not a "
            f"syntax one: re-save the file as UTF-8."
        ) from exc


def _parse(text: str) -> Mapping[str, object]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        # `TOMLDecodeError`'s message carries the line and column, which is the
        # only thing that makes a syntax refusal actionable.
        raise ConfigRefused(f"config.toml is not valid TOML: {exc}") from exc
    except RecursionError as exc:
        # What `tomllib` raises — not `TOMLDecodeError` — for a key with more
        # than 1000 dotted parts. Caught so the parser's implementation limit
        # arrives as this module's refusal rather than as a stack error from
        # somewhere the caller has never heard of.
        raise ConfigRefused(
            f"config.toml nests too deeply for the parser to read it ({exc}). "
            f"Every accepted key is a top-level scalar, so no legitimate file "
            f"goes anywhere near this."
        ) from exc


def _walk(
    table: Mapping[str, object], prefix: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], object]]:
    """Every node in the parsed document, tables and arrays-of-tables included.

    Tables are yielded as nodes in their own right, not just as containers, so
    an empty `[encryption]` table — which has no leaf keys to sweep — is still
    seen. Nested nodes come after their parent, which is what lets the
    unknown-key refusal name a top-level key rather than a path inside it.
    """
    for key, value in table.items():
        path = (*prefix, key)
        if len(path) > _MAX_DEPTH:
            raise ConfigRefused(
                f"`{'.'.join(path[:_MAX_DEPTH])}…` nests more than {_MAX_DEPTH} "
                f"levels deep in config.toml. Nothing that deep could be read "
                f"even if it parsed — every accepted key is a top-level scalar "
                f"— and refusing it here is what keeps a pathological file from "
                f"exhausting the stack instead of getting an answer."
            )
        yield path, value
        if isinstance(value, dict):
            yield from _walk(value, path)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield from _walk(item, path)


def _refuse_encryption(paths: tuple[tuple[tuple[str, ...], object], ...]) -> None:
    """Refuse any key in the encryption family, wherever it sits in the document.

    Matched per path segment, so burying it in a table (`[settings]` with
    `encryption = false`) does not evade the check. Nothing about the *value* is
    consulted: `encryption = true` is refused as firmly as `encryption = false`,
    because the objection is to the channel, not the setting.
    """
    for path, _ in paths:
        if any(marker in segment.lower() for segment in path for marker in ENCRYPTION_KEY_FAMILY):
            raise ConfigRefused(
                f"`{'.'.join(path)}` in config.toml reads as an encryption "
                f"setting, and config.toml may never carry one — not to turn "
                f"encryption on, and least of all to turn it off. The only "
                f"channel is the PM_AI_DISABLE_ENCRYPTION environment variable, "
                f"deliberately: it dies with the process, so restarting restores "
                f"encryption unconditionally, which a config key would not. "
                f"Remove the key and export the variable for the one session "
                f"that needs it."
            )


def _refuse_unknown(paths: tuple[tuple[tuple[str, ...], object], ...]) -> None:
    """Refuse a key outside the accepted vocabulary, naming the whole set.

    Only top-level names are judged here. A path *inside* an unknown table is
    already covered by its refused parent, and a path inside an accepted key
    means that key was written as a table — which the type checks report far
    more usefully than "unknown key" would.
    """
    for path, _ in paths:
        if len(path) == 1 and path[0] not in ACCEPTED_KEYS:
            raise ConfigRefused(
                f"`{path[0]}` is not a setting config.toml accepts. The accepted "
                f"keys are {', '.join(sorted(ACCEPTED_KEYS))}. Unknown keys are "
                f"refused rather than ignored, because a typo that silently does "
                f"nothing reads as configured forever."
            )


def _type_name(value: object) -> str:
    """What TOML calls the value's type, for a message a file's author can act on."""
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, dict):
        return "a table"
    if isinstance(value, list):
        return "an array"
    return f"a {type(value).__name__}"


def _number(key: str, value: object) -> float:
    """A positive, finite number. Integers are accepted and widened.

    `bool` is refused explicitly because it subclasses `int`: without this,
    `blended_hourly_rate = true` passes a numeric check, arrives at
    `Meeting.man_hour_cost` as `1.0`, and prices every meeting at one currency
    unit per attendee-hour — a headline figure that is wrong and looks computed.

    `0` and negatives are refused rather than clamped. A rate of zero is the
    unconfigured state, reached by omitting the key; writing it explicitly is
    someone expecting an effect there is none of. Non-finite is refused for the
    same reason — `nan` propagates through every cost silently.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigRefused(
            f"`{key}` must be a number, but config.toml gives {_type_name(value)}. "
            f"A boolean is not a number here despite Python treating it as one: "
            f"`true` would arrive as the rate 1.0."
        )
    try:
        number = float(value)
    except OverflowError as exc:
        # `tomllib` returns Python ints, which are arbitrary-precision: a
        # 400-digit integer parses perfectly and then has no float to become.
        # Left uncaught, the one call in this module that can raise something
        # other than `ConfigRefused` did.
        raise ConfigRefused(
            f"`{key}` in config.toml is a {len(str(value))}-digit integer, which "
            f"is too large to be a number at all — there is no float that far "
            f"out. A blended hourly rate is a small figure."
        ) from exc
    if not math.isfinite(number) or number <= 0:
        raise ConfigRefused(
            f"`{key}` must be a finite number greater than 0; config.toml gives "
            f"{number!r}. Omit the key to leave the rate unconfigured — that is "
            f"what zero means, and a cost computed from a zero or negative rate "
            f"would look like a measurement."
        )
    return number


def _text(key: str, value: object) -> str:
    """A non-blank string, kept exactly as written.

    Not stripped: a handle with a stray space is compared against speaker
    identities for equality, and silently trimming it would hide the edit that
    made the comparison start working.
    """
    if not isinstance(value, str):
        raise ConfigRefused(
            f"`{key}` must be a string, but config.toml gives {_type_name(value)}."
        )
    if not value.strip():
        raise ConfigRefused(
            f"`{key}` is blank in config.toml. Omit the key to leave it unset; "
            f"writing an empty value states a setting that cannot match anything."
        )
    return value


def _flag(key: str, value: object) -> bool:
    """A TOML boolean, and only that.

    `1`, `"yes"` and `"true"` are all refused. This is the opposite choice from
    `environment.TRUTHY`, and for the opposite reason: that allowlist exists
    because an environment variable can only be a string, whereas TOML has a
    real boolean type and a file that says `1` is a file whose author guessed.
    """
    if not isinstance(value, bool):
        raise ConfigRefused(
            f"`{key}` must be `true` or `false`, but config.toml gives "
            f"{_type_name(value)}. TOML has a boolean type; a quoted or numeric "
            f"stand-in is refused rather than interpreted."
        )
    return value
