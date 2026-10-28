"""What a model call may carry, and where its answer may go (AD-31, AD-15, story 7a).

One decision for both adapters. The local adapter (`7a`) and the frontier
adapter behind the router (`7c`) check the same closed table before anything
is sent, so the boundary AD-31 draws on the destination holds for a local call
too: a local model's answer bound for a project page is still an answer that
read whatever was in the prompt.

**The flow table is closed.** A flow is the scopes the prompt's fragments came
from and the destination the answer is bound for. `assert_flow_legal` allows a
flow only when every source may reach the destination by a listed row and no
combination rule refuses it; anything the table does not list is refused, by
name, with the sources and the destination in the sentence:

- allowed: a source bound for itself — `personal` to `personal`, `project:<id>`
  to the same project, `people:<id>` to the same person;
- allowed: project material bound for `personal` (a personal briefing reads
  every project);
- refused: personal material bound for a project, a `people` scope or
  `EXTERNAL`; `personal` and `people` in one prompt; `people` bound anywhere
  but itself; one project's material bound for another; anything bound for
  `EXTERNAL` (no current flow has an external destination — a slice that needs
  one adds its row here); a flow with no sources.

Also here, because both `setup` (a surface) and the adapter need them and
`domain` is the one layer every other may import: the model-name rules
(`normalize_model_name`, `is_cloud_tag`) and `ModelAnswer`, what an adapter
returns.

Imports nothing from `pm_ai` but `pm_ai.domain` (AD-30).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pm_ai.domain.health import Health, Probe
from pm_ai.domain.identity import DataScope, ScopeKind

__all__ = [
    "EXTERNAL",
    "Destination",
    "ExternalDestination",
    "LocalModelPresence",
    "ModelAnswer",
    "ModelSide",
    "ScopeFlow",
    "DEFAULT_LOCAL_MODEL_WINDOW",
    "LOCAL_MODEL_PROBE",
    "LOCAL_MODEL_WINDOWS",
    "SERVER_LIMITS",
    "START_OLLAMA",
    "SUGGESTED_LOCAL_MODEL",
    "ScopeFlowRefused",
    "assert_flow_legal",
    "is_cloud_tag",
    "normalize_model_name",
    "unset_local_model",
]


class ExternalDestination(Enum):
    """An answer bound for a system outside pm-ai's scopes (AD-31 obligation 3)."""

    EXTERNAL = "external"

    def __str__(self) -> str:
        return self.value


EXTERNAL = ExternalDestination.EXTERNAL
"""The one destination that is not a scope. No row of the table allows it today."""

Destination = DataScope | ExternalDestination


class ModelSide(Enum):
    """Which side a model call runs on (AD-15, story 7c): the vocabulary only.

    Which side each task class takes is the router's table
    (`pm_ai.models.router.ROUTES`); this names the two answers so a caller
    that may not import the router — the pipeline — can read one (AD-19:
    the side picks the pool).
    """

    LOCAL = "local"
    FRONTIER = "frontier"


class ScopeFlowRefused(Exception):
    """The flow table does not allow this flow, so nothing was sent (AD-31, AD-49).

    Declared here, beside the table, because the rule is one `domain` owns —
    a scope relation — and not a fact about any port's transport.
    """


@dataclass(frozen=True, slots=True)
class ScopeFlow:
    """The scopes a prompt's fragments came from, and where its answer is bound.

    `sources` is a frozenset so the same flow is one value however the caller
    listed it. An empty set is constructible on purpose: a flow with no
    sources is a row the table refuses by name, and refusing it at the check
    keeps every refusal in one sentence shape.
    """

    sources: frozenset[DataScope]
    destination: Destination

    def __post_init__(self) -> None:
        if isinstance(self.sources, (str, DataScope)) or not isinstance(
            self.sources, frozenset
        ):
            raise TypeError(
                f"ScopeFlow.sources is a frozenset of DataScope, not "
                f"{type(self.sources).__name__}; build it with ScopeFlow.of(...)."
            )
        stray = [s for s in self.sources if not isinstance(s, DataScope)]
        if stray:
            raise TypeError(f"ScopeFlow.sources holds {stray!r}, which are not scopes.")
        if not isinstance(self.destination, (DataScope, ExternalDestination)):
            raise TypeError(
                f"ScopeFlow.destination is a scope or EXTERNAL, not {self.destination!r}."
            )

    @classmethod
    def of(cls, sources: Iterable[DataScope], destination: Destination) -> ScopeFlow:
        return cls(frozenset(sources), destination)

    def __str__(self) -> str:
        named = ", ".join(sorted(str(s) for s in self.sources)) or "no sources"
        return f"{{{named}}} → {self.destination}"


