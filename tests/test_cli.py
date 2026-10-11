from __future__ import annotations

import json
import sys
import textwrap

import pytest
from typer.testing import CliRunner

from evalforge_agents import db
from evalforge_agents.cli import app

runner = CliRunner()
AGENT_CMD = f"{sys.executable} examples/agents/demo_agent.py"
SUITE = "examples/suites/customer_support.yaml"


@pytest.fixture(autouse=True)
def _no_ambient_database_url(monkeypatch):
    # These tests exercise the file backend specifically (they don't clean up
    # a shared Postgres table between runs); an EVALFORGE_DATABASE_URL left
    # set in the developer's shell must not silently switch them to the DB
    # backend and leak rows across tests.
    monkeypatch.delenv(db.DATABASE_URL_ENV, raising=False)


@pytest.fixture(autouse=True)
def _no_ambient_ci_branch(monkeypatch):
    # The runner reads GITHUB_HEAD_REF, then GITHUB_REF_NAME, then GIT_BRANCH.
    # Tests set GIT_BRANCH, but GitHub Actions sets the other two - and they
    # win. On a push to main GITHUB_REF_NAME=main happened to match; on a tag
    # push (v0.1.0) or a pull request it didn't, and baseline tests failed.
    for var in ("GITHUB_HEAD_REF", "GITHUB_REF_NAME", "GIT_BRANCH"):
        monkeypatch.delenv(var, raising=False)


def test_run_saves_output_and_passes_gates(tmp_path):
    out = tmp_path / "run.json"
    result = runner.invoke(app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(out)])
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text())
    assert data["schema_version"] == 1
    assert data["summary"]["task_success"] == 1.0


def test_run_requires_exactly_one_of_agent_or_url(tmp_path):
    result = runner.invoke(app, ["run", "--suite", SUITE, "--out", str(tmp_path / "r.json")])
    assert result.exit_code != 0


def test_regression_gate_without_baseline_fails_cleanly(tmp_path, monkeypatch):
    # No baseline means the regression gate can't pass: it must fail the job
    # (exit 2, like any failed gate) with a readable reason, not a traceback.
    monkeypatch.setenv("GIT_BRANCH", "main")
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        textwrap.dedent(
            """
            name: regression-only
            tasks:
              - id: t1
                input: hi
            gates:
              - metric: task_success
                op: ">="
                max_regression: 0.01
            """
        )
    )
    out = tmp_path / "r.json"
    result = runner.invoke(app, ["run", "--suite", str(suite), "--agent", AGENT_CMD, "--out", str(out)])
    assert result.exit_code == 2, result.output
    assert "no baseline" in result.output
    assert "FAIL" in result.output
    assert "Traceback" not in result.output
    assert json.loads(out.read_text())["summary"]["task_success"] == 1.0


def test_run_is_saved_even_when_gate_evaluation_raises(tmp_path, monkeypatch):
    # The run already happened; failing to evaluate its gates must not lose it.
    def boom(*args, **kwargs):
        raise ValueError("simulated gate failure")

    monkeypatch.setattr("evalforge_agents.cli.evaluate_gates", boom)
    out = tmp_path / "r.json"
    result = runner.invoke(app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(out)])
    assert result.exit_code == 1
    assert "could not evaluate gates (the run itself was saved)" in result.output
    assert "simulated gate failure" in result.output
    assert out.exists()


def test_invalid_suite_is_a_clean_error_before_running_anything(tmp_path):
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        textwrap.dedent(
            """
            name: typo
            tasks:
              - id: t1
                input: hi
            gates:
              - metric: task_sucess
                op: ">="
                value: 0.9
            """
        )
    )
    out = tmp_path / "r.json"
    result = runner.invoke(app, ["run", "--suite", str(suite), "--agent", AGENT_CMD, "--out", str(out)])
    assert result.exit_code == 1
    assert "invalid suite" in result.output
    assert "Unknown gate metric" in result.output
    assert not out.exists()


def test_unparseable_yaml_is_a_clean_error(tmp_path):
    suite = tmp_path / "suite.yaml"
    suite.write_text("name: [unclosed\n")
    result = runner.invoke(
        app, ["run", "--suite", str(suite), "--agent", AGENT_CMD, "--out", str(tmp_path / "r.json")]
    )
    assert result.exit_code == 1
    assert "invalid suite" in result.output


def test_compare_with_incompatible_run_file_is_a_clean_error(tmp_path):
    good, old = tmp_path / "good.json", tmp_path / "old.json"
    runner.invoke(app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(good)])
    data = json.loads(good.read_text())
    data["schema_version"] = 0
    old.write_text(json.dumps(data))
    result = runner.invoke(app, ["compare", "--baseline", str(old), "--candidate", str(good)])
    assert result.exit_code == 1
    assert "can't read run" in result.output
    assert "schema_version=0" in result.output


