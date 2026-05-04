# Project Plan

## Assignment Summary

Design and implement an LLM-based agentic work system for automated exam generation.

The system should take:

- Instructor lecture notes
- Instructor requirements such as difficulty, format, and coverage

The system should produce:

- A complete Scientific Management midterm exam paper
- Model answers for all questions

## Proposed Agent Architecture

```text
Lecture notes + instructor requirements
        |
        v
Input Parser Agent
        |
        v
Coverage Planner Agent
        |
        v
Question Writer Agent
        |
        v
Answer Writer Agent
        |
        v
Reviewer Agent
        |
        v
Formatter Agent
        |
        v
Exam paper + model answers + review notes
```

## Team Roles

1. Project manager and integration lead
2. Scientific Management content lead
3. Agent architecture lead
4. LLM pipeline developer
5. Input preprocessing lead
6. Output formatting lead
7. Quality review lead
8. Report and presentation lead

## One-Month Milestones

### Week 1: Scope and Design

- Collect all lecture materials up to the midterm
- Identify actual exam scope
- Draft agent architecture
- Define output format and question types
- Create initial README and project structure

### Week 2: Minimum Runnable System

- Implement lecture-note ingestion
- Implement requirements loading
- Connect LLM API
- Generate first exam draft
- Generate first model answer draft

### Week 3: Quality Improvement

- Add reviewer agent
- Improve coverage control
- Improve question diversity
- Check generated answers against lecture scope
- Produce near-final generated output

### Week 4: Final Submission

- Complete report PDF
- Finalize architecture diagram
- Clean code and README
- Run full system test
- Package code, report, generated exam, and model answers

## Immediate Next Step

Upload all lecture materials up to the midterm into:

```text
lecture_notes/raw/
```

After that, we will summarize the exam scope and design the first runnable version of the system.

