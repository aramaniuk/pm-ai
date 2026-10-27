"""What the operational store holds about each dashboard scope's renders (story 9b),
and what a render is handed about meeting summaries (story 7d).

Tier 2: the instant of the last successful render, which Proactive Enablement
looks back to, and the last failed attempt with its reason, which `pm-ai
doctor`'s `renders` line names. Written by every render path — the daemon's
07:00 render, a hand-run `pm-ai dashboard`, a dashboard job — each through one
insert-or-update statement keyed by scope.

Story 7d: the meetings whose summary — or its failure — was recorded since a
scope's last render, as values read from the summary state rows, the meeting
records and the proposals store before the render runs. The project renderer
takes `ProjectMeetingDigests`, whose constructor refuses a digest from any
scope but that project (AD-25, AD-38): the wall is a type.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.meeting_summaries import SummaryMaker, SummaryStateKind

__all__ = [
    "DigestItem",
    "DigestMaker",
    "DigestUnreadable",
    "MeetingDigest",
    "MeetingDigests",
    "PageCommitment",
    "PageCommitments",
    "ProjectMeetingDigests",
    "WITHHELD_CONFIDENTIAL",
    "WITHHELD_TEXT",
    "RenderRecord",
    "RenderUnrecorded",
    "ScopeUnreadable",
]


@dataclass(frozen=True, slots=True)
class RenderRecord:
    """One dashboard scope's last successful render and its current failure.

    `last_success` is `None` until a render succeeds. `last_failure` and
    `reason` are both `None` when the last attempt did not fail: a success
    clears them. All instants are aware UTC.
    """

    scope: DataScope
    last_success: datetime | None
    last_failure: datetime | None
    reason: str | None
    retryable: bool = False
    """The failure is one waiting can clear; the daemon retries it on the waits."""

    @property
    def failing(self) -> bool:
        """Whether the latest recorded attempt failed: a failure later than the
        last success. Decided here, on the read side, so a failure recorded
        before a newer success — two writers landing out of order — does not
        read as failing."""
        if self.last_failure is None:
            return False
        return self.last_success is None or self.last_failure > self.last_success


class RenderUnrecorded(Exception):
    """The dashboard was published, and recording its instant then failed (9b).

    The page on disk is the new one; only the record is missing, so the next
    evaluation renders again. The sentence is pm-ai's own and names why;
    `path` is where the new page is.
    """

    def __init__(self, sentence: str, *, path: Path | None = None) -> None:
        super().__init__(sentence)
        self.path = path


# ── Story 7d: meeting summaries and open action items on the dashboard ───────


@dataclass(frozen=True, slots=True)
class DigestItem:
    """One open action item as the page shows it: `owner: what (due)`.

    `owner` is the text to show, decided before the render: the label as said
    on the personal page; on a project page the member it resolves to by `7e`'s
    read-time rule, or `None` — shown as unassigned — when it resolves to none.
    `confidential`, on a project page only, is an item linked to a
    confidential issue — or to any issue, when the project's GitLab record
    could not be read (Andrei, 2026-10-20): its text is never shown, and
    `what` is empty; the meeting, owner and due date stay.
    """

    what: str
    owner: str | None = None
    due: str | None = None
    confidential: bool = False
    approved: bool = False
    """Story 13a: approved by the PM; otherwise waiting for review."""
    review_by: datetime | None = None
    """Story 13a: a waiting item's window end — "to review by <date>"."""
    unrecorded: str | None = None
    """Story 15d: an approved item the ledger holds no commitment for: the approved
    proposal's own id, which `proposal approve` writes the line for."""
    malformed: str | None = None
    """Story 15d: an approved item whose root a malformed ledger line names."""

    def __post_init__(self) -> None:
        if self.confidential and self.what:
            raise ValueError(
                "an action item on a confidential issue carries no text to the page."
            )
        if (self.unrecorded or self.malformed) and not self.approved:
            raise ValueError("only an approved item is recorded, or not, as a commitment.")


@dataclass(frozen=True, slots=True)
class DigestMaker:
    """Who made the summary the record holds, from its parsed `RecordedSummary`."""

    made_by: SummaryMaker
    model: str
    requested: bool


