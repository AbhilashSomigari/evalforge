from __future__ import annotations

from dataclasses import dataclass

from evalforge.models import EvalRun, GateSpec


@dataclass
class GateResult:
    metric: str
    passed: bool
    actual: float
    threshold: str
    baseline: float | None = None
    delta: float | None = None


def metric_value(run: EvalRun, metric: str) -> float:
    if not hasattr(run.summary, metric):
        raise ValueError(f"Unknown summary metric: {metric}")
    return float(getattr(run.summary, metric))


def _compare(actual: float, op: str, target: float) -> bool:
    return {">=": actual >= target, ">": actual > target, "<=": actual <= target, "<": actual < target}[op]


def evaluate_gates(candidate: EvalRun, gates: list[GateSpec], baseline: EvalRun | None = None) -> list[GateResult]:
    results: list[GateResult] = []
    for gate in gates:
        actual = metric_value(candidate, gate.metric)
        if gate.value is not None:
            passed = _compare(actual, gate.op, gate.value)
            results.append(GateResult(gate.metric, passed, actual, f"{gate.op} {gate.value}"))
            continue
        if baseline is None:
            raise ValueError(f"Gate {gate.metric} uses max_regression but no baseline was supplied")
        base = metric_value(baseline, gate.metric)
        delta = actual - base
        # max_regression is an absolute metric delta. Direction comes from op:
        # >=/> metrics must not fall too much; <=/< metrics must not rise too much.
        allowed = float(gate.max_regression)
        if gate.op in (">=", ">"):
            passed = delta >= -allowed
            threshold = f"drop <= {allowed}"
        else:
            passed = delta <= allowed
            threshold = f"increase <= {allowed}"
        results.append(GateResult(gate.metric, passed, actual, threshold, base, delta))
    return results
