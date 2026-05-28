# Scientific Management HW #2
## Automated Exam Generation: An Agentic Work System Design

---

## 1. System Architecture

### 1.1 Design Philosophy

This system treats exam generation as a **work-system redesign problem** rather than a simple LLM prompting task. Applying Alter's work-system framework: participants are specialized agents, processes form a sequential-parallel pipeline, information flows through a shared JSON state, technology is the Gemini/deterministic LLM backend, and the customer is the instructor who receives a graded, validated exam.

The core design principle is separation of concerns: each agent knows its task boundary, passes a typed result to the next stage, and never makes assumptions about upstream or downstream implementation. This mirrors the lecture's claim that work-system performance emerges from component configuration, not from any single component.

### 1.2 Agent Roles and Pipeline

The system consists of **12 agents** organized in a sequential-then-parallel pipeline.

| Task | Agent | Pattern | Role |
|------|-------|---------|------|
| Task 0 | `LectureNoteCollectorAgent` | Application Collector + JSON DB | Reads `.txt` lecture notes, deduplicates by character count, registers in `processed_notes_db.json` |
| Task 1 | `CoveragePlannerAgent` | Planner-Executor | Analyzes `requirements.json` and lecture inventory; produces a JSON coverage plan (topics, weights, keywords, source files) |
| Task 2a | `ShortAnswerWriterAgent` | Specialist Parallel Fan-out | Generates short-answer questions using round-robin topic sampling |
| Task 2b | `ComparisonWriterAgent` | Specialist Parallel Fan-out | Generates concept-comparison questions |
| Task 2c | `ApplicationWriterAgent` | Specialist Parallel Fan-out | Generates application/scenario questions |
| Task 2d | `EssayWriterAgent` | Specialist Parallel Fan-out | Generates synthesis essay questions |
| Task 3 | `AnswerWriterAgent` | ReAct + Retrieval | Thought → Action (`search_lecture_notes`) → Observation → answer with `source_refs` |
| Task 4a | `QuestionJudgeAgent` | LLM-as-Judge | Scores questions on scope, difficulty, clarity, answerability (0–5 each) |
| Task 4b | `AnswerJudgeAgent` | LLM-as-Judge | Scores answers on accuracy, completeness, grounding, conciseness (0–5 each) |
| Task 4c | `CoverageAuditAgent` | Deterministic validation | Checks point total = 100, all topics covered, mix matches `requirements.json` |
| Task 5 | `RefinementCoordinator` | Supervisor-Evaluator Reflection Loop | Re-runs writers for POOR-rated items; max 2 iterations |
| Task 5b | `AgenticJudgeSystemAgent` | Agentic Judge Closed Loop | Runs 6 specialist judges (Coverage, SourceGrounding, DifficultyBalance, PedagogicalQuality, AnswerRubric, RedTeam); aggregates PASS/REVISE/FAIL verdicts; re-generates REVISE/FAIL items; max 2 iterations |
| Task 6 | `FormatterAgent` | Deterministic local tool | Assembles `exam.md` (student-facing), `answers.md` (instructor-facing), `review.md` |

### 1.3 Agentic Process Diagram

