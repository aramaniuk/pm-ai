"""`read_pm_identity` — the one caller of `pm_identity`, over real saved rows (story 2o).

Spec: `_bmad-output/specs/spec-pm-ai/stories/2o-people-are-read-as-people.md`,
acceptance criteria one and two, and the matrix rows that need the read: an
enabled row with no usable user id is listed as unusable, and an alias table
filled in memory is not consulted.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from pm_ai.app.pipelines import run_transcript_ingestion
from pm_ai.app.wiring import build, read_pm_identity
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.transcripts import Transcript, TranscriptSource, Utterance
from pm_ai.storage.service import StorageService
from pm_ai.domain.handles import EMAIL, GRAPH, person_handle, register_alias
from pm_ai.domain.identity import Actor, DataScope, ScopeKind

NOW = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
APPLICATION = DataScope(ScopeKind.APPLICATION)
U1 = "0D9E3C4F-1A2B-4C3D-8E9F-A0B1C2D3E4F5"
U2 = "11111111-2222-4333-8444-555555555555"
U3 = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


@pytest.fixture
def daemon(tmp_path):
    return build(tmp_path, "alpha", now=lambda: NOW)


def _save(daemon, name: str, row: object) -> None:
    """A `connectors/<name>.json` row, saved through the single writer."""
    payload = row if isinstance(row, bytes) else json.dumps(row).encode("utf-8")
    daemon.storage.write_artifact(
        payload, scope=APPLICATION, artifact="connectors/", name=f"{name}.json"
    )


def _graph(name: str, user_id: object, *, enabled: bool = True) -> dict:
    row: dict[str, object] = {"instance": name, "system": "graph", "enabled": enabled}
    if user_id is not None:
        row["user_id"] = user_id
    return row


def test_two_saved_graph_rows_one_disabled_give_both_handles(daemon):
    """Acceptance 1, first half."""
    _save(daemon, "graph:work", _graph("graph:work", U1))
    _save(daemon, "graph:old", _graph("graph:old", U2, enabled=False))

    reading = read_pm_identity(daemon.storage)

    assert reading.identity == {person_handle(GRAPH, U1), person_handle(GRAPH, U2)}
    assert reading.unusable == ()


def test_a_row_added_while_the_daemon_runs_is_in_the_next_call(daemon):
    """Acceptance 1, second half: nothing is cached between calls."""
    _save(daemon, "graph:work", _graph("graph:work", U1))
    _save(daemon, "graph:old", _graph("graph:old", U2, enabled=False))
    first = read_pm_identity(daemon.storage)

    _save(daemon, "graph:third", _graph("graph:third", U3))

    assert person_handle(GRAPH, U3) not in first.identity
    assert read_pm_identity(daemon.storage).identity == {
        person_handle(GRAPH, U1),
        person_handle(GRAPH, U2),
        person_handle(GRAPH, U3),
    }


@pytest.mark.parametrize("user_id", [None, "", "  ", 42, "not-a-graph-user-id"])
def test_an_enabled_graph_row_with_no_usable_user_id_is_listed_in_the_same_result(
    daemon, user_id
):
    """Acceptance 2, and the matrix's "listed as unusable if enabled"."""
    _save(daemon, "graph:work", _graph("graph:work", U1))
    _save(daemon, "graph:blank", _graph("graph:blank", user_id))

    reading = read_pm_identity(daemon.storage)

    assert reading.identity == {person_handle(GRAPH, U1)}
    assert [row.name for row in reading.unusable] == ["graph:blank.json"]
    assert "no usable user id" in reading.unusable[0].reason


def test_a_disabled_row_with_no_user_id_is_not_listed(daemon):
    _save(daemon, "graph:off", _graph("graph:off", None, enabled=False))

    reading = read_pm_identity(daemon.storage)

    assert reading.identity == frozenset() and reading.unusable == ()


def test_a_row_that_is_not_graph_is_neither_identity_nor_unusable(daemon):
    _save(daemon, "gitlab:alpha", {"instance": "gitlab:alpha", "system": "gitlab", "enabled": True})

    reading = read_pm_identity(daemon.storage)

    assert reading.identity == frozenset() and reading.unusable == ()


@pytest.mark.parametrize(
    ("raw", "said"),
    [
        (b"{not json", "not valid JSON"),
        (b"[1, 2]", "holds a list"),
        (b'{"system": "graph", "user_id": "\xff"}', "not UTF-8"),
    ],
    ids=["not JSON", "not an object", "not UTF-8"],
)
def test_an_unreadable_row_is_listed_and_the_others_still_count(daemon, raw, said):
    _save(daemon, "graph:work", _graph("graph:work", U1))
    _save(daemon, "graph:broken", raw)

    reading = read_pm_identity(daemon.storage)

    assert reading.identity == {person_handle(GRAPH, U1)}
    assert [(row.name, said in row.reason) for row in reading.unusable] == [
        ("graph:broken.json", True)
    ]