@dataclass(frozen=True, slots=True)
class MeetingDigest:
    """One meeting whose summary, or its failure, was recorded in the window.

    `state`, `waiting_for` and `reason` are the summary state row's: its state,
    its maker (`made_by`) and its reason. `summary` and `maker` are the
    record's `## Summary` region, decoded — `None` when the record holds none,
    which with a `waiting` or `failed` row is the reason there is no excerpt,
    and with a summary there is the newer state to note. `items` are at most
    the page's limit of open action items and `hidden` the count of the rest;
    `placed` the issues Team Activity shows `placed_count` of them under, on a
    project page, left out of `items`; `unreadable_items` the open items under
    this meeting whose stored proposal could not be read, counted rather than
    dropped.
    """

    scope: DataScope
    meeting_id: str
    start: datetime
    title: str
    recorded: datetime
    state: SummaryStateKind
    waiting_for: SummaryMaker = SummaryMaker.LOCAL
    reason: str = ""
    summary: str | None = None
    maker: DigestMaker | None = None
    items: tuple[DigestItem, ...] = ()
    hidden: int = 0
    placed: tuple[int, ...] = ()
    placed_count: int = 0
    unreadable_items: int = 0
    expired: int = 0
    """Story 13a: items that expired without review and have no replacement."""
    waiting: int = 0
    """Story 13a: items waiting for review, shown or not: the review line's cue."""
    commitments: int = 0
    """Story 15d: the commitments of this scope's ledger whose `pending` line names
    this meeting, open and closed; 0 when the ledger could not be read."""
    open_commitments: int = 0
    """Story 15d: how many of `commitments` are open."""
    hidden_waiting_only: bool = False
    """Story 15d: every item `hidden` counts is waiting, so the overflow names
    `--state staged`; otherwise `--state open`."""

    def __post_init__(self) -> None:
        if not 0 <= self.open_commitments <= self.commitments:
            raise ValueError(
                f"{self.open_commitments} open of {self.commitments} commitments counts "
                f"nothing real."
            )
        for name in ("hidden", "placed_count", "unreadable_items", "expired", "waiting"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name}={getattr(self, name)} counts nothing; it is at least 0.")
        if self.placed_count and not self.placed:
            raise ValueError(
                f"{self.placed_count} action items are said to be shown under an issue, "
                f"and no issue is named."
            )
        if self.placed and not self.placed_count:
            raise ValueError("issues are named with no action item shown under them.")

    @property
    def quoted(self) -> bool:
        """Whether the page quotes this meeting's summary as an excerpt."""
        return self.summary is not None and self.state is not SummaryStateKind.NOTHING_SAID


@dataclass(frozen=True, slots=True)
class DigestUnreadable:
    """A summary row in the window whose record could not be read: named, not hidden."""

    scope: DataScope
    meeting_id: str
    recorded: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class ScopeUnreadable:
    """A scope whose summaries could not be read, or `None`: the summary rows themselves."""

    scope: DataScope | None
    reason: str


Digest = MeetingDigest | DigestUnreadable


def _check_shown(shown: tuple[Digest, ...], earlier: int) -> None:
    if earlier < 0:
        raise ValueError(f"earlier={earlier} counts no meetings; it is at least 0.")
    if earlier and not shown:
        raise ValueError(
            f"{earlier} earlier meetings are counted with none shown: the shown ones "
            f"are the most recent, so there is nothing for them to be earlier than."
        )
    for digest in shown:
        if not isinstance(digest, (MeetingDigest, DigestUnreadable)):
            raise TypeError(f"{digest!r} is not a meeting digest.")


