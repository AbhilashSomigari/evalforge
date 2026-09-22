from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from evalforge import db
from evalforge.adapters import CommandAgentAdapter, HttpAgentAdapter
from evalforge.config import load_suite
from evalforge.graders import run_grader
from evalforge.logging_utils import configure_logging
from evalforge.models import EvalRun, SuiteSpec, TrialResult, compute_suite_version
from evalforge.regression import evaluate_gates
from evalforge.runner import EvalRunner, summarize
from evalforge.storage import find_latest_on_branch, load_run, save_run


def _persist(run: EvalRun) -> None:
    """Write a run to Postgres too when EVALFORGE_DATABASE_URL is configured.

    The file written by save_run() stays the source of truth for local/CI use
    (nothing about existing workflows changes); the database is additive,
    durable, queryable storage for when many runs need to be compared or
    listed without globbing a directory of JSON files.
    """
    if db.is_enabled():
        db.ensure_schema()
        db.insert_run(run)


def _resolve_baseline(
    spec: SuiteSpec, baseline: Path | None, baseline_branch: str | None, run_dir: Path
) -> EvalRun | None:
    """Look up the baseline for gate evaluation.

    --baseline is a fixed path (existing behavior). --baseline-branch turns
    "compare against runs/main.json" into "compare against the latest run
    of this suite on branch main" - the actual dataset/version-registry
    ask: nobody has to hand-manage a baseline file. The file-backend search
    (no DB configured) looks in run_dir - the directory of this invocation's
    --out - not a hardcoded "runs/", since that's wherever this project is
    actually keeping its run artifacts.
    """
    if baseline:
        return load_run(baseline)
    if not baseline_branch:
        return None
    if db.is_enabled():
        db.ensure_schema()
        found = db.latest_run(spec.name, baseline_branch)
    else:
        found = find_latest_on_branch(run_dir, spec.name, baseline_branch)
    if found is None:
        console.print(
            f"[yellow]No prior run of suite '{spec.name}' found on branch "
            f"'{baseline_branch}' - regression gates below will fail without a baseline.[/yellow]"
        )
        return None
    _warn_if_suite_version_mismatch(spec, found)
    return found


def _warn_if_suite_version_mismatch(spec: SuiteSpec, baseline: EvalRun) -> None:
    current = compute_suite_version(spec)
    if baseline.suite_version and baseline.suite_version != current:
        console.print(
            f"[yellow]Baseline run used a different suite definition "
            f"({baseline.suite_version} vs {current} now) - regression comparison "
            "may not be meaningful.[/yellow]"
        )


app = typer.Typer(help="EvalForge — CI/CD evaluation infrastructure for AI agents")
console = Console()


@app.callback()
def _main() -> None:
    configure_logging()


def _print_summary(run: EvalRun) -> None:
    s = run.summary
    table = Table(title=f"EvalForge · {run.suite_name}")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for name, value in [
        ("Task success", f"{s.task_success:.1%}"),
        ("Tool correctness", f"{s.tool_correctness:.1%}"),
        ("Grader score", f"{s.grader_score:.1%}"),
        ("Hallucination rate", f"{s.hallucination_rate:.1%}"),
        ("Avg cost/task", f"${s.avg_cost_usd:.4f}"),
        ("Avg latency", f"{s.avg_latency_ms:.0f} ms"),
        ("Trials", str(s.trials)),
    ]:
        table.add_row(name, value)
    console.print(table)


@app.command()
def run(
    suite: Annotated[Path, typer.Option("--suite", "-s", exists=True, readable=True)],
    agent: Annotated[str | None, typer.Option("--agent", help="Command that reads TaskSpec JSON from stdin")]=None,
    url: Annotated[str | None, typer.Option("--url", help="HTTP agent endpoint")]=None,
    out: Annotated[Path, typer.Option("--out", "-o")]=Path("runs/latest.json"),
    baseline: Annotated[Path | None, typer.Option("--baseline", exists=True, readable=True)]=None,
    baseline_branch: Annotated[
        str | None,
        typer.Option(
            "--baseline-branch",
            help="Auto-resolve the baseline to the latest run of this suite on this branch",
        ),
    ]=None,
    retries: Annotated[
        int, typer.Option("--retries", help="Retry a failed agent invocation this many times (0 = off)")
    ]=0,
) -> None:
    """Run an evaluation suite and enforce its CI gates."""
    if bool(agent) == bool(url):
        raise typer.BadParameter("Provide exactly one of --agent or --url")
    if baseline and baseline_branch:
        raise typer.BadParameter("Provide at most one of --baseline or --baseline-branch")
    spec = load_suite(suite)
    adapter = (
        CommandAgentAdapter(agent, retries=retries)
        if agent
        else HttpAgentAdapter(url, retries=retries)  # type: ignore[arg-type]
    )
    result = asyncio.run(EvalRunner(spec, adapter).run())
    _print_summary(result)

    # Baseline is resolved from previously-saved/persisted runs *before* this
    # run is written below - both save_run() (file) and _persist() (DB) write
    # into the same place --baseline-branch searches, so writing first would
    # make a first-ever run on a branch resolve itself as its own baseline.
    base = _resolve_baseline(spec, baseline, baseline_branch, run_dir=out.parent) if spec.gates else None

    # Save/persist unconditionally, before evaluating gates: evaluate_gates
    # can legitimately raise (e.g. a max_regression gate with no baseline),
    # and a run that already happened must not be lost just because its
    # gates couldn't be evaluated.
    save_run(result, out)
    console.print(f"Saved run: [bold]{out}[/bold]")
    _persist(result)

    passed_gates = True
    if spec.gates:
        gates = evaluate_gates(result, spec.gates, base)
        table = Table(title="CI gates")
        table.add_column("Gate")
        table.add_column("Actual", justify="right")
        table.add_column("Threshold")
        table.add_column("Result")
        for g in gates:
            table.add_row(g.metric, f"{g.actual:.4f}", g.threshold, "PASS" if g.passed else "FAIL")
        console.print(table)
        passed_gates = all(g.passed for g in gates)

    if not passed_gates:
        raise typer.Exit(code=2)


