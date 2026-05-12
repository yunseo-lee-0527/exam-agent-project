from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Common interface (M5.3.1.2 / M5.3.2 / M5.3.3 — BaseAgentWorker)
# ---------------------------------------------------------------------------


class BaseAgentWorker:
    """Common interface for every APD task.

    The orchestrator calls run() without knowing the agent kind
    (reflexive / ReAct / planner / supervisor-evaluator).
    """

    def __init__(self, name: str, task_id: str):
        self.name = name
        self.task_id = task_id

    def run(self, payload: Any) -> Any:
        raise NotImplementedError(f"{self.__class__.__name__}.run() not implemented")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, task={self.task_id!r})"


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass
class Topic:
    key: str
    title: str
    weight: int
    keywords: list[str]
    source_files: list[str]


@dataclass
class Question:
    number: int
    kind: str
    topic: str
    prompt: str
    points: int
    answer: str = ""
    source_refs: list[str] = field(default_factory=list)
    difficulty: str = ""
    learning_objective: str = ""
    rubric: list[str] = field(default_factory=list)
    coverage_contribution: dict[str, int] = field(default_factory=dict)


@dataclass
class JudgeVerdict:
    """JSON-only output produced by an LLM-as-Judge agent (M5.3.4)."""

    target_id: str
    rubric: dict[str, int]
    total: int
    verdict: str  # GOOD | ACCEPTABLE | POOR
    suggestion: str


@dataclass
class AgenticJudgeFinding:
    """Structured finding from a specialist judge agent.

    verdict:
      - PASS: no actionable issue found.
      - SOFT_FAIL: revise before final submission.
      - HARD_FAIL: regenerate or block final submission.
    """

    target_id: str
    judge: str
    verdict: str
    failed_checks: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    revision_instruction: str = ""


# ---------------------------------------------------------------------------
# Local tools (M5.3.2 local-tool pattern, no LLM required)
# ---------------------------------------------------------------------------


def search_lecture_notes(notes: dict[str, str], keyword: str, limit: int = 3) -> list[str]:
    """Return up to `limit` short snippets where `keyword` appears in any note."""

    keyword_lc = keyword.lower()
    hits: list[str] = []
    for filename, body in notes.items():
        for paragraph in body.split("\n\n"):
            if keyword_lc in paragraph.lower():
                snippet = paragraph.strip().replace("\n", " ")
                if len(snippet) > 240:
                    snippet = snippet[:237] + "..."
                hits.append(f"[{filename}] {snippet}")
                if len(hits) >= limit:
                    return hits
    return hits


def normalize_filename(name: str) -> str:
    # Preserve module prefix case (M1.1 etc.) so downstream matching works.
    # Only collapse internal whitespace.
    return " ".join(name.split())


def parse_json_block(raw: str) -> dict[str, Any]:
    """Extract the first JSON object inside a (possibly fenced) string.

    Mirrors the M5.3.4 `re.search(r'\\{[^{}]+\\}', raw, re.DOTALL)` idiom.
    """

    cleaned = raw.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except Exception:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
    return {}


# ---------------------------------------------------------------------------
# Provider hook (deterministic by default; swap with Gemini/OpenAI later)
# ---------------------------------------------------------------------------


class DeterministicProvider:
    """Local fallback so the project runs without an API key.

    Each method returns the same structure that an LLM-backed provider
    would return, letting the agent boundaries stay stable when the
    provider is swapped (M5.3.2 standard pattern).
    """

    def plan(self, requirements: dict[str, Any], notes: dict[str, str]) -> dict[str, Any]:
        weights = requirements.get("coverage_weights", {})
        mix = requirements.get("question_mix", {})
        clusters = [
            (
                "work_and_work_systems",
                "Work and Work Systems",
                ["work", "task", "process", "work system", "emergence"],
                ("M1.1", "M1.2", "M1.3"),
            ),
            (
                "scientific_management",
                "Scientific Management",
                ["Taylor", "soldiering", "pig iron", "shovel", "Gilbreth"],
                ("M1.4",),
            ),
            (
                "problem_solving_and_ideation",
                "Problem Solving and Ideation",
                ["DASSI", "KJ Method", "Concept Fan", "brainstorming"],
                ("M2.1.1", "M2.1.2", "M2.1.3"),
            ),
            (
                "innovation_frameworks",
                "Five Innovation Frameworks",
                ["addition", "subtraction", "alternate means", "combination", "transposition"],
                ("M2.1.5",),
            ),
            (
                "motion_study_and_therbligs",
                "Motion Study and Therbligs",
                ["motion study", "Therbligs", "reach", "grasp", "pre-position"],
                ("M3.1.1",),
            ),
        ]
        filenames = list(notes)
        topics = []
        for key, title, keywords, prefixes in clusters:
            topics.append(
                {
                    "key": key,
                    "title": title,
                    "weight": int(weights.get(key, 20)),
                    "keywords": list(keywords),
                    "source_files": [f for f in filenames if any(f.startswith(p) for p in prefixes)],
                }
            )
        return {
            "topics": topics,
            "question_mix": dict(mix),
            "rationale": "Topic clusters derived from M1.x / M2.x / M3.x lecture modules; weights from requirements.",
        }

    def write_questions(
        self, kind: str, topic: Topic, count: int, notes: dict[str, str]
    ) -> list[dict[str, str]]:
        return _DETERMINISTIC_BANK.draw(kind, topic, count, notes)

    def pool_questions(
        self, kind: str, topic: Topic, notes: dict[str, str]
    ) -> list[dict[str, str]]:
        """Return every available candidate for (kind, topic). Used by writers
        to dedup across the round-robin without asking the provider for a
        deterministic count up front."""

        return _DETERMINISTIC_BANK.all_for(kind, topic)

    def write_answer(self, question: Question, notes: dict[str, str]) -> dict[str, Any]:
        if question.answer and question.source_refs:
            return {"answer": question.answer, "source_refs": question.source_refs}
        return {
            "answer": _DETERMINISTIC_BANK.answer_for(question),
            "source_refs": [
                snippet.split("]")[0][1:]
                for keyword in [question.topic.split()[0]]
                for snippet in search_lecture_notes(notes, keyword, limit=2)
            ],
        }

    def judge_question(self, question: Question, notes: dict[str, str]) -> dict[str, Any]:
        scope_hits = sum(1 for kw in question.prompt.split() if any(kw.lower() in body.lower() for body in notes.values()))
        scope = 5 if scope_hits >= 3 else 4 if scope_hits >= 1 else 2
        clarity = 5 if 12 <= len(question.prompt.split()) <= 60 else 3
        difficulty = {"Short Answer": 4, "Concept Comparison": 5, "Application": 5, "Essay": 5}.get(question.kind, 4)
        answerable = 5 if question.answer else 3
        total = scope + clarity + difficulty + answerable
        verdict = "GOOD" if total >= 17 else "ACCEPTABLE" if total >= 13 else "POOR"
        return {
            "target_id": f"Q{question.number}",
            "rubric": {
                "scope_alignment": scope,
                "difficulty_appropriateness": difficulty,
                "clarity_no_ambiguity": clarity,
                "answerable_from_lecture": answerable,
            },
            "total": total,
            "verdict": verdict,
            "suggestion": "" if verdict == "GOOD" else "Tighten the prompt to a single concept and reference one lecture term.",
        }

    def judge_answer(self, question: Question, notes: dict[str, str]) -> dict[str, Any]:
        grounded = 5 if question.source_refs else 2
        accuracy = 5 if question.answer and len(question.answer.split()) >= 20 else 3
        completeness = 5 if len(question.answer.split()) >= 30 else 3
        concise = 5 if len(question.answer.split()) <= 120 else 3
        total = grounded + accuracy + completeness + concise
        verdict = "GOOD" if total >= 17 else "ACCEPTABLE" if total >= 13 else "POOR"
        return {
            "target_id": f"A{question.number}",
            "rubric": {
                "factual_accuracy": accuracy,
                "completeness": completeness,
                "lecture_grounded": grounded,
                "concise_pedagogical": concise,
            },
            "total": total,
            "verdict": verdict,
            "suggestion": "" if verdict == "GOOD" else "Cite at least one lecture passage and add one concrete example.",
        }


