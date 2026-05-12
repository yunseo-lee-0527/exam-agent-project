# Quality Improvement Notes

This document records the changes made after a critical review against the
four evaluation criteria: system design quality, implementation completeness,
generated exam quality, and critical discussion.

## Main Problem Found

The earlier pipeline could produce a plausible exam, but the output was too
dependent on opportunistic topic selection. Work-system questions dominated
the exam, while Scientific Management, innovation frameworks, and Therbligs
were underrepresented. Some answers also cited weak or mismatched source
files. The evaluator detected source gaps but still passed the run, which made
the quality-control loop too permissive.

## What Changed

1. Added `exam_blueprint.json`.
   - The blueprint fixes the exam structure before generation.
   - It specifies question type, points, difficulty, learning objective,
     model answer, grading rubric, lecture sources, and coverage contribution.
   - Topic contribution now matches the requested weights exactly:
     25 / 20 / 25 / 15 / 15.

2. Added blueprint-aware generation.
   - `src/main.py` now reads `exam_blueprint.json` by default.
   - If the blueprint exists, Task 2 uses a Blueprint Question Writer instead
     of relying only on round-robin question generation.
   - The older parallel writer path remains available when no blueprint is
     supplied.

3. Strengthened source grounding.
   - Questions now preserve explicit lecture-source references.
   - The pipeline writes `outputs/source_grounding_report.json`.
   - All generated questions must have valid source files.

4. Strengthened coverage validation.
   - The pipeline writes `outputs/coverage_matrix.json`.
   - The matrix compares requested coverage weights against actual coverage
     contribution, not only question titles.
   - Current output passes with zero delta for every topic.

5. Made evaluation stricter.
   - Source gaps now fail structural tests.
   - Invalid source references now fail structural tests.
   - Coverage mismatch now fails structural tests.
   - `outputs/questions.json` stores structured question metadata so the
     evaluator does not need to infer everything from Markdown.

6. Improved answer key usefulness.
   - `outputs/answers.md` now includes learning objectives, grading rubrics,
     coverage contribution, and lecture source references for each question.

7. Added an agentic judge system.
   - `CoverageJudgeAgent`, `SourceGroundingJudgeAgent`,
     `DifficultyBalanceJudgeAgent`, `PedagogicalQualityJudgeAgent`,
     `AnswerRubricJudgeAgent`, `RedTeamJudgeAgent`, and
     `JudgeAggregatorAgent` now evaluate generated exams from distinct
     perspectives.
   - The system writes `outputs/agentic_judge_report.json`.
   - The report includes PASS / REVISE / FAIL decisions, evidence, failed
     checks, and revision instructions.

8. Closed the judge-to-revision loop.
   - `AgenticJudgeSystemAgent` now runs after the first refinement loop.
   - If a question receives `REVISE` or `FAIL`, the pipeline collects the
     judge revision instructions, regenerates/repairs the affected item, and
     reruns the judge system.
   - The loop history is stored in `evaluation_report.json` and
     `run_trace.json`.

9. Added chunk-level grounding and residual-risk artifacts.
   - `outputs/chunk_grounding_report.json` links each question to supporting
     lecture chunks.
   - `outputs/residual_risk_report.json` records known limitations such as
     deterministic-provider reliance, blueprint dependency, and
     self-evaluation bias.
   - `outputs/critical_discussion.md` converts those risks into report-ready
     discussion notes.
   - `outputs/human_review_notes_template.json` gives the team a structured
     way to capture human review.

## Current Evidence

After the change:

- `python src/main.py --provider deterministic --quality draft --max-refine 2`
  generated 11 questions successfully.
- `python src/evaluation.py --provider deterministic --quality draft --simulate-trials 2`
  passed pilot tests and structural tests.
- `outputs/coverage_matrix.json` shows exact coverage alignment.
- `outputs/source_grounding_report.json` shows all 11 questions grounded in
  existing lecture files.
- `outputs/agentic_judge_report.json` gives the current draft a final `PASS`
  verdict across coverage, source grounding, difficulty, pedagogy, answer
  rubric, and red-team checks.
- `outputs/chunk_grounding_report.json` supports all 11 questions with lecture
  chunks.
- `outputs/residual_risk_report.json` explicitly records 5 remaining risks for
  the final critical discussion.

## Remaining Limitations

The deterministic provider is still a local fallback, not a real final exam
generator. For final submission, the team should run the same pipeline with a
real LLM provider in strict mode, then manually review the final exam for
professor style, scope confirmation, and grading fairness.

The current quality checks verify source presence and topic contribution, but
they do not prove that every answer is pedagogically optimal. Human review is
still required for the final 20% of the workflow.
