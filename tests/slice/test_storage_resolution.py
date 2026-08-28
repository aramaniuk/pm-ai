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

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from pm_ai.app.wiring import build
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
    daemon.storage.append_event_log("- [test] first", scope=daemon.scope)
    daemon.storage.append_event_log("- [test] second", scope=daemon.scope)

    segments = list(daemon.storage.paths.resolve(daemon.scope, EVENT_LOG).glob("*.md"))
    assert len(segments) == 1, "one open segment per month, appended to"
    assert segments[0].read_text() == "- [test] first\n- [test] second\n"


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
        daemon.storage.append_event_log(f"- [test] {scope}", scope=scope)

    resolved = {scope: daemon.storage.paths.resolve(scope, EVENT_LOG) for scope in ALL_SCOPES}
    assert len(set(resolved.values())) == len(ALL_SCOPES), "two scopes share one ledger"
    for scope, log in resolved.items():
        assert (log / f"{NOW:%Y-%m}.md").read_text() == f"- [test] {scope}\n"

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
        daemon.storage.append_event_log("- [test] leak", scope=escaping)


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
    d.storage.append_event_log("- [test] entry", scope=d.scope)

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
        storage.append_event_log(f"- [test] {why}", scope=PERSONAL)


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
    d.storage.append_event_log("- [test] entry", scope=d.scope)

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