@dataclass(frozen=True, slots=True)
class MeetingDigests:
    """The personal dashboard's: personal meetings and every enrolled project's.

    The personal page may list a project's meetings — the direction AD-38
    allows — so this accepts digests of personal and project scopes, and
    refuses any other (a `people` meeting is never read for a dashboard). It
    has no Team Activity, so no digest here may say an item is shown under an
    issue. `shown` is the most recently recorded first; `earlier` counts the
    rest in the window; `unread` names each scope that could not be read;
    `not_enrolled` each project with summaries recorded in the window that is
    no longer enrolled, so not listed; `enrolment_unchecked` why that check
    could not be made, when it could not.
    """

    shown: tuple[Digest, ...] = ()
    earlier: int = 0
    unread: tuple[ScopeUnreadable, ...] = ()
    not_enrolled: tuple[DataScope, ...] = ()
    enrolment_unchecked: str | None = None

    def __post_init__(self) -> None:
        _check_shown(self.shown, self.earlier)
        allowed = (ScopeKind.PERSONAL, ScopeKind.PROJECT)
        for digest in self.shown:
            if digest.scope.kind not in allowed:
                raise ValueError(
                    f"a {digest.scope} meeting is never shown on a dashboard; the "
                    f"personal page lists personal and project meetings only."
                )
            if isinstance(digest, MeetingDigest) and (digest.placed or digest.placed_count):
                raise ValueError(
                    "the personal page has no Team Activity, so no action item there "
                    "is shown under an issue."
                )
            if isinstance(digest, MeetingDigest) and any(i.confidential for i in digest.items):
                raise ValueError(
                    "the personal page shows the PM's own action items as said; only a "
                    "project page withholds a confidential issue's item."
                )
        for failure in self.unread:
            if failure.scope is not None and failure.scope.kind not in allowed:
                raise ValueError(
                    f"{failure.scope} is never read for a dashboard, so it cannot be "
                    f"stated as unread on one."
                )
        for scope in self.not_enrolled:
            if scope.kind is not ScopeKind.PROJECT:
                raise ValueError(f"{scope} is not a project, so it is never enrolled.")


@dataclass(frozen=True, slots=True)
class ProjectMeetingDigests:
    """One project's digests — and nothing from any other scope (AD-25, AD-38).

    The only digests `render_project_dashboard` takes. The constructor refuses
    a scope that is not a project, a digest from any scope but `scope`, and an
    unread line naming another scope, so a personal digest cannot reach a
    project page by a wrong branch: it fails here, at construction.
    `aliases_unread` is why the saved alias table could not be read, when it
    could not: owners were then matched by exact name only.
    `confidentiality_unknown` is why the project's GitLab record could not
    tell which issues are confidential, when it could not: every item linked
    to an issue then has its text withheld (Andrei, 2026-10-20).
    `links_unread`: `7e`'s ledger could not be read, so which items are linked
    to an issue cannot be told, and every item's text was withheld.
    """

    scope: DataScope
    shown: tuple[Digest, ...] = ()
    earlier: int = 0
    unread: tuple[ScopeUnreadable, ...] = ()
    aliases_unread: str | None = None
    confidentiality_unknown: str | None = None
    links_unread: bool = False

    def __post_init__(self) -> None:
        if self.scope.kind is not ScopeKind.PROJECT:
            raise ValueError(
                f"{self.scope} is not a project, so its meetings have no project page "
                f"to be shown on (AD-25)."
            )
        _check_shown(self.shown, self.earlier)
        for digest in self.shown:
            if digest.scope != self.scope:
                raise ValueError(
                    f"a {digest.scope} meeting cannot be shown on the {self.scope} "
                    f"dashboard: a project page shows its own meetings only (AD-25, AD-38)."
                )
        for failure in self.unread:
            if failure.scope is not None and failure.scope != self.scope:
                raise ValueError(
                    f"{failure.scope} cannot be named on the {self.scope} dashboard "
                    f"(AD-38)."
                )


# ── Story 15d: the block of open commitments ─────────────────────────────────

WITHHELD_CONFIDENTIAL = "confidential"
"""A project page's commitment on a confidential issue: its kind and meeting only."""

WITHHELD_TEXT = "text"
"""A project page's commitment whose issue's confidentiality cannot be told: its
text and its due as said are withheld; the date, owner and state stay."""


