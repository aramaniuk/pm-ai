"""The PM's controls over meeting updates: `pm-ai member name|link|unmatched` (story 7e).

`MemberDesk` is what `pm_ai.surfaces.cli.dispatch` reaches through its
`Members` protocol: the CLI may not import storage, so the reads and appends
are made here, over the daemon this process composed — the client's own
writer for `member name` (it asks at the terminal, so it never runs in the
daemon), the daemon's handed writer for `member link` and `member unmatched`.
`aliases.md`, the ledger and `8t`'s record are read afresh for every command
(AD-5's refresh list).

The rules are `pm_ai.core.member_updates`' and `pm_ai.core.aliases`'; this
module only reads, decides what to append, and appends.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import TypeVar

from pm_ai.app.wiring import Daemon
from pm_ai.core.aliases import AliasRow, normalise, parse_aliases, render_alias_row
from pm_ai.core.meeting_records import MalformedMeeting, MeetingNotFound
from pm_ai.core.member_updates import (
    LinkKind,
    MemberRefused,
    NamePlan,
    People,
    Person,
    ProjectScope,
    Verdict,
    describe_person,
    is_generic_label,
    parse_ledger,
    people_of,
    render_verdict,
    unmatched_report,
)
from pm_ai.domain.handles import InvalidPersonHandle, parse_person_handle
from pm_ai.domain.identity import DataScope
from pm_ai.domain.member_ledgers import ALIAS_SCOPE, ALIAS_TABLE, MEMBER_UPDATES, NotAProjectScope
from pm_ai.domain.scope_model import ScopeResolutionError
from pm_ai.domain.work import GitLabPerson, ProjectWork
from pm_ai.ports import ArtifactBusy, DecryptionFailed, LedgerChanged
from pm_ai.storage.service import OperationalStoreUnavailable

__all__ = ["MemberDesk"]

_Named = TypeVar("_Named", Person, GitLabPerson)

_UNREADABLE = (OperationalStoreUnavailable, sqlite3.Error, OSError, ValueError, TypeError)


class MemberDesk:
    """`dispatch.Members` over one composed daemon."""

    def __init__(self, daemon: Daemon) -> None:
        self._daemon = daemon

    # ── Reads ────────────────────────────────────────────────────────────

    def _project(self, scope: DataScope) -> ProjectScope:
        """`scope` as a project this daemon knows; a refusal by name otherwise."""
        try:
            project = ProjectScope(scope)
            self._daemon.storage.paths.resolve(scope, MEMBER_UPDATES)
        except NotAProjectScope as refused:
            raise MemberRefused(str(refused)) from None
        except ScopeResolutionError as unknown:
            raise MemberRefused(
                f"{scope} is not a project this daemon knows, so nothing was done: {unknown}"
            ) from None
        if project.project_id not in self._daemon.watched:
            raise MemberRefused(
                f"{scope} is not a project this daemon knows, so nothing was done: the "
                f"projects it watches are {', '.join(sorted(self._daemon.watched)) or 'none'}."
            )
        return project

    def _work(self, scope: DataScope) -> ProjectWork:
        try:
            return self._daemon.storage.project_work(scope)
        except _UNREADABLE as unreadable:
            raise MemberRefused(
                f"the GitLab record of {scope} could not be read, so nothing was "
                f"decided: {unreadable}"
            ) from unreadable

    def _aliases_raw(self) -> bytes:
        try:
            return self._daemon.storage.read_aliases()
        except (OSError, DecryptionFailed) as unreadable:
            raise MemberRefused(
                f"aliases.md could not be read, so nothing was written: {unreadable}"
            ) from unreadable

    def _ledger_raw(self, scope: DataScope) -> bytes:
        try:
            return self._daemon.storage.read_member_updates(scope=scope)
        except (OSError, DecryptionFailed) as unreadable:
            raise MemberRefused(
                f"{scope}'s member_updates.md could not be read: {unreadable}"
            ) from unreadable

    # ── `member name` ────────────────────────────────────────────────────

    def plan_name(
        self,
        scope: DataScope,
        label: str,
        member: str | None,
        *,
        meeting: str | None,
        remove: bool,
    ) -> NamePlan:
        """What `member name` would append; refused by name before anything is asked."""
        self._project(scope)
        if not normalise(label):
            raise MemberRefused("an empty label names no one, so nothing was written.")
        if is_generic_label(label) and meeting is None:
            raise MemberRefused(
                f"{_quoted(label)} is a generic label — every meeting has one — so it is "
                f"named only for one meeting: give `--meeting <id>`. Nothing was written."
            )
        if meeting is not None:
            try:
                self._daemon.meetings.get(meeting, scope=scope)
            except MeetingNotFound:
                raise MemberRefused(
                    f"no meeting {meeting!r} is recorded in {scope}, so nothing was written."
                ) from None
            except MalformedMeeting as unreadable:
                raise MemberRefused(
                    f"meeting {meeting}'s record in {scope} could not be read, so nothing "
                    f"was written: {unreadable}"
                ) from unreadable
        where = f" in meeting {meeting}" if meeting is not None else " in every meeting"
        raw = self._aliases_raw()
        table = parse_aliases(raw)
        current = table.row_for(label, meeting)
        work = self._work(scope)
        people = people_of(work)
        now = self._daemon.clock()
        if remove:
            # With `--meeting`, an unscoped mapping is ended for that meeting
            # alone by a removal row scoped to it; the others keep it.
            unscoped = table.row_for(label, None) if meeting is not None else None
            maps_here = (current is not None and not current.removal) or (
                current is None and unscoped is not None and not unscoped.removal
            )
            if not maps_here:
                return NamePlan(
                    said=f"{_quoted(label)} is not named as anyone{where}; nothing was written."
                )
            row = render_alias_row(AliasRow(normalise(label), label, None, meeting, now))
            return NamePlan(
                said=(
                    f"{_quoted(label)} no longer names anyone{where}; the next render shows "
                    f"its updates as unassigned, even if a member carries that exact name."
                ),
                row=row,
                after=len(raw),
            )
        if member is None:
            raise MemberRefused(
                "`member name` needs the member the label names — a handle, a username "
                "or a display name — or `--remove`. Nothing was written."
            )
        person = _member(people, member, scope, work)
        named = describe_person(person)
        if current is not None and current.handle == person.handle:
            return NamePlan(
                said=f"{_quoted(label)} already names {named}{where}; nothing was written."
            )
        replaces = None
        if current is not None and current.handle is not None:
            other = people.by_handle(current.handle)
            replaces = describe_person(other) if other is not None else str(current.handle)
        row = render_alias_row(AliasRow(normalise(label), label, person.handle, meeting, now))
        return NamePlan(
            said=(
                f"{_quoted(label)} now names {named}{where}, saved in aliases.md; the "
                f"next render attributes its updates."
            ),
            row=row,
            replaces=replaces,
            names=named,
            after=len(raw),
        )

    def save_name(self, plan: NamePlan) -> str:
        """Append the planned row, if the table has not grown since it was read.

        Under `aliases.md`'s claim, taken around the re-read and the append, so
        another process appending meanwhile is refused here, never overwritten
        by a row decided on a stale read.
        """
        if plan.row is None:
            return plan.said
        storage = self._daemon.storage
        try:
            with storage.exclusive(scope=ALIAS_SCOPE, artifact=ALIAS_TABLE):
                now = len(storage.read_aliases())
                if now != plan.after:
                    raise LedgerChanged(
                        f"aliases.md changed while you were asked ({plan.after} bytes "
                        f"then, {now} now)."
                    )
                storage.append_alias(plan.row, after=plan.after)
        except LedgerChanged as changed:
            raise MemberRefused(f"{changed} Nothing was written; run the command again.") from changed
        except ArtifactBusy as busy:
            raise MemberRefused(f"{busy} Nothing was written.") from busy
        except OSError as unwritable:
            raise MemberRefused(
                f"aliases.md could not be written, so nothing changed: {unwritable}"
            ) from unwritable
        return plan.said

    # ── `member link` ────────────────────────────────────────────────────

    def link(self, scope: DataScope, update: str, *, confirm: bool) -> str:
        """Confirm or reject one model-matched link, appended to the ledger."""
        self._project(scope)
        raw = self._ledger_raw(scope)
        ledger = parse_ledger(raw)
        line = ledger.by_update(update)
        if line is None:
            if update in ledger.known_updates:
                raise MemberRefused(
                    f"{update} was withdrawn by a later summary of its meeting, so there "
                    f"is nothing to {'confirm' if confirm else 'reject'}."
                )
            raise MemberRefused(
                f"no update {update!r} is recorded in {scope}; `pm-ai member unmatched "
                f"--scope {scope}` lists the links awaiting your word."
            )
        if line.by is LinkKind.NUMBER:
            raise MemberRefused(
                f"{update}'s issue #{line.issue} was a number said in the meeting, so it "
                f"is already certain and needs no word from you."
            )
        verdict = ledger.verdicts.get(update)
        word = "confirmed" if confirm else "rejected"
        if verdict is not None and verdict.confirmed == confirm:
            return f"{update} (#{line.issue}) is already {word}; nothing was written."
        entry = render_verdict(Verdict(update, confirm, self._daemon.clock()))
        try:
            self._daemon.storage.append_member_updates([entry], scope=scope, after=len(raw))
        except LedgerChanged as changed:
            raise MemberRefused(f"{changed} Nothing was written; run the command again.") from changed
        except ArtifactBusy as busy:
            raise MemberRefused(f"{busy} Nothing was written.") from busy
        except OSError as unwritable:
            raise MemberRefused(
                f"member_updates.md could not be written, so nothing changed: {unwritable}"
            ) from unwritable
        shown = (
            "shown as said in the meeting from the next render"
            if confirm
            else "hidden from the next render"
        )
        return f"{update} (#{line.issue}, meeting {line.meeting}) is {word}: {shown}."

    # ── `member unmatched` ───────────────────────────────────────────────

    def unmatched(self, scope: DataScope) -> str:
        """The unmatched labels, the links awaiting the PM, and lines skipped."""
        project = self._project(scope)
        ledger = parse_ledger(self._ledger_raw(scope))
        aliases = parse_aliases(self._aliases_raw())
        try:
            matches = tuple(
                state for state in self._daemon.storage.match_records() if state.scope == scope
            )
        except _UNREADABLE:
            matches = ()
        report = unmatched_report(ledger, aliases, self._work(scope), matches=matches)
        return report.text(project)


def _quoted(label: str) -> str:
    return '"' + label.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _named_by(
    people: Sequence[_Named], text: str
) -> list[_Named]:
    """Who `text` names among `people`: a handle, else a username, else a display name."""
    found: list[_Named] = []
    if text.startswith("gitlab:"):
        try:
            handle = parse_person_handle(text)
        except InvalidPersonHandle:
            handle = None
        if handle is not None:
            found = [person for person in people if person.handle == handle]
    if not found:
        key = normalise(text.removeprefix("@"))
        found = [p for p in people if p.username is not None and normalise(p.username) == key]
    if not found:
        key = normalise(text)
        found = [p for p in people if p.name is not None and normalise(p.name) == key]
    return found


def _member(people: People, member: str, scope: DataScope, work: ProjectWork) -> Person:
    """The one active member `member` names: a handle, a username, or a display name.

    The rule `resolve_label` binds by, for all three forms: an active member,
    not a bot, with a usable `gitlab` handle. Anyone else it names is refused
    by name, with the members it knows listed.
    """
    text = member.strip()
    active = [person for person in people.people if person.active]
    known = ", ".join(describe_person(person) for person in active) or "none"
    found = _named_by(active, text)
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise MemberRefused(
            f"{member!r} names more than one member of {scope}: "
            f"{', '.join(describe_person(person) for person in found)}. Name one by its "
            f"username or handle. Nothing was written."
        )
    inactive = _named_by([person for person in people.people if not person.active], text)
    if inactive:
        raise MemberRefused(
            f"{member!r} is {describe_person(inactive[0])}, who is not an active member of "
            f"{scope}, so nothing was written. The members it knows: {known}."
        )
    recorded = [m.person for m in work.members] + [p for i in work.issues for p in i.assignees]
    unusable = _named_by(
        [p for p in recorded if p.bot is True or p.handle is None], text
    )
    if unusable:
        who = unusable[0]
        why = "a bot" if who.bot is True else "a person GitLab gave no usable id for"
        name = who.name or who.username or text
        raise MemberRefused(
            f"{member!r} is {name}, {why}, so a label cannot name them. Nothing was "
            f"written. The members it knows: {known}."
        )
    raise MemberRefused(
        f"{member!r} is not a member of {scope} in its GitLab record, so nothing was "
        f"written. The members it knows: {known}."
    )
