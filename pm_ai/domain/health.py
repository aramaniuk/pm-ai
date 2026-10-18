"""What a probe answers with, wherever the probe lives.

These three types started in `pm_ai/platform/doctor.py`, which is still the only
module that runs the *machine* probes. They moved here when `ConnectorPort`
gained a health method, and the move was forced rather than chosen:
`pm_ai.ports` may import only `pm_ai.domain` (`.importlinter`'s
`ports-depend-only-on-domain`), so a port that names a health type cannot leave
that type in `platform`. `pm_ai.connectors` could not have reached
`pm_ai.platform` either — the two are independent siblings in the layer stack —
so "follow the doctor's shape" was otherwise ambiguous between an illegal import
and a second, silently divergent `Health` enum.

They are pure value types with no behaviour beyond rendering, so the move costs
nothing and `pm_ai.platform.doctor` re-exports all three: every existing
`from pm_ai.platform.doctor import Health, Probe, Report` still resolves to
these objects, and there is exactly one `Health` in the process.

The rule the shape encodes, stated once here because two layers now depend on
it: **a probe reports; it never raises.** A caller sees the whole picture in one
pass, and one broken thing cannot hide three others.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["ArtifactState", "CONNECTOR_SYSTEMS", "GRAPH_SYSTEM", "EnrolmentReading", "Health", "Presence",
           "Probe", "Report", "UnbuiltConnector"]


class Health(Enum):
    """Four states, because three of them are not failures.

    `ABSENT` is separate from `FAILING` because "reachable, nothing stored" is an
    ordinary first-run state and "cannot reach it at all" is a broken machine.
    Collapsing them would tell an operator to fix a keychain that is fine. It is
    still not `is_healthy` — expected on a fresh install and a pass are different
    claims: setup is incomplete, encrypted writes will be refused until the key
    is enrolled, and a doctor that exits 0 over that would be the summary an
    operator trusts while the morning briefing quietly cannot decrypt anything.

    `WARNING` is separate from `OK` because encryption being off is not healthy
    even though nothing is broken — and separate from `FAILING` because the
    daemon is doing exactly what it was told to.
    """

    OK = "ok"
    WARNING = "warning"
    ABSENT = "absent"
    FAILING = "failing"

    @property
    def is_healthy(self) -> bool:
        return self is Health.OK


@dataclass(frozen=True, slots=True)
class Probe:
    """One question asked and answered, with what to do about the answer."""

    name: str
    health: Health
    detail: str
    remediation: str = ""

    def __str__(self) -> str:
        line = f"[{self.health.value:>7}] {self.name}: {self.detail}"
        return f"{line}\n          → {self.remediation}" if self.remediation else line


@dataclass(frozen=True, slots=True)
class Report:
    probes: tuple[Probe, ...]

    @property
    def healthy(self) -> bool:
        """False if anything is not `OK` — a warning is not a pass.

        Encryption disabled is the case this exists for: the daemon works, and a
        report that called it healthy would be the summary an operator trusts
        while credentials sit in plaintext.
        """
        return all(p.health.is_healthy for p in self.probes)

    def __str__(self) -> str:
        verdict = "healthy" if self.healthy else "NOT healthy"
        return "\n".join([*(str(p) for p in self.probes), "", f"pm-ai is {verdict}."])


@dataclass(frozen=True, slots=True)
class UnbuiltConnector:
    """An enrolled connector row the daemon did not build, and why (story 4o).

    Not a `Probe`: nothing was asked, so there is no health to report and no
    verdict to contribute. `pm-ai connector check` lists it beside the probed
    connectors, so that `doctor`'s count of enrolled rows (slice `4n`) and this
    one can be one count, with any difference a stated reason
    (AD-39).
    """

    instance: str
    reason: str

    def __str__(self) -> str:
        return f"[unbuilt] {self.instance}: enrolled, not built: {self.reason}"


GRAPH_SYSTEM = "graph"
"""A Microsoft Graph connector row's `system` (story 4u names it once)."""

CONNECTOR_SYSTEMS = ("gitlab", GRAPH_SYSTEM)
"""The systems pm-ai builds a connector for: `gitlab`, and `graph` since story 33c.

Here rather than in the composition root because two layers state it: the build
refuses a row naming any other, and `doctor`'s `connectors` line names these as
the supported ones (story 4n).
"""


@dataclass(frozen=True, slots=True)
class EnrolmentReading:
    """The `connectors/` rows on this machine, read by the one rule, as names (story 4n).

    What `pm-ai doctor`'s `connectors` line reports, read without contacting any
    provider and without opening the sealed credential store. Names only: a row's
    settings and credentials never reach it, so nothing secret can.

    - `enrolled` — every enabled row naming a system pm-ai has a connector for,
      with a text name: the rows the build goes on to construct or to list as
      not built with a reason (`connector check`, story 4o).
    - `disabled` — rows whose `enabled` is anything but `true`, by name (the
      file name when the row cannot name itself).
    - `unknown` — enabled rows naming a system pm-ai has no connector for.
    - `problems` — one sentence per row that could not be used: unreadable,
      not JSON, no text name or system, or one name carried by two files.
    - `unlisted` — why `connectors/` itself could not be listed, or `None`.
      Set, the other fields say nothing: an unlisted directory is not empty.
    - `directory` — where `connectors/` is under the paths the read used, so
      a remedy names the directory actually read.

    No field has a default: a caller that did not read the rows must not be
    able to report that there are none.
    """

    directory: str
    enrolled: tuple[str, ...]
    disabled: tuple[str, ...]
    unknown: tuple[str, ...]
    problems: tuple[str, ...]
    unlisted: str | None


class Presence(Enum):
    """How a read of one artifact turned out. Four answers, not two.

    `bytes | None` was the obvious carrier and the wrong one: it reports a
    permission error as an ordinary first run, and tells an operator to create a
    file they already have. Each of these has its own remedy —

    - `READ`: here are the bytes; whether they *parse* is the probe's question.
    - `ABSENT`: no file. A first run. `pm-ai setup`, or `pm-ai project add`.
    - `UNREADABLE`: a file exists and could not be read — a directory in the
      way, EACCES, a bad device. Check ownership; do not create anything.
    - `UNOBTAINABLE`: nothing could even try, because composition failed. `4c`
      requires `doctor` to survive that, so it is a reportable state rather than
      the absence of one.
    """

    READ = "read"
    ABSENT = "absent"
    UNREADABLE = "unreadable"
    UNOBTAINABLE = "unobtainable"


@dataclass(frozen=True, slots=True)
class ArtifactState:
    """One artifact as the caller found it, for a probe that opens nothing.

    Constructed through the four classmethods rather than directly, so a caller
    cannot express `READ` with no bytes or `UNREADABLE` with no reason.
    """

    presence: Presence
    raw: bytes | None = None
    detail: str = ""

    @classmethod
    def read(cls, raw: bytes) -> ArtifactState:
        return cls(Presence.READ, raw=raw)

    @classmethod
    def absent(cls) -> ArtifactState:
        return cls(Presence.ABSENT)

    @classmethod
    def unreadable(cls, detail: str) -> ArtifactState:
        return cls(Presence.UNREADABLE, detail=detail)

    @classmethod
    def unobtainable(cls, detail: str) -> ArtifactState:
        return cls(Presence.UNOBTAINABLE, detail=detail)