# Deterministic question/answer bank for the local fallback. The LLM
# provider replaces this entirely; the bank only exists so the project
# runs without a GCP key. Each (kind, topic) carries 2-3 entries so the
# round-robin writer can fill any reasonable mix without duplicates.
class _Bank:
    SHORT_ANSWER = [
        ("Work and Work Systems",
         "Define work, task, process, and work system. Explain how these levels differ.",
         "Work spans levels: a motion is a small physical or cognitive unit, a task is a purposeful activity, a process is a set of interrelated activities transforming inputs into outputs, and a work system is the organized socio-technical system in which processes occur."),
        ("Work and Work Systems",
         "Why is performance described as an emergent property of a work system rather than a property of any single component?",
         "A work system's outputs depend on interactions among participants, processes, technology, information, environment, and customers. Improving one component in isolation rarely improves the whole, because emergent performance arises from the configuration and coordination of all components."),
        ("Work and Work Systems",
         "List the major component categories Alter uses to describe a work system.",
         "Alter's framework names participants, processes and activities, information, technology, products and services, customers, environment, infrastructure, and strategies."),
        ("Scientific Management",
         "List Taylor's four principles of scientific management.",
         "Develop a science for each element of work, scientifically select and train workers, promote cooperation through aligned incentives, and divide planning from execution responsibilities."),
        ("Scientific Management",
         "Define soldiering and distinguish its natural and systematic forms.",
         "Soldiering is deliberate underperformance at work. Natural soldiering is the human tendency to conserve effort, while systematic soldiering is deliberate output restriction shaped by mistrust, fear of wage cuts, missing standards, and flawed organization."),
        ("Problem Solving and Ideation",
         "What are the five steps of the DASSI engineering problem-solving process?",
         "Define the problem, Analyze with data, Search for alternatives, Select the best alternative through comparison, Implement with follow-up."),
        ("Problem Solving and Ideation",
         "State two fundamental principles of brainstorming and explain why they matter.",
         "Delayed judgment prevents premature criticism from blocking creativity, and focus on quantity increases the chance of useful ideas surfacing among many alternatives."),
        ("Problem Solving and Ideation",
         "Briefly describe the KJ Method (affinity diagramming) and one situation in which it is useful.",
         "The KJ Method gathers language data such as facts, ideas, and opinions, groups items by natural relationships, and surfaces hidden connections. It is useful when a problem statement is messy or ambiguous and the team needs shared understanding before choosing a solution path."),
        ("Five Innovation Frameworks",
         "Name the five innovation frameworks introduced in the lecture.",
         "Addition, subtraction, alternate means, combination, and transposition."),
        ("Five Innovation Frameworks",
         "Define the subtraction and combination innovation frameworks with one short example each.",
         "Subtraction removes a component or feature to simplify or improve a system, such as a frameless touchscreen phone. Combination merges existing technologies or services into a new whole, such as combining payment, messaging, and ride-hailing in one app."),
        ("Motion Study and Therbligs",
         "What are Therbligs, and why are they useful in motion study?",
         "Therbligs are basic motion elements developed by Gilbreth that let analysts identify unnecessary motions, reduce fatigue, and improve task efficiency."),
        ("Motion Study and Therbligs",
         "Give three examples of Therblig motions and one motion-economy improvement that targets each.",
         "Search can be reduced by fixed tool locations and labels; reach can be shortened by bringing parts inside the normal work area; and position can be eased by guides, fixtures, or pre-positioning of components."),
    ]
    COMPARISON = [
        ("Work and Work Systems",
         "Compare Alter's work system view with a narrow technology-centered view of improvement.",
         "A technology-centered view treats technology as the object of improvement. Alter's view treats technology as one component of a broader system that also includes participants, processes, information, products, customers, environment, infrastructure, and strategies, so performance is emergent."),
        ("Work and Work Systems",
         "Compare an open-system view of work with a closed-system view, and explain why the lecture treats work systems as open.",
         "A closed-system view ignores environment and only models internal mechanics. An open-system view explicitly models inputs, outputs, customers, and external constraints. The lecture uses the open view because real work systems are shaped by environment, regulation, and customers as much as by internal processes."),
        ("Scientific Management",
         "Compare natural soldiering and systematic soldiering in Taylor's diagnosis of inefficiency.",
         "Natural soldiering is the tendency to conserve effort. Systematic soldiering is deliberate output restriction shaped by mistrust, fear of wage cuts, lack of standards, and flawed organization. Taylor framed it as a system design problem."),
        ("Scientific Management",
         "Compare Taylor's contributions and Gilbreth's contributions to scientific management.",
         "Taylor focused on principles, work standards, and incentive systems with cases such as pig iron handling and shovel studies. Gilbreth focused on motion study and Therbligs to reduce fatigue and unnecessary motion. Together they reframed productivity as a system-design problem rather than a worker-effort problem."),
        ("Problem Solving and Ideation",
         "Compare DASSI with the PDCA improvement cycle, identifying one structural similarity and one difference.",
         "Both decompose improvement into define/diagnose, plan, act, and review steps. PDCA loops continuously through Plan-Do-Check-Act, while DASSI front-loads alternative search and selection before implementation, making it better suited to one-off engineering redesign rather than continuous quality improvement."),
        ("Five Innovation Frameworks",
         "Compare the addition framework and the subtraction framework, and explain when each is more suitable.",
         "Addition adds features to enhance utility, ergonomics, or safety, and is suitable when users are missing a capability. Subtraction removes components to simplify or focus, and is suitable when complexity has accumulated faster than user benefit. The choice depends on whether usability or capability is the constraint."),
        ("Motion Study and Therbligs",
         "Compare worker-level improvement and system-level improvement in the context of motion study.",
         "Worker-level improvement targets how an individual performs motions, e.g., training and pacing. System-level improvement targets layout, tool placement, and workflow that shape what motions are required at all. The lecture treats system-level improvement as more durable and as the primary work-system redesign lever."),
    ]
    APPLICATION = [
        ("Problem Solving and Ideation",
         "A hospital outpatient clinic has long waiting times and frustrated staff. Apply DASSI to outline an improvement project.",
         "Define the wait-time problem precisely, analyze arrival and bottleneck data, separate symptoms from root causes, generate alternatives such as scheduling or information-system redesign, compare them by effectiveness, cost, feasibility, safety, and compatibility, and implement the chosen solution with follow-up monitoring."),
        ("Problem Solving and Ideation",
         "A startup's brainstorming sessions consistently produce few ideas and many arguments. Diagnose the failure using brainstorming principles and propose two specific corrective actions.",
         "The session likely violates delayed judgment and the no-criticism rule, and probably skips the idea-purge phase. Two corrections: explicitly separate idea generation from evaluation phases with a strict no-critique rule, and require a quantity target with short snappy ideas to defeat fixation."),
        ("Motion Study and Therbligs",
         "A worker repeatedly searches for screws, reaches across the table, grasps a screwdriver, positions a part, and assembles it. Identify likely Therbligs and propose two improvements.",
         "Likely Therbligs include search, reach, grasp, move, position, and assemble. Improvements include fixed tool locations and color coding to reduce search, pre-positioning screws to remove search, and shorter reach distances or guides for positioning."),
        ("Work and Work Systems",
         "Analyze a familiar service such as a coffee shop using Alter's work system framework, identifying at least four components and one emergent performance issue.",
         "Components include participants (baristas, customers), processes (order, brew, deliver), technology (espresso machines, POS), information (menu, queue state), and customers (walk-in, mobile order). An emergent issue is queue backup at peak times that cannot be fixed by speeding any single station because it arises from the interaction between order placement, brew time, and pickup capacity."),
        ("Five Innovation Frameworks",
         "Pick a generative-AI product feature and explain it using two of the five innovation frameworks.",
         "An AI summarization feature combines a language model with a productivity app (combination) and replaces manual reading and note-taking with model-generated summaries (alternate means). Using two frameworks clarifies that the innovation is both an integration choice and a substitution choice."),
        ("Scientific Management",
         "Apply scientific management thinking to redesign a packing job at an e-commerce warehouse, listing two principles you use and one risk you must guard against.",
         "Use 'develop a science of the work' by measuring pack times and standardizing motion, and 'divide planning from execution' by giving an industrial engineer responsibility for layout while packers focus on packing. Risk: narrow scientific management can ignore worker fatigue and motivation, so a human-centered design and feedback loop must be added."),
    ]
    ESSAY = [
        ("Scientific Management",
         "Discuss scientific management as an early form of work system redesign. Cover Taylor, Gilbreth, benefits, and limitations.",
         "Scientific management applied observation, measurement, standardization, and redesign to organized human work. Taylor's principles and cases such as pig iron handling and shovel studies showed productivity gains. Gilbreth's motion study and Therbligs reduced fatigue. Limitations include narrow models of motivation, worker control issues, and the need for human-centered work system design."),
        ("Work and Work Systems",
         "Argue why work-system thinking is necessary in addition to task analysis when redesigning modern operations such as a hospital, platform, or AI-enabled service.",
         "Task analysis decomposes individual activities but misses the configuration that produces system-level outcomes. Work-system thinking adds participants, technology, information, environment, and customer interactions, so emergent issues like queue backup, coordination failure, or information loss become visible. Modern operations are tightly coupled, so improvements localized to a single task often shift bottlenecks rather than removing them; work-system thinking gives a frame for choosing where to intervene."),
        ("Problem Solving and Ideation",
         "Synthesize DASSI, brainstorming, and the KJ Method into a coherent problem-solving toolkit, explaining where each tool fits and one limitation of the combined approach.",
         "DASSI provides the overall structure: define, analyze, search, select, implement. The KJ Method strengthens 'define' and 'analyze' by surfacing hidden structure in messy language data. Brainstorming feeds 'search' by generating many alternatives under delayed judgment. A limitation: the toolkit assumes the problem owner can convene cross-functional input; in highly siloed organizations the resistance-to-change step inside DASSI's 'implement' phase often dominates and requires explicit change-management treatment beyond what these methods provide."),
    ]

    @staticmethod
    def _bank_for(kind: str) -> list[tuple[str, str, str]]:
        return {
            "Short Answer": _Bank.SHORT_ANSWER,
            "Concept Comparison": _Bank.COMPARISON,
            "Application": _Bank.APPLICATION,
            "Essay": _Bank.ESSAY,
        }[kind]

    def draw(self, kind: str, topic: Topic, count: int, notes: dict[str, str]) -> list[dict[str, str]]:
        bank = self._bank_for(kind)
        on_topic = [item for item in bank if item[0] == topic.title]
        off_topic = [item for item in bank if item[0] != topic.title]
        chosen = on_topic[:count]
        if len(chosen) < count:
            chosen += off_topic[: count - len(chosen)]
        return [{"topic": t, "prompt": p, "answer": a} for t, p, a in chosen[:count]]

    def all_for(self, kind: str, topic: Topic) -> list[dict[str, str]]:
        """Return every entry available for (kind, topic) — used for dedup pools."""

        return [
            {"topic": t, "prompt": p, "answer": a}
            for t, p, a in self._bank_for(kind)
            if t == topic.title
        ]

    def answer_for(self, question: Question) -> str:
        for bank in (self.SHORT_ANSWER, self.COMPARISON, self.APPLICATION, self.ESSAY):
            for topic_title, prompt, answer in bank:
                if prompt == question.prompt:
                    return answer
        return "Answer not available; regenerate with LLM provider."


