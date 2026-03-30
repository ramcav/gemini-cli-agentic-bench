"""Phase scoring, tool counting, and result aggregation.

Scoring logic:
- Phases 1, 2, 4: LLM-as-judge via the judge module
- Phase 3: Deterministic — exit codes from verification commands
- Tool counting: total across all phases against min_tool_calls
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from judge.judge import (
    JudgeResult,
    evaluate_coverage,
    evaluate_phase_4,
    score_phase_1,
    score_phase_2,
    score_phase_4,
)
from runner.executor import ExecutionResult, check_test_files_exist, run_verification

# Tool types counted toward min_tool_calls.
# Excludes internal lifecycle events (session start/end, model routing, memory operations).
COUNTED_TOOL_TYPES = frozenset({
    "readFile",
    "writeFile",
    "runCommand",
    "searchCode",
    "webFetch",
    "mcpTool",
})


@dataclass
class PhaseScore:
    phase: str
    passed: bool
    failure_mode: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    judge_result: JudgeResult | None = None


@dataclass
class TaskScore:
    task_id: str
    status: str  # "pass" | "fail" | "error"
    failure_modes: list[str] = field(default_factory=list)
    phase_scores: dict[str, PhaseScore] = field(default_factory=dict)
    total_tool_calls: int = 0
    details: dict[str, Any] = field(default_factory=dict)


def count_tool_calls(activity_log: list[dict[str, Any]]) -> int:
    """Count tool calls of tracked types in the activity log."""
    count = 0
    for entry in activity_log:
        tool_type = entry.get("type") or entry.get("tool_type") or entry.get("name", "")
        if tool_type in COUNTED_TOOL_TYPES:
            count += 1
    return count


def score_task(task: dict[str, Any], execution: ExecutionResult) -> TaskScore:
    """Score all phases of an executed task. Returns a complete TaskScore."""
    task_id = task["id"]

    # Handle setup failure
    if execution.setup_failure:
        return TaskScore(
            task_id=task_id,
            status="error",
            failure_modes=["setup_failure"],
            details={"setup_error": execution.setup_failure},
        )

    result = TaskScore(task_id=task_id, status="pass")

    # Count tool calls
    result.total_tool_calls = count_tool_calls(execution.activity_log)
    if result.total_tool_calls < task["min_tool_calls"]:
        result.failure_modes.append("insufficient_tool_use")
        result.details["tool_calls_expected"] = task["min_tool_calls"]
        result.details["tool_calls_actual"] = result.total_tool_calls

    # Phase 1: Requirements elicitation (LLM judge)
    p1_output = execution.phase_outputs.get("phase_1")
    if p1_output:
        try:
            judge_result = score_phase_1(
                feature_request=task["feature_request"],
                expected_questions=task["phase_1_expected_questions"],
                agent_output=p1_output.raw_output,
            )
            if judge_result.is_error:
                result.phase_scores["phase_1"] = PhaseScore(
                    phase="phase_1", passed=False, failure_mode="judge_error",
                    details={"error": judge_result.error}, judge_result=judge_result,
                )
                result.failure_modes.append("judge_error")
            else:
                passed, coverage = evaluate_coverage(judge_result)
                result.phase_scores["phase_1"] = PhaseScore(
                    phase="phase_1", passed=passed,
                    failure_mode=None if passed else "elicitation_failure",
                    details={"coverage": coverage, "parsed": judge_result.parsed},
                    judge_result=judge_result,
                )
                if not passed:
                    result.failure_modes.append("elicitation_failure")
        except Exception as e:
            result.phase_scores["phase_1"] = PhaseScore(
                phase="phase_1", passed=False, failure_mode="judge_error",
                details={"error": str(e)},
            )
            result.failure_modes.append("judge_error")

    # Phase 2: Implementation planning (LLM judge)
    p2_output = execution.phase_outputs.get("phase_2")
    if p2_output:
        answers_text = "\n".join(
            f"- {k}: {v}" for k, v in task["phase_1_answers"].items()
        )
        phase_1_context = (
            f"Questions asked:\n{p1_output.raw_output if p1_output else 'N/A'}\n\n"
            f"Answers:\n{answers_text}"
        )
        try:
            judge_result = score_phase_2(
                feature_request=task["feature_request"],
                phase_1_context=phase_1_context,
                expected_plan_components=task["phase_2_expected_plan_components"],
                agent_output=p2_output.raw_output,
            )
            if judge_result.is_error:
                result.phase_scores["phase_2"] = PhaseScore(
                    phase="phase_2", passed=False, failure_mode="judge_error",
                    details={"error": judge_result.error}, judge_result=judge_result,
                )
                result.failure_modes.append("judge_error")
            else:
                passed, coverage = evaluate_coverage(judge_result)
                result.phase_scores["phase_2"] = PhaseScore(
                    phase="phase_2", passed=passed,
                    failure_mode=None if passed else "planning_failure",
                    details={"coverage": coverage, "parsed": judge_result.parsed},
                    judge_result=judge_result,
                )
                if not passed:
                    result.failure_modes.append("planning_failure")
        except Exception as e:
            result.phase_scores["phase_2"] = PhaseScore(
                phase="phase_2", passed=False, failure_mode="judge_error",
                details={"error": str(e)},
            )
            result.failure_modes.append("judge_error")

    # Phase 3a: Test file existence + verification must fail
    p3a_output = execution.phase_outputs.get("phase_3a")
    if p3a_output:
        missing = check_test_files_exist(execution.workspace, task["phase_3_test_paths"])
        if missing:
            result.phase_scores["phase_3a"] = PhaseScore(
                phase="phase_3a", passed=False, failure_mode="tdd_no_tests_written",
                details={"missing_files": missing},
            )
            result.failure_modes.append("tdd_no_tests_written")
        else:
            exit_code = run_verification(task["phase_3_verification_command"], execution.workspace)
            if exit_code == 0:
                result.phase_scores["phase_3a"] = PhaseScore(
                    phase="phase_3a", passed=False, failure_mode="tdd_skip",
                    details={"exit_code": exit_code, "reason": "Tests passed before implementation — agent may have written passing tests or implemented the feature"},
                )
                result.failure_modes.append("tdd_skip")
            else:
                result.phase_scores["phase_3a"] = PhaseScore(
                    phase="phase_3a", passed=True,
                    details={"exit_code": exit_code},
                )

    # Phase 3b: Verification must pass
    p3b_output = execution.phase_outputs.get("phase_3b")
    if p3b_output:
        exit_code = run_verification(task["phase_3_verification_command"], execution.workspace)
        if exit_code == 0:
            result.phase_scores["phase_3b"] = PhaseScore(
                phase="phase_3b", passed=True,
                details={"exit_code": exit_code},
            )
        else:
            result.phase_scores["phase_3b"] = PhaseScore(
                phase="phase_3b", passed=False, failure_mode="tdd_failure",
                details={"exit_code": exit_code},
            )
            result.failure_modes.append("tdd_failure")

    # Phase 4: Runtime validation (LLM judge)
    p4_output = execution.phase_outputs.get("phase_4")
    if p4_output:
        try:
            judge_result = score_phase_4(
                feature_request=task["feature_request"],
                runtime_check=task["phase_4_runtime_check"],
                runtime_type=task["phase_4_runtime_type"],
                agent_output=p4_output.raw_output,
            )
            if judge_result.is_error:
                result.phase_scores["phase_4"] = PhaseScore(
                    phase="phase_4", passed=False, failure_mode="judge_error",
                    details={"error": judge_result.error}, judge_result=judge_result,
                )
                result.failure_modes.append("judge_error")
            else:
                passed = evaluate_phase_4(judge_result)
                result.phase_scores["phase_4"] = PhaseScore(
                    phase="phase_4", passed=passed,
                    failure_mode=None if passed else "runtime_blind",
                    details={"parsed": judge_result.parsed},
                    judge_result=judge_result,
                )
                if not passed:
                    result.failure_modes.append("runtime_blind")
        except Exception as e:
            result.phase_scores["phase_4"] = PhaseScore(
                phase="phase_4", passed=False, failure_mode="judge_error",
                details={"error": str(e)},
            )
            result.failure_modes.append("judge_error")

    # Determine overall status
    if not result.failure_modes:
        result.status = "pass"
    elif any(fm in ("setup_failure", "judge_error") for fm in result.failure_modes):
        result.status = "error"
    else:
        result.status = "fail"

    return result
