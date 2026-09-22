from __future__ import annotations

import json
import sys
import textwrap

from typer.testing import CliRunner

from evalforge.cli import app

runner = CliRunner()
AGENT_CMD = f"{sys.executable} examples/agents/demo_agent.py"
SUITE = "examples/suites/customer_support.yaml"


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


def test_ab_runs_both_variants(tmp_path):
    out_dir = tmp_path / "ab"
    result = runner.invoke(
        app,
        ["ab", "--suite", SUITE, "--agent-a", AGENT_CMD, "--agent-b", AGENT_CMD, "--out-dir", str(out_dir)],
    )
    assert result.exit_code == 0, result.output
    assert (out_dir / "variant-a.json").exists()
    assert (out_dir / "variant-b.json").exists()
