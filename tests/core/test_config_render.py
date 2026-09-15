"""`config.toml`, written — one test per row of the story's matrix.

Spec: `_bmad-output/specs/spec-pm-ai/stories/4g-config-gains-a-writer.md`.

Every test hands `render_config` a `Config` and, where the claim is about
agreement rather than about bytes, reads the result back with `load_config`.
No test writes a file and none takes `tmp_path` — `render_config` returns bytes
and opens nothing, so a test here that needed a path would be evidence it had
grown a write. The one file read in this module is `pyproject.toml`, by
`test_no_toml_writing_dependency_was_added`, whose claim is about the project's
declared dependencies and cannot be made any other way. The AST sweep that
proves the module itself neither reads nor writes lives in
`tests/architecture/test_static_rules.py::test_story_4a_the_config_module_neither_reads_nor_writes_a_file`,
which story 4g widened from read verbs to both.

The round trip is driven over **every combination** of the four keys set and
unset rather than over examples. A renderer and a parser are the classic drift
pair, and a single-example test passes just as happily when one key of four is
dropped on the floor.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import fields
from itertools import product

from zoneinfo import ZoneInfoNotFoundError

import pytest

from pm_ai.core import config as config_module
from pm_ai.core.config import (
    ACCEPTED_KEYS,
    ENCRYPTION_KEY_FAMILY,
    HEADER,
    Config,
    ConfigRefused,
    load_config,
    render_config,
)

# One admissible non-default value per key, so "set" means something concrete
# for each. Every entry differs from `Config()`'s default for that field — the
# combination sweep below asserts exactly that, so a value that crept back to a
# default would fail loudly rather than quietly halve the coverage.
SET_VALUES: dict[str, object] = {
    "blended_hourly_rate": 85.5,
    "pm_handle": "pm@example.org",
    "verbose_logging": True,
    "display_timezone": "Europe/Warsaw",
}

KEYS = tuple(field.name for field in fields(Config))


def _rendered_keys(config: Config) -> set[str]:
    """The top-level keys the rendered file actually carries.

    Parsed with `tomllib` rather than matched with a regular expression: the
    claim is about what the file *says*, and a key inside an escaped string
    would fool a text scan in exactly the direction that matters.
    """
    return set(tomllib.loads(render_config(config).decode("utf-8")))


def _combinations() -> list[Config]:
    """Every subset of the keys, set to its `SET_VALUES` entry or left default."""
    return [
        Config(**{key: SET_VALUES[key] for key, on in zip(KEYS, flags) if on})
        for flags in product((False, True), repeat=len(KEYS))
    ]


# ── The round trip, enumerated ───────────────────────────────────────────────


def test_the_sweep_covers_every_key_and_every_combination_of_them():
    """The guard on the two tests below, which are only as good as this set.

    `SET_VALUES` is hand-written and `ACCEPTED_KEYS` is derived, so a field
    added to `Config` leaves the sweep running over a proper subset of the
    vocabulary — every assertion still passing, and the new key untested. This
    is what fails instead.
    """
    assert set(SET_VALUES) == ACCEPTED_KEYS
    assert len(_combinations()) == 2 ** len(ACCEPTED_KEYS)
    defaults = Config()
    for key, value in SET_VALUES.items():
        assert value != getattr(defaults, key), (
            f"{key}'s sample value is its own default, so half this sweep's "
            f"combinations are the same combination"
        )


@pytest.mark.parametrize("config", _combinations(), ids=repr)
def test_every_combination_of_set_and_unset_survives_a_round_trip(config: Config):
    """`load_config(render_config(c)) == c`, the rule the whole slice exists for.

    Enumerated rather than exampled: a renderer that dropped one key of four
    would pass any single-example test that happened not to set it.
    """
    assert load_config(render_config(config)) == config


@pytest.mark.parametrize("config", _combinations(), ids=repr)
def test_the_rendered_keys_are_exactly_the_ones_that_differ_from_default(
    config: Config,
):
    """Round-trip equality alone cannot see an always-emitted `verbose_logging`.

    `_flag` accepts `false`, so a renderer that wrote the flag out
    unconditionally would round-trip perfectly while stating a setting nobody
    chose. Omission is policy here, not something the loader enforces, which is
    why it needs an assertion of its own.
    """
    defaults = Config()
    expected = {
        key for key in ACCEPTED_KEYS if getattr(config, key) != getattr(defaults, key)
    }
    assert _rendered_keys(config) == expected


@pytest.mark.parametrize("config", _combinations(), ids=repr)
def test_no_key_outside_the_accepted_vocabulary_is_ever_rendered(config: Config):
    """The checkable form of "no encryption-shaped key is ever emitted".

    A direct grep for `ENCRYPTION_KEY_FAMILY` cannot fail — four typed fields
    cannot produce a matching name — and its only realistic outcome is a false
    positive against this module's own generated header. Subsetting into
    `ACCEPTED_KEYS` is the assertion that can actually break: it fails for an
    encryption key and for every other key the loader would refuse.
    """
    assert _rendered_keys(config) <= ACCEPTED_KEYS


def test_the_generated_header_carries_no_word_the_loader_sweeps_for():
    """The one place a false positive could plausibly arrive from.

    The header is comments and contributes no key, so it cannot trip
    `_refuse_encryption` — but it is prose a human will edit, and a sentence
    about sealed artifacts added to it later would read as an attempt at the
    thing the architecture forbids the moment anyone greps the file.
    """
    folded = HEADER.lower()
    assert not [marker for marker in ENCRYPTION_KEY_FAMILY if marker in folded]


# ── The matrix rows, each on its own ─────────────────────────────────────────


def test_a_fully_unset_config_renders_a_header_and_no_key():
    """A first-run file must be readable by its own loader.

    Both halves matter. "No key" is what makes the write a real first-run file
    rather than one stating four settings nobody chose; reading it back to
    `Config()` is what proves the unset state survives a write at all.
    """
    rendered = render_config(Config())
    assert tomllib.loads(rendered.decode("utf-8")) == {}
    assert load_config(rendered) == Config()
    assert rendered.decode("utf-8") == HEADER


def test_the_header_says_who_writes_the_file_and_what_a_rewrite_costs():
    """AD-3 promises the file *can* be hand-edited; the header says at what price.

    `tomllib` reads and cannot write, and no comment-preserving parser is a
    dependency — so an operator's comments do not survive the next write. A
    file that did not say so would lose them silently.
    """
    assert "pm-ai" in HEADER
    assert "comments are not preserved" in HEADER.lower()
    assert "hand-editing is supported" in HEADER.lower()
    assert all(
        not line or line.startswith("#") for line in HEADER.splitlines()
    ), "the header is comments only, or it would contribute a key"


@pytest.mark.parametrize("config", _combinations(), ids=repr)
def test_the_header_opens_every_rendered_file_not_only_the_empty_one(config: Config):
    """The assertions above pin `HEADER` and the unset case, and nothing else.

    A renderer emitting the preamble only on the no-keys branch passed the
    whole suite: `test_a_fully_unset_config_renders_a_header_and_no_key`
    compares bytes for `Config()` alone, and the test above compares the
    constant against itself. The file an operator actually receives is the
    configured one, and it is the one that has to say a rewrite eats their
    comments.
    """
    rendered = render_config(config).decode("utf-8")
    assert rendered.startswith(HEADER)
    assert "comments are not preserved" in rendered.lower()


def test_a_partially_set_config_emits_one_key_and_omits_the_unset_one():
    """The unset rate is absent, not written out as zero."""
    rendered = render_config(Config(pm_handle="pm@example.org")).decode("utf-8")
    assert 'pm_handle = "pm@example.org"' in rendered
    assert "blended_hourly_rate" not in rendered
    assert tomllib.loads(rendered) == {"pm_handle": "pm@example.org"}


def test_an_integral_rate_reads_back_as_a_float_not_an_int():
    """`85.0` written as `85` parses back as an `int`, and the type is the point.

    `Meeting.man_hour_cost` takes a float; `_number` widens on the way in, so
    the round trip would survive — but the file would state a different type
    from the one the field declares, and the next reader of `config.toml` is
    not guaranteed to be this loader.
    """
    rendered = render_config(Config(blended_hourly_rate=85.0)).decode("utf-8")
    assert "blended_hourly_rate = 85.0" in rendered
    assert isinstance(tomllib.loads(rendered)["blended_hourly_rate"], float)


def test_an_int_valued_rate_renders_the_same_bytes_as_its_float():
    """`Config(...=85)` is constructible directly and equals `Config(...=85.0)`.

    Two `Config`s that compare equal must render identically, or the round-trip
    rule holds for one of them and not for the other.
    """
    assert Config(blended_hourly_rate=85) == Config(blended_hourly_rate=85.0)
    assert render_config(Config(blended_hourly_rate=85)) == render_config(
        Config(blended_hourly_rate=85.0)
    )


def test_a_set_flag_is_a_toml_boolean_and_never_a_number_or_a_string():
    """`_flag` refuses `1` and `"true"`, so a renderer emitting either is a file
    the loader on the other side of this module would reject."""
    rendered = render_config(Config(verbose_logging=True)).decode("utf-8")
    assert "verbose_logging = true" in rendered
    assert tomllib.loads(rendered)["verbose_logging"] is True


def test_a_flag_at_its_default_is_omitted_like_every_other_unset_key():
    """The one key the loader would happily accept written out.

    `verbose_logging = false` is admissible input — which is exactly why the
    omission rule is policy stated here rather than a constraint inherited from
    `_flag`.
    """
    assert "verbose_logging" not in render_config(Config(verbose_logging=False)).decode()


def test_a_set_timezone_round_trips_and_the_zone_database_accepts_it():
    rendered = render_config(Config(display_timezone="Europe/Warsaw"))
    assert 'display_timezone = "Europe/Warsaw"' in rendered.decode("utf-8")
    assert load_config(rendered).display_timezone == "Europe/Warsaw"


@pytest.mark.parametrize(
    "zone",
    [
        pytest.param("Europe/Warsav", id="typo"),
        pytest.param("/etc/localtime", id="absolute-path"),
        pytest.param("..", id="traversal"),
        pytest.param("Warsaw", id="bare-city"),
    ],
)
def test_an_unknown_timezone_is_refused_by_name_before_it_can_be_rendered(zone: str):
    """Refused at construction, so there is no admissible `Config` to render.

    `ZoneInfoNotFoundError` is a `KeyError` and not a `ValueError`: a bare
    `except ValueError` around the lookup would let the typo row through, and
    the typo row is the one that matters — a zone one letter wrong does not
    fail, it silently shifts which meetings count as today.
    """
    with pytest.raises(ConfigRefused) as exc:
        Config(display_timezone=zone)
    assert "display_timezone" in str(exc.value)
    assert zone in str(exc.value)


def test_the_timezone_refusal_reaches_the_loader_too():
    """The class holds the rule, so the file gets it without the loader restating it."""
    with pytest.raises(ConfigRefused) as exc:
        load_config(b'display_timezone = "Europe/Warsav"\n')
    assert "display_timezone" in str(exc.value)


def test_an_unset_timezone_is_omitted_rather_than_defaulted_to_utc():
    """`Config()`'s default is the unset state, and a caller needing a day
    boundary refuses rather than assuming — so nothing here may write one in."""
    rendered = render_config(Config(pm_handle="pm@example.org")).decode("utf-8")
    assert "display_timezone" not in rendered
    assert load_config(rendered.encode("utf-8")).display_timezone == ""


# ── Escaping ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "handle",
    [
        pytest.param('a"b', id="double-quote"),
        pytest.param("a\\b", id="backslash"),
        pytest.param("a\\nb", id="backslash-then-n"),
        pytest.param("a\nb", id="newline"),
        pytest.param("a\rb", id="carriage-return"),
        pytest.param("a\tb", id="tab"),
        pytest.param("a\bb", id="backspace"),
        pytest.param("a\fb", id="form-feed"),
        pytest.param("a\x00b", id="nul"),
        pytest.param("a\x1fb", id="unit-separator"),
        pytest.param("a\x7fb", id="delete"),
        pytest.param('"""', id="three-quotes"),
        pytest.param("café \U0001f600", id="non-ascii-and-astral"),
        pytest.param("\U0001f600\U0001f600", id="two-astral-in-a-row"),
    ],
)
def test_a_handle_needing_escapes_parses_back_byte_identical(handle: str):
    """`Config.__post_init__` admits any non-blank handle, control chars included.

    So an unescaped one is not a hypothetical: `"a\\nb"` is an admissible handle
    and an unescaped newline inside a basic string produces a file this
    module's own loader cannot parse. Asserted as a round trip rather than
    against expected bytes, because the claim is that the loader gets the same
    string back, not that this renderer picked any particular escape for it.
    """
    config = Config(pm_handle=handle)
    assert load_config(render_config(config)) == config
    assert load_config(render_config(config)).pm_handle == handle