@app.command("ab")
def ab_test(
    suite: Annotated[Path, typer.Option("--suite", "-s", exists=True, readable=True)],
    agent_a: Annotated[str, typer.Option("--agent-a")],
    agent_b: Annotated[str, typer.Option("--agent-b")],
    out_dir: Annotated[Path, typer.Option("--out-dir")]=Path("runs/ab"),
    retries: Annotated[
        int, typer.Option("--retries", help="Retry a failed agent invocation this many times (0 = off)")
    ]=0,
) -> None:
    """Run the same suite against two agent variants for prompt/model A/B testing."""
    spec = load_suite(suite)

    async def _run_both():
        return await asyncio.gather(
            EvalRunner(spec, CommandAgentAdapter(agent_a, retries=retries)).run(),
            EvalRunner(spec, CommandAgentAdapter(agent_b, retries=retries)).run(),
        )

    a, b = asyncio.run(_run_both())
    out_dir.mkdir(parents=True, exist_ok=True)
    a_path, b_path = out_dir / "variant-a.json", out_dir / "variant-b.json"
    save_run(a, a_path)
    save_run(b, b_path)
    _persist(a)
    _persist(b)
    console.print("[bold]Variant A[/bold]")
    _print_summary(a)
    console.print("[bold]Variant B[/bold]")
    _print_summary(b)
    table = Table(title="A/B deltas (B - A)")
    table.add_column("Metric")
    table.add_column("A", justify="right")
    table.add_column("B", justify="right")
    table.add_column("Delta", justify="right")
    for field in type(a.summary).model_fields:
        av, bv = getattr(a.summary, field), getattr(b.summary, field)
        if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
            table.add_row(field, f"{av:.4f}", f"{bv:.4f}", f"{bv-av:+.4f}")
    console.print(table)
    console.print(f"Saved A/B runs under [bold]{out_dir}[/bold]")


@app.command()
def compare(
    baseline: Annotated[Path, typer.Option("--baseline", exists=True, readable=True)],
    candidate: Annotated[Path, typer.Option("--candidate", exists=True, readable=True)],
) -> None:
    """Show metric deltas between two runs."""
    a, b = load_run(baseline), load_run(candidate)
    if a.suite_version and b.suite_version and a.suite_version != b.suite_version:
        console.print(
            f"[yellow]These runs used different suite definitions "
            f"({a.suite_version} vs {b.suite_version}) - the comparison below "
            "may not be meaningful.[/yellow]"
        )
    table = Table(title="EvalForge regression comparison")
    table.add_column("Metric")
    table.add_column("Baseline", justify="right")
    table.add_column("Candidate", justify="right")
    table.add_column("Delta", justify="right")
    for field in type(a.summary).model_fields:
        av = getattr(a.summary, field)
        bv = getattr(b.summary, field)
        if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
            table.add_row(field, f"{av:.4f}", f"{bv:.4f}", f"{bv-av:+.4f}")
    console.print(table)


@app.command()
def replay(
    suite: Annotated[Path, typer.Option("--suite", "-s", exists=True, readable=True)],
    run_file: Annotated[Path, typer.Option("--run", exists=True, readable=True)],
    out: Annotated[Path, typer.Option("--out")]=Path("runs/replayed.json"),
) -> None:
    """Re-grade recorded outputs/trajectories without calling the agent again."""
    spec = load_suite(suite)
    old = load_run(run_file)
    task_map = {t.id: t for t in spec.tasks}

    async def _regrade() -> EvalRun:
        rebuilt: list[TrialResult] = []
        for row in old.results:
            task = task_map[row.task_id]
            specs = [*spec.graders, *task.graders]
            grades = [await run_grader(task, row.output, g) for g in specs]
            rebuilt.append(row.model_copy(update={"grades": grades, "success": all(g.passed for g in grades)}))
        # suite_version reflects the graders that produced these grades (this
        # suite file, which may have changed since); git_sha/git_branch stay as
        # the original run's, since replay never re-invokes the agent.
        return old.model_copy(
            update={
                "results": rebuilt,
                "summary": summarize(rebuilt),
                "suite_version": compute_suite_version(spec),
            }
        )

    new = asyncio.run(_regrade())
    save_run(new, out)
    _persist(new)
    _print_summary(new)
    console.print(f"Saved replay: [bold]{out}[/bold]")


@app.command()
def inspect(run_file: Annotated[Path, typer.Argument(exists=True, readable=True)]) -> None:
    """Print a compact JSON representation of one run."""
    run = load_run(run_file)
    console.print_json(json.dumps(run.model_dump(mode="json")))


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Serve the lightweight run API."""
    import uvicorn

    uvicorn.run("evalforge.api:app", host=host, port=port, reload=False)
