"""Offline evaluation harness (M5.3.4 — pilot test + LLM Judge + simulation).

Kept separate from production main.py so generation and evaluation
are independent concerns, as the lecture frames it.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from agents import (
    AnswerJudgeAgent,
    Question,
    QuestionJudgeAgent,
)
from main import build_coverage_matrix, run_pipeline
from providers import load_model_policy, make_provider


# ---------------------------------------------------------------------------
# Pilot test — golden set of labelled cases (M5.3.4 EvalCase)
# ---------------------------------------------------------------------------


@dataclass
class EvalCase:
    test_id: str
    topic: str
    expected_keywords: list[str]
    description: str


GOLDEN_CASES: list[EvalCase] = [
    EvalCase(
        test_id="G1",
        topic="Scientific Management",
        expected_keywords=["taylor", "principle"],
        description="A short-answer question on Taylor's four principles must surface the canonical four.",
    ),
    EvalCase(
        test_id="G2",
        topic="Problem Solving and Ideation",
        expected_keywords=["dassi", "define", "implement"],
        description="A DASSI question must reference the five-step structure.",
    ),
    EvalCase(
        test_id="G3",
        topic="Five Innovation Frameworks",
        expected_keywords=["addition", "subtraction", "alternate", "combination", "transposition"],
        description="An innovation-frameworks question should mention all five frameworks.",
    ),
    EvalCase(
        test_id="G4",
        topic="Motion Study and Therbligs",
        expected_keywords=["therblig", "motion"],
        description="A motion-study question must reference Therbligs.",
    ),
    EvalCase(
        test_id="G5",
        topic="Work and Work Systems",
        expected_keywords=["work system", "process", "task"],
        description="A work-systems question should surface multi-level vocabulary.",
    ),
]


def pilot_test(questions: list[Question]) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    passes = 0
    for case in GOLDEN_CASES:
        relevant = [q for q in questions if q.topic == case.topic]
        haystack = " ".join((q.prompt + " " + (q.answer or "")) for q in relevant).lower()
        matched = [kw for kw in case.expected_keywords if kw in haystack]
        ok = len(matched) >= max(1, len(case.expected_keywords) // 2)
        if ok:
            passes += 1
        hits.append(
            {
                "test_id": case.test_id,
                "topic": case.topic,
                "matched_keywords": matched,
                "expected_keywords": case.expected_keywords,
                "passed": ok,
            }
        )
    return {
        "passed": passes,
        "total": len(GOLDEN_CASES),
        "accuracy": passes / max(1, len(GOLDEN_CASES)),
        "details": hits,
    }


def structural_tests(
    questions: list[Question],
    notes: dict[str, str],
    requirements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompts = [q.prompt.strip().lower() for q in questions]
    duplicates = sorted({p for p in prompts if prompts.count(p) > 1})
    note_text = "\n".join(notes.values()).lower()
    out_of_scope_flags: list[dict[str, Any]] = []
    for q in questions:
        topic_terms = [term for term in q.topic.lower().split() if len(term) > 3]
        answer_terms = [term for term in q.answer.lower().split()[:30] if len(term) > 6]
        matched = [term for term in topic_terms + answer_terms if term in note_text]
        if not matched:
            out_of_scope_flags.append({"question": q.number, "topic": q.topic, "prompt": q.prompt})

    source_gaps = [{"question": q.number, "topic": q.topic} for q in questions if not q.source_refs]
    invalid_source_refs = [
        {"question": q.number, "missing_refs": [ref for ref in q.source_refs if ref not in notes]}
        for q in questions
        if any(ref not in notes for ref in q.source_refs)
    ]
    coverage_matrix = build_coverage_matrix(requirements or {}, questions) if requirements else {}
    coverage_failed = bool(coverage_matrix) and not coverage_matrix.get("passed", False)
    return {
        "duplicate_prompt_count": len(duplicates),
        "duplicates": duplicates,
        "out_of_scope_flag_count": len(out_of_scope_flags),
        "out_of_scope_flags": out_of_scope_flags,
        "source_gap_count": len(source_gaps),
        "source_gaps": source_gaps,
        "invalid_source_ref_count": len(invalid_source_refs),
        "invalid_source_refs": invalid_source_refs,
        "coverage_matrix": coverage_matrix,
        "passed": not duplicates and not out_of_scope_flags and not source_gaps and not invalid_source_refs and not coverage_failed,
    }


# ---------------------------------------------------------------------------
# LLM Judge harness (M5.3.4 — JUDGE_*_PROMPT pattern)
# ---------------------------------------------------------------------------


def judge_run(
    questions: list[Question],
    notes: dict[str, str],
    provider_name: str | None = None,
    model_policy: dict[str, Any] | None = None,
    strict_provider: bool = False,
) -> dict[str, Any]:
    provider = make_provider(provider_name, model_policy=model_policy, strict=strict_provider)
    q_judge = QuestionJudgeAgent(provider)
    a_judge = AnswerJudgeAgent(provider)
    q_verdicts = q_judge.run({"questions": questions, "notes": notes})
    a_verdicts = a_judge.run({"questions": questions, "notes": notes})

    def aggregate(verdicts: list) -> dict[str, Any]:
        if not verdicts:
            return {"avg_total": 0, "verdict_mix": {}}
        verdict_mix: dict[str, int] = {}
        for v in verdicts:
            verdict_mix[v.verdict] = verdict_mix.get(v.verdict, 0) + 1
        return {
            "avg_total": sum(v.total for v in verdicts) / len(verdicts),
            "verdict_mix": verdict_mix,
            "items": [{"target_id": v.target_id, "total": v.total, "verdict": v.verdict} for v in verdicts],
        }

    return {
        "questions": aggregate(q_verdicts),
        "answers": aggregate(a_verdicts),
    }


# ---------------------------------------------------------------------------
# Simulation — throughput / cost extrapolation (M5.3.4)
# ---------------------------------------------------------------------------


def simulate_throughput(
    processed_dir: Path,
    requirements_path: Path,
    outputs_dir: Path,
    n_trials: int = 3,
    provider_name: str | None = None,
    model_policy_path: Path | None = None,
    model_preset: str | None = None,
    model_overrides: dict[str, str] | None = None,
    blueprint_path: Path | None = None,
    quality: str = "draft",
    strict_provider: bool = False,
) -> dict[str, Any]:
    durations: list[float] = []
    for _ in range(n_trials):
        random.seed(time.time_ns() % 2**32)
        start = time.time()
        run_pipeline(
            processed_dir=processed_dir,
            requirements_path=requirements_path,
            outputs_dir=outputs_dir,
            notes_db_path=None,
            max_refine_iterations=1,
            provider_name=provider_name,
            model_policy_path=model_policy_path,
            model_preset=model_preset,
            model_overrides=model_overrides,
            blueprint_path=blueprint_path,
            quality=quality,
            strict_provider=strict_provider,
        )
        durations.append(time.time() - start)
    avg = sum(durations) / max(1, len(durations))
    return {
        "trials": n_trials,
        "avg_seconds": avg,
        "estimated_exams_per_minute": 60 / avg if avg > 0 else float("inf"),
        "estimated_cost_per_exam_usd": 0.0,
        "cost_note": "See outputs/cost_report.json for provider usage and static token estimates.",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the offline evaluation harness.")
    parser.add_argument("--processed-dir", default="lecture_notes/processed")
    parser.add_argument("--requirements", default="requirements.json")
    parser.add_argument("--outputs-dir", default="outputs")
    parser.add_argument("--report", default="outputs/evaluation_report.json")
    parser.add_argument("--simulate-trials", type=int, default=3)
    parser.add_argument("--model-policy", default="model_policy.json")
    parser.add_argument("--model-preset", default=None, help="Model toggle from model_policy.json, e.g. lecture_flash, gpt, claude_opus.")
    parser.add_argument("--planner-model", default=None, help="Override planner model for this run.")
    parser.add_argument("--writer-model", default=None, help="Override question writer model for this run.")
    parser.add_argument("--answer-model", default=None, help="Override answer writer model for this run.")
    parser.add_argument("--judge-model", default=None, help="Override judge model for this run.")
    parser.add_argument("--final-rewriter-model", default=None, help="Override final rewriter model for this run.")
    parser.add_argument("--blueprint", default="exam_blueprint.json")
    parser.add_argument("--quality", choices=["draft", "final", "final_low_cost"], default="draft")
    parser.add_argument("--strict-provider", action="store_true")
    parser.add_argument(
        "--provider",
        choices=["deterministic", "gemini", "vertex", "openai", "gpt", "anthropic", "claude"],
        default=None,
        help="LLM provider. Falls back to EXAM_AGENT_PROVIDER env var, then 'deterministic'.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    processed_dir = root / args.processed_dir
    requirements_path = root / args.requirements
    outputs_dir = root / args.outputs_dir
    model_policy_path = root / args.model_policy
    model_overrides = {
        "planner": args.planner_model,
        "writer": args.writer_model,
        "answer_writer": args.answer_model,
        "judge": args.judge_model,
        "final_rewriter": args.final_rewriter_model,
    }
    model_policy = load_model_policy(
        model_policy_path,
        quality=args.quality,
        model_preset=args.model_preset,
        model_overrides=model_overrides,
    )

    state = run_pipeline(
        processed_dir=processed_dir,
        requirements_path=requirements_path,
        outputs_dir=outputs_dir,
        notes_db_path=None,
        max_refine_iterations=2,
        provider_name=args.provider,
        model_policy_path=model_policy_path,
        model_preset=args.model_preset,
        model_overrides=model_overrides,
        blueprint_path=root / args.blueprint,
        quality=args.quality,
        strict_provider=args.strict_provider,
    )

    notes = state["collection"]["notes"]
    questions_payload: list[Question] = _reload_questions(outputs_dir)
    pilot = pilot_test(questions_payload) if questions_payload else {"note": "no questions to score"}
    structural = (
        structural_tests(questions_payload, notes, state.get("requirements"))
        if questions_payload
        else {"note": "no questions to structurally test"}
    )
    judge = (
        judge_run(
            questions_payload,
            notes,
            provider_name=args.provider,
            model_policy=model_policy,
            strict_provider=args.strict_provider,
        )
        if questions_payload
        else {"note": "no questions to judge"}
    )
    sim = simulate_throughput(
        processed_dir,
        requirements_path,
        outputs_dir,
        n_trials=args.simulate_trials,
        provider_name=args.provider,
        model_policy_path=model_policy_path,
        model_preset=args.model_preset,
        model_overrides=model_overrides,
        blueprint_path=root / args.blueprint,
        quality=args.quality,
        strict_provider=args.strict_provider,
    )

    report = {
        "provider": state["provider"],
        "generated_questions": state["question_count"],
        "refinement_iterations": len(state["history"]),
        "quality": args.quality,
        "model_preset": args.model_preset,
        "models": model_policy.get("models", {}),
        "strict_provider": args.strict_provider,
        "pilot_test": pilot,
        "structural_tests": structural,
        "llm_judge": judge,
        "agentic_judge": state.get("agentic_judge_report", {}),
        "agentic_judge_history": state.get("agentic_judge_history", []),
        "chunk_grounding": state.get("chunk_grounding_report", {}),
        "residual_risk": state.get("residual_risk_report", {}),
        "simulation": sim,
        "usage_summary": state.get("usage_summary", {}),
        "static_cost_inputs": state.get("static_cost_inputs", {}),
        "golden_cases": [asdict(c) for c in GOLDEN_CASES],
    }
    report_path = root / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote evaluation report to {report_path}")
    print(f"Pilot accuracy: {pilot.get('accuracy', 0):.0%}")
    print(f"Structural tests: {'PASS' if structural.get('passed') else 'CHECK'}")
    print(f"Question judge avg: {judge.get('questions', {}).get('avg_total', 0):.1f}")
    print(f"Answer judge avg:   {judge.get('answers', {}).get('avg_total', 0):.1f}")
    print(f"Avg seconds/exam:   {sim['avg_seconds']:.2f}")


def _reload_questions(outputs_dir: Path) -> list[Question]:
    """Re-parse the freshly generated exam/answers markdown into Question objects.

    Avoids re-running generation just for evaluation.
    """

    questions_json = outputs_dir / "questions.json"
    if questions_json.exists():
        raw_items = json.loads(questions_json.read_text(encoding="utf-8"))
        allowed = {field.name for field in fields(Question)}
        return [Question(**{k: v for k, v in item.items() if k in allowed}) for item in raw_items]

    exam_path = outputs_dir / "exam.md"
    ans_path = outputs_dir / "answers.md"
    if not exam_path.exists() or not ans_path.exists():
        return []

    questions: list[Question] = []
    current: dict[str, Any] | None = None
    for line in exam_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Q"):
            if current:
                questions.append(Question(**current))
            header = line[len("## Q"):]
            number, rest = header.split(".", 1)
            kind, _, points_str = rest.strip().partition(" (")
            points = int(points_str.split(" ")[0]) if points_str else 0
            current = {
                "number": int(number),
                "kind": kind.strip(),
                "topic": "",
                "prompt": "",
                "points": points,
                "answer": "",
            }
        elif current is not None:
            if line.startswith("Topic: "):
                current["topic"] = line[len("Topic: "):].strip()
            elif line.strip() and not line.startswith("#"):
                current["prompt"] = (current["prompt"] + " " + line.strip()).strip()
    if current:
        questions.append(Question(**current))

    # Pair answers by Q number.
    ans_text = ans_path.read_text(encoding="utf-8")
    parts = ans_text.split("## Q")[1:]
    answers_by_n: dict[int, str] = {}
    for part in parts:
        head, _, body = part.partition("\n")
        try:
            n = int(head.split(".")[0])
        except ValueError:
            continue
        answers_by_n[n] = body.strip()
    for q in questions:
        q.answer = answers_by_n.get(q.number, "")
    return questions


if __name__ == "__main__":
    main()
