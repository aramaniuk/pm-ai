"""Inbound adapters, one per external service.

Each implements harvest(since: Cursor) -> list[NormalizedEvent] and does only
auth, fetch, and map-to-schema. No threads, timers, or polling loops (AD-9).
Event types come from the core enumeration (AD-27).
"""
