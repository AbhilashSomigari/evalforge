"""Per-task views of a run: what the summary metrics average away."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from statistics import mean

from evalforge_agents.models import EvalRun, TrialResult


@dataclass
class GraderStats:
    name: str
    mean_score: float
    pass_rate: float


@dataclass
class TaskStats:
    task_id: str
    trials: int
    passed: int
    errors: int
    avg_latency_ms: float
    avg_cost_usd: float
    # Distinct tool-name sequences with how many trials produced each, most common first.
    tool_sequences: list[tuple[list[str], int]]
    graders: list[GraderStats] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.trials if self.trials else 0.0

    @property
    def failing_graders(self) -> list[GraderStats]:
        return [g for g in self.graders if g.pass_rate < 1.0]


@dataclass
class TaskComparison:
    task_id: str
    baseline: TaskStats | None
    candidate: TaskStats | None

    @property
    def pass_rate_delta(self) -> float | None:
        if self.baseline is None or self.candidate is None:
            return None
        return self.candidate.pass_rate - self.baseline.pass_rate


def trials_by_task(run: EvalRun) -> dict[str, list[TrialResult]]:
    """Trials grouped by task, tasks in order of first appearance."""
    grouped: dict[str, list[TrialResult]] = {}
    for trial in sorted(run.results, key=lambda r: r.trial_index):
        grouped.setdefault(trial.task_id, []).append(trial)
    return grouped


def _task_stats(task_id: str, trials: list[TrialResult]) -> TaskStats:
    sequences = Counter(tuple(call.name for call in t.output.tool_calls) for t in trials)
    by_grader: dict[str, list[tuple[float, bool]]] = {}
    for trial in trials:
        for grade in trial.grades:
            by_grader.setdefault(grade.grader, []).append((grade.score, grade.passed))
    return TaskStats(
        task_id=task_id,
        trials=len(trials),
        passed=sum(t.success for t in trials),
        errors=sum(t.error is not None for t in trials),
        avg_latency_ms=mean(t.latency_ms for t in trials),
        avg_cost_usd=mean(t.output.usage.cost_usd for t in trials),
        tool_sequences=[(list(seq), count) for seq, count in sequences.most_common()],
        graders=[
            GraderStats(
                name=name,
                mean_score=mean(score for score, _ in results),
                pass_rate=sum(passed for _, passed in results) / len(results),
            )
            for name, results in by_grader.items()
        ],
    )


def task_breakdown(run: EvalRun) -> list[TaskStats]:
    return [_task_stats(task_id, trials) for task_id, trials in trials_by_task(run).items()]


def compare_tasks(baseline: EvalRun, candidate: EvalRun) -> list[TaskComparison]:
    """Per-task pairs, baseline task order first, then tasks only the candidate has."""
    base = {s.task_id: s for s in task_breakdown(baseline)}
    cand = {s.task_id: s for s in task_breakdown(candidate)}
    order = list(base) + [task_id for task_id in cand if task_id not in base]
    return [TaskComparison(task_id, base.get(task_id), cand.get(task_id)) for task_id in order]