def _same_scope(source: DataScope, destination: Destination) -> bool:
    """Row 1: a `personal`, `project:<id>` or `people:<id>` source bound for itself."""
    return source.kind is not ScopeKind.APPLICATION and source == destination


def _project_to_personal(source: DataScope, destination: Destination) -> bool:
    """Row 2: project material bound for the PM's own scope."""
    return (
        source.is_project
        and isinstance(destination, DataScope)
        and destination.is_personal
    )


_ALLOWED_ROWS = (
    ("a scope bound for itself", _same_scope),
    ("project material bound for personal", _project_to_personal),
)
"""The closed table. A source → destination pair no row allows is refused."""


def _why_refused(source: DataScope, destination: Destination) -> str:
    """The named row a refused pair falls under, for the refusal's sentence."""
    if destination is EXTERNAL:
        return (
            "nothing is bound for EXTERNAL: no current flow has an external "
            "destination, and a slice that needs one adds its row"
        )
    if not isinstance(destination, DataScope):
        return "the flow table lists no row for this destination"
    if source.is_personal:
        return (
            "personal material may be bound only for personal — never for a "
            "project, a people scope or an external system (AD-31)"
        )
    if source.is_people:
        return "a people scope's material may be bound only for that same people scope"
    if source.is_project and destination.is_project:
        return "one project's material may not be bound for another project"
    return "the flow table lists no row for this pair, and an unlisted flow is refused"


def assert_flow_legal(flow: ScopeFlow) -> None:
    """Allow `flow`, or raise `ScopeFlowRefused` naming its sources and destination.

    Checked before anything is sent, on every model call, by every adapter.
    """
    if not isinstance(flow, ScopeFlow):
        raise TypeError(f"a model call's flow is a ScopeFlow, not {type(flow).__name__}.")
    if not flow.sources:
        raise ScopeFlowRefused(
            f"model call refused before anything was sent: the flow {flow} names "
            f"no sources, and a call must say where its fragments came from (AD-31)."
        )
    kinds = {source.kind for source in flow.sources}
    if ScopeKind.PERSONAL in kinds and ScopeKind.PEOPLE in kinds:
        raise ScopeFlowRefused(
            f"model call refused before anything was sent: the flow {flow} puts "
            f"personal and people material in one prompt. The PM's own record and "
            f"a report's record never enter the same prompt (AD-31)."
        )
    for source in sorted(flow.sources, key=str):
        if not any(allows(source, flow.destination) for _, allows in _ALLOWED_ROWS):
            raise ScopeFlowRefused(
                f"model call refused before anything was sent: the flow {flow} "
                f"carries {source} to {flow.destination}, and "
                f"{_why_refused(source, flow.destination)}."
            )


@dataclass(frozen=True, slots=True)
class ModelAnswer:
    """One model call's answer and what AD-31's ledger needs to record it.

    `text` is the answer exactly as the model gave it — validating it against
    a shape is the caller's. `model` is the name the server answered with,
    `local` whether it ran on this machine. Token counts are required: an
    answer that reports none is refused by the adapter, because a window it
    cannot measure is a window it cannot say was not reached. Durations are in
    nanoseconds, as the local server reports them, and `None` where an adapter
    is given none.
    """

    text: str
    model: str
    local: bool
    prompt_tokens: int
    output_tokens: int
    total_ns: int | None = None
    load_ns: int | None = None
    prompt_ns: int | None = None
    output_ns: int | None = None
    estimated_cost_usd: float | None = None
    """A frontier answer's cost, from the router's dated price table (story 7c);
    `None` for a local answer, which costs nothing pm-ai records."""
    call_id: str | None = None
    """The disclosure ledger's call id for a frontier answer (story 7c); `None` locally."""
    sent_at: datetime | None = None
    """When the frontier call's `sent` line was written (story 7c): the call's UTC
    month is that line's, however late the reply. `None` locally."""


# ── Model names ──────────────────────────────────────────────────────────────

