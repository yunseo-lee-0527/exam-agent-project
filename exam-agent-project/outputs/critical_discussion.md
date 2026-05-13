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
- Mitigation: Run the final pipeline with --provider vertex --quality final_low_cost --strict-provider or --provider vertex --quality final --strict-provider, then preserve cost_report.json as evidence.

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

The system now has a closed quality-control layer, but final submission should not claim full autonomy. The defensible claim is narrower: the program automates ingestion, planning, generation, checking, evidence collection, and revision support, while preserving a final human gate for educational validity.

## Automation Boundary

- Automated well: format conversion, first-pass coverage planning, question drafting, answer drafting, source-reference collection, rubric drafting, cost logging, and judge-based revision support.
- Still human-critical: deciding whether a question reflects the instructor's intended emphasis, whether a scenario is pedagogically authentic, whether difficulty matches the cohort, and whether the rubric will produce fair grading.

## LLM-as-Judge Bias

The agentic judge system reduces obvious defects but cannot be treated as independent ground truth. Because generator and judge may share similar model priors, they can agree on fluent but shallow questions. The mitigation is to compare agentic_judge_report.json with human_review_notes_template.json after a real reviewer fills it in.

## Cost-Quality Trade-off

The low-cost Vertex/Gemini path is appropriate for iteration, metadata filling, and repeated judge calls. A higher-capability model should be reserved for final question rewriting or cases where human review flags weak reasoning. This staged policy protects cost without pretending that all model calls have equal educational value.

## Evidence Required For The Final Claim

Before submission, the team should preserve assessment_validity_report.md, agentic_judge_report.json, cost_report.json, and a completed human_review_notes file. Together these show not only that the program ran, but why the resulting exam is aligned, grounded, gradeable, and still appropriately human-supervised.

The most important next validation step is to run the final pipeline with a live LLM provider in strict mode and compare the generated exam with human reviewer notes.
