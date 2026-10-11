# Changelog

Notable changes to `evalforge-agents`. Versions follow the PyPI releases.

## Unreleased

### Added

- `evalforge show RUN`: per-task results (pass rate, latency, cost, tool-call
  sequences, failing graders). `--task ID` shows every trial's tool calls,
  failed grades, and full answer; `--json` prints machine-readable output.
- `evalforge compare` also prints a per-task pass-rate table, so a gain on one
  task and a loss on another can't cancel out in the totals.
- Example agents backed by real LLMs: `examples/agents/claude_agent.py`
  (Claude Opus 5.5) and `examples/agents/openai_agent.py` (gpt-4o). Both share
  one system prompt, tool set, and mock order system (`support_tools.py`).
- `examples/suites/customer_support_live.yaml`: eight customer-support tasks,
  including unknown and missing order IDs, pressure to over-promise or skip
  checks, an inflated refund amount, and a multi-order request.

### Changed

- The README is reorganized for users, with reference tables. Development and
  release instructions moved to `CONTRIBUTING.md` and `RELEASING.md`.
- Example agents are linted in CI.

## 0.1.0 — 2026-10-11

First release on PyPI.

- YAML suites with multiple trials per task and concurrent execution.
- Command (stdin/stdout JSON) and HTTP agent adapters, with opt-in retries.
- Graders: exact match, contains, regex, similarity, JSON contract, tool
  sequence and arguments, citation groundedness, forbidden claims,
  LLM-as-judge, and human scores.
- Absolute and regression CI gates. `--baseline-branch` finds the latest run
  of the same suite on a branch; a regression gate without a baseline fails.
- `run`, `ab`, `compare`, `replay`, `inspect`, and `serve` commands.
- Versioned JSON run artifacts, optionally stored in Postgres.
- Run API with bearer auth, CORS allowlist, rate limiting, and structured
  JSON logs. Docker image and Docker Compose setup.
