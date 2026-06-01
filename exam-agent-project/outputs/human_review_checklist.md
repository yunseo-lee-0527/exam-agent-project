# Human Review Checklist

Use this before submitting the final generated exam.

## Scope

- [ ] Confirm the official midterm scope with the professor/TA.
- [ ] Confirm whether M3.1.1 Therbligs is included.
- [ ] Confirm that no question depends on out-of-scope material.

## Exam Quality

- [ ] Check that every question is answerable from lecture materials.
- [ ] Check that short answer, comparison, application, and essay questions match the requested mix.
- [ ] Remove duplicate or overly similar questions.
- [ ] Verify point allocation and expected duration.
- [ ] Check language, grammar, and professor style.
- [ ] Review outputs/agentic_judge_report.json and address every REVISE or FAIL target.
- [ ] Record final human decisions in outputs/human_review_notes_template.json.

## Model Answers

- [ ] Verify factual accuracy of every model answer.
- [ ] Check that source references match lecture files.
- [ ] Add grading rubric details for essay/application questions if needed.

## Provider and Cost

- [ ] Provider mode is correct for final generation. Estimated cost: $0.000000.
- [ ] If using Gemini/OpenAI/Anthropic, confirm strict provider mode for the final run.
- [ ] Confirm no private API keys or credentials are committed.

## Generated Questions

- [ ] Q1: Short Answer / Work and Work Systems / 5 points
- [ ] Q2: Short Answer / Scientific Management / 5 points
- [ ] Q3: Short Answer / Problem Solving and Ideation / 5 points
- [ ] Q4: Short Answer / Five Innovation Frameworks / 5 points
- [ ] Q5: Short Answer / Motion Study and Therbligs / 5 points
- [ ] Q6: Short Answer / Work and Work Systems / 5 points
- [ ] Q7: Concept Comparison / Scientific Management / 10 points
- [ ] Q8: Concept Comparison / Problem Solving and Ideation / 10 points
- [ ] Q9: Application / Five Innovation Frameworks / 15 points
- [ ] Q10: Application / Motion Study and Therbligs / 15 points
- [ ] Q11: Essay / Scientific Management / 20 points

## Coverage Audit Notes

- Coverage contribution by topic key: {'work_and_work_systems': 25, 'scientific_management': 20, 'problem_solving_and_ideation': 25, 'innovation_frameworks': 15, 'motion_study_and_therbligs': 15}.
- Question type mix: {'Short Answer': 6, 'Concept Comparison': 2, 'Application': 2, 'Essay': 1}.

## Requirements Snapshot

```json
{
  "course": "Scientific Management",
  "exam_name": "Midterm Exam",
  "language": "English",
  "target_duration_minutes": 75,
  "question_mix": {
    "short_answer": 6,
    "concept_comparison": 2,
    "application": 2,
    "essay": 1
  },
  "coverage_weights": {
    "work_and_work_systems": 25,
    "scientific_management": 20,
    "problem_solving_and_ideation": 25,
    "innovation_frameworks": 15,
    "motion_study_and_therbligs": 15
  },
  "difficulty": {
    "easy": 25,
    "medium": 50,
    "hard": 25
  },
  "notes": [
    "Use multiple question types.",
    "Reflect actual lecture materials up to the midterm.",
    "Require application and critical discussion, not only memorization."
  ]
}
```
