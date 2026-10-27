"""Story 15c — the PM's commitment commands.

One test per row of the story's I/O matrix that the slice reaches (the
fold's rows are in `tests/core/test_commitment_log.py`, the card's in
`tests/core/test_commitment_cards.py`), plus the two timed tests, the prompt's
routing and completion.

Every row runs against a temporary home with `alpha` enrolled at a real git
repository (13a's `Machine`); the clock is the test's and no test reaches a
model. Names are `11d`'s fixture names (`Example Weekly Sync-up`, `Alex
Morgan`, `Sam Rivera`); handles are `example.com`. Nothing reads the PM's
own store.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_commitments import (
    ITEMS,
    _Rig,
    _approve_in_store,
    _claim_held,
    _folded_one,
    _ledger_path,
    _lines,
    _summarised,
    _text,
)
from test_proposals import ALPHA, NOW, PERSONAL, Machine
from pm_ai.app import commitments as desk_module
from pm_ai.app import entry
from pm_ai.app.commitments import (
    COMMITMENT_WRITE_BOUND_SECONDS,
    CommitmentDesk,
    commitment_choices,
    commitment_owners,
    pm_due,
    pm_handle,
)
from pm_ai.app.entry import CONFIG_SHOW_BOUND_SECONDS, DAEMON_BOUNDS
from pm_ai.app.proposals import ProposalDesk
from pm_ai.core.commitment_log import Marked, Pending, entry_id, fold, parse_ledger, render_line
from pm_ai.domain.commitments import COMMITMENTS_LOG, Mark
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.lifecycle import ProposalState
from pm_ai.ports import LedgerChanged
from pm_ai.surfaces.cli import dispatch as cli
from pm_ai.surfaces.cli.completion import Completion, Readers
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, EXIT_USAGE, TABLE, TERMINAL_BOUND, dispatch

UTC = timezone.utc
SUNDAY = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
LISTED = (
    ("send the cache TTL numbers", "Sam Rivera", "by Thursday"),
    ("book the review room", "Alex Morgan", "30.10"),
    ("draft the release notes", None, "next sprint"),
    ("collect the feedback", "Alex Morgan", None),
)


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def machine(tmp_path, monkeypatch) -> Machine:
    return Machine(tmp_path, monkeypatch)


def _desk(machine: Machine, *, via: str = "terminal", storage=None, handle=None, **options) -> CommitmentDesk:
    store = machine.storage if storage is None else storage
    return CommitmentDesk(
        now=lambda: machine.clock,
        paths=machine.storage.paths,
        storage=store,
        via=via,
        types=machine.daemon.proposal_types,
        zone=UTC,
        handle=handle,
        proposals=ProposalDesk(
            now=lambda: machine.clock,
            paths=machine.storage.paths,
            storage=store,
            via=via,
            types=machine.daemon.proposal_types,
        ),
        **options,
    )


def _run(machine: Machine, *words: str, desk: CommitmentDesk | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = dispatch(
        list(words),
        daemon=machine.daemon,
        diagnose=lambda: None,  # type: ignore[arg-type, return-value]
        probe_connectors=None,
        proposals=machine.desk(),
        commitments=desk if desk is not None else _desk(machine),
        out=out,
        err=err,
        terminal=False,
    )
    return code, out.getvalue(), err.getvalue()


def _json(machine: Machine, *words: str, desk: CommitmentDesk | None = None) -> tuple[int, dict]:
    code, out, err = _run(machine, *words, "--json", desk=desk)
    assert out, err
    return code, json.loads(out)


def _approved(machine: Machine, items=ITEMS, **fields) -> list[str]:
    _, ids = _summarised(machine, items, **fields)
    for each in ids:
        code, _, err = machine.run("proposal", "approve", each)
        assert code == EXIT_OK, err
    return ids


def _marked(machine: Machine, scope: DataScope = PERSONAL) -> list[dict[str, str]]:
    return [line for line in _lines(machine, scope) if line["kind"] == "marked"]


def _append(machine: Machine, text: str, scope: DataScope = PERSONAL) -> None:
    with open(_ledger_path(machine, scope), "a", encoding="utf-8") as ledger:
        ledger.write(text + "\n")


def _verdict(root: str, state: str, at: datetime) -> str:
    when = at.isoformat()
    entry = entry_id((root, "verdict", state, when))
    return f"- entry={entry} commitment={root} kind=verdict state={state} occurred={when} at={when}"


class _Interleaved:
    """The store, with something run just before the first append: another surface's write."""

    def __init__(self, storage, before=None) -> None:
        self._storage = storage
        self.before = before
        self.appends = 0
        self.changed = 0

    def __getattr__(self, name):
        return getattr(self._storage, name)

    def append_commitments(self, lines, *, scope, after):
        self.appends += 1
        if self.appends == 1 and self.before is not None:
            self.before()
        try:
            return self._storage.append_commitments(lines, scope=scope, after=after)
        except LedgerChanged:
            self.changed += 1
            raise


def _review(machine, monkeypatch, *answers: str, words=()):
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    queue = list(answers)

    def typed(prompt=""):
        if not queue:
            raise EOFError
        answer = queue.pop(0)
        if answer == "^C":
            raise KeyboardInterrupt
        return answer

    monkeypatch.setattr("builtins.input", typed)
    return _run(machine, "commitment", "review", *words)


# ── The PM's typed dues ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("words", "today", "due"),
    [
        ("friday", date(2026, 10, 25), date(2026, 10, 30)),  # Sun → the coming Friday
        ("friday", date(2026, 10, 30), date(2026, 11, 6)),  # Fri → next week's
        ("в пятницу", date(2026, 10, 30), date(2026, 11, 6)),
        ("friday", date(2026, 10, 29), date(2026, 10, 30)),  # Thu
        ("end of week", date(2026, 10, 30), date(2026, 11, 6)),
        ("end of week", date(2026, 10, 31), date(2026, 11, 6)),
        ("end of week", date(2026, 11, 1), date(2026, 11, 6)),
        ("до конца недели", date(2026, 10, 30), date(2026, 11, 6)),
        ("EOW", date(2026, 10, 28), date(2026, 10, 30)),
        ("this friday", date(2026, 10, 30), None),
        ("next sprint", date(2026, 10, 25), None),
        ("2026-10-20", date(2026, 10, 25), date(2026, 10, 20)),
        ("20.10.2026", date(2026, 10, 25), date(2026, 10, 20)),
        ("20 Oct 2026", date(2026, 10, 25), date(2026, 10, 20)),
        ("20 октября 2026", date(2026, 10, 25), date(2026, 10, 20)),
    ],
)
def test_the_pms_words_resolve_against_today(words, today, due):
    """Matrix "Friday on a Friday", "End of week from Friday on", "This Friday on a Friday", past dates."""
    assert pm_due(words, today) == due


# ── List ─────────────────────────────────────────────────────────────────────


def test_open_commitments_are_listed_soonest_due_first(machine):
    """Matrix "List open"; acceptance 1."""
    ids = _approved(machine, LISTED)
    machine.clock = SUNDAY
    code, out, _ = _run(machine, "commitment", "list")
    assert code == EXIT_OK
    headings = [line for line in out.splitlines() if line.startswith("Commitment")]
    assert headings == [
        "Commitment, open, due Thu 22 Oct, past due",
        "Commitment, open, due Fri 30 Oct",
        'Commitment, open, no due date worked out from "next sprint"',
        "Commitment, open, no due date said",
    ]
    assert out.rstrip().splitlines()[-1] == "next: pm-ai commitment review --scope personal"
    code, found = _json(machine, "commitment", "list")
    assert [card["commitment_id"] for card in found["commitments"]] == ids
    assert found["next"]["review"] == ["commitment", "review", "--scope", "personal"]
    assert found["commitments"][0]["past_due"] is True
    assert found["counts"] == {"by_state": {"pending": 4}, "by_source": {"none": 4, "pm": 0, "telemetry": 0}}


def test_closed_commitments_are_listed_newest_change_first(machine):
    """Matrix "List closed"."""
    ids = _approved(machine)
    _run(machine, "commitment", "done", ids[0])
    machine.clock = NOW + timedelta(days=1)
    _run(machine, "commitment", "done", ids[1])
    code, found = _json(machine, "commitment", "list", "--state", "done")
    assert [card["commitment_id"] for card in found["commitments"]] == [ids[1], ids[0]]


def test_an_empty_listing_is_one_sentence(machine):
    """Matrix "Empty"."""
    _approved(machine)
    code, out, _ = _run(machine, "commitment", "list", "--state", "broken")
    assert code == EXIT_OK
    assert out.splitlines()[0] == "No commitment matches --state broken in personal, project:alpha."


def test_bad_lines_are_counted_per_scope(machine):
    """Matrix "Bad lines"."""
    ids = _approved(machine)
    _run(machine, "commitment", "done", ids[0])
    at = NOW + timedelta(minutes=5)
    refused = render_line(Marked(ids[0], Mark.ALTERED, occurred=at, via="terminal", at=at))
    _append(machine, "- entry=cml_0123456789ab kind=nonsense")
    _append(machine, refused.text)
    code, out, _ = _run(machine, "commitment", "list")
    assert "commitments_log.md in personal: 1 line could not be read, 1 mark was not applied" in out
    code, found = _json(machine, "commitment", "list")
    personal = next(each for each in found["ledgers"] if each["scope"] == "personal")
    assert personal["malformed"] == 1 and personal["refused"] == 1


def test_an_unreadable_project_is_named_and_personal_still_listed(machine):
    """Matrix "Unreadable project"; and "List one unreadable scope"."""
    _approved(machine)
    path = _ledger_path(machine, ALPHA)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()  # a directory where the file belongs: it cannot be read
    code, found = _json(machine, "commitment", "list")
    assert code == EXIT_OK and len(found["commitments"]) == 3
    assert [each["scope"] for each in found["unreadable"]] == ["project:alpha"]
    code, found = _json(machine, "commitment", "list", "--scope", "project:alpha")
    assert code == EXIT_REFUSAL and found["code"] == "ledger_unreadable"


def test_a_line_with_no_start_sorts_after_one_with_a_start(machine):
    """Matrix "No start on the line"."""
    ids = _approved(machine)
    held = _folded_one(machine, ids[0])
    from dataclasses import replace

    other = replace(held, commitment_id="prp_no_start", proposal_id="prp_no_start", title=None, start=None)
    _append(machine, render_line(Pending(other, via="terminal", written="approval", at=NOW)).text)
    code, found = _json(machine, "commitment", "list")
    order = [card["commitment_id"] for card in found["commitments"]]
    assert order.index(ids[0]) < order.index("prp_no_start")


