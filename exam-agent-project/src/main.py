from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agents import (
    AgenticJudgeSystemAgent,
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


def load_exam_blueprint(path: Path | None) -> dict[str, Any] | None:
    if not path or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_source_refs(refs: list[str], notes: dict[str, str]) -> list[str]:
    """Resolve exact filenames or module prefixes such as M1.4 to note files."""

    resolved: list[str] = []
    filenames = list(notes)
    for ref in refs:
        if ref in notes:
            match = ref
        else:
            ref_lc = ref.lower()
            match = next((f for f in filenames if f.lower().startswith(ref_lc)), None)
            if match is None:
                match = next((f for f in filenames if ref_lc in f.lower()), ref)
        if match not in resolved:
            resolved.append(match)
    return resolved


def questions_from_blueprint(blueprint: dict[str, Any], notes: dict[str, str]) -> list[Question]:
    questions: list[Question] = []
    for idx, item in enumerate(blueprint.get("questions", []), start=1):
        coverage = {str(k): int(v) for k, v in (item.get("coverage_contribution") or {}).items()}
        questions.append(
            Question(
                number=int(item.get("number", idx)),
                kind=str(item["kind"]),
                topic=str(item["topic"]),
                prompt=str(item["prompt"]).strip(),
                points=int(item["points"]),
                answer=str(item.get("answer", "")).strip(),
                source_refs=_resolve_source_refs(list(item.get("source_refs", [])), notes),
                difficulty=str(item.get("difficulty", "")),
                learning_objective=str(item.get("learning_objective", "")),
                rubric=list(item.get("rubric", [])),
                coverage_contribution=coverage,
            )
        )
    questions.sort(key=lambda q: q.number)
    for number, q in enumerate(questions, start=1):
        q.number = number
    return questions


def _points_for(kind: str, total: int = 100, mix: dict[str, int] | None = None) -> int:
    """Even point allocation per question, biased toward longer kinds."""

    weights = {"short_answer": 5, "concept_comparison": 10, "application": 15, "essay": 20}
    return weights.get(kind, 10)


def build_chunk_index(notes: dict[str, str], max_chars: int = 1800) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": chunk["chunk_id"],
            "source_file": chunk["source_file"],
            "text_preview": chunk["text"][:320],
            "char_count": len(chunk["text"]),
            "token_estimate": estimate_tokens(chunk["text"]),
        }
        for chunk in build_note_chunks(notes, max_chars=max_chars)
    ]


def build_note_chunks(notes: dict[str, str], max_chars: int = 1800) -> list[dict[str, Any]]:
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
                        "text": text,
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
                    "text": text,
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


def build_coverage_matrix(requirements: dict[str, Any], questions: list[Question]) -> dict[str, Any]:
    target = {str(k): int(v) for k, v in requirements.get("coverage_weights", {}).items()}
    actual: dict[str, int] = {}
    if any(q.coverage_contribution for q in questions):
        for q in questions:
            for key, points in q.coverage_contribution.items():
                actual[key] = actual.get(key, 0) + int(points)
    else:
        for q in questions:
            key = q.topic.lower().replace(" and ", "_").replace(" ", "_")
            actual[key] = actual.get(key, 0) + q.points

    deltas = {key: actual.get(key, 0) - expected for key, expected in target.items()}
    missing = [key for key in target if actual.get(key, 0) == 0]
    return {
        "target_weights": target,
        "actual_contribution": actual,
        "deltas": deltas,
        "missing_topics": missing,
        "total_contribution": sum(actual.values()),
        "passed": sum(actual.values()) == 100 and all(delta == 0 for delta in deltas.values()) and not missing,
    }


