"""The loopback API's per-user token: what a usable one is (AD-8, story 4e).

The file is `~/.pm-ai/private/api_token`, declared in the scope model as Tier 3,
plaintext, gitignored and therefore owner-only. The daemon mints it at first
start and reads it on every later one; `pm-ai doctor` reads it to ask a running
daemon how it is. Nothing here opens it: the single writer reads and writes it
(`StorageService.read_api_token` / `create_api_token`), and this module holds
the rule both sides of that agree on.

**A bad file is refused, never minted over.** An empty file, whitespace, a token
shorter than `MIN_TOKEN_LENGTH`, or a file readable by group or others each
refuse the start, naming the file. Re-minting over one would hide whatever made
it bad — a truncated write, a hand-edit, a `chmod` someone ran — and would also
lock out every client already holding the old value. Only absence is a reason
to mint.

`pm_ai.domain` imports nothing from `pm_ai`, so the refusal is nameable by the
storage layer that raises it and by the surface that reports it.
"""

from __future__ import annotations

import stat

__all__ = [
    "ApiTokenRefused",
    "MAX_TOKEN_FILE_BYTES",
    "MIN_TOKEN_LENGTH",
    "TOKEN_BYTES",
    "check_api_token",
    "symlinked",
]

MAX_TOKEN_FILE_BYTES = 4096
"""The most a reader takes from the token file. A longer file is refused, unread past this."""

MIN_TOKEN_LENGTH = 32
"""The fewest characters a usable token has. 31 is refused."""

TOKEN_BYTES = 32
"""Random bytes in a minted token: `secrets.token_urlsafe(32)` is 43 characters."""


class ApiTokenRefused(ValueError):
    """The token file exists and is not one the daemon may use. Nothing was changed."""


def symlinked(where: str) -> ApiTokenRefused:
    """The refusal for a token path that is a symbolic link (an `ELOOP` on `O_NOFOLLOW`)."""
    return ApiTokenRefused(
        f"{where} is a symbolic link, and pm-ai reads its token only from a "
        f"regular file it owns — a link could point the daemon at someone else's "
        f"file. Nothing was changed; replace the link with the file, or delete it "
        f"and pm-ai mints a new one at the next start."
    )


def check_api_token(raw: bytes, *, mode: int, where: str) -> str:
    """The token in `raw`, or `ApiTokenRefused` naming `where` and the reason.

    `mode` is the file's `st_mode` as the reader found it on the descriptor it
    read, and `raw` is at most `MAX_TOKEN_FILE_BYTES + 1` bytes of it. The checks
    run in a fixed order — what kind of file it is, who may read it, its size,
    whether it decodes, whether it is blank, its characters, then its length —
    and none of the messages carries the token or any part of it.

    One trailing newline is tolerated, because `echo` and most editors add one.
    Every other character must be printable ASCII without space (33 to 126):
    whitespace or a control character is refused rather than stripped, since a
    token carrying one is a file somebody edited — and a CR or LF would end the
    header line it is sent in.
    """
    if not stat.S_ISREG(mode):
        raise ApiTokenRefused(
            f"{where} is not a regular file, so it cannot hold the token. Nothing "
            f"was changed; remove whatever is there and pm-ai mints a new token at "
            f"the next start."
        )
    loose = stat.S_IMODE(mode) & 0o077
    if loose:
        raise ApiTokenRefused(
            f"{where} is readable or writable by group or others (mode "
            f"{stat.S_IMODE(mode):04o}), so any local account could drive the "
            f"daemon with it. Nothing was changed. Run `chmod 600 {where}` if the "
            f"file is yours, or delete it and pm-ai mints a new one at the next "
            f"start."
        )
    if len(raw) > MAX_TOKEN_FILE_BYTES:
        raise ApiTokenRefused(
            f"{where} is longer than {MAX_TOKEN_FILE_BYTES} bytes, far more than "
            f"any token pm-ai writes, so it was not read further. Nothing was "
            f"changed; delete it and the next start mints a new one."
        )
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ApiTokenRefused(
            f"{where} is not a token pm-ai wrote: it holds bytes that are not "
            f"ASCII. Nothing was changed; delete the file and pm-ai mints a new "
            f"one at the next start."
        ) from exc
    if text.endswith("\n"):
        text = text[:-1]
    if not text.strip():
        raise ApiTokenRefused(
            f"{where} is empty or holds only whitespace, so there is no token to "
            f"check requests against. Nothing was changed — pm-ai never mints "
            f"over a file it did not expect; delete it and the next start mints "
            f"a new one."
        )
    if any(not 33 <= ord(character) <= 126 for character in text):
        raise ApiTokenRefused(
            f"{where} holds whitespace or a control character inside or around "
            f"the token, which pm-ai never writes. Nothing was changed; delete the "
            f"file and the next start mints a new one."
        )
    if len(text) < MIN_TOKEN_LENGTH:
        raise ApiTokenRefused(
            f"{where} holds a token of {len(text)} characters, fewer than the "
            f"{MIN_TOKEN_LENGTH} pm-ai requires. Nothing was changed; delete the "
            f"file and the next start mints a new one."
        )
    return text
