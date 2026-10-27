"""Story 15b — the commitment index's rows, values, snapshot and replace.

The pinned values, the snapshot's order, a rebuild of a subset and the
conditional, difference-only replace, over a wired daemon's store beneath
`tmp_path`. The matrix rows the daemon reaches are in
`tests/slice/test_commitment_index.py`.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from commitment_index_fixtures import ALPHA, PERSONAL, after, commitment, ledger, marked, pending
from pm_ai.app.wiring import build
from pm_ai.core.commitment_index import (
    commitment_index_snapshot,
    derive,
    index_rows,
    rebuild_commitment_index,
    stored_value,
)
from pm_ai.core.commitment_log import fold, parse_ledger
from pm_ai.core.jobs import source_md5
from pm_ai.domain.commitment_index import IndexRow
from pm_ai.domain.commitments import COMMITMENTS_LOG, Mark
from pm_ai.storage import commitment_index_store as index_store


@pytest.fixture
def storage(tmp_path):
    return build(tmp_path, "alpha").storage


def _write(storage, data: bytes, scope=PERSONAL) -> None:
    path = Path(storage.paths.resolve(scope, COMMITMENTS_LOG, create=True))
    path.write_bytes(data)


# ── The checksum and the pinned values ───────────────────────────────────────


def test_the_checksum_is_md5_over_the_raw_bytes():
    lf = b"- a line\n"
    crlf = b"- a line\r\n"
    assert source_md5(lf) == hashlib.md5(lf).hexdigest()
    assert source_md5(lf) != source_md5(crlf), "no normalisation: CRLF and LF differ"
    assert len(source_md5(b"")) == 32 and source_md5(b"") == source_md5(b"").lower()


def test_values_are_stored_in_their_pinned_forms():
    berlin = datetime(2026, 10, 19, 13, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    assert stored_value(berlin) == "2026-10-19T11:00:00.000000+00:00"
    assert stored_value(datetime(2026, 10, 19, 12, 0, 0, 5, tzinfo=timezone.utc)) == "2026-10-19T12:00:00.000005+00:00"
    assert stored_value(date(2026, 10, 22)) == "2026-10-22"
    assert stored_value(True) == 1 and stored_value(False) == 0
    assert stored_value(None) is None and stored_value("") is None
    assert stored_value("pending") == "pending"
    with pytest.raises(TypeError):
        stored_value(datetime(2026, 10, 19, 12, 0))  # naive: it would depend on the machine's zone


def test_a_pending_line_becomes_one_row_written_by_hand():
    data = ledger(pending(commitment("prp_one")))
    (row,) = index_rows("personal", fold(parse_ledger(data)), source_md5(data))
    assert row == IndexRow(
        scope="personal",
        commitment_id="prp_one",
        proposal_id="prp_one",
        type="action_item",
        state="pending",
        state_source="none",
        state_at=None,
        due_value="2026-10-22",
        due_date="2026-10-22",
        due_estimated=0,
        due_by_pm=0,
        meeting_id="mtg_a",
        meeting_start="2026-10-19T10:00:00.000000+00:00",
        day="2026-10-19",
        day_from=None,
        approved_at="2026-10-19T12:00:00.000000+00:00",
        changed_at="2026-10-19T12:00:00.000000+00:00",
        version=parse_ledger(data).lines[0].entry,
        malformed_named=0,
        source_md5=hashlib.md5(data).hexdigest(),
    )


# ── Derive, snapshot, rebuild, replace ───────────────────────────────────────


def test_a_current_scope_is_neither_folded_nor_written(storage):
    _write(storage, ledger(pending(commitment("prp_one"))))
    assert derive(storage, PERSONAL).outcome == "derived"
    path = Path(storage.paths.resolve(PERSONAL, COMMITMENTS_LOG))
    index = Path(storage.paths.resolve(_app(), index_store.COMMITMENT_INDEX))
    stamp = index.stat().st_mtime_ns
    assert derive(storage, PERSONAL).outcome == "current"
    assert index.stat().st_mtime_ns == stamp and path.exists()


def _app():
    from pm_ai.domain.identity import DataScope, ScopeKind

    return DataScope(ScopeKind.APPLICATION)


def test_a_scope_with_no_commitment_and_no_rows_creates_no_file(storage):
    _write(storage, b"# only a heading\n")
    assert derive(storage, PERSONAL).outcome == "current"
    assert not Path(storage.paths.resolve(_app(), "commitment_index.db")).exists()


def test_the_snapshot_does_not_depend_on_the_order_rows_were_written(tmp_path):
    first = build(tmp_path / "a", "alpha").storage
    second = build(tmp_path / "b", "alpha").storage
    mine = ledger(pending(commitment("prp_one")), pending(commitment("prp_two")))
    theirs = ledger(pending(commitment("prp_alpha_one")))
    for store in (first, second):
        _write(store, mine)
        _write(store, theirs, ALPHA)
    derive(first, PERSONAL)
    derive(first, ALPHA)
    derive(second, ALPHA)
    rows = index_rows("personal", fold(parse_ledger(mine)), source_md5(mine))
    second.replace_commitment_rows(PERSONAL, tuple(reversed(rows)), frozenset())
    assert commitment_index_snapshot(first) == commitment_index_snapshot(second)
    assert [(row.scope, row.commitment_id) for row in commitment_index_snapshot(first)] == [
        ("personal", "prp_one"),
        ("personal", "prp_two"),
        ("project:alpha", "prp_alpha_one"),
    ]


def test_a_subset_rebuild_leaves_the_other_scopes_rows_alone(storage):
    """Matrix "Rebuild a subset"."""
    _write(storage, ledger(pending(commitment("prp_one"))))
    _write(storage, ledger(pending(commitment("prp_alpha_one"))), ALPHA)
    derive(storage, PERSONAL)
    derive(storage, ALPHA)
    alpha = storage.commitment_index_rows("project:alpha")
    _write(storage, ledger(pending(commitment("prp_alpha_one")), pending(commitment("prp_alpha_two"))), ALPHA)
    _write(storage, ledger(pending(commitment("prp_one")), pending(commitment("prp_two"))))
    report = rebuild_commitment_index(storage, [PERSONAL])
    assert report.count("derived") == 1
    assert [row.commitment_id for row in storage.commitment_index_rows("personal")] == ["prp_one", "prp_two"]
    assert storage.commitment_index_rows("project:alpha") == alpha


def test_the_replace_writes_nothing_when_the_checksums_moved(storage):
    data = ledger(pending(commitment("prp_one")))
    _write(storage, data)
    derive(storage, PERSONAL)
    kept = storage.commitment_index_rows()
    newer = data + ledger(pending(commitment("prp_two")))
    rows = index_rows("personal", fold(parse_ledger(newer)), source_md5(newer))
    replaced = storage.replace_commitment_rows(PERSONAL, rows, frozenset({"not-what-is-stored"}))
    assert replaced.changed and (replaced.written, replaced.removed) == (0, 0)
    assert storage.commitment_index_rows() == kept


def test_the_replace_writes_only_the_rows_that_changed(storage):
    data = ledger(*(pending(commitment(f"prp_{n}")) for n in range(5)))
    _write(storage, data)
    first = derive(storage, PERSONAL)
    assert (first.written, first.removed) == (5, 0)
    data += ledger(marked("prp_3", Mark.FULFILLED, after(1)))
    _write(storage, data)
    second = derive(storage, PERSONAL)
    assert (second.outcome, second.written, second.removed) == ("derived", 1, 0)
    assert {row.source_md5 for row in storage.commitment_index_rows()} == {source_md5(data)}
    trimmed = ledger(*(pending(commitment(f"prp_{n}")) for n in range(2)))
    _write(storage, trimmed)
    third = derive(storage, PERSONAL)
    assert (third.written, third.removed) == (0, 3)


def test_rows_from_another_scope_or_checksum_are_refused(storage):
    data = ledger(pending(commitment("prp_one")))
    rows = index_rows("personal", fold(parse_ledger(data)), source_md5(data))
    with pytest.raises(ValueError):
        storage.replace_commitment_rows(ALPHA, rows, frozenset())
    mixed = (rows[0], replace(rows[0], commitment_id="prp_two", source_md5="0" * 32))
    with pytest.raises(ValueError):
        storage.replace_commitment_rows(PERSONAL, mixed, frozenset())