_DETERMINISTIC_BANK = _Bank()


# ---------------------------------------------------------------------------
# Task 0 — LectureNoteCollectorAgent (APD blue, M5.3.2 Application Collector)
# ---------------------------------------------------------------------------


class LectureNoteCollectorAgent(BaseAgentWorker):
    """Loads and validates processed lecture text, then registers metadata.

    Mirrors the Application Collector pattern: local validation tools +
    long-term JSON DB so re-runs detect already-processed files.
    """

    def __init__(self, db_path: Path | None = None):
        super().__init__(name="Lecture Note Collector", task_id="Task 0")
        self.db_path = db_path

    def run(self, processed_dir: Path) -> dict[str, Any]:
        notes: dict[str, str] = {}
        skipped: list[str] = []
        for path in sorted(processed_dir.glob("*.txt")):
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                skipped.append(path.name)
                continue
            notes[normalize_filename(path.name)] = text

        if not notes:
            raise FileNotFoundError(
                f"No processed lecture text found in {processed_dir}. "
                "Run scripts/extract_pdf_text.py or add .txt files."
            )

        registered = self._register(notes) if self.db_path else None
        return {
            "notes": notes,
            "registered_modules": registered,
            "skipped": skipped,
        }

    def _register(self, notes: dict[str, str]) -> list[str]:
        # Long-term JSON DB pattern from M5.3.2 (ApplicationDatabase).
        db: dict[str, dict[str, int]] = {}
        if self.db_path and self.db_path.exists():
            try:
                db = json.loads(self.db_path.read_text(encoding="utf-8"))
            except Exception:
                db = {}
        new_keys: list[str] = []
        for filename, body in notes.items():
            if filename in db and db[filename].get("chars") == len(body):
                continue
            db[filename] = {"chars": len(body)}
            new_keys.append(filename)
        if self.db_path:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path.write_text(json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8")
        return new_keys


# ---------------------------------------------------------------------------
# Task 1 — CoveragePlannerAgent (APD orange, Planner-Executor M5.3.3)
# ---------------------------------------------------------------------------


class CoveragePlannerAgent(BaseAgentWorker):
    """Builds a JSON exam blueprint via the Planner-Executor pattern."""

    def __init__(self, provider: Any = None):
        super().__init__(name="Coverage Planner", task_id="Task 1")
        self.provider = provider or DeterministicProvider()

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        requirements = payload["requirements"]
        notes = payload["notes"]
        plan = self.provider.plan(requirements, notes)
        topics = [Topic(**t) for t in plan["topics"]]
        return {
            "plan": plan,
            "topics": topics,
        }


# ---------------------------------------------------------------------------
# Task 2 — Question Writer specialists (APD green, parallel fan-out M5.3.3)
# ---------------------------------------------------------------------------


class _BaseQuestionWriter(BaseAgentWorker):
    KIND: str = ""

    def __init__(self, name: str, task_id: str, provider: Any = None):
        super().__init__(name=name, task_id=task_id)
        self.provider = provider or DeterministicProvider()

    def run(self, payload: dict[str, Any]) -> list[Question]:
        topics: list[Topic] = payload["topics"]
        notes: dict[str, str] = payload["notes"]
        count: int = payload["count"]
        start_number: int = payload.get("start_number", 1)
        per_topic_points: int = payload["points_per_question"]

        ordered = sorted(topics, key=lambda t: -t.weight)
        # Pre-fetch a pool of candidates per topic so we can dedup deterministically.
        # Each call returns the topic's full available set (deterministic) or a
        # fresh batch (LLM provider).
        pools: dict[str, list[dict[str, str]]] = {
            t.title: list(self.provider.pool_questions(self.KIND, t, notes))
            for t in ordered
        }
        cursors: dict[str, int] = {t.title: 0 for t in ordered}
        seen_prompts: set[str] = set()

        questions: list[Question] = []
        # Round-robin across topics, popping next unused candidate.
        while len(questions) < count:
            progressed = False
            for topic in ordered:
                if len(questions) >= count:
                    break
                pool = pools[topic.title]
                cursor = cursors[topic.title]
                while cursor < len(pool):
                    draft = pool[cursor]
                    cursor += 1
                    if draft["prompt"] in seen_prompts:
                        continue
                    questions.append(
                        Question(
                            number=start_number + len(questions),
                            kind=self.KIND,
                            topic=draft["topic"],
                            prompt=draft["prompt"],
                            points=per_topic_points,
                            answer=draft.get("answer", ""),
                        )
                    )
                    seen_prompts.add(draft["prompt"])
                    progressed = True
                    break
                cursors[topic.title] = cursor

            if not progressed:
                # Pools exhausted. Ask the provider for a fresh batch on the
                # highest-weight topics that still need fills.
                refilled = False
                for topic in ordered:
                    if len(questions) >= count:
                        break
                    fresh = self.provider.write_questions(
                        self.KIND, topic, count - len(questions), notes
                    )
                    new_items = [d for d in fresh if d["prompt"] not in seen_prompts]
                    if new_items:
                        pools[topic.title].extend(new_items)
                        refilled = True
                if not refilled:
                    break
        return questions


class ShortAnswerWriterAgent(_BaseQuestionWriter):
    KIND = "Short Answer"

    def __init__(self, provider: Any = None):
        super().__init__("Short Answer Writer", "Task 2a", provider)


class ComparisonWriterAgent(_BaseQuestionWriter):
    KIND = "Concept Comparison"

    def __init__(self, provider: Any = None):
        super().__init__("Comparison Writer", "Task 2b", provider)


class ApplicationWriterAgent(_BaseQuestionWriter):
    KIND = "Application"

    def __init__(self, provider: Any = None):
        super().__init__("Application Writer", "Task 2c", provider)


class EssayWriterAgent(_BaseQuestionWriter):
    KIND = "Essay"

    def __init__(self, provider: Any = None):
        super().__init__("Essay Writer", "Task 2d", provider)


def fan_out_question_writers(
    writers: list[_BaseQuestionWriter],
    payloads: list[dict[str, Any]],
    max_workers: int = 4,
) -> list[Question]:
    """ThreadPoolExecutor fan-out, M5.3.3 parallel_screening pattern."""

    assert len(writers) == len(payloads)
    results: list[list[Question]] = [[] for _ in writers]
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(w.run, p): i for i, (w, p) in enumerate(zip(writers, payloads))}
        for future in futures:
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                results[idx] = []
                print(f"[fan_out] writer {writers[idx].name} failed: {exc}")
    flat: list[Question] = []
    next_n = 1
    for batch in results:
        for q in batch:
            q.number = next_n
            next_n += 1
            flat.append(q)
    return flat


