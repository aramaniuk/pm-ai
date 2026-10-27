"""Story 15d: the commitments a dashboard shows, read once per scope per render.

Each page reads its scopes' commitment ledgers through `CommitmentLog.read`
once per render (`read_page_ledgers`): the personal page the personal ledger
and each enrolled project's (AD-38's direction, project into personal), a
project page its own only (AD-25). The meeting lines, the block and Team
Activity share that one read.

`page_commitments` turns the folded ledgers into the block's values: the
open commitments in `commitment list`'s order (`open_order`), the closed
ones counted inside the window, the notes for what could not be read or
used, and — on a project page — names shown only when they resolve to a
member and text withheld on a confidential issue. Every word about a state
or a due is `commitment_cards.page_parts`'. Reads only: no write, no model.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, tzinfo

from pm_ai.app.commitments import LEDGER_READ_FAILURES
from pm_ai.core.commitment_cards import (
    LedgerCounts,
    bad_lines_words,
    closed_state_word,
    closed_words,
    open_order,
    page_due,
    page_parts,
)
from pm_ai.core.commitment_log import CommitmentLedger, CommitmentLog
from pm_ai.core.member_updates import spoken_numbers
from pm_ai.core.proposals import IMPLICIT_COMMITMENT
from pm_ai.core.team_activity import NO_ISSUE_NOTES, IssueNotes
from pm_ai.domain.commitments import Commitment, StateSource
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.renders import (
    WITHHELD_CONFIDENTIAL,
    WITHHELD_TEXT,
    PageCommitment,
    PageCommitments,
)
from pm_ai.domain.work import ProjectWork
from pm_ai.ports import StoragePort

__all__ = [
    "PageLedgers",
    "PageWords",
    "page_commitments",
    "page_scopes",
    "read_page_ledgers",
]

NameOf = Callable[[str | None, str | None], str | None]
"""A name as recorded and the line's meeting id (or `None`), to the name a page shows."""


def page_scopes(scope: DataScope, watched: Sequence[str]) -> tuple[DataScope, ...]:
    """The scopes a page reads, in page scope order: personal first, then projects by id.

    The personal page reads the personal ledger and each enrolled project's;
    a project page its own only (AD-25). Never a `people` scope.
    """
    if scope.kind is ScopeKind.PROJECT:
        return (scope,)
    return (DataScope(ScopeKind.PERSONAL),) + tuple(
        DataScope(ScopeKind.PROJECT, project_id) for project_id in sorted(watched)
    )


@dataclass
class PageLedgers:
    """Each scope's ledger as one render read it: folded, or why it could not be read.

    `order` is page scope order. A root in more than one ledger (a hand copy)
    belongs to the first scope in that order, and is skipped everywhere else.
    """

    order: tuple[DataScope, ...]
    read: dict[str, CommitmentLedger] = field(default_factory=dict)
    failed: dict[str, BaseException] = field(default_factory=dict)
    first: dict[str, str] = field(default_factory=dict)
    """Each root, to the scope whose copy the page shows."""

    def ledger(self, scope: DataScope) -> CommitmentLedger | None:
        return self.read.get(str(scope))

    def failure(self, scope: DataScope) -> BaseException | None:
        return self.failed.get(str(scope))

    def is_copy(self, scope: DataScope, root: str) -> bool:
        """A hand copy of a root shown under an earlier scope: skipped here."""
        return self.first.get(root, str(scope)) != str(scope)

    def also_in(self, scope: DataScope, root: str) -> tuple[DataScope, ...]:
        return tuple(
            other
            for other in self.order
            if other != scope and (held := self.ledger(other)) is not None and root in held.commitments
        )


