"""Gemini CLI invocation and 5-phase orchestration.

Handles: repo cloning, setup commands, context accumulation across phases,
and the two-step TDD split (3a: write failing tests, 3b: implement).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PhaseOutput:
    phase: str
    prompt: str
    raw_output: str
    exit_code: int
    truncated: bool = False


@dataclass
class ExecutionResult:
    task_id: str
    workspace: str
    phase_outputs: dict[str, PhaseOutput] = field(default_factory=dict)
    activity_log: list[dict[str, Any]] = field(default_factory=list)
    setup_failure: str | None = None


MAX_OUTPUT_CHARS = 10_000


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS], True
    return text, False


def _check_gemini_cli() -> None:
    if shutil.which("gemini") is None:
        raise RuntimeError(
            "Gemini CLI not found. Install with: npm install -g @google/gemini-cli"
        )


def _clone_repo(repo: str, commit_sha: str, workspace: Path) -> None:
    url = f"https://github.com/{repo}.git"
    subprocess.run(
        ["git", "clone", "--depth", "100", url, str(workspace)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", commit_sha],
        cwd=str(workspace),
        check=True,
        capture_output=True,
    )


def _run_setup(
    commands: list[str], workspace: Path, timeout: int
) -> str | None:
    """Run setup commands sequentially. Returns error message or None on success."""
    for cmd in commands:
        try:
            subprocess.run(
                cmd,
                shell=True,
                cwd=str(workspace),
                check=True,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.CalledProcessError as e:
            return f"Setup command failed: {cmd}\nstderr: {e.stderr.decode()[:500]}"
        except subprocess.TimeoutExpired:
            return f"Setup command timed out after {timeout}s: {cmd}"
    return None


def _invoke_gemini(prompt: str, workspace: Path, log_path: Path) -> tuple[str, int]:
    """Invoke gemini --bare -p with the given prompt. Returns (output, exit_code)."""
    env = os.environ.copy()
    env["GEMINI_CLI_ACTIVITY_LOG_TARGET"] = str(log_path)

    try:
        result = subprocess.run(
            ["gemini", "--bare", "-p", prompt, "--output-format", "json"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            env=env,
            timeout=300,
        )
        return result.stdout, result.returncode
    except subprocess.TimeoutExpired:
        return "<gemini invocation timed out after 300s>", 1


def _read_activity_log(log_path: Path) -> list[dict[str, Any]]:
    """Read JSONL activity log. Returns empty list if missing or malformed."""
    if not log_path.exists():
        return []
    entries = []
    try:
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    return entries


def _run_verification(command: str, workspace: Path) -> int:
    """Run a verification command and return the exit code."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(workspace),
            capture_output=True,
            timeout=120,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        return -1


def _build_phase_1_prompt(task: dict[str, Any]) -> str:
    return (
        "You are working on a feature request. Before writing any code, "
        "identify ambiguities and ask clarifying questions.\n\n"
        f"## Feature Request\n{task['feature_request']}\n\n"
        "List the clarifying questions you would ask before starting implementation. "
        "Do not write any code yet."
    )


def _build_phase_2_prompt(task: dict[str, Any], phase_1_output: str) -> str:
    answers_text = "\n".join(
        f"- **{k}**: {v}" for k, v in task["phase_1_answers"].items()
    )
    return (
        "You previously asked clarifying questions about a feature request. "
        "Here is the full context. Now produce a structured implementation plan.\n\n"
        f"## Feature Request\n{task['feature_request']}\n\n"
        f"## Your Clarifying Questions\n{phase_1_output}\n\n"
        f"## Answers to Your Questions\n{answers_text}\n\n"
        "Produce a detailed implementation plan identifying: components to touch, "
        "interfaces to define, edge cases to handle, and the order of implementation."
    )


def _build_phase_3a_prompt(task: dict[str, Any], phase_1_output: str, phase_2_output: str) -> str:
    answers_text = "\n".join(
        f"- **{k}**: {v}" for k, v in task["phase_1_answers"].items()
    )
    return (
        "You are implementing a feature using test-driven development. "
        "Write the failing tests ONLY. Do not implement the feature yet.\n\n"
        f"## Feature Request\n{task['feature_request']}\n\n"
        f"## Answers to Clarifying Questions\n{answers_text}\n\n"
        f"## Your Implementation Plan\n{phase_2_output}\n\n"
        f"## Test File Path\n{task['phase_3_test_file']}\n\n"
        "Write comprehensive tests that cover the expected behavior. "
        "Tests MUST FAIL when run because the feature is not yet implemented. "
        "Do not implement the feature — only write the tests."
    )


def _build_phase_3b_prompt(
    task: dict[str, Any], phase_1_output: str, phase_2_output: str, phase_3a_output: str
) -> str:
    answers_text = "\n".join(
        f"- **{k}**: {v}" for k, v in task["phase_1_answers"].items()
    )
    return (
        "The tests are written and failing. Now implement the feature until all tests pass. "
        "Do NOT modify the test files.\n\n"
        f"## Feature Request\n{task['feature_request']}\n\n"
        f"## Answers to Clarifying Questions\n{answers_text}\n\n"
        f"## Your Implementation Plan\n{phase_2_output}\n\n"
        f"## Tests You Wrote (Phase 3a)\n{phase_3a_output}\n\n"
        f"## Verification Command\n{task['phase_3_verification_command']}\n\n"
        "Implement the feature so that all tests pass. "
        "Do not modify any test files."
    )