def test_the_listing_reads_files_only_at_a_terminal(machine, monkeypatch, capsys):
    """`commitment list` composes nothing: no daemon, no keychain."""
    _approved(machine)

    def forbidden(*_a, **_k):
        raise AssertionError("the listing composed a daemon or asked the keychain")

    monkeypatch.setattr(entry, "MacOSKeychainAdapter", forbidden)
    monkeypatch.setattr(entry, "_compose", forbidden)
    assert entry.main(["commitment", "list", "--json"]) == EXIT_OK
    assert len(json.loads(capsys.readouterr().out)["commitments"]) == 3


# ── Show and finding ─────────────────────────────────────────────────────────


def test_show_prints_the_full_card(machine):
    """Matrix "Show" and "Replacement id"."""
    ids = _approved(machine)
    code, out, _ = _run(machine, "commitment", "show", ids[0])
    assert code == EXIT_OK and "--if-version cml_" in out and "--scope personal" in out
    machine.run("proposal", "edit", ids[0], "--what", "send the TTL numbers")
    code, found = _json(machine, "commitment", "show", f"{ids[0]}_e1")
    assert code == EXIT_OK and found["commitment"]["commitment_id"] == ids[0]
    code, found = _json(machine, "commitment", "show", f"{ids[0]}_e9")
    assert code == EXIT_REFUSAL and found["code"] == "not_found"


def test_a_waiting_proposal_is_not_found_naming_proposal_show(machine):
    """Matrix "Waiting proposal"."""
    _, ids = _summarised(machine)
    code, found = _json(machine, "commitment", "done", ids[0])
    assert code == EXIT_REFUSAL and found["code"] == "not_found"
    assert found["next"] == ["proposal", "show", ids[0], "--scope", "personal"]


def test_an_approval_not_recorded_names_its_repair(machine):
    """Matrix "Approved, not recorded"."""
    _, ids = _summarised(machine)
    _approve_in_store(machine, ids[0])
    code, found = _json(machine, "commitment", "done", ids[0])
    assert code == EXIT_REFUSAL and found["code"] == "not_found"
    assert "not recorded in the ledger yet" in found["reason"]
    assert found["next"] == ["proposal", "approve", ids[0], "--scope", "personal"]


def test_a_malformed_line_naming_the_root_is_refused(machine):
    """Matrix "Malformed"."""
    ids = _approved(machine)
    _append(machine, f"- entry=cml_0123456789ab commitment={ids[0]} kind=marked state=finished")
    code, found = _json(machine, "commitment", "done", ids[0])
    assert code == EXIT_REFUSAL and found["code"] == "malformed"
    assert len(_marked(machine)) == 0


def test_not_found_with_an_unreadable_ledger_is_ledger_unreadable(machine):
    """Matrix "Not found, ledger unreadable"."""
    _approved(machine)
    path = _ledger_path(machine, ALPHA)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()
    code, found = _json(machine, "commitment", "done", "prp_nowhere")
    assert code == EXIT_REFUSAL and found["code"] == "ledger_unreadable"
    assert "project:alpha" in found["reason"]


def test_the_same_id_in_two_ledgers_is_ambiguous(machine):
    """Matrix "Same id in two ledgers"."""
    ids = _approved(machine)
    first = _text(machine).splitlines()[0]
    path = _ledger_path(machine, ALPHA)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(first + "\n")
    code, found = _json(machine, "commitment", "show", ids[0])
    assert code == EXIT_OK and found["commitment"]["also_in"] == ["project:alpha"]
    code, found = _json(machine, "commitment", "done", ids[0])
    assert code == EXIT_REFUSAL and found["code"] == "ambiguous"


@pytest.mark.parametrize(("scope", "code_word"), [("people:example", "scope_refused"), ("project:gone", "not_enrolled")])
def test_scopes_are_refused_as_13a_refuses_them(machine, scope, code_word):
    """Matrix "People scope" and "Not enrolled"."""
    ids = _approved(machine)
    code, found = _json(machine, "commitment", "done", ids[0], "--scope", scope)
    assert code == EXIT_REFUSAL and found["code"] == code_word
    if code_word == "not_enrolled":
        assert "project:alpha" in found["reason"]


# ── Marks ────────────────────────────────────────────────────────────────────


def test_done_writes_one_marked_line(machine):
    """Matrix "Done" and "No handle"."""
    ids = _approved(machine)
    code, out, _ = _run(machine, "commitment", "done", ids[0])
    assert code == EXIT_OK and f"{ids[0]} marked done." in out
    assert "undo until" in out and out.index("undo until") < out.index("next:")
    (line,) = _marked(machine)
    assert line["state"] == "fulfilled" and line["by"] == "pm" and line["via"] == "terminal"
    assert "handle" not in line


def test_done_records_the_pms_handle_when_there_is_one(machine):
    ids = _approved(machine)
    desk = _desk(machine, handle=lambda: ("graph:00000000-0000-0000-0000-000000000001", True))
    code, found = _json(machine, "commitment", "done", ids[0], desk=desk)
    assert found["handle_recorded"] is True and found["wrote"] == ["marked"]
    assert _marked(machine)[0]["handle"] == "graph:00000000-0000-0000-0000-000000000001"


def test_a_handle_that_cannot_be_read_is_said_and_the_mark_written(machine):
    """Matrix "No handle readable"."""
    ids = _approved(machine)
    desk = _desk(machine, handle=lambda: (None, False))
    code, found = _json(machine, "commitment", "done", ids[0], desk=desk)
    assert code == EXIT_OK and found["handle_recorded"] is False
    code, out, _ = _run(machine, "commitment", "broken", ids[1], desk=desk)
    assert "recorded without your handle: the connector rows could not be read" in out
    assert len(_marked(machine)) == 2


def test_pm_handle_takes_the_lowest_and_says_when_rows_are_unusable(monkeypatch):
    from pm_ai.app import wiring

    class Reading:
        def __init__(self, identity, unusable):
            self.identity, self.unusable = identity, unusable

    monkeypatch.setattr(wiring, "read_pm_identity", lambda storage: Reading({"graph:b", "graph:a"}, ()))
    assert pm_handle(object()) == ("graph:a", True)
    monkeypatch.setattr(wiring, "read_pm_identity", lambda storage: Reading({"graph:a"}, ("x",)))
    assert pm_handle(object()) == (None, False)


def test_done_twice_is_already_and_a_note_is_not_recorded(machine):
    """Matrix "Done twice" and "Done twice with a note"; "Already beats the version"."""
    ids = _approved(machine)
    code, shown = _json(machine, "commitment", "show", ids[0])
    old = shown["commitment"]["version"]  # the version before the done mark
    code, first = _json(machine, "commitment", "done", ids[0])
    assert first["commitment"]["version"] != old
    code, found = _json(machine, "commitment", "done", ids[0], "--note", "x", "--if-version", old)
    assert code == EXIT_OK and found["outcome"] == "already" and found["note_recorded"] is False
    assert found["handle_recorded"] is None and found["wrote"] == []
    code, out, _ = _run(machine, "commitment", "done", ids[0], "--note", "x")
    assert "the note was not recorded" in out.lower()
    assert len(_marked(machine)) == 1 and old


@pytest.mark.parametrize("verb", ["altered", "withdrawn"])
def test_a_done_mark_refuses_altered_and_withdrawn(machine, verb):
    """Matrix "Altered after done", "Withdrawn after done"; acceptance 3."""
    ids = _approved(machine)
    _run(machine, "commitment", "done", ids[0])
    before = _text(machine)
    code, found = _json(machine, "commitment", verb, ids[0])
    assert code == EXIT_REFUSAL and found["code"] == "mark_refused"
    assert "you marked it done on" in found["reason"]
    assert _text(machine) == before


def test_broken_after_done_and_done_after_withdrawn_are_written(machine):
    """Matrix "Broken after done", "Done after withdrawn"."""
    ids = _approved(machine)
    _run(machine, "commitment", "done", ids[0])
    code, found = _json(machine, "commitment", "broken", ids[0])
    assert code == EXIT_OK and found["commitment"]["state"] == "broken"
    _run(machine, "commitment", "withdrawn", ids[1])
    code, found = _json(machine, "commitment", "done", ids[1])
    assert code == EXIT_OK and found["commitment"]["state"] == "fulfilled"


def test_done_confirms_a_telemetry_verdict(machine):
    """Matrix "Confirm telemetry"."""
    ids = _approved(machine)
    _append(machine, _verdict(ids[0], "fulfilled", NOW + timedelta(minutes=1)))
    machine.clock = NOW + timedelta(minutes=2)
    code, out, _ = _run(machine, "commitment", "done", ids[0])
    assert code == EXIT_OK and "confirms telemetry's verdict" in out
    assert _marked(machine)[0]["state"] == "fulfilled"


def test_a_bad_version_is_a_usage_error(machine):
    """Matrix "Bad version"."""
    ids = _approved(machine)
    code, _, err = _run(machine, "commitment", "done", ids[0], "--if-version", "42")
    assert code == EXIT_USAGE and "cml_" in err


def test_a_stale_version_never_appends(machine):
    """Matrix "Stale version"; acceptance 5."""
    ids = _approved(machine)
    code, shown = _json(machine, "commitment", "show", ids[0])
    _run(machine, "commitment", "owner", ids[0], "Alex Morgan")
    size = _ledger_path(machine).stat().st_size
    code, found = _json(machine, "commitment", "done", ids[0], "--if-version", shown["commitment"]["version"])
    assert code == EXIT_REFUSAL and found["code"] == "changed"
    assert "its owner was set to Alex Morgan" in found["reason"]
    assert found["commitment"]["commitment_id"] == ids[0]
    assert _ledger_path(machine).stat().st_size == size


def test_prompt_lines_say_via_prompt(machine):
    """Matrix "Prompt via" (the daemon's desk)."""
    ids = _approved(machine)
    _run(machine, "commitment", "done", ids[0], desk=_desk(machine, via="prompt"))
    assert _marked(machine)[0]["via"] == "prompt"


# ── Altered ──────────────────────────────────────────────────────────────────


def test_altered_alone_writes_its_mark_with_the_note(machine):
    """Matrix "Altered alone"; "Altered again"."""
    ids = _approved(machine)
    code, found = _json(machine, "commitment", "altered", ids[0], "--note", "scope cut to the API only")
    assert code == EXIT_OK and found["commitment"]["state"] == "altered"
    assert _marked(machine)[0]["note"] == "scope cut to the API only"
    code, found = _json(machine, "commitment", "altered", ids[0], "--note", "b")
    assert code == EXIT_OK and found["outcome"] == "altered"
    assert [line["note"] for line in _marked(machine)] == ["scope cut to the API only", "b"]


