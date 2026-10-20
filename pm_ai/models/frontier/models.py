"""The frontier models pm-ai may ask, their prices and windows, dated (story 7c, AD-17).

**Cost is computed, never invented.** A frontier call's cost is its reported
input and output tokens times this table's price per million tokens. A model
with no row refuses the call before anything is sent (the router), rather than
guessing a price.

**Dated, because prices move.** The rows are the spine's Cost-model note,
checked against Anthropic's pricing and models pages on 2026-10-16 and again on
2026-10-20, when this slice was built: Sonnet 5.5 $2/$10 per million tokens,
Opus 5.5 $4/$20, cache hits at 0.05× the input price on both ($0.10 and $0.20),
a 1M-token window and 128K output tokens. `ModelAnswer` carries no cache-token
fields and no request asks for caching, so no cache price is applied and none
is recorded here.

**The window** is the model's context window in tokens, used only to refuse a
prompt estimated past it before sending (`ESTIMATE_CHARACTERS_PER_TOKEN`). No
token-counting request is made: it would send the transcript.

Imports nothing from `pm_ai` (AD-30): data only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

__all__ = [
    "CHECKED_ON",
    "ESTIMATE_CHARACTERS_PER_TOKEN",
    "MODELS",
    "OPUS_TIER",
    "SONNET_TIER",
    "FrontierModel",
    "cost_usd",
]

CHECKED_ON = date(2026, 10, 16)
"""When the prices and windows below were last checked against Anthropic's pages."""

OPUS_TIER = "claude-opus-5-5"
"""AD-15's Opus tier: `coaching` and `research`."""

SONNET_TIER = "claude-sonnet-5-5"
"""AD-15's Sonnet tier: every other frontier-eligible class, `meeting_summary` included."""

ESTIMATE_CHARACTERS_PER_TOKEN = 2
"""The local estimate's divisor: 2 characters per token, conservative (story 7c).

Conservative for Latin and Cyrillic text, which runs nearer 3–4 characters per
token, so there it overstates a prompt's tokens. Dense scripts (Chinese,
Japanese, Korean) can run past one token per character, and a prompt in them
can exceed the estimate — an admitted limit; the API then refuses it, and the
call's ledger line says `failed`. It is only ever used to refuse before sending, never
written to the ledger as a fact.
"""


@dataclass(frozen=True, slots=True)
class FrontierModel:
    """One model's row: its id, its prices per million tokens, and its window."""

    model_id: str
    input_usd_per_million: float
    output_usd_per_million: float
    context_window: int
    max_output_tokens: int


MODELS: dict[str, FrontierModel] = {
    SONNET_TIER: FrontierModel(
        model_id=SONNET_TIER,
        input_usd_per_million=2.0,
        output_usd_per_million=10.0,
        context_window=1_000_000,
        max_output_tokens=128_000,
    ),
    OPUS_TIER: FrontierModel(
        model_id=OPUS_TIER,
        input_usd_per_million=4.0,
        output_usd_per_million=20.0,
        context_window=1_000_000,
        max_output_tokens=128_000,
    ),
}
"""The dated price and window table, by model id. A model with no row is refused."""


def cost_usd(model: FrontierModel, *, input_tokens: int, output_tokens: int) -> float:
    """The cost of one answer's reported tokens at `model`'s prices."""
    return (
        input_tokens * model.input_usd_per_million
        + output_tokens * model.output_usd_per_million
    ) / 1_000_000
