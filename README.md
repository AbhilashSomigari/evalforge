# EvalForge — CI/CD for AI Agents

EvalForge is an infrastructure-first evaluation harness for **tool-using, multi-step AI agents**. It turns agent behavior into repeatable tests that can run locally or in CI and block a deployment when quality regresses.

> Change a prompt, model, tool schema, or RAG pipeline → run scenarios → capture trajectories → grade outcomes → compare against a baseline → enforce gates.

## Why this exists

Normal unit tests are necessary but insufficient for agents. Agents are non-deterministic, make multiple model calls, use tools, retrieve context, and mutate environments. EvalForge treats the **trajectory and outcome** as first-class test artifacts instead of evaluating only the final string.

## v0.1 capabilities

- YAML evaluation suites and reusable scenarios
- Multiple trials per task + concurrent execution
- Language-agnostic command adapter (JSON over stdin/stdout)
- HTTP adapter for already-running agents
- Full trajectory/tool-call capture
- Deterministic graders: exact match, contains, regex, similarity, JSON contract
- Tool sequence and tool argument correctness
- Citation-based RAG groundedness proxy
- Known-hallucination regression guard
- Optional OpenAI-compatible LLM-as-judge grader
- Human-score ingestion that participates in the same pass/fail pipeline
- Task success, tool correctness, hallucination rate, latency, token and cost aggregation
- Baseline-vs-candidate regression comparison
- Prompt/model A/B runs over the exact same suite
- Absolute and regression-based CI gates
- Trajectory replay / re-grading without paying for another agent run
- FastAPI run-inspection API
- Docker + GitHub Actions example

## Architecture

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

## Run it

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest

evalforge run \
  --suite examples/suites/customer_support.yaml \
  --agent "python examples/agents/demo_agent.py" \
  --out runs/demo.json
```

A failing gate exits with code `2`, which is what blocks a CI job.

## Agent protocol

EvalForge deliberately does **not** force a LangChain/LlamaIndex/custom-agent dependency. For the command adapter, your executable receives one `TaskSpec` JSON object on stdin and returns one `AgentOutput` JSON object on stdout:

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
  "usage": {
    "input_tokens": 100,
    "output_tokens": 35,
    "cost_usd": 0.012
  },
  "model": "your-model"
}
```

That adapter boundary lets the same suite evaluate Python, TypeScript, Java, Go, MCP-based agents, or remote services.

## Suite example

```yaml
name: checkout-agent
trials_per_task: 3
concurrency: 8

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
    graders:
      - type: tool_sequence
        name: tool_correctness
        pass_threshold: 1.0
```

For a regression gate, provide `--baseline runs/main.json`. A quality metric with `op: ">="` is allowed to drop by at most `max_regression`; a cost/latency metric with `op: "<="` is allowed to rise by at most that amount.

## Replay a trajectory

When you improve a grader or rubric, re-score an existing run without spending tokens again:

```bash
evalforge replay \
  --suite examples/suites/customer_support.yaml \
  --run runs/demo.json \
  --out runs/replayed.json
```

## Compare two runs

```bash
evalforge compare --baseline runs/main.json --candidate runs/pr-184.json
```

## Prompt/model A/B test

Run the same task bank against two commands:

```bash
evalforge ab \
  --suite examples/suites/customer_support.yaml \
  --agent-a "python agent_prompt_v11.py" \
  --agent-b "python agent_prompt_v12.py"
```

EvalForge stores both run artifacts and prints metric deltas. The two commands can represent different prompts, models, tool schemas, retrieval strategies, or entire agent implementations.

## Human grading

Human scores can be imported without changing the run pipeline:

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

## Inspect runs over HTTP

```bash
evalforge serve --host 0.0.0.0 --port 8000
# GET /health
# GET /runs?limit=20&offset=0&suite=customer-support-regression
# GET /runs/{key}
```

Interactive FastAPI docs are available at `/docs`.

By default runs are read back from the local `runs/` directory (`{key}` is a
filename). Set `EVALFORGE_DATABASE_URL` to persist runs to Postgres instead —
`evalforge run`/`ab`/`replay` write to it in addition to the local JSON file,
and `/runs`/`/runs/{key}` (`{key}` becomes the run id) query it directly with
real pagination and suite filtering instead of parsing every file in `runs/`
on every request:

```bash
export EVALFORGE_DATABASE_URL=postgresql://evalforge:evalforge@localhost:5432/evalforge
pip install -e ".[postgres]"
```

`docker-compose.yml` runs a Postgres container alongside the API and wires
this automatically.

## LLM-as-judge

Add this grader to a task or suite:

```yaml
- type: llm_judge
  name: policy_quality
  pass_threshold: 0.8
  config:
    model: your-judge-model
    rubric: >-
      Score whether the answer follows the refund policy, cites evidence,
      and avoids claiming actions that did not occur.
```

Then set:

```bash
export EVALFORGE_JUDGE_API_KEY=...
export EVALFORGE_JUDGE_BASE_URL=https://api.openai.com/v1
export EVALFORGE_JUDGE_MODEL=...
```

The implementation is OpenAI-compatible so it can also point to compatible gateways/local inference servers.

## What comes next

The next platform milestones are: distributed workers; dataset/version registry; human review queues; richer LLM/NLI groundedness graders; environment snapshots and state-based outcome grading; provider-native OpenAI/Anthropic adapters; statistical confidence intervals; flaky-eval detection; prompt/model matrix experiments; GitHub PR annotations; and an observability UI built on the same run schema.

## Design principle

**The run artifact is the product.** The UI is only one consumer. Every trial stores the task, outcome-facing output, tool calls, trajectory, grader results, latency, usage and cost so the same evidence can drive CI, debugging, dashboards, human review, and future graders.
