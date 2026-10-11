from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, NoReturn

import typer
import yaml
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from evalforge_agents import db
from evalforge_agents.adapters import CommandAgentAdapter, HttpAgentAdapter
from evalforge_agents.breakdown import TaskStats, compare_tasks, task_breakdown, trials_by_task
from evalforge_agents.config import load_suite
from evalforge_agents.graders import run_grader
from evalforge_agents.logging_utils import configure_logging
from evalforge_agents.models import EvalRun, SuiteSpec, TrialResult, compute_suite_version
from evalforge_agents.regression import evaluate_gates
from evalforge_agents.runner import EvalRunner, summarize
from evalforge_agents.storage import find_latest_on_branch, load_run, save_run


def _fail(message: str) -> NoReturn:
    """Exit 1 with a one-line error: bad input files and unevaluable gates are
    user/config errors, not crashes, and shouldn't print a traceback."""
    # soft_wrap: keep the message on one line so CI logs stay greppable.
    err_console.print(f"[red]Error:[/red] {message}", soft_wrap=True)
    raise typer.Exit(code=1)


def _load_suite(path: Path) -> SuiteSpec:
    try:
        return load_suite(path)
    except (ValueError, yaml.YAMLError) as exc:  # pydantic's ValidationError is a ValueError
        _fail(f"invalid suite {path}: {exc}")


def _load_run(path: Path) -> EvalRun:
    try:
        return load_run(path)
    except (OSError, ValueError) as exc:
        _fail(f"can't read run {path}: {exc}")


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
        fixed = _load_run(baseline)
        console.print(f"Baseline: {_describe_run(fixed)} from [bold]{baseline}[/bold]")
        return fixed
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
    console.print(f"Baseline: {_describe_run(found)}")
    _warn_if_suite_version_mismatch(spec, found)
    return found


def _describe_run(run: EvalRun) -> str:
    """One-line identity of a run, so a resolved baseline is never invisible."""
    parts = [f"run [bold]{run.id}[/bold]"]
    if run.git_branch:
        parts.append(f"branch '{run.git_branch}'")
    if run.git_sha:
        parts.append(f"sha {run.git_sha[:12]}")
    parts.append(run.created_at.strftime("%Y-%m-%d %H:%M:%S %Z").strip())
    return ", ".join(parts)


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
err_console = Console(stderr=True)


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
    spec = _load_suite(suite)
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

    # Save/persist unconditionally, before evaluating gates: if gate
    # evaluation fails, a run that already happened must not be lost with it.
    save_run(result, out)
    console.print(f"Saved run: [bold]{out}[/bold]")
    _persist(result)

    passed_gates = True
    if spec.gates:
        try:
            gates = evaluate_gates(result, spec.gates, base)
        except ValueError as exc:
            _fail(f"could not evaluate gates (the run itself was saved): {exc}")
        table = Table(title="CI gates")
        table.add_column("Gate")
        table.add_column("Actual", justify="right")
        table.add_column("Threshold")
        # Only regression (max_regression) gates have a baseline value; skip
        # the columns entirely when every gate is absolute.
        show_baseline = any(g.baseline is not None or g.note for g in gates)
        if show_baseline:
            table.add_column("Baseline", justify="right")
            table.add_column("Delta", justify="right")
        table.add_column("Result")
        for g in gates:
            row = [g.metric, f"{g.actual:.4f}", g.threshold]
            if show_baseline:
                row += (
                    [f"{g.baseline:.4f}", f"{g.delta:+.4f}"]
                    if g.baseline is not None and g.delta is not None
                    else [g.note or "", ""]
                )
            table.add_row(*row, "PASS" if g.passed else "FAIL")
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
    spec = _load_suite(suite)

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
    a, b = _load_run(baseline), _load_run(candidate)
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

    tasks = Table(title="Per-task pass rate")
    tasks.add_column("Task", no_wrap=True)
    tasks.add_column("Baseline", justify="right")
    tasks.add_column("Candidate", justify="right")
    tasks.add_column("Change", justify="right")
    for pair in compare_tasks(a, b):
        delta = pair.pass_rate_delta
        style = None if delta == 0 else ("red" if delta is not None and delta < 0 else "yellow")
        tasks.add_row(
            pair.task_id,
            _passed_cell(pair.baseline),
            _passed_cell(pair.candidate),
            "-" if delta is None else f"{delta:+.0%}",
            style=style,
        )
    console.print(tasks)


def _passed_cell(stats: TaskStats | None) -> str:
    if stats is None:
        return "-"
    return f"{stats.passed}/{stats.trials} ({stats.pass_rate:.0%})"