def test_a_non_ascii_handle_is_written_as_itself_rather_than_escaped():
    """The file is UTF-8 and a human may edit it; `\\u00e9` helps nobody."""
    rendered = render_config(Config(pm_handle="café")).decode("utf-8")
    assert 'pm_handle = "café"' in rendered


@pytest.mark.parametrize(
    "handle",
    [
        pytest.param("\ud800", id="lone-high-surrogate"),
        pytest.param("\ud800x", id="surrogate-then-text"),
        pytest.param("pm@ex\udcffample.org", id="surrogateescape-byte"),
    ],
)
def test_a_handle_with_an_unpaired_surrogate_is_inadmissible(handle: str):
    """The one string no escape can rescue, so it cannot be an admissible `Config`.

    A lone surrogate has no UTF-8 encoding at all, so `render_config`'s final
    `.encode("utf-8")` raised `UnicodeEncodeError` — the wrong exception type
    out of a module that promises `ConfigRefused` and nothing else, and a hole
    in the frozen "round trip or nothing" rule, since such a `Config` was
    admissible and could not be rendered.

    Reachable rather than theoretical: POSIX argv is decoded with
    `surrogateescape`, so bytes the shell passes that are not valid UTF-8
    arrive as exactly these code points, and `4h` sets `pm_handle` from the
    command line. Refused at construction, which is where the round-trip rule
    stays true as written.
    """
    with pytest.raises(ConfigRefused) as exc:
        Config(pm_handle=handle)
    assert "pm_handle" in str(exc.value)
    assert "surrogate" in str(exc.value)


