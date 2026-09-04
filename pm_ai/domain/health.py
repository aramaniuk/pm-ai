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

__all__ = ["Health", "Probe", "Report"]


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
