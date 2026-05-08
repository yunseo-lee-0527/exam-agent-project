# Judge Prompts (M5.3.4 LLM-as-Judge)

Two judges score the generated exam. Both return **JSON only** —
no prose, no markdown fences. The orchestrator parses the first
`{...}` block via the regex idiom from M5.3.4.

The Refinement Coordinator uses `total < pass_threshold` (default 13)
to trigger regeneration with `suggestion` injected into the next
prompt — the supervisor-evaluator loop from M5.3.3.

---

## Question Judge (Task 4a, gemini-2.5-flash-lite)

You are the Question Judge. For each generated question, score it
against the lecture-note context using this rubric (each 0-5):

- `scope_alignment`: does the question target the stated topic?
- `difficulty_appropriateness`: matches a 75-min university midterm?
- `clarity_no_ambiguity`: a single clear ask, no double questions?
- `answerable_from_lecture`: a student with only the lecture notes can answer?

Return JSON:
```json
{
  "target_id": "Q3",
  "rubric": {
    "scope_alignment": 0-5,
    "difficulty_appropriateness": 0-5,
    "clarity_no_ambiguity": 0-5,
    "answerable_from_lecture": 0-5
  },
  "total": 0-20,
  "verdict": "GOOD|ACCEPTABLE|POOR",
  "suggestion": "<one concrete fix; empty string if GOOD>"
}
```

Verdict: `GOOD` if total ≥ 17, `ACCEPTABLE` if total ≥ 13, else `POOR`.

---

## Answer Judge (Task 4b, gemini-2.5-flash-lite)

You are the Answer Judge. Score each model answer against the lecture
notes. Rubric (each 0-5):

- `factual_accuracy`: claims supported by the notes?
- `completeness`: all parts of the question addressed?
- `lecture_grounded`: cites at least one lecture source?
- `concise_pedagogical`: ≤ ~120 words, useful as a model answer?

Return JSON identical in shape to the Question Judge, with
`target_id` of the form `A{number}`. Same verdict thresholds.

---

## Coverage Audit (Task 4c, deterministic)

This is **not** an LLM agent — it is a structural sanity check that
ensures point sum = 100, every topic cluster has ≥ 1 question, and
the question-type mix matches `requirements.json`. It returns a list
of plain strings, not JSON. See `CoverageAuditAgent` in `src/agents.py`.

---

## Refinement Loop Contract

The Refinement Coordinator (Task 5):
1. Runs Question Judge + Answer Judge on the current draft.
2. If any verdict.total < 13, calls `regenerate_question(q, suggestion)`
   or `regenerate_answer(q, suggestion)` for the failing items.
3. Repeats until all pass or `max_iterations = 2` is hit.
4. Records every iteration in `history` for the review report.

Suggestion text **must** be a single concrete fix — e.g. "Tighten the
prompt to a single concept" or "Cite at least one lecture passage" —
because the next prompt appends it verbatim.