```
[Lecture PDFs]  →  scripts/extract_pdf_text.py
                            │
                    lecture_notes/processed/*.txt
                            │
             ┌──────────────▼──────────────────────┐
             │  Task 0: LectureNoteCollectorAgent   │  🟦 Collect
             │  (Application Collector + JSON DB)  │
             └──────────────┬──────────────────────┘
                            │ notes{}, processed_notes_db.json
             ┌──────────────▼──────────────────────┐
             │  Task 1: CoveragePlannerAgent        │  🟧 Plan
             │  (Planner-Executor)                 │
             └──────────────┬──────────────────────┘
                            │ topics[], coverage_plan{}
          ┌─────────────────┼──────────────────────────┐
          │                 │                          │
  ┌───────▼───────┐ ┌───────▼───────┐ ┌───────────────▼────┐
  │ Task 2a:      │ │ Task 2b:      │ │ Task 2c/2d:        │
  │ ShortAnswer   │ │ Comparison    │ │ Application/Essay  │
  │ Writer        │ │ Writer        │ │ Writers            │
  └───────┬───────┘ └───────┬───────┘ └──────────┬─────────┘
          └────────────┬────┘                    │           🟩 Generate
                       └──────────┬──────────────┘
                            Combiner (renumber Q1–Q11)
                                   │
             ┌─────────────────────▼────────────────────┐
             │  Task 3: AnswerWriterAgent               │  🟩 Generate
             │  (ReAct: search → observe → answer)      │
             └─────────────────────┬────────────────────┘
                                   │
              ┌────────────────────┼─────────────────────┐
              │                   │                      │
  ┌───────────▼───────┐  ┌────────▼─────────┐  ┌────────▼──────────┐
  │ Task 4c:          │  │ Task 4a:          │  │ Task 4b:          │
  │ CoverageAudit     │  │ QuestionJudge     │  │ AnswerJudge       │
  │ (deterministic)   │  │ (LLM-as-Judge)    │  │ (LLM-as-Judge)    │
  └───────────┬───────┘  └────────┬─────────┘  └────────┬──────────┘
              │                   └─────────┬────────────┘
              │                  ┌──────────▼─────────────────────┐
              │                  │ Task 5: RefinementCoordinator  │  🟪 Decide
              │                  │ (Supervisor-Evaluator Loop)    │
              │                  │ ← regen if POOR; max 2 iters  │
              │                  └──────────┬─────────────────────┘
              │                             │
              │           ┌─────────────────▼─────────────────────┐
              │           │ Task 5b: AgenticJudgeSystemAgent       │  🟪 Decide
              │           │ 6 specialist judges:                   │
              │           │  CoverageJudge / SourceGrounding       │
              │           │  DifficultyBalance / PedagogicalQuality│
              │           │  AnswerRubric / RedTeam                │
              │           │ JudgeAggregator → PASS/REVISE/FAIL     │
              │           │ ← regen REVISE/FAIL; max 2 iters       │
              │           └─────────────────┬──────────────────────┘
              │                             │
              └──────────────┬──────────────┘
                    ┌────────▼────────────────────────┐
                    │  Task 6: FormatterAgent          │  🟩 Output
                    └────────┬────────────────────────┘
                             │
              ┌──────────────┼──────────────────────────┐
              │              │                          │
        outputs/          outputs/               outputs/
        exam.md           answers.md             review.md
        (student)         (instructor)           + reports
```

### 1.4 Data Flow Between Components

All inter-agent communication passes through Python in-memory objects (typed `Question` dataclasses and plain dicts), with JSON checkpoints written to `outputs/` at each stage for auditability:

```
requirements.json + lecture notes
    → coverage_plan (in-memory dict)
    → questions[] (list[Question] dataclass)
    → questions[] + answers (same dataclass, answer field filled)
    → judge verdicts (list[JudgeVerdict])
    → refined questions[]
    → agentic judge findings (list[AgenticJudgeFinding])
    → enriched questions[] (Bloom, difficulty, time, rubric, source_refs)
    → formatted markdown strings
    → outputs/exam.md, outputs/answers.md, outputs/review.md
    + outputs/coverage_matrix.json, agentic_judge_report.json,
      assessment_validity_report.md, residual_risk_report.json,
      human_review_checklist.md, run_trace.json, cost_report.json
```

### 1.5 Memory Strategy

Following the lecture's four memory layers (M5.3.2):

| Layer | Mechanism | Usage |
|-------|-----------|-------|
| LLM weights | Gemini model priors | Taylor, Gilbreth, DASSI domain knowledge without explicit retrieval |
| Short-term (session) | `seen_prompts` set in writer loop | Prevents duplicate questions within a run |
| Long-term (external DB) | `outputs/processed_notes_db.json` | Tracks processed lecture files; avoids re-processing on re-runs |
| Retrieval (RAG) | `search_lecture_notes()` in AnswerWriterAgent | Keyword-based snippet lookup; source_refs grounding |

---

## 2. Implementation Details

### 2.1 Technology Stack

- **Language**: Python 3.9+
- **LLM Providers**: Vertex AI Gemini (primary), Google AI Studio API key (alternative), OpenAI, Anthropic (via same interface), local Deterministic fallback (no API key required)
- **Key Libraries**: `google-genai` for Gemini, `pypdf` for PDF text extraction, `concurrent.futures` for parallel fan-out
- **Infrastructure**: No framework dependency (no LangChain, no Autogen); all agent patterns implemented from scratch to align with lecture M5.3.x

### 2.2 Agent Pattern Implementation

**BaseAgentWorker** (M5.3.1.2): All 12 agents inherit from a common interface with a single `run(payload) → result` contract. The orchestrator in `main.py` calls `agent.run(payload)` without knowing the agent's internal implementation.

