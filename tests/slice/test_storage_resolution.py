"""Storage writes through the resolver (story 1b).

Every write the single writer performs now goes to a path `pm_ai.platform.paths`
returned, and every timestamp it stamps comes from an injected clock. These
exercise the parts of that which a green suite could otherwise hide:

- the event log is *appended* to. The static AD-5 scan reads call source text, so
  spelling the artifact key as a constant blinded it once already; the guard that
  cannot be blinded is reading the file back and finding both entries.
- the clock is used at month granularity for the segment filename, so a fixture
  pinned to the current month proves nothing. The clocks here are in other years.
- a batch that fails partway leaves no dedup row behind, or the events it names
  are lost permanently — deduped against a segment that was never written.
- all four scopes, because `event_log/` is homed in all four and the traversal
  the subject-id guard exists for is a people-scope id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import pytest

from pm_ai.app.wiring import build
from pm_ai.core import ledger
from pm_ai.domain.scope_model import FOREIGN_ROOTS
from pm_ai.domain import (
    ARTIFACT_TIER,
    EVENT_LOG,
    OPERATIONAL_DB,
    DataScope,
    ScopeKind,
    ScopeResolutionError,
    TargetRef,
    Tier,
)
from pm_ai.domain.event_entries import EventEntry, SelfActionType
from pm_ai.domain.harvest import Cursor
from pm_ai.platform.vcs import GitVcs
from pm_ai.platform.paths import (
    APPLICATION_DIRNAME,
    ENCLAVE_DIRNAME,
    PEOPLE_DIRNAME,
    PERSONAL_DIRNAME,
    PROJECT_DIRNAME,
    ROOTED_PROJECTS_DIRNAME,
    MalformedSubjectId,
    ScopePaths,
    UnknownProject,
    artifacts_in,
)
from pm_ai.storage.service import (
    NonUtcClock,
    SealedSegment,
    OperationalStoreUnavailable,
    StorageService,
)

from pm_ai.storage.crypto import AesGcmCrypto

# A real cipher with a fixed key: these tests never touch an encrypted
# artifact, and passing `PlaintextCrypto` would wire them as though the
# debug flag were on — a difference that would matter the day one does.
TEST_CIPHER = AesGcmCrypto(b"0" * 32)

NOW = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
# Deliberately not this month: the clock's only observable in `append_event_log`
# is the `%Y-%m` segment filename, so a fixture that happens to agree with the
# wall clock lets a re-introduced `datetime.now()` pass until the month turns.
ELSEWHEN = datetime(2019, 3, 4, 12, 30, tzinfo=timezone.utc)

PROJECT = DataScope(ScopeKind.PROJECT, "alpha")
PERSONAL = DataScope(ScopeKind.PERSONAL)
PEOPLE = DataScope(ScopeKind.PEOPLE, person_id="alex")
APPLICATION = DataScope(ScopeKind.APPLICATION)
ALL_SCOPES = (PROJECT, PERSONAL, PEOPLE, APPLICATION)


@pytest.fixture
def daemon(tmp_path):
    d = build(tmp_path, "alpha", now=lambda: NOW)
    d.connectors["gitlab:alpha"]._fake_api = [
        {"sha": "9f2a1c", "message": "Fix auth refactor", "author_email": "alex@example.com",
         "committed_at": NOW - timedelta(hours=2)},
        {"sha": "3b7e02", "message": "Add Redis benchmark", "author_email": "alex@example.com",
         "committed_at": NOW - timedelta(hours=1)},
    ]
    return d


def _events(daemon):
    """Two real normalized events, straight from the connector."""
    return daemon.connectors["gitlab:alpha"].harvest(Cursor()).events


def _mask(text: str) -> str:
    """Blank the minted surrogate so the rest of the grammar stays exact.

    The id is per-call by design (AD-34), and dropping the content assertion
    rather than masking it would retire the only check that notices a format
    drift reaching disk.
    """
    return re.sub(r"evt_[0-9a-f]+", "evt_ID", text)


def _test_entry(marker: str):
    """A minimal typed entry, standing in for the old free-string call.

    Uses a real category rather than inventing a `test` one: a tag existing only
    in fixtures is an entry no parser would ever be asked to read.
    """
    return EventEntry(
        category=SelfActionType.SECURITY, actor="test", fields=(("detail", marker),)
    )


def _segment(storage, scope, at=NOW):
    return storage.paths.resolve(scope, EVENT_LOG) / f"{at:%Y-%m}.md"


# ── AD-5: the ledger is appended to, never replaced ──────────────────────────


def test_the_event_log_is_appended_to_never_rewritten(daemon):
    """AD-5 — the guard no rename can blind.

    `test_ad5_storage_never_rewrites_a_markdown_ledger_in_place` matches the
    source text of the write call, and naming the artifact key with a constant
    was enough to make both event-log writes invisible to it. Reading the file
    back is what actually proves the mode.
    """
    daemon.storage.append_event_log(_test_entry("first"), scope=daemon.scope)
    daemon.storage.append_event_log(_test_entry("second"), scope=daemon.scope)

    segments = list(daemon.storage.paths.resolve(daemon.scope, EVENT_LOG).glob("*.md"))
    assert len(segments) == 1, "one open segment per month, appended to"
    body = _mask(segments[0].read_text())
    assert body == (
        "- [evt_ID] security actor=test ingested_at=2026-08-19T09:00:00+00:00 detail=first\n"
        "- [evt_ID] security actor=test ingested_at=2026-08-19T09:00:00+00:00 detail=second\n"
    )


def test_persisted_batches_accumulate_in_one_segment(daemon):
    """AD-5 — the second write site, which the same mutation truncated."""
    first = daemon.storage.persist_events(_events(daemon), scope=daemon.scope)
    assert first.persisted == 2

    daemon.connectors["gitlab:alpha"]._fake_api = [
        {"sha": "cc31de", "message": "Later work", "author_email": "alex@example.com",
         "committed_at": NOW},
    ]
    second = daemon.storage.persist_events(_events(daemon), scope=daemon.scope)
    assert second.persisted == 1

    body = _segment(daemon.storage, daemon.scope).read_text()
    for sha in ("9f2a1c", "3b7e02", "cc31de"):
        assert sha in body, f"{sha} was dropped — the earlier batch was overwritten"


# ── The four scopes ──────────────────────────────────────────────────────────


def test_every_scope_writes_into_its_own_resolved_tree(daemon, tmp_path):
    """AD-4 — one service, four scopes, and no directory named after a scope.

    `event_log/` is homed in all four scopes, so all four are exercised: the
    people scope is the enclave whose subject id is interpolated into a path, and
    the application scope is the one that must not end up holding Tier-1
    Markdown beside its own operational store.
    """
    for scope in ALL_SCOPES:
        daemon.storage.append_event_log(_test_entry(str(scope)), scope=scope)

    resolved = {scope: daemon.storage.paths.resolve(scope, EVENT_LOG) for scope in ALL_SCOPES}
    assert len(set(resolved.values())) == len(ALL_SCOPES), "two scopes share one ledger"
    for scope, log in resolved.items():
        assert _mask((log / f"{NOW:%Y-%m}.md").read_text()) == (
            f"- [evt_ID] security actor=test ingested_at=2026-08-19T09:00:00+00:00 detail={scope}\n"
        )

    # Each in the tree its scope owns, spelled from the resolver's own names so
    # this test cannot drift from the layout it is checking.
    assert PROJECT_DIRNAME in resolved[PROJECT].parts
    assert PERSONAL_DIRNAME in resolved[PERSONAL].parts
    assert resolved[PEOPLE].is_relative_to(daemon.storage.paths.people_root)
    assert PEOPLE_DIRNAME in resolved[PEOPLE].parts
    assert resolved[APPLICATION].is_relative_to(daemon.storage.paths.application_root)

    # The old layout put a `project_alpha/` directory at the root. Rather than
    # asserting the absence of that one name — which no code can produce any
    # more, so the assertion can never fail — assert that nothing but the
    # resolver's own three top-level directories was created.
    assert {p.name for p in tmp_path.iterdir()} <= {
        APPLICATION_DIRNAME,
        PERSONAL_DIRNAME,
        ROOTED_PROJECTS_DIRNAME,
    }


def test_a_traversing_person_id_is_refused_at_the_write(daemon):
    """AD-31 — the enclave boundary is a path, so the id that names it is checked.

    `DataScope` cannot refuse this: it is a domain type with no filesystem to
    reason about. The writer therefore inherits the resolver's refusal, and it
    arrives as a `ScopeResolutionError` — the domain base — because `storage` may
    not import the module the concrete classes live in.
    """
    escaping = DataScope(ScopeKind.PEOPLE, person_id="../../../tmp/evil")
    with pytest.raises(ScopeResolutionError):
        daemon.storage.append_event_log(_test_entry("leak"), scope=escaping)


# ── AD-3: Tier 2 is not in anyone's Markdown tree ────────────────────────────


def test_the_operational_store_is_outside_every_tier_one_path(daemon):
    """AD-3 — checked against every Tier-1 artifact, not just `memory/`.

    Defining "the Markdown tree" as the event log's parent directory left the
    store free to sit beside `config.toml` or `rules/` in a scope root and still
    pass.
    """
    paths = daemon.storage.paths
    store = paths.resolve(APPLICATION, OPERATIONAL_DB)
    assert store.exists(), "the store was not opened where the resolver says it lives"
    assert ENCLAVE_DIRNAME in store.parts, "Tier 2 belongs in the gitignored enclave"

    for scope in ALL_SCOPES:
        for artifact in artifacts_in(scope.kind):
            if ARTIFACT_TIER.get(artifact) is not Tier.TRUTH:
                continue
            # `people/` is Tier 1 and is a foreign scope root, which `resolve`
            # refuses since 2026-08-28. Its location still has to be checked:
            # it shares the `private/` enclave with the operational store.
            tier_one = (
                paths.foreign_scope_root(artifact)
                if artifact in FOREIGN_ROOTS
                else paths.resolve(scope, artifact)
            )
            assert store != tier_one and not store.is_relative_to(tier_one), (
                f"the operational store lies inside {artifact} of the "
                f"{scope.kind.value} scope"
            )


def test_an_unopenable_operational_store_names_itself(tmp_path):
    """A daemon that cannot open Tier 2 must say which path and why.

    The path is resolved now rather than passed in, so the operator cannot read
    it off the call site, and `sqlite3`'s own message names neither.
    """
    paths = ScopePaths.rooted(tmp_path)
    store = paths.resolve(APPLICATION, OPERATIONAL_DB)
    store.mkdir(parents=True)  # a directory where the file belongs

    with pytest.raises(OperationalStoreUnavailable) as refusal:
        StorageService(paths, now=lambda: NOW, vcs=GitVcs(), crypto=TEST_CIPHER)
    assert str(store) in str(refusal.value)


def test_a_store_created_before_the_settle_column_still_settles(tmp_path):
    """AD-3 — Tier 2 is never rebuilt, so a schema change has to be a migration.

    `CREATE TABLE IF NOT EXISTS` is a no-op against an existing store, so the
    first settle after this story would have failed on "no such column" for every
    operational.db that predates it — and "delete it and start again" throws away
    the job queue, the cursors, and the dedup set.
    """
    import sqlite3

    paths = ScopePaths.rooted(tmp_path)
    store = paths.resolve(APPLICATION, OPERATIONAL_DB, create=True)
    legacy = sqlite3.connect(store)
    legacy.executescript(
        """
        CREATE TABLE executed (
            key         TEXT PRIMARY KEY,
            lock_key    TEXT NOT NULL,
            external_id TEXT,
            state       TEXT NOT NULL,
            at          TEXT NOT NULL
        );
        """
    )
    legacy.commit()
    legacy.close()

    storage = StorageService(paths, now=lambda: NOW, vcs=GitVcs(), crypto=TEST_CIPHER)
    target = TargetRef.parse("gitlab:alpha:issue:102")
    storage.record_execution("idem_legacy", target, "cmt_1")

    assert storage.was_executed("idem_legacy")
    assert list(storage._db.execute("SELECT settled_at FROM executed")) == [(NOW.isoformat(),)]


# ── The injected clock ───────────────────────────────────────────────────────


def test_the_segment_filename_comes_from_the_injected_clock(tmp_path):
    """The one observable of the clock in `append_event_log`, pinned off-month."""
    d = build(tmp_path, "alpha", now=lambda: ELSEWHEN)
    d.storage.append_event_log(_test_entry("entry"), scope=d.scope)

    segments = list(d.storage.paths.resolve(d.scope, EVENT_LOG).glob("*.md"))
    assert [s.name for s in segments] == [f"{ELSEWHEN:%Y-%m}.md"] == ["2019-03.md"]


def test_the_ingestion_stamp_comes_from_the_injected_clock(tmp_path):
    """AD-35 — the stamp assigned at persist time, in a month that is not now."""
    d = build(tmp_path, "alpha", now=lambda: ELSEWHEN)
    d.connectors["gitlab:alpha"]._fake_api = [
        {"sha": "9f2a1c", "message": "Fix auth refactor", "author_email": "a@example.com",
         "committed_at": ELSEWHEN},
    ]
    result = d.storage.persist_events(_events(d), scope=d.scope)

    assert result.at == ELSEWHEN
    body = _segment(d.storage, d.scope, ELSEWHEN).read_text()
    assert f"ingested_at={ELSEWHEN.isoformat()}" in body


def test_a_mutation_is_stamped_from_the_injected_clock_at_both_ends(daemon):
    """AD-20 — claim time and settle time, so an open window is measurable."""
    daemon.skills.invoke(
        "gitlab.post_comment",
        target=TargetRef.parse("gitlab:alpha:issue:102"),
        payload={"comment": "Approved"},
        idempotency_key="idem_clock",
    )
    rows = list(daemon.storage._db.execute("SELECT at, settled_at FROM executed"))
    assert rows == [(NOW.isoformat(), NOW.isoformat())]


@pytest.mark.parametrize(
    "clock,why",
    [
        (lambda: datetime(2026, 8, 19, 9, 0), "naive"),
        (lambda: datetime(2026, 8, 19, 9, 0, tzinfo=timezone(timedelta(hours=2))), "offset"),
    ],
)
def test_a_clock_that_is_not_utc_is_refused(tmp_path, clock, why):
    """A wrong-month segment and a `TypeError` on comparison are worse than a raise."""
    storage = StorageService(
        ScopePaths.rooted(tmp_path), now=clock, vcs=GitVcs(), crypto=TEST_CIPHER
    )
    with pytest.raises(NonUtcClock):
        storage.append_event_log(_test_entry(why), scope=PERSONAL)


# ── The batch is one unit ────────────────────────────────────────────────────


@dataclass
class RefusingPaths:
    """A resolver that refuses to locate the ledger a fixed number of times.

    Three of the resolver's five refusals became reachable from inside
    `persist_events` in this story, none of which existed when its dedup rows
    were first written before the segment.
    """

    inner: ScopePaths
    refusals: int = 1

    def resolve(self, scope, artifact, *, create=False):
        if artifact == EVENT_LOG and self.refusals:
            self.refusals -= 1
            raise MalformedSubjectId("refused, as a resolver may")
        return self.inner.resolve(scope, artifact, create=create)


def test_a_refused_write_does_not_swallow_the_batch(daemon, tmp_path):
    """AD-34 — dedup rows and the segment append are one unit, or events vanish.

    The `INSERT INTO seen` ran per event before anything was written. A refusal
    after it left those rows pending in the implicit transaction, and the next
    unrelated `commit()` — a saved cursor, here, exactly as the harvest pipeline
    does it — made them permanent. Every event in the batch was then a duplicate
    of a segment entry that does not exist.
    """
    paths = RefusingPaths(ScopePaths.rooted(tmp_path / "txn"))
    storage = StorageService(paths, now=lambda: NOW, vcs=GitVcs(), crypto=TEST_CIPHER)
    events = _events(daemon)
    assert len(events) == 2

    with pytest.raises(ScopeResolutionError):
        storage.persist_events(events, scope=PROJECT)

    storage.save_cursor("gitlab:alpha", Cursor(), None)  # the unrelated commit

    result = storage.persist_events(events, scope=PROJECT)
    assert (result.persisted, result.duplicates) == (2, 0), (
        "the batch was deduped against a segment that was never written"
    )
    body = _segment(storage, PROJECT).read_text()
    assert body.count("commit_pushed") == 2


# ── The composition root ─────────────────────────────────────────────────────


def test_production_paths_reach_the_daemon(tmp_path):
    """AD-11 — the real factory has to be wireable, or only the test one is used.

    `rooted()` invents a repository path for any project id it is handed, which
    is precisely what `production()` refuses to do. Hardcoding `rooted()` in
    `build()` left that refusal with no caller.
    """
    repository = tmp_path / "repo-alpha"
    paths = ScopePaths.production(home=tmp_path / "home", projects={"alpha": repository})
    assert paths.project_parent is None, "production may not invent a repository"

    d = build(None, "alpha", paths=paths, now=lambda: NOW)
    d.storage.append_event_log(_test_entry("entry"), scope=d.scope)

    log = d.storage.paths.resolve(d.scope, EVENT_LOG)
    assert log.is_relative_to(repository / PROJECT_DIRNAME)
    assert (log / f"{NOW:%Y-%m}.md").exists()


def test_build_takes_exactly_one_of_a_root_and_a_resolver(tmp_path):
    """Both, or neither, is an ambiguity about where everything is stored."""
    paths = ScopePaths.rooted(tmp_path)
    for args, kwargs in ((( None, "alpha"), {}), ((tmp_path, "alpha"), {"paths": paths})):
        with pytest.raises(ValueError):
            build(*args, **kwargs)


def test_a_malformed_project_id_is_refused_at_build_time(tmp_path):
    """Mid-harvest is too late: the batch is already in hand and the write fails.

    Uppercase is the interesting case rather than an obvious one — two ids that
    differ only in case are one directory on a case-insensitive filesystem.
    """
    with pytest.raises(MalformedSubjectId):
        build(tmp_path, "Alpha", now=lambda: NOW)
    with pytest.raises(ScopeResolutionError):
        build(tmp_path, "../escape", now=lambda: NOW)


def test_an_unregistered_project_is_refused_at_build_time(tmp_path):
    """AD-11 — a repository enters through the registry, so an absent one is an error."""
    paths = ScopePaths.production(home=tmp_path / "home", projects={"alpha": tmp_path / "a"})
    with pytest.raises(UnknownProject):
        build(None, "beta", paths=paths, now=lambda: NOW)


# ── Story 2d: one renderer owns the line the ledger already holds ────────────


def test_the_persisted_line_keeps_the_grammar_it_had_before_the_renderer(daemon):
    """Captured from `_append_batch` before 2d moved the format out of it.

    The break this catches is the expensive one: a format drift orphans every
    segment already on disk, and nothing fails at the moment it happens. Only
    the minted id is masked — everything else is asserted byte for byte.
    """
    daemon.storage.persist_events(_events(daemon), scope=daemon.scope)
    lines = _segment(daemon.storage, daemon.scope).read_text().splitlines()

    masked = [re.sub(r"evt_[0-9a-f]+", "evt_MASKED", line) for line in lines]
    for line in masked:
        assert re.fullmatch(
            r"- \[evt_MASKED\] \w+ actor=\S+ ingested_at=\S+ src=\S+ "
            r"occurred_at=\S+ authored_by=\w+",
            line,
        ), f"the ledger grammar moved: {line!r}"


def test_an_absent_provider_timestamp_still_renders_unknown(daemon):
    """`occurred_at` is nullable, and `unknown` is what the ledger has always said."""
    events = tuple(replace(event, occurred_at=None) for event in _events(daemon))
    daemon.storage.persist_events(events, scope=daemon.scope)

    body = _segment(daemon.storage, daemon.scope).read_text()
    assert "occurred_at=unknown" in body
    assert "occurred_at=None" not in body


# ── Story 2e: the writer accepts a typed entry and names it ──────────────────


def test_the_writer_mints_the_entry_id(daemon):
    """AD-34 — the surrogate is assigned by storage at persist, never by a caller."""
    daemon.storage.append_event_log(_test_entry("first"), scope=daemon.scope)

    line = _segment(daemon.storage, daemon.scope).read_text().rstrip("\n")
    assert re.fullmatch(
        r"- \[evt_[0-9a-f]+\] security actor=test "
        + re.escape(f"ingested_at={NOW.isoformat()} detail=first"),
        line,
    )


def test_an_entry_arriving_with_an_id_is_refused(daemon):
    """Overwriting it silently would make AD-34 a convention rather than a rule."""
    entry = replace(_test_entry("first"), entry_id="evt_minted_by_the_caller")
    with pytest.raises(ValueError):
        daemon.storage.append_event_log(entry, scope=daemon.scope)


# ── Story 2b: a provider clock that cannot be believed, flagged not dropped ──


def _skewed(daemon, **kw):
    """The connector's real events, with one field overridden on all of them."""
    return tuple(replace(event, **kw) for event in _events(daemon))


