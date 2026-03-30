"""CLI entry point for the Gemini CLI Agentic Benchmark runner.

Usage:
    python -m runner --all              # Run all tasks
    python -m runner --task <task_id>   # Run a single task
    python -m runner --dry-run          # Validate schemas only
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import jsonschema
from dotenv import load_dotenv

from runner.executor import execute_task
from runner.reporter import print_task_progress, write_summary, write_task_result
from runner.scorer import score_task

TASKS_DIR = Path(__file__).parent.parent / "tasks"
SCHEMA_PATH = TASKS_DIR / "schema.json"
DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent / "results"


def _load_schema() -> dict:
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def _load_tasks(schema: dict, task_id: str | None = None) -> list[dict]:
    """Load and validate task files. Returns list of valid tasks."""
    tasks = []
    task_files = sorted(TASKS_DIR.glob("*.json"))

    for tf in task_files:
        if tf.name == "schema.json":
            continue
        try:
            with open(tf) as f:
                task = json.load(f)
            jsonschema.validate(task, schema)
            if task_id and task["id"] != task_id:
                continue
            tasks.append(task)
        except jsonschema.ValidationError as e:
            print(f"SCHEMA ERROR in {tf.name}: {e.message}", file=sys.stderr)
        except json.JSONDecodeError as e:
            print(f"JSON ERROR in {tf.name}: {e}", file=sys.stderr)

    return tasks


def _check_prerequisites() -> bool:
    """Check that required tools are available."""
    if shutil.which("gemini") is None:
        print(
            "ERROR: Gemini CLI not found. Install with: npm install -g @google/gemini-cli",
            file=sys.stderr,
        )
        return False
    return True


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Gemini CLI Agentic Benchmark Runner",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--task", type=str, help="Run a single task by ID")
    group.add_argument("--all", action="store_true", help="Run all tasks")
    group.add_argument(
        "--dry-run", action="store_true",
        help="Validate task schemas without executing",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help="Output directory for results",
    )
    args = parser.parse_args()

    schema = _load_schema()
    tasks = _load_tasks(schema, task_id=args.task if hasattr(args, "task") else None)

    if not tasks:
        if args.task:
            print(f"No valid task found with ID: {args.task}", file=sys.stderr)
        else:
            print("No valid tasks found.", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(tasks)} task(s)")

    if args.dry_run:
        for task in tasks:
            print(f"  VALID: {task['id']} ({task['difficulty']}) - {task['title']}")
        print("\nAll tasks validated successfully.")
        return

    if not _check_prerequisites():
        sys.exit(1)

    output_dir = args.output_dir
    all_scores: list[tuple[dict, TaskScore]] = []

    for i, task in enumerate(tasks, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(tasks)}] Running: {task['id']} ({task['difficulty']})")
        print(f"{'='*60}")

        execution = execute_task(task)
        score = score_task(task, execution)

        print_task_progress(task["id"], score)

        result_path = write_task_result(task, score, execution, output_dir)
        print(f"  Result: {result_path}")

        all_scores.append((task, score))

    # Write aggregate summary
    if len(all_scores) > 1:
        json_path, md_path = write_summary(all_scores, output_dir)
        print(f"\n{'='*60}")
        print(f"Summary: {json_path}")
        print(f"Report:  {md_path}")

    # Final stats
    total = len(all_scores)
    passed = sum(1 for _, s in all_scores if s.status == "pass")
    failed = sum(1 for _, s in all_scores if s.status == "fail")
    errors = sum(1 for _, s in all_scores if s.status == "error")
    print(f"\nDone: {passed} passed, {failed} failed, {errors} errors out of {total} tasks")


if __name__ == "__main__":
    main()