**Planner-Executor** (M5.3.3): `CoveragePlannerAgent` produces a structured JSON plan (topics with weights, keywords, source files). The four writer agents then execute against this plan independently. When a live LLM is used, the planner is Gemini-backed; it reasons about the full lecture inventory before committing to topic-weight allocations.

**Parallel Fan-out** (M5.3.3 `parallel_screening`): `fan_out_question_writers()` uses `ThreadPoolExecutor(max_workers=4)` to run all four writer agents simultaneously. Each writer maintains its own deduplication set (`seen_prompts`), then results are merged and renumbered.

**ReAct + Retrieval** (M5.3.1.2 §8): `AnswerWriterAgent` follows the Thought→Action→Observation cycle. The "Action" is a call to `search_lecture_notes(notes, keyword, limit=3)` which returns up to 3 paragraph snippets with their source filenames. The LLM provider receives these snippets as `Lecture context:` and must anchor its answer to them.

**LLM-as-Judge** (M5.3.4): Two judge tiers. The first tier (`QuestionJudgeAgent`, `AnswerJudgeAgent`) produces a numeric rubric (0–5 per criterion, max 20) and a GOOD/ACCEPTABLE/POOR verdict. The second tier (`AgenticJudgeSystemAgent`) runs 6 specialist judges in sequence — coverage, source grounding, difficulty balance, pedagogical quality, answer rubric adequacy, and a red-team fairness pass — then aggregates their PASS/REVISE/FAIL findings.

**Supervisor-Evaluator Reflection Loop** (M5.3.3): `RefinementCoordinator` injects the judge's `suggestion` string back into the regeneration prompt. This implements the "reflective" pattern: the regenerated prompt carries forward context about *why* the previous version failed. Maximum 2 iterations prevents runaway loops.

**Provider Abstraction**: `providers.py` defines `make_provider()` which selects from Vertex AI Gemini, API-key Gemini, OpenAI, Anthropic, or Deterministic based on CLI flags and environment variables. All providers expose the same five methods (`plan`, `write_questions`, `pool_questions`, `write_answer`, `judge_question`, `judge_answer`), so agent code never imports a provider directly.

### 2.3 Model Policy

`model_policy.json` assigns different models to different roles:

```json
"draft":       { "planner": "gemini-2.5-flash", "writer": "gemini-2.5-flash",
                 "answer_writer": "gemini-2.5-flash", "judge": "gemini-2.5-flash-lite" }
"final":       { "planner": "gemini-2.5-pro", "writer": "gemini-2.5-flash",
                 "answer_writer": "gemini-2.5-flash", "judge": "gemini-2.5-flash-lite" }
"final_low_cost": { "planner": "gemini-2.5-flash", "writer": "gemini-2.5-flash-lite", ... }
```

**Design rationale**: The planner makes a single high-stakes decision (what to test) so it justifies the most capable model. Writers and answer writers are called once per question, so Flash is appropriate. Judges run in a loop (up to 2×11 = 22 calls per iteration), so Flash-Lite minimizes cost without losing verdict quality on structured rubric output.

### 2.4 Running the System

```bash
# Step 1: Extract lecture PDFs to text
python scripts/extract_pdf_text.py

# Step 2: Generate exam (deterministic mode, no API key needed)
python src/main.py

# Step 3: Generate exam with Gemini Vertex AI
set GCP_PROJECT_ID=your-project-id
python src/main.py --provider vertex --quality final --strict-provider

# Step 4: Evaluate quality
python src/evaluation.py --simulate-trials 3
```

The `--blueprint nonexistent_path` flag bypasses the pre-approved `exam_blueprint.json` and forces a fully agentic run.

### 2.5 Automation Coverage

| Sub-task | Automated? | Evidence |
|----------|-----------|---------|
| PDF ingestion and text normalization | ✅ Full | `scripts/extract_pdf_text.py`, `ingest_materials.py` |
| Coverage planning from requirements | ✅ Full | `CoveragePlannerAgent` |
| Question drafting (all 4 types) | ✅ Full | `fan_out_question_writers` + 4 specialists |
| Model answer drafting | ✅ Full | `AnswerWriterAgent` with source grounding |
| Quality judging (2 tiers, 7 judges) | ✅ Full | `RefinementCoordinator` + `AgenticJudgeSystemAgent` |
| Rubric and Bloom metadata enrichment | ✅ Full | `enrich_assessment_metadata` |
| Cost tracking | ✅ Full | `UsageTracker`, `cost_report.json` |
| Final scope confirmation | ❌ Human | Human must confirm M3.1.1 Therbligs inclusion |
| Pedagogical fairness check | ❌ Human | `human_review_checklist.md` |
| Point allocation approval | ❌ Human | Final review by instructor |