def test_a_future_dated_provider_timestamp_is_flagged_and_still_persisted(daemon):
    """AD-35 — flagged. `persist_events` is all-or-nothing, so raising here would
    drop a whole harvest because one provider clock is wrong."""
    events = _skewed(daemon, occurred_at=NOW + timedelta(hours=48))
    result = daemon.storage.persist_events(events, scope=daemon.scope)

    body = _segment(daemon.storage, daemon.scope).read_text()
    assert result.persisted == len(events)
    assert body.count("occurred_at_flag=implausible") == len(events)


def test_a_flagged_timestamp_is_never_backfilled_from_the_local_clock(daemon):
    """The substitution AD-35 forbids: the suspect value stays, verbatim, beside
    the flag. A replaced one is indistinguishable from a real one afterwards."""
    skewed = NOW + timedelta(hours=48)
    daemon.storage.persist_events(_skewed(daemon, occurred_at=skewed), scope=daemon.scope)

    body = _segment(daemon.storage, daemon.scope).read_text()
    assert f"occurred_at={skewed.isoformat()}" in body
    assert f"occurred_at={NOW.isoformat()}" not in body


def test_a_plausible_timestamp_carries_no_flag(daemon):
    daemon.storage.persist_events(
        _skewed(daemon, occurred_at=NOW - timedelta(hours=2)), scope=daemon.scope
    )
    assert "occurred_at_flag" not in _segment(daemon.storage, daemon.scope).read_text()


