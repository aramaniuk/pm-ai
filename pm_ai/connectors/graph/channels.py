"""Teams channel messages, walked newest first and cut by thread activity (story 33d).

`GET /teams/{team}/channels/{channel}/messages?$top=50&$expand=replies` is the
only request. pm-ai never lists teams or channels: which channels are read is
written by hand in the connector's settings file, one entry per channel.

## Two timestamps, two jobs

Microsoft returns a channel's threads newest first, fifty per page, ordered by
each thread's **latest activity** — the root message or its newest reply. A
root's own creation time says nothing about where its thread sits in the list.
So the two times do different work here:

- **Thread activity decides where the walk stops.** The walk ends after the
  first page on which *every* thread is older than the channel's position. A
  page with no threads at all decides nothing, so an empty page that still
  carries a next-page link is followed.
- **A message's own creation time decides whether it is emitted**: created at
  or after the position. A message seen twice — on the previous run, or twice
  in this one — is dropped by the natural key at persist (AD-34), and inside
  one walk by its id.

The position is the channel's stored one, or the first-run reach-back the row
already holds for the calendar.

## Where the position goes

- After a complete walk: the newest creation time seen, never earlier than the
  position the walk started from.
- After a walk the page cap or the time budget cut short: still the newest
  creation time seen. Messages older than the oldest walked were not read, and
  the failure says so by name — a stated loss, never a silent one.
- After any other failure, or a page that answered with nothing to read:
  unchanged, so the next run walks the same history again and the natural key
  drops what it already holds.

## Which rows are messages

Only a row Microsoft types `message`. A system event, a typing notice, a chat
event and any type pm-ai does not know are skipped — not emitted, not refused.
A message row with no id, no body, a body neither HTML nor text, or a creation
time missing, unparseable or without a timezone is refused by name, and the
page continues.

## What this module does not do

No event, no reference, no person handle: `GraphConnector` builds those from the
rows returned here, beside the calendar's. No sanitization (AD-12): the text is
stored as posted, and the model boundary cleans it.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any

from pm_ai.connectors.graph.auth import GraphAuthError, GraphUnreachable
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
    "EXCERPT_LENGTH",
    "ChannelFetch",
    "ChannelListing",
    "GraphChannelFetch",
    "MAX_LABEL_LENGTH",
    "MESSAGE_TYPE",
    "MessageRow",
    "MessageRowRefused",
    "instant",
    "label_problem",
    "plain_text",
    "read_message",
    "unusable_id_character",
    "usable_message_id",
]


EXCERPT_LENGTH = 280
"""How much of a message the event carries (decided 2026-10-09).

An excerpt is short. The whole message stays in Teams, where the reference
points; the excerpt is the first 280 characters of the plain text, cut and never
cleaned (AD-29).
"""

MESSAGE_TYPE = "message"
"""The one `messageType` that is a message. Every other value is skipped."""


# ── What the settings file lists ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ChannelListing:
    """One channel the PM listed by hand: where it is, and whose project it is.

    Built by `pm_ai.connectors.graph.graph_channel_listings`, which refuses a
    malformed entry, an unenrolled project and a channel listed twice before any
    connector exists.
    """

    team: str
    channel: str
    project: str
    label: str | None = None

    @property
    def name(self) -> str:
        """What the dashboard calls this channel: the label, or the channel id."""
        return self.label if self.label is not None else self.channel

    @property
    def scope(self) -> DataScope:
        """The project this channel belongs to (AD-4) — never the personal scope."""
        return DataScope(ScopeKind.PROJECT, project_id=self.project)


# ── What one walk returns ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MessageRow:
    """One channel message or reply, as far as the event needs it.

    The instants are aware UTC, converted once here. The text is the body as
    plain text; the excerpt is cut from it by the connector.
    """

    message_id: str
    created_at: datetime
    text: str
    sender_user: str | None = None
    """`from.user.id` — a Graph user id, exactly as Microsoft sent it."""
    sender_application: str | None = None
    """`from.application.id` — set when a bot posted the message."""
    mentions: tuple[str, ...] | None = None
    """The user id of each mention that names a user, in the order Microsoft
    sent them. `None` when Microsoft sent no `mentions` list at all, which is a
    different fact from `()` — a list naming nobody. A mention with no user id
    (a channel, team, tag or bot) is not here; the text still carries its words.
    """
    reply_to: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelFetch:
    """Everything one channel's walk learned, as values rather than a raise."""

    listing: ChannelListing
    rows: tuple[MessageRow, ...] = ()
    refusals: tuple[RowRefusal, ...] = ()
    failure: HarvestFailure | None = None
    answered_at: datetime | None = None
    """This machine's clock when the first page's body arrived, or `None` when
    no page did (AD-9, `8p`)."""
    finished: datetime | None = None
    """This machine's clock when the walk stopped, however it stopped."""
    position: datetime | None = None
    """Where the next run starts, or `None` for "unchanged"."""
    signed_out: bool = False
    """The credential itself failed: a later resource would fail the same way."""
    pages: int = 0
    unread_pages: int = 0
    """Pages that answered 200 with no `value` array to read. The position
    does not move past one, and the run claims no coverage over it."""
    busy: bool = False
    """A throttle or a server error stopped the walk (`33f`): the provider is
    asking pm-ai to wait, so the run reads no chat after it."""
    unread_replies: int = 0
    """Threads whose replies did not all arrive inline (`replies@odata.nextLink`).
    The run claims no coverage: the connector cannot vouch for replies it did
    not read."""


