# Contributing

How to set up EvalForge for development, run the checks, and keep the build
reproducible. To publish a release, see [RELEASING.md](RELEASING.md).

## Setup

Requires Python 3.11+.

```bash
git clone https://github.com/AbhilashSomigari/evalforge.git
cd evalforge
python3.11 -m venv .venv
source .venv/bin/activate
make install
```

`make install` installs the hash-pinned development dependencies from
`requirements/dev.lock`, then the package itself in editable mode.

## Checks

| Command | Runs |
|---|---|
| `make lint` | `ruff check src tests examples` and `mypy src` |
| `make test` | `pytest` |
| `make demo` | The example suite against the demo agent |

CI runs the same checks on pull requests and on pushes to `main`.

### Postgres-backed tests

`tests/test_db.py` is skipped unless `EVALFORGE_DATABASE_URL` points at a test
database. To run it against a throwaway Postgres container:

```bash
pip install --require-hashes -r requirements/dev-postgres.lock
pip install --no-deps -e .
docker run -d --name evalforge-pg-test \
  -e POSTGRES_USER=evalforge -e POSTGRES_PASSWORD=evalforge -e POSTGRES_DB=evalforge \
  -p 5433:5432 postgres:16-alpine
EVALFORGE_DATABASE_URL=postgresql://evalforge:evalforge@localhost:5433/evalforge pytest
```

The tests truncate their tables, so never point them at a database you care
about.

## Example agents

`examples/agents/` contains a deterministic demo agent (used by CI) and two
real LLM agents, `claude_agent.py` and `openai_agent.py`.

- The real agents share their system prompt, tools, and mock order system
  through `support_tools.py`, so their scores on
  `examples/suites/customer_support_live.yaml` are comparable. Change the test
  environment there, never in one agent.
- Running them calls a paid API and needs `pip install anthropic` or
  `pip install openai` plus the matching key. Neither SDK is a package
  dependency. Run one task directly before a full suite to catch setup
  problems cheaply:

  ```bash
  echo '{"id": "refund", "input": "Refund order R200 please."}' \
    | python examples/agents/claude_agent.py
  ```

- Their settings use an `EVAL_` prefix (`EVAL_CLAUDE_MODEL`,
  `EVAL_OPENAI_MODEL`, ...) so they can't collide with variables set by
  other tools, such as `CLAUDE_EFFORT`, which Claude Code exports.

## Dependencies

Every dependency is pinned with hashes in three lockfiles:

| Lockfile | Used by |
|---|---|
| `requirements/runtime.lock` | The Docker image |
| `requirements/dev.lock` | Local development and the CI `eval` job |
| `requirements/dev-postgres.lock` | The CI `db` job and local Postgres testing |

After changing dependencies in `pyproject.toml`, regenerate all three with
`make lock` (requires [uv](https://docs.astral.sh/uv/)) and commit them with
the change. `dev.lock` deliberately excludes the `postgres` extra so CI proves
nothing imports `psycopg` at module level.

## Continuous integration

`.github/workflows/evalforge.yml`:

| Job | Checks |
|---|---|
| `eval` | ruff, mypy, pytest, and the example suite, without a database |
| `db` | Postgres-backed tests against a `postgres:16-alpine` service container |
| `package` | Builds the sdist and wheel and runs `twine check --strict` |

## Writing tests

Tests must not depend on the environment of the machine running them. If code
reads an environment variable, clear it in the test (with `monkeypatch.delenv`)
rather than assuming it's unset. CI sets variables a local shell doesn't, for
example `GITHUB_REF_NAME`, and it sets them differently for branch pushes,
tags, and pull requests.
