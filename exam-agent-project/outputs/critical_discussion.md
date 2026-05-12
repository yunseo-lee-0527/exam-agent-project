# Critical Discussion

This file summarizes the main limitations that remain after the current implementation.

## Current Evidence

- Agentic judge final verdict: `PASS`.
- Chunk-level grounding passed: `True`.
- Supported questions: 11 / 11.

## Residual Risks

### deterministic_provider

- Severity: high
- Evidence: Current run used the local deterministic fallback, not a live LLM provider.
- Mitigation: Run the final pipeline with --provider gemini --quality final --strict-provider and preserve cost_report.json as evidence.

### blueprint_dependency

- Severity: medium
- Evidence: exam_blueprint.json controls the current exam draft.
- Mitigation: Frame it as an instructor-approved blueprint or generate a fresh blueprint from the planner before final submission.

### provider_fallback_hidden

- Severity: medium
- Evidence: Strict provider mode is off, so live provider failures can fall back during development.
- Mitigation: Use --strict-provider for final generation.

### self_evaluation_bias

- Severity: medium
- Evidence: The same system family generates and judges the exam.
- Mitigation: Add human_review_notes.json and compare human findings against agentic_judge_report.json.

### chunk_grounding_is_not_entailment

- Severity: low
- Evidence: Chunk grounding verifies lexical support, not full semantic entailment.
- Mitigation: Upgrade SourceGroundingJudgeAgent to compare answer claims against cited chunks with a live LLM judge.

## Discussion

The system now has a closed quality-control layer, but final submission should not claim full autonomy.
The strongest defensible claim is that the system automates generation, checking, evidence collection, and revision support, while preserving a final human gate for scope and fairness.

The most important next validation step is to run the final pipeline with a live LLM provider in strict mode and compare the generated exam with human reviewer notes.