# ---------------------------------------------------------------------------
# Task 3 — AnswerWriterAgent (APD green, ReAct + retrieval tool)
# ---------------------------------------------------------------------------


class AnswerWriterAgent(BaseAgentWorker):
    """ReAct: Thought (locate keyword) -> Action (search_lecture_notes) -> Observation -> answer."""

    def __init__(self, provider: Any = None):
        super().__init__("Answer Writer", "Task 3")
        self.provider = provider or DeterministicProvider()

    def run(self, payload: dict[str, Any]) -> list[Question]:
        questions: list[Question] = payload["questions"]
        notes: dict[str, str] = payload["notes"]
        for q in questions:
            if q.answer and q.source_refs:
                q.source_refs = list(dict.fromkeys(q.source_refs))
                continue
            result = self.provider.write_answer(q, notes)
            if not q.answer:
                q.answer = result.get("answer", "")
            q.source_refs = list(dict.fromkeys(q.source_refs + (result.get("source_refs", []) or [])))
        return questions


# ---------------------------------------------------------------------------
# Task 4 — Judge agents (APD purple, M5.3.4 LLM-as-Judge)
# ---------------------------------------------------------------------------


class QuestionJudgeAgent(BaseAgentWorker):
    def __init__(self, provider: Any = None):
        super().__init__("Question Judge", "Task 4a")
        self.provider = provider or DeterministicProvider()

    def run(self, payload: dict[str, Any]) -> list[JudgeVerdict]:
        questions: list[Question] = payload["questions"]
        notes: dict[str, str] = payload["notes"]
        verdicts: list[JudgeVerdict] = []
        for q in questions:
            raw = self.provider.judge_question(q, notes)
            verdicts.append(JudgeVerdict(**{k: raw[k] for k in ("target_id", "rubric", "total", "verdict", "suggestion")}))
        return verdicts