def test_altered_with_due_and_owner_is_one_append(machine):
    """Matrix "Altered with due and owner"; "Altered reopens with a due"."""
    ids = _approved(machine)
    _run(machine, "commitment", "broken", ids[0])
    machine.clock = SUNDAY
    code, found = _json(machine, "commitment", "altered", ids[0], "--due", "friday", "--owner", "Alex Morgan")
    assert code == EXIT_OK, found
    assert found["wrote"] == ["marked", "owner_set", "due_set"]
    lines = _lines(machine)[-3:]
    assert [line["kind"] for line in lines] == ["marked", "owner_set", "due_set"]
    assert lines[2]["due"] == "2026-10-30" and lines[1]["owner"] == "Alex Morgan"
    assert len({line["occurred"] for line in lines}) == 1
    assert found["commitment"]["state"] == "altered"


def test_altered_with_an_unresolved_due_writes_nothing(machine):
    """Matrix "Altered, unresolved due"."""
    ids = _approved(machine)
    before = _text(machine)
    code, found = _json(machine, "commitment", "altered", ids[0], "--due", "next sprint", "--note", "x")
    assert code == EXIT_REFUSAL and found["code"] == "due_unresolved"
    assert _text(machine) == before


def test_altered_with_a_past_date_is_taken_as_typed(machine):
    """Matrix "Altered, past due date"."""
    ids = _approved(machine)
    machine.clock = SUNDAY
    code, out, _ = _run(machine, "commitment", "altered", ids[0], "--due", "20 Oct 2026")
    assert code == EXIT_OK and "(already past)" in out
    assert _lines(machine)[-1]["due"] == "2026-10-20"


def test_altered_with_wording_edits_the_proposal_after_the_mark(machine):
    """Matrix "Altered with wording" and "Altered wording, version"."""
    ids = _approved(machine)
    code, found = _json(machine, "commitment", "altered", ids[0], "--what", "send the numbers for the API only")
    assert code == EXIT_OK, found
    assert found["edited"] == {"proposal_id": f"{ids[0]}_e1"}
    assert machine.load(f"{ids[0]}_e1").state is ProposalState.APPROVED
    kinds = [line["kind"] for line in _lines(machine) if line["commitment"] == ids[0]]
    assert kinds == ["pending", "marked", "revised"]
    assert found["commitment"]["state"] == "altered"
    assert found["commitment"]["what"] == "send the numbers for the API only"
    code, done = _json(machine, "commitment", "done", ids[0], "--if-version", found["commitment"]["version"])
    assert code == EXIT_OK and done["outcome"] == "marked"


def test_altered_with_the_same_wording_starts_no_edit(machine):
    """Matrix "Altered, same wording"."""
    ids = _approved(machine)
    code, found = _json(machine, "commitment", "altered", ids[0], "--what", ITEMS[0][0])
    assert code == EXIT_OK and found["edited"] is None
    assert machine.load(ids[0]).state is ProposalState.APPROVED


def test_altered_wording_refused_keeps_the_mark(machine, monkeypatch):
    """Matrix "Altered wording, edit fails"."""
    from pm_ai.core.proposal_cards import ProposalRefused

    ids = _approved(machine)
    desk = _desk(machine)

    def refused(*_a, **_k):
        raise ProposalRefused("the edit was refused for the test.")

    monkeypatch.setattr(desk.proposals, "edit", refused)
    code, found = _json(machine, "commitment", "altered", ids[0], "--what", "new words", desk=desk)
    assert code == EXIT_REFUSAL and found["code"] == "edit_refused"
    assert found["wrote"] == ["marked"]
    assert found["next"] == ["proposal", "edit", ids[0], "--scope", "personal", "--what", "new words"]
    assert "the altered mark is recorded" in found["reason"]
    assert len(_marked(machine)) == 1


def test_altered_out_of_time_does_not_start_the_edit(machine):
    """Matrix "Altered, out of time"."""
    ids = _approved(machine)
    ticks = iter([0.0, 1.5])
    desk = _desk(machine, monotonic=lambda: next(ticks, 1.5))
    code, found = _json(machine, "commitment", "altered", ids[0], "--what", "new words", desk=desk)
    assert code == EXIT_REFUSAL and found["code"] == "edit_refused" and "out of time" in found["reason"]
    assert found["wrote"] == ["marked"] and found["next"][:3] == ["proposal", "edit", ids[0]]
    assert machine.load(ids[0]).state is ProposalState.APPROVED


def test_altered_wording_on_a_promise_is_not_editable(machine):
    """Matrix "Altered wording on a promise"."""
    ids = _approved(machine)
    held = _folded_one(machine, ids[0])
    from dataclasses import replace

    promise = replace(held, commitment_id="prp_promise", proposal_id="prp_promise", type="implicit_commitment",
                      owner=None, speaker="Sam Rivera")
    _append(machine, render_line(Pending(promise, via="terminal", written="approval", at=NOW)).text)
    before = _text(machine)
    code, found = _json(machine, "commitment", "altered", "prp_promise", "--what", "x")
    assert code == EXIT_REFUSAL and found["code"] == "what_not_editable"
    assert _text(machine) == before
    # Matrix "Owner of a promise".
    code, found = _json(machine, "commitment", "owner", "prp_promise", "Sam Rivera")
    assert code == EXIT_OK and _lines(machine)[-1]["owner"] == "Sam Rivera"
    code, out, _ = _run(machine, "commitment", "show", "prp_promise")
    assert "said by Sam Rivera, owner Sam Rivera:" in out


# ── Due and owner ────────────────────────────────────────────────────────────


def test_due_words_set_the_coming_friday(machine):
    """Matrix "Due words"; acceptance 4."""
    ids = _approved(machine)
    machine.clock = SUNDAY
    code, found = _json(machine, "commitment", "due", ids[2], "friday")
    assert code == EXIT_OK and found["outcome"] == "due_set"
    assert _lines(machine)[-1]["due"] == "2026-10-30"
    assert found["commitment"]["due"]["by_pm"] is True and found["commitment"]["due"]["estimated"] is False
    assert found["commitment"]["due"]["said"] == "next sprint"
    code, out, _ = _run(machine, "commitment", "show", ids[2])
    assert "due Fri 30 Oct, set by you" in out


@pytest.mark.parametrize("words", ["2026-10-20", "20.10.2026", "20 Oct 2026"])
def test_a_past_date_with_its_year_is_written(machine, words):
    """Matrix "Due ISO in the past" and "Due past, other forms"."""
    ids = _approved(machine)
    machine.clock = SUNDAY
    code, out, _ = _run(machine, "commitment", "due", ids[0], words)
    assert code == EXIT_OK and "(already past)" in out
    assert _lines(machine)[-1]["due"] == "2026-10-20"


def test_due_unresolved_names_the_forms(machine):
    """Matrix "Due unresolved" and "This Friday on a Friday"."""
    ids = _approved(machine)
    before = _text(machine)
    code, found = _json(machine, "commitment", "due", ids[0], "next sprint")
    assert code == EXIT_REFUSAL and found["code"] == "due_unresolved" and "2026-11-06" in found["reason"]
    machine.clock = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
    code, found = _json(machine, "commitment", "due", ids[0], "this friday")
    assert code == EXIT_REFUSAL and found["code"] == "due_unresolved"
    assert _text(machine) == before


def test_friday_on_a_friday_is_next_weeks(machine):
    """Matrix "Friday on a Friday"."""
    ids = _approved(machine)
    machine.clock = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
    code, out, _ = _run(machine, "commitment", "due", ids[0], "friday")
    assert code == EXIT_OK and "due Fri 6 Nov" in out
    assert _lines(machine)[-1]["due"] == "2026-11-06"


@pytest.mark.parametrize("words", [("  ",), ("friday", "--clear"), ()])
def test_due_usage_errors(machine, words):
    """Matrix "Due blank" and "Due both"."""
    ids = _approved(machine)
    code, _, _ = _run(machine, "commitment", "due", ids[0], *words)
    assert code == EXIT_USAGE


def test_due_clear_and_unchanged(machine):
    """Matrix "Due cleared", "Due cleared twice", "Due the same, set by the PM", "Due the same, estimated"."""
    ids = _approved(machine)
    code, found = _json(machine, "commitment", "due", ids[0], "--clear")
    assert found["outcome"] == "due_set" and _lines(machine)[-1]["due"] == "none"
    code, found = _json(machine, "commitment", "due", ids[0], "--clear")
    assert found["outcome"] == "unchanged"
    # The same date as worked out from what was said is written, as the PM's.
    code, found = _json(machine, "commitment", "due", ids[1], "2026-10-23")
    assert found["outcome"] == "due_set" and found["commitment"]["due"]["by_pm"] is True
    code, found = _json(machine, "commitment", "due", ids[1], "2026-10-23")
    assert found["outcome"] == "unchanged"


def test_owner_rules(machine):
    """Matrix "Owner over its limit", "Owner blank", "Owner is a handle", "Owner the same"."""
    ids = _approved(machine)
    assert _run(machine, "commitment", "owner", ids[0], "A" * 81)[0] == EXIT_USAGE
    assert _run(machine, "commitment", "owner", ids[0], "  \n ")[0] == EXIT_USAGE
    code, found = _json(machine, "commitment", "owner", ids[0], "alex@example.com")
    assert code == EXIT_REFUSAL and found["code"] == "owner_is_handle"
    code, found = _json(machine, "commitment", "owner", ids[0], "graph:1234")
    assert code == EXIT_REFUSAL and found["code"] == "owner_is_handle"
    name = "Zoë Example"
    _run(machine, "commitment", "owner", ids[0], unicodedata.normalize("NFC", name))
    code, found = _json(machine, "commitment", "owner", ids[0], unicodedata.normalize("NFD", name))
    assert found["outcome"] == "unchanged"


