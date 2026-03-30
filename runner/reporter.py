"""Results writing: per-task JSON, aggregate summary JSON + markdown, live terminal output."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runner.executor import ExecutionResult
from runner.scorer import TaskScore


def _serialize_phase_score(ps: Any) -> dict[str, Any]:
    """Convert a PhaseScore to a JSON-serializable dict."""
    d: dict[str, Any] = {
        "phase": ps.phase,
        "passed": ps.passed,
        "failure_mode": ps.failure_mode,
        "details": ps.details,
    }
    if ps.judge_result:
        d["judge_raw_response"] = ps.judge_result.raw_response
        d["judge_error"] = ps.judge_result.error
    return d


def write_task_result(
    task: dict[str, Any],
    score: TaskScore,
    execution: ExecutionResult,
    output_dir: Path,
) -> Path:
    """Write a single task result to results/{task_id}_{timestamp}.json."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{score.task_id}_{timestamp}.json"
    output_path = output_dir / filename

    result = {
        "task_id": score.task_id,
        "timestamp": timestamp,
        "status": score.status,
        "failure_modes": score.failure_modes,
        "failure_mode_category": task.get("failure_mode_category"),
        "difficulty": task.get("difficulty"),
        "total_tool_calls": score.total_tool_calls,
        "min_tool_calls": task.get("min_tool_calls"),
        "phase_scores": {
            k: _serialize_phase_score(v) for k, v in score.phase_scores.items()
        },
        "phase_outputs": {
            k: {
                "phase": v.phase,
                "raw_output": v.raw_output,
                "truncated": v.truncated,
                "exit_code": v.exit_code,
            }
            for k, v in execution.phase_outputs.items()
        },
        "activity_log": execution.activity_log,
        "workspace": execution.workspace,
        "setup_failure": execution.setup_failure,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    return output_path


def print_task_progress(task_id: str, score: TaskScore) -> None:
    """Print live phase-by-phase progress to the terminal."""
    parts = [f"[{task_id}]"]

    phase_labels = {
        "phase_1": "Phase 1 (Elicitation)",
        "phase_2": "Phase 2 (Planning)",
        "phase_3a": "Phase 3a (Write Tests)",
        "phase_3b": "Phase 3b (Implement)",
        "phase_4": "Phase 4 (Runtime)",
    }

    for phase_key, label in phase_labels.items():
        ps = score.phase_scores.get(phase_key)
        if ps is None:
            parts.append(f"{label}: SKIP")
            continue

        status = "PASS" if ps.passed else "FAIL"
        detail = ""

        if ps.judge_result and ps.judge_result.parsed:
            parsed = ps.judge_result.parsed
            if "coverage" in parsed:
                total_key = "total_surfaced" if "total_surfaced" in parsed else "total_addressed"
                total_expected = parsed.get("total_expected", "?")
                total_hit = parsed.get(total_key, "?")
                detail = f" ({total_hit}/{total_expected} expected items)"
            elif "rationale" in parsed:
                rationale = parsed["rationale"][:80]
                detail = f" ({rationale})"
        elif ps.failure_mode:
            detail = f" ({ps.failure_mode})"

        parts.append(f"{label}: {status}{detail}")

    parts.append(f"Tool calls: {score.total_tool_calls}")
    parts.append(f"Status: {score.status.upper()}")

    print(" | ".join(parts))


def write_summary(
    scores: list[tuple[dict[str, Any], TaskScore]],
    output_dir: Path,
) -> tuple[Path, Path]:
    """Write aggregate summary as JSON and markdown. Returns (json_path, md_path)."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"summary_{timestamp}.json"
    md_path = output_dir / f"summary_{timestamp}.md"

    # Exclude error results from pass rate calculations
    non_error = [(t, s) for t, s in scores if s.status != "error"]
    total = len(non_error)
    total_all = len(scores)
    errors = total_all - total

    overall_pass = sum(1 for _, s in non_error if s.status == "pass")
    overall_rate = overall_pass / total if total > 0 else 0.0

    # Per-phase pass rates
    phase_keys = ["phase_1", "phase_2", "phase_3a", "phase_3b", "phase_4"]
    phase_rates: dict[str, float] = {}
    for pk in phase_keys:
        scored = [(t, s) for t, s in non_error if pk in s.phase_scores]
        passed = sum(1 for _, s in scored if s.phase_scores[pk].passed)
        phase_rates[pk] = passed / len(scored) if scored else 0.0

    # Per-difficulty pass rates
    difficulty_rates: dict[str, float] = {}
    for diff in ("easy", "medium", "hard"):
        d_tasks = [(t, s) for t, s in non_error if t.get("difficulty") == diff]
        d_passed = sum(1 for _, s in d_tasks if s.status == "pass")
        difficulty_rates[diff] = d_passed / len(d_tasks) if d_tasks else 0.0

    # Per-failure-mode rates
    failure_mode_counts: dict[str, int] = {}
    for _, s in non_error:
        for fm in s.failure_modes:
            failure_mode_counts[fm] = failure_mode_counts.get(fm, 0) + 1

    # Tool call stats
    tool_counts = [s.total_tool_calls for _, s in non_error]
    avg_tools = sum(tool_counts) / len(tool_counts) if tool_counts else 0.0

    # Ranked tasks by difficulty (hardest first = lowest pass rate)
    task_pass = {t["id"]: (1.0 if s.status == "pass" else 0.0) for t, s in non_error}
    ranked = sorted(task_pass.items(), key=lambda x: x[1])

    summary_data = {
        "timestamp": timestamp,
        "total_tasks": total_all,
        "total_scored": total,
        "total_errors": errors,
        "overall_pass_rate": overall_rate,
        "phase_pass_rates": phase_rates,
        "difficulty_pass_rates": difficulty_rates,
        "failure_mode_counts": failure_mode_counts,
        "average_tool_calls": avg_tools,
        "ranked_tasks": ranked,
    }

    output_dir.mkdir(parents=True, exist_ok=True)

    with open(json_path, "w") as f:
        json.dump(summary_data, f, indent=2)

    # Markdown
    lines = [
        "# Benchmark Summary",
        "",
        f"**Date:** {timestamp}",
        f"**Tasks scored:** {total} ({errors} errors excluded)",
        f"**Overall pass rate:** {overall_rate:.1%}",
        f"**Average tool calls:** {avg_tools:.1f}",
        "",
        "## Pass Rate by Phase",
        "",
        "| Phase | Pass Rate |",
        "|-------|-----------|",
    ]
    phase_labels = {
        "phase_1": "Elicitation",
        "phase_2": "Planning",
        "phase_3a": "TDD (Tests)",
        "phase_3b": "TDD (Impl)",
        "phase_4": "Runtime",
    }
    for pk in phase_keys:
        lines.append(f"| {phase_labels.get(pk, pk)} | {phase_rates[pk]:.1%} |")

    lines += [
        "",
        "## Pass Rate by Difficulty",
        "",
        "| Difficulty | Pass Rate |",
        "|------------|-----------|",
    ]
    for diff in ("easy", "medium", "hard"):
        lines.append(f"| {diff} | {difficulty_rates[diff]:.1%} |")

    lines += [
        "",
        "## Failure Mode Distribution",
        "",
        "| Failure Mode | Count |",
        "|-------------|-------|",
    ]
    for fm, count in sorted(failure_mode_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {fm} | {count} |")

    lines += [
        "",
        "## Tasks Ranked by Difficulty (hardest first)",
        "",
        "| Task | Result |",
        "|------|--------|",
    ]
    for tid, rate in ranked:
        lines.append(f"| {tid} | {'PASS' if rate == 1.0 else 'FAIL'} |")

    with open(md_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    return json_path, md_path
