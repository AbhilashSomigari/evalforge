"""Shared test environment for the example support agents.

Every model-backed agent in this directory imports its system prompt, tool
definitions, and mock order system from here, so a score difference between
two agents comes from the model, not from different instructions or data.
"""

from __future__ import annotations

import json
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

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "lookup_order",
        "description": "Look up an order's status, item, and amount by order ID.",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "Order ID, e.g. A100"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "issue_refund",
        "description": "Issue a refund for an order. Only call this after looking the order up.",
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


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one tool call and return the record EvalForge stores for it."""
    started = time.perf_counter()
    result: dict[str, Any] | None = None
    error: str | None = None
    function = TOOL_FUNCTIONS.get(name)
    if function is None:
        error = f"Unknown tool: {name}"
    else:
        try:
            result = function(**arguments)
        except TypeError as exc:
            error = f"Bad arguments for {name}: {exc}"
    return {
        "name": name,
        "arguments": arguments,
        "result": result,
        "error": error,
        "latency_ms": (time.perf_counter() - started) * 1000,
    }


def tool_result_text(record: dict[str, Any]) -> str:
    """What the model sees as the tool's output."""
    if record["error"] is not None:
        return json.dumps({"error": record["error"]})
    return json.dumps(record["result"])


def agent_output(
    answer: str,
    trajectory: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    input_price_per_mtok: float,
    output_price_per_mtok: float,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "output": answer,
        "trajectory": trajectory,
        "tool_calls": tool_calls,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "cost_usd": (input_tokens * input_price_per_mtok + output_tokens * output_price_per_mtok)
            / 1_000_000,
        },
        "model": model,
        "metadata": metadata,
    }
