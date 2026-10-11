#!/usr/bin/env python3
"""A real tool-using customer-support agent backed by the OpenAI API.

EvalForge runs this once per trial: it reads a TaskSpec JSON object on stdin
and writes an AgentOutput JSON object on stdout. The model only ever sees the
task's `input` (the customer message) - never the grading metadata. The
prompt, tools, and mock order system come from `support_tools.py`, shared
with the other model-backed agents.

Requires `pip install openai` and OPENAI_API_KEY. Optional:
  EVAL_OPENAI_MODEL                    model name (default gpt-4o)
  EVAL_OPENAI_INPUT_PRICE_PER_MTOK     USD per 1M input tokens, for cost reporting (default 2.50)
  EVAL_OPENAI_OUTPUT_PRICE_PER_MTOK    USD per 1M output tokens (default 10.00)
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

TOOLS = [{"type": "function", "function": {**spec, "strict": True}} for spec in TOOL_SPECS]


def main() -> int:
    task = json.load(sys.stdin)
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set", file=sys.stderr)
        return 2

    from openai import OpenAI

    model = os.getenv("EVAL_OPENAI_MODEL", "gpt-4o")
    client = OpenAI()

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task["input"]},
    ]
    trajectory: list[dict[str, Any]] = [{"type": "message", "role": "user", "content": task["input"]}]
    tool_calls: list[dict[str, Any]] = []
    input_tokens = output_tokens = cached_tokens = model_calls = 0
    answer = ""
    served_by = model

    while model_calls < MAX_MODEL_CALLS:
        response = client.chat.completions.create(model=model, messages=messages, tools=TOOLS)
        model_calls += 1
        served_by = response.model
        if response.usage:
            input_tokens += response.usage.prompt_tokens
            output_tokens += response.usage.completion_tokens
            details = response.usage.prompt_tokens_details
            cached_tokens += (details.cached_tokens or 0) if details else 0

        message = response.choices[0].message
        if message.refusal:
            answer = message.refusal
            trajectory.append({"type": "message", "role": "assistant", "content": answer})
            break
        if not message.tool_calls:
            answer = message.content or ""
            trajectory.append({"type": "message", "role": "assistant", "content": answer})
            break

        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.function.name, "arguments": call.function.arguments},
                    }
                    for call in message.tool_calls
                ],
            }
        )
        for call in message.tool_calls:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                record = {"name": call.function.name, "arguments": {}, "result": None,
                          "error": f"Invalid tool arguments: {exc}", "latency_ms": 0.0}
            else:
                record = call_tool(call.function.name, arguments)
            tool_calls.append(record)
            trajectory.append({"type": "tool_call", "tool_call": record})
            messages.append({"role": "tool", "tool_call_id": call.id, "content": tool_result_text(record)})
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
            input_price_per_mtok=float(os.getenv("EVAL_OPENAI_INPUT_PRICE_PER_MTOK", "2.50")),
            output_price_per_mtok=float(os.getenv("EVAL_OPENAI_OUTPUT_PRICE_PER_MTOK", "10.00")),
            metadata={"model_calls": model_calls},
        ),
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
