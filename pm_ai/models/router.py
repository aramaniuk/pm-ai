"""The one router every model call goes through (AD-15, AD-31, story 7c).

**One table, every class.** `ROUTES` names, for every `TaskClass` member, its
side — local-only or frontier-eligible — and, if frontier-eligible, its tier:
`coaching` and `research` on the Opus tier (`claude-opus-5-5`), the rest on the
Sonnet tier (`claude-sonnet-5-5`). A class added later is added here, and a
test checks the table against `TaskClass`, so no member is unrouted.

**The flow first.** `ModelRouter.complete` checks `flow` against `domain`'s
closed table (`assert_flow_legal`) before any adapter is reached, local calls
included (AD-31 obligation 3).

**Local classes** go to `7a`'s adapter; with no local model named they refuse
`NoLocalModel`, and frontier routes still work. **Frontier classes** go to the
frontier adapter at their tier, and the router writes the disclosure ledger
around the call, so no caller can skip it (AD-31, AD-38):

- before the request, a `sent` line — a fresh call id (`uuid4`), the task
  class, the model, the flow's sources and destination, the input unknown. If
  it cannot be written, nothing is sent;
- after it, a `completed` line with the same call id when an answer arrived
  (written or not), carrying the tokens it reported and the cost from the dated
  price table; or a `failed` line, input unknown unless the answer reported
  usage. A call abandoned at its deadline or by the shutdown keeps its `sent`
  line. The `completed` and `failed` lines are written whatever the refusal: a
  paid call is always recorded, and no line carries a secret or prompt text.

Refused before any line: the SDK environment variables the frontier adapter
names, no key held (`pm-ai frontier key`), a model with no price row, a prompt
estimated past the window. Never a fallback either way: a local failure is not
retried on Claude, and a frontier failure never falls back to the local model
(AD-17).

**Which pool** (AD-19): `side_of` answers which side a class runs on — the model
pool of one for the local side, the frontier pool of one for the frontier side
— so the caller never chooses a pool, it asks the router.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from pm_ai.domain.disclosure import DisclosureRecord, DisclosureStatus
from pm_ai.domain.identity import DataScope
from pm_ai.domain.model_calls import ModelAnswer, ModelSide, ScopeFlow, assert_flow_legal
from pm_ai.domain.sanitize import Sanitized
from pm_ai.domain.task_classes import TaskClass
from pm_ai.models.frontier.anthropic import AnthropicFrontier, FrontierReply
from pm_ai.models.frontier.models import MODELS, OPUS_TIER, SONNET_TIER, FrontierModel, cost_usd
from pm_ai.ports import (
    FrontierFailed,
    FrontierModelMismatch,
    FrontierNotSent,
    FrontierRefused,
    FrontierStopped,
    ModelPort,
    NoLocalModel,
    StoragePort,
)

__all__ = [
    "FRONTIER_KEY_COMMAND",
    "ROUTES",
    "ModelRouter",
    "Route",
    "Side",
    "route",
]

_log = logging.getLogger(__name__)

FRONTIER_KEY_COMMAND = "pm-ai frontier key"
"""How the PM gives pm-ai an Anthropic API key (story 7c)."""


Side = ModelSide
"""Where a task class may run (AD-15): `domain`'s vocabulary, read by the table below."""


@dataclass(frozen=True, slots=True)
class Route:
    """One class's row: its side and, on the frontier side, its model."""

    side: Side
    model_id: str | None = None

    @property
    def is_local(self) -> bool:
        return self.side is Side.LOCAL


_LOCAL = Route(Side.LOCAL)

ROUTES: Mapping[TaskClass, Route] = {
    TaskClass.BRIEFING_SYNTHESIS: Route(Side.FRONTIER, SONNET_TIER),
    TaskClass.CLASSIFICATION: _LOCAL,
    TaskClass.COACHING: Route(Side.FRONTIER, OPUS_TIER),
    TaskClass.DRAFT_GENERATION: Route(Side.FRONTIER, SONNET_TIER),
    TaskClass.EMBEDDING: _LOCAL,
    TaskClass.EXTRACTION: _LOCAL,
    TaskClass.FUZZY_MATCH: _LOCAL,
    TaskClass.INQUIRY_SYNTHESIS: Route(Side.FRONTIER, SONNET_TIER),
    TaskClass.MEETING_SUMMARY: Route(Side.FRONTIER, SONNET_TIER),
    TaskClass.RESEARCH: Route(Side.FRONTIER, OPUS_TIER),
    TaskClass.TRANSCRIPTION: _LOCAL,
}
"""AD-15's table: every `TaskClass` member, its side, and its tier."""


