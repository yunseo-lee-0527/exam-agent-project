from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agents import (
    AnswerJudgeAgent,
    AnswerWriterAgent,
    ApplicationWriterAgent,
    ComparisonWriterAgent,
    CoverageAuditAgent,
    CoveragePlannerAgent,
    EssayWriterAgent,
    FormatterAgent,
    LectureNoteCollectorAgent,
    Question,
    QuestionJudgeAgent,
    RefinementCoordinator,
    ShortAnswerWriterAgent,
    Topic,
    fan_out_question_writers,
)
from providers import make_provider


def load_requirements(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _points_for(kind: str, total: int = 100, mix: dict[str, int] | None = None) -> int:
    """Even point allocation per question, biased toward longer kinds."""

    weights = {"short_answer": 5, "concept_comparison": 10, "application": 15, "essay": 20}
    return weights.get(kind, 10)


def run_pipeline(
    processed_dir: Path,
    requirements_path: Path,
    outputs_dir: Path,
    notes_db_path: Path | None = None,
    max_refine_iterations: int = 2,
    provider_name: str | None = None,
) -> dict[str, Any]:
    """Sequential orchestrator with parallel fan-out and a refinement loop.

    Pipeline shape mirrors M5.3.3:
      Collector -> Planner -> [4 specialists in parallel] -> AnswerWriter
        -> CoverageAudit + (RefinementCoordinator wrapping QuestionJudge & AnswerJudge)
        -> Formatter
    """

    state: dict[str, Any] = {"status": "STARTED"}
    requirements = load_requirements(requirements_path)
    state["requirements"] = requirements

    provider = make_provider(provider_name)
    state["provider"] = provider.__class__.__name__

    # --- Task 0: Collector ---
    collector = LectureNoteCollectorAgent(db_path=notes_db_path)
    collected = collector.run(processed_dir)
    state["collection"] = collected
    notes = collected["notes"]

    # --- Task 1: Planner ---
    planner = CoveragePlannerAgent(provider)
    planned = planner.run({"requirements": requirements, "notes": notes})
    state["plan"] = planned["plan"]
    topics: list[Topic] = planned["topics"]

    # --- Task 2: Question writers in parallel ---
    mix = requirements.get("question_mix", {})
    writers = [
        ShortAnswerWriterAgent(provider),
        ComparisonWriterAgent(provider),
        ApplicationWriterAgent(provider),
        EssayWriterAgent(provider),
    ]
    payloads = [
        {
            "topics": topics,
            "notes": notes,
            "count": mix.get("short_answer", 6),
            "points_per_question": _points_for("short_answer"),
            "start_number": 1,
        },
        {
            "topics": topics,
            "notes": notes,
            "count": mix.get("concept_comparison", 2),
            "points_per_question": _points_for("concept_comparison"),
            "start_number": 1,
        },
        {
            "topics": topics,
            "notes": notes,
            "count": mix.get("application", 2),
            "points_per_question": _points_for("application"),
            "start_number": 1,
        },
        {
            "topics": topics,
            "notes": notes,
            "count": mix.get("essay", 1),
            "points_per_question": _points_for("essay"),
            "start_number": 1,
        },
    ]
    questions: list[Question] = fan_out_question_writers(writers, payloads, max_workers=4)
    state["draft_questions"] = len(questions)

    # --- Task 3: Answers (ReAct + retrieval) ---
    answer_writer = AnswerWriterAgent(provider)
    questions = answer_writer.run({"questions": questions, "notes": notes})

    # --- Task 4c: Coverage audit (deterministic structural checks) ---
    auditor = CoverageAuditAgent()
    coverage_notes = auditor.run(
        {
            "topics": topics,
            "questions": questions,
            "target_mix": mix,
        }
    )

    # --- Task 4a + 4b inside Task 5: Supervisor-Evaluator loop ---
    question_judge = QuestionJudgeAgent(provider)
    answer_judge = AnswerJudgeAgent(provider)

    def regen_question(q: Question, suggestion: str, notes_: dict[str, str]) -> Question:
        # Deterministic regeneration: keep prompt, append suggestion as a hint.
        if suggestion:
            q.prompt = q.prompt.rstrip(".") + f". (Refinement hint: {suggestion})"
        return q

    def regen_answer(q: Question, suggestion: str, notes_: dict[str, str]) -> Question:
        result = provider.write_answer(q, notes_)
        q.answer = (q.answer + "\n\nRefined: " + result["answer"]).strip()
        if suggestion:
            q.answer += f"\n\n(Refinement hint addressed: {suggestion})"
        q.source_refs = result.get("source_refs", []) or q.source_refs
        return q

    coordinator = RefinementCoordinator(
        question_judge=question_judge,
        answer_judge=answer_judge,
        regenerate_question=regen_question,
        regenerate_answer=regen_answer,
        max_iterations=max_refine_iterations,
    )
    refined = coordinator.run({"questions": questions, "notes": notes})
    questions = refined["questions"]
    state["history"] = refined["history"]

    # --- Task 6: Formatter ---
    formatter = FormatterAgent()
    rendered = formatter.run(
        {
            "requirements": requirements,
            "questions": questions,
            "coverage_notes": coverage_notes,
            "verdicts_q": refined["verdicts_q"],
            "verdicts_a": refined["verdicts_a"],
            "history": refined["history"],
        }
    )

    outputs_dir.mkdir(parents=True, exist_ok=True)
    (outputs_dir / "exam.md").write_text(rendered["exam_md"], encoding="utf-8")
    (outputs_dir / "answers.md").write_text(rendered["answers_md"], encoding="utf-8")
    (outputs_dir / "review.md").write_text(rendered["review_md"], encoding="utf-8")

    state["status"] = "COMPLETED"
    state["question_count"] = len(questions)
    state["outputs_dir"] = str(outputs_dir)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Scientific Management exam draft.")
    parser.add_argument("--processed-dir", default="lecture_notes/processed")
    parser.add_argument("--requirements", default="requirements.json")
    parser.add_argument("--outputs-dir", default="outputs")
    parser.add_argument("--notes-db", default="outputs/processed_notes_db.json")
    parser.add_argument("--max-refine", type=int, default=2)
    parser.add_argument(
        "--provider",
        choices=["deterministic", "gemini"],
        default=None,
        help="LLM provider. Falls back to EXAM_AGENT_PROVIDER env var, then 'deterministic'.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    state = run_pipeline(
        processed_dir=root / args.processed_dir,
        requirements_path=root / args.requirements,
        outputs_dir=root / args.outputs_dir,
        notes_db_path=root / args.notes_db,
        max_refine_iterations=args.max_refine,
        provider_name=args.provider,
    )

    print(f"Provider: {state['provider']}")
    print(f"Status: {state['status']}")
    print(f"Generated {state['question_count']} questions.")
    print(f"Wrote outputs to {state['outputs_dir']}")
    print(f"Refinement iterations: {len(state['history'])}")


if __name__ == "__main__":
    main()