def test_no_admissible_config_can_fail_to_encode():
    """The claim the refusal above buys, stated over the whole escape matrix.

    `render_config` promises bytes for every admissible `Config`; anything that
    would raise on the way out has to have been refused before it got here.
    """
    for handle in ("\ud800", "\udcff", "a\ud800b"):
        with pytest.raises(ConfigRefused):
            Config(pm_handle=handle)


# ── Values the class must refuse so the renderer never meets them ────────────


@pytest.mark.parametrize(
    "flag",
    [
        pytest.param(1, id="one"),
        pytest.param(0, id="zero"),
        pytest.param("true", id="the-word"),
        pytest.param(1.0, id="a-float"),
    ],
)
def test_a_non_boolean_flag_is_refused_at_construction(flag: object):
    """`Config(verbose_logging=1)` rendered `verbose_logging = 1.0` — a file
    pm-ai wrote and `_flag` then refused.

    The rate has guarded the opposite direction since 4a (`isinstance(rate,
    bool)`); the flag had no guard at all, and the renderer is what turned that
    asymmetry into bytes the loader on the other side of this module rejects.
    A flag is a boolean or it is not a flag.
    """
    with pytest.raises(ConfigRefused) as exc:
        Config(verbose_logging=flag)  # type: ignore[arg-type]
    assert "verbose_logging" in str(exc.value)