def test_an_absent_timestamp_is_not_flagged(daemon):
    """Absence is a distinct state: `unknown` is a known answer, not a suspect one."""
    daemon.storage.persist_events(_skewed(daemon, occurred_at=None), scope=daemon.scope)

    body = _segment(daemon.storage, daemon.scope).read_text()
    assert "occurred_at=unknown" in body
    assert "occurred_at_flag" not in body


def test_a_naive_provider_timestamp_is_flagged(daemon):
    """Not comparable to the batch clock, so its plausibility is unknowable."""
    daemon.storage.persist_events(
        _skewed(daemon, occurred_at=datetime(2026, 8, 19, 8, 0)), scope=daemon.scope
    )
    assert "occurred_at_flag=implausible" in _segment(
        daemon.storage, daemon.scope
    ).read_text()


def test_a_mixed_batch_persists_everything_and_counts_what_it_flagged(daemon):
    """The break: a connector with a broken clock flags every event it emits and
    nothing anywhere reports that it happened."""
    events = _events(daemon)
    mixed = (replace(events[0], occurred_at=NOW + timedelta(hours=48)),) + events[1:]

    result = daemon.storage.persist_events(mixed, scope=daemon.scope)

    assert result.persisted == len(mixed)
    assert result.flagged == 1


# ── Every entry carries when it arrived (CAP-10) ────────────────────────────