# ── HTML to text ─────────────────────────────────────────────────────────────

_BLOCKS = frozenset(
    {
        "address", "article", "aside", "blockquote", "dd", "div", "dl", "dt",
        "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
        "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
)  # fmt: skip
"""Elements whose edges are line breaks. `br` is not here: it is a break itself."""


class _Text(HTMLParser):
    """Character data, entities decoded; tags and attributes dropped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._pending_break = False

    def _boundary(self) -> None:
        # One break between two blocks, none before the first and none after
        # the last: a block boundary separates text, it is not text.
        if self.parts:
            self._pending_break = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")
            self._pending_break = False
        elif tag in _BLOCKS:
            self._boundary()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCKS:
            self._boundary()

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if self._pending_break:
            if not self.parts[-1].endswith("\n"):
                self.parts.append("\n")
            self._pending_break = False
        self.parts.append(data)


def plain_text(html: str) -> str:
    """A Teams HTML body as plain text, with the standard library alone.

    Tags and attributes are dropped and all character data kept — a mention's
    `<at id="0">Jane</at>` reads "Jane", which stays in the excerpt as text a
    person typed, untrusted. Entities are decoded. Block boundaries become line
    breaks, one between two blocks and none at either end.
    """
    parser = _Text()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)


# ── The walk ─────────────────────────────────────────────────────────────────


class _Refused(ValueError):
    """One message row this walk will not return; the page continues."""


@dataclass
class _Walk:
    """The running state of one channel's walk."""

    listing: ChannelListing
    position: datetime
    rows: list[MessageRow] = field(default_factory=list)
    refusals: list[RowRefusal] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    newest: datetime | None = None
    oldest: datetime | None = None
    unread_pages: int = 0
    unread_replies: int = 0

    def saw(self, created: datetime) -> None:
        self.newest = created if self.newest is None else max(self.newest, created)
        self.oldest = created if self.oldest is None else min(self.oldest, created)

    def refuse(self, identifier: str | None, reason: str) -> None:
        self.refusals.append(
            RowRefusal(
                identifier=(
                    f"{self.listing.channel}/{identifier}" if identifier else None
                ),
                reason=f"channel {self.listing.name!r}: {reason}",
            )
        )


@dataclass
class GraphChannelFetch:
    """Walk one listed channel newest first and return what was earned.

    Reports; never raises — exactly as `GraphCalendarFetch` does, and for the
    same reason: the messages a partial walk already read cannot travel up a
    stack as an exception. Owns no cadence, no cursor and no schedule (AD-9).
    """

    client: GraphClient
    instance: str
    reach_back: timedelta
    """How far back a channel with no stored position reaches: the row's
    first-run reach-back, the same one the calendar's first run uses."""
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def fetch(
        self,
        listing: ChannelListing,
        *,
        since: datetime | None,
        deadline: datetime,
    ) -> ChannelFetch:
        """Walk until a page is wholly older than the position, or the walk stops."""
        position = since if since is not None else self.now() - self.reach_back
        state = _Walk(listing=listing, position=position)
        answered_at: datetime | None = None
        pages = 0
        failure: HarvestFailure | None = None
        signed_out = False
        busy = False
        cut: GraphCallFailed | None = None

        url = self.client.channel_messages(listing.team, listing.channel)
        try:
            for body in self.client.walk(url, deadline=deadline):
                if answered_at is None:
                    answered_at = self.now()
                pages += 1
                if self._read(body, state):
                    break
        except (PageCapReached, FetchBudgetSpent) as stopped:
            # The sentence needs the position this walk ends at, so it is
            # written below, once that is known.
            cut = stopped
        except ConsentChanged:
            failure = HarvestFailure(
                reason=(
                    f"{self.instance} was refused channel {listing.name!r} "
                    f"(team {listing.team}, channel {listing.channel}) with 403: "
                    f"the signed-in account is not a member of this channel or "
                    f"cannot read it. Ask to be added to {listing.name!r}, or "
                    f"remove it from the channels list in this connector's "
                    f"settings file. The other resources were still read."
                ),
                retryable=False,
            )
        except GraphRefused as refused:
            failure = HarvestFailure(
                reason=(
                    f"{self.instance} could not read channel {listing.name!r}: "
                    f"Graph answered {refused.status if refused.status else 'a 4xx'} "
                    f"for team {listing.team!r}, channel {listing.channel!r} "
                    f"(project {listing.project!r}) — a mistyped team or channel "
                    f"id in the channels list. Correct the entry in this "
                    f"connector's settings file; re-sending it unchanged will be "
                    f"refused the same way."
                ),
                retryable=False,
            )
        except GraphCallFailed as refused:
            busy = isinstance(refused, (GraphThrottled, GraphUnavailable))
            failure = HarvestFailure(
                reason=(
                    f"{self.instance} could not finish channel {listing.name!r}: "
                    f"{refused.reason}"
                ),
                retryable=refused.retryable,
                retry_after=refused.retry_after,
            )
        except GraphUnreachable as silent:
            failure = HarvestFailure(
                reason=f"{self.instance} could not reach channel {listing.name!r}: {silent}",
                retryable=True,
            )
        except GraphAuthError as unauthenticated:
            signed_out = True
            failure = HarvestFailure(
                reason=(
                    f"{self.instance} could not sign in to read channel "
                    f"{listing.name!r}: {unauthenticated}"
                ),
                retryable=False,
            )
        except Exception as unexpected:  # noqa: BLE001 — a failure is a value here
            # The type and never the message, as everywhere a reason reaches
            # Tier 2: an arbitrary message can carry a URL with its query.
            failure = HarvestFailure(
                reason=(
                    f"{self.instance} could not finish channel {listing.name!r}: "
                    f"{type(unexpected).__name__} was raised while walking it, "
                    f"and pm-ai cannot classify it"
                ),
                retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
            )
        finished = self.now()

        # Never past this machine's clock: a message dated in the future is
        # emitted (AD-35 flags an implausible time, it never replaces one), but a
        # position ahead of `now` would skip every message posted until the
        # clock caught up with it.
        newest = None if state.newest is None else min(state.newest, finished)
        if cut is not None:
            moved: datetime | None = (
                max(position, newest)
                if newest is not None and newest > position
                else None
            )
            failure = self._cut_short(state, cut, moved=moved)
        elif failure is None and state.unread_pages:
            # A page asked for and not answered: walking it again next run is
            # lossless, and the natural key drops what this run already holds.
            moved = None
        elif failure is None:
            moved = position if newest is None else max(position, newest)
        else:
            moved = None

        return ChannelFetch(
            listing=listing,
            rows=tuple(state.rows),
            refusals=tuple(state.refusals),
            failure=failure,
            answered_at=answered_at,
            finished=finished,
            position=moved,
            signed_out=signed_out,
            busy=busy,
            pages=pages,
            unread_pages=state.unread_pages,
            unread_replies=state.unread_replies,
        )

    # ── One page, one thread, one message ────────────────────────────────────

    def _read(self, body: Mapping[str, Any], state: _Walk) -> bool:
        """Take one page's messages. True when every thread on it is older.

        A page with no threads is never "every thread older": an empty page that
        still carries a next-page link does not stop the walk. A thread with no
        readable activity time counts as older, so an undatable thread cannot
        force every walk to the page cap.
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
        threads = 0
        older = 0
        for raw in values:
            threads += 1
            if not isinstance(raw, Mapping):
                # Undatable, so it counts as older: a thread with no time on it
                # must not hold every walk open until the page cap.
                older += 1
                state.refuse(
                    None,
                    f"a messages page carried a {type(raw).__name__} where a "
                    f"message object belongs.",
                )
                continue
            activity = _activity(raw)
            if activity is None or activity < state.position:
                older += 1
            root = self._take(raw, state, reply_to=None)
            replies = raw.get("replies")
            if isinstance(replies, Sequence) and not isinstance(replies, (str, bytes)):
                for reply in replies:
                    self._take(reply, state, reply_to=root)
            if raw.get("replies@odata.nextLink"):
                state.unread_replies += 1
                state.refuse(
                    root,
                    f"the thread has more replies than Microsoft returns inline "
                    f"({len(replies) if isinstance(replies, Sequence) else 0} "
                    f"arrived, with a link for more). The replies that arrived "
                    f"were read; the rest were not, and this walk does not page "
                    f"replies.",
                )
        return threads > 0 and older == threads

    def _take(self, raw: Any, state: _Walk, *, reply_to: str | None) -> str | None:
        """Keep one message row, skip it, or refuse it by name. Returns its id."""
        if not isinstance(raw, Mapping):
            state.refuse(
                None, f"a thread carried a {type(raw).__name__} where a reply belongs."
            )
            return None
        identifier = usable_message_id(raw.get("id"))
        try:
            row = read_message(raw, reply_to=reply_to)
        except MessageRowRefused as refused:
            state.refuse(identifier, str(refused))
            return identifier
        if row is None:
            # Not a message, or a tombstone: skipped, neither emitted nor refused.
            return identifier
        # Only an accepted row moves the position: a refused one is read again
        # next run rather than skipped.
        state.saw(row.created_at)
        if row.created_at < state.position or row.message_id in state.seen:
            return identifier
        state.seen.add(row.message_id)
        state.rows.append(row)
        return identifier

    def _cut_short(
        self, state: _Walk, stopped: GraphCallFailed, *, moved: datetime | None
    ) -> HarvestFailure:
        """The page cap or the budget stopped the walk: a stated loss, or none.

        The loss named never reaches below the stored position: messages older
        than it were read by an earlier run. When the position does not move,
        nothing is lost — the next run walks the same history again.
        """
        why = (
            f"the {self.client.page_cap}-page cap"
            if isinstance(stopped, PageCapReached)
            else "the run's time budget"
        )
        if moved is None:
            outcome = (
                "no message was accepted, so the position did not move and the "
                "next run walks the same history again"
                if state.newest is None
                else "no message newer than the position was accepted, so the "
                "position did not move and the next run walks the same history "
                "again"
            )
        else:
            floor = (
                state.position
                if state.oldest is None
                else max(state.oldest, state.position)
            )
            skipped = (
                f"messages created at or after {state.position.isoformat()} in "
                f"threads the walk did not reach"
                if floor == state.position
                else f"messages created from {state.position.isoformat()} up to "
                f"{floor.isoformat()}, the oldest walked, in threads the walk "
                f"did not reach"
            )
            outcome = (
                f"the position advanced to {moved.isoformat()}, the newest "
                f"message seen, so {skipped} were skipped and are not read again"
            )
        return HarvestFailure(
            reason=(
                f"{self.instance} stopped walking channel {state.listing.name!r} "
                f"at {why} before reaching its position "
                f"{state.position.isoformat()}: {outcome}."
            ),
            retryable=True,
            retry_after=stopped.retry_after,
        )


# ── One message row, for every walk that reads one ───────────────────────────


class MessageRowRefused(ValueError):
    """One message row no walk will return, and why. The page continues.

    Public because the chat walk (`33f`) reads its rows through the same
    `read_message` the channel walk does: which rows are messages, which are
    refused and what a refusal says are this module's, once.
    """


def read_message(raw: Mapping[str, Any], *, reply_to: str | None) -> MessageRow | None:
    """One provider row as a `MessageRow`, or `None` when it is not a message.

    `None` for a row Microsoft does not type `message` — a system event, a
    typing notice, a chat event, a type pm-ai does not know — and for a
    tombstone: skipped, neither emitted nor refused. Raises `MessageRowRefused`
    for a message row with no usable id, no body, a body neither HTML nor
    text, or a creation time missing, unparseable or without a timezone.

    Where the row stands against a position, and whether it was seen already,
    is the calling walk's business.
    """
    raw_id = raw.get("id")
    identifier = usable_message_id(raw_id)
    if raw.get("messageType") != MESSAGE_TYPE:
        return None
    if raw.get("deletedDateTime"):
        return None
    try:
        if identifier is None:
            if isinstance(raw_id, str) and raw_id.strip():
                raise _Refused(
                    f"a message row carries the id {raw_id!r}, which holds "
                    f"whitespace, a control or format character, or one of "
                    f"`/`, `\\`, `?`, `#`. The id becomes part of the "
                    f"message's reference, so it is refused rather than "
                    f"repaired."
                )
            raise _Refused(
                "a message row carries no usable `id`, so nothing could "
                "cite it. Refused rather than emitted under an invented one."
            )
        created = _created(raw.get("createdDateTime"))
        text = _body_text(raw.get("body"))
    except _Refused as refused:
        raise MessageRowRefused(str(refused)) from None
    sender = raw.get("from")
    return MessageRow(
        message_id=identifier,
        created_at=created,
        text=text,
        sender_user=_nested_id(sender, "user"),
        sender_application=_nested_id(sender, "application"),
        mentions=_mentions(raw),
        reply_to=reply_to,
    )


# ── What a channel or chat is called ─────────────────────────────────────────

MAX_LABEL_LENGTH = 100
"""The longest name a channel or chat may be shown under, in characters.

A choice, not a measurement: a label is a display name on every message line
and on the dashboard, and a hundred characters is longer than any name a person
reads at a glance while leaving each ledger line far under `MAX_ENTRY_LENGTH`.
It bounds a hand-written label (`33d`, `33f`) and a chat topic used in its
place (`33f`).
"""


def label_problem(label: object) -> str | None:
    """Why `label` cannot name a channel or chat, or `None` when it can.

    The one rule for a hand-written label and for a chat's server topic used
    as one: text, not blank, at most `MAX_LABEL_LENGTH` characters once
    trimmed, and holding no control or format character (Unicode `Cc`, which
    includes C0 and DEL, or `Cf`) — such a character reaches every message line
    and the dashboard unseen. The phrase completes "… has a label that …".
    """
    if not isinstance(label, str) or not label.strip():
        return f"is not text ({label!r})"
    if len(label.strip()) > MAX_LABEL_LENGTH:
        return (
            f"is {len(label.strip())} characters, past the {MAX_LABEL_LENGTH} "
            f"a name may be; it is shown on every message line and on the "
            f"dashboard, so shorten it"
        )
    if any(unicodedata.category(character) in ("Cc", "Cf") for character in label):
        return (
            f"holds a control or format character ({label!r}), which would "
            f"reach every message line and the dashboard unseen; remove it"
        )
    return None


# ── Reading provider fields, each in one place ───────────────────────────────


def unusable_id_character(character: str) -> bool:
    """Whether one character may not appear in a team, channel or message id.

    Whitespace, C0 and DEL, any Unicode control or format character, and the
    four that would change a request path or split a reference: `/`, `\\`,
    `?`, `#`.
    """
    return (
        character.isspace()
        or ord(character) < 32
        or ord(character) == 127
        or unicodedata.category(character) in ("Cc", "Cf")
        or character in "/\\?#"
    )


def usable_message_id(value: Any) -> str | None:
    """A message id usable in a reference, or `None`. Shared by the chat walk."""
    if not isinstance(value, str) or not value:
        return None
    if any(unusable_id_character(character) for character in value):
        return None
    return value


def instant(value: Any) -> datetime | None:
    """An ISO instant carrying its own offset, as aware UTC, or `None`.

    Public because the chat walk (`33f`) reads `lastModifiedDateTime` with the
    same reading the channel walk makes of every provider time.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, _, tail = text.partition(".")
        span = 0
        while span < len(tail) and tail[span].isdigit():
            span += 1
        digits, remainder = tail[:span], tail[span:]
        text = f"{head}.{digits[:6]}{remainder}" if digits else head + remainder
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _created(value: Any) -> datetime:
    """A message's `createdDateTime`, or the refusal that says what was wrong."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise _Refused("the message carries no `createdDateTime`.")
    parsed = instant(value)
    if parsed is not None:
        return parsed
    if isinstance(value, str):
        text = value.strip()
        try:
            naive = datetime.fromisoformat(text.split(".")[0])
        except ValueError:
            naive = None
        if naive is not None and naive.tzinfo is None:
            raise _Refused(
                f"the message's `createdDateTime` {text!r} carries no timezone, "
                f"so the instant it names cannot be known. Refused rather than "
                f"assumed to be UTC."
            )
    raise _Refused(
        f"the message's `createdDateTime` {value!r} cannot be parsed as an "
        f"instant. Refused rather than emitted with a guessed time."
    )


def _body_text(body: Any) -> str:
    """The body as plain text: HTML converted, text taken as is."""
    if not isinstance(body, Mapping):
        raise _Refused(f"the message has no body ({type(body).__name__}).")
    kind = body.get("contentType")
    content = body.get("content")
    if not isinstance(content, str):
        raise _Refused(
            f"the message body carries no text content ({type(content).__name__})."
        )
    folded = kind.casefold() if isinstance(kind, str) else None
    if folded == "html":
        return plain_text(content)
    if folded == "text":
        return content
    raise _Refused(
        f"the message body is of type {kind!r}, which is neither HTML nor text."
    )


def _nested_id(holder: Any, key: str) -> str | None:
    """`holder[key].id` when every step of it is there."""
    if not isinstance(holder, Mapping):
        return None
    inner = holder.get(key)
    if not isinstance(inner, Mapping):
        return None
    value = inner.get("id")
    return value if isinstance(value, str) and value else None


def _mentions(raw: Mapping[str, Any]) -> tuple[str, ...] | None:
    """The user ids a message mentions, or `None` when no list was sent."""
    listed = raw.get("mentions")
    if not isinstance(listed, Sequence) or isinstance(listed, (str, bytes)):
        return None
    ids: list[str] = []
    for mention in listed:
        if not isinstance(mention, Mapping):
            continue
        user = _nested_id(mention.get("mentioned"), "user")
        if user is not None:
            ids.append(user)
    return tuple(ids)


def _activity(raw: Mapping[str, Any]) -> datetime | None:
    """A thread's latest activity: the newest time on the root or any reply.

    The list is ordered by it, so it is what the cut reads. Creation and
    modification times both count — Microsoft orders by "the last modified date
    of the entire reply chain" — and an unreadable one is left out rather than
    guessed. `None` when nothing on the thread can be read, and such a thread
    never counts as older.
    """
    times = [
        stamp
        for stamp in (
            instant(raw.get("createdDateTime")),
            instant(raw.get("lastModifiedDateTime")),
        )
        if stamp is not None
    ]
    replies = raw.get("replies")
    if isinstance(replies, Sequence) and not isinstance(replies, (str, bytes)):
        for reply in replies:
            if isinstance(reply, Mapping):
                for key in ("createdDateTime", "lastModifiedDateTime"):
                    stamp = instant(reply.get(key))
                    if stamp is not None:
                        times.append(stamp)
    return max(times) if times else None