def test_a_non_string_timezone_is_refused_as_a_type_not_as_a_missing_zone():
    """`ZoneInfo` raises `TypeError` here, and the remedy is not the same one.

    The catch has a `TypeError` arm on purpose — a direct construction can pass
    anything — but templating one message over all of them told an operator
    that their timezone database "does not hold" the integer `5`, which is not
    a fact about any database.
    """
    with pytest.raises(ConfigRefused) as exc:
        Config(display_timezone=5)  # type: ignore[arg-type]
    message = str(exc.value)
    assert "display_timezone" in message
    assert "must be a string" in message
    assert "does not hold" not in message


def test_the_missing_database_refusal_names_tzdata_rather_than_blaming_the_name(
    monkeypatch: pytest.MonkeyPatch,
):
    """`tzdata` is a `runtime` extra, so "no database at all" is an ordinary state.

    On such a machine *every* zone is refused, and the typo message — "it must
    be an IANA zone name such as `Europe/Warsaw`" — is advice the operator has
    already followed. `connectors.graph.calendar.zone_of` draws exactly this
    distinction at its own lookup; this follows that precedent.

    Simulated by a `ZoneInfo` that raises for everything, which is what such a
    machine has. The probe is what tells the two apart: `UTC` resolves on any
    database that exists.
    """

    def no_database(name: str) -> object:
        raise ZoneInfoNotFoundError(f"no time zone found with key {name}")

    monkeypatch.setattr(config_module, "ZoneInfo", no_database)
    with pytest.raises(ConfigRefused) as exc:
        Config(display_timezone="Europe/Warsaw")
    message = str(exc.value)
    assert "tzdata" in message
    assert "missing database" in message
    assert "such as `Europe/Warsaw`" not in message