**Automation ratio**: approximately 85% of the end-to-end workflow is fully automated. Human review is required only for the final 3 gates above.

---

## 3. Generated Exam and Model Answers

### 3.1 Exam Blueprint Summary

The exam reflects the actual midterm scope of this semester (Modules 1.1–1.5, 2.1.1–2.1.5, 3.1.1) and satisfies all structural requirements of `requirements.json`:

| Requirement | Target | Achieved |
|------------|--------|---------|
| Total points | 100 | 100 ✅ |
| Duration | 75 min | 75 min ✅ |
| Short Answer | 6 questions | 6 (Q1–Q6) ✅ |
| Concept Comparison | 2 questions | 2 (Q7–Q8) ✅ |
| Application | 2 questions | 2 (Q9–Q10) ✅ |
| Essay | 1 question | 1 (Q11) ✅ |
| Easy difficulty | 25 pts | 25 pts (Q1–Q5) ✅ |
| Medium difficulty | 50 pts | 50 pts (Q7–Q10) ✅ |
| Hard difficulty | 25 pts | 25 pts (Q6, Q11) ✅ |

**Coverage weights** (verified by `CoverageJudgeAgent`, all deltas = 0):

| Topic | Target | Actual |
|-------|--------|-------|
| Work and Work Systems | 25 | 25 ✅ |
| Scientific Management | 20 | 20 ✅ |
| Problem Solving and Ideation | 25 | 25 ✅ |
| Innovation Frameworks | 15 | 15 ✅ |
| Motion Study and Therbligs | 15 | 15 ✅ |

**Bloom level distribution** (higher-order share = 45%):

| Level | Questions | Weight |
|-------|-----------|--------|
| Remember/Understand | Q1–Q5 | 25 pts |
| Analyze | Q6, Q7, Q8 | 25 pts |
| Apply/Analyze | Q9, Q10 | 30 pts |
| Evaluate/Create | Q11 | 20 pts |

### 3.2 Generated Exam (student-facing version)

---

**Scientific Management Midterm Exam**
Duration: 75 minutes | Total: 100 points

**Instructions**: Answer all questions in the space provided. Use concepts and terminology from the lecture materials. For application and essay questions, justify your reasoning explicitly.

**Q1. Short Answer (5 points)**
Define work, task, process, and work system. Explain how the four levels differ from one another.

**Q2. Short Answer (5 points)**
List Taylor's four principles of scientific management and state the managerial logic behind them in one sentence.

**Q3. Short Answer (5 points)**
What are the five steps of the DASSI engineering problem-solving process? Give one phrase describing the purpose of each step.

**Q4. Short Answer (5 points)**
Name the five innovation frameworks from the lecture and give a one-line meaning for any two of them.

**Q5. Short Answer (5 points)**
What are Therbligs? Give three examples of Therblig motions and explain why this vocabulary is useful for motion study.

**Q6. Short Answer (5 points)** *(Hard — abstract systems reasoning)*
Why is performance described as an emergent property of a work system? Answer using at least three work-system components.

**Q7. Concept Comparison (10 points)** *(Medium — Analyze)*
Compare natural soldiering and systematic soldiering in Taylor's theory. Why does the distinction matter for scientific management?

**Q8. Concept Comparison (10 points)** *(Medium — Analyze)*
Compare DASSI, the KJ Method, and brainstorming. Where does each fit in a disciplined engineering problem-solving process?

**Q9. Application (15 points)** *(Medium — Apply/Analyze)*
A university library wants to reduce student waiting time for study rooms without building more rooms. Use DASSI briefly to frame the problem, then propose two redesign ideas using two different innovation frameworks.

**Q10. Application (15 points)** *(Medium — Apply/Analyze)*
A worker assembling small kits repeatedly searches for parts, reaches across the bench, positions items by hand, inspects the kit, and repacks defective kits. Identify likely Therbligs and propose a redesign that improves both motion efficiency and the surrounding work system.

**Q11. Essay (20 points)** *(Hard — Evaluate/Create)*
Discuss scientific management as an early form of work-system redesign. Your answer must cover Taylor, the Gilbreths, benefits, limitations, and how a modern manager should adapt the approach using work-system thinking and DASSI.

