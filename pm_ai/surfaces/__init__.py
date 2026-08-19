"""Telegram bridge, CLI client, loopback HTTP API.

Feature parity across surfaces (AD-7). The CLI is a thin client and owns no
scheduling. Requests over 5s acknowledge and deliver asynchronously (AD-21).
"""
