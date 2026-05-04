from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agents import CoveragePlannerAgent, InputParserAgent, QuestionWriterAgent, ReviewerAgent


def load_requirements(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_exam(path: Path, requirements: dict[str, Any], questions: list) -> None:
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
    for question in questions:
        lines.extend(
            [
                f"## Q{question.number}. {question.kind} ({question.points} points)",
                "",
                f"Topic: {question.topic}",
                "",
                question.prompt,
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_answers(path: Path, requirements: dict[str, Any], questions: list) -> None:
    lines = [
        f"# Model Answers: {requirements['course']} {requirements['exam_name']}",
        "",
    ]
    for question in questions:
        lines.extend(
            [
                f"## Q{question.number}. {question.kind}",
                "",
                question.answer,
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_review(path: Path, review_notes: list[str]) -> None:
    lines = ["# Generation Review", ""]
    lines.extend(f"- {note}" for note in review_notes)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Scientific Management exam draft.")
    parser.add_argument("--processed-dir", default="lecture_notes/processed")
    parser.add_argument("--requirements", default="requirements.json")
    parser.add_argument("--outputs-dir", default="outputs")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    processed_dir = root / args.processed_dir
    requirements_path = root / args.requirements
    outputs_dir = root / args.outputs_dir
    outputs_dir.mkdir(parents=True, exist_ok=True)

    requirements = load_requirements(requirements_path)
    notes = InputParserAgent().run(processed_dir)
    topics = CoveragePlannerAgent().run(requirements, notes)
    questions = QuestionWriterAgent().run(requirements, topics)
    review_notes = ReviewerAgent().run(topics, questions)

    write_exam(outputs_dir / "exam.md", requirements, questions)
    write_answers(outputs_dir / "answers.md", requirements, questions)
    write_review(outputs_dir / "review.md", review_notes)

    print(f"Generated {len(questions)} questions.")
    print(f"Wrote outputs to {outputs_dir}")


if __name__ == "__main__":
    main()

