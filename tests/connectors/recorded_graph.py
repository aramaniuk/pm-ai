"""Shared by the tests over the recorded Graph pages (`fixtures/graph/*_recorded.*`).

A scripted transport, a token source, and readings of a recorded page made
without pm-ai's code — a timestamp parser and the mention list — so a test
compares pm-ai's reading with an independent one rather than with itself.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pm_ai.connectors.graph.client import GraphRequest, GraphResponse
from pm_ai.domain.events import ObservedEventType
from pm_ai.domain.health import Health, Probe

FIXTURES = Path(__file__).parent / "fixtures" / "graph"
INSTANCE = "graph:work"


def recorded(name: str) -> dict:
    """One recorded JSON fixture, `_provenance` and all."""
    loaded: dict = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return loaded


# ── Independent readings ─────────────────────────────────────────────────────

_STAMP = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})T(?P<clock>\d{2}:\d{2}:\d{2})(?:\.(?P<fraction>\d+))?Z"
)


def graph_time(stamp: str) -> datetime:
    """A Graph UTC instant (`…T08:33:21.243328Z`), read without pm-ai's parser.

    Any number of fractional digits; the first six are microseconds, the rest
    dropped (truncated, as pm-ai documents). Raises on anything else.
    """
    found = _STAMP.fullmatch(stamp)
    if found is None:
        raise ValueError(f"not a Graph UTC instant: {stamp!r}")
    fraction = (found["fraction"] or "").ljust(6, "0")[:6]
    year, month, day = (int(part) for part in found["date"].split("-"))
    hour, minute, second = (int(part) for part in found["clock"].split(":"))
    return datetime(year, month, day, hour, minute, second, int(fraction), tzinfo=timezone.utc)


def fraction_digits(stamp: str) -> int:
    found = _STAMP.fullmatch(stamp)
    if found is None:
        raise ValueError(f"not a Graph UTC instant: {stamp!r}")
    return len(found["fraction"] or "")


def date_times(node: Any) -> Iterator[str]:
    """Every string under a key ending in `DateTime`, anywhere in a document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key.endswith("DateTime") and isinstance(value, str):
                yield value
            else:
                yield from date_times(value)
    elif isinstance(node, list):
        for value in node:
            yield from date_times(value)


def mentioned_users(row: dict) -> list[str]:
    """The user ids a row's `mentions[]` names, first appearance first, each once."""
    ids: list[str] = []
    for mention in row.get("mentions") or []:
        user = (mention.get("mentioned") or {}).get("user") or {}
        if user.get("id") and user["id"] not in ids:
            ids.append(user["id"])
    return ids


# ── The provider ─────────────────────────────────────────────────────────────


def ok(body: dict) -> GraphResponse:
    return GraphResponse(status=200, body=body, headers={"Content-Type": "application/json"})


class Recorded:
    """The calendar answered empty; every other request answered by the next
    scripted page, and by an empty last page once they run out — so a recorded
    page's own `@odata.nextLink` is followed and answered."""

    def __init__(self, *pages: dict) -> None:
        self.pages = [copy.deepcopy(page) for page in pages]
        self.requests: list[GraphRequest] = []

    def __call__(self, request: GraphRequest) -> GraphResponse:
        self.requests.append(request)
        if "/me/calendarView" in request.url or not self.pages:
            return ok({"value": []})
        return ok(self.pages.pop(0))

    def resource_requests(self) -> list[str]:
        """Every request but the calendar's, in order."""
        return [r.url for r in self.requests if "/me/calendarView" not in r.url]


class Auth:
    instance = INSTANCE

    def access_token(self, *, force_refresh: bool = False) -> str:
        return "token-1"

    def check_health(self) -> Probe:
        return Probe(INSTANCE, Health.OK, "graph answered")


def message_events(result: Any) -> dict[str, Any]:
    """The run's `message_posted` events, keyed by message id."""
    return {
        event.source_ref.native_id.rsplit("/", 1)[1]: event
        for event in result.events
        if event.type is ObservedEventType.MESSAGE_POSTED
    }


def tag_names(html: str) -> set[str]:
    """The element names a body's markup uses, opening or closing."""
    return {name.lower() for name in re.findall(r"</?\s*([A-Za-z][A-Za-z0-9]*)", html)}


def tags_left(text: str, names: set[str]) -> list[str]:
    """Any of `names` still written as a tag in `text`."""
    return [name for name in names if re.search(rf"</?\s*{name}\b", text, re.IGNORECASE)]


# ── From a harvest to the dashboard (`23f`) ──────────────────────────────────


class _NoKey:
    """A keychain with nothing in it: a dashboard render needs no key."""

    def get(self, name: str) -> bytes:
        from pm_ai.ports import KeyNotFound

        raise KeyNotFound(f"nothing is stored under {name!r}")

    def set(self, name: str, secret: bytes) -> None:
        raise AssertionError("a dashboard render must never enrol a key")

    def delete(self, name: str) -> None:
        raise AssertionError("a dashboard render must never delete a key")


def harvest_then_dashboard(
    root: Path, events: Any, *, scope: Any, pm_user_id: str, now: datetime
) -> tuple[str, str]:
    """Persist a harvest's events and run `pm-ai dashboard` over them.

    The real single writer and the real render (`run_dashboard`), on a machine
    whose one Graph connector row records `pm_user_id` as the PM's, with no
    calendar. Returns the dashboard's text and the scope's event-log text.
    """
    from pm_ai.app.pipelines import run_dashboard
    from pm_ai.app.wiring import build
    from pm_ai.core.config import Config
    from pm_ai.domain.identity import DataScope, ScopeKind

    built = build(
        root, "alpha", now=lambda: now, keychain=_NoKey(), config=Config(display_timezone="UTC")
    )
    built.connectors = {}
    built.storage.write_artifact(
        json.dumps(
            {"instance": INSTANCE, "system": "graph", "enabled": True, "user_id": pm_user_id}
        ).encode("utf-8"),
        scope=DataScope(ScopeKind.APPLICATION),
        artifact="connectors/",
        name=f"{INSTANCE}.json",
    )
    built.storage.persist_events(tuple(events), scope=scope)
    dashboard = run_dashboard(built, scope=scope, now=now).read_text(encoding="utf-8")
    log = "".join(
        path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("event_log/*.md"))
    )
    return dashboard, log


def signal_lines(dashboard: str) -> list[str]:
    """The Proactive Enablement section's signal lines, in rendered order."""
    _, _, after = dashboard.partition("## Proactive Enablement")
    body = after.split("\n## ", 1)[0]
    return [line for line in body.split("\n") if line.startswith("- **")]