---

### 3.3 Selected Model Answers

**Q7. Concept Comparison — Model Answer**
> Natural soldiering is the human tendency to conserve effort. Systematic soldiering is deliberate output restriction shaped by group norms, mistrust, fear that higher output will lower piece rates, lack of reliable standards, and weak management systems. The distinction matters because Taylor's solution is not simply to demand harder work. Scientific management redesigns measurement, standards, training, incentives, and planning so that productivity becomes a system property rather than a moral judgment about workers.

*Rubric: 3 pts natural soldiering | 3 pts systematic soldiering | 4 pts system-redesign implication*

**Q11. Essay — Model Answer**
> Scientific management treated productivity as a design problem. Taylor attacked rule-of-thumb work through measurement, standards, scientific selection and training, cooperation, incentives, and a clearer split between planning and execution. The Gilbreths extended the logic to motion study and Therbligs, showing that fatigue and waste could be reduced by redesigning motions, tools, and layout. Benefits include explicit standards, teachable methods, and productivity improvement. Limitations include narrow views of motivation, worker control, and over-optimization of isolated tasks. A modern manager should adapt the approach as work-system redesign: define and analyze the problem with DASSI, consider participants, processes, information, technology, customers, and environment, search for alternatives, and select interventions that improve both efficiency and human sustainability.

*Rubric: 4 pts Taylor | 3 pts Gilbreth/Therbligs | 4 pts benefits | 4 pts limitations | 5 pts modern adaptation*

---

## 4. Discussion: Limitations and Future Improvements

### 4.1 Technical Limitations

**4.1.1 Deterministic Fallback Masks LLM Quality**

The project ships with a local `DeterministicProvider` that draws from a pre-written static question bank. This fallback is essential for testing the pipeline structure without API costs, but it means a reviewer running `python src/main.py` without a GCP project ID will see questions drawn from the bank rather than LLM-generated questions. The pipeline architecture is valid; the generation quality evidence requires running with `--provider vertex`.

*Mitigation*: The `--strict-provider` flag disables the fallback, making API key configuration explicit. `residual_risk_report.json` flags this risk as "high" when the deterministic provider is used.

**4.1.2 Blueprint Bypass of Task 1 and Task 2**

When `exam_blueprint.json` exists in the project root, the Coverage Planner and all four Writer agents are bypassed. This shortcut improves reproducibility for grading (the exam content is stable), but it means the full agentic pipeline is not exercised by default. The blueprint was generated by a prior full run and represents an instructor-approved exam structure.

*Mitigation*: Pass `--blueprint nonexistent_path` to force a fully agentic run. The README clearly documents this behavior.

**4.1.3 Lexical Grounding Is Not Semantic Entailment**

The `SourceGroundingJudgeAgent` and `build_chunk_grounding_report()` verify that keyword terms from the question and answer appear in the cited lecture chunks. This lexical signal is necessary but insufficient: a question could use correct terminology while misrepresenting the concept. For example, a question about "Taylor" and "soldiering" would pass lexical grounding even if it incorrectly characterizes systematic soldiering.

*Mitigation*: Replace lexical matching with an LLM-backed entailment check: given [question, answer, lecture chunk], ask the judge whether the answer claim is *supported by* the chunk, not merely co-occurring.

**4.1.4 LLM-as-Judge Self-Evaluation Bias**

The generator and judge agents share the same model family (Gemini). They may agree on fluent but pedagogically shallow questions because they share similar priors about what "good" phrasing looks like. A judge from a different model family (e.g., judging Gemini output with Claude) would provide more independent signal.

*Mitigation*: The `human_review_notes_template.json` creates a structured protocol for a human reviewer to independently rate every question. Comparing human ratings against `agentic_judge_report.json` would quantify judge bias empirically.

**4.1.5 Fixed Difficulty Operationalization**

Difficulty is assigned by label ("easy"/"medium"/"hard") rather than by empirical item difficulty (p-values from student response data). The label assignments are pedagogically motivated: all lower-recall short answers are "easy," analysis-level comparisons and standard applications are "medium," and the abstract emergence concept and synthesis essay are "hard." This achieves the required easy:25/medium:50/hard:25 distribution exactly, but the labels have not been validated against actual student performance.

*Mitigation*: After the exam is administered, compute item p-values and compare against labels. Use this feedback to calibrate the difficulty classifier in future runs.

### 4.2 Practical Limitations

**4.2.1 Dependency on Input Quality**