def test_due_and_owner_on_a_closed_commitment_are_refused(machine):
    """Matrix "Due on a closed commitment" and "Owner on a closed commitment"."""
    ids = _approved(machine)
    _run(machine, "commitment", "done", ids[0])
    before = _text(machine)
    code, found = _json(machine, "commitment", "due", ids[0], "friday")
    assert code == EXIT_REFUSAL and found["code"] == "commitment_closed"
    assert "pm-ai commitment undo" in found["reason"]
    _run(machine, "commitment", "withdrawn", ids[1])
    code, found = _json(machine, "commitment", "owner", ids[1], "Alex Morgan")
    assert code == EXIT_REFUSAL and found["code"] == "commitment_closed"
    # Story 15d: the reason is prose; the card's `reopen` carries the command.
    assert "the reopen command reopens it with an owner" in found["reason"]
    reopen = found["commitment"]["actions"]["owner"]["reopen"]
    assert reopen["command"][:2] == ["commitment", "altered"] and reopen["command"][-2:] == ["--owner", "<name>"]
    assert reopen["fill"] == [{"index": len(reopen["command"]) - 1, "name": "name", "required": True}]


def test_the_pms_owner_survives_a_proposal_edit(machine):
    """Matrix "Owner kept after an edit" and "Owner changed by an edit"."""
    ids = _approved(machine)
    # The third item has no owner in its proposal.
    _run(machine, "commitment", "owner", ids[2], "Alex Morgan")
    machine.clock = NOW + timedelta(minutes=5)
    machine.run("proposal", "edit", ids[2], "--what", "draft the release notes today")
    code, found = _json(machine, "commitment", "show", ids[2])
    assert found["commitment"]["owner"] == "Alex Morgan"
    machine.clock = NOW + timedelta(minutes=10)
    machine.run("proposal", "edit", f"{ids[2]}_e1", "--owner", "Sam Rivera")
    code, found = _json(machine, "commitment", "show", ids[2])
    assert found["commitment"]["owner"] == "Sam Rivera"


# ── Undo ─────────────────────────────────────────────────────────────────────


def test_undo_inside_the_window(machine):
    """Matrix "Undo inside the window", "Undo twice", "Undo offered"."""
    ids = _approved(machine)
    code, done = _json(machine, "commitment", "done", ids[0])
    assert done["undo"]["command"][:3] == ["commitment", "undo", ids[0]]
    assert "--if-version" in done["undo"]["command"]
    # 4 hours after the mark, whose `occurred` follows the approval by 1 µs.
    assert done["undo"]["until"] == (NOW + timedelta(hours=4, microseconds=1)).isoformat()
    machine.clock = NOW + timedelta(hours=3, minutes=59)
    code, found = _json(machine, "commitment", "undo", ids[0])
    assert code == EXIT_OK and found["outcome"] == "undone" and found["commitment"]["state"] == "pending"
    assert _lines(machine)[-1]["kind"] == "undone"
    code, found = _json(machine, "commitment", "undo", ids[0])
    assert code == EXIT_OK and found["outcome"] == "already"


def test_undo_back_to_altered(machine):
    ids = _approved(machine)
    _run(machine, "commitment", "altered", ids[0])
    _run(machine, "commitment", "done", ids[0])
    code, found = _json(machine, "commitment", "undo", ids[0])
    assert found["commitment"]["state"] == "altered"


def test_undo_refusals(machine):
    """Matrix "Undo after 4 h", "Undo after another line", "Undo of a telemetry mark", "Undo of broken"."""
    ids = _approved(machine)
    _run(machine, "commitment", "done", ids[0])
    machine.clock = NOW + timedelta(hours=4, minutes=1)
    code, found = _json(machine, "commitment", "undo", ids[0])
    assert code == EXIT_REFUSAL and found["code"] == "undo_expired"
    machine.clock = NOW
    _run(machine, "commitment", "done", ids[1])
    at = NOW + timedelta(minutes=1)
    from pm_ai.core.commitment_log import DueSet

    _append(machine, render_line(DueSet(ids[1], date(2026, 11, 2), occurred=at, via="prompt", at=at)).text)
    machine.clock = NOW + timedelta(minutes=2)
    code, found = _json(machine, "commitment", "undo", ids[1])
    assert code == EXIT_REFUSAL and found["code"] == "undo_superseded" and "due was set" in found["reason"]
    _append(machine, _verdict(ids[2], "fulfilled", at))
    code, found = _json(machine, "commitment", "undo", ids[2])
    assert code == EXIT_REFUSAL and found["code"] == "nothing_to_undo"
    assert "telemetry recorded it done" in found["reason"]
    _, more = _summarised(machine, (("x item", "Alex Morgan", None),), meeting_id="mtg_d")
    machine.run("proposal", "approve", more[0])
    _run(machine, "commitment", "broken", more[0])
    code, found = _json(machine, "commitment", "undo", more[0])
    assert code == EXIT_REFUSAL and found["code"] == "nothing_to_undo"


def test_undo_by_version(machine):
    """Matrix "Undo by version"."""
    ids = _approved(machine)
    code, shown = _json(machine, "commitment", "show", ids[0])
    code, done = _json(machine, "commitment", "done", ids[0])
    code, found = _json(machine, "commitment", "undo", ids[0], "--if-version", shown["commitment"]["version"])
    assert code == EXIT_REFUSAL and found["code"] == "changed"
    code, found = _json(machine, "commitment", "undo", ids[0], "--if-version", done["commitment"]["version"])
    assert code == EXIT_OK and found["outcome"] == "undone"


# ── Concurrency ──────────────────────────────────────────────────────────────


def test_two_surfaces_marking_done_record_one_mark(machine):
    """Matrix "Two surfaces"; acceptance 2."""
    ids = _approved(machine)
    first = _desk(machine, via="prompt")
    interleaved = _Interleaved(machine.storage, before=lambda: first.mark("done", ids[0]))
    second = _desk(machine, storage=interleaved)
    answer = second.mark("done", ids[0])
    assert answer.outcome == "already" and interleaved.changed == 1
    assert len(_marked(machine)) == 1


def test_done_then_withdrawn_from_another_surface_is_refused(machine):
    """Matrix "Done, then withdrawn"."""
    ids = _approved(machine)
    first = _desk(machine)
    interleaved = _Interleaved(machine.storage, before=lambda: first.mark("done", ids[0]))
    code, found = _json(machine, "commitment", "withdrawn", ids[0], desk=_desk(machine, storage=interleaved))
    assert code == EXIT_REFUSAL and found["code"] == "mark_refused"


def test_withdrawn_then_done_from_another_surface_is_written(machine):
    """Matrix "Withdrawn, then done"."""
    ids = _approved(machine)
    first = _desk(machine)
    interleaved = _Interleaved(machine.storage, before=lambda: first.mark("withdrawn", ids[0]))
    answer = _desk(machine, storage=interleaved).mark("done", ids[0])
    assert answer.outcome == "marked" and answer.card.state.value == "fulfilled"
    # With `--if-version`, the same race is refused as `changed`.
    code, shown = _json(machine, "commitment", "show", ids[1])
    racing = _Interleaved(machine.storage, before=lambda: first.mark("withdrawn", ids[1]))
    code, found = _json(
        machine, "commitment", "done", ids[1], "--if-version", shown["commitment"]["version"],
        desk=_desk(machine, storage=racing),
    )
    assert code == EXIT_REFUSAL and found["code"] == "changed"


def test_another_commitments_line_does_not_refuse_the_version(machine):
    """Matrix "Other commitment moved"."""
    ids = _approved(machine)
    code, shown = _json(machine, "commitment", "show", ids[0])
    other = _desk(machine)
    interleaved = _Interleaved(machine.storage, before=lambda: other.mark("broken", ids[1]))
    answer = _desk(machine, storage=interleaved).mark("done", ids[0], if_version=shown["commitment"]["version"])
    assert answer.outcome == "marked" and interleaved.changed == 1


def test_a_retry_sorts_after_the_line_it_lost_to(machine):
    """Matrix "Retry sorts last"."""
    ids = _approved(machine)
    later = NOW + timedelta(minutes=30)

    def competing_done() -> None:
        _append(machine, render_line(Marked(ids[0], Mark.FULFILLED, occurred=later, via="prompt", at=later)).text)

    interleaved = _Interleaved(machine.storage, before=competing_done)
    answer = _desk(machine, storage=interleaved).mark("broken", ids[0])
    assert answer.card.state.value == "broken"
    broken = _marked(machine)[-1]
    assert broken["occurred"] == (later + timedelta(microseconds=1)).isoformat()


def test_an_append_that_raised_after_writing_is_reported_as_made(machine):
    """Matrix "Append raised after writing"."""
    ids = _approved(machine)

    class Raises(_Interleaved):
        def append_commitments(self, lines, *, scope, after):
            self._storage.append_commitments(lines, scope=scope, after=after)
            raise OSError(5, "I/O error after the bytes were written")

    answer = _desk(machine, storage=Raises(machine.storage)).mark("done", ids[0])
    assert answer.outcome == "marked" and len(_marked(machine)) == 1


def test_a_busy_claim_answers_busy_at_once(machine):
    """Matrix "Busy"."""
    ids = _approved(machine)
    with _claim_held(machine):
        started = time.monotonic()
        code, found = _json(machine, "commitment", "done", ids[0])
        took = time.monotonic() - started
    assert code == EXIT_REFUSAL and found["code"] == "busy" and took < COMMITMENT_WRITE_BOUND_SECONDS
    assert not _marked(machine)


def test_a_ledger_that_keeps_changing_is_busy(machine):
    """Matrix "Ledger kept changing"."""
    ids = _approved(machine)

    class Always(_Interleaved):
        def append_commitments(self, lines, *, scope, after):
            raise LedgerChanged("grew")

    code, found = _json(machine, "commitment", "done", ids[0], desk=_desk(machine, storage=Always(machine.storage)))
    assert code == EXIT_REFUSAL and found["code"] == "busy"


def test_a_project_git_does_not_exclude_is_refused(machine):
    """Matrix "Project git refuses"."""
    _, ids = _summarised(machine, scope=ALPHA)
    machine.run("proposal", "approve", ids[0])
    (machine.projects["alpha"] / ".gitignore").write_text("node_modules/\n")
    machine.daemon = machine._build()  # a new store asks the question afresh
    before = _text(machine, ALPHA)
    code, found = _json(machine, "commitment", "done", ids[0])
    assert code == EXIT_REFUSAL and found["code"] == "git_not_excluded"
    assert _text(machine, ALPHA) == before


# ── Review ───────────────────────────────────────────────────────────────────


def test_review_walks_compact_cards(machine, monkeypatch):
    """Matrix "Review"."""
    ids = _approved(machine)
    machine.clock = SUNDAY
    code, out, _ = _review(machine, monkeypatch, "d", "u", "friday", "s")
    assert code == EXIT_OK, out
    assert "1 marked done, 1 due set, 1 skipped." in out
    assert "--if-version" not in out and "pm-ai commitment done" not in out
    assert "next: pm-ai commitment list --scope personal" in out
    assert "marked done; [z] undoes it until" in out
    assert len(_marked(machine)) == 1