class AnswerJudgeAgent(BaseAgentWorker):
    def __init__(self, provider: Any = None):
        super().__init__("Answer Judge", "Task 4b")
        self.provider = provider or DeterministicProvider()

    def run(self, payload: dict[str, Any]) -> list[JudgeVerdict]:
        questions: list[Question] = payload["questions"]
        notes: dict[str, str] = payload["notes"]
        verdicts: list[JudgeVerdict] = []
        for q in questions:
            raw = self.provider.judge_answer(q, notes)
            verdicts.append(JudgeVerdict(**{k: raw[k] for k in ("target_id", "rubric", "total", "verdict", "suggestion")}))
        return verdicts


class CoverageAuditAgent(BaseAgentWorker):
    """Deterministic structural checks (point sum, topic mix, type mix)."""

    def __init__(self):
        super().__init__("Coverage Audit", "Task 4c")

    def run(self, payload: dict[str, Any]) -> list[str]:
        topics: list[Topic] = payload["topics"]
        questions: list[Question] = payload["questions"]
        target_mix: dict[str, int] = payload.get("target_mix", {})
        target_weights: dict[str, int] = payload.get("target_weights", {})
        notes: list[str] = []

        covered_titles = {q.topic for q in questions}
        for topic in topics:
            if topic.title not in covered_titles:
                notes.append(f"Coverage gap: no question targets {topic.title}.")

        total_points = sum(q.points for q in questions)
        if total_points != 100:
            notes.append(f"Point total is {total_points}; consider normalizing to 100.")

        if any(q.coverage_contribution for q in questions):
            actual_weights: dict[str, int] = {}
            for q in questions:
                for key, points in q.coverage_contribution.items():
                    actual_weights[key] = actual_weights.get(key, 0) + int(points)
            notes.append(f"Coverage contribution by topic key: {actual_weights}.")
            for key, expected in target_weights.items():
                actual = actual_weights.get(key, 0)
                if actual != int(expected):
                    notes.append(f"Coverage weight mismatch: {key} got {actual}, expected {expected}.")

        kind_counts: dict[str, int] = {}
        for q in questions:
            kind_counts[q.kind] = kind_counts.get(q.kind, 0) + 1
        for kind, expected in target_mix.items():
            label = _MIX_LABEL.get(kind, kind)
            actual = kind_counts.get(label, 0)
            if actual != expected:
                notes.append(f"Mix mismatch: {label} got {actual}, expected {expected}.")

        notes.append(f"Question type mix: {kind_counts}.")
        return notes


def _point_contribution(questions: list[Question]) -> dict[str, int]:
    contribution: dict[str, int] = {}
    for q in questions:
        if q.coverage_contribution:
            for key, points in q.coverage_contribution.items():
                contribution[key] = contribution.get(key, 0) + int(points)
        else:
            key = q.topic.lower().replace(" and ", "_").replace(" ", "_")
            contribution[key] = contribution.get(key, 0) + q.points
    return contribution


def _difficulty_contribution(questions: list[Question]) -> dict[str, int]:
    contribution = {"easy": 0, "medium": 0, "hard": 0, "unspecified": 0}
    for q in questions:
        level = (q.difficulty or "unspecified").lower()
        if level not in contribution:
            level = "unspecified"
        contribution[level] += q.points
    return contribution


def _pass(target_id: str, judge: str, evidence: list[str] | None = None) -> AgenticJudgeFinding:
    return AgenticJudgeFinding(
        target_id=target_id,
        judge=judge,
        verdict="PASS",
        evidence=evidence or [],
    )


class CoverageJudgeAgent(BaseAgentWorker):
    """Hard-fail judge for exam-level topic coverage and point totals."""

    def __init__(self):
        super().__init__("Coverage Judge", "Task 4d")

    def run(self, payload: dict[str, Any]) -> list[AgenticJudgeFinding]:
        questions: list[Question] = payload["questions"]
        requirements: dict[str, Any] = payload["requirements"]
        target = {str(k): int(v) for k, v in requirements.get("coverage_weights", {}).items()}
        actual = _point_contribution(questions)
        failed: list[str] = []
        evidence = [
            f"target_coverage={target}",
            f"actual_coverage={actual}",
            f"total_points={sum(q.points for q in questions)}",
        ]

        if sum(q.points for q in questions) != 100:
            failed.append("point_total")
        for key, expected in target.items():
            if actual.get(key, 0) != expected:
                failed.append(f"coverage:{key}")
        if failed:
            return [
                AgenticJudgeFinding(
                    target_id="EXAM",
                    judge=self.name,
                    verdict="HARD_FAIL",
                    failed_checks=failed,
                    evidence=evidence,
                    revision_instruction="Regenerate the blueprint so topic contribution exactly matches requirements.json and total points equal 100.",
                )
            ]
        return [_pass("EXAM", self.name, evidence)]


