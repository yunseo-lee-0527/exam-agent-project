from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
    answer: str


class InputParserAgent:
    """Loads processed lecture notes and builds a compact corpus index."""

    def run(self, processed_dir: Path) -> dict[str, str]:
        notes: dict[str, str] = {}
        for path in sorted(processed_dir.glob("*.txt")):
            notes[path.name] = path.read_text(encoding="utf-8", errors="ignore")
        if not notes:
            raise FileNotFoundError(
                f"No processed lecture text found in {processed_dir}. "
                "Extract PDF text first or add .txt files."
            )
        return notes


class CoveragePlannerAgent:
    """Creates the exam blueprint from requirements and known course clusters."""

    def run(self, requirements: dict[str, Any], notes: dict[str, str]) -> list[Topic]:
        weights = requirements.get("coverage_weights", {})
        filenames = list(notes)
        return [
            Topic(
                key="work_and_work_systems",
                title="Work and Work Systems",
                weight=int(weights.get("work_and_work_systems", 25)),
                keywords=[
                    "work",
                    "task",
                    "process",
                    "work system",
                    "emergence",
                    "socio-technical system",
                ],
                source_files=[f for f in filenames if f.startswith("M1.1") or f.startswith("M1.2") or f.startswith("M1.3")],
            ),
            Topic(
                key="scientific_management",
                title="Scientific Management",
                weight=int(weights.get("scientific_management", 20)),
                keywords=[
                    "Taylor",
                    "scientific management",
                    "soldiering",
                    "pig iron",
                    "shovel",
                    "Gilbreth",
                ],
                source_files=[f for f in filenames if f.startswith("M1.4")],
            ),
            Topic(
                key="problem_solving_and_ideation",
                title="Problem Solving and Ideation",
                weight=int(weights.get("problem_solving_and_ideation", 25)),
                keywords=[
                    "DASSI",
                    "problem definition",
                    "KJ Method",
                    "Concept Fan",
                    "brainstorming",
                    "delayed judgment",
                ],
                source_files=[f for f in filenames if f.startswith("M2.1.1") or f.startswith("M2.1.2") or f.startswith("M2.1.3")],
            ),
            Topic(
                key="innovation_frameworks",
                title="Five Innovation Frameworks",
                weight=int(weights.get("innovation_frameworks", 15)),
                keywords=[
                    "addition",
                    "subtraction",
                    "alternate means",
                    "combination",
                    "transposition",
                ],
                source_files=[f for f in filenames if f.startswith("M2.1.5")],
            ),
            Topic(
                key="motion_study_and_therbligs",
                title="Motion Study and Therbligs",
                weight=int(weights.get("motion_study_and_therbligs", 15)),
                keywords=[
                    "motion study",
                    "Therbligs",
                    "reach",
                    "grasp",
                    "move",
                    "pre-position",
                ],
                source_files=[f for f in filenames if f.startswith("M3.1.1")],
            ),
        ]


