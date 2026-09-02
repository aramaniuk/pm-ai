"""`config.toml`, interpreted — one test per row of the story's matrix.

Spec: `_bmad-output/specs/spec-pm-ai/stories/4a-config-loading.md`.

Every test hands `load_config` bytes or constructs a `Config`. That is the whole
interface, and there is no filesystem anywhere in this module — no `tmp_path` in
any signature, nothing read from disk, not even to inspect the loader's own
source. A test here that needed a path would be evidence the loader had grown a
read. The repository-wide sweeps that *do* touch files — that this module opens
nothing, that one module imports `tomllib`, that no developer's address survives
in the package — live in `tests/architecture/test_static_rules.py`, which is
where AST invariants over `pm_ai/` belong and which already has the helpers.
"""

from __future__ import annotations

import math
from dataclasses import fields

import pytest

from pm_ai.core.config import ACCEPTED_KEYS, Config, ConfigRefused, load_config

# The name of the one channel the encryption toggle may travel on, imported from
# the one module permitted to hold it in code. `pm_ai.core.config` spells it out
# in prose instead (see the note there), so this is what pins the two together.
from pm_ai.platform.environment import DISABLE_ENCRYPTION_VAR


# ── Absent and empty: ordinary first-run states ──────────────────────────────


def test_absent_returns_defaults():
    """A missing optional config is not a failure — the caller reports `None`."""
    assert load_config(None) == Config()


def test_empty_returns_defaults():
    """`b""` is a file someone created and has not filled in yet."""
    assert load_config(b"") == Config()
    assert load_config(b"\n  \n") == Config()


def test_defaults_are_the_unconfigured_states():
    """Named here so a later change to any default is a deliberate one."""
    assert Config() == Config(blended_hourly_rate=0.0, pm_handle="", verbose_logging=False)


# ── Valid ────────────────────────────────────────────────────────────────────


def test_a_declared_key_at_its_declared_type_is_carried():
    config = load_config(
        b'blended_hourly_rate = 85.5\npm_handle = "pm@example.org"\nverbose_logging = true\n'
    )
    assert config == Config(
        blended_hourly_rate=85.5,
        pm_handle="pm@example.org",
        verbose_logging=True,
    )


def test_an_omitted_key_keeps_its_default_while_others_load():
    """Partial files are the normal case; only the stated keys change."""
    config = load_config(b"verbose_logging = true\n")
    assert config.verbose_logging is True
    assert config.blended_hourly_rate == Config().blended_hourly_rate
    assert config.pm_handle == Config().pm_handle


def test_an_integer_rate_is_accepted_and_widened():
    """`blended_hourly_rate = 100` is a rate, not a type error."""
    rate = load_config(b"blended_hourly_rate = 100\n").blended_hourly_rate
    assert rate == 100.0
    assert isinstance(rate, float)


# ── The encryption family ────────────────────────────────────────────────────


def test_the_encryption_toggle_is_refused_by_name():
    """The acceptance criterion: the reader learns where the setting *does* live."""
    with pytest.raises(ConfigRefused) as exc:
        load_config(b"disable_encryption = true\n")
    assert DISABLE_ENCRYPTION_VAR in str(exc.value)


def test_the_encryption_refusal_wins_over_the_unknown_key_sweep():
    """`encryption_mode` matches two matrix rows; precedence is declared.

    Both refusals are true, and only one is useful — "unknown key" would answer
    a deliberate attempt at the forbidden thing with a shrug.
    """
    with pytest.raises(ConfigRefused) as exc:
        load_config(b'encryption_mode = "off"\n')
    assert DISABLE_ENCRYPTION_VAR in str(exc.value)
    assert "not a setting" not in str(exc.value)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"[encryption]\n", id="empty-table"),
        pytest.param(b"[encryption]\ndisable = true\n", id="table-with-key"),
        pytest.param(b"encryption.disable = true\n", id="dotted-key"),
        pytest.param(b"[settings]\nencrypt = false\n", id="nested-under-another-table"),
        pytest.param(b"[[profiles]]\ncipher = \"none\"\n", id="array-of-tables"),
        pytest.param(b"plaintext_credentials = true\n", id="another-family-member"),
        # The module docstring promises case-folding, and nothing exercised it.
        pytest.param(b"ENCRYPTION = true\n", id="upper-case"),
        pytest.param(b"[Settings]\nDisable_Cipher = true\n", id="mixed-case-nested"),
    ],
)
def test_the_family_is_matched_on_the_full_dotted_path(raw: bytes):
    """Burying it in a table does not evade the check — nor does an empty one."""
    with pytest.raises(ConfigRefused) as exc:
        load_config(raw)
    assert DISABLE_ENCRYPTION_VAR in str(exc.value)


