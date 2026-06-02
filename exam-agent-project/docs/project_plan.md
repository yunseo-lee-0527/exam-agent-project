# Project Plan

## Objective

Build an LLM-based agentic work system that turns Scientific Management
lecture materials and instructor requirements into:

- A student-facing midterm exam
- Instructor-facing model answers and rubrics
- Grounding, coverage, cost, risk, and review evidence
- A mandatory human-review checkpoint before release

## Current Status

The minimum runnable system and the quality-control layer are implemented.
The current accepted artifacts in `outputs/` passed the agentic judge gate:

- `outputs/agentic_judge_report.json`: `PASS`
- 12 / 12 targets passed: the full exam target plus Q1-Q11
- `outputs/coverage_matrix.json`: all configured topic deltas are zero
- `outputs/source_grounding_report.json`: 11 / 11 questions grounded
- `outputs/chunk_grounding_report.json`: 11 / 11 questions supported
- `outputs/assessment_validity_report.json`: estimated exam time is 75 minutes

The latest recorded pass is a judge-only recheck. Its `outputs/run_trace.json`
shows Tasks 1-3 skipped through `--resume-from-judge`, followed by a successful
strict Gemini review and formatting pass.

## Implemented Work

### Input and Preprocessing

- Raw lecture material ingestion through `scripts/ingest_materials.py`
- PDF, TXT/MD, DOCX, and PPTX text extraction
- OCR and transcription follow-up flags for unsupported media
- Processed-note registration in `outputs/processed_notes_db.json`

### Generation

- Requirement loading from `requirements.json`
- Role-level model configuration from `model_policy.json`
- Coverage planning and deterministic question-slot allocation
- Four specialist question writers
- Optional blueprint mode for reproducible question sets
- Answer and rubric generation with cleaned lecture-note references

### Quality Control

- Deterministic coverage audit
- First LLM question and answer review loop
- Three-stage agentic judge system
- Cited-source-first line-span retrieval for factual judging
- Concept-overlap checks and neighbor-aware regeneration hints
- Minimal-scope regeneration by earliest failing stage
- Final-output safety gate for non-passing candidates
- Separate `outputs/failed_candidate_questions.json` on failed candidates

### Evidence and Reporting

- Coverage, source grounding, chunk grounding, assessment validity, residual
  risk, cost, and execution-trace reports
- Human-review checklist and structured notes template
- Offline evaluation harness in `src/evaluation.py`
- Retrieval and batching regression tests in `tests/`

## Remaining Work

### Required Before Submission

1. Complete an independent instructor review using
   `outputs/human_review_checklist.md`.
2. Record the decisions in `outputs/human_review_notes_template.json`.
3. Confirm whether M3.1.1 Therbligs is officially examinable.
4. Preserve strict-provider run evidence with the submitted artifacts.

### Recommended Evidence Run

The latest recorded pass validates the accepted question set, but it skips
planning and drafting. Run one fresh strict writer-path generation before final
submission:

```bash
python src/main.py \
  --provider gemini \
  --quality final_low_cost \
  --strict-provider \
  --blueprint nonexistent_path
```

On PowerShell, the same command can be entered on one line.

### Review Focus

- Inspect the factual warnings retained as evidence for Q5 and Q6 in
  `outputs/agentic_judge_report.json`.
- Review processed text for extraction artifacts before relying on a passage.
- Treat lexical chunk grounding as support evidence, not semantic entailment.
- Compare automated findings with an independent human reading of the cited
  excerpts.

## Verification Commands

```bash
python -m unittest discover -s tests -v
python scripts/doctor.py
python src/main.py --resume-from-judge --provider gemini --quality final_low_cost --strict-provider
```

## Completion Criteria

The project is ready for submission when:

- A fresh strict writer-path run completes with agentic judge `PASS`.
- The human review template is completed.
- Official scope, timing, language, point allocation, and fairness are
  confirmed.
- The final accepted `outputs/` artifact set is preserved with the report.