def test_with_no_connectors_the_pm_is_nobody(daemon):
    """Matrix: no Graph row — empty set."""
    reading = read_pm_identity(daemon.storage)
    assert reading.identity == frozenset() and reading.unusable == ()


def test_an_alias_table_filled_in_memory_is_not_consulted(daemon):
    """Matrix: alias table filled in memory — `aliased` stays empty until saved."""
    _save(daemon, "graph:work", _graph("graph:work", U1))
    pm = Actor("person_pm", "PM")
    register_alias(person_handle(GRAPH, U1), pm)
    register_alias(person_handle(EMAIL, "pm@example.com"), pm)

    assert read_pm_identity(daemon.storage).identity == {person_handle(GRAPH, U1)}


@pytest.mark.parametrize("enabled", ["true", 1, None, "yes"])
def test_a_graph_row_whose_enabled_is_not_a_bool_is_listed_naming_it(daemon, enabled):
    """No usable user id and an `enabled` that is neither true nor false."""
    row = _graph("graph:odd", None)
    row["enabled"] = enabled
    _save(daemon, "graph:odd", row)

    (unusable,) = read_pm_identity(daemon.storage).unusable

    assert unusable.name == "graph:odd.json"
    assert repr(enabled) in unusable.reason


def test_a_row_that_cannot_be_read_is_listed_and_the_others_still_count(
    daemon, monkeypatch
):
    _save(daemon, "graph:work", _graph("graph:work", U1))
    _save(daemon, "graph:locked", _graph("graph:locked", U2))
    real = StorageService.read_artifact

    def refusing(self, *, scope, artifact, name=None):
        if name == "graph:locked.json":
            raise PermissionError(13, "Permission denied", name)
        return real(self, scope=scope, artifact=artifact, name=name)

    monkeypatch.setattr(StorageService, "read_artifact", refusing)

    reading = read_pm_identity(daemon.storage)

    assert reading.identity == {person_handle(GRAPH, U1)}
    (unusable,) = reading.unusable
    assert unusable.name == "graph:locked.json"
    assert "Permission denied" in unusable.reason


def test_a_collection_that_cannot_be_listed_is_one_entry_and_nothing_raises(
    daemon, monkeypatch
):
    _save(daemon, "graph:work", _graph("graph:work", U1))

    def refusing(self, *, scope, artifact):
        raise PermissionError(13, "Permission denied", artifact)

    monkeypatch.setattr(StorageService, "list_collection", refusing)

    reading = read_pm_identity(daemon.storage)

    assert reading.identity == frozenset()
    assert [row.name for row in reading.unusable] == ["connectors/"]


def test_a_row_removed_between_the_listing_and_the_read_is_listed(daemon, monkeypatch):
    _save(daemon, "graph:work", _graph("graph:work", U1))
    _save(daemon, "graph:gone", _graph("graph:gone", U2))
    real = StorageService.read_artifact

    def removed(self, *, scope, artifact, name=None):
        if name == "graph:gone.json":
            return None
        return real(self, scope=scope, artifact=artifact, name=name)

    monkeypatch.setattr(StorageService, "read_artifact", removed)

    reading = read_pm_identity(daemon.storage)

    assert reading.identity == {person_handle(GRAPH, U1)}
    assert [(r.name, r.reason) for r in reading.unusable] == [
        ("graph:gone.json", "was removed while it was read")
    ]


def test_a_programming_error_in_a_read_propagates(daemon, monkeypatch):
    """Only storage-read failures are reported; a defect in pm-ai is not hidden."""
    _save(daemon, "graph:work", _graph("graph:work", U1))

    def broken(self, *, scope, artifact, name=None):
        raise AttributeError("a bug, not a storage failure")

    monkeypatch.setattr(StorageService, "read_artifact", broken)

    with pytest.raises(AttributeError):
        read_pm_identity(daemon.storage)


def test_an_unreadable_row_during_ingestion_stages_the_command(daemon):
    """The pipeline reads the identity and carries on: nothing crashes, and with
    no usable row the PM is nobody, so the spoken command stages."""
    _save(daemon, "graph:broken", b"{not json")
    meeting = Meeting(
        meeting_id="mtg_01HX",
        title="Project Alpha sync",
        start=NOW,
        duration_minutes=30,
        attendees=(),
        scope=DataScope(ScopeKind.PROJECT, "alpha"),
        calendar_event_ref="outlook:evt_1",
    )
    transcript = Transcript(
        meeting_id=meeting.meeting_id,
        source=TranscriptSource.GRAPH,
        utterances=(
            Utterance(
                "Andrei",
                "pm-ai, post_comment gitlab:alpha:issue:102 ship it",
                0,
                person_handle(GRAPH, U1),
            ),
        ),
    )

    out = run_transcript_ingestion(daemon, transcript, meeting)

    assert not out["executed"] and len(out["staged"]) == 1