def test_enabling_encryption_from_the_file_is_refused_too():
    """The objection is to the channel, not to the value."""
    with pytest.raises(ConfigRefused):
        load_config(b"encryption = true\n")


def test_the_refusal_names_the_variable_the_daemon_actually_reads():
    """The name is prose in `core.config`, so pin it to the module that reads it.

    `test_doctor.py::test_the_environment_is_read_in_exactly_one_place` forbids
    any other module from binding the name to a constant, so this cannot be an
    equality between two constants. A drifted spelling would produce a refusal
    pointing at a variable nothing reads, which is worse than no message.
    """
    with pytest.raises(ConfigRefused) as exc:
        load_config(b"encryption_mode = \"off\"\n")
    assert DISABLE_ENCRYPTION_VAR in str(exc.value)


# ── The closed vocabulary ────────────────────────────────────────────────────


def test_an_unknown_key_is_refused_and_the_accepted_set_listed():
    """A typo that silently does nothing reads as configured forever."""
    with pytest.raises(ConfigRefused) as exc:
        load_config(b"verbose_loging = true\n")
    message = str(exc.value)
    assert "verbose_loging" in message
    for key in ACCEPTED_KEYS:
        assert key in message


def test_an_unknown_table_is_refused_by_its_top_level_name():
    with pytest.raises(ConfigRefused) as exc:
        load_config(b"[reporting]\nweekly = true\n")
    assert "`reporting`" in str(exc.value)


def test_the_accepted_set_is_the_dataclass():
    """One place states what the file may say; nothing restates it."""
    assert ACCEPTED_KEYS == {field.name for field in fields(Config)}
    assert ACCEPTED_KEYS == {"blended_hourly_rate", "pm_handle", "verbose_logging"}


# ── Types ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected", "given"),
    [
        pytest.param(b"blended_hourly_rate = true\n", "number", "a boolean", id="bool-for-number"),
        pytest.param(b'verbose_logging = "yes"\n', "true", "a string", id="string-for-bool"),
        pytest.param(b"verbose_logging = 1\n", "false", "a number", id="number-for-bool"),
        pytest.param(b"pm_handle = 42\n", "string", "a number", id="number-for-string"),
        pytest.param(b"[pm_handle]\nvalue = 1\n", "string", "a table", id="table-for-string"),
        pytest.param(b'pm_handle = ["a"]\n', "string", "an array", id="array-for-string"),
        pytest.param(b"pm_handle = 1979-05-27\n", "string", "a date", id="date-for-string"),
    ],
)
def test_a_wrong_type_is_refused_naming_both_the_expected_and_the_given(
    raw: bytes, expected: str, given: str
):
    """Both halves of the message, because only one of them was ever verified.

    Asserting the expected type alone let `_type_name` be wrong in five of its
    six branches: swapping its `bool` and `(int, float)` arms makes the loader
    say "must be a number, but config.toml gives a number" and a suite that
    checked only the first half stayed green (review 2026-09-02). The `given`
    column is the fix, and every branch of `_type_name` now has a row.
    """
    with pytest.raises(ConfigRefused) as exc:
        load_config(raw)
    message = str(exc.value)
    assert expected in message
    assert given in message


