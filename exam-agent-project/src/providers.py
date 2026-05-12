"""LLM providers. The DeterministicProvider lives in agents.py because
the local fallback bank is tied to the agent boundaries; this module
adds the Vertex AI Gemini provider following M5.3.1.1 / M5.3.2 patterns
without dragging the SDK import into the always-imported agent module.

Authentication assumes one of:
- Colab: `from google.colab import auth; auth.authenticate_user()` was run
- Local: `gcloud auth application-default login` was run

Required env vars (or constructor args):
- GCP_PROJECT_ID
- GCP_LOCATION (default us-central1; lecture also uses 'global')
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from agents import DeterministicProvider, Question, Topic, parse_json_block, search_lecture_notes
from costing import UsageTracker


DEFAULT_MODEL_POLICY = {
    "quality_profiles": {
        "draft": {
            "planner": "gemini-2.5-pro",
            "writer": "gemini-2.5-flash",
            "answer_writer": "gemini-2.5-flash",
            "judge": "gemini-2.5-flash-lite",
            "final_rewriter": "gemini-2.5-flash",
        }
    },
    "price_per_1m_tokens_usd": {},
}


def load_model_policy(path: str | Path | None, quality: str = "draft") -> dict[str, Any]:
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    else:
        data = DEFAULT_MODEL_POLICY
    profiles = data.get("quality_profiles", {})
    if quality not in profiles:
        raise ValueError(f"Unknown quality profile: {quality}. Available: {', '.join(profiles)}")
    return {
        "quality": quality,
        "models": profiles[quality],
        "price_per_1m_tokens_usd": data.get("price_per_1m_tokens_usd", {}),
        "fallback_provider": data.get("fallback_provider", "deterministic"),
    }


class GeminiProvider:
    """Vertex AI Gemini implementation of the provider interface.

    Mirrors the methods exposed by `DeterministicProvider`. Falls back
    to the deterministic provider for any single call that errors so a
    transient API problem does not abort the pipeline.
    """

    def __init__(
        self,
        project_id: str | None = None,
        location: str | None = None,
        fallback: DeterministicProvider | None = None,
        model_policy: dict[str, Any] | None = None,
        strict: bool = False,
    ):
        try:
            from google import genai  # type: ignore
            from google.genai.types import HttpOptions  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "google-genai SDK not installed. Run: pip install google-genai\n"
                "See M5.3.1.1 for the full setup."
            ) from exc

        self._genai = genai
        from google.genai import types as genai_types  # type: ignore

        self._types = genai_types

        project = project_id or os.environ.get("GCP_PROJECT_ID")
        if not project:
            raise RuntimeError(
                "GCP_PROJECT_ID is not set. Either pass project_id= or set the env var.\n"
                "See M5.3.1.1 §3 for how to find your project ID."
            )
        loc = location or os.environ.get("GCP_LOCATION", "us-central1")

        self.client = genai.Client(
            vertexai=True,
            project=project,
            location=loc,
            http_options=HttpOptions(api_version="v1"),
        )
        self.fallback = fallback or DeterministicProvider()
        self.model_policy = model_policy or load_model_policy(None)
        self.strict = strict
        self.usage = UsageTracker(self.model_policy.get("price_per_1m_tokens_usd", {}))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _model(self, role: str) -> str:
        return self.model_policy.get("models", {}).get(role, DEFAULT_MODEL_POLICY["quality_profiles"]["draft"].get(role, "gemini-2.5-flash"))

    def _generate(self, model: str, prompt: str, system: str | None = None, stage: str = "llm_call") -> str:
        config = None
        if system:
            config = self._types.GenerateContentConfig(system_instruction=system)
        response = self.client.models.generate_content(
            model=model, contents=prompt, config=config
        )
        text = (response.text or "").strip()
        self.usage.record(stage, model, (system or "") + "\n" + prompt, text)
        return text

    def _generate_json(self, model: str, prompt: str, system: str | None = None, stage: str = "llm_json") -> dict[str, Any]:
        raw = self._generate(model, prompt, system, stage=stage)
        return parse_json_block(raw) or {}

    def _fallback_or_raise(self, exc: Exception, method: str, fallback_call):
        if self.strict:
            raise RuntimeError(f"GeminiProvider.{method} failed in strict mode: {exc}") from exc
        print(f"[GeminiProvider.{method}] fallback: {exc}")
        return fallback_call()

    def get_usage_summary(self) -> dict[str, Any]:
        return self.usage.summary()

    @staticmethod
    def _retrieval_context(notes: dict[str, str], keywords: list[str], limit: int = 3) -> tuple[str, list[str]]:
        snippets: list[str] = []
        sources: list[str] = []
        for kw in keywords:
            for hit in search_lecture_notes(notes, kw, limit=limit):
                if hit not in snippets:
                    snippets.append(hit)
                    src = hit.split("]")[0][1:]
                    if src not in sources:
                        sources.append(src)
        return "\n\n".join(snippets), sources

    # ------------------------------------------------------------------
    # Provider interface (matches DeterministicProvider)
    # ------------------------------------------------------------------

    def plan(self, requirements: dict[str, Any], notes: dict[str, str]) -> dict[str, Any]:
        system = (
            "You are the Coverage Planner for a Scientific Management midterm. "
            "Return JSON only with keys topics, question_mix, rationale. "
            "Each topic has key, title, weight (int), keywords (list[str]), source_files (list[str]). "
            "Use only filenames from the provided notes inventory. Weights must sum to 100."
        )
        inventory = list(notes.keys())
        prompt = (
            f"Requirements:\n{json.dumps(requirements, ensure_ascii=False)}\n\n"
            f"Notes inventory (filenames only):\n{json.dumps(inventory, ensure_ascii=False)}\n\n"
            "Return the JSON plan."
        )
        try:
            plan = self._generate_json(self._model("planner"), prompt, system, stage="planner")
            if not plan.get("topics"):
                raise ValueError("planner returned no topics")
            # Coerce weights to ints, sum-normalize if drifted slightly.
            for t in plan["topics"]:
                t["weight"] = int(t.get("weight", 0))
                t.setdefault("keywords", [])
                t.setdefault("source_files", [])
            return plan
        except Exception as exc:
            return self._fallback_or_raise(exc, "plan", lambda: self.fallback.plan(requirements, notes))

    def write_questions(
        self, kind: str, topic: Topic, count: int, notes: dict[str, str]
    ) -> list[dict[str, str]]:
        system = (
            f"You are the {kind} writer for a university midterm. "
            "Return JSON only: a list of objects with keys topic, prompt, answer. "
            "Anchor every question in the lecture notes; do not invent historical facts."
        )
        ctx, _ = self._retrieval_context(notes, topic.keywords, limit=3)
        prompt = (
            f"Topic: {topic.title}\n"
            f"Lecture context:\n{ctx or '(no direct hits — stay conservative)'}\n\n"
            f"Write exactly {count} {kind} question(s) for this topic. "
            "Each prompt must be a single clear ask. The answer field is a "
            "concise model answer in <=120 words.\n"
            "Return: [{\"topic\":..., \"prompt\":..., \"answer\":...}, ...]"
        )
        try:
            raw = self._generate(self._model("writer"), prompt, system, stage=f"question_writer:{kind}")
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            data = json.loads(cleaned) if cleaned.startswith("[") else None
            if not data:
                match = re.search(r"\[.*\]", cleaned, re.DOTALL)
                data = json.loads(match.group(0)) if match else []
            results: list[dict[str, str]] = []
            for item in data[:count]:
                results.append(
                    {
                        "topic": item.get("topic", topic.title),
                        "prompt": str(item.get("prompt", "")).strip(),
                        "answer": str(item.get("answer", "")).strip(),
                    }
                )
            results = [r for r in results if r["prompt"]]
            if not results:
                raise ValueError("no questions returned")
            return results
        except Exception as exc:
            return self._fallback_or_raise(
                exc,
                "write_questions",
                lambda: self.fallback.write_questions(kind, topic, count, notes),
            )

    def pool_questions(
        self, kind: str, topic: Topic, notes: dict[str, str]
    ) -> list[dict[str, str]]:
        # The LLM has no fixed pool; return one initial batch sized to the
        # likely demand. Writers will request more via write_questions if needed.
        per_kind = {"Short Answer": 3, "Concept Comparison": 2, "Application": 2, "Essay": 1}
        return self.write_questions(kind, topic, per_kind.get(kind, 2), notes)

    def write_answer(self, question: Question, notes: dict[str, str]) -> dict[str, Any]:
        keywords = [w for w in question.topic.split() if len(w) > 3] + question.prompt.split()[:3]
        ctx, sources = self._retrieval_context(notes, keywords, limit=3)
        system = (
            "You are the Answer Writer. Use ReAct: think briefly about which "
            "lecture concept the question targets, then write the model answer "
            "anchored to the supplied context. <=120 words. Return JSON: "
            "{\"answer\":..., \"source_refs\":[...]}"
        )
        prompt = (
            f"Question ({question.kind}, topic {question.topic}):\n{question.prompt}\n\n"
            f"Lecture context:\n{ctx or '(no direct hits — answer conservatively)'}\n\n"
            "Return JSON only."
        )
        try:
            data = self._generate_json(self._model("answer_writer"), prompt, system, stage="answer_writer")
            answer = str(data.get("answer", "")).strip()
            refs = data.get("source_refs") or sources
            if not answer:
                raise ValueError("empty answer")
            return {"answer": answer, "source_refs": list(refs)}
        except Exception as exc:
            return self._fallback_or_raise(
                exc,
                "write_answer",
                lambda: self._fallback_answer(question, notes, sources),
            )

    def judge_question(self, question: Question, notes: dict[str, str]) -> dict[str, Any]:
        system = (
            "You are the Question Judge. Score each question on a 0-5 rubric: "
            "scope_alignment, difficulty_appropriateness, clarity_no_ambiguity, "
            "answerable_from_lecture. Return JSON only: "
            "{target_id, rubric:{...}, total, verdict (GOOD|ACCEPTABLE|POOR), suggestion}. "
            "GOOD if total>=17, ACCEPTABLE if total>=13, else POOR."
        )
        ctx, _ = self._retrieval_context(notes, question.topic.split(), limit=2)
        prompt = (
            f"target_id: Q{question.number}\n"
            f"kind: {question.kind}\n"
            f"topic: {question.topic}\n"
            f"prompt: {question.prompt}\n"
            f"answer: {question.answer}\n\n"
            f"Lecture context:\n{ctx or '(no direct hits)'}"
        )
        try:
            data = self._generate_json(self._model("judge"), prompt, system, stage="question_judge")
            self._normalize_verdict(data, prefix="Q", number=question.number)
            return data
        except Exception as exc:
            return self._fallback_or_raise(exc, "judge_question", lambda: self.fallback.judge_question(question, notes))

    def judge_answer(self, question: Question, notes: dict[str, str]) -> dict[str, Any]:
        system = (
            "You are the Answer Judge. Score each model answer on a 0-5 rubric: "
            "factual_accuracy, completeness, lecture_grounded, concise_pedagogical. "
            "Return JSON only: {target_id, rubric:{...}, total, verdict, suggestion}. "
            "GOOD if total>=17, ACCEPTABLE if total>=13, else POOR."
        )
        ctx, _ = self._retrieval_context(notes, question.topic.split(), limit=2)
        prompt = (
            f"target_id: A{question.number}\n"
            f"question: {question.prompt}\n"
            f"answer: {question.answer}\n"
            f"source_refs: {question.source_refs}\n\n"
            f"Lecture context:\n{ctx or '(no direct hits)'}"
        )
        try:
            data = self._generate_json(self._model("judge"), prompt, system, stage="answer_judge")
            self._normalize_verdict(data, prefix="A", number=question.number)
            return data
        except Exception as exc:
            return self._fallback_or_raise(exc, "judge_answer", lambda: self.fallback.judge_answer(question, notes))

    @staticmethod
    def _normalize_verdict(data: dict[str, Any], prefix: str, number: int) -> None:
        data.setdefault("target_id", f"{prefix}{number}")
        rubric = data.get("rubric") or {}
        # Coerce ints, recompute total if missing.
        clean: dict[str, int] = {}
        for k, v in rubric.items():
            try:
                clean[k] = max(0, min(5, int(v)))
            except Exception:
                clean[k] = 0
        data["rubric"] = clean
        if "total" not in data:
            data["total"] = sum(clean.values())
        data["total"] = int(data["total"])
        if "verdict" not in data:
            t = data["total"]
            data["verdict"] = "GOOD" if t >= 17 else "ACCEPTABLE" if t >= 13 else "POOR"
        data.setdefault("suggestion", "")


    def _fallback_answer(self, question: Question, notes: dict[str, str], sources: list[str]) -> dict[str, Any]:
        base = self.fallback.write_answer(question, notes)
        base["source_refs"] = base.get("source_refs") or sources
        return base


class ConfiguredDeterministicProvider(DeterministicProvider):
    def __init__(self, model_policy: dict[str, Any] | None = None):
        super().__init__()
        self.model_policy = model_policy or load_model_policy(None)

    def get_usage_summary(self) -> dict[str, Any]:
        return {
            "calls": 0,
            "estimated_input_tokens": 0,
            "estimated_output_tokens": 0,
            "estimated_cost_usd": 0.0,
            "by_model": {},
            "records": [],
            "note": "Deterministic provider uses local static generation and makes no billable model calls.",
        }


def make_provider(
    name: str | None = None,
    model_policy: dict[str, Any] | None = None,
    strict: bool = False,
) -> Any:
    """Factory honoring CLI flag + env var.

    name precedence: explicit > EXAM_AGENT_PROVIDER env > 'deterministic'.
    """

    chosen = (name or os.environ.get("EXAM_AGENT_PROVIDER") or "deterministic").lower()
    if chosen == "gemini":
        try:
            return GeminiProvider(model_policy=model_policy, strict=strict)
        except Exception as exc:
            if strict:
                raise
            print(f"[make_provider] Gemini unavailable; using deterministic fallback: {exc}")
            return ConfiguredDeterministicProvider(model_policy=model_policy)
    if chosen == "deterministic":
        return ConfiguredDeterministicProvider(model_policy=model_policy)
    if chosen in {"openai", "anthropic"}:
        raise NotImplementedError(
            f"Provider '{chosen}' is reserved for the premium final-generation hook. "
            "Add the provider client and API key before selecting it."
        )
    raise ValueError(f"Unknown provider: {chosen}. Use deterministic, gemini, openai, or anthropic.")