def test_run_exits_2_on_gate_failure(tmp_path):
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        textwrap.dedent(
            """
            name: impossible
            tasks:
              - id: t1
                input: hi
            gates:
              - metric: task_success
                op: ">="
                value: 2.0
            """
        )
    )
    result = runner.invoke(
        app, ["run", "--suite", str(suite), "--agent", AGENT_CMD, "--out", str(tmp_path / "r.json")]
    )
    assert result.exit_code == 2


def test_inspect_prints_run_json(tmp_path):
    out = tmp_path / "run.json"
    runner.invoke(app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(out)])
    result = runner.invoke(app, ["inspect", str(out)])
    assert result.exit_code == 0
    assert "customer-support-regression" in result.output


def test_compare_shows_deltas(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    runner.invoke(app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(a)])
    runner.invoke(app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(b)])
    result = runner.invoke(app, ["compare", "--baseline", str(a), "--candidate", str(b)])
    assert result.exit_code == 0
    assert "task_success" in result.output


def test_replay_regrades_without_calling_agent(tmp_path):
    original = tmp_path / "run.json"
    replayed = tmp_path / "replayed.json"
    runner.invoke(app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(original)])
    result = runner.invoke(app, ["replay", "--suite", SUITE, "--run", str(original), "--out", str(replayed)])
    assert result.exit_code == 0, result.output
    assert json.loads(replayed.read_text())["summary"]["task_success"] == 1.0


def test_run_auto_resolves_baseline_from_branch(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_BRANCH", "main")
    first = runner.invoke(app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(tmp_path / "first.json")])
    assert first.exit_code == 0, first.output

    second = runner.invoke(
        app,
        [
            "run", "--suite", SUITE, "--agent", AGENT_CMD,
            "--out", str(tmp_path / "second.json"), "--baseline-branch", "main",
        ],
    )
    assert second.exit_code == 0, second.output
    assert "CI gates" in second.output
    assert "No prior run" not in second.output
    # The resolved baseline must be named, not applied invisibly.
    first_id = json.loads((tmp_path / "first.json").read_text())["id"]
    assert f"Baseline: run {first_id}" in second.output