class SourceGroundingJudgeAgent(BaseAgentWorker):
    """Checks whether every question has valid lecture-note grounding."""

    STOPWORDS = {
        "what",
        "from",
        "give",
        "using",
        "with",
        "where",
        "does",
        "each",
        "them",
        "this",
        "that",
        "your",
        "answer",
        "explain",
        "state",
        "list",
        "name",
        "cover",
        "must",
        "should",
        "wants",
        "without",
    }
    PRIORITY_TERMS = {
        "taylor",
        "gilbreth",
        "therblig",
        "therbligs",
        "dassi",
        "scientific",
        "management",
        "soldiering",
        "innovation",
        "frameworks",
        "subtraction",
        "combination",
        "brainstorming",
        "method",
        "motion",
        "work",
        "system",
        "redesign",
    }

    def __init__(self):
        super().__init__("Source Grounding Judge", "Task 4e")

    def run(self, payload: dict[str, Any]) -> list[AgenticJudgeFinding]:
        questions: list[Question] = payload["questions"]
        notes: dict[str, str] = payload["notes"]
        findings: list[AgenticJudgeFinding] = []
        for q in questions:
            failed: list[str] = []
            evidence: list[str] = []
            refs = list(dict.fromkeys(q.source_refs))
            if not refs:
                failed.append("missing_source_refs")
            missing = [ref for ref in refs if ref not in notes]
            if missing:
                failed.append("invalid_source_refs")
                evidence.append(f"missing_refs={missing}")

            matched_refs = [ref for ref in refs if ref in notes]
            raw_terms = re.findall(r"[A-Za-z][A-Za-z-]{3,}", q.prompt + " " + q.answer)
            prompt_terms: list[str] = []
            for word in raw_terms:
                term = word.lower()
                if term in self.STOPWORDS or term in prompt_terms:
                    continue
                prompt_terms.append(term)
                if len(prompt_terms) >= 20:
                    break
            ref_text = "\n".join(notes[ref].lower() for ref in matched_refs)
            hits = [term for term in prompt_terms if term in ref_text]
            hits = sorted(hits, key=lambda term: (term not in self.PRIORITY_TERMS, prompt_terms.index(term)))
            if matched_refs and not hits:
                failed.append("weak_evidence_match")
                evidence.append("No salient prompt terms were found in referenced lecture files.")
            if matched_refs:
                evidence.append("valid_refs=" + ", ".join(matched_refs))
            if hits:
                evidence.append("matched_terms=" + ", ".join(hits[:6]))

            if failed:
                verdict = "HARD_FAIL" if "missing_source_refs" in failed or "invalid_source_refs" in failed else "SOFT_FAIL"
                findings.append(
                    AgenticJudgeFinding(
                        target_id=f"Q{q.number}",
                        judge=self.name,
                        verdict=verdict,
                        failed_checks=failed,
                        evidence=evidence,
                        revision_instruction="Attach valid lecture-note source_refs and rewrite the prompt/answer so the cited notes directly support the tested concept.",
                    )
                )
            else:
                findings.append(_pass(f"Q{q.number}", self.name, evidence))
        return findings


class DifficultyBalanceJudgeAgent(BaseAgentWorker):
    """Exam-level judge for easy/medium/hard balance by points."""

    def __init__(self, tolerance_points: int = 5):
        super().__init__("Difficulty Balance Judge", "Task 4f")
        self.tolerance_points = tolerance_points

    def run(self, payload: dict[str, Any]) -> list[AgenticJudgeFinding]:
        questions: list[Question] = payload["questions"]
        requirements: dict[str, Any] = payload["requirements"]
        target = {str(k): int(v) for k, v in requirements.get("difficulty", {}).items()}
        actual = _difficulty_contribution(questions)
        failed = [
            key
            for key, expected in target.items()
            if abs(actual.get(key, 0) - expected) > self.tolerance_points
        ]
        if actual.get("unspecified", 0):
            failed.append("unspecified")
        evidence = [f"target_difficulty={target}", f"actual_difficulty={actual}"]
        if failed:
            return [
                AgenticJudgeFinding(
                    target_id="EXAM",
                    judge=self.name,
                    verdict="SOFT_FAIL",
                    failed_checks=[f"difficulty:{key}" for key in failed],
                    evidence=evidence,
                    revision_instruction="Adjust question difficulty labels or replace questions so point-weighted easy/medium/hard balance follows requirements.json.",
                )
            ]
        return [_pass("EXAM", self.name, evidence)]


class PedagogicalQualityJudgeAgent(BaseAgentWorker):
    """Checks learning objective, cognitive demand, and lecture-specificity."""

    def __init__(self):
        super().__init__("Pedagogical Quality Judge", "Task 4g")

    def run(self, payload: dict[str, Any]) -> list[AgenticJudgeFinding]:
        questions: list[Question] = payload["questions"]
        findings: list[AgenticJudgeFinding] = []
        for q in questions:
            failed: list[str] = []
            evidence: list[str] = []
            prompt_lc = q.prompt.lower()
            if not q.learning_objective:
                failed.append("missing_learning_objective")
            if q.kind in {"Application", "Essay", "Concept Comparison"}:
                higher_order_terms = ["apply", "compare", "discuss", "analyze", "redesign", "propose", "why"]
                if not any(term in prompt_lc for term in higher_order_terms):
                    failed.append("weak_higher_order_demand")
            lecture_terms = [
                "taylor",
                "gilbreth",
                "therblig",
                "dassi",
                "kj method",
                "brainstorm",
                "innovation",
                "work system",
                "soldiering",
            ]
            if not any(term in prompt_lc or term in q.answer.lower() for term in lecture_terms):
                failed.append("weak_lecture_specificity")
            evidence.append(f"kind={q.kind}; difficulty={q.difficulty or 'unspecified'}")
            if q.learning_objective:
                evidence.append(f"learning_objective={q.learning_objective}")

            if failed:
                findings.append(
                    AgenticJudgeFinding(
                        target_id=f"Q{q.number}",
                        judge=self.name,
                        verdict="SOFT_FAIL",
                        failed_checks=failed,
                        evidence=evidence,
                        revision_instruction="Clarify the learning objective and make the prompt test a lecture-specific concept with appropriate cognitive demand.",
                    )
                )
            else:
                findings.append(_pass(f"Q{q.number}", self.name, evidence))
        return findings


