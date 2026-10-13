"""Teams chats the PM listed by hand, each read from its position (story 33f).

`GET /chats/{chat}/messages?$top=50&$orderby=lastModifiedDateTime desc&$filter=
lastModifiedDateTime gt <position>` is the walk. pm-ai never lists chats and
never previews their last messages: which chats are read is written by hand in
the connector's settings file, one entry per chat, each with the scope its
messages go to.

## The server filters, the client still cuts

Unlike a channel, a chat can be asked for only what changed after a position.
Microsoft honours the filter on `lastModifiedDateTime` only beside an
`$orderby` on the same field, so both are sent, newest first. The filter is on
the *modification* time, so an old message edited since comes back too; the
client therefore cuts on the *creation* time, exactly as the channel walk does:
a message is emitted when it was created at or after the position, and one
seen before is dropped by the natural key at persist (AD-34), inside one walk
by its id.

**When the walk stops early.** A page on which every row was modified before
the position means the server ignored the filter. If it still honoured the
sort, every later page is older again, so the walk ends there. It ends there
only while the rows read so far really arrived newest modification first: a
server that ignored `$orderby` too can put a message modified after the
position on any later page, so the walk then pages on to the cap rather than
guess. A row with no readable time counts as older and is left out of the
order check.

The position is the chat's stored one — never later than this machine's clock,
so a position stamped by a clock that ran ahead is pulled back to now before
the filter is built — or the first-run reach-back the row already holds for
the calendar and the channels.

## Where the position goes

- After a complete walk: the newest creation time seen, never earlier than the
  position the walk started from — so a chat that yielded nothing keeps it.
- After a walk the page cap or the time budget cut short: still the newest
  creation time seen — the channel walk's rule, carried over — and the failure
  says how many pages were read and that older messages were not.
- After any other failure, or a page that answered with nothing to read:
  unchanged.

A throttle, a server error, an unreachable token endpoint or a spent budget
also stops the run's remaining chats (`ChatFetch.stops_run`): every later chat
would meet the same answer, so they are named as not attempted, with the
provider's hint where it gave one.

## What the payload calls a chat

The label the PM gave it; without one, the chat's topic, when it is not blank
and passes the label rules (`channels.label_problem`); otherwise one of four
fixed words from the chat's kind. Topic and kind are asked for — one
`GET /chats/{chat}` — only for a chat listed without a label.

## What this module does not do

No event, no reference, no person handle: `GraphConnector` builds those from
the rows returned here with the channel slice's mapping. Which rows are
messages, and which are refused, is `channels.read_message`'s. No
sanitization (AD-12).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from pm_ai.connectors.graph.auth import GraphAuthError, GraphUnreachable
from pm_ai.connectors.graph.channels import (
    MessageRow,
    MessageRowRefused,
    instant,
    label_problem,
    read_message,
    usable_message_id,
)
from pm_ai.connectors.graph.client import (
    ConsentChanged,
    FetchBudgetSpent,
    GraphCallFailed,
    GraphClient,
    GraphRefused,
    GraphThrottled,
    GraphUnavailable,
    PageCapReached,
)
from pm_ai.domain.harvest import (
    UNCLASSIFIED_FAULT_IS_RETRYABLE,
    HarvestFailure,
    RowRefusal,
)
from pm_ai.domain.identity import DataScope, ScopeKind

__all__ = [
    "ANY_OTHER_CHAT",
    "CHAT_KIND_WORDS",
    "FILTER_SLACK",
    "MAX_CHAT_ID_LENGTH",
    "ChatFetch",
    "ChatListing",
    "GraphChatFetch",
    "chat_name",
]


CHAT_KIND_WORDS: Mapping[str, str] = {
    "oneonone": "1:1 chat",
    "group": "group chat",
    "meeting": "meeting chat",
}
"""What a chat with no label and no usable topic is called, by its `chatType`.

Keyed by the casefolded `chatType` Microsoft sends (`oneOnOne`, `group`,
`meeting`). Any other value — `unknownFutureValue`, a type added later, none at
all — is `ANY_OTHER_CHAT`.
"""

ANY_OTHER_CHAT = "chat"

MAX_CHAT_ID_LENGTH = 200
"""The longest chat id a settings row may list, in characters.

