from evalforge_agents.breakdown import compare_tasks, task_breakdown, trials_by_task
from evalforge_agents.models import (
    AgentOutput,
    EvalRun,
    GradeResult,
    RunSummary,
    ToolCall,
    TrialResult,
    Usage,
)


def _trial(task_id, index, *, success, tools=(), grades=(), error=None, latency=100.0, cost=0.01):
    return TrialResult(
        task_id=task_id,
        trial_index=index,
        output=AgentOutput(
            output=f"answer {task_id} {index}",
            tool_calls=[ToolCall(name=name) for name in tools],
            usage=Usage(cost_usd=cost),
        ),
        grades=[GradeResult(grader=name, score=score, passed=passed) for name, score, passed in grades],
        success=success,
        latency_ms=latency,
        error=error,
    )


def _run(results):
    return EvalRun(suite_name="s", agent_name="a", results=results, summary=RunSummary())


def test_task_breakdown_counts_passes_sequences_and_failing_graders():
    run = _run(
        [
            _trial("refund", 0, success=True, tools=["lookup", "refund"],
                   grades=[("tool_correctness", 1.0, True), ("answer", 1.0, True)], latency=100, cost=0.02),
            _trial("refund", 1, success=False, tools=["refund"],
                   grades=[("tool_correctness", 0.0, False), ("answer", 1.0, True)], latency=300, cost=0.04),
            _trial("refund", 2, success=True, tools=["lookup", "refund"],
                   grades=[("tool_correctness", 1.0, True), ("answer", 1.0, True)], latency=200, cost=0.03),
        ]
    )
    [stats] = task_breakdown(run)
    assert (stats.task_id, stats.trials, stats.passed, stats.errors) == ("refund", 3, 2, 0)
    assert round(stats.pass_rate, 4) == 0.6667
    assert stats.avg_latency_ms == 200
    assert round(stats.avg_cost_usd, 4) == 0.03
    assert stats.tool_sequences == [(["lookup", "refund"], 2), (["refund"], 1)]
    assert [(g.name, round(g.pass_rate, 4)) for g in stats.failing_graders] == [("tool_correctness", 0.6667)]


def test_task_breakdown_keeps_task_order_and_counts_errors():
    run = _run(
        [
            _trial("b", 0, success=True),
            _trial("a", 1, success=False, error="RuntimeError: boom"),
            _trial("b", 2, success=True),
        ]
    )
    stats = task_breakdown(run)
    assert [s.task_id for s in stats] == ["b", "a"]
    assert stats[1].errors == 1
    assert stats[1].tool_sequences == [([], 1)]
    assert list(trials_by_task(run)) == ["b", "a"]


def test_compare_tasks_pairs_tasks_and_reports_pass_rate_change():
    baseline = _run([_trial("x", 0, success=True), _trial("x", 1, success=True), _trial("only-base", 2, success=True)])
    candidate = _run([_trial("x", 0, success=True), _trial("x", 1, success=False), _trial("only-cand", 2, success=True)])
    pairs = {p.task_id: p for p in compare_tasks(baseline, candidate)}
    assert list(pairs) == ["x", "only-base", "only-cand"]
    assert pairs["x"].pass_rate_delta == -0.5
    assert pairs["only-base"].candidate is None and pairs["only-base"].pass_rate_delta is None
    assert pairs["only-cand"].baseline is None
