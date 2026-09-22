from __future__ import annotations

from abc import ABC, abstractmethod

from evalforge.models import AgentOutput, GradeResult, GraderSpec, TaskSpec


class Grader(ABC):
    @abstractmethod
    async def grade(
        self, task: TaskSpec, output: AgentOutput, spec: GraderSpec
    ) -> GradeResult:
        raise NotImplementedError