class AnswerRubricJudgeAgent(BaseAgentWorker):
    """Checks answer completeness and grading-rubric usefulness."""

    def __init__(self):
        super().__init__("Answer Rubric Judge", "Task 4h")

    def run(self, payload: dict[str, Any]) -> list[AgenticJudgeFinding]:
        questions: list[Question] = payload["questions"]
        findings: list[AgenticJudgeFinding] = []
        for q in questions:
            failed: list[str] = []
            evidence = [
                f"answer_words={len(q.answer.split())}",
                f"rubric_items={len(q.rubric)}",
            ]
            if not q.answer:
                failed.append("missing_model_answer")
            elif len(q.answer.split()) < 20:
                failed.append("thin_model_answer")
            if not q.rubric:
                failed.append("missing_rubric")
            elif q.points >= 10 and len(q.rubric) < 3:
                failed.append("thin_rubric")

            if failed:
                verdict = "HARD_FAIL" if "missing_model_answer" in failed or "missing_rubric" in failed else "SOFT_FAIL"
                findings.append(
                    AgenticJudgeFinding(
                        target_id=f"Q{q.number}",
                        judge=self.name,
                        verdict=verdict,
                        failed_checks=failed,
                        evidence=evidence,
                        revision_instruction="Expand the model answer and rubric so a grader can assign partial credit consistently.",
                    )
                )
            else:
                findings.append(_pass(f"Q{q.number}", self.name, evidence))
        return findings


class RedTeamJudgeAgent(BaseAgentWorker):
    """Student-perspective ambiguity and fairness judge."""

    def __init__(self):
        super().__init__("Red-Team Judge", "Task 4i")

    def run(self, payload: dict[str, Any]) -> list[AgenticJudgeFinding]:
        questions: list[Question] = payload["questions"]
        findings: list[AgenticJudgeFinding] = []
        for q in questions:
            failed: list[str] = []
            prompt_lc = q.prompt.lower()
            evidence = [f"prompt_words={len(q.prompt.split())}"]
            if len(q.prompt.split()) > 70 and q.kind != "Essay":
                failed.append("overlong_prompt")
            vague_terms = ["familiar", "etc.", "some", "appropriate"]
            if any(term in prompt_lc for term in vague_terms):
                failed.append("vague_wording")
            if q.kind == "Application" and not any(mark in q.prompt for mark in [".", ";", ":"]):
                failed.append("underspecified_scenario")
            if q.points >= 10 and len(q.rubric) < 3:
                failed.append("partial_credit_unclear")

            if failed:
                findings.append(
                    AgenticJudgeFinding(
                        target_id=f"Q{q.number}",
                        judge=self.name,
                        verdict="SOFT_FAIL",
                        failed_checks=failed,
                        evidence=evidence,
                        revision_instruction="Tighten wording, add scenario constraints, and clarify how partial credit should be awarded.",
                    )
                )
            else:
                findings.append(_pass(f"Q{q.number}", self.name, evidence))
        return findings


class JudgeAggregatorAgent(BaseAgentWorker):
    """Aggregates specialist judge findings into final decisions."""

    def __init__(self):
        super().__init__("Judge Aggregator", "Task 4j")

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        findings: list[AgenticJudgeFinding] = payload["findings"]
        by_target: dict[str, list[AgenticJudgeFinding]] = {}
        for finding in findings:
            by_target.setdefault(finding.target_id, []).append(finding)

        target_decisions: dict[str, dict[str, Any]] = {}
        for target_id, target_findings in sorted(by_target.items()):
            hard = [f for f in target_findings if f.verdict == "HARD_FAIL"]
            soft = [f for f in target_findings if f.verdict == "SOFT_FAIL"]
            final = "FAIL" if hard else "REVISE" if soft else "PASS"
            target_decisions[target_id] = {
                "final_verdict": final,
                "failed_checks": [check for f in hard + soft for check in f.failed_checks],
                "revision_instructions": [f.revision_instruction for f in hard + soft if f.revision_instruction],
            }

        verdicts = [item["final_verdict"] for item in target_decisions.values()]
        final_verdict = "FAIL" if "FAIL" in verdicts else "REVISE" if "REVISE" in verdicts else "PASS"
        return {
            "final_verdict": final_verdict,
            "target_decisions": target_decisions,
            "summary": {
                "targets": len(target_decisions),
                "pass": verdicts.count("PASS"),
                "revise": verdicts.count("REVISE"),
                "fail": verdicts.count("FAIL"),
            },
        }


class AgenticJudgeSystemAgent(BaseAgentWorker):
    """Runs specialist judges, then aggregates their evidence and verdicts."""

    def __init__(self):
        super().__init__("Agentic Judge System", "Task 5b")
        self.judges = [
            CoverageJudgeAgent(),
            SourceGroundingJudgeAgent(),
            DifficultyBalanceJudgeAgent(),
            PedagogicalQualityJudgeAgent(),
            AnswerRubricJudgeAgent(),
            RedTeamJudgeAgent(),
        ]
        self.aggregator = JudgeAggregatorAgent()

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        findings: list[AgenticJudgeFinding] = []
        trace: list[dict[str, Any]] = []
        for judge in self.judges:
            judge_findings = judge.run(payload)
            findings.extend(judge_findings)
            trace.append(
                {
                    "task": judge.task_id,
                    "agent": judge.name,
                    "findings": len(judge_findings),
                    "non_pass": sum(1 for f in judge_findings if f.verdict != "PASS"),
                }
            )
        aggregate = self.aggregator.run({"findings": findings})
        trace.append(
            {
                "task": self.aggregator.task_id,
                "agent": self.aggregator.name,
                "final_verdict": aggregate["final_verdict"],
            }
        )
        return {
            "final_verdict": aggregate["final_verdict"],
            "summary": aggregate["summary"],
            "target_decisions": aggregate["target_decisions"],
            "findings": [finding.__dict__ for finding in findings],
            "trace": trace,
        }


_MIX_LABEL = {
    "short_answer": "Short Answer",
    "concept_comparison": "Concept Comparison",
    "application": "Application",
    "essay": "Essay",
}


# ---------------------------------------------------------------------------
# Task 5 — RefinementCoordinator (Supervisor-Evaluator loop, M5.3.3)
# ---------------------------------------------------------------------------


