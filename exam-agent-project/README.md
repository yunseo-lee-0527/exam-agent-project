# Automated Exam Generation Project

Team project for Scientific Management HW #2.

## Goal

Build an LLM-based agentic work system that automates at least 80% of the exam generation workflow:

- Read instructor lecture notes and requirements
- Plan exam coverage
- Generate exam questions
- Generate model answers
- Review and refine the generated exam
- Export the final exam paper and answer key

## Project Structure

```text
exam-agent-project/
  lecture_notes/
    raw/          # Put original lecture materials here
    processed/    # Cleaned or extracted text files
  src/            # System implementation code
  prompts/        # Agent prompts
  outputs/        # Generated exam, answers, and review files
  report/         # Final PDF report materials
  docs/           # Planning docs, scope summaries, diagrams
  scripts/        # Utility scripts
```

## Quick Start

Extract text from PDF lecture materials:

```bash
python scripts/extract_pdf_text.py
```

Generate the current exam draft:

```bash
python src/main.py
```

Generated files will be written to:

```text
outputs/exam.md
outputs/answers.md
outputs/review.md
```

## Current Plan

1. Collect all lecture materials up to the midterm.
2. Summarize the actual exam scope.
3. Design the agentic workflow.
4. Implement a minimum runnable system.
5. Generate a Scientific Management midterm exam and model answers.
6. Review limitations and future improvements.
7. Package the report, code, and generated output for submission.

## Lecture Materials

Place all files related to the midterm scope in:

```text
lecture_notes/raw/
```

Accepted materials can include:

- PDF lecture slides
- Word documents
- Text files
- Professor announcements
- Personal notes
- Any file specifying the midterm scope

## Providers

The pipeline supports two providers, swapped at the boundary by
`src/providers.py`. Agent boundaries do not move when switching.

### Deterministic (default, no API key)

Uses a static question/answer bank for offline runs and CI. Round-robin
across topic clusters, with prompt-level dedup so no two questions
share text.

```bash
python src/main.py
# or explicitly
python src/main.py --provider deterministic
```

### Gemini (Vertex AI, follows M5.3.1.1)

Uses `gemini-2.5-pro` for the planner, `gemini-2.5-flash` for question
and answer writers, and `gemini-2.5-flash-lite` for both judges.

```bash
pip install google-genai
gcloud auth application-default login   # local; in Colab use auth.authenticate_user()
export GCP_PROJECT_ID=<your-project-id>
export GCP_LOCATION=us-central1         # optional, default us-central1
python src/main.py --provider gemini
```

`EXAM_AGENT_PROVIDER=gemini` is honored as a fallback when the
`--provider` flag is omitted. If a single Gemini call fails, that one
call falls back to the deterministic provider so the pipeline keeps
running and the failure is logged.

## Evaluation

Run the offline harness from M5.3.4 (golden set + LLM Judge + simulation):

```bash
python src/evaluation.py --simulate-trials 3
# Gemini-backed
python src/evaluation.py --provider gemini --simulate-trials 3
```

Output: `outputs/evaluation_report.json`.
