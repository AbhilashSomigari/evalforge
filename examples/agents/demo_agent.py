#!/usr/bin/env python3
"""A deterministic toy agent used to prove the EvalForge harness works end-to-end."""

import json
import sys
import time


def main() -> None:
    task = json.load(sys.stdin)
    prompt = task["input"].lower()
    started = time.perf_counter()
    tool_calls = []
    trajectory = [{"type": "message", "role": "user", "content": task["input"]}]

    if "order" in prompt or "refund" in prompt:
        call = {
            "name": "lookup_order",
            "arguments": {"order_id": task["metadata"].get("order_id", "unknown")},
            "result": {"status": "delivered", "amount": 49.99},
            "latency_ms": 7.0,
        }
        tool_calls.append(call)
        trajectory.append({"type": "tool_call", "tool_call": call})

    if "refund" in prompt:
        call = {
            "name": "issue_refund",
            "arguments": {"order_id": task["metadata"].get("order_id", "unknown"), "amount": 49.99},
            "result": {"refund_id": "rf_123", "status": "submitted"},
            "latency_ms": 9.0,
        }
        tool_calls.append(call)
        trajectory.append({"type": "tool_call", "tool_call": call})
        answer = "Refund submitted for $49.99. Reference rf_123. Source: KB-REFUND-1"
    else:
        answer = "Order is delivered. Source: KB-ORDER-1"

    trajectory.append({"type": "message", "role": "assistant", "content": answer})
    latency = (time.perf_counter() - started) * 1000
    json.dump(
        {
            "output": answer,
            "trajectory": trajectory,
            "tool_calls": tool_calls,
            "usage": {"input_tokens": 45, "output_tokens": 22, "cost_usd": 0.0031},
            "model": "demo-deterministic-v1",
            "metadata": {"internal_latency_ms": latency},
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
