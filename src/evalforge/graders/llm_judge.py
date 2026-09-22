from __future__ import annotations

import json
import os

import httpx

from evalforge.models import AgentOutput, GradeResult, GraderSpec, TaskSpec

from .base import Grader


class OpenAICompatibleJudgeGrader(Grader):
    """Optional model-based grader against an OpenAI-compatible /chat/completions endpoint.

    The judge must return JSON: {"score": 0..1, "reason": "..."}.
    Keeping this provider-neutral makes EvalForge usable with hosted or local models.
    """

    async def grade(self, task: TaskSpec, output: AgentOutput, spec: GraderSpec) -> GradeResult:
        base_url = spec.config.get("base_url", os.getenv("EVALFORGE_JUDGE_BASE_URL", "https://api.openai.com/v1"))
        # API key is env-var-only: suite YAML files are typically committed to a repo,
        # and spec.config is a bad place for a secret to end up.
        api_key = os.getenv("EVALFORGE_JUDGE_API_KEY")
        model = spec.config.get("model", os.getenv("EVALFORGE_JUDGE_MODEL"))
        if not api_key or not model:
            return GradeResult(
                grader=spec.name or spec.type,
                score=0,
                passed=False,
                reason="LLM judge not configured (set EVALFORGE_JUDGE_API_KEY and EVALFORGE_JUDGE_MODEL)",
            )

        rubric = spec.config.get(
            "rubric",
            "Score whether the agent answer correctly completes the user task without unsupported claims.",
        )
        prompt = f"""You are an evaluation grader. Return ONLY JSON with keys score and reason.
Score must be a number from 0 to 1.

RUBRIC:
{rubric}

TASK:
{task.input}

REFERENCE:
{task.expected_output or '(none)'}

AGENT OUTPUT:
{output.output}
"""
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        # A judge call is an external dependency the run doesn't control (rate limits,
        # provider outages, malformed responses). Failing here must degrade this one
        # grade, not take down the whole suite's grading pass.
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{base_url.rstrip('/')}/chat/completions", headers=headers, json=payload
                )
                response.raise_for_status()
                body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            score = max(0.0, min(1.0, float(parsed["score"])))
        except Exception as exc:
            return GradeResult(
                grader=spec.name or spec.type,
                score=0.0,
                passed=False,
                reason=f"LLM judge call failed: {type(exc).__name__}: {exc}",
            )

        usage = body.get("usage") or {}
        return GradeResult(
            grader=spec.name or spec.type,
            score=score,
            passed=score >= spec.pass_threshold,
            reason=str(parsed.get("reason", "")),
            metadata={
                "judge_model": model,
                # Token counts only: with no pricing table for arbitrary OpenAI-compatible
                # endpoints, a fabricated dollar figure would be worse than none.
                "judge_input_tokens": usage.get("prompt_tokens", 0),
                "judge_output_tokens": usage.get("completion_tokens", 0),
            },
        )
