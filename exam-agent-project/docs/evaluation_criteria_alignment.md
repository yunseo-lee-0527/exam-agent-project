# Evaluation Criteria Alignment

This document explains how the project now defends itself against the four
grading criteria.

## System Design Quality

The pipeline is organized as an agentic work system:

- Collector: ingests processed lecture materials.
- Planner: maps requirements to topic weights.
- Parallel question writers: draft short-answer, comparison, application, and essay items.
- Answer writer: retrieves source references and writes model answers.
- Structural judges: check question and answer quality.
- Agentic judge system: performs specialist review and revision routing.
- Formatter and evidence builders: produce exam, answers, reports, and review templates.

The important design claim is not just that many agents exist. The stronger
claim is that each stage has an explicit input/output responsibility and leaves
inspectable artifacts.

## Implementation Completeness

The project supports:

- deterministic demo mode for reproducible execution;
- lecture-style Vertex AI / Agent Platform API mode via `--provider vertex`;
- low-cost final mode via `--quality final_low_cost`;
- provider usage and cost logging;
- source grounding, coverage matrix, agentic judge report, and assessment validity report.

Run the setup checker before a final demo:

```bat
python .\scripts\doctor.py
```

## Quality of Generated Exam

Every question now carries instructor-facing metadata:

- `learning_objective`
- `bloom_level`
- `difficulty`
- `estimated_time_minutes`
- `exam_intent`
- `assessed_skill`
- `rubric`
- `source_refs`

The key output is:

```text
outputs/assessment_validity_report.md
```

That report is meant to answer the professor's core question: not merely "Did
the system generate an exam?", but "Why should we believe this is an aligned,
grounded, gradeable exam?"

## Critical Discussion

The project explicitly preserves limits:

- LLM-as-judge is useful but not independent ground truth.
- Low-cost models are appropriate for iteration, but final weak items may need a
  stronger rewrite model or human intervention.
- Human review remains necessary for educational validity, difficulty fit, and
  fairness.
- The strongest automation claim is around generation and evidence collection,
  not fully autonomous exam release.

Before submission, fill in:

```text
outputs/human_review_notes_template.json
```

The completed file should be used as evidence that the final human gate was not
only claimed, but actually performed.
