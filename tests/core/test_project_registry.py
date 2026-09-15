"""`projects.toml`, parsed and rendered — one test per matrix row of story 4d.

The rows this file does *not* cover are the two the slice moved elsewhere on
2026-09-15, and they are named here so a reader does not go looking:

- **"same id, different path"** went to `4k`. `render_registry` takes a mapping
  keyed by id, so two paths under one id cannot be handed to it; a move is
  something the read-modify-write behind `pm-ai project add` meets, not the
  serializer.
- **"registry unreadable"** is not a parse outcome at all. It is a state of the
  read that happens before these bytes exist, carried by `ArtifactState` in
  `pm_ai.app.wiring` and reported by `doctor`. Covered in `tests/slice`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pm_ai.core.project_registry import (
    DuplicateProject,
    ProjectEntry,
    ProjectPathUnusable,
    RegistryMalformed,
    parse_registry,
    render_registry,
)

ALPHA = ProjectEntry(path=Path("/srv/alpha"), alias="Alpha")
BETA = ProjectEntry(path=Path("/srv/beta"), alias=None)


# ── Absence and emptiness are ordinary ───────────────────────────────────────


def test_an_absent_registry_parses_to_an_empty_mapping():
    """A first run, not an error — the state every machine starts in."""
    assert parse_registry(None) == {}


def test_a_present_but_empty_registry_parses_to_an_empty_mapping():
    """Distinct from absent to the *caller*, identical to the parser.

    `doctor` draws the distinction that matters, because "reachable, nothing
    stored" and "no file" have the same remedy — `pm-ai project add` — and a
    parser that refused one of them would make a rendered-then-emptied registry
    unreadable.
    """
    assert parse_registry(render_registry({})) == {}
    assert parse_registry(b"") == {}


# ── The round trip, which is the whole guarantee ─────────────────────────────


def test_one_entry_round_trips_through_render_and_parse():
    assert parse_registry(render_registry({"alpha": ALPHA})) == {"alpha": ALPHA}


def test_an_entry_without_an_alias_round_trips_as_none():
    """`alias=None` must not come back as the empty string.

    The alias is a display label and its absence is a real state: `4k` makes it
    optional, so a registry written without one has to read back without one
    rather than with a label that renders as nothing.
    """
    assert parse_registry(render_registry({"beta": BETA})) == {"beta": BETA}


@pytest.mark.parametrize(
    "mapping",
    [
        {},
        {"alpha": ALPHA},
        {"alpha": ALPHA, "beta": BETA},
        {"a-b_c.d": ProjectEntry(path=Path("/srv/x"), alias="Quoted \"name\"\n\ttab")},
        {"unicode": ProjectEntry(path=Path("/srv/ünïcode"), alias="ünïcode")},
    ],
)
def test_render_then_parse_is_the_identity(mapping):
    """The drift pair `4g` guards for `config.toml`, on this file.

    A serializer and a parser that disagree is the defect no single-direction
    test can see, and the escaping cases are where they disagree first.
    """
    assert parse_registry(render_registry(mapping)) == mapping


def test_adding_to_a_two_entry_registry_keeps_all_three():
    """Asserted on the bytes, because `write_artifact` replaces the file whole.

    An interface that accepted one entry would let a caller publish a file
    holding only that entry, and the other two would be gone with nothing
    raising. `render_registry` takes the whole mapping, so this is the shape of
    the only write it permits.
    """
    held = {"alpha": ALPHA, "beta": BETA}
    rendered = render_registry({**held, "gamma": ProjectEntry(path=Path("/srv/gamma"))})

    assert parse_registry(rendered).keys() == {"alpha", "beta", "gamma"}


# ── Refusals ─────────────────────────────────────────────────────────────────


def test_a_colliding_alias_is_refused_and_names_both_projects():
    """Two projects answering to one label, refused at the serializer.

    The only duplicate this slice can see: ids are the mapping's keys and
    therefore unique by construction, while aliases are values and are not.
    """
    with pytest.raises(DuplicateProject) as refused:
        render_registry(
            {
                "alpha": ProjectEntry(path=Path("/srv/alpha"), alias="Same"),
                "beta": ProjectEntry(path=Path("/srv/beta"), alias="Same"),
            }
        )

    assert "Same" in str(refused.value)
    assert "alpha" in str(refused.value) and "beta" in str(refused.value)


def test_a_relative_path_in_the_file_is_refused_naming_the_project():
    """`_absolute_map` would take `./alpha` as-is, against whatever CWD the
    daemon happened to start in. Refused rather than resolved: resolving needs a
    working directory and `core` has none.
    """
    with pytest.raises(ProjectPathUnusable) as refused:
        parse_registry(b'[projects.alpha]\npath = "./alpha"\n')

    assert "alpha" in str(refused.value)


def test_a_relative_path_is_refused_at_the_serializer_too():
    """Both directions, for the reason `4g` learned the hard way.

    A guarantee asserted only on the way in is a guarantee a writer can break,
    and `render_registry` is what `4k` will call with a path it derived.
    """
    with pytest.raises(ProjectPathUnusable):
        render_registry({"alpha": ProjectEntry(path=Path("relative/alpha"))})


def test_a_duplicate_id_in_the_file_is_refused_rather_than_last_wins():
    """Pinned, not branched — `tomllib` already refuses both shapes.

    Measured 2026-09-15: a repeated table raises `Cannot declare ('projects',
    'alpha') twice` and a repeated key `Cannot overwrite a value`. This test
    exists so that guarantee survives a parser swap, since the failure mode it
    prevents — the second entry silently winning — is invisible in the result.
    """
    repeated_table = b'[projects.alpha]\npath = "/a"\n[projects.alpha]\npath = "/b"\n'

    with pytest.raises(RegistryMalformed):
        parse_registry(repeated_table)


def test_malformed_toml_is_refused_naming_the_position():
    """Never silently reset. A registry that parses to empty is a registry the
    next `project add` mints over, and every enrolled project is forgotten —
    Tier 1 and rebuildable from nothing (`scope_model.py:440`).
    """
    with pytest.raises(RegistryMalformed) as refused:
        parse_registry(b'[projects.alpha]\npath = "unterminated\n')

    assert "line" in str(refused.value).lower()


def test_a_project_missing_its_path_is_refused():
    with pytest.raises(RegistryMalformed) as refused:
        parse_registry(b'[projects.alpha]\nalias = "Alpha"\n')

    assert "path" in str(refused.value)


def test_an_unknown_key_under_a_project_is_refused_rather_than_ignored():
    """A typo'd `pathh` beside no `path` would otherwise read as a project with
    no location, and the same key on a future version of this file would read as
    a setting that has no effect — `4a`'s rule, on the other TOML file.
    """
    with pytest.raises(RegistryMalformed) as refused:
        parse_registry(b'[projects.alpha]\npath = "/a"\npathh = "/b"\n')

    assert "pathh" in str(refused.value)


def test_a_non_string_path_is_refused():
    with pytest.raises(RegistryMalformed):
        parse_registry(b"[projects.alpha]\npath = 7\n")


def test_a_file_that_is_not_utf8_is_refused_as_an_encoding_problem():
    """A different fix from a syntax error, so a different message."""
    with pytest.raises(RegistryMalformed) as refused:
        parse_registry(b'[projects.alpha]\npath = "/\xff"\n')

    assert "UTF-8" in str(refused.value)
