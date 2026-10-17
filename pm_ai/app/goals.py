"""`strategic_goals.md`, read, set and deleted through the single reader and writer.

`core.goal_register` parses and renders bytes and opens nothing, so the reads,
the writes and the event-log entries are made here, in the composition root
(stories 22b and 4t). Its own module rather than `entry`'s so the daemon's line
runner (`prompts`) can hand a fresh reader to every line without importing the
module that starts the daemon.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from pm_ai.core.goal_register import ARTIFACT as GOALS_ARTIFACT
from pm_ai.core.goal_register import (
    GOALS_SCOPE,
    GoalChanged,
    GoalDeletion,
    GoalRegister,
    GoalUnrecorded,
    MalformedGoals,
    delete_goal,
    parse_goals,
    set_goal,
)
from pm_ai.domain.event_entries import (
    DAEMON_ACTOR,
    EventEntry,
    MalformedEntry,
    SelfActionType,
    render_entry,
)
from pm_ai.domain.goals import Goal
from pm_ai.ports import ArtifactBusy
from pm_ai.storage.service import StorageService

__all__ = ["GoalBook", "GoalChanged", "GoalDeleted", "GoalReader", "GoalWritten"]


@dataclass(frozen=True, slots=True)
class GoalWritten:
    """What one `GoalBook.set` did, read off the file it merged onto.

    `revised` comes from the fresh read `set` makes, not from anything a
    surface read before its prompts — a goal someone else set meanwhile is a
    revision, and saying "set" over it would be false. `path` is `None` exactly
    when `changed` is false: nothing was written, so there is no write to name.
    """

    path: Path | None
    revised: bool
    changed: bool


# How long the writer's own stamps make a line, measured with stand-ins of the
# same length: `evt_` and twenty hex digits, and a microsecond UTC isoformat.
_PROBE_ID = "evt_" + "0" * 20
_PROBE_AT = "0000-00-00T00:00:00.000000+00:00"


class GoalReader:
    """`strategic_goals.md`, read through the single reader and nothing else.

    What `goal list` and `goal show` are handed (story 4t): at the command line
    over a writer that knows no project, built before anything is composed, so
    a broken `config.toml` or an empty keychain does not stop them; in the
    daemon over its own storage, built for each line, so every read is fresh
    (AD-7). The file is not sealed, so no key is asked for.
    """

    def __init__(self, storage: StorageService) -> None:
        self._storage = storage

    @property
    def path(self) -> Path:
        """Where the file lives, whether or not it exists yet."""
        return self._storage.paths.resolve(GOALS_SCOPE, GOALS_ARTIFACT)

    def read(self) -> GoalRegister:
        """The register as the file says now; `MalformedGoals` if it cannot say."""
        return parse_goals(self._raw(), scope=GOALS_SCOPE)

    def _raw(self) -> bytes | None:
        return self._storage.read_artifact(scope=GOALS_SCOPE, artifact=GOALS_ARTIFACT)


@dataclass(frozen=True, slots=True)
class GoalDeleted:
    """What one `GoalBook.delete` did."""

    path: Path
    goal: Goal
    removed: tuple[str, ...]


class GoalBook(GoalReader):
    """`strategic_goals.md`, read, set and deleted through the single reader and writer.

    The CLI names this shape structurally (`dispatch.GoalBook`) because
    `surfaces` may not reach storage (AD-30). `core` renders and opens nothing,
    so the read, the write and the event-log entry are all made here — and
    Telegram, the second channel, will hold one of these with its own `channel`.
    """

    def __init__(self, storage: StorageService, *, channel: str) -> None:
        super().__init__(storage)
        self._channel = channel

    def set(self, goal: Goal) -> GoalWritten:
        """Merge `goal` into the file, write it, then record the act (CAP-10).

        Everything that can refuse runs before the write: the file is parsed and
        merged (`set_goal` refuses a file the parser refuses, so a hand-broken
        `strategic_goals.md` stays byte-identical), and the entry is built and
        rendered at full length, so a title too long for one ledger line is a
        `MalformedGoals` with nothing written rather than a `MalformedEntry`
        after the file already changed.

        A goal identical to the one on file is a no-op: no write, no entry. Only
        the append can fail after the write, and that is `GoalUnrecorded`, which
        says the file changed and the log did not.
        """
        raw = self._raw()
        current = parse_goals(raw, scope=GOALS_SCOPE).get(goal.goal_id)
        if current == goal:
            return GoalWritten(path=None, revised=True, changed=False)
        payload = set_goal(raw, goal)
        entry = self._entry(SelfActionType.GOAL_SET, goal, goal.title)
        _assert_recordable(entry)
        path = self._storage.write_artifact(payload, scope=GOALS_SCOPE, artifact=GOALS_ARTIFACT)
        try:
            self._storage.append_event_log(entry, scope=GOALS_SCOPE)
        except (OSError, ArtifactBusy, ValueError) as unrecorded:
            raise GoalUnrecorded(
                f"{goal.goal_id} WAS written to {path}, but its goal_set entry "
                f"could not be appended to the personal event log, so the log "
                f"does not record this change: {unrecorded}"
            ) from unrecorded
        return GoalWritten(path=path, revised=current is not None, changed=True)

    def preview(self, goal_id: str) -> GoalDeletion:
        """What deleting `goal_id` would remove, from the file as it is now.

        Writes nothing. `GoalAbsent` for an id not on file, `MalformedGoals`
        for a file the parser refuses or a goal nested in the block.
        """
        return delete_goal(self._raw(), goal_id)

    def delete(self, goal_id: str, shown: Sequence[str] | None) -> GoalDeleted:
        """Remove `goal_id` and its detail lines, write, then record (CAP-10).

        The file is read again here, so `shown` — the lines a surface printed
        before it asked — is compared with what would be removed now, and a
        difference is `GoalChanged` with nothing written. `None` is a delete
        nobody was shown (`--yes`). The entry carries the goal as it read
        before the delete, its title cut with `…` when it is too long for one
        ledger line, and is checked before the write; only the append can fail
        after it, and that is `GoalUnrecorded`.
        """
        deletion = delete_goal(self._raw(), goal_id)
        if shown is not None and deletion.removed != tuple(shown):
            raise GoalChanged(
                f"{goal_id}'s lines in {GOALS_ARTIFACT} changed after they were "
                f"shown, so nothing was deleted. Run `pm-ai goal delete "
                f"{goal_id}` again to see what it would remove now."
            )
        goal = deletion.goal
        entry = self._entry(SelfActionType.GOAL_DELETED, goal, self._fitting_title(goal))
        _assert_recordable(entry)
        path = self._storage.write_artifact(
            deletion.payload, scope=GOALS_SCOPE, artifact=GOALS_ARTIFACT
        )
        try:
            self._storage.append_event_log(entry, scope=GOALS_SCOPE)
        except (OSError, ArtifactBusy, ValueError) as unrecorded:
            raise GoalUnrecorded(
                f"{goal_id} WAS deleted from {path}, but its goal_deleted entry "
                f"could not be appended to the personal event log, so the log "
                f"does not record this change: {unrecorded}"
            ) from unrecorded
        return GoalDeleted(path=path, goal=goal, removed=deletion.removed)

    def _fitting_title(self, goal: Goal) -> str:
        """`goal.title`, or its longest prefix with `…` that one ledger line holds.

        A hand-edited file can hold a title longer than an entry may be, and a
        goal that could not be deleted because of its own title would have to be
        deleted by hand. The longest prefix is found by halving, against the
        writer's own check, so the cut is exactly as short as it must be. Only
        an id so long that `…` alone does not fit is refused, by its own
        sentence: the id is what the entry exists to name, so it is never cut.
        """

        def fits(title: str) -> bool:
            try:
                _assert_recordable(self._entry(SelfActionType.GOAL_DELETED, goal, title))
            except MalformedGoals:
                return False
            return True

        if fits(goal.title):
            return goal.title
        if not fits(_CUT):
            raise MalformedGoals(
                f"the id of the goal on {GOALS_ARTIFACT}'s line for it is "
                f"{len(goal.goal_id)} characters, too long to record in the event "
                f"log even with its title cut to `{_CUT}`, so nothing was deleted. "
                f"Delete its line from {GOALS_ARTIFACT} by hand."
            )
        low, high = 0, len(goal.title)
        while low < high:
            middle = (low + high + 1) // 2
            if fits(goal.title[:middle].rstrip() + _CUT):
                low = middle
            else:
                high = middle - 1
        return goal.title[:low].rstrip() + _CUT

    def _entry(self, category: SelfActionType, goal: Goal, title: str) -> EventEntry:
        return EventEntry(
            category=category,
            actor=DAEMON_ACTOR,
            fields=(
                ("goal_id", goal.goal_id),
                ("domain", goal.domain.value),
                ("horizon", goal.horizon.value),
                ("title", title),
                ("channel", self._channel),
            ),
        )


_CUT = "…"


def _assert_recordable(entry: EventEntry) -> None:
    """Refuse, before any write, an entry the ledger writer would refuse after it."""
    stamped = replace(
        entry,
        entry_id=_PROBE_ID,
        fields=(("ingested_at", _PROBE_AT), *entry.fields),
    )
    try:
        # `render_entry` is the writer's own check, `MAX_ENTRY_LENGTH` included,
        # so this refuses exactly what `append_event_log` would.
        render_entry(stamped)
    except MalformedEntry as refused:
        raise MalformedGoals(
            f"this goal cannot be recorded in the event log, so nothing was "
            f"written: {refused}"
        ) from refused