def _build_phase_4_prompt(
    task: dict[str, Any],
    phase_2_output: str,
    phase_3b_output: str,
) -> str:
    return (
        "The feature is implemented and tests pass. Now perform runtime validation.\n\n"
        f"## Feature Request\n{task['feature_request']}\n\n"
        f"## Implementation Plan\n{phase_2_output}\n\n"
        f"## Implementation Output\n{phase_3b_output}\n\n"
        f"## Runtime Validation Task\n{task['phase_4_runtime_check']}\n\n"
        f"## Validation Type\n{task['phase_4_runtime_type']}\n\n"
        "Execute the necessary commands to validate the feature at runtime. "
        "Observe the output, interpret the results, and report whether the feature "
        "is working correctly. If something fails, adapt your approach and try again."
    )


def execute_task(task: dict[str, Any]) -> ExecutionResult:
    """Execute all 5 phases of a benchmark task."""
    _check_gemini_cli()

    task_id = task["id"]
    workspace = Path(tempfile.mkdtemp(prefix=f"bench_{task_id}_"))
    log_path = Path(tempfile.mktemp(suffix=".jsonl", prefix=f"activity_{task_id}_"))

    result = ExecutionResult(task_id=task_id, workspace=str(workspace))

    # Clone and setup
    try:
        _clone_repo(task["repo"], task["commit_sha"], workspace)
    except subprocess.CalledProcessError as e:
        result.setup_failure = f"Clone failed: {e.stderr.decode()[:500] if e.stderr else str(e)}"
        return result

    setup_timeout = task.get("setup_timeout_seconds", 120)
    setup_err = _run_setup(task["setup_commands"], workspace, setup_timeout)
    if setup_err:
        result.setup_failure = setup_err
        return result

    # Phase 1: Requirements elicitation
    p1_prompt = _build_phase_1_prompt(task)
    p1_output, p1_exit = _invoke_gemini(p1_prompt, workspace, log_path)
    p1_text, p1_trunc = _truncate(p1_output)
    result.phase_outputs["phase_1"] = PhaseOutput(
        phase="phase_1", prompt=p1_prompt, raw_output=p1_text,
        exit_code=p1_exit, truncated=p1_trunc,
    )

    # Phase 2: Implementation planning
    p2_prompt = _build_phase_2_prompt(task, p1_output)
    p2_output, p2_exit = _invoke_gemini(p2_prompt, workspace, log_path)
    p2_text, p2_trunc = _truncate(p2_output)
    result.phase_outputs["phase_2"] = PhaseOutput(
        phase="phase_2", prompt=p2_prompt, raw_output=p2_text,
        exit_code=p2_exit, truncated=p2_trunc,
    )

    # Phase 3a: Write failing tests
    p3a_prompt = _build_phase_3a_prompt(task, p1_output, p2_output)
    p3a_output, p3a_exit = _invoke_gemini(p3a_prompt, workspace, log_path)
    p3a_text, p3a_trunc = _truncate(p3a_output)
    result.phase_outputs["phase_3a"] = PhaseOutput(
        phase="phase_3a", prompt=p3a_prompt, raw_output=p3a_text,
        exit_code=p3a_exit, truncated=p3a_trunc,
    )

    # Phase 3b: Implement feature
    p3b_prompt = _build_phase_3b_prompt(task, p1_output, p2_output, p3a_output)
    p3b_output, p3b_exit = _invoke_gemini(p3b_prompt, workspace, log_path)
    p3b_text, p3b_trunc = _truncate(p3b_output)
    result.phase_outputs["phase_3b"] = PhaseOutput(
        phase="phase_3b", prompt=p3b_prompt, raw_output=p3b_text,
        exit_code=p3b_exit, truncated=p3b_trunc,
    )

    # Phase 4: Runtime validation
    p4_prompt = _build_phase_4_prompt(task, p2_output, p3b_output)
    p4_output, p4_exit = _invoke_gemini(p4_prompt, workspace, log_path)
    p4_text, p4_trunc = _truncate(p4_output)
    result.phase_outputs["phase_4"] = PhaseOutput(
        phase="phase_4", prompt=p4_prompt, raw_output=p4_text,
        exit_code=p4_exit, truncated=p4_trunc,
    )

    # Read activity log
    result.activity_log = _read_activity_log(log_path)

    return result


def check_test_files_exist(workspace: str, test_paths: list[str]) -> list[str]:
    """Check which expected test files exist. Returns list of missing paths."""
    missing = []
    for path in test_paths:
        full = Path(workspace) / path
        if not full.exists():
            missing.append(path)
    return missing


def run_verification(command: str, workspace: str) -> int:
    """Run the verification command in the workspace. Returns exit code."""
    return _run_verification(command, Path(workspace))
