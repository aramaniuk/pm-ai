"""Idempotency keys (AD-20).

Deterministic from (job_type, target_ref, canonical payload) — never seeded by
time, PID, or hash randomisation. A per-attempt key passes review, passes type
checking, and double-posts after a restart.
"""

from __future__ import annotations

import hashlib
import json


def canonical_payload(payload: dict) -> str:
    """Stable serialization: sorted keys, no whitespace drift, UTF-8 preserved.

    Two components computing the key must agree byte-for-byte, so the encoding
    is pinned here rather than left to whoever calls first.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def idempotency_key(job_type: str, target_ref: str, payload: dict) -> str:
    digest = hashlib.sha256(
        f"{job_type}\x00{target_ref}\x00{canonical_payload(payload)}".encode()
    ).hexdigest()
    return f"idem_{digest[:32]}"
