"""Microsoft Graph — the delegated auth this connector family runs on.

Auth only for now. The calendar, chat, channel-message and transcript adapters
that use it are stories 33b, 33d and 33e; this package exists first because
none of them can ask Graph anything without a token.
"""