def test_review_asks_again_for_an_unresolved_due_and_a_long_note(machine, monkeypatch):
    """Matrix "Review unresolved due", "Review past date", "Review long note", "Review Enter at due"."""
    ids = _approved(machine)
    machine.clock = SUNDAY
    code, out, _ = _review(
        machine, monkeypatch, "u", "next sprint", "2026-10-20", "b", "x" * 300, "short", "u", "", "q"
    )
    assert code == EXIT_OK
    assert "names no one date" in out and "(already past)" in out
    assert "at most 200 characters" in out
    assert "1 marked broken, 1 due set, 1 skipped." in out


def test_review_ctrl_c_in_a_question_writes_nothing(machine, monkeypatch):
    """Matrix "Review Ctrl-C in a question"."""
    _approved(machine)
    before = _text(machine)
    code, out, _ = _review(machine, monkeypatch, "a", "", "", "", "^C")
    assert code == EXIT_OK and "nothing reviewed." in out
    assert _text(machine) == before


def test_review_undo_of_the_last_done(machine, monkeypatch):
    """Matrix "Undo from review"."""
    ids = _approved(machine)
    code, out, _ = _review(machine, monkeypatch, "d", "z", "s", "q")
    assert code == EXIT_OK
    # `d` on card 1, `z` on card 2: the walk goes on with card 2, which is skipped.
    assert "1 undone, 1 skipped." in out and "marked done," not in out.splitlines()[-2]
    code, found = _json(machine, "commitment", "show", ids[0])
    assert found["commitment"]["state"] == "pending"
    assert [line["kind"] for line in _lines(machine)][-2:] == ["marked", "undone"]


def test_review_with_nothing_open(machine, monkeypatch):
    """Matrix "Review nothing open"."""
    code, out, _ = _review(machine, monkeypatch)
    assert code == EXIT_OK and "No commitment matches --state open" in out


def test_review_skips_one_closed_meanwhile(machine, monkeypatch):
    """Matrix "Review closed meanwhile"."""
    ids = _approved(machine)
    real = CommitmentDesk.current
    calls = []

    def current(self, commitment_id, scope):
        if not calls:
            _desk(machine, via="prompt").mark("done", ids[1])
        calls.append(commitment_id)
        return real(self, commitment_id, scope)

    monkeypatch.setattr(CommitmentDesk, "current", current)
    code, out, _ = _review(machine, monkeypatch, "s", "s")
    assert "marked done on" in out and "meanwhile; skipped" in out


def test_review_conflict_shows_the_card_again(machine, monkeypatch):
    """Matrix "Review conflict"."""
    ids = _approved(machine)
    real = CommitmentDesk.mark
    raced = []

    def mark(self, verb, commitment_id, **options):
        if not raced:
            raced.append(True)
            _desk(machine, via="prompt").set_owner(commitment_id, name="Alex Morgan")
        return real(self, verb, commitment_id, **options)

    monkeypatch.setattr(CommitmentDesk, "mark", mark)
    code, out, _ = _review(machine, monkeypatch, "d", "d", "q")
    assert "its owner was set to Alex Morgan" in out and "1 marked done" in out


def test_review_without_a_terminal_is_refused(machine):
    """Matrix "Review without a terminal"."""
    code, out, err = _run(machine, "commitment", "review")
    assert code == EXIT_REFUSAL and "commitment list --json" in out + err


# ── Where it runs ────────────────────────────────────────────────────────────


def test_the_bounds_are_declared(machine):
    """The slice test of the bounds' values, and each help names its seconds."""
    assert COMMITMENT_WRITE_BOUND_SECONDS == 2.0
    for verb in ("list", "show"):
        assert DAEMON_BOUNDS[("commitment", verb)](None) == CONFIG_SHOW_BOUND_SECONDS
        assert "within 1 second" in TABLE["commitment"].leaves[verb].summary
    for verb in ("done", "altered", "broken", "withdrawn", "due", "owner", "undo"):
        assert DAEMON_BOUNDS[("commitment", verb)](None) == COMMITMENT_WRITE_BOUND_SECONDS
        assert "within 2 seconds" in TABLE["commitment"].leaves[verb].summary
    assert ("commitment", "review") in TERMINAL_BOUND


def test_prompt_lines_run_inline_through_the_daemon(machine):
    """Matrix "Prompt bounds" and "Prompt via"."""
    ids = _approved(machine)
    rig = _Rig(machine)
    try:
        started = time.monotonic()
        listed = asyncio.run(rig.path.prompt("commitment list"))
        assert time.monotonic() - started < 1.0 + 0.5
        assert listed.body["exit"] == EXIT_OK and ids[0] in listed.body["output"]
        started = time.monotonic()
        done = asyncio.run(rig.path.prompt(f"commitment done {ids[0]}"))
        assert time.monotonic() - started < COMMITMENT_WRITE_BOUND_SECONDS
        assert done.body["exit"] == EXIT_OK, done.body
    finally:
        rig.close()
    assert _marked(machine)[0]["via"] == "prompt"


