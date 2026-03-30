"""Unit tests for the judge module: JSON parsing and error handling."""

from __future__ import annotations

from judge.judge import JudgeResult, evaluate_coverage, evaluate_phase_4, _parse_json


class TestJudgeJsonParsing:
    def test_parse_valid_json(self):
        raw = '{"scores": [], "total_expected": 3, "total_surfaced": 2, "coverage": 0.67}'
        parsed = _parse_json(raw)
        assert parsed["coverage"] == 0.67
        assert parsed["total_expected"] == 3

    def test_parse_json_with_markdown_fences(self):
        raw = '```json\n{"scores": [], "coverage": 0.5}\n```'
        parsed = _parse_json(raw)
        assert parsed["coverage"] == 0.5

    def test_parse_json_with_bare_fences(self):
        raw = '```\n{"coverage": 0.8}\n```'
        parsed = _parse_json(raw)
        assert parsed["coverage"] == 0.8

    def test_parse_malformed_json_raises(self):
        raw = "This is not JSON at all"
        try:
            _parse_json(raw)
            assert False, "Should have raised"
        except Exception:
            pass

    def test_parse_empty_string_raises(self):
        try:
            _parse_json("")
            assert False, "Should have raised"
        except Exception:
            pass


class TestJudgeResultHandling:
    def test_judge_error_result(self):
        result = JudgeResult(
            phase=1,
            raw_response="not json",
            parsed=None,
            error="Expecting value: line 1 column 1",
        )
        assert result.is_error
        passed, coverage = evaluate_coverage(result)
        assert not passed
        assert coverage == 0.0

    def test_successful_coverage_above_threshold(self):
        result = JudgeResult(
            phase=1,
            raw_response="{}",
            parsed={"coverage": 0.75, "total_expected": 4, "total_surfaced": 3},
            error=None,
        )
        assert not result.is_error
        # Default threshold is 0.6
        passed, coverage = evaluate_coverage(result)
        assert passed
        assert coverage == 0.75

    def test_coverage_below_threshold(self):
        result = JudgeResult(
            phase=2,
            raw_response="{}",
            parsed={"coverage": 0.4, "total_expected": 5, "total_addressed": 2},
            error=None,
        )
        passed, coverage = evaluate_coverage(result)
        assert not passed
        assert coverage == 0.4

    def test_coverage_at_exact_threshold(self):
        result = JudgeResult(
            phase=1,
            raw_response="{}",
            parsed={"coverage": 0.6},
            error=None,
        )
        passed, coverage = evaluate_coverage(result)
        assert passed
        assert coverage == 0.6

    def test_phase_4_pass(self):
        result = JudgeResult(
            phase=4,
            raw_response="{}",
            parsed={"pass": True, "rationale": "Feature works correctly"},
            error=None,
        )
        assert evaluate_phase_4(result) is True

    def test_phase_4_fail(self):
        result = JudgeResult(
            phase=4,
            raw_response="{}",
            parsed={"pass": False, "rationale": "Feature broken"},
            error=None,
        )
        assert evaluate_phase_4(result) is False

    def test_phase_4_error(self):
        result = JudgeResult(
            phase=4,
            raw_response="garbage",
            parsed=None,
            error="parse error",
        )
        assert evaluate_phase_4(result) is False
