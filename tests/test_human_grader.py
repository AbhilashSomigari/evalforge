import json

import pytest

from evalforge.graders import run_grader
from evalforge.models import AgentOutput, GraderSpec, TaskSpec


@pytest.mark.asyncio
async def test_human_grader(tmp_path):
    path = tmp_path / "scores.json"
    path.write_text(json.dumps({"t1": {"score": 0.9, "reason": "good"}}))
    grade = await run_grader(
        TaskSpec(id="t1", input="x"),
        AgentOutput(output="y"),
        GraderSpec(type="human", pass_threshold=0.8, config={"file": str(path)}),
    )
    assert grade.passed
    assert grade.score == 0.9