def _tool_sequences_cell(stats: TaskStats) -> str:
    lines = []
    for sequence, count in stats.tool_sequences:
        text = " -> ".join(sequence) or "(no tools)"
        lines.append(text if len(stats.tool_sequences) == 1 else f"{text} ({count}/{stats.trials})")
    return "\n".join(lines)


def _failing_cell(stats: TaskStats) -> str:
    lines = [f"{g.name} {g.pass_rate:.0%}" for g in stats.failing_graders]
    if stats.errors:
        lines.append(f"{stats.errors} trial error(s)")
    return "\n".join(lines) or "-"


def _compact(value: object, limit: int = 140) -> str:
    text = json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _print_task_trials(run: EvalRun, task_id: str) -> None:
    grouped = trials_by_task(run)
    trials = grouped.get(task_id)
    if trials is None:
        _fail(f"no task {task_id!r} in this run; tasks: {', '.join(grouped)}")
    console.print(f"[bold]{escape(task_id)}[/bold] · {run.suite_name} · {_describe_run(run)}")
    for trial in trials:
        verdict = "[green]PASS[/green]" if trial.success else "[red]FAIL[/red]"
        console.print(
            f"\n[bold]Trial {trial.trial_index}[/bold] {verdict}  "
            f"{trial.latency_ms:.0f} ms · ${trial.output.usage.cost_usd:.4f}"
        )
        if trial.error:
            console.print(f"  [red]error:[/red] {escape(trial.error)}")
        for call in trial.output.tool_calls:
            outcome = f"error: {call.error}" if call.error else _compact(call.result)
            console.print(f"  tool  {escape(call.name)}({escape(_compact(call.arguments))}) -> {escape(outcome)}")
        for grade in trial.grades:
            if not grade.passed:
                console.print(f"  [red]failed[/red] {escape(grade.grader)} {grade.score:.2f}: {escape(grade.reason)}")
        console.print(f"  answer: {escape(trial.output.output) or '[dim](empty)[/dim]'}")


@app.command()
def show(
    run_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    task: Annotated[str | None, typer.Option("--task", help="Show every trial of one task")]=None,
    as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON")]=False,
) -> None:
    """Show per-task results: pass rates, tool sequences, failing graders."""
    run = _load_run(run_file)
    if task is not None:
        if as_json:
            trials = trials_by_task(run).get(task)
            if trials is None:
                _fail(f"no task {task!r} in this run")
            print(json.dumps([t.model_dump(mode="json") for t in trials], indent=2))
            return
        _print_task_trials(run, task)
        return

    stats = task_breakdown(run)
    if as_json:
        print(json.dumps([{**asdict(s), "pass_rate": s.pass_rate} for s in stats], indent=2))
        return

    table = Table(
        title=f"Per-task results · {run.suite_name}",
        caption="Failing graders show each grader's pass rate on that task.",
    )
    table.add_column("Task", no_wrap=True)
    # Task IDs stay whole so they can be copied into --task.
    table.add_column("Passed", justify="right")
    table.add_column("Avg latency", justify="right")
    table.add_column("Avg cost", justify="right")
    table.add_column("Tool calls", overflow="fold")
    table.add_column("Failing graders", overflow="fold")
    for s in stats:
        style = None if s.pass_rate == 1 else ("red" if s.pass_rate == 0 else "yellow")
        table.add_row(
            s.task_id,
            _passed_cell(s),
            f"{s.avg_latency_ms:.0f} ms",
            f"${s.avg_cost_usd:.4f}",
            escape(_tool_sequences_cell(s)),
            escape(_failing_cell(s)),
            style=style,
        )
    console.print(f"{_describe_run(run)}")
    console.print(table)
    passed = sum(s.passed for s in stats)
    trials_total = sum(s.trials for s in stats)
    console.print(
        f"{passed}/{trials_total} trials passed across {len(stats)} tasks. "
        "Use --task ID to see every trial of one task."
    )


@app.command()
def replay(
    suite: Annotated[Path, typer.Option("--suite", "-s", exists=True, readable=True)],
    run_file: Annotated[Path, typer.Option("--run", exists=True, readable=True)],
    out: Annotated[Path, typer.Option("--out")]=Path("runs/replayed.json"),
) -> None:
    """Re-grade recorded outputs/trajectories without calling the agent again."""
    spec = _load_suite(suite)
    old = _load_run(run_file)
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
    run = _load_run(run_file)
    console.print_json(json.dumps(run.model_dump(mode="json")))


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Serve the lightweight run API."""
    import uvicorn

    uvicorn.run("evalforge_agents.api:app", host=host, port=port, reload=False)
