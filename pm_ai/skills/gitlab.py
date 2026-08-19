"""GitLab class-M skills. Stands in for MCP transport in the slice."""

from __future__ import annotations

from dataclasses import dataclass, field

from pm_ai.domain.identity import SkillPermission, TargetRef


@dataclass
class PostComment:
    """The mutation FR-06 performs, and FR-34 must later NOT read as evidence."""

    name: str = "post_comment"
    system: str = "gitlab"
    permission: SkillPermission = SkillPermission.COMMENT
    posted: list[tuple[str, str]] = field(default_factory=list)  # stands in for the API

    def execute(self, target: TargetRef, payload: dict) -> str:
        self.posted.append((target.lock_key, payload["comment"]))
        return f"note_{len(self.posted)}"