def test_with_a_database_present_the_same_refusal_blames_the_name():
    """The other side of the probe, so the test above is not passing by accident."""
    with pytest.raises(ConfigRefused) as exc:
        Config(display_timezone="Europe/Warsav")
    message = str(exc.value)
    assert "tzdata" not in message
    assert "such as `Europe/Warsaw`" in message


def test_an_unreadable_timezone_database_is_refused_rather_than_raised_from(
    monkeypatch: pytest.MonkeyPatch,
):
    """`ZoneInfo` can raise `OSError` for a TZif file it can open and not parse.

    `load_config` documents that it raises `ConfigRefused` and nothing else, so
    an `OSError` reaching a caller from here would break that promise as surely
    as the `KeyError` the typo row guards against.
    """

    def unreadable(name: str) -> object:
        raise OSError(f"cannot read the timezone file for {name}")

    monkeypatch.setattr(config_module, "ZoneInfo", unreadable)
    with pytest.raises(ConfigRefused):
        Config(display_timezone="Europe/Warsaw")


def test_a_value_no_toml_literal_can_carry_is_refused_rather_than_dropped():
    """`_literal`'s last branch is unreachable today, and that is what it is for.

    A field added to `Config` at a type this renderer does not know would
    otherwise be omitted from the file and read back as unset — the same "reads
    as configured while having no effect" failure the closed vocabulary exists
    to prevent, arriving from the writing side. Called directly, because there
    is deliberately no `Config` that can reach it.
    """
    with pytest.raises(ConfigRefused) as exc:
        config_module._literal("some_future_key", ["a", "list"])
    assert "some_future_key" in str(exc.value)
    assert "an array" in str(exc.value)


# ── Pins ─────────────────────────────────────────────────────────────────────


def test_the_renderer_returns_bytes_ending_in_a_newline():
    """A text file ends with one, and the caller writes these bytes verbatim."""
    rendered = render_config(Config(pm_handle="pm@example.org"))
    assert isinstance(rendered, bytes)
    assert rendered.endswith(b"\n")
    assert rendered.decode("utf-8")  # valid UTF-8, which is what the loader decodes


