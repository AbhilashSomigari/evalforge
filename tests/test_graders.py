import pytest

from evalforge.graders import run_grader
from evalforge.models import AgentOutput, GraderSpec, TaskSpec, ToolCall


@pytest.mark.asyncio
async def test_tool_sequence_ordered_subsequence():
    task = TaskSpec(id="x", input="x", expected_tools=["search", "buy"])
    output = AgentOutput(output="ok", tool_calls=[ToolCall(name="search"), ToolCall(name="buy")])
    grade = await run_grader(task, output, GraderSpec(type="tool_sequence", name="tool_correctness"))
    assert grade.passed
    assert grade.score == 1


@pytest.mark.asyncio
async def test_forbidden_claims():
    task = TaskSpec(id="x", input="x")
    output = AgentOutput(output="This is guaranteed tomorrow")
    spec = GraderSpec(type="no_forbidden_claims", config={"phrases": ["guaranteed tomorrow"]})
    grade = await run_grader(task, output, spec)
    assert not grade.passed