def test_a_self_action_entry_carries_the_ingestion_time(daemon):
    """CAP-10 requires an ISO-8601 timestamp on *every* entry.

    The harvest path built one by hand; entries arriving through
    `append_event_log` carried an id, a category and an actor, and no time at all.
    """
    daemon.storage.append_event_log(_test_entry("first"), scope=daemon.scope)

    line = _segment(daemon.storage, daemon.scope).read_text().rstrip("\n")
    assert f"ingested_at={NOW.isoformat()}" in line


def test_the_ingestion_time_comes_from_the_injected_clock(daemon, tmp_path):
    """Not `datetime.now()`: the writer has exactly one clock and this is it."""
    d = build(tmp_path, "beta", now=lambda: ELSEWHEN)
    d.storage.append_event_log(_test_entry("entry"), scope=d.scope)

    body = (d.storage.paths.resolve(d.scope, EVENT_LOG) / f"{ELSEWHEN:%Y-%m}.md").read_text()
    assert f"ingested_at={ELSEWHEN.isoformat()}" in body


def test_the_ingestion_time_sits_in_the_same_place_on_every_path(daemon):
    """One grammar: a reader finds the field without knowing which path wrote it."""
    daemon.storage.append_event_log(_test_entry("first"), scope=daemon.scope)
    daemon.storage.persist_events(_events(daemon), scope=daemon.scope)

    for line in _segment(daemon.storage, daemon.scope).read_text().splitlines():
        after_actor = line.split(" actor=", 1)[1]
        assert after_actor.split(" ", 1)[1].startswith("ingested_at="), line


