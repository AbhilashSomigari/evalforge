from __future__ import annotations

import httpx

from evalforge.models import AgentOutput, TaskSpec

from .base import AgentAdapter


class HttpAgentAdapter(AgentAdapter):
    """POST a TaskSpec to an existing agent endpoint and parse AgentOutput."""

    def __init__(self, url: str, timeout_s: float = 120.0):
        self.url = url
        self.timeout_s = timeout_s
        self.name = url

    async def run(self, task: TaskSpec) -> AgentOutput:
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(self.url, json=task.model_dump(mode="json"))
            response.raise_for_status()
            return AgentOutput.model_validate(response.json())