def read_page_ledgers(storage: StoragePort, scopes: Sequence[DataScope]) -> PageLedgers:
    """Each of `scopes`' ledgers, read once through `CommitmentLog.read` (15d).

    A ledger that cannot be read never fails the render: its failure is kept
    for the note that names the scope and the exception's type.
    """
    found = PageLedgers(order=tuple(scopes))
    log = CommitmentLog(storage)
    for scope in scopes:
        try:
            ledger = log.read(scope)
        except LEDGER_READ_FAILURES as failed:
            found.failed[str(scope)] = failed
            continue
        found.read[str(scope)] = ledger
        for root in ledger.commitments:
            found.first.setdefault(root, str(scope))
    return found


@dataclass(frozen=True, slots=True)
class PageWords:
    """What the block showed that the digests' notes must account for (a project page).

    `names` when any line had an owner or a speaker to resolve, so the alias
    table's sentence is printed when it could not be read; `unknown` when a
    line's text was withheld because the GitLab record could not tell
    confidentiality; `links` when it was withheld because the meeting
    updates' links could not be read.
    """

    names: bool = False
    unknown: bool = False
    links: bool = False


def _as_recorded(label: str | None, meeting: str | None) -> str | None:
    return label


def _withheld(
    commitment: Commitment, said: str | None, notes: IssueNotes, work: object
) -> str:
    """How much of a commitment a project page withholds (15d, stricter than 7d).

    Confidential when its root is linked to a confidential issue, or a
    number it names is one; its text unknown when the links could not be read
    (action items), when it names a number while the GitLab record cannot be
    read, or when it names one the readable record does not hold.
    """
    numbers = spoken_numbers(commitment.what) | spoken_numbers(said or "")
    record = work if isinstance(work, ProjectWork) and work.instance is not None else None
    issues = {} if record is None else {issue.iid: issue for issue in record.issues}
    if commitment.commitment_id in notes.confidential or any(
        (issue := issues.get(number)) is not None and issue.confidential for number in numbers
    ):
        return WITHHELD_CONFIDENTIAL
    action = commitment.type != IMPLICIT_COMMITMENT
    if action and notes.links_unread:
        return WITHHELD_TEXT
    if numbers and (record is None or any(number not in issues for number in numbers)):
        return WITHHELD_TEXT
    return ""


