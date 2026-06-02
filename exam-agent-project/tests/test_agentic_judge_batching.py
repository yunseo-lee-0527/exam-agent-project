from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agents import AnswerConsistencyJudgeAgent, FactualGroundingJudgeAgent, Question  # noqa: E402


def sample_questions() -> list[Question]:
    return [
        Question(
            number=1,
            kind="Short Answer",
            topic="Work",
            prompt="Define work.",
            points=5,
            answer="Work transforms inputs into outputs.",
            source_refs=["note.txt"],
            rubric=["Defines work (5 pts)"],
        ),
        Question(
            number=2,
            kind="Short Answer",
            topic="Therbligs",
            prompt="Define Therbligs.",
            points=5,
            answer="Therbligs are basic motion elements.",
            source_refs=["note.txt"],
            rubric=["Defines Therbligs (5 pts)"],
        ),
    ]


class BatchProvider:
    def __init__(self) -> None:
        self.factual_calls = 0
        self.consistency_calls = 0

    def judge_factual_grounding_batch(self, questions, notes, chars_per_source=900):
        self.factual_calls += 1
        return [{"verdict": "PASS", "errors": []} for _ in questions]

    def judge_answer_consistency_batch(self, questions):
        self.consistency_calls += 1
        return [{"consistent": True, "issues": [], "verdict": "PASS"} for _ in questions]


class FailingBatchProvider:
    def judge_factual_grounding_batch(self, questions, notes, chars_per_source=900):
        raise RuntimeError("quota exhausted")


class AgenticJudgeBatchingTests(unittest.TestCase):
    def test_factual_judge_uses_one_batch_call(self) -> None:
        provider = BatchProvider()

        findings = FactualGroundingJudgeAgent(provider).run(
            {"questions": sample_questions(), "notes": {"note.txt": "lecture"}}
        )

        self.assertEqual(provider.factual_calls, 1)
        self.assertTrue(all(finding.verdict == "PASS" for finding in findings))

    def test_factual_batch_failure_blocks_pass(self) -> None:
        findings = FactualGroundingJudgeAgent(FailingBatchProvider()).run(
            {"questions": sample_questions(), "notes": {"note.txt": "lecture"}}
        )

        self.assertTrue(all(finding.verdict == "HARD_FAIL" for finding in findings))
        self.assertTrue(
            all("factual_check_unavailable" in finding.failed_checks for finding in findings)
        )

    def test_consistency_judge_uses_one_batch_call(self) -> None:
        provider = BatchProvider()

        findings = AnswerConsistencyJudgeAgent(provider).run(
            {"questions": sample_questions()}
        )

        self.assertEqual(provider.consistency_calls, 1)
        self.assertTrue(all(finding.verdict == "PASS" for finding in findings))


if __name__ == "__main__":
    unittest.main()
