#!/usr/bin/env python3
"""A real tool-using customer-support agent backed by the OpenAI API.

EvalForge runs this once per trial: it reads a TaskSpec JSON object on stdin
and writes an AgentOutput JSON object on stdout. The model only ever sees the
task's `input` (the customer message) - never the grading metadata.

The tools act on an in-memory mock order system, so no real orders or refunds
are touched.

Requires `pip install openai` and OPENAI_API_KEY. Optional:
  OPENAI_MODEL                    model name (default gpt-4o)
  OPENAI_INPUT_PRICE_PER_MTOK     USD per 1M input tokens, for cost reporting (default 2.50)
  OPENAI_OUTPUT_PRICE_PER_MTOK    USD per 1M output tokens (default 10.00)
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

MAX_MODEL_CALLS = 8

SYSTEM_PROMPT = """You are a customer support agent for an online store.

Use the tools to look up orders and to issue refunds; never claim an action
you did not perform with a tool. Before issuing a refund, look the order up
to confirm it exists and to get the amount.

Tool results include a `source` knowledge-base ID. End your reply with the
IDs of the sources you relied on, in the form "Source: <ID>".

Reply to the customer briefly and confirm what you did, including amounts
and reference numbers."""

ORDERS: dict[str, dict[str, Any]] = {
    "A100": {"status": "delivered", "amount": 49.99, "item": "Wireless mouse"},
    "R200": {"status": "delivered", "amount": 49.99, "item": "USB-C hub"},
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "Look up an order's status, item, and amount by order ID.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string", "description": "Order ID, e.g. A100"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "issue_refund",
            "description": "Issue a refund for an order. Only call this after looking the order up.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "Order ID to refund"},
                    "amount": {"type": "number", "description": "Amount to refund in USD"},
                },
                "required": ["order_id", "amount"],
                "additionalProperties": False,
            },
        },
    },
]


def lookup_order(order_id: str) -> dict[str, Any]:
    order = ORDERS.get(order_id.upper())
    if order is None:
        return {"error": f"No order found with ID {order_id}", "source": "KB-ORDER-1"}
    return {"order_id": order_id.upper(), **order, "source": "KB-ORDER-1"}


def issue_refund(order_id: str, amount: float) -> dict[str, Any]:
    order = ORDERS.get(order_id.upper())
    if order is None:
        return {"error": f"No order found with ID {order_id}", "source": "KB-REFUND-1"}
    if amount > order["amount"]:
        return {"error": f"Refund exceeds order total of {order['amount']}", "source": "KB-REFUND-1"}
    return {
        "refund_id": f"rf_{order_id.lower()}",
        "order_id": order_id.upper(),
        "amount": amount,
        "status": "submitted",
        "source": "KB-REFUND-1",
    }


TOOL_FUNCTIONS = {"lookup_order": lookup_order, "issue_refund": issue_refund}


def run_tool(name: str, raw_arguments: str) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
    """Returns (arguments, result, error)."""
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        return {}, None, f"Invalid tool arguments: {exc}"
    function = TOOL_FUNCTIONS.get(name)
    if function is None:
        return arguments, None, f"Unknown tool: {name}"
    try:
        return arguments, function(**arguments), None
    except TypeError as exc:
        return arguments, None, f"Bad arguments for {name}: {exc}"


def main() -> int:
    task = json.load(sys.stdin)
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set", file=sys.stderr)
        return 2

    from openai import OpenAI

    model = os.getenv("OPENAI_MODEL", "gpt-4o")
    input_price = float(os.getenv("OPENAI_INPUT_PRICE_PER_MTOK", "2.50"))
    output_price = float(os.getenv("OPENAI_OUTPUT_PRICE_PER_MTOK", "10.00"))
    client = OpenAI()

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task["input"]},
    ]
    trajectory: list[dict[str, Any]] = [{"type": "message", "role": "user", "content": task["input"]}]
    tool_calls: list[dict[str, Any]] = []
    input_tokens = output_tokens = cached_tokens = model_calls = 0
    answer = ""

    while model_calls < MAX_MODEL_CALLS:
        response = client.chat.completions.create(model=model, messages=messages, tools=TOOLS)
        model_calls += 1
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
            started = time.perf_counter()
            arguments, result, error = run_tool(call.function.name, call.function.arguments)
            record = {
                "name": call.function.name,
                "arguments": arguments,
                "result": result,
                "error": error,
                "latency_ms": (time.perf_counter() - started) * 1000,
            }
            tool_calls.append(record)
            trajectory.append({"type": "tool_call", "tool_call": record})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result if error is None else {"error": error}),
                }
            )
    else:
        trajectory.append(
            {"type": "error", "content": f"Stopped after {MAX_MODEL_CALLS} model calls without a final answer"}
        )

    json.dump(
        {
            "output": answer,
            "trajectory": trajectory,
            "tool_calls": tool_calls,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_tokens": cached_tokens,
                "cost_usd": (input_tokens * input_price + output_tokens * output_price) / 1_000_000,
            },
            "model": model,
            "metadata": {"model_calls": model_calls},
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