class QuestionWriterAgent:
    """Drafts a balanced deterministic exam for the current MVP."""

    def run(self, requirements: dict[str, Any], topics: list[Topic]) -> list[Question]:
        questions: list[Question] = []
        n = 1

        short_answer_prompts = [
            (
                "Work and Work Systems",
                "Define work, task, process, and work system. Explain how these levels differ.",
                "Work can be described at multiple levels: an action or motion is a small physical or cognitive unit; a task is a purposeful activity; a process is a set of interrelated activities transforming inputs into outputs; and a work system is an organized socio-technical system in which processes occur.",
            ),
            (
                "Scientific Management",
                "List Taylor's four principles of scientific management.",
                "The four principles are developing a science for each element of work, scientifically selecting and training workers, promoting cooperation through aligned incentives, and dividing planning from execution responsibilities.",
            ),
            (
                "Problem Solving and Ideation",
                "What are the five steps of the DASSI engineering problem-solving process?",
                "DASSI consists of defining the problem, analyzing the problem with data collection, searching for alternatives, selecting the best alternative through comparison, and implementing the solution with follow-up.",
            ),
            (
                "Problem Solving and Ideation",
                "State two fundamental principles of brainstorming and explain why they matter.",
                "Delayed judgment prevents premature criticism from blocking creativity, while focus on quantity increases the chance of finding useful ideas among many possible ideas.",
            ),
            (
                "Five Innovation Frameworks",
                "Name the five innovation frameworks introduced in the lecture.",
                "The five frameworks are addition, subtraction, alternate means, combination, and transposition.",
            ),
            (
                "Motion Study and Therbligs",
                "What are Therbligs, and why are they useful in motion study?",
                "Therbligs are basic motion elements developed by Gilbreth to describe manual work. They help analysts identify unnecessary motions, reduce fatigue, and improve task efficiency.",
            ),
        ]

        for topic, prompt, answer in short_answer_prompts:
            questions.append(Question(n, "Short Answer", topic, prompt, 5, answer))
            n += 1

        comparison_prompts = [
            (
                "Work and Work Systems",
                "Compare Alter's work system view with a narrow technology-centered view of improvement.",
                "A technology-centered view treats technology as the main object of improvement. Alter's work system view treats technology as only one part of a broader system that also includes participants, processes, information, products or services, customers, environment, infrastructure, and strategies. This broader view helps explain why performance depends on interactions among components.",
            ),
            (
                "Scientific Management",
                "Compare natural soldiering and systematic soldiering in Taylor's diagnosis of inefficiency.",
                "Natural soldiering is the tendency to conserve effort, while systematic soldiering is deliberate output restriction shaped by mistrust, fear of wage cuts, lack of objective standards, and flawed work organization. Taylor viewed the deeper issue as a system design problem rather than only individual laziness.",
            ),
        ]

        for topic, prompt, answer in comparison_prompts:
            questions.append(Question(n, "Concept Comparison", topic, prompt, 10, answer))
            n += 1

        application_prompts = [
            (
                "Problem Solving and Ideation",
                "A hospital outpatient clinic has long waiting times, repeated data entry, and frustrated staff. Apply DASSI to outline an improvement project.",
                "A strong answer defines the problem precisely, collects data on arrival patterns and bottlenecks, distinguishes symptoms from root causes, generates multiple alternatives such as scheduling changes or information-system redesign, compares them by effectiveness, cost, feasibility, safety, and organizational compatibility, then implements a selected solution with follow-up monitoring.",
            ),
            (
                "Motion Study and Therbligs",
                "A worker repeatedly searches for screws, reaches across the table, grasps a screwdriver, positions a part, and assembles it. Identify likely Therbligs and propose two improvements.",
                "Likely Therbligs include search, reach, grasp, move, position, and assemble. Improvements may include fixed tool locations, color coding or labels, pre-positioned screws, shorter reach distances, guides or fixtures for positioning, and layout changes that reduce unnecessary motion.",
            ),
        ]

        for topic, prompt, answer in application_prompts:
            questions.append(Question(n, "Application", topic, prompt, 15, answer))
            n += 1

        essay_prompt = (
            "Scientific Management",
            "Discuss scientific management as an early form of work system redesign. In your answer, include Taylor, Gilbreth, benefits, and limitations.",
            "A strong essay explains that scientific management applied observation, measurement, standardization, and redesign to organized human work. It should describe Taylor's principles and cases such as pig iron handling or shovel studies, Gilbreth's motion study and Therbligs, and the benefits of productivity, reduced variability, and systematic training. It should also discuss limitations, including worker control, incentive problems, fatigue, narrow views of human motivation, and the need for human-centered work system design.",
        )
        questions.append(Question(n, "Essay", essay_prompt[0], essay_prompt[1], 20, essay_prompt[2]))
        return questions


class ReviewerAgent:
    """Produces lightweight quality checks for the generated exam."""

    def run(self, topics: list[Topic], questions: list[Question]) -> list[str]:
        notes: list[str] = []
        covered = {q.topic for q in questions}
        for topic in topics:
            if topic.title not in covered:
                notes.append(f"Coverage gap: no question directly targets {topic.title}.")

        total_points = sum(q.points for q in questions)
        if total_points != 100:
            notes.append(f"Point total is {total_points}; consider normalizing to 100.")

        kind_counts: dict[str, int] = {}
        for question in questions:
            kind_counts[question.kind] = kind_counts.get(question.kind, 0) + 1
        notes.append(f"Question type mix: {kind_counts}.")
        notes.append("Human review needed: verify whether M3.1.1 Therbligs is officially inside the midterm scope.")
        notes.append("Human review needed: replace deterministic answers with LLM-generated answers after API integration.")
        return notes