@dataclass(frozen=True, slots=True)
class PageCommitment:
    """One open commitment as the block shows it (story 15d).

    Every word about its state and due (`due`, `state`) was built by
    `commitment_cards.page_parts` before the render; the renderer only
    composes and escapes. `owner` and `speaker` are the names to show: as
    recorded on the personal page; on a project page the member a name
    resolves to, or `None` — and `speaker_unresolved` when a speaker was
    said whom no member matches. `said` is the due as said, quoted only when
    a date was worked out from it. `withheld` is `""`, `WITHHELD_TEXT` or
    `WITHHELD_CONFIDENTIAL` (a project page only).
    """

    scope: DataScope
    promise: bool
    due: str
    what: str = ""
    what_cut: bool = False
    owner: str | None = None
    speaker: str | None = None
    speaker_unresolved: bool = False
    said: str | None = None
    state: str = ""
    title: str | None = None
    day: str | None = None
    """The meeting's start as the origin says it (`Mon 19 Oct`); `None` with no start on the line."""
    meeting_id: str | None = None
    also_in: tuple[DataScope, ...] = ()
    withheld: str = ""
    name_scope: bool = False
    """On the personal page, a project's commitment names its scope."""

    def __post_init__(self) -> None:
        if self.withheld not in ("", WITHHELD_TEXT, WITHHELD_CONFIDENTIAL):
            raise ValueError(f"{self.withheld!r} is no way of withholding a commitment.")
        if self.withheld and (self.what or self.said is not None):
            raise ValueError("a withheld commitment carries no text or due as said to the page.")
        if self.withheld == WITHHELD_CONFIDENTIAL and (
            self.due or self.owner or self.speaker or self.state or self.speaker_unresolved
        ):
            raise ValueError(
                "a commitment on a confidential issue shows its kind and meeting only, as "
                "Team Activity's row does."
            )


@dataclass(frozen=True, slots=True)
class PageCommitments:
    """A page's block of open commitments, read from its scopes' ledgers (story 15d).

    `scopes` are the scopes whose ledger was read, in page scope order
    (personal first, then projects by id). `lines` is every open commitment
    the block may list, in its order; the renderer shows the first
    `MAX_PAGE_COMMITMENTS` and counts the rest. `under_issue` are the iids
    Team Activity shows `under_issue_count` open ones under (a project
    page). `open_scopes` are the scopes with an open commitment. `closed` is
    the closed line's counts in words (`commitment_cards.closed_words`),
    `closed_scopes` the scopes with one, `since` the window's start.
    `unreadable` names each scope whose ledger could not be read and the
    exception's type name; `bad_lines` each scope's counts of lines the fold
    could not use; `unrecorded` each scope's approved items not yet recorded
    and not under a meeting on this page (the first ids, and how many in
    all); `not_enrolled` the projects no longer enrolled whose approved
    items the store still holds. `project` is the project page's scope:
    every scope named must be it (AD-25).
    """

    scopes: tuple[DataScope, ...] = ()
    lines: tuple[PageCommitment, ...] = ()
    under_issue: tuple[int, ...] = ()
    under_issue_count: int = 0
    open_scopes: tuple[DataScope, ...] = ()
    closed: str = ""
    closed_scopes: tuple[DataScope, ...] = ()
    since: datetime | None = None
    unreadable: tuple[tuple[DataScope, str], ...] = ()
    bad_lines: tuple[tuple[DataScope, str], ...] = ()
    unrecorded: tuple[tuple[DataScope, tuple[str, ...], int], ...] = ()
    not_enrolled: tuple[DataScope, ...] = ()
    project: DataScope | None = None

    def __post_init__(self) -> None:
        if self.under_issue_count and not self.under_issue:
            raise ValueError("commitments are said to be shown under an issue, and none is named.")
        named = [
            *self.scopes,
            *(line.scope for line in self.lines),
            *(other for line in self.lines for other in line.also_in),
            *self.open_scopes,
            *self.closed_scopes,
            *(scope for scope, _ in self.unreadable),
            *(scope for scope, _ in self.bad_lines),
            *(scope for scope, _, _ in self.unrecorded),
            *self.not_enrolled,
        ]
        allowed = (ScopeKind.PERSONAL, ScopeKind.PROJECT)
        for scope in named:
            if scope.kind not in allowed:
                raise ValueError(f"{scope} keeps no commitment ledger, so no page names it.")
        if self.project is None:
            if self.under_issue_count:
                raise ValueError("the personal page has no Team Activity to show a commitment under.")
            if any(line.withheld for line in self.lines):
                raise ValueError("the personal page withholds nothing (7d).")
            return
        if self.project.kind is not ScopeKind.PROJECT:
            raise ValueError(f"{self.project} is not a project, so it has no project page (AD-25).")
        for scope in named:
            if scope != self.project:
                raise ValueError(
                    f"{scope} cannot be named on the {self.project} dashboard: a project page "
                    f"reads its own ledger only (AD-25)."
                )
        if self.not_enrolled:
            raise ValueError("a project page names no other project (AD-25, AD-38).")