SUGGESTED_LOCAL_MODEL = "qwen3:8b"
"""The local model, chosen by Andrei on 2026-10-19 from `7a`'s measurement; `setup` offers it.

The selection rule's answer on the re-run after a reboot (window 16,384,
output cap 2,048): the only candidate that validated on every run, with
recall 9 of 11 on the authored meeting and the Ollama runner peaking at
8,077 MB on an 18 GB Mac. Whether 16 GB is enough stays open. Figures are in
the story's Design Notes.
"""

LOCAL_MODEL_WINDOWS = (16384, 32768)
"""The two context windows the local model may run at (`config.toml`'s `local_model_window`, story 7g).

16,384 was the window from `7a` to `7g`; 32,768 holds an hour-long meeting in
one call. Measured 2026-10-27 on an 18 GB M3 Pro with `qwen3:8b` and a
fictional transcript: Ollama peaked at 9.4–9.7 GB at 32,768 against 7.0 GB at
16,384. Whether 16 GB of memory is enough at 32,768 is open.
"""

DEFAULT_LOCAL_MODEL_WINDOW = 32768
"""The window when `config.toml` names none (Andrei, 2026-10-27)."""

LOCAL_MODEL_PROBE = "local model"
"""The name of `doctor`'s line about the local model (story 7a)."""

START_OLLAMA = "Start Ollama: open the Ollama app, or run `ollama serve` in a terminal."
"""How to start the server, which pm-ai never does itself."""

SERVER_LIMITS = (
    "pm-ai cannot read or set the Ollama server's own settings. Set "
    "OLLAMA_MAX_LOADED_MODELS=1 and OLLAMA_NUM_PARALLEL=1 (the second is already "
    "the default on current servers), and OLLAMA_NO_CLOUD=1 to turn cloud models "
    "off on the server — on macOS with `launchctl setenv OLLAMA_MAX_LOADED_MODELS 1` "
    "(and likewise for the others), then restart the Ollama app; a shell `export` "
    "does not reach it."
)
"""The server's limits, named rather than set (AD-19): printed by `setup` and `doctor`."""



def normalize_model_name(name: str) -> str:
    """`qwen3` → `qwen3:latest`; a name with a tag is kept as written.

    The tag is what follows the last `:` of the last path segment, so a
    registry host with a port (`host:5000/ns/model`) is not read as a tag.
    Names are compared in this form (`config.toml`'s `local_model`, the
    server's model list).
    """
    stripped = name.strip()
    if not stripped:
        return stripped
    last = stripped.rsplit("/", 1)[-1]
    return stripped if ":" in last else f"{stripped}:latest"


def is_cloud_tag(name: str) -> bool:
    """Whether a model name's tag marks it as one the local server runs elsewhere.

    Ollama forwards a model tagged `cloud`, or with a tag ending `-cloud`
    (`gpt-oss:120b-cloud`), to its hosted service — the prompt leaves the
    machine. Refused by name before any prompt is sent (AD-1 class LM). Read
    without regard to case: `x:CLOUD` is a cloud tag too.
    """
    tag = normalize_model_name(name).rsplit("/", 1)[-1].rpartition(":")[2].casefold()
    return tag == "cloud" or tag.endswith("-cloud")


@dataclass(frozen=True, slots=True)
class LocalModelPresence:
    """The local model's presence check, as `doctor` prints it and `setup` reads it.

    `probe` is the `local model` line. `runs_elsewhere` is true when the named
    model would run off this machine — a cloud tag, or a server answer naming
    `remote_host` or `remote_model` — which `setup` refuses as an answer.
    `not_pulled` is true only when the server answered and does not list the
    model: the one case `setup` prints `ollama pull <name>` for.
    """

    probe: Probe
    runs_elsewhere: bool = False
    not_pulled: bool = False


def unset_local_model() -> LocalModelPresence:
    """The `local model` line when `config.toml` names none: `OK`, saying how to set one up.

    An optional model left unset is not a failure (Andrei, 2026-10-19): only a
    named model that is broken, refused or unreachable affects `doctor`'s exit
    code. Declared here, not in the adapter, so the line is the same with or
    without the runtime extra; nothing is asked of Ollama.
    """
    return LocalModelPresence(
        Probe(
            LOCAL_MODEL_PROBE,
            Health.OK,
            "none named (`local_model` is unset in config.toml), so transcripts are "
            "read by pattern matching only; not a failure.",
            f"To use one: run `pm-ai setup`, or set `local_model` in config.toml to "
            f"a model pulled with `ollama pull` ({SUGGESTED_LOCAL_MODEL} is the one "
            f"7a measured).",
        )
    )
