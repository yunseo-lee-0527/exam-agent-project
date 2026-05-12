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
from costing import estimate_tokens
from providers import load_model_policy, make_provider


def load_requirements(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _points_for(kind: str, total: int = 100, mix: dict[str, int] | None = None) -> int:
    """Even point allocation per question, biased toward longer kinds."""

    weights = {"short_answer": 5, "concept_comparison": 10, "application": 15, "essay": 20}
    return weights.get(kind, 10)


def build_chunk_index(notes: dict[str, str], max_chars: int = 1800) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for filename, body in notes.items():
        paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
        current: list[str] = []
        current_len = 0
        chunk_n = 1
        for paragraph in paragraphs:
            if current and current_len + len(paragraph) > max_chars:
                text = "\n\n".join(current)
                chunks.append(
                    {
                        "chunk_id": f"{Path(filename).stem}_{chunk_n:03d}",
                        "source_file": filename,
                        "text_preview": text[:320],
                        "char_count": len(text),
                        "token_estimate": estimate_tokens(text),
                    }
                )
                chunk_n += 1
                current = []
                current_len = 0
            current.append(paragraph)
            current_len += len(paragraph)
        if current:
            text = "\n\n".join(current)
            chunks.append(
                {
                    "chunk_id": f"{Path(filename).stem}_{chunk_n:03d}",
                    "source_file": filename,
                    "text_preview": text[:320],
                    "char_count": len(text),
                    "token_estimate": estimate_tokens(text),
                }
            )
    return chunks


def estimate_static_cost_inputs(notes: dict[str, str], questions: list[Question]) -> dict[str, Any]:
    note_tokens = sum(estimate_tokens(text) for text in notes.values())
    prompt_tokens = sum(estimate_tokens(q.prompt) for q in questions)
    answer_tokens = sum(estimate_tokens(q.answer) for q in questions)
    return {
        "lecture_note_tokens": note_tokens,
        "question_prompt_tokens": prompt_tokens,
        "answer_tokens": answer_tokens,
        "note": "Static estimates use len(text)//4 and complement provider usage records.",
    }


def render_human_review_checklist(
    requirements: dict[str, Any],
    questions: list[Question],
    coverage_notes: list[str],
    usage_summary: dict[str, Any],
) -> str:
    lines = [
        "# Human Review Checklist",
        "",
        "Use this before submitting the final generated exam.",
        "",
        "## Scope",
        "",
        "- [ ] Confirm the official midterm scope with the professor/TA.",
        "- [ ] Confirm whether M3.1.1 Therbligs is included.",
        "- [ ] Confirm that no question depends on out-of-scope material.",
        "",
        "## Exam Quality",
        "",
        "- [ ] Check that every question is answerable from lecture materials.",
        "- [ ] Check that short answer, comparison, application, and essay questions match the requested mix.",
        "- [ ] Remove duplicate or overly similar questions.",
        "- [ ] Verify point allocation and expected duration.",
        "- [ ] Check language, grammar, and professor style.",
        "",
        "## Model Answers",
        "",
        "- [ ] Verify factual accuracy of every model answer.",
        "- [ ] Check that source references match lecture files.",
        "- [ ] Add grading rubric details for essay/application questions if needed.",
        "",
        "## Provider and Cost",
        "",
        f"- [ ] Provider mode is correct for final generation. Estimated cost: ${usage_summary.get('estimated_cost_usd', 0):.6f}.",
        "- [ ] If using Gemini/OpenAI/Anthropic, confirm strict provider mode for the final run.",
        "- [ ] Confirm no private API keys or credentials are committed.",
        "",
        "## Generated Questions",
        "",
    ]
    for q in questions:
        lines.append(f"- [ ] Q{q.number}: {q.kind} / {q.topic} / {q.points} points")
    lines += ["", "## Coverage Audit Notes", ""]
    lines += [f"- {note}" for note in coverage_notes] or ["- No coverage notes."]
    lines += ["", "## Requirements Snapshot", "", "```json", json.dumps(requirements, indent=2, ensure_ascii=False), "```", ""]
    return "\n".join(lines)


def run_pipeline(
    processed_dir: Path,
    requirements_path: Path,
    outputs_dir: Path,
    notes_db_path: Path | None = None,
    max_refine_iterations: int = 2,
    provider_name: str | None = None,
    model_policy_path: Path | None = None,
    quality: str = "draft",
    strict_provider: bool = False,
) -> dict[str, Any]:
    """Sequential orchestrator with parallel fan-out and a refinement loop.

    Pipeline shape mirrors M5.3.3:
      Collector -> Planner -> [4 specialists in parallel] -> AnswerWriter
        -> CoverageAudit + (RefinementCoordinator wrapping QuestionJudge & AnswerJudge)
        -> Formatter
    """

    state: dict[str, Any] = {"status": "STARTED", "run_trace": []}
    requirements = load_requirements(requirements_path)
    state["requirements"] = requirements
    model_policy = load_model_policy(model_policy_path, quality=quality)
    state["model_policy"] = model_policy

    provider = make_provider(provider_name, model_policy=model_policy, strict=strict_provider)
    state["provider"] = provider.__class__.__name__
    state["strict_provider"] = strict_provider

    # --- Task 0: Collector ---
    collector = LectureNoteCollectorAgent(db_path=notes_db_path)
    collected = collector.run(processed_dir)
    state["run_trace"].append({"task": collector.task_id, "agent": collector.name, "status": "completed"})
    state["collection"] = collected
    notes = collected["notes"]
    chunk_index = build_chunk_index(notes)
    state["chunk_index"] = {
        "chunks": len(chunk_index),
        "estimated_tokens": sum(c["token_estimate"] for c in chunk_index),
    }

    # --- Task 1: Planner ---
    planner = CoveragePlannerAgent(provider)
    planned = planner.run({"requirements": requirements, "notes": notes})
    state["run_trace"].append({"task": planner.task_id, "agent": planner.name, "status": "completed"})
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
    state["run_trace"].append({"task": "Task 2", "agent": "Question Writer fan-out", "status": "completed", "questions": len(questions)})
    state["draft_questions"] = len(questions)

    # --- Task 3: Answers (ReAct + retrieval) ---
    answer_writer = AnswerWriterAgent(provider)
    questions = answer_writer.run({"questions": questions, "notes": notes})
    state["run_trace"].append({"task": answer_writer.task_id, "agent": answer_writer.name, "status": "completed"})

    # --- Task 4c: Coverage audit (deterministic structural checks) ---
    auditor = CoverageAuditAgent()
    coverage_notes = auditor.run(
        {
            "topics": topics,
            "questions": questions,
            "target_mix": mix,
        }
    )
    state["run_trace"].append({"task": auditor.task_id, "agent": auditor.name, "status": "completed", "notes": len(coverage_notes)})

    # --- Task 4a + 4b inside Task 5: Supervisor-Evaluator loop ---
    question_judge = QuestionJudgeAgent(provider)
    answer_judge = AnswerJudgeAgent(provider)

    def regen_question(q: Question, suggestion: str, notes_: dict[str, str]) -> Question:
        topic = next((t for t in topics if t.title == q.topic), None)
        if topic is None:
            topic = Topic(
                key=q.topic.lower().replace(" ", "_"),
                title=q.topic,
                weight=0,
                keywords=q.topic.split(),
                source_files=[],
            )
        regenerated = provider.write_questions(q.kind, topic, 1, notes_)
        if regenerated:
            q.prompt = regenerated[0].get("prompt", q.prompt)
            q.answer = regenerated[0].get("answer", q.answer)
            if suggestion:
                q.answer = (q.answer + f"\n\nRevision focus: {suggestion}").strip()
        return q

    def regen_answer(q: Question, suggestion: str, notes_: dict[str, str]) -> Question:
        result = provider.write_answer(q, notes_)
        q.answer = result["answer"].strip()
        if suggestion:
            q.answer += f"\n\nRevision focus addressed: {suggestion}"
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
    state["run_trace"].append({"task": coordinator.task_id, "agent": coordinator.name, "status": "completed", "iterations": len(refined["history"])})
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
    state["run_trace"].append({"task": formatter.task_id, "agent": formatter.name, "status": "completed"})

    usage_summary = provider.get_usage_summary() if hasattr(provider, "get_usage_summary") else {}
    static_cost_inputs = estimate_static_cost_inputs(notes, questions)

    outputs_dir.mkdir(parents=True, exist_ok=True)
    (outputs_dir / "exam.md").write_text(rendered["exam_md"], encoding="utf-8")
    (outputs_dir / "answers.md").write_text(rendered["answers_md"], encoding="utf-8")
    (outputs_dir / "review.md").write_text(rendered["review_md"], encoding="utf-8")
    (outputs_dir / "chunk_index.json").write_text(json.dumps(chunk_index, indent=2, ensure_ascii=False), encoding="utf-8")
    (outputs_dir / "run_trace.json").write_text(json.dumps(state["run_trace"], indent=2, ensure_ascii=False), encoding="utf-8")
    (outputs_dir / "cost_report.json").write_text(
        json.dumps(
            {
                "provider": state["provider"],
                "quality": quality,
                "strict_provider": strict_provider,
                "usage": usage_summary,
                "static_estimates": static_cost_inputs,
                "chunk_index": state["chunk_index"],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (outputs_dir / "human_review_checklist.md").write_text(
        render_human_review_checklist(requirements, questions, coverage_notes, usage_summary),
        encoding="utf-8",
    )

    state["status"] = "COMPLETED"
    state["question_count"] = len(questions)
    state["outputs_dir"] = str(outputs_dir)
    state["usage_summary"] = usage_summary
    state["static_cost_inputs"] = static_cost_inputs
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Scientific Management exam draft.")
    parser.add_argument("--processed-dir", default="lecture_notes/processed")
    parser.add_argument("--requirements", default="requirements.json")
    parser.add_argument("--outputs-dir", default="outputs")
    parser.add_argument("--notes-db", default="outputs/processed_notes_db.json")
    parser.add_argument("--max-refine", type=int, default=2)
    parser.add_argument("--model-policy", default="model_policy.json")
    parser.add_argument("--quality", choices=["draft", "final"], default="draft")
    parser.add_argument("--strict-provider", action="store_true")
    parser.add_argument(
        "--provider",
        choices=["deterministic", "gemini", "openai", "anthropic"],
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
        model_policy_path=root / args.model_policy,
        quality=args.quality,
        strict_provider=args.strict_provider,
    )

    print(f"Provider: {state['provider']}")
    print(f"Status: {state['status']}")
    print(f"Generated {state['question_count']} questions.")
    print(f"Wrote outputs to {state['outputs_dir']}")
    print(f"Refinement iterations: {len(state['history'])}")
    print(f"Estimated model cost: ${state['usage_summary'].get('estimated_cost_usd', 0):.6f}")


if __name__ == "__main__":
    main()
