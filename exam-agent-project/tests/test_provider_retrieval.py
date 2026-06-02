from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agents import Question  # noqa: E402
from providers import GeminiProvider  # noqa: E402


PROCESSED = ROOT / "lecture_notes" / "processed"


def load_note(filename: str) -> dict[str, str]:
    return {filename: (PROCESSED / filename).read_text(encoding="utf-8")}


class QuestionContextRetrievalTests(unittest.TestCase):
    def test_therbligs_context_includes_definition_and_gilbreth_attribution(self) -> None:
        filename = "M3.1.1 Micro-level Motion Study (Therbligs)_041626.txt"
        question = Question(
            number=10,
            kind="Application",
            topic="Motion Study and Therbligs",
            prompt="Explain the definition and purpose of Therbligs in motion study.",
            points=15,
            focus="definition and purpose of Therbligs",
            source_refs=[filename],
        )

        context, sources = GeminiProvider._question_context(question, load_note(filename))

        self.assertIn(filename, sources)
        self.assertIn("Therbligs", context)
        self.assertIn("Developed by Gilbreth", context)
        self.assertIn("17 basic motion elements", context)
        self.assertRegex(context, rf"\[{re.escape(filename)}#L\d+-L\d+\]")

    def test_therbligs_answer_claims_retrieve_search_select_and_position_support(self) -> None:
        filename = "M3.1.1 Micro-level Motion Study (Therbligs)_041626.txt"
        question = Question(
            number=10,
            kind="Application",
            topic="Motion Study and Therbligs",
            prompt=(
                "A worker spends time locating a part, choosing it from alternatives, "
                "and aligning it. Identify the relevant Therbligs and propose improvements."
            ),
            points=15,
            focus="definition and purpose of Therbligs",
            answer=(
                "Therbligs are 17 basic motion elements developed by Gilbreth. "
                "Use S (Search), SL (Select), and P (Position). Improve them with "
                "color coding, standardization, and guides or chamfers."
            ),
            source_refs=[filename],
            rubric=[
                "Defines Therbligs as 17 basic motion elements used to describe manual work (3 pts)",
                "Identifies S (Search), SL (Select), and P (Position) correctly (6 pts)",
                "Proposes color coding, standardization, and guides or chamfers (6 pts)",
            ],
        )

        context, _sources = GeminiProvider._question_context(question, load_note(filename))

        self.assertIn("Developed by Gilbreth", context)
        self.assertIn("S (Search)", context)
        self.assertIn("SL (Select)", context)
        self.assertIn("P (Position)", context)
        self.assertIn("color coding", context)
        self.assertIn("standardization", context)
        self.assertIn("guides to make positioning easier", context)

    def test_dassi_context_includes_search_for_alternatives_step(self) -> None:
        filename = "M2.1.1 Engineering Problem-Solving Process.txt"
        question = Question(
            number=2,
            kind="Short Answer",
            topic="Problem Solving and Ideation",
            prompt="Explain the Search for Alternatives step in DASSI.",
            points=5,
            focus="DASSI Search for Alternatives",
            source_refs=[filename],
        )

        context, sources = GeminiProvider._question_context(question, load_note(filename))

        self.assertIn(filename, sources)
        self.assertIn("Step 3: Search for Alternatives", context)

    def test_subtraction_context_includes_framework_and_removal_language(self) -> None:
        filename = "M2.1.5 Systematic Innovation Methods 1 (Five Frameworks).txt"
        question = Question(
            number=9,
            kind="Application",
            topic="Innovation Frameworks",
            prompt="Propose an application of the Subtraction framework by removing features.",
            points=15,
            focus="Subtraction framework",
            source_refs=[filename],
        )

        context, sources = GeminiProvider._question_context(question, load_note(filename))

        self.assertIn(filename, sources)
        self.assertIn("subtraction framework", context.lower())
        self.assertIn("removing features", context)


if __name__ == "__main__":
    unittest.main()
