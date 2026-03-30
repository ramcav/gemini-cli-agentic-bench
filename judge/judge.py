"""LLM-as-judge for scoring phases 1, 2, and 4.

Uses the Anthropic SDK to call the configured judge model. Returns structured
scores or a judge_error sentinel when the model returns malformed JSON.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import anthropic

from judge.prompts import PHASE_1_TEMPLATE, PHASE_2_TEMPLATE, PHASE_4_TEMPLATE


@dataclass
class JudgeResult:
    phase: int
    raw_response: str
    parsed: dict[str, Any] | None
    error: str | None

    @property
    def is_error(self) -> bool:
        return self.error is not None


def _get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _get_model() -> str:
    return os.environ.get("JUDGE_MODEL", "claude-sonnet-4-6")


def _get_threshold() -> float:
    return float(os.environ.get("JUDGE_PASS_THRESHOLD", "0.6"))


def _call_judge(prompt: str) -> str:
    client = _get_client()
    model = _get_model()
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _parse_json(raw: str) -> dict[str, Any]:
    """Parse JSON from the judge response, stripping markdown fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # drop opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


def score_phase_1(
    feature_request: str,
    expected_questions: list[str],
    agent_output: str,
) -> JudgeResult:
    prompt = PHASE_1_TEMPLATE.format(
        feature_request=feature_request,
        expected_questions="\n".join(f"- {q}" for q in expected_questions),
        agent_output=agent_output,
    )
    raw = _call_judge(prompt)
    try:
        parsed = _parse_json(raw)
        return JudgeResult(phase=1, raw_response=raw, parsed=parsed, error=None)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        return JudgeResult(phase=1, raw_response=raw, parsed=None, error=str(e))


def score_phase_2(
    feature_request: str,
    phase_1_context: str,
    expected_plan_components: list[str],
    agent_output: str,
) -> JudgeResult:
    prompt = PHASE_2_TEMPLATE.format(
        feature_request=feature_request,
        phase_1_context=phase_1_context,
        expected_plan_components="\n".join(f"- {c}" for c in expected_plan_components),
        agent_output=agent_output,
    )
    raw = _call_judge(prompt)
    try:
        parsed = _parse_json(raw)
        return JudgeResult(phase=2, raw_response=raw, parsed=parsed, error=None)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        return JudgeResult(phase=2, raw_response=raw, parsed=None, error=str(e))


def score_phase_4(
    feature_request: str,
    runtime_check: str,
    runtime_type: str,
    agent_output: str,
) -> JudgeResult:
    prompt = PHASE_4_TEMPLATE.format(
        feature_request=feature_request,
        runtime_check=runtime_check,
        runtime_type=runtime_type,
        agent_output=agent_output,
    )
    raw = _call_judge(prompt)
    try:
        parsed = _parse_json(raw)
        return JudgeResult(phase=4, raw_response=raw, parsed=parsed, error=None)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        return JudgeResult(phase=4, raw_response=raw, parsed=None, error=str(e))


def evaluate_coverage(result: JudgeResult) -> tuple[bool, float]:
    """Check if a phase 1 or 2 judge result meets the pass threshold.

    Returns (passed, coverage_ratio).
    """
    if result.is_error or result.parsed is None:
        return False, 0.0
    coverage = result.parsed.get("coverage", 0.0)
    threshold = _get_threshold()
    return coverage >= threshold, coverage


def evaluate_phase_4(result: JudgeResult) -> bool:
    """Check if a phase 4 judge result is a pass."""
    if result.is_error or result.parsed is None:
        return False
    return result.parsed.get("pass", False)
