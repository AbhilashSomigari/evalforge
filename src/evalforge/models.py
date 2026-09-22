from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any | None = None
    error: str | None = None
    latency_ms: float | None = None


class TraceEvent(BaseModel):
    type: Literal["message", "tool_call", "tool_result", "state", "error"]
    role: str | None = None
    content: str | None = None
    tool_call: ToolCall | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class AgentOutput(BaseModel):
    output: str
    trajectory: list[TraceEvent] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraderSpec(BaseModel):
    type: str
    name: str | None = None
    weight: float = 1.0
    pass_threshold: float = 1.0
    config: dict[str, Any] = Field(default_factory=dict)


class TaskSpec(BaseModel):
    id: str
    input: str
    expected_output: str | None = None
    expected_tools: list[str] = Field(default_factory=list)
    context: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    graders: list[GraderSpec] = Field(default_factory=list)


class GateSpec(BaseModel):
    metric: str
    op: Literal[">=", ">", "<=", "<"]
    value: float | None = None
    max_regression: float | None = None

    @model_validator(mode="after")
    def validate_threshold(self) -> "GateSpec":
        if self.value is None and self.max_regression is None:
            raise ValueError("Gate must define either value or max_regression")
        return self


class SuiteSpec(BaseModel):
    name: str
    description: str = ""
    trials_per_task: int = 1
    concurrency: int = 4
    graders: list[GraderSpec] = Field(default_factory=list)
    gates: list[GateSpec] = Field(default_factory=list)
    tasks: list[TaskSpec]


def compute_suite_version(spec: SuiteSpec) -> str:
    """Content hash identifying what this suite actually tests.

    Excludes `description` (cosmetic) and `concurrency` (a performance knob,
    not part of what's being tested), so editing either doesn't spuriously
    invalidate baseline comparability. Used to warn when a candidate run is
    compared against a baseline produced by a materially different suite.
    """
    data = spec.model_dump(mode="json")
    data.pop("description", None)
    data.pop("concurrency", None)
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


class GradeResult(BaseModel):
    grader: str
    score: float
    passed: bool
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class TrialResult(BaseModel):
    task_id: str
    trial_index: int
    output: AgentOutput
    grades: list[GradeResult] = Field(default_factory=list)
    success: bool = False
    latency_ms: float = 0.0
    error: str | None = None


class RunSummary(BaseModel):
    task_success: float = 0.0
    tool_correctness: float = 0.0
    grader_score: float = 0.0
    hallucination_rate: float = 0.0
    avg_cost_usd: float = 0.0
    avg_latency_ms: float = 0.0
    avg_input_tokens: float = 0.0
    avg_output_tokens: float = 0.0
    total_cost_usd: float = 0.0
    total_judge_input_tokens: int = 0
    total_judge_output_tokens: int = 0
    trials: int = 0
    failed_trials: int = 0


CURRENT_SCHEMA_VERSION = 1


class EvalRun(BaseModel):
    schema_version: int = CURRENT_SCHEMA_VERSION
    id: str = Field(default_factory=lambda: str(uuid4()))
    suite_name: str
    created_at: datetime = Field(default_factory=utc_now)
    git_sha: str | None = None
    git_branch: str | None = None
    # "" (not "" | None) for old run files predating this field, and for tests
    # that build EvalRun by hand: absent means "unknown", not "no suite".
    suite_version: str = ""
    agent_name: str
    results: list[TrialResult]
    summary: RunSummary
    metadata: dict[str, Any] = Field(default_factory=dict)