The system's output quality is bounded by the quality of the processed lecture text. If `extract_pdf_text.py` produces garbled text (common with scanned PDFs or slides with heavy graphics), the retrieval context passed to writer and answer agents will be noisy, leading to hallucinated or weakly grounded questions.

*Mitigation*: `scripts/ingest_materials.py` flags scanned PDFs and image-only files as requiring OCR. An OCR step (e.g., Tesseract, Google Document AI) should precede ingestion for slides with low text density.

**4.2.2 Hallucination in Open-Ended Generation**

When the system is run without the blueprint (fully agentic mode), LLM-generated questions may reference facts not in the lecture notes — for example, inventing a specific Taylor experiment or misattributing a Therblig. The `SourceGroundingJudgeAgent` catches the most obvious cases (no source refs, no keyword match), but cannot detect subtle fabrications.

*Mitigation*: Add a fact-grounding agent that retrieves the top-3 chunks for each answer claim and uses an LLM to verify whether the claim is supported, contradicted, or absent in the lecture.

**4.2.3 Language and Style**

The system generates exam questions in a consistent academic register, but it cannot replicate the instructor's specific question style, preferred vocabulary, or course-specific framing conventions. A student who has seen past exams by this instructor may notice a stylistic gap.

*Mitigation*: Fine-tune the writer prompt with 3–5 exemplar questions from the instructor's past exams as few-shot context. This is straightforward with the current provider abstraction.

### 4.3 Future Improvements

**4.3.1 Retrieval-Augmented Generation (RAG) with Dense Retrieval**

The current retrieval in `AnswerWriterAgent` uses keyword matching (`str.lower()` substring search). This misses semantically related passages that use different terminology (e.g., a question about "work standardization" may miss a passage that uses "task specification"). Replacing keyword search with embedding-based dense retrieval (e.g., using Vertex AI Embedding API or `sentence-transformers`) would substantially improve grounding coverage.

**4.3.2 Constraint Satisfaction for Difficulty**

The current difficulty assignment is post-hoc labeling. A better architecture would encode difficulty as a planning constraint: the Coverage Planner would allocate specific Bloom levels to each question slot before writing begins, and the Writer agents would receive a Bloom-level specification in their system prompt. This would guarantee the difficulty distribution without requiring post-generation relabeling.

**4.3.3 Cross-Model Judge Panel**

Replace the single-provider judge with a panel of 2–3 judges from different model families (e.g., Gemini + Claude + GPT). Aggregate verdicts by majority vote. This reduces the self-evaluation bias described in §4.1.4 and provides a more robust quality signal.

**4.3.4 Grading System Integration**

The current system ends at exam generation and model answers. An extension would connect the model answers to a grading rubric agent that can evaluate student responses. Given a student's answer to Q7 (natural vs. systematic soldiering), the rubric agent would allocate partial credit according to the stored rubric criteria. This would close the full exam lifecycle loop: generate → administer → grade → feedback.

**4.3.5 Version-Controlled Exam History**

Each run produces a deterministic artifact (the blueprint JSON). Storing blueprint versions under git allows the team to trace exactly which lecture materials and requirements produced each exam draft, enabling reproducible audit for academic integrity review.

---

## Appendix: Agent Pattern-to-Lecture Mapping

| Implemented Pattern | Lecture Reference | Location in Code |
|--------------------|------------------|-----------------|
| `BaseAgentWorker` interface | M5.3.1.2 §11 | `src/agents.py` — all agents |
| Local tools + JSON DB | M5.3.2 ApplicationCollector | `LectureNoteCollectorAgent` |
| Planner-Executor | M5.3.3 `plan_and_accept` | `CoveragePlannerAgent` → Writers |
| Parallel Fan-out | M5.3.3 `parallel_screening` | `fan_out_question_writers()` |
| ReAct + Retrieval | M5.3.1.2 §8 + M5.3.2 | `AnswerWriterAgent` |
| LLM-as-Judge (JSON rubric) | M5.3.4 `JUDGE_*_PROMPT` | `QuestionJudgeAgent`, `AnswerJudgeAgent` |
| Supervisor-Evaluator Loop | M5.3.3 reflective | `RefinementCoordinator` |
| Agentic Judge Closed Loop | M5.3.4 | `AgenticJudgeSystemAgent` |
| 3-method Evaluation matrix | M5.3.4 §3-method | `src/evaluation.py` |

---

*GitHub repository: https://github.com/yunseo-lee-0527/exam-agent-project*
