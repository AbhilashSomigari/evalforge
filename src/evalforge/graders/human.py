from __future__ import annotations

import json
import os
from pathlib import Path

from evalforge.models import AgentOutput, GradeResult, GraderSpec, TaskSpec

from .base import Grader


class HumanScoreGrader(Grader):
    """Load human-authored scores from a JSON file.

    File format:
    {
      "task-id": {"score": 0.9, "reason": "..."},
      "other-task": 1.0
    }

    The file path comes from grader config `file` or EVALFORGE_HUMAN_SCORES.
    This lets human review participate in the same aggregation/gating pipeline.
    """

    async def grade(self, task: TaskSpec, output: AgentOutput, spec: GraderSpec) -> GradeResult:
        path = spec.config.get("file") or os.getenv("EVALFORGE_HUMAN_SCORES")
        if not path:
            return GradeResult(
                grader=spec.name or spec.type,
                score=0.0,
                passed=False,
                reason="Human score file not configured",
            )
        data = json.loads(Path(path).read_text())
        entry = data.get(task.id)
        if entry is None:
            return GradeResult(
                grader=spec.name or spec.type,
                score=0.0,
                passed=False,
                reason=f"No human score for task {task.id}",
            )
        if isinstance(entry, (int, float)):
            score, reason = float(entry), "Human score"
        else:
            score = float(entry["score"])
            reason = str(entry.get("reason", "Human score"))
        score = max(0.0, min(1.0, score))
        return GradeResult(
            grader=spec.name or spec.type,
            score=score,
            passed=score >= spec.pass_threshold,
            reason=reason,
            metadata={"source": str(path)},
        )