def test_review_at_the_prompt_records_via_prompt(machine, monkeypatch):
    """Matrix "Prompt review": the prompt's own process, `via=prompt`."""
    ids = _approved(machine)
    seen = []
    real = entry._commitment_desk

    def recorded(daemon):
        desk = real(daemon)
        seen.append(desk.via)
        return desk

    monkeypatch.setattr(entry, "_commitment_desk", recorded)
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    answers = iter(["d", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert entry.typed_at_the_prompt(["commitment", "review"]) == EXIT_OK
    assert seen == ["prompt"] and ids


def test_a_mark_on_a_large_project_ledger_answers_within_its_bound(machine):
    """Matrix "Timed mark": 5,000 lines, the git check, one `LedgerChanged`, under 2 s."""
    _, ids = _summarised(machine, scope=ALPHA)
    machine.run("proposal", "approve", ids[0])
    _fill(machine, ALPHA, ids[1], 5000)
    other = _desk(machine)
    filler = "prp_filler_00007"
    interleaved = _Interleaved(machine.storage, before=lambda: other.mark("broken", filler))
    started = time.monotonic()
    answer = _desk(machine, storage=interleaved).mark("done", ids[0])
    took = time.monotonic() - started
    assert answer.outcome == "marked" and interleaved.changed == 1
    assert took < COMMITMENT_WRITE_BOUND_SECONDS, f"{took:.2f} s"
    machine.run("proposal", "approve", ids[2])
    started = time.monotonic()
    altered = _desk(machine).altered(ids[2], what="send the numbers for the API only")
    took = time.monotonic() - started
    assert altered.edited == f"{ids[2]}_e1" and took < COMMITMENT_WRITE_BOUND_SECONDS, f"{took:.2f} s"


def _fill(machine: Machine, scope: DataScope, template_id: str, count: int) -> None:
    from dataclasses import replace

    template = _folded_one(machine, template_id)
    text = "".join(
        render_line(
            Pending(
                replace(template, commitment_id=f"prp_filler_{n:05d}", proposal_id=f"prp_filler_{n:05d}"),
                via="terminal", written="approval", at=NOW,
            )
        ).text
        + "\n"
        for n in range(count)
    )
    path = _ledger_path(machine, scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as ledger:
        ledger.write(text)


def test_listing_two_large_ledgers_answers_within_a_second(machine):
    """Matrix "Timed listing"."""
    _, ids = _summarised(machine)
    _fill(machine, PERSONAL, ids[0], 5000)
    _fill(machine, ALPHA, ids[0], 5000)
    desk = CommitmentDesk(now=lambda: machine.clock, paths=machine.storage.paths, zone=UTC)
    started = time.monotonic()
    listing = desk.listing()
    took = time.monotonic() - started
    assert listing.more == 10000 - 20 and took < 1.0, f"{took:.2f} s"


# ── Completion ───────────────────────────────────────────────────────────────


def test_completion_offers_what_the_verb_can_act_on(machine):
    """Matrix "Completion"."""
    ids = _approved(machine)
    _run(machine, "commitment", "done", ids[0])
    offered = commitment_choices("done", now=machine.clock, paths=machine.storage.paths)
    assert [each[0] for each in offered] == ids[1:]
    assert offered[0][1] == "Alex Morgan: book the review room (\"до конца недели\")"
    assert offered[0][2] == "Example Weekly Sync-up"
    assert [each[0] for each in commitment_choices("undo", now=machine.clock, paths=machine.storage.paths)] == [ids[0]]
    assert ids[0] not in [each[0] for each in commitment_choices("due", now=machine.clock, paths=machine.storage.paths)]
    assert len(commitment_choices("show", now=machine.clock, paths=machine.storage.paths)) == 3
    completion = Completion(Readers(commitments=lambda verb: commitment_choices(verb, now=machine.clock, paths=machine.storage.paths)))
    found = completion.complete("commitment done book")
    assert [each.display for each in found] == [ids[1]]


def test_completion_offers_the_owners_in_the_ledger_the_speaker_first(machine):
    """Matrix "Completion owner"."""
    ids = _approved(machine)
    held = _folded_one(machine, ids[0])
    from dataclasses import replace

    promise = replace(held, commitment_id="prp_promise", proposal_id="prp_promise", type="implicit_commitment",
                      owner=None, speaker="Sam Rivera")
    _append(machine, render_line(Pending(promise, via="terminal", written="approval", at=NOW)).text)
    names = commitment_owners("prp_promise", "personal", now=machine.clock, paths=machine.storage.paths)
    assert names[0] == "Sam Rivera" and "Alex Morgan" in names
    completion = Completion(Readers(commitment_owners=lambda cid, scope: ("Sam Rivera", "Alex Morgan")))
    found = completion.complete("commitment owner prp_promise --scope personal ")
    assert [each.display for each in found][:1] == ["Sam Rivera"]


def test_completion_offers_meeting_ids_and_states(machine):
    """Matrix "Completion meeting"."""
    completion = Completion(Readers(meetings=lambda scope: (("mtg_c", NOW, "Example Weekly Sync-up"),)))
    found = completion.complete("commitment list --scope personal --meeting ")
    assert [each.display for each in found] == ["mtg_c"]
    states = [each.display for each in Completion().complete("commitment list --state ")]
    assert states == ["open", "done", "broken", "withdrawn", "all"]


# ── The reject refusal and the pages ─────────────────────────────────────────


def test_reject_after_done_offers_no_withdrawn_or_altered(machine):
    """Matrix "Reject after done"."""
    ids = _approved(machine)
    _run(machine, "commitment", "done", ids[0])
    code, found = machine.json("proposal", "reject", ids[0])
    assert code == EXIT_REFUSAL and set(found["commitment"]["commands"]) == {"edit"}
    assert "commitment withdrawn" not in found["reason"]


def test_reject_when_the_ledger_write_failed_names_the_repair(machine):
    """Matrix "Reject, ledger write failed"."""
    _, ids = _summarised(machine)
    _approve_in_store(machine, ids[0])
    code, found = machine.json("proposal", "reject", ids[0])
    assert code == EXIT_REFUSAL and found["commitment"]["recorded"] is False
    assert "It is not recorded in the ledger yet: pm-ai proposal approve" in found["reason"]
    assert set(found["commitment"]["commands"]) == {"approve", "edit"}


def test_the_dashboard_leaves_out_closed_commitments(machine):
    """Matrix "Dashboard, closed items", "Dashboard, altered", "Dashboard, edited item"."""
    items = tuple(
        (what, "Sam Rivera", None)
        for what in (
            "send the cache numbers", "book the room", "draft the notes", "plan the demo",
            "collect the feedback", "check the budget",
        )
    )
    _, ids = _summarised(machine, items)
    for each in ids[:5]:
        machine.run("proposal", "approve", each)
    _run(machine, "commitment", "done", ids[0])
    _run(machine, "commitment", "broken", ids[1])
    _run(machine, "commitment", "withdrawn", ids[2])
    _run(machine, "commitment", "altered", ids[3])
    machine.run("proposal", "edit", ids[4], "--what", "collect the feedback forms")
    _run(machine, "commitment", "done", ids[4])
    page = machine.page()
    for what in ("send the cache numbers", "book the room", "draft the notes", "collect the feedback forms"):
        assert what not in page
    # Story 15d: every recorded approved item leaves the meeting; one line counts them.
    lines = page.split("\n")
    assert not any(line.startswith("  - ") and ", approved" in line for line in lines)
    assert any(line.startswith("  - 5 commitments from this meeting, 1 open: ") for line in lines)
    altered = [line for line in lines if line.startswith("- **") and "plan the demo" in line]
    assert len(altered) == 1 and " — altered on " in altered[0]
    assert "  - Sam Rivera: check the budget" in page  # waiting


def test_a_telemetry_done_leaves_the_item_out_of_the_dashboard(machine):
    """Matrix "Dashboard, closed items": one fulfilled by a hand-written telemetry `verdict`."""
    ids = _approved(machine)
    assert ITEMS[0][0] in machine.page()
    _append(machine, _verdict(ids[0], "fulfilled", NOW + timedelta(minutes=1)))
    assert ITEMS[0][0] not in machine.page()


def test_the_dashboard_fails_open_on_an_unreadable_ledger(machine, monkeypatch):
    """Matrix "Dashboard, ledger unreadable"."""
    ids = _approved(machine)
    _run(machine, "commitment", "done", ids[0])
    monkeypatch.setattr(type(machine.storage), "read_commitments", lambda self, *, scope: (_ for _ in ()).throw(OSError(5, "x")))
    page = machine.page()
    assert f"  - Sam Rivera: {ITEMS[0][0]} (by Thursday), approved" in page
    # Story 15d: the note names the scope and the exception's type, no text.
    assert "The commitments of `personal` could not be read (OSError), so they are not listed" in page
    assert "`pm-ai commitment list --scope personal`" in page
    assert "No open commitment" not in page


def test_team_activity_hides_a_withdrawn_item(machine):
    """Matrix "Team Activity, withdrawn", "done and broken", "ledger unreadable" (by the join)."""
    from pm_ai.app.pipelines import _item_decisions

    ids = _approved(machine, scope=ALPHA)
    _run(machine, "commitment", "withdrawn", ids[0])
    _run(machine, "commitment", "done", ids[1])
    _run(machine, "commitment", "broken", ids[2])

    class Line:
        def __init__(self, item):
            from pm_ai.core.member_updates import ItemKind

            self.item, self.kind, self.meeting = item, ItemKind.ACTION, "mtg_c"

    class Ledger:
        live = {n: Line(each) for n, each in enumerate(ids)}

    from pm_ai.core.commitment_log import CommitmentLog

    decisions, _, _ = _item_decisions(
        machine.daemon, Ledger(), scope=ALPHA, commitments=CommitmentLog(machine.storage).read(ALPHA)  # type: ignore[arg-type]
    )
    assert decisions[ids[0]].hidden is True
    # Story 15d: done shows its state; broken is not shown on a project page.
    assert decisions[ids[1]].commitment.row_state.startswith("commitment done on ")
    assert decisions[ids[2]].commitment.row_state == ""
    unread, _, _ = _item_decisions(machine.daemon, Ledger(), scope=ALPHA, commitments=None)  # type: ignore[arg-type]
    assert ids[0] not in unread


# ── Matrix rows asserted end to end (15c audit) ──────────────────────────────


def test_due_the_same_over_an_estimated_date_is_written_as_the_pms(machine):
    """Matrix "Due the same, estimated": written, now exact and "set by you", `due.said` kept."""
    _, ids = _summarised(machine, start_estimated_from="header")
    for each in ids:
        machine.run("proposal", "approve", each)
    code, shown = _json(machine, "commitment", "show", ids[0])
    assert shown["commitment"]["due"]["estimated"] is True
    assert shown["commitment"]["due"]["value"] == "2026-10-22"
    code, out, _ = _run(machine, "commitment", "show", ids[0])
    assert "due Thu 22 Oct (meeting day estimated)" in out and "past due" not in out
    code, found = _json(machine, "commitment", "due", ids[0], "2026-10-22")
    assert code == EXIT_OK and found["outcome"] == "due_set" and found["wrote"] == ["due_set"]
    due = found["commitment"]["due"]
    assert due == {"said": "by Thursday", "value": "2026-10-22", "date": "2026-10-22", "estimated": False, "by_pm": True}
    code, out, _ = _run(machine, "commitment", "show", ids[0])
    assert "due Thu 22 Oct, set by you" in out and '("by Thursday")' in out


@pytest.mark.parametrize(
    ("today", "words", "due", "shown"),
    [
        (datetime(2026, 10, 30, 9, 0, tzinfo=UTC), "end of week", "2026-11-06", "due Fri 6 Nov"),
        (datetime(2026, 10, 31, 9, 0, tzinfo=UTC), "end of week", "2026-11-06", "due Fri 6 Nov"),
        (datetime(2026, 11, 1, 9, 0, tzinfo=UTC), "end of week", "2026-11-06", "due Fri 6 Nov"),
        (datetime(2026, 10, 30, 9, 0, tzinfo=UTC), "до конца недели", "2026-11-06", "due Fri 6 Nov"),
        (datetime(2026, 10, 30, 9, 0, tzinfo=UTC), "в пятницу", "2026-11-06", "due Fri 6 Nov"),
        (datetime(2026, 10, 28, 9, 0, tzinfo=UTC), "EOW", "2026-10-30", "due Fri 30 Oct"),
        (datetime(2026, 10, 29, 9, 0, tzinfo=UTC), "friday", "2026-10-30", "due Fri 30 Oct"),
    ],
)
def test_the_pms_due_words_through_the_command(machine, today, words, due, shown):
    """Matrix "End of week from Friday on", "End of week midweek", "Friday on a Thursday", "Friday on a Friday"."""
    ids = _approved(machine)
    machine.clock = today
    code, out, _ = _run(machine, "commitment", "due", ids[0], words)
    assert code == EXIT_OK and shown in out
    assert _lines(machine)[-1]["due"] == due


def test_a_meetings_own_friday_stays_unresolved_on_its_pending_line(machine):
    """Matrix "Extraction unchanged": a meeting on Fri 30 Oct, "by Friday", still `unresolved`."""
    machine.clock = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    ids = _approved(
        machine, (("send the cache TTL numbers", "Sam Rivera", "by Friday"),),
        start=datetime(2026, 10, 30, 10, 0, tzinfo=UTC),
    )
    (line,) = _lines(machine)
    assert line["commitment"] == ids[0] and line["day"] == "2026-10-30"
    assert line["due"] == "unresolved" and line["due_said"] == "by Friday"


def test_a_closed_card_offers_no_due_or_owner_and_completion_agrees(machine):
    """Matrix "Closed card"."""
    ids = _approved(machine)
    _run(machine, "commitment", "broken", ids[0])
    code, found = _json(machine, "commitment", "show", ids[0])
    assert code == EXIT_OK
    for name in ("due", "owner"):
        action = found["commitment"]["actions"][name]
        assert action["available"] is False and action["command"] is None and action["reason"]
        offered = commitment_choices(name, now=machine.clock, paths=machine.storage.paths)
        assert ids[0] not in [each[0] for each in offered]


def test_undo_is_unavailable_on_the_card_after_four_hours(machine):
    """Matrix "Undo offered": the card's `undo` action, then unavailable with its reason."""
    ids = _approved(machine)
    _run(machine, "commitment", "done", ids[0])
    code, found = _json(machine, "commitment", "show", ids[0])
    undo = found["commitment"]["actions"]["undo"]
    assert undo["available"] is True and undo["reason"].startswith("until ")
    machine.clock = NOW + timedelta(hours=4, minutes=1)
    code, found = _json(machine, "commitment", "show", ids[0])
    undo = found["commitment"]["actions"]["undo"]
    assert undo["available"] is False and "more than 4 hours ago" in undo["reason"]


def test_a_malformed_line_with_no_readable_pending_line_is_refused(machine):
    """Matrix "Malformed": without a readable `pending` line too."""
    _approved(machine)
    _append(machine, "- entry=cml_0123456789ab commitment=prp_hand_written kind=pending what=x")
    code, found = _json(machine, "commitment", "show", "prp_hand_written")
    assert code == EXIT_REFUSAL and found["code"] == "malformed"
    assert "fix or remove that line" in found["reason"]


def test_team_activity_shows_every_line_when_the_ledger_cannot_be_read(machine, monkeypatch):
    """Matrix "Team Activity, ledger unreadable"."""
    from pm_ai.app.pipelines import _item_decisions
    from pm_ai.core.member_updates import ItemKind

    ids = _approved(machine, scope=ALPHA)
    _run(machine, "commitment", "withdrawn", ids[0])

    class Line:
        def __init__(self, item):
            self.item, self.kind, self.meeting = item, ItemKind.ACTION, "mtg_c"

    class Ledger:
        live = {n: Line(each) for n, each in enumerate(ids)}

    # Story 15d: the render's one read failed, so no ledger is handed in.
    decisions, _, _ = _item_decisions(machine.daemon, Ledger(), scope=ALPHA, commitments=None)  # type: ignore[arg-type]
    assert ids[0] not in decisions


def test_reject_of_an_open_item_in_text_and_json(machine):
    """Matrix "Reject refusal": the three commands, in the text and in `commitment.commands`."""
    ids = _approved(machine)
    code, out, err = machine.run("proposal", "reject", ids[0])
    said = out + err
    assert code == EXIT_REFUSAL
    for words in (
        f"pm-ai commitment withdrawn {ids[0]}",
        f"pm-ai commitment altered {ids[0]}",
        f"pm-ai proposal edit {ids[0]}",
    ):
        assert words in said
    code, found = machine.json("proposal", "reject", ids[0])
    assert set(found["commitment"]["commands"]) == {"withdrawn", "altered", "edit"}
    assert found["commitment"]["show"] == ["commitment", "show", ids[0], "--scope", "personal"]
    for each in found["commitment"]["commands"].values():
        words = each["command"]
        assert words[1] in TABLE[words[0]].leaves


# ── Review findings (15c, three lenses) ──────────────────────────────────────


class _ReadsFailAfter(_Interleaved):
    """The store, whose ledger reads fail from the `n`th on: an unreadable re-read."""

    def __init__(self, storage, *, fail_from: int, before=None, append=None) -> None:
        super().__init__(storage, before)
        self.reads = 0
        self.fail_from = fail_from
        self.append_with = append

    def read_commitments(self, *, scope):
        self.reads += 1
        if self.reads >= self.fail_from:
            raise OSError(5, "unreadable for the test")
        return self._storage.read_commitments(scope=scope)

    def append_commitments(self, lines, *, scope, after):
        if self.append_with is not None:
            return self.append_with(self._storage, lines, scope, after)
        return super().append_commitments(lines, scope=scope, after=after)


def _review_prompts(machine, monkeypatch, *answers: str, words=()):
    """`_review`, also recording each prompt the walk asked."""
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    queue, asked = list(answers), []

    def typed(prompt=""):
        asked.append(prompt)
        if not queue:
            raise EOFError
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", typed)
    code, out, err = _run(machine, "commitment", "review", *words)
    return code, out, asked


def test_completion_and_the_review_due_prompt_show_the_due_not_the_heading(machine, monkeypatch):
    """Finding 1: the due comes from the commitment, never from the heading's last words."""
    ids = _approved(machine)
    machine.clock = SUNDAY  # 22 Oct is past due now
    _run(machine, "commitment", "due", ids[1], "2026-10-30")
    offered = {each[0]: each[3] for each in commitment_choices("show", now=machine.clock, paths=machine.storage.paths)}
    assert offered[ids[0]] == "Thu 22 Oct"
    assert offered[ids[1]] == "Fri 30 Oct"
    assert offered[ids[2]] == 'no due date worked out from "next sprint"'
    code, out, asked = _review_prompts(machine, monkeypatch, "a", "", "", "", "", "q")
    assert "  due [Thu 22 Oct] (Enter keeps, - clears): " in asked


def test_a_refused_z_keeps_the_card_it_was_on(machine, monkeypatch):
    """Finding 2: `d` on card 1, a line on card 1 from elsewhere, `z` on card 2."""
    ids = _approved(machine)
    real = CommitmentDesk.current
    seen: list[str] = []

    def current(self, commitment_id, scope):
        seen.append(commitment_id)
        if len(seen) == 2:
            at = machine.clock + timedelta(minutes=1)
            from pm_ai.core.commitment_log import OwnerSet

            _append(machine, render_line(OwnerSet(ids[0], "Alex Morgan", occurred=at, via="prompt", at=at)).text)
        return real(self, commitment_id, scope)

    monkeypatch.setattr(CommitmentDesk, "current", current)
    code, out, _ = _review(machine, monkeypatch, "d", "z", "s", "s")
    assert code == EXIT_OK
    assert "a later line follows it" in out and "its owner was set to Alex Morgan" in out
    # Card 2 was asked again after the refused `z`, then skipped; card 3 skipped.
    assert "1 marked done, 2 skipped." in out
    assert UNDO_KEY_TEXT not in out.split("a later line follows it", 1)[1]


UNDO_KEY_TEXT = "[z] undo that done"


def test_review_a_counts_an_edit_refused_after_the_mark(machine, monkeypatch):
    """Finding 3: `a` whose edit is refused after the mark: counted altered, the next words, moved on."""
    from pm_ai.core.proposal_cards import ProposalRefused

    ids = _approved(machine)

    def refused(*_a, **_k):
        raise ProposalRefused("the edit was refused for the test.")

    monkeypatch.setattr(ProposalDesk, "edit", refused)
    code, out, _ = _review(machine, monkeypatch, "a", "new words", "", "", "", "q")
    assert code == EXIT_OK
    assert "the altered mark is recorded" in out
    assert f"next: pm-ai proposal edit {ids[0]} --scope personal --what 'new words'" in out
    assert "1 altered." in out
    assert [line["state"] for line in _marked(machine)] == ["altered"]


def test_review_a_alters_with_a_new_due(machine, monkeypatch):
    """Finding 3: the successful `a` path."""
    ids = _approved(machine)
    machine.clock = SUNDAY
    code, out, _ = _review(machine, monkeypatch, "a", "", "", "friday", "a note", "q")
    assert code == EXIT_OK and "1 altered." in out
    assert [line["kind"] for line in _lines(machine)][-2:] == ["marked", "due_set"]
    assert _lines(machine)[-1]["due"] == "2026-10-30" and _marked(machine)[0]["note"] == "a note"


def test_an_unreadable_reread_before_writing_is_ledger_unreadable(machine):
    """Finding 4a: `LedgerChanged`, then the re-read fails: refused, nothing written."""
    ids = _approved(machine)
    other = _desk(machine)
    store = _ReadsFailAfter(machine.storage, fail_from=2, before=lambda: other.mark("broken", ids[1]))
    with pytest.raises(desk_module.CommitmentRefused) as refused:
        _desk(machine, storage=store).mark("done", ids[0])
    assert refused.value.code == "ledger_unreadable" and "nothing was written" in refused.value.reason
    assert [line["commitment"] for line in _marked(machine)] == [ids[1]]


def test_a_failed_read_after_a_written_append_still_reports_it_made(machine):
    """Finding 4b: the line is written, the read after it fails: reported as made."""
    ids = _approved(machine)
    store = _ReadsFailAfter(machine.storage, fail_from=2)
    answer = _desk(machine, storage=store).mark("done", ids[0])
    assert answer.outcome == "marked" and answer.card.state.value == "fulfilled"
    assert answer.wrote == ("marked",) and len(_marked(machine)) == 1


def test_the_wording_edit_survives_a_failed_read_either_way(machine, monkeypatch):
    """Finding 4c: after the edit, or in its refusal, a failed read keeps the answer."""
    from pm_ai.core.proposal_cards import ProposalRefused

    ids = _approved(machine)
    store = _ReadsFailAfter(machine.storage, fail_from=3)
    answer = _desk(machine, storage=store).altered(ids[0], what="send the numbers for the API only")
    assert answer.outcome == "altered" and answer.edited == f"{ids[0]}_e1"
    store = _ReadsFailAfter(machine.storage, fail_from=3)
    desk = _desk(machine, storage=store)

    def refused(*_a, **_k):
        raise ProposalRefused("the edit was refused for the test.")

    monkeypatch.setattr(desk.proposals, "edit", refused)
    with pytest.raises(desk_module.CommitmentRefused) as caught:
        desk.altered(ids[1], what="new words")
    assert caught.value.code == "edit_refused" and caught.value.wrote == ("marked",)
    assert caught.value.next is not None and caught.value.card is not None


def test_a_partial_disk_write_says_what_was_written(machine):
    """Finding 5: part of the append landed; the refusal names the kinds written."""
    ids = _approved(machine)
    machine.clock = SUNDAY

    def first_line_only(storage, lines, scope, after):
        storage.append_commitments(list(lines)[:1], scope=scope, after=after)
        raise OSError(28, "No space left on device")

    store = _ReadsFailAfter(machine.storage, fail_from=10**6, append=first_line_only)
    with pytest.raises(desk_module.CommitmentRefused) as refused:
        _desk(machine, storage=store).altered(ids[0], owner="Alex Morgan", due="friday")
    assert refused.value.code == "disk" and refused.value.wrote == ("marked",)
    assert "only part of it was written: marked" in refused.value.reason


def test_a_disk_failure_with_an_unreadable_ledger_says_it_could_not_check(machine):
    """Finding 5: the re-read fails too; never "nothing was written" unchecked."""
    ids = _approved(machine)

    def fails(storage, lines, scope, after):
        raise OSError(5, "I/O error")

    store = _ReadsFailAfter(machine.storage, fail_from=2, append=fails)
    with pytest.raises(desk_module.CommitmentRefused) as refused:
        _desk(machine, storage=store).mark("done", ids[0])
    assert refused.value.code == "disk"
    assert "could not be checked" in refused.value.reason and "nothing was written" not in refused.value.reason


def test_meeting_choice_by_id_and_title(machine, monkeypatch):
    """Finding 6: `--meeting` filters; `--title` on two meetings is `ambiguous`, none is `not_found`."""
    first = _approved(machine)
    second = _approved(machine, (("plan the demo", "Sam Rivera", None),), meeting_id="mtg_d")
    code, found = _json(machine, "commitment", "list", "--meeting", "mtg_d")
    assert code == EXIT_OK and [card["commitment_id"] for card in found["commitments"]] == second
    code, found = _json(machine, "commitment", "list", "--title", "weekly")
    assert code == EXIT_REFUSAL and found["code"] == "ambiguous"
    code, found = _json(machine, "commitment", "list", "--title", "nonesuch")
    assert code == EXIT_REFUSAL and found["code"] == "not_found"
    code, out, _ = _review(machine, monkeypatch, "s", words=("--meeting", "mtg_d"))
    assert code == EXIT_OK and "1 skipped." in out and first


def test_meeting_choice_codes_do_not_hang_on_the_sentence(machine, monkeypatch):
    """Finding 6: the code comes from the refusal's kind, so rewording 13a cannot flip it."""
    from pm_ai.app import proposals as proposal_module

    def reworded(*_a, **_k):
        raise proposal_module.MeetingChoiceRefused("ambiguous", "entirely different words.")

    monkeypatch.setattr(proposal_module, "choose_meeting", reworded)
    code, found = _json(machine, "commitment", "list", "--title", "weekly")
    assert found["code"] == "ambiguous" and found["reason"] == "entirely different words."


def test_owner_names_fold_nfc_and_nfd_into_one(machine):
    """Finding 9."""
    ids = _approved(machine)
    _run(machine, "commitment", "owner", ids[0], unicodedata.normalize("NFC", "Zoë Example"))
    _run(machine, "commitment", "owner", ids[1], unicodedata.normalize("NFD", "Zoë Example"))
    names = commitment_owners(None, "personal", now=machine.clock, paths=machine.storage.paths)
    assert names.count(unicodedata.normalize("NFC", "Zoë Example")) == 1
    assert unicodedata.normalize("NFD", "Zoë Example") not in names or (
        unicodedata.normalize("NFD", "Zoë Example") == unicodedata.normalize("NFC", "Zoë Example")
    )


def test_team_activity_through_issue_notes(machine):
    """Finding 17: a withdrawn item's row is gone; done and broken rows stay."""
    from test_commitments import _work
    from pm_ai.app.pipelines import _issue_notes
    from pm_ai.core.aliases import parse_aliases
    from pm_ai.core.cadence import LookBack, LookBackKind
    from pm_ai.core.member_updates import Candidate, ItemKind, Link, LinkKind, lines_to_append
    from pm_ai.core.member_updates import parse_ledger as parse_updates

    items = (("fix the cache", "Sam Rivera", None), ("plan the demo", "Sam Rivera", None), ("fix the build", "Sam Rivera", None))
    meeting, ids = _summarised(machine, items, scope=ALPHA)
    for each in ids:
        machine.run("proposal", "approve", each)
    links = tuple(
        Link(Candidate(ItemKind.ACTION, item, what, owner, due), 142, LinkKind.NUMBER)
        for item, (what, owner, due) in zip(ids, items)
    )
    lines = lines_to_append(parse_updates(None), meeting, links=links, at=NOW)
    machine.storage.append_member_updates(list(lines), scope=ALPHA, after=len(machine.storage.read_member_updates(scope=ALPHA)))

    def rows() -> str:
        notes = _issue_notes(
            machine.daemon, scope=ALPHA, work=_work(), entries=(), now=NOW,
            since=LookBack(NOW - timedelta(days=1), LookBackKind.SINCE_LAST_RENDER, NOW - timedelta(days=1)),
            zone=UTC, aliases=parse_aliases(None),
        )
        return "\n".join(notes.rows.get(142, ()))

    before = rows()
    # Team Activity shows two rows per issue, then counts the rest.
    assert "fix the cache" in before and "1 more meeting update on this issue is not shown." in before
    _run(machine, "commitment", "withdrawn", ids[0])
    _run(machine, "commitment", "done", ids[1])
    _run(machine, "commitment", "broken", ids[2])
    shown = rows()
    assert "fix the cache" not in shown and "plan the demo" in shown and "fix the build" in shown


def test_the_prompts_completion_offers_commitments_from_the_home(machine):
    """Finding 18: `entry._completion()` over the test's home (monkeypatched by `Machine`)."""
    ids = _approved(machine)
    held = _folded_one(machine, ids[0])
    from dataclasses import replace

    promise = replace(held, commitment_id="prp_promise", proposal_id="prp_promise", type="implicit_commitment",
                      owner=None, speaker="Sam Rivera")
    _append(machine, render_line(Pending(promise, via="terminal", written="approval", at=NOW)).text)
    completion = entry._completion()
    offered = [each.display for each in completion.complete("commitment done ")]
    assert ids[0] in offered and "prp_promise" in offered
    owners = [each.display for each in completion.complete("commitment owner prp_promise ")]
    assert owners[0] == "Sam Rivera" and "Alex Morgan" in owners


def test_review_through_the_prompt_loop_records_via_prompt(machine, monkeypatch):
    """Finding 19: `pm-ai` at a terminal runs the prompt with `typed_at_the_prompt`, and
    `commitment review` typed there writes `via=prompt`."""
    from pm_ai.surfaces.cli import repl

    ids = _approved(machine)
    given: list[object] = []
    monkeypatch.setattr(entry, "_at_a_terminal", lambda: True)
    monkeypatch.setattr(entry.repl, "run", lambda runner, **_k: given.append(runner) or EXIT_OK)
    assert entry.main([]) == EXIT_OK and given == [entry.typed_at_the_prompt]
    monkeypatch.undo()
    monkeypatch.setenv("HOME", str(machine.home))
    monkeypatch.chdir(machine.tmp)
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: machine.keychain)
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    answers = iter(["d", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    lines = iter(["commitment review", "exit"])
    assert repl.run(entry.typed_at_the_prompt, read=lambda prompt: next(lines)) == EXIT_OK
    (line,) = _marked(machine)
    assert line["commitment"] == ids[0] and line["via"] == "prompt"


def test_list_all_orders_open_then_closed_and_names_more(machine):
    """Finding 20: open ones by due, then closed ones newest change first; `next.more`."""
    ids = _approved(machine, LISTED)
    _run(machine, "commitment", "done", ids[3])
    machine.clock = NOW + timedelta(minutes=5)
    _run(machine, "commitment", "broken", ids[1])
    code, found = _json(machine, "commitment", "list", "--state", "all")
    assert [card["commitment_id"] for card in found["commitments"]] == [ids[0], ids[2], ids[1], ids[3]]
    code, found = _json(machine, "commitment", "list", "--state", "done", "--limit", "1")
    assert found["more"] == 0 and found["next"]["more"][-2:] == ["--state", "all"]
    _run(machine, "commitment", "withdrawn", ids[0])
    code, found = _json(machine, "commitment", "list", "--state", "all", "--limit", "1")
    assert found["more"] == 3 and found["next"]["review"] is not None
    code, found = _json(machine, "commitment", "list", "--state", "broken", "--limit", "1")
    assert found["more"] == 0
    code, out, _ = _run(machine, "commitment", "list", "--state", "done")
    assert "to see more: pm-ai commitment list --scope personal --state all" in out
    _run(machine, "commitment", "broken", ids[2])
    code, found = _json(machine, "commitment", "list", "--state", "broken", "--limit", "1")
    assert found["more"] == 1 and found["next"]["more"][-4:] == ["--state", "broken", "--limit", "200"]


def test_reject_with_an_unreadable_ledger_offers_both_marks_unchecked(machine, monkeypatch):
    """Finding 21."""
    ids = _approved(machine)

    def unreadable(self, *, scope):
        raise OSError(5, "unreadable for the test")

    monkeypatch.setattr(type(machine.storage), "read_commitments", unreadable)
    code, found = machine.json("proposal", "reject", ids[0])
    assert code == EXIT_REFUSAL and found["commitment"]["recorded"] is None
    assert set(found["commitment"]["commands"]) == {"withdrawn", "altered", "edit"}
    assert "could not be read, so these may be refused:" in found["reason"]


def test_undo_by_the_dones_version_after_another_line_is_superseded(machine):
    """Finding 22."""
    ids = _approved(machine)
    code, done = _json(machine, "commitment", "done", ids[0])
    at = NOW + timedelta(minutes=1)
    from pm_ai.core.commitment_log import OwnerSet

    _append(machine, render_line(OwnerSet(ids[0], "Alex Morgan", occurred=at, via="prompt", at=at)).text)
    code, found = _json(machine, "commitment", "undo", ids[0], "--if-version", done["commitment"]["version"])
    assert code == EXIT_REFUSAL and found["code"] == "undo_superseded"


def test_altered_clears_owner_and_due_through_the_parser(machine):
    """Finding 23."""
    ids = _approved(machine)
    code, found = _json(machine, "commitment", "altered", ids[0], "--owner", "", "--due", "")
    assert code == EXIT_OK and found["wrote"] == ["marked", "owner_set", "due_set"]
    assert found["commitment"]["owner"] is None and found["commitment"]["due"]["value"] == "none"


def test_altered_due_of_only_spaces_is_a_usage_error(machine):
    """Andrei, 2026-10-26: `--due "  "` is refused as `due` refuses it, nothing written."""
    ids = _approved(machine)
    before = _ledger_path(machine, PERSONAL).read_text(encoding="utf-8")
    code, _, _ = _run(machine, "commitment", "altered", ids[0], "--due", "   ")
    assert code == EXIT_USAGE
    assert _ledger_path(machine, PERSONAL).read_text(encoding="utf-8") == before


def test_every_code_and_outcome_the_desk_says_is_listed():
    """Finding 24: the refusal codes and the outcomes are the spec's lists."""
    import re as _re

    from pm_ai.core.commitment_cards import OUTCOMES, REFUSAL_CODES

    source = Path(desk_module.__file__).read_text(encoding="utf-8")
    codes = set(_re.findall(r'CommitmentRefused\(\s*"([a-z_]+)"', source))
    outcomes = set(_re.findall(r'_outcome\(\s*"([a-z_]+)"', source)) | set(
        _re.findall(r'CommitmentOutcome\(\s*"([a-z_]+)"', source)
    )
    assert codes and codes <= set(REFUSAL_CODES), codes - set(REFUSAL_CODES)
    assert outcomes and outcomes <= set(OUTCOMES), outcomes - set(OUTCOMES)
    proposal_source = Path(desk_module.__file__).with_name("proposals.py").read_text(encoding="utf-8")
    kinds = set(_re.findall(r'MeetingChoiceRefused\(\s*"([a-z_]+)"', proposal_source))
    assert kinds == {"ambiguous", "not_found"} <= set(REFUSAL_CODES)


def test_a_render_reads_each_scopes_ledger_once(machine, monkeypatch):
    """Finding 25: once per personal or project scope with digests, and no other scope."""
    _approved(machine)
    _approved(machine, (("plan the demo", "Sam Rivera", None),), meeting_id="mtg_d")
    _approved(machine, (("check the budget", "Sam Rivera", None),), meeting_id="mtg_p", scope=ALPHA)
    real = type(machine.storage).read_commitments
    scopes: list[str] = []

    def spy(self, *, scope):
        scopes.append(str(scope))
        return real(self, scope=scope)

    monkeypatch.setattr(type(machine.storage), "read_commitments", spy)
    machine.page()
    assert sorted(scopes) == ["personal", "project:alpha"]


def test_review_walks_past_the_listings_limit(machine, monkeypatch):
    """Finding 26: `review` has no limit; every open commitment is walked."""
    ids = _approved(machine)
    _fill(machine, PERSONAL, ids[0], 205)
    code, out, _ = _review(machine, monkeypatch, *(["s"] * 208))
    assert code == EXIT_OK and "208 skipped." in out
    assert "beyond the" not in out
