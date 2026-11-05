import json

import httpx
import pytest

from evalforge_agents.config import load_suite
from evalforge_agents.graders import llm_judge, run_grader
from evalforge_agents.models import AgentOutput, GraderSpec, TaskSpec

JUDGE_ENV = ("EVALFORGE_JUDGE_API_KEY", "EVALFORGE_JUDGE_BASE_URL", "EVALFORGE_JUDGE_MODEL")


@pytest.fixture(autouse=True)
def _no_ambient_judge_env(monkeypatch):
    # A judge key or endpoint set in the developer's shell must not leak into
    # these tests (or let them make real network calls).
    for var in JUDGE_ENV:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def judge_requests(monkeypatch):
    """Route the judge's HTTP calls to a fake endpoint and record them."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"score": 0.9, "reason": "good"})}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 8},
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_judge.httpx,
        "AsyncClient",
        lambda *args, **kwargs: real_client(*args, transport=httpx.MockTransport(handler), **kwargs),
    )
    return requests


TASK = TaskSpec(id="t", input="Refund order R200")
OUTPUT = AgentOutput(output="Refunded $49.99.")


def test_suite_with_judge_base_url_is_rejected_at_load(tmp_path):
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        "name: s\n"
        "tasks:\n"
        "  - id: t\n"
        "    input: hi\n"
        "    graders:\n"
        "      - type: llm_judge\n"
        "        config: {base_url: 'https://attacker.example/v1'}\n"
    )
    with pytest.raises(ValueError, match="EVALFORGE_JUDGE_BASE_URL"):
        load_suite(suite)


def test_base_url_is_only_rejected_for_the_judge():
    # Other graders' config keys are their own business.
    assert GraderSpec(type="contains", config={"base_url": "x"}).config == {"base_url": "x"}


@pytest.mark.asyncio
async def test_judge_sends_the_key_to_the_env_endpoint(monkeypatch, judge_requests):
    monkeypatch.setenv("EVALFORGE_JUDGE_API_KEY", "test-key")
    monkeypatch.setenv("EVALFORGE_JUDGE_BASE_URL", "https://judge.internal/v1/")
    spec = GraderSpec(type="llm_judge", name="quality", pass_threshold=0.8, config={"model": "m"})

    grade = await run_grader(TASK, OUTPUT, spec)

    assert [str(r.url) for r in judge_requests] == ["https://judge.internal/v1/chat/completions"]
    assert judge_requests[0].headers["Authorization"] == "Bearer test-key"
    assert (grade.score, grade.passed, grade.reason) == (0.9, True, "good")
    assert grade.metadata["judge_input_tokens"] == 120


@pytest.mark.asyncio
async def test_judge_defaults_to_openai_when_no_endpoint_is_set(monkeypatch, judge_requests):
    monkeypatch.setenv("EVALFORGE_JUDGE_API_KEY", "test-key")
    await run_grader(TASK, OUTPUT, GraderSpec(type="llm_judge", config={"model": "m"}))
    assert str(judge_requests[0].url) == "https://api.openai.com/v1/chat/completions"


@pytest.mark.asyncio
async def test_judge_ignores_config_base_url_even_if_validation_is_bypassed(monkeypatch, judge_requests):
    monkeypatch.setenv("EVALFORGE_JUDGE_API_KEY", "test-key")
    # model_construct skips validators, as a library caller might.
    spec = GraderSpec.model_construct(
        type="llm_judge", name=None, weight=1.0, pass_threshold=1.0,
        config={"model": "m", "base_url": "https://attacker.example/v1"},
    )
    await run_grader(TASK, OUTPUT, spec)
    assert [r.url.host for r in judge_requests] == ["api.openai.com"]


@pytest.mark.asyncio
async def test_judge_without_key_fails_the_grade_without_a_request(judge_requests):
    grade = await run_grader(TASK, OUTPUT, GraderSpec(type="llm_judge", config={"model": "m"}))
    assert not grade.passed
    assert "not configured" in grade.reason
    assert judge_requests == []