# ── File order is arrival order ─────────────────────────────────────────────


def test_file_order_is_arrival_order(daemon):
    """The one thing that orders events exactly, with no ties.

    Appends are serialized (AD-5) and each adds one line at the end, so the
    sequence in the file *is* the sequence they arrived in — at any rate, and
    including entries that share a timestamp. `fold` deliberately reorders; this
    is what a caller reads when it wants a chronology instead.
    """
    for marker in ("first", "second", "third", "fourth"):
        daemon.storage.append_event_log(_test_entry(marker), scope=daemon.scope)

    text = _segment(daemon.storage, daemon.scope).read_text()
    parsed = ledger.parse_segment(text)
    assert [dict(e.fields)["detail"] for e in parsed] == [
        "first",
        "second",
        "third",
        "fourth",
    ]


# ── Story 2g: exactly one segment is open, and the rest are immutable ────────


class _Rewindable:
    """A clock the test moves, so a month boundary is crossable in both directions."""

    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


def test_a_month_boundary_seals_the_old_segment_with_no_ceremony(tmp_path):
    clock = _Rewindable(datetime(2026, 8, 31, 23, 0, tzinfo=timezone.utc))
    d = build(tmp_path, "alpha", now=clock)
    d.storage.append_event_log(_test_entry("august"), scope=d.scope)

    clock.at = datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc)
    d.storage.append_event_log(_test_entry("september"), scope=d.scope)

    log = d.storage.paths.resolve(d.scope, EVENT_LOG)
    assert sorted(p.name for p in log.glob("*.md")) == ["2026-08.md", "2026-09.md"]
    assert "august" in (log / "2026-08.md").read_text()
    assert "september" in (log / "2026-09.md").read_text()


