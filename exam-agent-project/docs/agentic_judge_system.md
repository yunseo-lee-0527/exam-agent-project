# Agentic Judge System

This project now includes a specialist judge layer after exam generation and
basic refinement. The goal is to make evaluation more agentic than a single
LLM-as-judge score: each judge checks a different risk, records evidence, and
returns a structured PASS / SOFT_FAIL / HARD_FAIL finding.

The judge layer is now connected to a closed revision loop. If the aggregator
returns `REVISE` or `FAIL` for a specific question, the pipeline collects the
revision instructions, regenerates or repairs the affected question/answer, and
runs the judge system again up to the configured iteration limit.

## Judge Agents

The system is implemented in `src/agents.py`.

| Agent | Role | Failure Type |
| --- | --- | --- |
| `CoverageJudgeAgent` | Checks total points and topic coverage against `requirements.json`. | Hard fail |
| `SourceGroundingJudgeAgent` | Checks that every question has valid lecture-source references and basic evidence matches. | Hard or soft fail |
| `DifficultyBalanceJudgeAgent` | Checks point-weighted easy/medium/hard balance. | Soft fail |
| `PedagogicalQualityJudgeAgent` | Checks learning objective, lecture specificity, and higher-order cognitive demand. | Soft fail |
| `AnswerRubricJudgeAgent` | Checks model-answer presence and rubric usefulness. | Hard or soft fail |
| `RedTeamJudgeAgent` | Reads prompts from a student perspective for ambiguity, vagueness, and unclear partial credit. | Soft fail |
| `JudgeAggregatorAgent` | Aggregates specialist findings into target-level PASS / REVISE / FAIL decisions. | Final decision |
| `AgenticJudgeSystemAgent` | Orchestrates all judge agents and writes the final report. | System wrapper |

## Output

Each run writes:

- `outputs/agentic_judge_report.json`
- `outputs/chunk_grounding_report.json`
- `outputs/residual_risk_report.json`
- `outputs/critical_discussion.md`
- `outputs/human_review_notes_template.json`

The report includes:

- final exam-level verdict
- per-question decisions
- failed checks, if any
- evidence from each judge
- revision instructions for generator or human reviewer
- judge execution trace
- judge-loop history
- chunk-level lecture evidence
- residual risks for critical discussion

Example report shape:

```json
{
  "final_verdict": "PASS",
  "summary": {
    "targets": 12,
    "pass": 12,
    "revise": 0,
    "fail": 0
  },
  "target_decisions": {
    "EXAM": {"final_verdict": "PASS"},
    "Q1": {"final_verdict": "PASS"}
  }
}
```

## Why This Matters

This improves the project against three evaluation criteria:

- **System Design Quality**: evaluation is decomposed into specialized agents,
  not one opaque score.
- **Implementation Completeness**: the pipeline now emits machine-readable
  judge evidence and revision instructions.
- **Critical Discussion**: the report makes residual risks explicit, which can
  be discussed in the final paper.

## Current Run

The current deterministic draft passes the agentic judge system:

- final verdict: `PASS`
- coverage: exact 25 / 20 / 25 / 15 / 15 topic contribution
- difficulty: exact 25 / 50 / 25 easy/medium/hard contribution
- source grounding: all 11 questions have valid lecture-source references
- chunk grounding: all 11 questions have at least one supporting lecture chunk
- agentic judge loop: completed in 1 iteration with no non-pass targets

The system is still intended as a quality-control layer. The final submission
should be regenerated with a real LLM provider in strict mode, then reviewed by
a human for professor style and scope confirmation.
