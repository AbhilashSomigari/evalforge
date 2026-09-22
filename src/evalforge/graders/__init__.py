from evalforge.models import AgentOutput, GradeResult, GraderSpec, TaskSpec

from .deterministic import REGISTRY
from .human import HumanScoreGrader
from .llm_judge import OpenAICompatibleJudgeGrader


async def run_grader(task: TaskSpec, output: AgentOutput, spec: GraderSpec) -> GradeResult:
    if spec.type == "llm_judge":
        return await OpenAICompatibleJudgeGrader().grade(task, output, spec)
    if spec.type == "human":
        return await HumanScoreGrader().grade(task, output, spec)
    cls = REGISTRY.get(spec.type)
    if cls is None:
        raise ValueError(f"Unknown grader type: {spec.type}")
    return await cls().grade(task, output, spec)


__all__ = ["run_grader"]
