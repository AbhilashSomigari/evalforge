# EvalForge

[![PyPI](https://img.shields.io/pypi/v/evalforge-agents)](https://pypi.org/project/evalforge-agents/)
[![CI](https://github.com/AbhilashSomigari/evalforge/actions/workflows/evalforge.yml/badge.svg)](https://github.com/AbhilashSomigari/evalforge/actions/workflows/evalforge.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**CI/CD evaluation for tool-using, multi-step AI agents.** EvalForge runs
YAML-defined scenarios against your agent, captures the full trajectory,
grades the outcome, and blocks a merge or deploy when quality regresses.

> Change a prompt, model, tool schema, or RAG pipeline → run scenarios →
> capture trajectories → grade outcomes → compare against a baseline →
> enforce gates.

## Contents

- [Why EvalForge](#why-evalforge)
- [Features](#features)
- [Install](#install)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Connecting an agent](#connecting-an-agent)
- [Writing a suite](#writing-a-suite)
- [Graders](#graders)
- [Gates and baselines](#gates-and-baselines)
- [CLI reference](#cli-reference)
- [Run API](#run-api)
- [Configuration](#configuration)
- [Development](#development)
- [Roadmap](#roadmap)

## Why EvalForge

Unit tests are necessary but not sufficient for agents. Agents are
non-deterministic, make several model calls, use tools, retrieve context, and
change external state. EvalForge treats the **trajectory and the outcome** as
first-class test artifacts instead of checking only the final string.

## Features

| Area | What you get |
|---|---|
| Suites | YAML tasks, multiple trials per task, concurrent execution |
| Agents | Language-agnostic command adapter (JSON over stdin/stdout), HTTP adapter, opt-in retries with backoff |
| Grading | Exact/contains/regex/similarity/JSON-contract, tool sequence and arguments, RAG citation groundedness, hallucination guard, LLM-as-judge, human scores |
| Metrics | Task success, tool correctness, hallucination rate, latency, tokens, cost |
| CI gates | Absolute thresholds and regression limits against a baseline, auto-resolved per branch |
| Workflows | Run, A/B test two agents, compare runs, re-grade a recorded run without calling the agent |
| Storage | JSON run artifacts, optionally mirrored to Postgres |
| API | FastAPI service with auth, CORS allowlist, rate limiting, structured JSON logs |

## Install

```bash
pip install evalforge-agents
pip install "evalforge-agents[postgres]"   # optional Postgres run storage
```

Requires Python 3.11+.

| Name | Value |
|---|---|
| PyPI distribution | `evalforge-agents` |
| CLI command | `evalforge` |
| Import package | `evalforge_agents` |

The `evalforge` package on PyPI is an unrelated project.

## Quick start

The repository includes an example suite and a demo agent:

```bash
git clone https://github.com/AbhilashSomigari/evalforge.git
cd evalforge
pip install evalforge-agents

evalforge run \
  --suite examples/suites/customer_support.yaml \
  --agent "python examples/agents/demo_agent.py" \
  --out runs/demo.json
```

The command prints a metrics table and a CI gates table, and writes the full
run artifact to `runs/demo.json`.

## How it works

```text
                   ┌────────────────────────┐
                   │  YAML Evaluation Suite │
                   │ tasks · trials · gates │
                   └────────────┬───────────┘
                                │
                    ┌───────────▼───────────┐
                    │      Eval Runner      │
                    │ concurrency · retries │
                    └───────┬────────┬──────┘
                            │        │
             ┌──────────────▼─┐    ┌─▼──────────────┐
             │ Agent Adapter   │    │ Recorded Replay │
             │ command / HTTP  │    │ no model call   │
             └────────┬────────┘    └──────┬─────────┘
                      │                    │
                trajectory + output + usage
                      │                    │
                ┌─────▼────────────────────▼──┐
                │          Graders            │
                │ code · tool · RAG · LLM     │
                └──────────────┬──────────────┘
                               │
                       ┌───────▼────────┐
                       │ Metrics + Runs │
                       └───────┬────────┘
                               │
                    ┌──────────▼──────────┐
                    │ Regression CI Gates │
                    │  PASS / BLOCK       │
                    └─────────────────────┘
```

**The run artifact is the product.** Every trial stores the task, the output,
tool calls, trajectory, grader results, latency, usage, and cost. CI gates,
the API, dashboards, and human review all read the same versioned JSON object
(`schema_version`).

## Connecting an agent

EvalForge doesn't require any agent framework. The same suite can evaluate
Python, TypeScript, Java, Go, MCP-based agents, or remote services.

**Command adapter** (`--agent "<command>"`): EvalForge starts the command once
per trial, writes one `TaskSpec` JSON object to stdin, and reads one
`AgentOutput` JSON object from stdout. A non-zero exit code, invalid JSON, or a
timeout (120 s) fails the trial.

**HTTP adapter** (`--url <endpoint>`): EvalForge `POST`s the `TaskSpec` JSON to
the endpoint and expects an `AgentOutput` JSON response. With `--retries`,
timeouts, connection errors, and 5xx responses are retried; 4xx responses
never are.

`TaskSpec` (input):

```json
{
  "id": "refund",
  "input": "Refund order R200 please.",
  "expected_output": null,
  "expected_tools": ["lookup_order", "issue_refund"],
  "context": [],
  "metadata": {"order_id": "R200"},
  "graders": []
}
```

`AgentOutput` (response):

```json
{
  "output": "Refund submitted. Source: KB-REFUND-1",
  "trajectory": [
    {"type": "message", "role": "user", "content": "Refund order R200"},
    {"type": "tool_call", "tool_call": {"name": "lookup_order", "arguments": {"order_id": "R200"}}}
  ],
  "tool_calls": [
    {"name": "lookup_order", "arguments": {"order_id": "R200"}}
  ],
  "usage": {"input_tokens": 100, "output_tokens": 35, "cost_usd": 0.012},
  "model": "your-model"
}
```

Retries are off by default because retrying re-runs any tool calls the agent
makes. Enable them only for agents whose tools are safe to repeat.

## Writing a suite

```yaml
name: checkout-agent
trials_per_task: 3
concurrency: 8

graders:                      # applied to every task
  - type: no_forbidden_claims
    name: hallucination_guard
    config:
      phrases: ["guaranteed tomorrow"]

gates:
  - metric: task_success
    op: ">="
    value: 0.90
  - metric: avg_cost_usd
    op: "<="
    value: 0.15
  - metric: task_success
    op: ">="
    max_regression: 0.03

tasks:
  - id: refund-order
    input: Refund order R200.
    expected_tools: [lookup_order, issue_refund]
    metadata:
      source_ids: [KB-REFUND-1]
    graders:                  # applied to this task only
      - type: tool_sequence
        name: tool_correctness
```

| Suite field | Default | Meaning |
|---|---|---|
| `name` | required | Suite name; runs and baselines are grouped by it |
| `trials_per_task` | `1` | Runs per task, for measuring non-determinism |
| `concurrency` | `4` | Trials executed in parallel |
| `graders` | `[]` | Graders applied to every task |
| `gates` | `[]` | CI gates evaluated on the run summary |
| `tasks` | required | `id`, `input`, and optional `expected_output`, `expected_tools`, `context`, `metadata`, `graders` |

A trial passes when every grader that applies to it reaches its
`pass_threshold` (default `1.0`).

## Graders

| Type | Checks | Config |
|---|---|---|
| `exact_match` | Output equals the expected text | `expected` (or task `expected_output`), `case_sensitive` |
| `contains` | Output contains each value | `values` (or `expected`) |
| `regex` | Output matches a pattern | `pattern` |
| `similarity` | Sequence similarity to a reference | `expected` (or task `expected_output`) |
| `json_contract` | Output is a JSON object with required keys | `required_keys` |
| `tool_sequence` | Tools called match the expected tools | `expected` (or task `expected_tools`), `mode`: `ordered_subsequence` (default), `exact`, `set` |
| `tool_arguments` | Specific tool calls used specific arguments | `checks`: list of `{tool, arguments}` |
| `citation_groundedness` | Output cites known source IDs | `source_ids` (or task `metadata.source_ids`), `require_all` |
| `no_forbidden_claims` | Output avoids known hallucinated phrases | `phrases` |
| `llm_judge` | An OpenAI-compatible model scores the output against a rubric | `rubric`, `model`, `base_url` |
| `human` | Imported human review scores | `file` |

### LLM-as-judge

```yaml
- type: llm_judge
  name: policy_quality
  pass_threshold: 0.8
  config:
    rubric: >-
      Score whether the answer follows the refund policy, cites evidence,
      and avoids claiming actions that did not occur.
```

The API key is read only from `EVALFORGE_JUDGE_API_KEY`, never from suite
files, because suites are usually committed. `EVALFORGE_JUDGE_BASE_URL` points
the grader at any OpenAI-compatible endpoint, including gateways and local
inference servers. A failed judge call fails that one grade, not the run.

### Human scores

```yaml
- type: human
  name: human_quality
  pass_threshold: 0.8
  config:
    file: reviews/human_scores.json
```

```json
{
  "refund": {"score": 0.95, "reason": "Correct and policy-compliant"},
  "order-status": 1.0
}
```

## Gates and baselines

Each gate compares one summary metric:

- `value` sets an absolute threshold, for example `task_success >= 0.9`.
- `max_regression` limits change against a baseline run. A metric with
  `op: ">="` may drop by at most that amount; a metric with `op: "<="` may
  rise by at most that amount.

Available metrics: `task_success`, `tool_correctness`, `grader_score`,
`hallucination_rate`, `avg_cost_usd`, `avg_latency_ms`, `avg_input_tokens`,
`avg_output_tokens`, `total_cost_usd`, `total_judge_input_tokens`,
`total_judge_output_tokens`, `trials`, `failed_trials`. An unknown metric is
rejected when the suite loads, before any agent is called.

### Choosing a baseline

| Option | Baseline used |
|---|---|
| `--baseline runs/main.json` | A fixed run file |
| `--baseline-branch main` | The latest run of the same suite on that branch |

`--baseline-branch` searches Postgres when `EVALFORGE_DATABASE_URL` is set,
otherwise the directory of `--out`. Each run records its branch from
`GITHUB_HEAD_REF`, `GITHUB_REF_NAME`, or `GIT_BRANCH`, in that order; GitHub
Actions sets the first two automatically.

The run prints which baseline it used, and the gates table shows the baseline
value and delta for each regression gate. A regression gate with no baseline
**fails** and shows `no baseline`. If the baseline was produced by a different
suite definition, EvalForge warns that the comparison may not be meaningful.
It detects this from a content hash of the suite (`suite_version`) that
ignores `description` and `concurrency`.

### Baselines in CI

Each CI job starts with an empty workspace, so `--baseline-branch` needs run
storage that outlives the job. Either:

- set `EVALFORGE_DATABASE_URL` (as a secret) to a Postgres instance the job can
  reach, or
- restore earlier run files into the `--out` directory before running, for
  example with `actions/cache` or by downloading the main branch's last run
  artifact.

Without one of these, every regression gate fails with `no baseline`.

## CLI reference

| Command | Purpose |
|---|---|
| `evalforge run --suite S (--agent CMD \| --url URL) [--out F] [--baseline F \| --baseline-branch B] [--retries N]` | Run a suite and enforce its gates |
| `evalforge ab --suite S --agent-a CMD --agent-b CMD [--out-dir D]` | Run one suite against two agents and print metric deltas |
| `evalforge compare --baseline F --candidate F` | Show metric deltas between two runs |
| `evalforge replay --suite S --run F [--out F]` | Re-grade a recorded run without calling the agent |
| `evalforge inspect F` | Print a run as compact JSON |
| `evalforge serve [--host H] [--port P]` | Start the run API |

| Exit code | Meaning |
|---|---|
| `0` | All gates passed |
| `1` | Invalid input: malformed suite, unknown gate metric, unreadable run file |
| `2` | At least one gate failed; this is what blocks a CI job |

Every run is saved, even when its gates fail.

## Run API

```bash
evalforge serve --host 0.0.0.0 --port 8000
```

| Endpoint | Returns |
|---|---|
| `GET /health` | Liveness; never requires the API key |
| `GET /runs?limit=20&offset=0&suite=NAME` | Run summaries, paginated and filterable by suite |
| `GET /runs/{key}` | One full run (`409` if it was written by an incompatible `schema_version`) |
| `GET /docs` | Interactive OpenAPI docs |

Without a database, runs are read from the local `runs/` directory and
`{key}` is a filename. With `EVALFORGE_DATABASE_URL` set, `run`, `ab`, and
`replay` also write each run to Postgres, `{key}` is the run ID, and listing
uses real pagination and filtering instead of scanning files.

### Securing the API

The API is open by default, which is only appropriate on `localhost`. Before
exposing it, set `EVALFORGE_API_KEY` (and, as needed, `EVALFORGE_CORS_ORIGINS`
and `EVALFORGE_RATE_LIMIT_PER_MINUTE`; see [Configuration](#configuration)).

- Clients send `Authorization: Bearer <key>`. The key is compared in constant
  time.
- The rate limit applies before authentication, so failed key guesses are
  throttled too. With Postgres it is counted in the database and shared by
  every replica; without it, each process counts separately.
- Behind a reverse proxy, every request arrives from the proxy's IP. Rate-limit
  at the proxy as well.
- Every response carries an `X-Request-ID`, and each request and run is logged
  as one JSON line on stderr. Human-readable CLI output stays on stdout.

### Docker Compose

`docker-compose.yml` runs the API with Postgres:

```bash
cp .env.example .env    # set POSTGRES_PASSWORD and EVALFORGE_API_KEY
docker compose up --build
```

- Compose refuses to start unless both values are set. Generate them with
  `openssl rand -hex 32`.
- The API is published on `127.0.0.1` only (port `EVALFORGE_PORT`, default
  `8000`). Put a TLS-terminating reverse proxy in front of it to expose it
  further.
- An existing `evalforge-db` volume keeps the password it was created with.
  Change it in the database or remove the volume to match `.env`.

## Configuration

All settings are environment variables, and all are optional.

| Variable | Purpose | Default |
|---|---|---|
| `EVALFORGE_DATABASE_URL` | Postgres connection URL; enables database run storage | unset (file storage only) |
| `EVALFORGE_DB_POOL_MAX_SIZE` | Maximum pooled database connections | `10` |
| `EVALFORGE_API_KEY` | Bearer token required on `/runs*` | unset (API open) |
| `EVALFORGE_CORS_ORIGINS` | Comma-separated browser origins allowed to read the API | unset (no cross-origin reads) |
| `EVALFORGE_RATE_LIMIT_PER_MINUTE` | Requests per minute per client IP | unset (no limit) |
| `EVALFORGE_JUDGE_API_KEY` | API key for the `llm_judge` grader | unset |
| `EVALFORGE_JUDGE_BASE_URL` | OpenAI-compatible endpoint for the judge | `https://api.openai.com/v1` |
| `EVALFORGE_JUDGE_MODEL` | Judge model when the grader config doesn't set one | unset |
| `EVALFORGE_HUMAN_SCORES` | Human score file when the grader config doesn't set one | unset |
| `GIT_BRANCH` | Branch to record on a run outside GitHub Actions | unset |
| `EVALFORGE_PORT` | Host port for the API (Docker Compose only) | `8000` |

## Development

```bash
python3.11 -m venv .venv
source .venv/bin/activate
make install          # hash-pinned dev dependencies, then the package in editable mode
make lint             # ruff + mypy
make test             # pytest
```

| Topic | Details |
|---|---|
| Postgres tests | `tests/test_db.py` runs only when `EVALFORGE_DATABASE_URL` points at a test database |
| Lockfiles | `requirements/{runtime,dev,dev-postgres}.lock` pin every dependency with hashes; run `make lock` ([uv](https://docs.astral.sh/uv/)) after changing dependencies |
| CI | `.github/workflows/evalforge.yml`: lint, types, tests and the demo suite; Postgres-backed tests; package build check |

### Releasing

Releases publish to PyPI from `.github/workflows/release.yml` using Trusted
Publishing, so no API token is stored.

1. Bump `version` in `pyproject.toml` and commit.
2. Push a matching tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. Approve the deployment in the `pypi` environment on GitHub.

The workflow rejects a tag that doesn't match the version, re-runs lint, type
checks and tests, builds the sdist and wheel, and checks them before
uploading. PyPI never accepts the same version twice, so a fix always needs a
new version.

## Roadmap

- Distributed runner workers
- Human review queues
- Richer LLM/NLI groundedness graders
- Environment snapshots and state-based outcome grading
- Provider-native OpenAI and Anthropic adapters
- Statistical confidence intervals and flaky-eval detection
- Prompt/model matrix experiments
- GitHub PR annotations
- An observability UI built on the run schema

## License

MIT. See [LICENSE](LICENSE).