class RefinementCoordinator(BaseAgentWorker):
    """Re-runs writers when judges return POOR, up to max_iterations.

    Mirrors notification_sender_reflective from M5.3.3: a generator + a
    judge JSON -> if not passed, inject `suggestion` into next prompt.
    """

    def __init__(
        self,
        question_judge: QuestionJudgeAgent,
        answer_judge: AnswerJudgeAgent,
        regenerate_question: Callable[[Question, str, dict[str, str]], Question],
        regenerate_answer: Callable[[Question, str, dict[str, str]], Question],
        max_iterations: int = 2,
        pass_threshold: int = 13,
    ):
        super().__init__("Refinement Coordinator", "Task 5")
        self.question_judge = question_judge
        self.answer_judge = answer_judge
        self.regenerate_question = regenerate_question
        self.regenerate_answer = regenerate_answer
        self.max_iterations = max_iterations
        self.pass_threshold = pass_threshold

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        questions: list[Question] = payload["questions"]
        notes: dict[str, str] = payload["notes"]
        history: list[dict[str, Any]] = []

        for iteration in range(1, self.max_iterations + 1):
            q_verdicts = self.question_judge.run({"questions": questions, "notes": notes})
            a_verdicts = self.answer_judge.run({"questions": questions, "notes": notes})

            failed_q = [v for v in q_verdicts if v.total < self.pass_threshold]
            failed_a = [v for v in a_verdicts if v.total < self.pass_threshold]
            history.append(
                {
                    "iteration": iteration,
                    "q_total_avg": sum(v.total for v in q_verdicts) / max(1, len(q_verdicts)),
                    "a_total_avg": sum(v.total for v in a_verdicts) / max(1, len(a_verdicts)),
                    "failed_questions": [v.target_id for v in failed_q],
                    "failed_answers": [v.target_id for v in failed_a],
                }
            )

            if not failed_q and not failed_a:
                return {"questions": questions, "verdicts_q": q_verdicts, "verdicts_a": a_verdicts, "history": history}

            for v in failed_q:
                idx = int(v.target_id[1:]) - 1
                if 0 <= idx < len(questions):
                    questions[idx] = self.regenerate_question(questions[idx], v.suggestion, notes)
            for v in failed_a:
                idx = int(v.target_id[1:]) - 1
                if 0 <= idx < len(questions):
                    questions[idx] = self.regenerate_answer(questions[idx], v.suggestion, notes)

        # Final scoring pass after the last regeneration.
        q_verdicts = self.question_judge.run({"questions": questions, "notes": notes})
        a_verdicts = self.answer_judge.run({"questions": questions, "notes": notes})
        return {"questions": questions, "verdicts_q": q_verdicts, "verdicts_a": a_verdicts, "history": history}


# ---------------------------------------------------------------------------
# Task 6 — FormatterAgent (APD green, deterministic local tool)
# ---------------------------------------------------------------------------


class FormatterAgent(BaseAgentWorker):
    def __init__(self):
        super().__init__("Formatter", "Task 6")

    def run(self, payload: dict[str, Any]) -> dict[str, str]:
        requirements = payload["requirements"]
        questions: list[Question] = payload["questions"]
        coverage_notes: list[str] = payload.get("coverage_notes", [])
        verdicts_q: list[JudgeVerdict] = payload.get("verdicts_q", [])
        verdicts_a: list[JudgeVerdict] = payload.get("verdicts_a", [])
        history: list[dict[str, Any]] = payload.get("history", [])

        return {
            "exam_md": self._render_exam(requirements, questions),
            "answers_md": self._render_answers(requirements, questions),
            "review_md": self._render_review(coverage_notes, verdicts_q, verdicts_a, history),
        }

    @staticmethod
    def _render_exam(requirements: dict[str, Any], questions: list[Question]) -> str:
        lines = [
            f"# {requirements['course']} {requirements['exam_name']}",
            "",
            f"Duration: {requirements.get('target_duration_minutes', 'TBD')} minutes",
            "",
            "## Instructions",
            "",
            "- Answer all questions.",
            "- Use concepts and examples from the lecture materials.",
            "- For application and essay questions, justify your reasoning.",
            "",
        ]
        for q in questions:
            lines += [
                f"## Q{q.number}. {q.kind} ({q.points} points)",
                "",
                f"Topic: {q.topic}",
                "",
                q.prompt,
                "",
            ]
        return "\n".join(lines)

    @staticmethod
    def _render_answers(requirements: dict[str, Any], questions: list[Question]) -> str:
        lines = [f"# Model Answers: {requirements['course']} {requirements['exam_name']}", ""]
        for q in questions:
            lines += [f"## Q{q.number}. {q.kind}", "", q.answer or "(answer pending)", ""]
            if q.learning_objective:
                lines += [f"Learning objective: {q.learning_objective}", ""]
            if q.rubric:
                lines += ["Rubric:", ""]
                lines += [f"- {item}" for item in q.rubric]
                lines.append("")
            if q.coverage_contribution:
                coverage = ", ".join(f"{k}: {v}" for k, v in q.coverage_contribution.items())
                lines += [f"Coverage contribution: {coverage}", ""]
            if q.source_refs:
                lines += ["Sources: " + ", ".join(q.source_refs), ""]
        return "\n".join(lines)

    @staticmethod
    def _render_review(
        coverage_notes: list[str],
        verdicts_q: list[JudgeVerdict],
        verdicts_a: list[JudgeVerdict],
        history: list[dict[str, Any]],
    ) -> str:
        lines = ["# Generation Review", "", "## Coverage Audit", ""]
        lines += [f"- {n}" for n in coverage_notes] or ["- (no coverage notes)"]
        lines += ["", "## Question Judge", ""]
        for v in verdicts_q:
            lines.append(f"- {v.target_id}: total={v.total} verdict={v.verdict} suggestion={v.suggestion or '-'}")
        lines += ["", "## Answer Judge", ""]
        for v in verdicts_a:
            lines.append(f"- {v.target_id}: total={v.total} verdict={v.verdict} suggestion={v.suggestion or '-'}")
        lines += ["", "## Refinement History", ""]
        for h in history:
            lines.append(
                f"- iter {h['iteration']}: q_avg={h['q_total_avg']:.1f} a_avg={h['a_total_avg']:.1f} "
                f"failed_q={h['failed_questions']} failed_a={h['failed_answers']}"
            )
        lines += ["", "## Human-in-the-loop", ""]
        lines += [
            "- Verify whether M3.1.1 Therbligs is officially in the midterm scope.",
            "- Confirm point allocation, language, and exam length with the instructor.",
            "- Replace deterministic provider with the LLM provider before final submission.",
        ]
        return "\n".join(lines)