def test_a_write_into_a_sealed_segment_is_refused_not_redirected(tmp_path):
    """Redirecting would silently re-date the entry; `occurred_at` carries when
    it happened, and the filename does not."""
    clock = _Rewindable(datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc))
    d = build(tmp_path, "alpha", now=clock)
    d.storage.append_event_log(_test_entry("september"), scope=d.scope)

    clock.at = datetime(2026, 8, 31, 23, 0, tzinfo=timezone.utc)
    with pytest.raises(SealedSegment) as caught:
        d.storage.append_event_log(_test_entry("late august"), scope=d.scope)

    message = str(caught.value)
    assert "2026-08.md" in message and "2026-09.md" in message


def test_a_refused_write_leaves_the_sealed_segment_untouched(tmp_path):
    clock = _Rewindable(datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc))
    d = build(tmp_path, "alpha", now=clock)
    d.storage.append_event_log(_test_entry("september"), scope=d.scope)
    log = d.storage.paths.resolve(d.scope, EVENT_LOG)

    clock.at = datetime(2026, 8, 31, 23, 0, tzinfo=timezone.utc)
    with pytest.raises(SealedSegment):
        d.storage.append_event_log(_test_entry("late august"), scope=d.scope)

    assert not (log / "2026-08.md").exists(), "a refused write created a segment"
    assert "september" in (log / "2026-09.md").read_text()


