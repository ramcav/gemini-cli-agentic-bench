# gemini-cli-agentic-bench

A lifecycle benchmark for evaluating coding agents across the full software development process — not just patch generation.

## Thesis

Current coding agent benchmarks like [SWE-bench](https://arxiv.org/abs/2310.06770) measure a single phase of software development: patch generation given a pre-described issue. [SWE-bench+](https://arxiv.org/abs/2410.06992) showed that **32.67%** of "solved" instances involved solution leakage and **31.08%** passed only due to weak tests. [SWE-bench Multimodal](https://arxiv.org/abs/2410.03859) extended evaluation to visual domains but remains single-phase.

Meanwhile, [ARC-AGI-3](https://arxiv.org/abs/2603.24621) (March 2026) argues that intelligence is adaptive behaviour in novel environments — the ability to explore, infer goals, and plan effective action sequences without explicit instructions. Frontier AI systems which, as of March 2026, score below 1% precisely because they cannot explore, plan, and adapt sequentially.

**We argue the same definition applies to software engineering agents.** A coding agent that can only pattern-match on static context is not intelligent in this sense — it is a lookup table. This benchmark measures the other thing: whether Gemini CLI can reason adaptively across a full development lifecycle, using tools to observe, adapt, and verify.

Gemini CLI in 2026, with MCP integrations for browser and runtime access, is capable of participating in the entire development lifecycle. We should measure that, not just patch generation.

## Why Existing Benchmarks Fall Short

| Benchmark | What it measures | What it misses |
|-----------|-----------------|----------------|
| [SWE-bench](https://arxiv.org/abs/2310.06770) | Patch generation from issue descriptions | Requirements elicitation, planning, TDD, runtime validation |
| [SWE-bench+](https://arxiv.org/abs/2410.06992) | Same, with contamination analysis | Found 32.67% solution leakage, 31.08% weak-test passes — single-phase measurement is inherently gameable |
| [SWE-bench Multimodal](https://arxiv.org/abs/2410.03859) | Patch generation for visual bugs | Still single-phase, still static context |
| [ARC-AGI-3](https://arxiv.org/abs/2603.24621) | Adaptive reasoning in novel abstract environments | Not applied to software engineering — but the framework transfers directly |

This benchmark fills the gap: **multi-phase evaluation that requires the agent to take actions, observe results, and adapt its plan based on what it sees.**

## The Lifecycle Framing

Each task covers four phases (executed as five Gemini CLI invocations):

### Phase 1: Requirements Elicitation
The agent receives a deliberately underspecified feature request and must ask clarifying questions before writing any code. **Eval (LLM-as-judge):** Did it surface the key ambiguities?

### Phase 2: Implementation Planning
The agent receives canonical answers to its questions and produces a structured implementation plan. **Eval (LLM-as-judge):** Is the plan complete and correct?

### Phase 3: Red-Green TDD
Split into two invocations:
- **3a:** Agent writes failing tests only. Runner verifies tests fail (non-zero exit code). If tests pass, scored as `tdd_skip` — the agent cheated.
- **3b:** Agent implements the feature. Runner verifies tests pass (zero exit code). If tests fail, scored as `tdd_failure`.

**Eval (deterministic):** Do tests genuinely fail before implementation? Do they pass after without hardcoding?

### Phase 4: Runtime Validation
The agent uses tool calls (shell commands, server interaction) to verify behaviour in a real runtime environment. **Eval (LLM-as-judge):** Does it correctly interpret runtime feedback and adapt?

Context accumulates across phases — each invocation receives the full history of prior phases as context.

## Task Validity Rule

A task is only valid if it **cannot be solved by static context reading alone**. The agent must take actions, observe results, and adapt its plan based on what it sees. Each task requires at least 3 sequential dependent tool calls by design.

The runner enforces a minimum total tool call count (`min_tool_calls`) across all phases. This is a **necessary but not sufficient** condition — it catches degenerate cases (an agent that outputs text without using tools) but the deeper dependency structure is guaranteed by task design and human review during authoring.

## Task Schema

Tasks are defined as JSON files in `tasks/` and validated against `tasks/schema.json` at load time.

```json
{
  "id": "string (snake_case)",
  "title": "string",
  "repo": "string (owner/repo)",
  "commit_sha": "string (40-char hex)",
  "feature_request": "string (deliberately underspecified)",
  "phase_1_expected_questions": ["ambiguities the agent should surface"],
  "phase_1_answers": {"topic": "canonical answer from product owner"},
  "phase_2_expected_plan_components": ["required planning elements"],
  "phase_3_test_file": "path to primary test file",
  "phase_3_test_paths": ["paths to test files agent must create"],
  "phase_3_verification_command": "shell command to run tests",
  "phase_4_runtime_check": "description of runtime state to validate",
  "phase_4_runtime_type": "interactive_server | build_and_test | browser_mcp",
  "setup_commands": ["commands to run before handing off to agent"],
  "setup_timeout_seconds": 120,
  "min_tool_calls": 8,
  "difficulty": "easy | medium | hard",
  "failure_mode_category": "elicitation_failure | planning_failure | tdd_skip | runtime_blind | tool_sequence_error",
  "references": ["SWE-bench", "ARC-AGI-3"]
}
```

### Key fields

- **`setup_commands`**: Executed sequentially by the runner in the cloned repo before the agent starts. Task scope determines setup complexity — if a task requires a 10-minute build, it's a bad task, not a runner problem. `setup_timeout_seconds` (default 120) enforces this.
- **`phase_1_answers`**: Canonical answers authored at task creation time. These flow into phase 2+ regardless of how well the agent did on phase 1 — a bad phase 1 scores poorly but doesn't block the pipeline.
- **`phase_3_test_paths`**: The runner verifies these files exist after phase 3a before running the verification command. If they don't exist, the task is scored as `tdd_no_tests_written`.
- **`phase_4_runtime_type`**: Determines the style of runtime validation. `interactive_server` means the agent starts a server and hits endpoints (FastAPI tasks). `build_and_test` means the agent runs builds/tests and interprets output (Cal.com, VS Code tasks). `browser_mcp` is reserved for future tasks using browser MCP tooling.
- **`failure_mode_category`**: A design-time label indicating what kind of failure this task is designed to catch. `tool_sequence_error` never appears in runtime results — the closest runtime proxy is `insufficient_tool_use`.

## Included Tasks

| ID | Repo | Difficulty | Failure Mode Target |
|----|------|-----------|-------------------|
| `fastapi_rate_limit` | tiangolo/fastapi | medium | elicitation_failure |
| `fastapi_websocket` | tiangolo/fastapi | hard | tool_sequence_error |
| `calcom_availability_override` | calcom/cal.com | medium | planning_failure |
| `calcom_buffer_time` | calcom/cal.com | medium | runtime_blind |
| `vscode_word_count` | microsoft/vscode | easy | tdd_skip |

Tasks are pinned to specific commits from late March 2026. Commit SHAs should be updated periodically — this is a feature, not a bug, as it prevents contamination.

## Runtime Validation Realism (v1 Limitation)

Phase 4 realism varies by repo in this PoC:

- **FastAPI tasks**: Genuinely interactive — the agent starts a dev server, hits endpoints with curl, and interprets responses.
- **Cal.com / VS Code tasks**: Build-and-test — the agent runs builds and test suites, then interprets output to confirm correct behaviour.

Full interactive runtime validation (real browser via MCP, running application servers for all repos) is the target for future tasks. The infrastructure supports it today via `browser_mcp` runtime type — the first five tasks demonstrate the foundation. This is the natural next step and connects directly to Claude Code's browser MCP capability.

## Scoring

### LLM-as-Judge (Phases 1, 2, 4)
Judge prompts are runner-owned templates in `judge/prompts.py`. The judge model is configurable via `JUDGE_MODEL` env var (default: `claude-sonnet-4-6`). The pass threshold is configurable via `JUDGE_PASS_THRESHOLD` (default: `0.6` — 60% coverage of expected items).

If the judge returns malformed JSON, the phase is scored as `judge_error` rather than crashing.

### Deterministic (Phase 3)
Phase 3a: verification command must exit non-zero (tests fail). Phase 3b: must exit zero (tests pass).

### Failure Modes
Runtime result states: `setup_failure`, `judge_error`, `insufficient_tool_use`, `tdd_skip`, `tdd_failure`, `tdd_no_tests_written`, `elicitation_failure`, `planning_failure`, `runtime_blind`.

A task produces `failure_modes: list[str]` — multiple failures are captured. Overall status:
- **`pass`**: empty failure_modes
- **`fail`**: agent failures (elicitation, planning, TDD, runtime)
- **`error`**: infrastructure failures (setup_failure, judge_error) — excluded from pass rate calculations

## How to Run

### Prerequisites
- Python 3.10+
- [Gemini CLI](https://github.com/google-gemini/gemini-cli) (`npm install -g @google/gemini-cli`)
- An Anthropic API key (for the judge)

### Setup

```bash
git clone https://github.com/your-org/gemini-cli-agentic-bench.git
cd gemini-cli-agentic-bench
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY
```

### Run

```bash
# Validate all task schemas without executing
python -m runner --dry-run

# Run all tasks
python -m runner --all

# Run a single task
python -m runner --task fastapi_rate_limit

# Custom output directory
python -m runner --all --output-dir ./my-results
```

Or use the wrapper script:
```bash
python run_bench.py --all
```

### Output

Results are written to `results/`:
- `{task_id}_{timestamp}.json` — per-task results with phase scores, agent output, activity log
- `summary_{timestamp}.json` — aggregate statistics
- `summary_{timestamp}.md` — human-readable markdown report

Live terminal output shows phase-by-phase progress:
```
[fastapi_rate_limit] Phase 1 (Elicitation): PASS (5/6 expected items) | Phase 2 (Planning): PASS (6/7 expected items) | ...
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | (required) | API key for the judge model |
| `JUDGE_MODEL` | `claude-sonnet-4-6` | Model for LLM-as-judge scoring |
| `JUDGE_PASS_THRESHOLD` | `0.6` | Minimum coverage ratio for phases 1, 2 |
| `GEMINI_CLI_ACTIVITY_LOG_TARGET` | (set by runner) | Path for Gemini CLI activity log JSONL |

### Running Tests

```bash
python -m pytest tests/ -v
```

## Adding New Tasks

1. Create a JSON file in `tasks/` following the schema.
2. Run `python -m runner --dry-run` to validate.
3. Ensure your task cannot be solved by static context reading alone.
4. Ensure `setup_commands` complete within `setup_timeout_seconds` (default 120s).
5. Test the task end-to-end with `python -m runner --task your_task_id`.

## License

Apache 2.0 — matching the [Gemini CLI](https://github.com/google-gemini/gemini-cli) license intentionally to avoid compatibility friction for integrations.