A choice, not a measurement: the chat ids Microsoft documents run to about a
hundred characters (`19:<user id>_<user id>@unq.gbl.spaces`), and the id is
part of a request path and of every message's reference, so a longer one is a
paste gone wrong rather than a chat.
"""


def chat_name(label: str | None, topic: object, kind: object) -> str:
    """The label; else the topic when it passes the label rules; else the kind's word.

    A topic is server text the PM may never have seen, so it is held to the
    same rules as a hand-written label (`label_problem`); one that fails them
    — too long, or carrying a control or format character — falls back to the
    kind's word rather than reaching every message line.
    """
    if label is not None:
        return label
    if isinstance(topic, str) and topic.strip() and label_problem(topic) is None:
        return topic.strip()
    folded = kind.casefold() if isinstance(kind, str) else None
    return CHAT_KIND_WORDS.get(folded, ANY_OTHER_CHAT) if folded else ANY_OTHER_CHAT


# ── What the settings file lists ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ChatListing:
    """One chat the PM listed by hand, and the scope its messages go to.

    Built by `pm_ai.connectors.graph.graph_chat_listings`, which refuses a
    malformed entry, a people scope, an unenrolled project and a chat listed
    twice before any connector exists. The scope is personal or a project
    (AD-4); a meeting chat is filed by it too, never joined to its meeting.
    """

    chat: str
    scope: DataScope
    label: str | None = None

    def __post_init__(self) -> None:
        if self.scope.kind not in (ScopeKind.PERSONAL, ScopeKind.PROJECT):
            raise ValueError(
                f"a listed chat is filed personally or in a project, not in "
                f"{self.scope}"
            )

    @property
    def name(self) -> str:
        """What a failure calls this chat: the label, or the chat id."""
        return self.label if self.label is not None else self.chat

    @property
    def reference_scope(self) -> str:
        """The scope segment of each message's reference (AD-34).

        `personal`, or the project's id — the same spelling a channel
        message's reference uses. The two cannot collide because a chat list
        naming a project called `personal`, in any case, is refused.
        """
        if self.scope.project_id is not None:
            return self.scope.project_id
        return self.scope.kind.value


# ── What one walk returns ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ChatFetch:
    """Everything one chat's walk learned, as values rather than a raise."""

    listing: ChatListing
    name: str | None = None
    """What each message's payload calls this chat, or `None` when the walk
    never learned it (its `GET /chats/{chat}` did not answer)."""
    rows: tuple[MessageRow, ...] = ()
    refusals: tuple[RowRefusal, ...] = ()
    failure: HarvestFailure | None = None
    answered_at: datetime | None = None
    """This machine's clock when the first body arrived, or `None` (AD-9)."""
    finished: datetime | None = None
    position: datetime | None = None
    """Where the next run starts, or `None` for "unchanged"."""
    signed_out: bool = False
    """The credential itself failed: every later resource would fail the same way."""
    stops_run: bool = False
    """A throttle, a server error, an unreachable token endpoint or a spent
    budget: the run's remaining chats are skipped and named."""
    pages: int = 0
    unread_pages: int = 0


# ── The walk ─────────────────────────────────────────────────────────────────


@dataclass
class _Walk:
    listing: ChatListing
    position: datetime
    rows: list[MessageRow] = field(default_factory=list)
    refusals: list[RowRefusal] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    newest: datetime | None = None
    unread_pages: int = 0
    last_modified: datetime | None = None
    """The modification time of the last datable row read, for the order check."""
    in_order: bool = True
    """Whether every datable row so far arrived newest modification first."""

    def saw(self, created: datetime) -> None:
        self.newest = created if self.newest is None else max(self.newest, created)

    def ordered(self, modified: datetime) -> None:
        if self.last_modified is not None and modified > self.last_modified:
            self.in_order = False
        self.last_modified = modified

    def refuse(self, identifier: str | None, reason: str) -> None:
        self.refusals.append(
            RowRefusal(
                identifier=f"{self.listing.chat}/{identifier}" if identifier else None,
                reason=f"chat {self.listing.name!r}: {reason}",
            )
        )


class _NotAnObject(ValueError):
    """The chat's own object came back as something other than an object."""


FILTER_SLACK = timedelta(milliseconds=1)
"""How far before the position the server filter is set.

The filter is `gt` and Graph's times are to the millisecond, so a message
modified exactly at the position would be filtered out — and a message created
exactly at the position is one the client cut emits. One millisecond earlier
makes the filter "at or after", which is what the client cut reads.
"""


