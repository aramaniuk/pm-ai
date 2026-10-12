"""Row keys shared by the connector that writes them and the core that reads them.

`pm_ai.connectors` does not import `pm_ai.core`, and `core` may not import
`connectors`, so a key both need is spelled here, in `pm_ai.domain`, which both
may import (the same arrangement as `pm_ai.domain.event_entries`).
"""

from __future__ import annotations

__all__ = ["USER_ID_KEY"]

USER_ID_KEY = "user_id"
"""The row key naming which provider user a connector signed in as (story 8q).

For a Graph row it is the signed-in Microsoft user's Graph user id — the id
token's `oid`, the value Graph returns as a user's `id`. Not a secret, and not
reserved: `connector add graph` hands it in with its other settings, and
`pm_ai.core.connector_enrolment.record_user_id` is the one writer that changes
it afterwards. A row without a usable value has no recorded user; such a row
still builds. What counts as usable is one rule, `pm_ai.domain.handles.usable_user_id`
(story 2o), which `recorded_user_id` and `pm_identity` both apply.
"""
