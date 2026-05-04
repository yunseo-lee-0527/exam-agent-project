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

