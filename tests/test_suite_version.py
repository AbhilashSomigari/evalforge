from __future__ import annotations

from evalforge.models import GateSpec, GraderSpec, SuiteSpec, TaskSpec, compute_suite_version


def _suite(**overrides) -> SuiteSpec:
    defaults = dict(
        name="s",
        description="original description",
        concurrency=4,
        tasks=[TaskSpec(id="t1", input="hi")],
    )
    defaults.update(overrides)
    return SuiteSpec(**defaults)


def test_same_suite_same_version():
    assert compute_suite_version(_suite()) == compute_suite_version(_suite())


def test_description_change_does_not_change_version():
    a = compute_suite_version(_suite(description="one"))
    b = compute_suite_version(_suite(description="another"))
    assert a == b


def test_concurrency_change_does_not_change_version():
    a = compute_suite_version(_suite(concurrency=4))
    b = compute_suite_version(_suite(concurrency=16))
    assert a == b


def test_task_change_changes_version():
    a = compute_suite_version(_suite(tasks=[TaskSpec(id="t1", input="hi")]))
    b = compute_suite_version(_suite(tasks=[TaskSpec(id="t1", input="different input")]))
    assert a != b


def test_gate_change_changes_version():
    a = compute_suite_version(_suite())
    b = compute_suite_version(_suite(gates=[GateSpec(metric="task_success", op=">=", value=0.9)]))
    assert a != b


def test_grader_change_changes_version():
    a = compute_suite_version(_suite())
    b = compute_suite_version(_suite(graders=[GraderSpec(type="no_forbidden_claims")]))
    assert a != b
