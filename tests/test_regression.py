from evalforge.models import EvalRun, GateSpec, RunSummary
from evalforge.regression import evaluate_gates


def run(success: float, cost: float = 0.1) -> EvalRun:
    return EvalRun(
        suite_name="s",
        agent_name="a",
        results=[],
        summary=RunSummary(task_success=success, avg_cost_usd=cost),
    )


def test_absolute_gate():
    result = evaluate_gates(run(0.95), [GateSpec(metric="task_success", op=">=", value=0.9)])
    assert result[0].passed


def test_regression_gate():
    result = evaluate_gates(
        run(0.91),
        [GateSpec(metric="task_success", op=">=", max_regression=0.03)],
        baseline=run(0.93),
    )
    assert result[0].passed