def test_the_batch_path_is_guarded_too(tmp_path):
    """A guard on one append path is not a guard."""
    clock = _Rewindable(datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc))
    d = build(tmp_path, "alpha", now=clock)
    d.storage.append_event_log(_test_entry("september"), scope=d.scope)

    d.connectors["gitlab:alpha"]._fake_api = [
        {
            "sha": "9f2a1c",
            "message": "Fix auth refactor",
            "author_email": "alex@example.com",
            "committed_at": clock.at - timedelta(hours=2),
        }
    ]
    events = _events(d)
    assert events, "the fixture harvested nothing, so the guard was never reached"

    clock.at = datetime(2026, 8, 31, 23, 0, tzinfo=timezone.utc)
    with pytest.raises(SealedSegment):
        d.storage.persist_events(events, scope=d.scope)


def test_the_first_append_opens_a_segment(tmp_path):
    d = build(tmp_path, "alpha", now=lambda: NOW)
    d.storage.append_event_log(_test_entry("first"), scope=d.scope)

    log = d.storage.paths.resolve(d.scope, EVENT_LOG)
    assert [p.name for p in log.glob("*.md")] == [f"{NOW:%Y-%m}.md"]


def test_a_stray_file_in_the_log_directory_is_not_a_segment(tmp_path):
    """`- [test]` notes, editor leftovers and `.DS_Store` must not seal anything."""
    d = build(tmp_path, "alpha", now=lambda: NOW)
    log = d.storage.paths.resolve(d.scope, EVENT_LOG, create=True)
    (log / "notes.md").write_text("not a segment\n")
    (log / "9999-99.md.bak").write_text("nor this\n")

    d.storage.append_event_log(_test_entry("first"), scope=d.scope)
    assert f"first" in (log / f"{NOW:%Y-%m}.md").read_text()