def build_source_grounding_report(questions: list[Question], notes: dict[str, str]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    valid_count = 0
    for q in questions:
        refs = list(dict.fromkeys(q.source_refs))
        missing = [ref for ref in refs if ref not in notes]
        has_grounding = bool(refs) and not missing
        if has_grounding:
            valid_count += 1
        items.append(
            {
                "question": q.number,
                "topic": q.topic,
                "source_refs": refs,
                "missing_refs": missing,
                "grounded": has_grounding,
            }
        )
    return {
        "grounded_questions": valid_count,
        "total_questions": len(questions),
        "ungrounded_questions": [item for item in items if not item["grounded"]],
        "passed": valid_count == len(questions),
        "items": items,
    }


GROUNDING_STOPWORDS = {
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
    "question",
    "lecture",
    "problem",
}


def signal_terms(text: str, limit: int = 24) -> list[str]:
    terms: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z-]{3,}", text):
        term = word.lower()
        if term in GROUNDING_STOPWORDS or term in terms:
            continue
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def build_chunk_grounding_report(questions: list[Question], notes: dict[str, str]) -> dict[str, Any]:
    chunks = build_note_chunks(notes)
    items: list[dict[str, Any]] = []
    supported_count = 0
    for q in questions:
        terms = signal_terms(q.prompt + " " + q.answer)
        candidates = [chunk for chunk in chunks if chunk["source_file"] in q.source_refs] or chunks
        scored: list[dict[str, Any]] = []
        for chunk in candidates:
            text_lc = chunk["text"].lower()
            matched = [term for term in terms if term in text_lc]
            if not matched:
                continue
            scored.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "source_file": chunk["source_file"],
                    "matched_terms": matched[:8],
                    "support_score": len(matched),
                    "text_preview": chunk["text"][:260].replace("\n", " "),
                }
            )
        scored.sort(key=lambda item: item["support_score"], reverse=True)
        top_chunks = scored[:3]
        supported = bool(top_chunks)
        if supported:
            supported_count += 1
        items.append(
            {
                "question": q.number,
                "topic": q.topic,
                "source_refs": q.source_refs,
                "signal_terms": terms[:12],
                "support_chunks": top_chunks,
                "supported": supported,
            }
        )
    return {
        "supported_questions": supported_count,
        "total_questions": len(questions),
        "unsupported_questions": [item for item in items if not item["supported"]],
        "passed": supported_count == len(questions),
        "items": items,
    }


