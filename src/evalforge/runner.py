from __future__ import annotations

import asyncio
import logging
import os
import time
from statistics import mean

from evalforge.adapters.base import AgentAdapter
from evalforge.graders import run_grader
from evalforge.logging_utils import get_logger, log
from evalforge.models import EvalRun, GradeResult, RunSummary, SuiteSpec, TaskSpec, TrialResult

logger = get_logger(__name__)


class EvalRunner:
    def __init__(self, suite: SuiteSpec, agent: AgentAdapter):
        self.suite = suite
        self.agent = agent
        self._sem = asyncio.Semaphore(suite.concurrency)

    async def _one(self, task: TaskSpec, trial_index: int) -> TrialResult:
        async with self._sem:
            started = time.perf_counter()
            try:
                output = await self.agent.run(task)
                latency_ms = (time.perf_counter() - started) * 1000
                specs = [*self.suite.graders, *task.graders]
                grades: list[GradeResult] = []
                for spec in specs:
                    grades.append(await run_grader(task, output, spec))
                # A trial passes only if every configured grader passes.
                success = all(g.passed for g in grades) if grades else True
                return TrialResult(
                    task_id=task.id,
                    trial_index=trial_index,
                    output=output,
                    grades=grades,
                    success=success,
                    latency_ms=latency_ms,
                )
            except Exception as exc:  # runner must capture failures, not kill the suite
                latency_ms = (time.perf_counter() - started) * 1000
                from evalforge.models import AgentOutput

                log(
                    logger,
                    logging.WARNING,
                    "trial failed",
                    task_id=task.id,
                    trial_index=trial_index,
                    error=f"{type(exc).__name__}: {exc}",
                )
                return TrialResult(
                    task_id=task.id,
                    trial_index=trial_index,
                    output=AgentOutput(output=""),
                    grades=[],
                    success=False,
                    latency_ms=latency_ms,
                    error=f"{type(exc).__name__}: {exc}",
                )

    async def run(self) -> EvalRun:
        log(
            logger,
            logging.INFO,
            "run started",
            suite=self.suite.name,
            agent=self.agent.name,
            tasks=len(self.suite.tasks),
            trials_per_task=self.suite.trials_per_task,
        )
        jobs = [
            self._one(task, i)
            for task in self.suite.tasks
            for i in range(self.suite.trials_per_task)
        ]
        results = await asyncio.gather(*jobs)
        summary = summarize(results)
        log(
            logger,
            logging.INFO,
            "run completed",
            suite=self.suite.name,
            task_success=summary.task_success,
            failed_trials=summary.failed_trials,
            trials=summary.trials,
        )
        return EvalRun(
            suite_name=self.suite.name,
            git_sha=os.getenv("GITHUB_SHA") or os.getenv("GIT_COMMIT"),
            agent_name=self.agent.name,
            results=results,
            summary=summary,
        )


def summarize(results: list[TrialResult]) -> RunSummary:
    if not results:
        return RunSummary()
    successful = [r for r in results if r.success]
    grades = [g for r in results for g in r.grades]
    tool_grades = [g for g in grades if "tool" in g.grader.lower()]
    hallucination_grades = [
        g for g in grades if "halluc" in g.grader.lower() or "forbidden" in g.grader.lower()
    ]
    return RunSummary(
        task_success=len(successful) / len(results),
        tool_correctness=mean(g.score for g in tool_grades) if tool_grades else 0.0,
        grader_score=mean(g.score for g in grades) if grades else 1.0,
        hallucination_rate=(1 - mean(g.score for g in hallucination_grades)) if hallucination_grades else 0.0,
        avg_cost_usd=mean(r.output.usage.cost_usd for r in results),
        avg_latency_ms=mean(r.latency_ms for r in results),
        avg_input_tokens=mean(r.output.usage.input_tokens for r in results),
        avg_output_tokens=mean(r.output.usage.output_tokens for r in results),
        total_cost_usd=sum(r.output.usage.cost_usd for r in results),
        total_judge_input_tokens=sum(int(g.metadata.get("judge_input_tokens", 0)) for g in grades),
        total_judge_output_tokens=sum(int(g.metadata.get("judge_output_tokens", 0)) for g in grades),
        trials=len(results),
        failed_trials=sum(bool(r.error) for r in results),
    )