def test_regression_gate_table_shows_baseline_and_delta(tmp_path):
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        textwrap.dedent(
            """
            name: regression-gated
            tasks:
              - id: t1
                input: hi
            gates:
              - metric: task_success
                op: ">="
                max_regression: 0.01
            """
        )
    )
    base = tmp_path / "base.json"
    # No baseline yet: the regression gate can't be evaluated, but the run is saved.
    runner.invoke(app, ["run", "--suite", str(suite), "--agent", AGENT_CMD, "--out", str(base)])
    assert base.exists()

    result = runner.invoke(
        app,
        [
            "run", "--suite", str(suite), "--agent", AGENT_CMD,
            "--out", str(tmp_path / "cand.json"), "--baseline", str(base),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Baseline: run" in result.output
    assert "Delta" in result.output
    assert "+0.0000" in result.output


def test_absolute_only_gate_table_has_no_baseline_columns(tmp_path):
    result = runner.invoke(
        app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(tmp_path / "r.json")]
    )
    assert result.exit_code == 0, result.output
    assert "Delta" not in result.output


def test_run_baseline_branch_warns_when_nothing_found(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_BRANCH", "main")
    result = runner.invoke(
        app,
        [
            "run", "--suite", SUITE, "--agent", AGENT_CMD,
            "--out", str(tmp_path / "r.json"), "--baseline-branch", "main",
        ],
    )
    assert "No prior run" in result.output


def test_run_rejects_both_baseline_and_baseline_branch(tmp_path):
    baseline_file = tmp_path / "baseline.json"
    runner.invoke(app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(baseline_file)])
    result = runner.invoke(
        app,
        [
            "run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(tmp_path / "r.json"),
            "--baseline", str(baseline_file), "--baseline-branch", "main",
        ],
    )
    assert result.exit_code != 0


def test_run_warns_on_suite_version_mismatch_with_baseline_branch(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_BRANCH", "main")
    gate = "gates:\n  - metric: task_success\n    op: \">=\"\n    value: 0.0\n"
    suite_a = tmp_path / "a.yaml"
    suite_a.write_text(f"name: v-suite\n{gate}tasks:\n  - id: t1\n    input: hi\n")
    suite_b = tmp_path / "b.yaml"
    suite_b.write_text(f"name: v-suite\n{gate}tasks:\n  - id: t1\n    input: a totally different task\n")

    runner.invoke(app, ["run", "--suite", str(suite_a), "--agent", AGENT_CMD, "--out", str(tmp_path / "first.json")])
    result = runner.invoke(
        app,
        [
            "run", "--suite", str(suite_b), "--agent", AGENT_CMD,
            "--out", str(tmp_path / "second.json"), "--baseline-branch", "main",
        ],
    )
    assert "different suite definition" in result.output


def test_compare_warns_on_suite_version_mismatch(tmp_path):
    suite_a = tmp_path / "a.yaml"
    suite_a.write_text("name: v-suite\ntasks:\n  - id: t1\n    input: hi\n")
    suite_b = tmp_path / "b.yaml"
    suite_b.write_text("name: v-suite\ntasks:\n  - id: t1\n    input: a totally different task\n")
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    runner.invoke(app, ["run", "--suite", str(suite_a), "--agent", AGENT_CMD, "--out", str(a)])
    runner.invoke(app, ["run", "--suite", str(suite_b), "--agent", AGENT_CMD, "--out", str(b)])
    result = runner.invoke(app, ["compare", "--baseline", str(a), "--candidate", str(b)])
    assert "different suite definitions" in result.output


def test_ab_runs_both_variants(tmp_path):
    out_dir = tmp_path / "ab"
    result = runner.invoke(
        app,
        ["ab", "--suite", SUITE, "--agent-a", AGENT_CMD, "--agent-b", AGENT_CMD, "--out-dir", str(out_dir)],
    )
    assert result.exit_code == 0, result.output
    assert (out_dir / "variant-a.json").exists()
    assert (out_dir / "variant-b.json").exists()


def test_show_prints_per_task_table(tmp_path):
    out = tmp_path / "run.json"
    runner.invoke(app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(out)])
    result = runner.invoke(app, ["show", str(out)])
    assert result.exit_code == 0, result.output
    assert "order-status" in result.output and "refund" in result.output
    assert "2/2 (100%)" in result.output
    assert "4/4 trials passed across 2 tasks" in result.output


def test_show_task_lists_every_trial_with_tools_and_answer(tmp_path):
    out = tmp_path / "run.json"
    runner.invoke(app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(out)])
    result = runner.invoke(app, ["show", str(out), "--task", "refund"])
    assert result.exit_code == 0, result.output
    assert result.output.count("Trial ") == 2
    assert "issue_refund" in result.output
    assert "answer: Refund submitted" in result.output


def test_show_unknown_task_is_a_clean_error(tmp_path):
    out = tmp_path / "run.json"
    runner.invoke(app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(out)])
    result = runner.invoke(app, ["show", str(out), "--task", "nope"])
    assert result.exit_code == 1
    assert "no task 'nope'" in result.output


def test_show_json_is_machine_readable(tmp_path):
    out = tmp_path / "run.json"
    runner.invoke(app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(out)])
    result = runner.invoke(app, ["show", str(out), "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert {r["task_id"] for r in rows} == {"order-status", "refund"}
    assert all(r["pass_rate"] == 1.0 for r in rows)


def test_show_highlights_failures_and_prints_answers_literally(tmp_path):
    # A failing trial must surface its grader, and model output containing
    # Rich markup ("[bold]") must be printed as text, not interpreted.
    from evalforge_agents.models import AgentOutput, EvalRun, GradeResult, RunSummary, TrialResult
    from evalforge_agents.storage import save_run

    run = EvalRun(
        suite_name="s",
        agent_name="a",
        summary=RunSummary(),
        results=[
            TrialResult(
                task_id="t1",
                trial_index=0,
                output=AgentOutput(output="see [bold]this[/bold]"),
                grades=[GradeResult(grader="answer_content", score=0.0, passed=False, reason="missing")],
                success=False,
            )
        ],
    )
    path = tmp_path / "run.json"
    save_run(run, path)
    overview = runner.invoke(app, ["show", str(path)])
    assert "answer_content 0%" in overview.output
    assert "0/1 trials passed" in overview.output
    detail = runner.invoke(app, ["show", str(path), "--task", "t1"])
    assert "failed answer_content 0.00: missing" in detail.output
    assert "see [bold]this[/bold]" in detail.output


def test_compare_includes_per_task_pass_rates(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    runner.invoke(app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(a)])
    runner.invoke(app, ["run", "--suite", SUITE, "--agent", AGENT_CMD, "--out", str(b)])
    result = runner.invoke(app, ["compare", "--baseline", str(a), "--candidate", str(b)])
    assert "Per-task pass rate" in result.output
    assert "order-status" in result.output
