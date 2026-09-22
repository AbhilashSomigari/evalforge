from __future__ import annotations

import asyncio

import httpx

from evalforge.models import AgentOutput, TaskSpec

from .base import AgentAdapter

_RETRYABLE_STATUS = {500, 502, 503, 504}


class HttpAgentAdapter(AgentAdapter):
    """POST a TaskSpec to an existing agent endpoint and parse AgentOutput."""

    def __init__(
        self,
        url: str,
        timeout_s: float = 120.0,
        retries: int = 0,
        retry_backoff_s: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.url = url
        self.timeout_s = timeout_s
        self.retries = retries
        self.retry_backoff_s = retry_backoff_s
        # Injectable for tests (httpx.MockTransport); unset in normal use.
        self._transport = transport
        self.name = url

    async def run(self, task: TaskSpec) -> AgentOutput:
        attempt = 0
        while True:
            try:
                return await self._run_once(task)
            except (httpx.TimeoutException, httpx.ConnectError):
                if attempt >= self.retries:
                    raise
            except httpx.HTTPStatusError as exc:
                # 4xx is a deterministic client/agent error, not worth retrying.
                if exc.response.status_code not in _RETRYABLE_STATUS or attempt >= self.retries:
                    raise
            await asyncio.sleep(self.retry_backoff_s * (2**attempt))
            attempt += 1

    async def _run_once(self, task: TaskSpec) -> AgentOutput:
        async with httpx.AsyncClient(timeout=self.timeout_s, transport=self._transport) as client:
            response = await client.post(self.url, json=task.model_dump(mode="json"))
            response.raise_for_status()
            return AgentOutput.model_validate(response.json())
