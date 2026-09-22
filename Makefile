.PHONY: install test demo api
install:
	pip install -e ".[dev]"

test:
	pytest

demo:
	evalforge run --suite examples/suites/customer_support.yaml --agent "python examples/agents/demo_agent.py" --out runs/demo.json

api:
	evalforge serve --host 0.0.0.0 --port 8000
