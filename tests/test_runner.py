import pytest

from evalforge.adapters.base import AgentAdapter
from evalforge.models import AgentOutput, GraderSpec, SuiteSpec, TaskSpec, ToolCall
from evalforge.runner import EvalRunner


class FakeAgent(AgentAdapter):
    name = "fake"

    async def run(self, task: TaskSpec) -> AgentOutput:
        return AgentOutput(output="done", tool_calls=[ToolCall(name="search")])


@pytest.mark.asyncio
async def test_runner_summary():
    suite = SuiteSpec(
        name="test",
        graders=[],
        tasks=[TaskSpec(
            id="t1",
            input="go",
            expected_tools=["search"],
            graders=[GraderSpec(type="tool_sequence", name="tool_correctness")],
        )],
    )
    run = await EvalRunner(suite, FakeAgent()).run()
    assert run.summary.task_success == 1
    assert run.summary.tool_correctness == 1
