"""Story 7e — the saved alias table: a name the PM confirmed → a handle (AD-34).

Pure: rows rendered and read back, `normalise`, scoped and removal rows, the
newest row winning, malformed lines skipped and counted.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pm_ai.core.aliases import AliasRow, normalise, parse_aliases, render_alias_row
from pm_ai.domain.handles import gitlab_handle

AT = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
IVAN = gitlab_handle("https://gitlab.example.com", 17)
PETR = gitlab_handle("https://gitlab.example.com", 18)
assert IVAN is not None and PETR is not None


def _table(*rows: AliasRow, extra: str = "") -> bytes:
    return ("".join(render_alias_row(row) + "\n" for row in rows) + extra).encode("utf-8")


def _row(said: str, handle=IVAN, *, meeting: str | None = None, at: datetime = AT) -> AliasRow:
    return AliasRow(normalise(said), said, handle, meeting, at)


def test_normalise_is_nfkc_casefolded_trimmed_and_one_space():
    assert normalise(" ВАНЯ ") == normalise("Ваня") == "ваня"
    assert normalise("Ana\t  Ivanova") == "ana ivanova"
    assert normalise("ＡＮＡ") == "ana"  # full-width letters fold under NFKC


def test_normalise_composes_again_after_case_folding():
    """`ǰ` folds to `j` + a combining caron; the key is NFKC-normal and idempotent."""
    import unicodedata

    key = normalise("ǰ")
    assert unicodedata.is_normalized("NFKC", key)
    assert key == normalise("J̌") == normalise(key)
    assert "ǰ".casefold() == "ǰ"  # what the second pass repairs


def test_a_row_reads_back_as_written():
    table = parse_aliases(_table(_row("Ваня")))
    assert table.handle_for("Ваня", None) == IVAN
    assert table.handle_for(" ВАНЯ ", "mtg_x") == IVAN  # variant spelling, same key
    (row,) = table.rows
    assert row.said == "Ваня" and row.meeting is None and row.at == AT


def test_a_name_with_spaces_and_quotes_stays_one_value():
    said = 'Ivan "the" Petrov'
    table = parse_aliases(_table(_row(said)))
    assert table.handle_for(said, None) == IVAN


def test_a_later_row_replaces_and_a_removal_ends_the_mapping():
    later = AT + timedelta(days=1)
    table = parse_aliases(_table(_row("Ваня"), _row("Ваня", PETR, at=later)))
    assert table.handle_for("Ваня", None) == PETR
    removed = parse_aliases(
        _table(_row("Ваня"), AliasRow("ваня", "Ваня", None, None, later))
    )
    assert removed.handle_for("Ваня", None) is None
    assert removed.row_for("Ваня", None) is not None and removed.row_for("Ваня", None).removal


def test_a_meeting_row_applies_to_that_meeting_only():
    table = parse_aliases(_table(_row("Speaker 2", meeting="mtg_a")))
    assert table.handle_for("Speaker 2", "mtg_a") == IVAN
    assert table.handle_for("Speaker 2", "mtg_b") is None
    assert table.handle_for("Speaker 2", None) is None


def test_a_meeting_row_wins_over_the_unscoped_one():
    table = parse_aliases(_table(_row("Ваня"), _row("Ваня", PETR, meeting="mtg_a")))
    assert table.handle_for("Ваня", "mtg_a") == PETR
    assert table.handle_for("Ваня", "mtg_b") == IVAN


def test_malformed_lines_are_skipped_and_counted_and_headings_are_not():
    extra = (
        "# Aliases, by hand\n"
        "\n"
        "- name=ваня said=Ваня handle=not-a-handle at=2026-10-14T09:00:00+00:00\n"
        "- name=wrong said=Ваня handle=gitlab:gitlab.example.com:17 at=2026-10-14T09:00:00+00:00\n"
        "garbage\n"
        "- name=x said=x handle=gitlab:gitlab.example.com:17"  # no newline: not a record
    )
    table = parse_aliases(_table(_row("Ana"), extra=extra))
    assert table.skipped == 3
    assert table.handle_for("Ana", None) == IVAN
    assert table.handle_for("x", None) is None


def test_absent_and_empty_are_the_empty_table():
    assert parse_aliases(None).rows == () and parse_aliases(b"").skipped == 0


def test_a_row_whose_key_is_not_its_name_normalised_is_refused():
    with pytest.raises(ValueError):
        render_alias_row(AliasRow("other", "Ваня", IVAN, None, AT))
    with pytest.raises(ValueError):
        render_alias_row(AliasRow("ваня", "Ваня", IVAN, None, AT.replace(tzinfo=None)))