def test_a_bool_is_not_accepted_where_a_float_is_declared():
    """`isinstance(True, int)` is True, so this needs its own refusal.

    A `true` reaching `Meeting.man_hour_cost` as `1.0` prices every meeting at
    one currency unit per attendee-hour: wrong, and indistinguishable from
    computed. Kept as its own test beside the table above because it is the one
    type confusion Python itself invites.
    """
    with pytest.raises(ConfigRefused) as exc:
        load_config(b"blended_hourly_rate = true\n")
    message = str(exc.value)
    assert "blended_hourly_rate" in message
    assert "1.0" in message


def test_a_number_for_a_bool_is_refused_rather_than_interpreted():
    """The opposite choice from `environment.TRUTHY`, because TOML has booleans."""
    with pytest.raises(ConfigRefused) as exc:
        load_config(b"verbose_logging = 1\n")
    assert "verbose_logging" in str(exc.value)


# ── Type-valid but unusable ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"blended_hourly_rate = -5.0\n", id="negative"),
        pytest.param(b"blended_hourly_rate = 0\n", id="zero-is-the-unset-state"),
        pytest.param(b"blended_hourly_rate = nan\n", id="nan"),
        pytest.param(b"blended_hourly_rate = inf\n", id="inf"),
    ],
)
def test_an_unusable_rate_is_refused_with_its_admissible_range(raw: bytes):
    with pytest.raises(ConfigRefused) as exc:
        load_config(raw)
    message = str(exc.value)
    assert "blended_hourly_rate" in message
    assert "greater than 0" in message


@pytest.mark.parametrize(
    "raw",
    [pytest.param(b'pm_handle = ""\n', id="empty"), pytest.param(b'pm_handle = "  "\n', id="blank")],
)
def test_a_blank_handle_is_refused_naming_the_key(raw: bytes):
    """Absent means unset; writing an empty value states a setting that matches nothing."""
    with pytest.raises(ConfigRefused) as exc:
        load_config(raw)
    assert "pm_handle" in str(exc.value)


def test_a_handle_is_kept_exactly_as_written():
    """Not stripped — a silent trim hides the edit that made equality start working."""
    assert load_config(b'pm_handle = " pm@example.org "\n').pm_handle == " pm@example.org "


# ── Encoding and syntax ──────────────────────────────────────────────────────


def test_a_utf8_bom_is_stripped_before_parsing():
    """An editor-added BOM must not make a valid file read as malformed."""
    assert load_config(b'\xef\xbb\xbfpm_handle = "a@b.c"\n') == Config(pm_handle="a@b.c")


def test_a_bom_only_file_is_an_empty_one():
    assert load_config(b"\xef\xbb\xbf") == Config()


def test_non_utf8_bytes_are_refused_at_decode_distinctly_from_bad_syntax():
    """An encoding fault and a syntax fault are different fixes."""
    with pytest.raises(ConfigRefused) as exc:
        load_config(b'pm_handle = "\xff\xfe"\n')
    message = str(exc.value)
    assert "UTF-8" in message
    assert "TOML" not in message


def test_malformed_toml_is_refused_naming_the_line():
    with pytest.raises(ConfigRefused) as exc:
        load_config(b'pm_handle = "a@b.c"\n[truncated\n')
    message = str(exc.value)
    assert "TOML" in message
    assert "line 2" in message


# ── Refuse or return: the contract holds for pathological input too ──────────


def test_a_pathologically_deep_document_is_refused_not_raised_from():
    """`RecursionError` is not a refusal, and it was what came back.

    A 2000-part dotted key is legal TOML text; `tomllib` gives up at 1000 parts
    and does so with `RecursionError`, not `TOMLDecodeError`. A module whose
    whole contract is refuse-or-return must not leak the parser's stack limit.
    """
    with pytest.raises(ConfigRefused):
        load_config(b"a." * 2000 + b"b = 1\n")


def test_nesting_past_the_walk_bound_is_refused_with_the_depth_named():
    """The bound the walk enforces itself, below anything `tomllib` refuses."""
    with pytest.raises(ConfigRefused) as exc:
        load_config(b"a." * 40 + b"b = 1\n")
    message = str(exc.value)
    assert "nests more than" in message
    assert "a.a.a" in message


