from __future__ import annotations

import json
import re
from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Any

from evalforge.models import AgentOutput, GradeResult, GraderSpec, TaskSpec

from .base import Grader


def _result(spec: GraderSpec, score: float, reason: str, **metadata: Any) -> GradeResult:
    score = max(0.0, min(1.0, float(score)))
    return GradeResult(
        grader=spec.name or spec.type,
        score=score,
        passed=score >= spec.pass_threshold,
        reason=reason,
        metadata=metadata,
    )


class ExactMatchGrader(Grader):
    async def grade(self, task: TaskSpec, output: AgentOutput, spec: GraderSpec) -> GradeResult:
        expected = spec.config.get("expected", task.expected_output)
        if expected is None:
            return _result(spec, 0, "No expected output configured")
        case_sensitive = bool(spec.config.get("case_sensitive", False))
        actual = output.output.strip()
        expected = str(expected).strip()
        if not case_sensitive:
            actual, expected = actual.lower(), expected.lower()
        ok = actual == expected
        return _result(spec, 1 if ok else 0, "Exact match" if ok else "Output differed")


class ContainsGrader(Grader):
    async def grade(self, task: TaskSpec, output: AgentOutput, spec: GraderSpec) -> GradeResult:
        values = spec.config.get("values")
        if values is None:
            expected = spec.config.get("expected", task.expected_output)
            values = [expected] if expected is not None else []
        haystack = output.output.lower()
        required = [str(v).lower() for v in values]
        if not required:
            return _result(spec, 0, "No required strings configured")
        hits = sum(v in haystack for v in required)
        score = hits / len(required)
        return _result(spec, score, f"Found {hits}/{len(required)} required strings")


class RegexGrader(Grader):
    async def grade(self, task: TaskSpec, output: AgentOutput, spec: GraderSpec) -> GradeResult:
        pattern = spec.config.get("pattern")
        if not pattern:
            return _result(spec, 0, "No regex pattern configured")
        ok = re.search(pattern, output.output, re.MULTILINE | re.IGNORECASE) is not None
        return _result(spec, 1 if ok else 0, "Pattern matched" if ok else "Pattern not found")


class SimilarityGrader(Grader):
    async def grade(self, task: TaskSpec, output: AgentOutput, spec: GraderSpec) -> GradeResult:
        expected = spec.config.get("expected", task.expected_output)
        if expected is None:
            return _result(spec, 0, "No reference output configured")
        score = SequenceMatcher(None, output.output.lower(), str(expected).lower()).ratio()
        return _result(spec, score, f"Sequence similarity={score:.3f}")


class JsonSchemaLikeGrader(Grader):
    """Small zero-dependency JSON contract grader.

    Validates parseability plus required top-level keys. Full JSON Schema can be added later.
    """

    async def grade(self, task: TaskSpec, output: AgentOutput, spec: GraderSpec) -> GradeResult:
        try:
            parsed = json.loads(output.output)
        except json.JSONDecodeError:
            return _result(spec, 0, "Output is not valid JSON")
        required = spec.config.get("required_keys", [])
        if not isinstance(parsed, dict):
            return _result(spec, 0, "Output JSON is not an object")
        missing = [key for key in required if key not in parsed]
        if not required:
            return _result(spec, 1, "Valid JSON object")
        score = (len(required) - len(missing)) / len(required)
        return _result(spec, score, "JSON contract checked", missing=missing)


class ToolSequenceGrader(Grader):
    async def grade(self, task: TaskSpec, output: AgentOutput, spec: GraderSpec) -> GradeResult:
        expected: Sequence[str] = spec.config.get("expected", task.expected_tools)
        actual = [c.name for c in output.tool_calls]
        if not expected:
            return _result(spec, 1 if not actual else 0, "No tool calls expected", actual=actual)

        mode = spec.config.get("mode", "ordered_subsequence")
        if mode == "exact":
            score = 1.0 if actual == list(expected) else 0.0
        elif mode == "set":
            exp, act = set(expected), set(actual)
            score = len(exp & act) / len(exp) if exp else 1.0
        else:
            # Credit the fraction of expected calls appearing in order.
            i = 0
            for name in actual:
                if i < len(expected) and name == expected[i]:
                    i += 1
            score = i / len(expected)
        return _result(spec, score, f"Expected {list(expected)}, got {actual}", actual=actual)


class ToolArgumentsGrader(Grader):
    async def grade(self, task: TaskSpec, output: AgentOutput, spec: GraderSpec) -> GradeResult:
        checks = spec.config.get("checks", [])
        if not checks:
            return _result(spec, 1, "No tool argument checks configured")
        passed = 0
        details = []
        for check in checks:
            name = check["tool"]
            expected_args = check.get("arguments", {})
            candidates = [c for c in output.tool_calls if c.name == name]
            ok = any(all(c.arguments.get(k) == v for k, v in expected_args.items()) for c in candidates)
            passed += int(ok)
            details.append({"tool": name, "ok": ok, "arguments": expected_args})
        score = passed / len(checks)
        return _result(spec, score, f"Passed {passed}/{len(checks)} argument checks", checks=details)


class CitationGroundednessGrader(Grader):
    """Cheap deterministic proxy for RAG groundedness.

    A production system should pair this with an LLM/NLI judge. This grader checks whether
    configured source IDs are actually cited and optionally penalizes unknown citations.
    """

    async def grade(self, task: TaskSpec, output: AgentOutput, spec: GraderSpec) -> GradeResult:
        source_ids = spec.config.get("source_ids") or task.metadata.get("source_ids", [])
        if not source_ids:
            return _result(spec, 0, "No source_ids configured")
        required = bool(spec.config.get("require_all", False))
        cited = {sid for sid in source_ids if str(sid) in output.output}
        if required:
            score = len(cited) / len(source_ids)
        else:
            score = 1.0 if cited else 0.0
        return _result(spec, score, f"Cited {len(cited)}/{len(source_ids)} known sources", cited=sorted(cited))


class NoForbiddenClaimsGrader(Grader):
    """Regression guard for known hallucination patterns."""

    async def grade(self, task: TaskSpec, output: AgentOutput, spec: GraderSpec) -> GradeResult:
        forbidden = [str(x).lower() for x in spec.config.get("phrases", [])]
        hits = [x for x in forbidden if x in output.output.lower()]
        score = 0.0 if hits else 1.0
        return _result(spec, score, "Forbidden claims detected" if hits else "No forbidden claims", hits=hits)


REGISTRY: dict[str, type[Grader]] = {
    "exact_match": ExactMatchGrader,
    "contains": ContainsGrader,
    "regex": RegexGrader,
    "similarity": SimilarityGrader,
    "json_contract": JsonSchemaLikeGrader,
    "tool_sequence": ToolSequenceGrader,
    "tool_arguments": ToolArgumentsGrader,
    "citation_groundedness": CitationGroundednessGrader,
    "no_forbidden_claims": NoForbiddenClaimsGrader,
}
