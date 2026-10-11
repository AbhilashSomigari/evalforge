#!/usr/bin/env python3
"""A real tool-using customer-support agent backed by the Claude API.

EvalForge runs this once per trial: it reads a TaskSpec JSON object on stdin
and writes an AgentOutput JSON object on stdout. The model only ever sees the
task's `input` (the customer message) - never the grading metadata. The
prompt, tools, and mock order system come from `support_tools.py`, shared
with the other model-backed agents.

Server-side refusal fallbacks are deliberately not enabled: they would let a
different model answer some trials, so the score would no longer measure the
model under test. A refusal is recorded and fails that trial instead.

Requires `pip install anthropic` and ANTHROPIC_API_KEY. Optional (prefixed
EVAL_ so they can't collide with variables Claude Code sets, such as
CLAUDE_EFFORT):
  EVAL_CLAUDE_MODEL                    model ID (default claude-opus-5-5)
  EVAL_CLAUDE_EFFORT                   low | medium | high | xhigh | max (default medium)
  EVAL_CLAUDE_INPUT_PRICE_PER_MTOK     USD per 1M input tokens, for cost reporting (default 4.00)
  EVAL_CLAUDE_OUTPUT_PRICE_PER_MTOK    USD per 1M output tokens (default 20.00)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from support_tools import (
    MAX_MODEL_CALLS,
    SYSTEM_PROMPT,
    TOOL_SPECS,
    agent_output,
    call_tool,
    tool_result_text,
)

TOOLS = [
    {
        "name": spec["name"],
        "description": spec["description"],
        "input_schema": spec["parameters"],
        "strict": True,
    }
    for spec in TOOL_SPECS
]


def main() -> int:
    task = json.load(sys.stdin)
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2

    import anthropic

    model = os.getenv("EVAL_CLAUDE_MODEL", "claude-opus-5-5")
    effort = os.getenv("EVAL_CLAUDE_EFFORT", "medium")
    client = anthropic.Anthropic()

    messages: list[dict[str, Any]] = [{"role": "user", "content": task["input"]}]
    trajectory: list[dict[str, Any]] = [{"type": "message", "role": "user", "content": task["input"]}]
    tool_calls: list[dict[str, Any]] = []
    input_tokens = output_tokens = cached_tokens = model_calls = 0
    answer = ""
    served_by = model
    stop_reason: str | None = None

    while model_calls < MAX_MODEL_CALLS:
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            output_config={"effort": effort},
        )
        model_calls += 1
        served_by = response.model
        stop_reason = response.stop_reason
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        cached_tokens += response.usage.cache_read_input_tokens or 0

        text = "".join(block.text for block in response.content if block.type == "text")
        tool_uses = [block for block in response.content if block.type == "tool_use"]

        if response.stop_reason == "refusal":
            answer = text
            trajectory.append(
                {"type": "error", "content": "Model refused", "metadata": {"stop_details": str(response.stop_details)}}
            )
            break
        if response.stop_reason != "tool_use" or not tool_uses:
            answer = text
            trajectory.append({"type": "message", "role": "assistant", "content": answer})
            break

        # Append the full content (including thinking blocks), unchanged.
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for tool_use in tool_uses:
            record = call_tool(tool_use.name, dict(tool_use.input))
            tool_calls.append(record)
            trajectory.append({"type": "tool_call", "tool_call": record})
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": tool_result_text(record),
                    "is_error": record["error"] is not None,
                }
            )
        messages.append({"role": "user", "content": results})
    else:
        trajectory.append(
            {"type": "error", "content": f"Stopped after {MAX_MODEL_CALLS} model calls without a final answer"}
        )

    json.dump(
        agent_output(
            answer,
            trajectory,
            tool_calls,
            model=served_by,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            input_price_per_mtok=float(os.getenv("EVAL_CLAUDE_INPUT_PRICE_PER_MTOK", "4.00")),
            output_price_per_mtok=float(os.getenv("EVAL_CLAUDE_OUTPUT_PRICE_PER_MTOK", "20.00")),
            metadata={"model_calls": model_calls, "effort": effort, "final_stop_reason": stop_reason},
        ),
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
