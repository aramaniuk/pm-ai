"""`~/.pm-ai/config.toml`, interpreted — the only reader of that file.

`config.toml` was declared as an application-scope Tier-1 artifact
(`scope_model.py:432`) and read by nothing: a promise the layout made and the
code did not keep. This module is the reader.

**It parses bytes and never opens a file.** That is structural rather than a
promise — `load_config` takes `bytes | None` and there is nothing here to open.
`core` is I/O-free by contract and `StorageService.read_artifact` is already the
single reader, so the caller reads and this module interprets. Worth stating
because no *inherited* gate would catch a file read here: the single-writer AST
sweep exempts read-mode opens, the import contracts list only network and
database clients, and the file-I/O rule is scoped to `pm_ai.storage`. So this
module has one of its own —
`test_static_rules.py::test_story_4a_the_config_loader_reads_no_file`
allowlists what it may import, which is why adding an import here is a
deliberate act rather than an ordinary one.

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
vocabulary is three keys, so a closed set costs nothing.

Absent and empty are both ordinary first-run states returning defaults — a
missing optional config is not a failure. There is no write path: this file is
hand-edited (AD-3).
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields

__all__ = [
    "ACCEPTED_KEYS",
    "ENCRYPTION_KEY_FAMILY",
    "Config",
    "ConfigRefused",
    "load_config",
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

# `config.toml`'s vocabulary is three flat scalars, so anything nested is
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
        if self.pm_handle and not self.pm_handle.strip():
            raise ConfigRefused(
                f"pm_handle is {self.pm_handle!r} — whitespace, which is neither "
                f"unset nor a handle. `\"\"` is the unset state and matches "
                f"nobody; whitespace matches a speaker whose handle is the same "
                f"whitespace, which would hand that speaker the PM's execution "
                f"authority (AD-32)."
            )


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
    )


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
