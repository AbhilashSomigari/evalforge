from __future__ import annotations

import asyncio
import json
import shlex

from evalforge.models import AgentOutput, TaskSpec

from .base import AgentAdapter


class CommandAgentAdapter(AgentAdapter):
    """Language-agnostic adapter.

    Sends one JSON task on stdin and expects one JSON AgentOutput on stdout.
    This is intentionally boring: any Python/Node/Go/Java agent can integrate.
    """

    def __init__(
        self,
        command: str,
        timeout_s: float = 120.0,
        retries: int = 0,
        retry_backoff_s: float = 0.5,
    ):
        self.command = command
        self.timeout_s = timeout_s
        # Off by default: a retry re-invokes the whole agent process, including any
        # real tool calls it makes. Only enable this if the agent under test is
        # known to be safe to re-run (idempotent or side-effect-free tools).
        self.retries = retries
        self.retry_backoff_s = retry_backoff_s
        self.name = command

    async def run(self, task: TaskSpec) -> AgentOutput:
        attempt = 0
        while True:
            try:
                return await self._run_once(task)
            except Exception:
                if attempt >= self.retries:
                    raise
                await asyncio.sleep(self.retry_backoff_s * (2**attempt))
                attempt += 1

    async def _run_once(self, task: TaskSpec) -> AgentOutput:
        proc = await asyncio.create_subprocess_exec(
            *shlex.split(self.command),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        payload = json.dumps(task.model_dump(mode="json")).encode()
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(payload), self.timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"Agent timed out after {self.timeout_s}s")
        if proc.returncode != 0:
            raise RuntimeError(
                f"Agent exited with {proc.returncode}: {stderr.decode(errors='replace').strip()}"
            )
        try:
            raw = json.loads(stdout.decode())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Agent stdout was not valid JSON: {stdout[:500]!r}") from exc
        return AgentOutput.model_validate(raw)