def route(task_class: TaskClass) -> Route:
    """`task_class`'s row; a member with none is a defect, refused by name."""
    if not isinstance(task_class, TaskClass):
        raise TypeError(f"a model call's task class is a TaskClass, not {task_class!r}.")
    found = ROUTES.get(task_class)
    if found is None:
        raise LookupError(
            f"{task_class.value} has no row in the router's table, so it is not "
            f"routed (AD-15). A class added to TaskClass declares its side here."
        )
    return found


class ModelRouter:
    """`ModelPort` for every class: the flow checked, then the side its row names.

    `local` is `7a`'s adapter or `None`; `frontier` the frontier adapter;
    `storage` where the ledger is written and the key read; `key` reads the
    sealed key now (`None` when none is held); `clock` stamps the ledger.
    `bound(storage)` is the same router writing through another door to the
    store — the daemon's, which hands each write to its writer thread.
    """

    def __init__(
        self,
        *,
        local: ModelPort | None,
        frontier: AnthropicFrontier | None,
        storage: StoragePort,
        key: Callable[[StoragePort], str | None],
        clock: Callable[[], datetime],
        models: Mapping[str, FrontierModel] = MODELS,
        call_id: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._local = local
        self._frontier = frontier
        self._storage = storage
        self._key = key
        self._clock = clock
        self._models = models
        self._call_id = call_id

    @property
    def local(self) -> ModelPort | None:
        return self._local

    @property
    def frontier(self) -> AnthropicFrontier | None:
        """The one frontier adapter: every request to Anthropic, the key check
        included, is made through it (story 7c)."""
        return self._frontier

    def bound(self, storage: StoragePort) -> ModelRouter:
        """This router, writing the ledger and reading the key through `storage`."""
        return ModelRouter(
            local=self._local,
            frontier=self._frontier,
            storage=storage,
            key=self._key,
            clock=self._clock,
            models=self._models,
            call_id=self._call_id,
        )

    @staticmethod
    def side_of(task_class: TaskClass) -> Side:
        """The side `task_class` runs on, which picks its pool (AD-19)."""
        return route(task_class).side

    def close(self) -> None:
        """Close the frontier side's connections (the frontier pool's shutdown)."""
        if self._frontier is not None:
            self._frontier.close()

    def complete(
        self,
        *,
        task_class: TaskClass,
        instructions: str,
        external: Sequence[Sanitized],
        flow: ScopeFlow,
        shape: Mapping[str, object] | None = None,
    ) -> ModelAnswer:
        """Route one call: the flow checked first, then the side `task_class`'s row names."""
        assert_flow_legal(flow)
        found = route(task_class)
        if found.side is Side.LOCAL:
            if self._local is None:
                raise NoLocalModel(
                    "no local model is named: `local_model` is unset in config.toml, "
                    "so no local adapter is wired. Run `pm-ai setup`, or set "
                    "`local_model` to a model pulled with `ollama pull`."
                )
            return self._local.complete(
                task_class=task_class,
                instructions=instructions,
                external=external,
                flow=flow,
                shape=shape,
            )
        frontier, key, model, sent = self._prepared(task_class, found, flow)
        written: list[bool] = []

        def before_send() -> None:
            self._storage.append_disclosure(sent)
            written.append(True)

        try:
            reply = frontier.complete(
                model=model,
                key=key,
                instructions=instructions,
                external=external,
                shape=shape,
                before_send=before_send,
            )
        except FrontierRefused as refusal:
            # Anything else — unexpected, or the shutdown — leaves the `sent`
            # line as the call's last word: of unknown cost, never free.
            if written:
                self._after(sent, refusal)
            raise
        if not written:  # an adapter that skipped the line is a defect, not a call
            raise FrontierNotSent("the frontier adapter answered without the `sent` line.")
        return self._completed(sent, model, reply)

    # ── the frontier side, with its ledger lines ─────────────────────────────

    def _prepared(
        self, task_class: TaskClass, found: Route, flow: ScopeFlow
    ) -> tuple[AnthropicFrontier, str, FrontierModel, DisclosureRecord]:
        """The adapter, the key, the priced model and the `sent` line; or refused, writing none."""
        frontier = self._frontier
        if frontier is None:
            raise FrontierNotSent(
                "no frontier adapter is wired in this process (the runtime extra is "
                "missing, or this is not the daemon), so nothing was sent."
            )
        refused = frontier.refused()
        if refused is not None:
            raise FrontierNotSent(refused)
        try:
            key = self._key(self._storage)
        except Exception as unreadable:  # noqa: BLE001 — named by type; nothing is sent
            raise FrontierNotSent(
                f"the sealed credential store could not be read for the Anthropic key "
                f"({type(unreadable).__name__}), so nothing was sent and no ledger line "
                f"was written."
            ) from unreadable
        if not key:
            raise FrontierNotSent(
                f"no Anthropic API key is held, so nothing was sent and no ledger line "
                f"was written. Enter one with `{FRONTIER_KEY_COMMAND}` while the daemon "
                f"is running."
            )
        model_id = found.model_id or ""
        model = self._models.get(model_id)
        if model is None:
            raise FrontierNotSent(
                f"{model_id or 'the route'} has no row in the dated price table, so its "
                f"cost could not be computed; refused rather than guessed. Nothing was "
                f"sent and no ledger line was written."
            )
        destination = flow.destination
        if not isinstance(destination, DataScope):
            raise FrontierNotSent(
                f"a frontier answer is bound for a scope, not {destination}; nothing was sent."
            )
        sent = DisclosureRecord(
            at=self._clock(),
            task_class=task_class.value,
            model=model.model_id,
            contributing_scopes=flow.sources,
            input_tokens=None,
            output_tokens=None,
            estimated_cost_usd=None,
            destination=destination,
            call_id=self._call_id(),
            status=DisclosureStatus.SENT,
        )
        return frontier, key, model, sent

    def _completed(
        self, sent: DisclosureRecord, model: FrontierModel, reply: FrontierReply
    ) -> ModelAnswer:
        """The `completed` line with the reported usage and its cost, and the answer."""
        cost = cost_usd(model, input_tokens=reply.input_tokens, output_tokens=reply.output_tokens)
        self._append_after(
            replace(
                sent,
                at=self._clock(),
                status=DisclosureStatus.COMPLETED,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                estimated_cost_usd=cost,
            )
        )
        return ModelAnswer(
            text=reply.text,
            model=reply.model,
            local=False,
            prompt_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            estimated_cost_usd=cost,
            call_id=sent.call_id,
            sent_at=sent.at,
        )

    def _after(self, sent: DisclosureRecord, refusal: FrontierRefused) -> None:
        """The `completed` or `failed` line for a refused call, from the usage it carries."""
        answered = isinstance(refusal, (FrontierStopped, FrontierModelMismatch)) or (
            isinstance(refusal, FrontierFailed) and refusal.model is not None
        )
        if not refusal.sent and not answered:
            # Refused after the line but before the request: nothing left.
            status = DisclosureStatus.FAILED
        else:
            status = DisclosureStatus.COMPLETED if answered else DisclosureStatus.FAILED
        model_id = refusal.model or sent.model
        priced = self._models.get(model_id)
        cost = (
            cost_usd(priced, input_tokens=refusal.input_tokens or 0, output_tokens=refusal.output_tokens or 0)
            if refusal.reports_usage and priced is not None
            else None
        )
        self._append_after(
            replace(
                sent,
                at=self._clock(),
                status=status,
                model=model_id,
                input_tokens=refusal.input_tokens if refusal.reports_usage else None,
                output_tokens=refusal.output_tokens if refusal.reports_usage else None,
                estimated_cost_usd=cost,
            )
        )
        refusal.call_id = sent.call_id
        refusal.cost_usd = cost
        refusal.sent_at = sent.at

    def _append_after(self, record: DisclosureRecord) -> None:
        """The line after a call: written when it can be, never in place of the answer.

        A line that cannot be written leaves the call's `sent` line as its last
        word — a call of unknown cost in the month's total, never a free one —
        and is logged by its call id alone.
        """
        try:
            self._storage.append_disclosure(record)
        except Exception as unwritten:  # noqa: BLE001 — logged; the sent line stands
            _log.warning(
                "the disclosure ledger's %s line for call %s could not be written (%s); "
                "its sent line stands, of unknown cost",
                record.status.value if record.status else "closing",
                record.call_id,
                type(unwritten).__name__,
            )
