.PHONY: install test lint demo api lock

# Hash-pinned lockfiles (one per install profile), universal across platforms
# and Python >= 3.11. Regenerate after changing dependencies in pyproject.toml.
LOCK = uv pip compile pyproject.toml --universal --python-version 3.11 --generate-hashes --quiet

install:
	pip install --require-hashes -r requirements/dev.lock
	pip install --no-deps -e .

test:
	pytest

lint:
	ruff check src tests
	mypy src

demo:
	evalforge run --suite examples/suites/customer_support.yaml --agent "python examples/agents/demo_agent.py" --out runs/demo.json

api:
	evalforge serve --host 0.0.0.0 --port 8000

lock:
	$(LOCK) --extra postgres -o requirements/runtime.lock
	$(LOCK) --extra dev -o requirements/dev.lock
	$(LOCK) --extra dev --extra postgres -o requirements/dev-postgres.lock