def test_the_key_order_is_the_dataclass_order():
    """Derived rather than chosen, so a field added to `Config` needs no edit here
    and two renders of the same `Config` cannot differ.

    Read off the starts of the key lines rather than with `str.index`, which
    would happily find `pm_handle = ` inside a quoted value the moment a
    fixture gained one and report an order the file does not have.
    """
    every = Config(**SET_VALUES)  # type: ignore[arg-type]
    rendered = render_config(every).decode("utf-8")
    emitted = [
        line.split(" = ", 1)[0]
        for line in rendered.splitlines()
        if not line.startswith("#") and " = " in line
    ]
    assert emitted == list(KEYS)
    assert render_config(every) == render_config(every)


def _requirement_name(requirement: str) -> str:
    """The normalized distribution name of one PEP 508 requirement string.

    PEP 508 puts the name first and then any of an extras bracket, a version
    specifier, an environment marker or a direct URL. Split on the whole
    separator class rather than on the three punctuation marks that come to
    mind, then normalized per PEP 503 — names compare case-insensitively and
    `_` and `-` are the same character.

    Written out and tested below rather than inlined, because the first version
    of the check that used it was a chain of `.split()` calls that `tomli-w ~=
    1.0` walked straight past.
    """
    return re.split(r"[\s\[<>=!~;@]", requirement.strip())[0].casefold().replace("_", "-")


@pytest.mark.parametrize(
    ("requirement", "name"),
    [
        pytest.param("tomli-w", "tomli-w", id="bare"),
        pytest.param("tomli-w==1.0.0", "tomli-w", id="pinned"),
        pytest.param("tomli-w ~= 1.0", "tomli-w", id="compatible-release"),
        pytest.param("tomli-w != 1.0", "tomli-w", id="exclusion"),
        pytest.param("tomli-w<2", "tomli-w", id="upper-bound"),
        pytest.param("Tomli_W", "tomli-w", id="pep503-spelling"),
        pytest.param("tomlkit[extra]>=0.13", "tomlkit", id="extras"),
        pytest.param('toml ; python_version < "4"', "toml", id="marker"),
        pytest.param("toml @ git+https://example.org/toml", "toml", id="direct-url"),
        pytest.param("cryptography>=50.0.0", "cryptography", id="an-innocent-one"),
    ],
)
def test_the_requirement_parser_finds_the_name_in_every_pep508_shape(
    requirement: str, name: str
):
    """The guard on the test below, which is only as good as this parser.

    Every row but the last is a spelling that defeated the chained-`split`
    version — the check passed while the dependency it forbids sat in the file.
    """
    assert _requirement_name(requirement) == name


def test_no_toml_writing_dependency_was_added():
    """The closed vocabulary is why none is needed: four typed scalars are
    emitted directly, and there is nothing unknown to round-trip.

    Reads `pyproject.toml`, the one file this module touches. The claim is
    about the project's declared dependencies and there is no other place to
    ask — `importlib.metadata` would answer for what is *installed*, which is a
    different and weaker question.
    """
    from pathlib import Path

    pyproject = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    declared = tomllib.loads(pyproject)
    project = declared["project"]
    requirements = [
        requirement
        for group in (
            project.get("dependencies", []),
            *project.get("optional-dependencies", {}).values(),
            *declared.get("dependency-groups", {}).values(),
        )
        for requirement in group
    ]
    assert requirements, (
        "no requirement was found in pyproject.toml, so this check scanned "
        "nothing and would pass against any dependency at all"
    )
    writers = {"tomli-w", "tomlkit", "toml", "pytomlpp", "qtoml", "toml-writer"}
    offenders = [
        requirement
        for requirement in requirements
        if _requirement_name(requirement) in writers
    ]
    assert not offenders, (
        f"a TOML-writing dependency appeared in pyproject.toml: {offenders}. "
        f"`render_config` emits four typed scalars directly, and adding a "
        f"writer would give config.toml a second idea of its own format."
    )
