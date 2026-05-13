"""LLM providers. The DeterministicProvider lives in agents.py because
the local fallback bank is tied to the agent boundaries; this module
adds a Gemini provider following M5.3.1.1 / M5.3.2 patterns without
dragging the SDK import into the always-imported agent module.

Gemini authentication supports two modes:
- Vertex AI / Agent Platform API, matching the lecture notebooks:
  set GCP_PROJECT_ID or GOOGLE_CLOUD_PROJECT and authenticate with
  `gcloud auth application-default login` outside Colab.
- Google AI Studio API key: set GEMINI_API_KEY or GOOGLE_API_KEY.

Vertex AI is preferred when a project ID is present or when the provider is
selected as `vertex`. Set EXAM_AGENT_GEMINI_AUTH=api_key to force API-key mode.
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
        "model_fallbacks": data.get("model_fallbacks", {}),
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

        project = (
            project_id
            or os.environ.get("GCP_PROJECT_ID")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("PROJECT_ID")
        )
        if not project:
            raise RuntimeError(
                "GCP_PROJECT_ID is not set. Set it to the Google Cloud Project ID "
                "from the lecture's Agent Platform API setup.\n"
                "Windows cmd example: set GCP_PROJECT_ID=your-project-id\n"
                "PowerShell example: $env:GCP_PROJECT_ID=\"your-project-id\""
            )
        loc = (
            location
            or os.environ.get("GCP_LOCATION")
            or os.environ.get("GOOGLE_CLOUD_LOCATION")
            or os.environ.get("LOCATION")
            or "us-central1"
        )

        self.client = genai.Client(
            vertexai=True,
            project=project,
            location=loc,
            http_options=HttpOptions(api_version="v1"),
        )
        self.auth_mode = "vertex_ai"
        self.fallback = fallback or DeterministicProvider()
        self.model_policy = model_policy or load_model_policy(None)
        self.strict = strict
        self.usage = UsageTracker(self.model_policy.get("price_per_1m_tokens_usd", {}))
        self.model_fallback_events: list[dict[str, str]] = []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _model(self, role: str) -> str:
        return self.model_policy.get("models", {}).get(role, DEFAULT_MODEL_POLICY["quality_profiles"]["draft"].get(role, "gemini-2.5-flash"))

    def _models_for(self, role: str) -> list[str]:
        primary = self._model(role)
        configured = self.model_policy.get("model_fallbacks", {}).get(primary, [])
        if isinstance(configured, str):
            configured = [configured]
        defaults = {
            "gemini-2.5-pro": ["gemini-2.5-flash", "gemini-2.5-flash-lite"],
            "premium": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"],
        }
        candidates = [primary] + list(configured or defaults.get(primary, []))
        deduped: list[str] = []
        for model in candidates:
            if model and model not in deduped:
                deduped.append(model)
        return deduped

    @staticmethod
    def _is_retryable_model_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in [
                "429",
                "resource_exhausted",
                "quota",
                "not found",
                "not supported",
                "permission_denied",
            ]
        )

    def _generate_for_role(self, role: str, prompt: str, system: str | None = None, stage: str = "llm_call") -> str:
        errors: list[str] = []
        candidates = self._models_for(role)
        for model in candidates:
            try:
                return self._generate(model, prompt, system, stage=stage)
            except Exception as exc:
                errors.append(f"{model}: {exc}")
                if model != candidates[-1] and self._is_retryable_model_error(exc):
                    self.model_fallback_events.append(
                        {
                            "stage": stage,
                            "role": role,
                            "failed_model": model,
                            "fallback_model": candidates[candidates.index(model) + 1],
                            "error": str(exc)[:500],
                        }
                    )
                    continue
                raise RuntimeError("All Gemini model attempts failed: " + " | ".join(errors)) from exc

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
        summary = self.usage.summary()
        summary["auth_mode"] = getattr(self, "auth_mode", "vertex_ai")
        summary["model_fallback_events"] = self.model_fallback_events
        return summary

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
            raw = self._generate_for_role("planner", prompt, system, stage="planner")
            plan = parse_json_block(raw) or {}
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
            "Return JSON only: a list of objects with keys topic, prompt, answer, "
            "learning_objective, bloom_level, difficulty, estimated_time_minutes, "
            "exam_intent, assessed_skill, rubric. "
            "Anchor every question in the lecture notes; do not invent historical facts."
        )
        ctx, _ = self._retrieval_context(notes, topic.keywords, limit=3)
        prompt = (
            f"Topic: {topic.title}\n"
            f"Lecture context:\n{ctx or '(no direct hits — stay conservative)'}\n\n"
            f"Write exactly {count} {kind} question(s) for this topic. "
            "Each prompt must be a single clear ask. The answer field is a "
            "concise model answer in <=120 words.\n"
            "Rubric must be a list of 2-4 concrete scoring criteria. "
            "Return: [{\"topic\":..., \"prompt\":..., \"answer\":..., "
            "\"learning_objective\":..., \"bloom_level\":..., \"difficulty\":..., "
            "\"estimated_time_minutes\":..., \"exam_intent\":..., "
            "\"assessed_skill\":..., \"rubric\":[...]}, ...]"
        )
        try:
            raw = self._generate_for_role("writer", prompt, system, stage=f"question_writer:{kind}")
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
                        "learning_objective": str(item.get("learning_objective", "")).strip(),
                        "bloom_level": str(item.get("bloom_level", "")).strip(),
                        "difficulty": str(item.get("difficulty", "")).strip(),
                        "estimated_time_minutes": int(item.get("estimated_time_minutes", 0) or 0),
                        "exam_intent": str(item.get("exam_intent", "")).strip(),
                        "assessed_skill": str(item.get("assessed_skill", "")).strip(),
                        "rubric": list(item.get("rubric", [])),
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
            raw = self._generate_for_role("answer_writer", prompt, system, stage="answer_writer")
            data = parse_json_block(raw) or {}
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
            raw = self._generate_for_role("judge", prompt, system, stage="question_judge")
            data = parse_json_block(raw) or {}
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
            raw = self._generate_for_role("judge", prompt, system, stage="answer_judge")
            data = parse_json_block(raw) or {}
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


class GeminiApiKeyProvider(GeminiProvider):
    """Google AI Studio API-key variant of the Gemini provider.

    This avoids the GCP project ID / gcloud setup path. Set either
    GEMINI_API_KEY or GOOGLE_API_KEY before selecting --provider gemini.
    """

    def __init__(
        self,
        api_key: str | None = None,
        fallback: DeterministicProvider | None = None,
        model_policy: dict[str, Any] | None = None,
        strict: bool = False,
    ):
        try:
            from google import genai  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "google-genai SDK not installed. Run: pip install google-genai"
            ) from exc

        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is required for Gemini API-key mode.")

        self._genai = genai
        from google.genai import types as genai_types  # type: ignore

        self._types = genai_types
        self.client = genai.Client(api_key=key)
        self.auth_mode = "api_key"
        self.fallback = fallback or DeterministicProvider()
        self.model_policy = model_policy or load_model_policy(None)
        self.strict = strict
        self.usage = UsageTracker(self.model_policy.get("price_per_1m_tokens_usd", {}))
        self.model_fallback_events: list[dict[str, str]] = []


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
    if chosen in {"gemini", "vertex", "vertexai", "gemini-vertex"}:
        try:
            auth_mode = os.environ.get("EXAM_AGENT_GEMINI_AUTH", "auto").lower()
            has_project = bool(
                os.environ.get("GCP_PROJECT_ID")
                or os.environ.get("GOOGLE_CLOUD_PROJECT")
                or os.environ.get("PROJECT_ID")
            )
            has_api_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))

            if chosen in {"vertex", "vertexai", "gemini-vertex"} or auth_mode in {"vertex", "vertex_ai"}:
                return GeminiProvider(model_policy=model_policy, strict=strict)
            if auth_mode in {"api_key", "apikey", "ai_studio"}:
                return GeminiApiKeyProvider(model_policy=model_policy, strict=strict)
            if has_project:
                return GeminiProvider(model_policy=model_policy, strict=strict)
            if has_api_key:
                return GeminiApiKeyProvider(model_policy=model_policy, strict=strict)
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
    raise ValueError(f"Unknown provider: {chosen}. Use deterministic, gemini, vertex, openai, or anthropic.")