def test_an_integer_too_large_for_a_float_is_refused_not_overflowed():
    """`tomllib` returns arbitrary-precision ints; `float()` has a ceiling.

    A 400-digit rate parsed perfectly and then raised `OverflowError` out of the
    one call in the module that could raise anything but `ConfigRefused`.
    """
    with pytest.raises(ConfigRefused) as exc:
        load_config(b"blended_hourly_rate = " + b"9" * 400 + b"\n")
    message = str(exc.value)
    assert "blended_hourly_rate" in message
    assert "400-digit" in message


def test_a_decode_offset_is_reported_against_the_file_not_the_stripped_buffer():
    """The BOM shifts every byte position by three.

    Reporting the offset into the buffer `_decode` sliced misdirects exactly the
    files it went out of its way to tolerate — an off-by-three in the one number
    the message exists to carry.
    """
    body = b'pm_handle = "\xff"\n'
    without = str(pytest.raises(ConfigRefused, lambda: load_config(body)).value)
    with_bom = str(pytest.raises(ConfigRefused, lambda: load_config(b"\xef\xbb\xbf" + body)).value)
    assert "byte 13" in without
    assert "byte 16" in with_bom


# ── Invariants the class holds, not only the loader ──────────────────────────


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"pm_handle": "   "}, id="whitespace-handle"),
        pytest.param({"pm_handle": "\t"}, id="tab-handle"),
        pytest.param({"blended_hourly_rate": -5.0}, id="negative-rate"),
        pytest.param({"blended_hourly_rate": float("nan")}, id="nan-rate"),
        pytest.param({"blended_hourly_rate": float("inf")}, id="inf-rate"),
        pytest.param({"blended_hourly_rate": True}, id="bool-rate"),
    ],
)
def test_a_nonsense_value_is_refused_at_construction(kwargs: dict):
    """`load_config` is not the only way in, so the rules cannot live only there.

    `build(config=...)` takes a `Config` directly, this suite constructs them,
    and `4c` will too. Until the class checked itself, `Config(pm_handle="   ")`
    constructed happily — and a whitespace handle both defeats `extract()`'s
    `bool(pm_handle)` guard and matches any speaker carrying the same
    whitespace, which is an AD-32 fail-open reachable without any file at all.
    """
    with pytest.raises(ConfigRefused):
        Config(**kwargs)


def test_the_unset_states_are_constructible():
    """The class permits what the *file* may not write: absent is not a mistake."""
    assert Config(pm_handle="", blended_hourly_rate=0.0) == Config()


# ── The vocabulary is bound to what the loader consumes ──────────────────────


ROUND_TRIP = {
    "blended_hourly_rate": (b"blended_hourly_rate = 42.5\n", 42.5),
    "pm_handle": (b'pm_handle = "pm@example.org"\n', "pm@example.org"),
    "verbose_logging": (b"verbose_logging = true\n", True),
}


def test_every_accepted_key_round_trips_into_the_config():
    """`ACCEPTED_KEYS` is derived; the loader's three reads are hand-written.

    Nothing tied the two together, so a fourth field would have been *admitted*
    by the unknown-key sweep and then silently dropped on the floor — the exact
    "reads as configured while having no effect" failure this module exists to
    prevent, now wearing the costume of a declared key. The first assertion is
    what fails when a field is added without a read to match.
    """
    assert set(ROUND_TRIP) == ACCEPTED_KEYS
    for key, (raw, expected) in ROUND_TRIP.items():
        assert getattr(load_config(raw), key) == expected


# ── Pins ─────────────────────────────────────────────────────────────────────


def test_the_refusal_is_a_value_error():
    """The documented base class, and what `4c` will catch to exit `3`."""
    assert issubclass(ConfigRefused, ValueError)


def test_nan_and_inf_are_what_toml_calls_them():
    """Guards the two parametrized rows above: `nan`/`inf` are real TOML floats.

    If they were syntax errors the refusal would come from the parser, and those
    cases would be testing nothing about the range check.
    """
    import tomllib

    assert math.isnan(tomllib.loads("x = nan")["x"])
    assert math.isinf(tomllib.loads("x = inf")["x"])