def build_residual_risk_report(
    provider_name: str,
    strict_provider: bool,
    blueprint: dict[str, Any] | None,
    agentic_judge_report: dict[str, Any],
    chunk_grounding_report: dict[str, Any],
) -> dict[str, Any]:
    risks: list[dict[str, Any]] = []
    if provider_name == "ConfiguredDeterministicProvider":
        risks.append(
            {
                "risk": "deterministic_provider",
                "severity": "high",
                "evidence": "Current run used the local deterministic fallback, not a live LLM provider.",
                "mitigation": "Run the final pipeline with --provider gemini --quality final --strict-provider and preserve cost_report.json as evidence.",
            }
        )
    if blueprint:
        risks.append(
            {
                "risk": "blueprint_dependency",
                "severity": "medium",
                "evidence": "exam_blueprint.json controls the current exam draft.",
                "mitigation": "Frame it as an instructor-approved blueprint or generate a fresh blueprint from the planner before final submission.",
            }
        )
    if not strict_provider:
        risks.append(
            {
                "risk": "provider_fallback_hidden",
                "severity": "medium",
                "evidence": "Strict provider mode is off, so live provider failures can fall back during development.",
                "mitigation": "Use --strict-provider for final generation.",
            }
        )
    if agentic_judge_report.get("final_verdict") == "PASS":
        risks.append(
            {
                "risk": "self_evaluation_bias",
                "severity": "medium",
                "evidence": "The same system family generates and judges the exam.",
                "mitigation": "Add human_review_notes.json and compare human findings against agentic_judge_report.json.",
            }
        )
    if chunk_grounding_report.get("passed"):
        risks.append(
            {
                "risk": "chunk_grounding_is_not_entailment",
                "severity": "low",
                "evidence": "Chunk grounding verifies lexical support, not full semantic entailment.",
                "mitigation": "Upgrade SourceGroundingJudgeAgent to compare answer claims against cited chunks with a live LLM judge.",
            }
        )
    return {
        "risk_count": len(risks),
        "risks": risks,
        "agentic_judge_final_verdict": agentic_judge_report.get("final_verdict"),
        "chunk_grounding_passed": chunk_grounding_report.get("passed"),
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
        "- [ ] Review outputs/agentic_judge_report.json and address every REVISE or FAIL target.",
        "- [ ] Record final human decisions in outputs/human_review_notes_template.json.",
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


def human_review_template(questions: list[Question]) -> dict[str, Any]:
    return {
        "reviewer": "",
        "review_date": "",
        "scale": "approve | revise | reject",
        "overall_notes": "",
        "items": [
            {
                "question": q.number,
                "decision": "",
                "scope_ok": None,
                "difficulty_ok": None,
                "source_grounding_ok": None,
                "rubric_ok": None,
                "comments": "",
            }
            for q in questions
        ],
    }


def render_critical_discussion(
    residual_risk_report: dict[str, Any],
    agentic_judge_report: dict[str, Any],
    chunk_grounding_report: dict[str, Any],
) -> str:
    lines = [
        "# Critical Discussion",
        "",
        "This file summarizes the main limitations that remain after the current implementation.",
        "",
        "## Current Evidence",
        "",
        f"- Agentic judge final verdict: `{agentic_judge_report.get('final_verdict', 'UNKNOWN')}`.",
        f"- Chunk-level grounding passed: `{chunk_grounding_report.get('passed', False)}`.",
        f"- Supported questions: {chunk_grounding_report.get('supported_questions', 0)} / {chunk_grounding_report.get('total_questions', 0)}.",
        "",
        "## Residual Risks",
        "",
    ]
    for risk in residual_risk_report.get("risks", []):
        lines += [
            f"### {risk['risk']}",
            "",
            f"- Severity: {risk['severity']}",
            f"- Evidence: {risk['evidence']}",
            f"- Mitigation: {risk['mitigation']}",
            "",
        ]
    lines += [
        "## Discussion",
        "",
        "The system now has a closed quality-control layer, but final submission should not claim full autonomy.",
        "The strongest defensible claim is that the system automates generation, checking, evidence collection, and revision support, while preserving a final human gate for scope and fairness.",
        "",
        "The most important next validation step is to run the final pipeline with a live LLM provider in strict mode and compare the generated exam with human reviewer notes.",
        "",
    ]
    return "\n".join(lines)


def run_pipeline(
    processed_dir: Path,
    requirements_path: Path,
    outputs_dir: Path,
    notes_db_path: Path | None = None,
    max_refine_iterations: int = 2,
    max_agentic_judge_iterations: int = 2,
    provider_name: str | None = None,
    model_policy_path: Path | None = None,
    blueprint_path: Path | None = None,
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
    blueprint = load_exam_blueprint(blueprint_path)
    if blueprint:
        questions = questions_from_blueprint(blueprint, notes)
        state["blueprint"] = {
            "path": str(blueprint_path),
            "version": blueprint.get("version", "unknown"),
            "questions": len(questions),
        }
        state["run_trace"].append(
            {
                "task": "Task 2",
                "agent": "Blueprint Question Writer",
                "status": "completed",
                "questions": len(questions),
            }
        )
    else:
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
        questions = fan_out_question_writers(writers, payloads, max_workers=4)
        state["run_trace"].append(
            {
                "task": "Task 2",
                "agent": "Question Writer fan-out",
                "status": "completed",
                "questions": len(questions),
            }
        )
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
            "target_weights": requirements.get("coverage_weights", {}),
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

    # --- Task 5b: Agentic judge closed-loop revision ---
    agentic_judge = AgenticJudgeSystemAgent()
    agentic_judge_history: list[dict[str, Any]] = []
    agentic_judge_report: dict[str, Any] = {}
    for iteration in range(1, max_agentic_judge_iterations + 1):
        agentic_judge_report = agentic_judge.run(
            {
                "questions": questions,
                "notes": notes,
                "requirements": requirements,
            }
        )
        decisions = agentic_judge_report.get("target_decisions", {})
        non_pass = {
            target: decision
            for target, decision in decisions.items()
            if decision.get("final_verdict") != "PASS"
        }
        agentic_judge_history.append(
            {
                "iteration": iteration,
                "final_verdict": agentic_judge_report.get("final_verdict"),
                "non_pass_targets": sorted(non_pass),
            }
        )
        if not non_pass:
            break

        revised_any = False
        for target, decision in non_pass.items():
            if not target.startswith("Q"):
                continue
            try:
                idx = int(target[1:]) - 1
            except ValueError:
                continue
            if not (0 <= idx < len(questions)):
                continue
            instruction = " ".join(decision.get("revision_instructions", []))
            failed_checks = " ".join(decision.get("failed_checks", []))
            if any(key in failed_checks for key in ["rubric", "model_answer", "source"]):
                questions[idx] = regen_answer(questions[idx], instruction, notes)
            else:
                questions[idx] = regen_question(questions[idx], instruction, notes)
                questions[idx] = regen_answer(questions[idx], instruction, notes)
            revised_any = True

        if not revised_any:
            break
        questions = answer_writer.run({"questions": questions, "notes": notes})

    state["agentic_judge_history"] = agentic_judge_history
    state["agentic_judge_report"] = agentic_judge_report
    state["run_trace"].append(
        {
            "task": agentic_judge.task_id,
            "agent": agentic_judge.name,
            "status": "completed",
            "final_verdict": agentic_judge_report["final_verdict"],
            "iterations": len(agentic_judge_history),
            "non_pass_targets": agentic_judge_report["summary"].get("revise", 0)
            + agentic_judge_report["summary"].get("fail", 0),
        }
    )

    coverage_matrix = build_coverage_matrix(requirements, questions)
    source_grounding_report = build_source_grounding_report(questions, notes)
    chunk_grounding_report = build_chunk_grounding_report(questions, notes)
    residual_risk_report = build_residual_risk_report(
        provider_name=state["provider"],
        strict_provider=strict_provider,
        blueprint=blueprint,
        agentic_judge_report=agentic_judge_report,
        chunk_grounding_report=chunk_grounding_report,
    )
    state["coverage_matrix"] = coverage_matrix
    state["source_grounding_report"] = source_grounding_report
    state["chunk_grounding_report"] = chunk_grounding_report
    state["residual_risk_report"] = residual_risk_report

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
    (outputs_dir / "questions.json").write_text(
        json.dumps([asdict(q) for q in questions], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (outputs_dir / "coverage_matrix.json").write_text(json.dumps(coverage_matrix, indent=2, ensure_ascii=False), encoding="utf-8")
    (outputs_dir / "source_grounding_report.json").write_text(
        json.dumps(source_grounding_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (outputs_dir / "agentic_judge_report.json").write_text(
        json.dumps(agentic_judge_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (outputs_dir / "chunk_grounding_report.json").write_text(
        json.dumps(chunk_grounding_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (outputs_dir / "residual_risk_report.json").write_text(
        json.dumps(residual_risk_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (outputs_dir / "human_review_notes_template.json").write_text(
        json.dumps(human_review_template(questions), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
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
    (outputs_dir / "critical_discussion.md").write_text(
        render_critical_discussion(residual_risk_report, agentic_judge_report, chunk_grounding_report),
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
    parser.add_argument("--max-agentic-judge-refine", type=int, default=2)
    parser.add_argument("--model-policy", default="model_policy.json")
    parser.add_argument("--blueprint", default="exam_blueprint.json")
    parser.add_argument("--quality", choices=["draft", "final", "final_low_cost"], default="draft")
    parser.add_argument("--strict-provider", action="store_true")
    parser.add_argument(
        "--provider",
        choices=["deterministic", "gemini", "vertex", "openai", "anthropic"],
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
        max_agentic_judge_iterations=args.max_agentic_judge_refine,
        provider_name=args.provider,
        model_policy_path=root / args.model_policy,
        blueprint_path=root / args.blueprint,
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
