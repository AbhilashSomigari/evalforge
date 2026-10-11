from __future__ import annotations

from abc import ABC, abstractmethod

from evalforge_agents.models import AgentOutput, TaskSpec


class AgentAdapter(ABC):
    name: str

    @abstractmethod
    async def run(self, task: TaskSpec) -> AgentOutput:
        raise NotImplementedError
