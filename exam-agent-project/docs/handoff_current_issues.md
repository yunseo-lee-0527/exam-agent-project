# Handoff: current exam-agent issues

Use this as a prompt for the next engineer:

```text
You are taking over an automated exam generation project.

Repository state:
- Work on the existing main branch. The user explicitly asked not to create a new branch.
- Recent code changes are in src/agents.py, src/main.py, and src/providers.py.
- The latest strict run was:
  python src/main.py --provider gemini --quality final_low_cost --model-preset cheap --strict-provider --blueprint nonexistent_path --max-refine 2 --max-agentic-judge-refine 5
- That run intentionally stopped with RuntimeError because the agentic judge did not pass:
  Remaining targets: Q10, Q2, Q9.
- The safety gate now prevents exam.md, answers.md, questions.json, and related final outputs from being overwritten when agentic_judge_report.final_verdict is not PASS. In a failed run, outputs/agentic_judge_report.json and outputs/run_trace.json still reflect the latest failure.

What was already fixed:
- Question writers now draft only the question prompt, topic, source_refs, and focus. They no longer draft answer/rubric/metadata in the same LLM call.
- AnswerWriterAgent is forced to regenerate answers and rubrics after question generation or regeneration.
- Question.focus was added and mandatory focus markers are stripped from student-facing prompts.
- Rubric point allocations are normalized to integer points that sum to the item total.
- The final formatter is blocked when the agentic judge does not pass.
- Factual HARD_FAILs now block the run instead of being downgraded into warning evidence.
- Provider answer/judge calls now use question-aware context instead of only broad topic context.

Known remaining failures from the latest strict run:
1. Q2 overlaps with Q8.
   - The report says Q2 and Q8 both touch the DASSI Search for Alternatives / idea-generation area.
   - This means overlap detection is working, but regeneration still failed to find a sufficiently distinct replacement before the iteration budget ended.
   - Fix direction: make the planner and regeneration step collision-aware before generation, not only after judging. Add a structured "avoid_focuses" or "forbidden_neighbor_topics" field and force regeneration to select a different focus, not just rewrite wording.

2. Q2 factual grounding failure.
   - Judge evidence says the provided excerpts did not support activities in Search for Alternatives or the minimum number of alternatives.
   - The lecture notes do contain relevant DASSI material in lecture_notes/processed/M2.1.1 Engineering Problem-Solving Process.txt around "Step 3: Search for Alternatives".
   - Likely root cause: retrieval context did not reliably include the exact supporting chunk.
   - Fix direction: source_refs should point to chunk IDs or line spans, not only filenames. _question_context should include exact cited chunks first, then additional retrieval.

3. Q9 factual grounding failure.
   - Judge objected to "reduced product cost" in a Subtraction example.
   - This may be unsupported if the prompt asks for a common household appliance but the notes only ground the framework generally.
   - Fix direction: either allow reasoned application claims when the question is explicitly an application task, or restrict application questions to lecture-provided examples. Do not mix strict "explicitly present only" factual judging with open-ended invented examples.

4. Q10 factual grounding failure.
   - Judge claimed the excerpts did not mention Gilbreth/Therbligs, even though lecture_notes/processed/M3.1.1 Micro-level Motion Study (Therbligs)_041626.txt does contain "Therbligs" and "Developed by Gilbreth".
   - Likely root cause: factual judge context retrieval is still under-selecting or presenting the wrong excerpts despite source_refs containing the right file.
   - Fix direction: add deterministic retrieval tests proving that _question_context for Q10 includes the M3.1.1 Therbligs chunk before calling the LLM judge.

Highest priority code work:
1. Add retrieval unit tests:
   - Q10 focus "definition and purpose of Therbligs" must retrieve M3.1.1 lines around "Therbligs - Basic Motion Elements" and "Developed by Gilbreth".
   - Q2 focus "DASSI Search for Alternatives" must retrieve M2.1.1 lines around "Step 3: Search for Alternatives".
   - Q9 focus "Subtraction framework" must retrieve M2.1.5 lines around "Subtraction framework".
2. Replace filename-only source_refs with chunk refs or line-span refs.
3. Make planning/regeneration use explicit non-overlap constraints:
   - Store focus terms per question.
   - Pass used focuses and forbidden overlapping terms into write_questions and regen_question.
   - Validate focus uniqueness deterministically before invoking the LLM judge.
4. Decide the factual standard for application questions:
   - Option A: strict lecture-only examples. Then prompts must use examples from notes.
   - Option B: allow grounded extrapolation. Then factual judge must distinguish lecture facts from plausible application reasoning.
5. Add a failed-candidate output file if needed:
   - outputs/agentic_judge_report.json currently reflects failed in-memory candidates.
   - final exam/answer files may reflect the last PASS run because failed runs do not overwrite them.
   - A separate outputs/failed_candidate_questions.json would make debugging less confusing.

Useful files to inspect first:
- src/main.py
- src/agents.py
- src/providers.py
- outputs/agentic_judge_report.json
- outputs/run_trace.json
- lecture_notes/processed/M2.1.1 Engineering Problem-Solving Process.txt
- lecture_notes/processed/M2.1.5 Systematic Innovation Methods 1 (Five Frameworks).txt
- lecture_notes/processed/M3.1.1 Micro-level Motion Study (Therbligs)_041626.txt
- docs/scope.md

Do not assume a generated exam is acceptable just because exam.md exists. The current intended correctness signal is outputs/agentic_judge_report.json final_verdict == PASS.
```
