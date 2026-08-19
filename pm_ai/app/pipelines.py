"""The ingestion pipeline: harvest → sanitize → normalize → persist.

Lives in `app` because it must touch a connector, the core, and storage — which
no other layer is permitted to do (AD-30).
"""

from __future__ import annotations

from pm_ai.app.wiring import Daemon
from pm_ai.core.sanitize import sanitize
from pm_ai.domain.harvest import PersistResult


def run_harvest(daemon: Daemon, instance: str) -> PersistResult:
    connector = daemon.connectors[instance]
    cursor = daemon.storage.load_cursor(instance)  # scheduler owns the cursor (AD-9)

    result = connector.harvest(cursor)

    # AD-12 — sanitization at the boundary, uniformly, outside the connector.
    for event in result.events:
        sanitize(getattr(event.payload, "message", "") or "")

    persisted = daemon.storage.persist_events(result.events, scope=daemon.scope)
    daemon.storage.save_cursor(instance, result.cursor, result.coverage)  # AD-35
    return persisted
