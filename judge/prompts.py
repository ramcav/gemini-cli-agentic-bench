"""Judge prompt templates for LLM-as-judge scoring of phases 1, 2, and 4.

Templates are runner-owned, not per-task. The task schema provides the content
(expected questions, expected plan components, runtime check description);
these templates provide the evaluation wrapper.
"""

PHASE_1_TEMPLATE = """You are a benchmark judge evaluating whether a coding agent correctly identified ambiguities in an underspecified feature request.

## Feature Request
{feature_request}

## Expected Ambiguities the Agent Should Have Surfaced
{expected_questions}

## Agent's Actual Output (Clarifying Questions Asked)
{agent_output}

## Instructions
For each expected ambiguity, determine whether the agent surfaced it — either by asking a direct question about it or by raising a closely related concern that demonstrates awareness of the ambiguity. Semantic equivalence counts; exact wording does not.

Return only valid JSON, no markdown, no preamble:
{{
  "scores": [
    {{
      "expected": "<the expected ambiguity>",
      "surfaced": true | false,
      "rationale": "<one sentence explaining why>"
    }}
  ],
  "total_expected": <int>,
  "total_surfaced": <int>,
  "coverage": <float between 0.0 and 1.0>
}}"""

PHASE_2_TEMPLATE = """You are a benchmark judge evaluating whether a coding agent produced a complete and correct implementation plan.

## Feature Request
{feature_request}

## Clarifying Questions and Answers
{phase_1_context}

## Expected Plan Components
{expected_plan_components}

## Agent's Actual Implementation Plan
{agent_output}

## Instructions
For each expected plan component, determine whether the agent's plan addresses it — either explicitly or through a closely related design decision. The component does not need to be listed verbatim; it must be substantively covered.

Return only valid JSON, no markdown, no preamble:
{{
  "scores": [
    {{
      "expected": "<the expected plan component>",
      "addressed": true | false,
      "rationale": "<one sentence explaining why>"
    }}
  ],
  "total_expected": <int>,
  "total_addressed": <int>,
  "coverage": <float between 0.0 and 1.0>
}}"""

PHASE_4_TEMPLATE = """You are a benchmark judge evaluating whether a coding agent correctly performed runtime validation of a feature.

## Feature Request
{feature_request}

## Expected Runtime Validation
{runtime_check}

## Runtime Validation Type
{runtime_type}

## Agent's Runtime Validation Output (tool calls and observations)
{agent_output}

## Instructions
Evaluate whether the agent:
1. Executed appropriate commands to validate the feature at runtime
2. Correctly interpreted the output of those commands
3. Identified whether the feature is working correctly based on runtime evidence
4. Adapted its approach if initial validation revealed issues

Return only valid JSON, no markdown, no preamble:
{{
  "executed_validation": true | false,
  "correct_interpretation": true | false,
  "identified_working_state": true | false,
  "adapted_on_failure": true | false | null,
  "rationale": "<2-3 sentences explaining the overall assessment>",
  "pass": true | false
}}"""
