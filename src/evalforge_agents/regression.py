from __future__ import annotations

from dataclasses import dataclass

from evalforge_agents.models import EvalRun, GateSpec


@dataclass
class GateResult:
    metric: str
    passed: bool
    actual: float
    threshold: str
    baseline: float | None = None
    delta: float | None = None
    # Why a gate failed without being evaluated (e.g. "no baseline").
    note: str | None = None


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
        # GateSpec.validate_threshold guarantees value or max_regression is set; value
        # was None to reach this branch, so max_regression must be set.
        assert gate.max_regression is not None
        # max_regression is an absolute metric delta. Direction comes from op:
        # >=/> metrics must not fall too much; <=/< metrics must not rise too much.
        allowed = gate.max_regression
        rising_is_bad = gate.op not in (">=", ">")
        threshold = f"increase <= {allowed}" if rising_is_bad else f"drop <= {allowed}"
        if baseline is None:
            # Fail the gate (and so the CI job), visibly, rather than raise: a
            # regression gate that can't compare against anything must not pass.
            results.append(GateResult(gate.metric, False, actual, threshold, note="no baseline"))
            continue
        base = metric_value(baseline, gate.metric)
        delta = actual - base
        passed = delta <= allowed if rising_is_bad else delta >= -allowed
        results.append(GateResult(gate.metric, passed, actual, threshold, base, delta))
    return results