def page_commitments(
    ledgers: PageLedgers,
    *,
    now: datetime,
    zone: tzinfo,
    since: datetime,
    project: DataScope | None = None,
    issue_notes: IssueNotes = NO_ISSUE_NOTES,
    work: object = None,
    name_of: NameOf = _as_recorded,
    unrecorded: Mapping[str, Sequence[str]] | None = None,
    not_enrolled: Sequence[DataScope] = (),
) -> tuple[PageCommitments, PageWords]:
    """The block of open commitments from the ledgers one render read (story 15d).

    `project` is the project page's scope (`None` for the personal page): its
    names are shown only when `name_of` resolves them to a member, a
    commitment Team Activity shows under an issue is counted rather than
    listed, and a confidential issue's text is withheld. `unrecorded` maps
    each scope to its approved items not yet recorded and not under a meeting
    on this page; `not_enrolled` the projects no longer enrolled whose
    approved items the store holds (the personal page only).
    """
    on_project = project is not None
    opened: list[tuple[Commitment, PageCommitment]] = []
    withheld_last: list[tuple[Commitment, PageCommitment]] = []
    under: set[int] = set()
    under_count = 0
    open_scopes: list[DataScope] = []
    closed: dict[str, list[int]] = {}
    closed_scopes: list[DataScope] = []
    unreadable: list[tuple[DataScope, str]] = []
    bad: list[tuple[DataScope, str]] = []
    names = unknown = links = False
    for scope in ledgers.order:
        failed = ledgers.failure(scope)
        if failed is not None:
            unreadable.append((scope, type(failed).__name__))
            continue
        ledger = ledgers.ledger(scope)
        if ledger is None:
            continue
        counts = LedgerCounts(
            str(scope), ledger.malformed, ledger.orphans, ledger.duplicates, ledger.conflicts, ledger.refused
        )
        if counts.any:
            bad.append((scope, bad_lines_words(counts)))
        for root, commitment in ledger.commitments.items():
            if root in ledger.malformed_roots or ledgers.is_copy(scope, root):
                continue
            details = ledger.details[root]
            if commitment.state.is_terminal:
                word = closed_state_word(commitment.state)
                if word is None or (on_project and word == "broken"):
                    continue
                at = details.closed_at
                if at is None:
                    continue
                at = min(at, now)  # a clock stepped back: counted as closed now
                if not since < at <= now:
                    continue
                by = closed.setdefault(word, [0, 0])
                by[0 if commitment.state_source is StateSource.PM else 1] += 1
                if scope not in closed_scopes:
                    closed_scopes.append(scope)
                continue
            if scope not in open_scopes:
                open_scopes.append(scope)
            shown_under = issue_notes.items.get(root) if on_project else None
            if shown_under:
                under.update(shown_under)
                under_count += 1
                continue
            parts = page_parts(commitment, details, now=now, zone=zone)
            promise = commitment.type == IMPLICIT_COMMITMENT
            hold = _withheld(commitment, parts.said, issue_notes, work) if on_project else ""
            if hold == WITHHELD_CONFIDENTIAL:
                line = PageCommitment(
                    scope=scope,
                    promise=promise,
                    due="",
                    withheld=hold,
                    title=commitment.title,
                    day=parts.origin_day,
                    meeting_id=commitment.meeting_id,
                )
                withheld_last.append((commitment, line))
                continue
            owner_label = commitment.owner
            speaker_label = commitment.speaker if promise else None
            if owner_label or speaker_label:
                names = True
            owner = name_of(owner_label, commitment.meeting_id) if owner_label else None
            speaker = name_of(speaker_label, commitment.meeting_id) if speaker_label else None
            if hold == WITHHELD_TEXT:
                if commitment.type != IMPLICIT_COMMITMENT and issue_notes.links_unread:
                    links = True
                else:
                    unknown = True
            line = PageCommitment(
                scope=scope,
                promise=promise,
                due=page_due(parts, project=on_project, withheld=bool(hold)),
                what="" if hold else commitment.what,
                what_cut=False if hold else commitment.what_cut,
                owner=owner,
                speaker=speaker,
                speaker_unresolved=on_project and bool(speaker_label) and speaker is None,
                said=parts.said if parts.quote_said and not hold else None,
                state=parts.block_state,
                title=commitment.title,
                day=parts.origin_day,
                meeting_id=commitment.meeting_id,
                also_in=ledgers.also_in(scope, root),
                withheld=hold,
                name_scope=not on_project and scope.kind is ScopeKind.PROJECT,
            )
            opened.append((commitment, line))
    opened.sort(key=lambda each: open_order(each[0]))
    # Withheld for a confidential issue: last, by the meeting's start, newest
    # first, so their place says nothing of their due.
    withheld_last.sort(
        key=lambda each: (
            0 if each[0].start is not None else 1,
            -each[0].start.timestamp() if each[0].start is not None else 0.0,
            each[0].commitment_id,
        )
    )
    held = unrecorded or {}
    block = PageCommitments(
        scopes=tuple(scope for scope in ledgers.order if ledgers.ledger(scope) is not None),
        lines=tuple(line for _, line in [*opened, *withheld_last]),
        under_issue=tuple(sorted(under)),
        under_issue_count=under_count,
        open_scopes=tuple(open_scopes),
        closed=closed_words({word: (pm, telemetry) for word, (pm, telemetry) in closed.items()}),
        closed_scopes=tuple(closed_scopes),
        since=since,
        unreadable=tuple(unreadable),
        bad_lines=tuple(bad),
        unrecorded=tuple(
            (scope, tuple(held[str(scope)][:3]), len(held[str(scope)]))
            for scope in ledgers.order
            if held.get(str(scope))
        ),
        not_enrolled=tuple(not_enrolled),
        project=project,
    )
    return block, PageWords(names=names, unknown=unknown, links=links)