@dataclass
class GraphChatFetch:
    """Walk one listed chat from its position and return what was earned.

    Reports; never raises, as `GraphChannelFetch` does. Owns no cadence, no
    cursor and no schedule (AD-9).
    """

    client: GraphClient
    instance: str
    reach_back: timedelta
    """How far back a chat with no stored position reaches: the row's
    first-run reach-back, the one the calendar and the channels use."""
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def fetch(
        self,
        listing: ChatListing,
        *,
        since: datetime | None,
        deadline: datetime,
    ) -> ChatFetch:
        """The chat's name when unlabelled, then its messages after the position."""
        started = self.now()
        position = since if since is not None else started - self.reach_back
        # A stored position ahead of this machine's clock would filter out
        # every message posted until the clock caught up with it.
        position = min(position, started)
        state = _Walk(listing=listing, position=position)
        name = listing.label
        answered_at: datetime | None = None
        pages = 0
        failure: HarvestFailure | None = None
        signed_out = False
        stops_run = False
        cut: GraphCallFailed | None = None

        try:
            if name is None:
                about = self.client.page(
                    self.client.chat(listing.chat), deadline=deadline
                )
                answered_at = self.now()
                if not isinstance(about, Mapping):
                    raise _NotAnObject(type(about).__name__)
                name = chat_name(None, about.get("topic"), about.get("chatType"))
            url = self.client.chat_messages(listing.chat, after=position - FILTER_SLACK)
            for body in self.client.walk(url, deadline=deadline):
                if answered_at is None:
                    answered_at = self.now()
                pages += 1
                if self._read(body, state):
                    break
        except (PageCapReached, FetchBudgetSpent) as stopped:
            cut = stopped
            # A spent budget is spent for every later chat too.
            stops_run = isinstance(stopped, FetchBudgetSpent)
        except _NotAnObject as kind:
            failure = HarvestFailure(
                reason=(
                    f"{self.instance} could not read chat {listing.name!r}: "
                    f"Graph answered the lookup of its topic and kind with a "
                    f"{kind}, not an object. Nothing was read from it and its "
                    f"position did not move."
                ),
                retryable=False,
            )
        except ConsentChanged:
            failure = HarvestFailure(
                reason=(
                    f"{self.instance} was refused chat {listing.name!r} "
                    f"(chat {listing.chat}, filed in {listing.scope}) with 403: "
                    f"either the signed-in account is not a member of this "
                    f"chat, or the chat-read consent (Chat.Read) was withdrawn "
                    f"from the app registration. If the account left the chat, "
                    f"remove it from the chats list in this connector's settings "
                    f"file; if the consent was withdrawn, grant it again. The "
                    f"other resources were still read."
                ),
                retryable=False,
            )
        except GraphRefused as refused:
            failure = HarvestFailure(
                reason=self._refused(listing, refused),
                retryable=False,
            )
        except (GraphThrottled, GraphUnavailable) as busy:
            # The provider is busy, not this chat wrong: every later chat would
            # meet the same answer, so they wait for the run the hint names.
            stops_run = True
            failure = HarvestFailure(
                reason=(
                    f"{self.instance} could not finish chat {listing.name!r}: "
                    f"{busy.reason} The messages already read are kept and the "
                    f"chat's position did not move"
                ),
                retryable=True,
                retry_after=busy.retry_after,
            )
        except GraphCallFailed as refused:
            failure = HarvestFailure(
                reason=(
                    f"{self.instance} could not finish chat {listing.name!r}: "
                    f"{refused.reason}"
                ),
                retryable=refused.retryable,
                retry_after=refused.retry_after,
            )
        except GraphUnreachable as silent:
            # The token endpoint did not answer: every later chat asks it too.
            stops_run = True
            failure = HarvestFailure(
                reason=(
                    f"{self.instance} could not reach chat {listing.name!r}: {silent}"
                ),
                retryable=True,
            )
        except GraphAuthError as unauthenticated:
            signed_out = True
            failure = HarvestFailure(
                reason=(
                    f"{self.instance} could not sign in to read chat "
                    f"{listing.name!r}: {unauthenticated}"
                ),
                retryable=False,
            )
        except Exception as unexpected:  # noqa: BLE001 — a failure is a value here
            # The type and never the message: a reason reaches Tier 2.
            failure = HarvestFailure(
                reason=(
                    f"{self.instance} could not finish chat {listing.name!r}: "
                    f"{type(unexpected).__name__} was raised while walking it, "
                    f"and pm-ai cannot classify it"
                ),
                retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
            )
        finished = self.now()

        # Never past this machine's clock, as the channel walk.
        newest = None if state.newest is None else min(state.newest, finished)
        if cut is not None:
            moved: datetime | None = (
                max(position, newest)
                if newest is not None and newest > position
                else None
            )
            failure = self._cut_short(state, cut, moved=moved, pages=pages)
        elif failure is None and state.unread_pages:
            moved = None
        elif failure is None:
            moved = position if newest is None else max(position, newest)
        else:
            moved = None

        return ChatFetch(
            listing=listing,
            name=name,
            rows=tuple(state.rows),
            refusals=tuple(state.refusals),
            failure=failure,
            answered_at=answered_at,
            finished=finished,
            position=moved,
            signed_out=signed_out,
            stops_run=stops_run,
            pages=pages,
            unread_pages=state.unread_pages,
        )

    def _refused(self, listing: ChatListing, refused: GraphRefused) -> str:
        """A 4xx on this chat, worded by status: a missing chat, or a bad request."""
        if refused.status == 404:
            return (
                f"{self.instance} could not read chat {listing.name!r}: Graph "
                f"answered 404 for chat {listing.chat!r} (filed in "
                f"{listing.scope}) — the chat id in the chats list is likely "
                f"mistyped, or the chat no longer exists. Correct the entry in "
                f"this connector's settings file; re-sending it unchanged will "
                f"be refused the same way."
            )
        codes = f" (error code {', '.join(refused.codes)})" if refused.codes else ""
        status = refused.status if refused.status else "a 4xx"
        return (
            f"{self.instance} could not read chat {listing.name!r}: Graph would "
            f"not take the request pm-ai sent for chat {listing.chat!r} (filed "
            f"in {listing.scope}) and answered {status}{codes}. Re-sending it "
            f"unchanged will be refused the same way; if the chat id in the "
            f"chats list is right, this is a request pm-ai builds wrongly — "
            f"report it."
        )

    def _read(self, body: Mapping[str, Any], state: _Walk) -> bool:
        """Take one page's messages. True when the walk may stop here.

        It may stop when every row on the page was modified before the position
        — a row the filter should have kept out — and every datable row so far
        arrived newest modification first (see the module docstring). A page
        with no rows never stops the walk; a row with no readable time counts as
        older, so an undatable row cannot hold every walk open until the cap.
        """
        values = body.get("value")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            state.unread_pages += 1
            state.refuse(
                None,
                f"a messages page carried no `value` array "
                f"({type(values).__name__}). Nothing was taken from it; the walk "
                f"continues and the position does not move past it.",
            )
            return False
        rows = 0
        older = 0
        for raw in values:
            rows += 1
            if not isinstance(raw, Mapping):
                older += 1
                state.refuse(
                    None,
                    f"a messages page carried a {type(raw).__name__} where a "
                    f"message object belongs.",
                )
                continue
            modified = instant(raw.get("lastModifiedDateTime")) or instant(
                raw.get("createdDateTime")
            )
            if modified is None:
                older += 1
            else:
                state.ordered(modified)
                if modified < state.position:
                    older += 1
            self._take(raw, state)
        return rows > 0 and older == rows and state.in_order

    def _take(self, raw: Mapping[str, Any], state: _Walk) -> None:
        """Keep one message row, skip it, or refuse it by name."""
        try:
            row = read_message(raw, reply_to=None)
        except MessageRowRefused as refused:
            state.refuse(usable_message_id(raw.get("id")), str(refused))
            return
        if row is None:
            return
        state.saw(row.created_at)
        if row.created_at < state.position or row.message_id in state.seen:
            return
        state.seen.add(row.message_id)
        state.rows.append(row)

    def _cut_short(
        self,
        state: _Walk,
        stopped: GraphCallFailed,
        *,
        moved: datetime | None,
        pages: int,
    ) -> HarvestFailure:
        """The page cap or the budget stopped the walk: what is known, plainly.

        The walk is in modification order, so it establishes no range of
        creation times it did not reach; the sentence says how far it got and
        that older messages were not read, never a creation-time span.
        """
        why = (
            f"the {self.client.page_cap}-page cap"
            if isinstance(stopped, PageCapReached)
            else "the run's time budget"
        )
        if moved is None:
            outcome = (
                "messages modified earlier than the last one read were not "
                "read; no message created after the position was accepted, so "
                "the position did not move and the next run asks from the same "
                "position again"
            )
        else:
            outcome = (
                f"messages modified earlier than the last one read were not "
                f"read; the position advanced to {moved.isoformat()}, the newest "
                f"message seen, so a message created before it that the walk "
                f"did not reach is not read again"
            )
        return HarvestFailure(
            reason=(
                f"{self.instance} stopped walking chat {state.listing.name!r} at "
                f"{why} after {pages} page(s), before reaching its position "
                f"{state.position.isoformat()}: {outcome}."
            ),
            retryable=True,
            retry_after=stopped.retry_after,
        )
