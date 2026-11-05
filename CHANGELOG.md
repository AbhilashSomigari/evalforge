# Changelog

Notable changes to `evalforge-agents`. Versions follow the PyPI releases.

## Unreleased

### Changed

- Exit code `2` now means only that a gate failed. Command-line usage errors
  (an unknown or missing option, a bad value, `--agent` together with `--url`,
  an unknown command) exit `1` like other input errors; they used to exit `2`,
  so CI couldn't tell a blocked change from a misconfigured step. The error
  messages are unchanged.

### Security

- The `llm_judge` endpoint comes only from `EVALFORGE_JUDGE_BASE_URL`. A suite
  could previously set `config.base_url`, and the judge sent
  `EVALFORGE_JUDGE_API_KEY` to that URL, so a change to a committed suite could
  send the key to any host. **Breaking:** a suite that sets `base_url` for an
  `llm_judge` grader now fails to load, with a message pointing to
  `EVALFORGE_JUDGE_BASE_URL`.

## 0.2.1 — 2026-11-05

### Fixed

- `evalforge replay` no longer turns errored trials into passes. A trial whose
  agent crashed or timed out stays failed with no grades, as `run` records it;
  before, re-grading the empty placeholder output could pass it.
- `evalforge replay` writes a new run instead of reusing the original's ID, so
  with Postgres it no longer overwrites the original run. The replay records
  the original's ID in `metadata.replayed_from` and keeps its `created_at`,
  git SHA, and branch.

## 0.2.0 — 2026-11-04

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
- `examples/suites/customer_support_hard.yaml`: seven harder tasks for the
  same agents: an already-refunded order, an instruction injected through a
  tool result, a partial refund, a customer naming the wrong item, a cancel
  request with no cancel tool, a delivery date the tools don't provide, and a
  refund plus a status check in one message. The mock order system gains
  orders P300, C400, and N500 for them.

### Fixed

- `evalforge replay` exits with a clear error naming the missing tasks when
  the run has trials for tasks the suite no longer contains, instead of
  crashing with a `KeyError` traceback.

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
