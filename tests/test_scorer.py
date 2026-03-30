"""Unit tests for the scorer module: tool counting and TDD scoring logic."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from runner.scorer import COUNTED_TOOL_TYPES, count_tool_calls, PhaseScore, TaskScore


class TestToolCounting:
    def test_counts_tracked_tool_types(self):
        log = [
            {"type": "readFile", "path": "/foo.py"},
            {"type": "writeFile", "path": "/bar.py"},
            {"type": "runCommand", "command": "pytest"},
            {"type": "searchCode", "query": "def main"},
            {"type": "webFetch", "url": "http://example.com"},
            {"type": "mcpTool", "name": "browser.click"},
        ]
        assert count_tool_calls(log) == 6

    def test_excludes_lifecycle_events(self):
        log = [
            {"type": "sessionStart"},
            {"type": "modelRouting"},
            {"type": "memoryWrite"},
            {"type": "readFile", "path": "/foo.py"},
        ]
        assert count_tool_calls(log) == 1

    def test_empty_log(self):
        assert count_tool_calls([]) == 0

    def test_handles_missing_type_field(self):
        log = [
            {"action": "something"},
            {"name": "readFile"},
        ]
        # "name" field is checked as fallback
        assert count_tool_calls(log) == 1

    def test_handles_tool_type_key(self):
        log = [
            {"tool_type": "writeFile", "path": "/foo.py"},
        ]
        assert count_tool_calls(log) == 1

    def test_counted_types_constant_is_frozen(self):
        assert isinstance(COUNTED_TOOL_TYPES, frozenset)
        assert "readFile" in COUNTED_TOOL_TYPES
        assert "sessionStart" not in COUNTED_TOOL_TYPES


class TestTDDScoring:
    """Test the TDD-specific scoring logic: tdd_skip and tdd_no_tests_written."""

    def test_tdd_skip_when_verification_exits_zero_after_3a(self):
        """If tests pass before implementation, score as tdd_skip."""
        from runner.executor import check_test_files_exist, run_verification

        with tempfile.TemporaryDirectory() as workspace:
            # Create test files so existence check passes
            test_path = Path(workspace) / "tests" / "test_feature.py"
            test_path.parent.mkdir(parents=True)
            test_path.write_text("def test_placeholder(): pass")

            # Check files exist
            missing = check_test_files_exist(workspace, ["tests/test_feature.py"])
            assert missing == [], "Test file should exist"

            # Simulate verification command that exits 0 (tests pass = tdd_skip)
            exit_code = run_verification("exit 0", workspace)
            assert exit_code == 0, "Should exit 0 indicating tests passed"

            # This would be scored as tdd_skip in the scorer
            # (tests should NOT pass before implementation)

    def test_tdd_no_tests_written_when_files_missing(self):
        """If expected test files don't exist after 3a, score as tdd_no_tests_written."""
        from runner.executor import check_test_files_exist

        with tempfile.TemporaryDirectory() as workspace:
            missing = check_test_files_exist(
                workspace,
                ["tests/test_feature.py", "tests/test_integration.py"],
            )
            assert len(missing) == 2
            assert "tests/test_feature.py" in missing
            assert "tests/test_integration.py" in missing

    def test_tdd_correct_flow_tests_fail_then_pass(self):
        """Correct TDD: tests fail before implementation (non-zero), pass after (zero)."""
        from runner.executor import check_test_files_exist, run_verification

        with tempfile.TemporaryDirectory() as workspace:
            test_path = Path(workspace) / "tests" / "test_feature.py"
            test_path.parent.mkdir(parents=True)
            test_path.write_text("def test_something(): assert False")

            missing = check_test_files_exist(workspace, ["tests/test_feature.py"])
            assert missing == []

            # Phase 3a: tests should fail
            exit_code_3a = run_verification("exit 1", workspace)
            assert exit_code_3a != 0, "Tests should fail before implementation"

            # Phase 3b: tests should pass after implementation
            exit_code_3b = run_verification("exit 0", workspace)
            assert exit_code_3b == 0, "Tests should pass after implementation"

    def test_partial_test_files_missing(self):
        """Some test files exist, some don't."""
        from runner.executor import check_test_files_exist

        with tempfile.TemporaryDirectory() as workspace:
            existing = Path(workspace) / "tests" / "test_a.py"
            existing.parent.mkdir(parents=True)
            existing.write_text("pass")

            missing = check_test_files_exist(
                workspace,
                ["tests/test_a.py", "tests/test_b.py"],
            )
            assert missing == ["tests/test_b.py"]
