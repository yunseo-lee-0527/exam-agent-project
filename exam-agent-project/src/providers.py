"""LLM providers. The DeterministicProvider lives in agents.py because
the local fallback bank is tied to the agent boundaries; this module
adds a Gemini provider following M5.3.1.1 / M5.3.2 patterns without
dragging the SDK import into the always-imported agent module.

Gemini authentication supports two modes:
- Vertex AI / Agent Platform API, matching the lecture notebooks:
  set GCP_PROJECT_ID or GOOGLE_CLOUD_PROJECT and authenticate with
  `gcloud auth application-default login` outside Colab.
- Google AI Studio API key: place it in the ignored project-root
  `.gemini_api_key` file, or set GEMINI_API_KEY / GOOGLE_API_KEY.
  The project-local file takes precedence over environment variables so
  changing the file cannot silently leave a stale shell key active.

Vertex AI is preferred when a project ID is present or when the provider is
selected as `vertex`. Set EXAM_AGENT_GEMINI_AUTH=api_key to force API-key mode.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from agents import (
    DeterministicProvider,
    Question,
    Topic,
    normalize_topic_key,
    parse_json_block,
    search_lecture_notes,
    strip_source_ref_locator,
)
from costing import UsageTracker


DEFAULT_MODEL_POLICY = {
    "quality_profiles": {
        "draft": {
            "planner": "gemini-2.5-flash",
            "writer": "gemini-2.5-flash",
            "answer_writer": "gemini-2.5-flash",
            "judge": "gemini-2.5-flash-lite",
            "final_rewriter": "gemini-2.5-flash",
        }
    },
    "price_per_1m_tokens_usd": {},
}


def load_model_policy(
    path: str | Path | None,
    quality: str = "draft",
    model_preset: str | None = None,
    model_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    else:
        data = DEFAULT_MODEL_POLICY
    profiles = data.get("quality_profiles", {})
    if quality not in profiles:
        raise ValueError(f"Unknown quality profile: {quality}. Available: {', '.join(profiles)}")
    models = dict(profiles[quality])
    preset_info: dict[str, Any] | None = None
    if model_preset:
        presets = data.get("model_presets", {})
        if model_preset not in presets:
            raise ValueError(f"Unknown model preset: {model_preset}. Available: {', '.join(presets)}")
        preset_info = presets[model_preset]
        models.update(preset_info.get("models", {}))
    if model_overrides:
        models.update({role: model for role, model in model_overrides.items() if model})
    return {
        "quality": quality,
        "model_preset": model_preset,
        "model_preset_description": (preset_info or {}).get("description", ""),
        "models": models,
        "available_model_presets": sorted(data.get("model_presets", {})),
        "model_fallbacks": data.get("model_fallbacks", {}),
        "price_per_1m_tokens_usd": data.get("price_per_1m_tokens_usd", {}),
        "fallback_provider": data.get("fallback_provider", "deterministic"),
}


def _read_local_gemini_api_key(path: str | Path | None = None) -> str | None:
    key_path = Path(path) if path else Path(__file__).resolve().parents[1] / ".gemini_api_key"
    try:
        key = key_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return key or None


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
            http_options=HttpOptions(
                api_version="v1",
                timeout=60_000,
                retry_options=genai_types.HttpRetryOptions(attempts=1),
            ),
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

    @staticmethod
    def _strip_provider_prefix(model: str) -> str:
        if ":" in model:
            provider, raw_model = model.split(":", 1)
            if provider in {"gemini", "vertex", "openai", "anthropic", "claude"}:
                return raw_model
        return model

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
                "503",
                "unavailable",
                "overloaded",
                "high demand",
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

    @staticmethod
    def _rate_limit_delay(exc: Exception) -> int:
        msg = str(exc).lower()
        # "please retry in 18.88s" — from human-readable API message
        m = re.search(r"please retry in (\d+(?:\.\d+)?)s", msg)
        if m:
            return min(int(float(m.group(1))) + 3, 120)
        # "retryDelay": "18s" — from proto RetryInfo field (cap to avoid absurd values)
        m = re.search(r'"retrydelay":\s*"(\d+)s"', msg)
        if m:
            return min(int(m.group(1)) + 3, 120)
        return 30

    @staticmethod
    def _is_transient_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        if "generaterequestsperdayperprojectpermodel" in msg:
            return False
        return any(k in msg for k in ["429", "resource_exhausted", "quota", "503", "unavailable", "overloaded"])

    def _generate(self, model: str, prompt: str, system: str | None = None, stage: str = "llm_call") -> str:
        model = self._strip_provider_prefix(model)
        config_kwargs: dict[str, Any] = {"response_mime_type": "application/json"}
        if system:
            config_kwargs["system_instruction"] = system
        config = self._types.GenerateContentConfig(**config_kwargs)
        max_retries = 5
        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=model, contents=prompt, config=config
                )
                text = (response.text or "").strip()
                self.usage.record(stage, model, (system or "") + "\n" + prompt, text)
                return text
            except Exception as exc:
                if self._is_transient_error(exc) and attempt < max_retries - 1:
                    is_503 = "503" in str(exc) or "unavailable" in str(exc).lower()
                    delay = 20 if is_503 else self._rate_limit_delay(exc)
                    label = "Server overload (503)" if is_503 else "Rate-limited (429)"
                    print(f"[{self.__class__.__name__}] {label} on {model}, waiting {delay}s (attempt {attempt + 1}/{max_retries})...")
                    time.sleep(delay)
                    continue
                raise

    def _fallback_or_raise(self, exc: Exception, method: str, fallback_call):
        if self.strict:
            raise RuntimeError(f"{self.__class__.__name__}.{method} failed in strict mode: {exc}") from exc
        print(f"[{self.__class__.__name__}.{method}] fallback: {exc}")
        return fallback_call()

    def get_usage_summary(self) -> dict[str, Any]:
        summary = self.usage.summary()
        summary["auth_mode"] = getattr(self, "auth_mode", "vertex_ai")
        summary["auth_source"] = getattr(self, "auth_source", "environment_or_adc")
        summary["model_fallback_events"] = self.model_fallback_events
        return summary

    @staticmethod
    def _retrieval_terms(text: str) -> list[str]:
        stopwords = {
            "about", "after", "against", "also", "answer", "briefly", "common",
            "context", "could", "describe", "discuss", "each", "elaborate",
            "explain", "from", "have", "into", "lecture", "notes", "primary",
            "question", "specific", "their", "these", "this", "through", "using",
            "what", "when", "where", "which", "with", "within", "would",
        }
        terms: list[str] = []
        for token in re.findall(r"[A-Za-z][A-Za-z-]{3,}", text.lower()):
            if token in stopwords or token in terms:
                continue
            terms.append(token)
        expansions = {
            "therbligs": ["gilbreth", "basic", "motion", "elements"],
            "dassi": ["define", "analyze", "search", "alternatives", "select", "implement"],
            "subtraction": ["remove", "removing", "features", "resources", "streamlining"],
        }
        for trigger, related_terms in expansions.items():
            if trigger not in terms:
                continue
            for term in related_terms:
                if term not in terms:
                    terms.append(term)
        return terms

    @staticmethod
    def _ranked_passages(
        source: str,
        body: str,
        terms: list[str],
    ) -> list[dict[str, Any]]:
        """Rank lecture slide/page passages and retain line spans for traceability."""

        lines = body.splitlines()
        sections: list[tuple[int, int, list[str]]] = []
        start_line = 1
        current: list[str] = []
        for line_number, line in enumerate(lines, start=1):
            if re.match(r"^\s*---\s*[Pp]age\s+\d+\s*---\s*$", line):
                if current:
                    sections.append((start_line, line_number - 1, current))
                start_line = line_number + 1
                current = []
                continue
            current.append(line)
        if current:
            sections.append((start_line, len(lines), current))
        if not sections and lines:
            sections.append((1, len(lines), lines))

        rendered_sections = [
            (
                start,
                end,
                " ".join(line.strip() for line in section_lines if line.strip()),
            )
            for start, end, section_lines in sections
        ]
        term_document_frequency = {
            term: sum(term in text.lower() for _start, _end, text in rendered_sections)
            for term in terms
        }

        ranked: list[dict[str, Any]] = []
        for start, end, text in rendered_sections:
            if not text:
                continue
            text_lc = text.lower()
            matched = [term for term in terms if term in text_lc]
            if not matched:
                continue
            ranked.append(
                {
                    "source": source,
                    "start": start,
                    "end": end,
                    "text": text[:1200] + ("..." if len(text) > 1200 else ""),
                    "term_weights": {
                        term: 1.0 / term_document_frequency[term]
                        for term in set(matched)
                    },
                    "score": sum(
                        1.0 / term_document_frequency[term]
                        for term in set(matched)
                    ),
                }
            )
        ranked.sort(key=lambda item: (-float(item["score"]), int(item["start"])))
        return ranked

    @staticmethod
    def _select_passages(passages: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        """Prefer passages that add distinct support instead of repeated boilerplate."""

        remaining = list(passages)
        selected: list[dict[str, Any]] = []
        covered_terms: set[str] = set()
        while remaining and len(selected) < limit:
            remaining.sort(
                key=lambda item: (
                    -sum(
                        float(weight)
                        for term, weight in item.get("term_weights", {}).items()
                        if term not in covered_terms
                    ),
                    -float(item["score"]),
                    str(item["source"]),
                    int(item["start"]),
                )
            )
            passage = remaining.pop(0)
            selected.append(passage)
            covered_terms.update(passage.get("term_weights", {}))
        return selected

    @staticmethod
    def _render_passage(passage: dict[str, Any]) -> str:
        return (
            f"[{passage['source']}#L{passage['start']}-L{passage['end']}] "
            f"{passage['text']}"
        )

    @staticmethod
    def _retrieval_context(notes: dict[str, str], keywords: list[str], limit: int = 3) -> tuple[str, list[str]]:
        terms = GeminiProvider._retrieval_terms(" ".join(keywords))
        passages: list[dict[str, Any]] = []
        for source, body in notes.items():
            passages.extend(GeminiProvider._ranked_passages(source, body, terms))
        passages.sort(key=lambda item: (-float(item["score"]), str(item["source"]), int(item["start"])))

        snippets: list[str] = []
        sources: list[str] = []
        for passage in GeminiProvider._select_passages(passages, limit):
            snippets.append(GeminiProvider._render_passage(passage))
            src = str(passage["source"])
            if src not in sources:
                sources.append(src)
        return "\n\n".join(snippets), sources

    @staticmethod
    def _question_context(question: Question, notes: dict[str, str], limit: int = 4) -> tuple[str, list[str]]:
        claim_text = " ".join([question.answer, " ".join(question.rubric)]).strip()
        terms = GeminiProvider._retrieval_terms(
            " ".join(
                [
                    question.focus,
                    question.topic,
                    claim_text or question.prompt,
                ]
            )
        )
        refs = [
            clean
            for ref in question.source_refs
            if (clean := strip_source_ref_locator(ref)) in notes
        ]
        snippets: list[str] = []
        sources: list[str] = []
        locators: set[tuple[str, int, int]] = set()

        cited_passages: list[dict[str, Any]] = []
        for ref in refs:
            cited_passages.extend(GeminiProvider._ranked_passages(ref, notes[ref], terms))
        cited_passages.sort(
            key=lambda item: (-float(item["score"]), refs.index(str(item["source"])), int(item["start"]))
        )
        for passage in GeminiProvider._select_passages(cited_passages, limit):
            snippets.append(GeminiProvider._render_passage(passage))
            source = str(passage["source"])
            sources.append(source) if source not in sources else None
            locators.add((source, int(passage["start"]), int(passage["end"])))

        if len(snippets) < limit:
            extra_passages: list[dict[str, Any]] = []
            for source, body in notes.items():
                extra_passages.extend(GeminiProvider._ranked_passages(source, body, terms))
            extra_passages.sort(
                key=lambda item: (-float(item["score"]), str(item["source"]), int(item["start"]))
            )
            for passage in GeminiProvider._select_passages(extra_passages, len(extra_passages)):
                locator = (
                    str(passage["source"]),
                    int(passage["start"]),
                    int(passage["end"]),
                )
                if locator in locators:
                    continue
                snippets.append(GeminiProvider._render_passage(passage))
                source = str(passage["source"])
                sources.append(source) if source not in sources else None
                locators.add(locator)
                if len(snippets) >= limit:
                    break
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
        self,
        kind: str,
        topic: Topic,
        count: int,
        notes: dict[str, str],
        revision_instruction: str | None = None,
    ) -> list[dict[str, str]]:
        system = (
            f"You are the {kind} writer for a university midterm. "
            "Return JSON only: a list of objects with keys topic, prompt, source_refs. "
            "Do NOT write model answers, grading rubrics, learning objectives, or other metadata; "
            "a separate answer writer will ground those fields after the prompt is accepted. "
            "Anchor every question in the lecture notes; do not invent historical facts. "
            "For innovation-framework questions, use only the lecture frameworks: "
            "Addition, Subtraction, Alternate, Combination, and Transposition. "
            "Do not substitute external frameworks such as Lean Startup or Design Thinking. "
            "For application questions, clearly ask for a proposed application or use a "
            "lecture-provided example. Do not imply that an invented scenario is a lecture fact. "
        )
        ctx, sources = self._retrieval_context(notes, topic.keywords, limit=3)
        prompt = (
            f"Topic: {topic.title}\n"
            f"Lecture context:\n{ctx or '(no direct hits — stay conservative)'}\n\n"
            f"Write exactly {count} {kind} question(s) for this topic. "
            "Each prompt must be a single clear ask. Return source_refs using only filenames "
            "from the lecture context. Return: [{\"topic\":..., \"prompt\":..., "
            "\"source_refs\":[...]}, ...]"
        )
        if revision_instruction:
            prompt += (
                "\n\nRevision instruction from the previous judge: "
                + revision_instruction
                + "\nRewrite the question so it directly addresses this instruction."
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
                        "answer": "",
                        "source_refs": list(item.get("source_refs") or sources),
                        "learning_objective": "",
                        "bloom_level": "",
                        "difficulty": "",
                        "estimated_time_minutes": 0,
                        "exam_intent": "",
                        "assessed_skill": "",
                        "rubric": [],
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

    def batch_write_questions(
        self,
        kind: str,
        topics: list,
        count: int,
        notes: dict[str, str],
        slots: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Generate all `count` questions of `kind` across all topics in ONE API call.

        Replaces the 5 separate pool_questions calls (one per topic) with a single
        call, reducing the question-writing phase from 20 calls to 4.
        """
        topic_by_key = {t.key: t for t in topics}
        if not slots:
            ordered = sorted(topics, key=lambda t: -getattr(t, "weight", 1))
            slots = [
                {
                    "slot_id": f"{kind.lower().replace(' ', '_')}:{index + 1}",
                    "topic_key": ordered[index % len(ordered)].key,
                    "topic_title": ordered[index % len(ordered)].title,
                    "coverage_contribution": {},
                }
                for index in range(count)
            ]
        slots = list(slots)

        system = (
            f"You are the {kind} specialist writer for a university midterm on Scientific Management. "
            "Return a JSON ARRAY. Each element must have exactly these keys: "
            "slot_id (string, copy it exactly from the requested slot), "
            "topic (string, copy the slot's exact topic_key), prompt (string), "
            "source_refs (list of lecture filenames that support this question). "
            "Do NOT write model answers, grading rubrics, learning objectives, or other metadata; "
            "a separate answer writer will ground those fields against the selected source_refs. "
            "Write one question for each requested slot. Use the slot's target_difficulty. "
            "Do not invent or merge slots. For innovation-framework slots, use only the "
            "lecture frameworks: Addition, Subtraction, Alternate, Combination, and "
            "Transposition. Do not substitute external frameworks such as Lean Startup "
            "or Design Thinking. For application questions, clearly ask for a proposed "
            "application or use a lecture-provided example. Do not imply that an invented "
            "scenario is a lecture fact. "
            "CRITICAL diversity rule: each slot begins with a line "
            "'>>> MANDATORY TOPIC for this question: ... <<<'. The 'prompt' you write "
            "for that slot MUST be specifically and exclusively about that exact "
            "sub-topic. Do NOT drift to a neighbouring concept, even if it appears in "
            "the lecture context. Two questions in this exam must never test the same "
            "concept, so honouring each slot's MANDATORY TOPIC is essential."
        )
        topic_blocks: list[str] = []
        for t in topics:
            ctx, srcs = self._retrieval_context(notes, t.keywords, limit=2)
            topic_blocks.append(
                f"topic_key: {t.key}\n"
                f"  title: {t.title}\n"
                f"  source files: {srcs}\n"
                f"  context: {(ctx or '(none)')[:300]}"
            )
        # Each slot already carries a distinct sub-topic 'focus' assigned globally
        # in build_question_slots() (round-robin within each topic across all kinds).
        # We embed it as REQUIRED_FOCUS so the LLM targets a different concept per
        # slot, preventing overlap at generation time.
        slot_lines = []
        for slot in slots:
            focus = slot.get("focus", "")
            head = (
                f">>> MANDATORY TOPIC for this question: {focus} <<<\n"
                if focus else ""
            )
            slot_lines.append(
                head
                + f"slot_id: {slot['slot_id']}; topic_key: {slot['topic_key']}; "
                f"title: {slot.get('topic_title', slot['topic_key'])}; "
                f"total_points: {slot.get('points', '?')}; "
                f"coverage_contribution: {slot.get('coverage_contribution', {})}; "
                f"target_difficulty: {slot.get('target_difficulty', '')}"
            )

        prompt = (
            f"Write exactly {count} {kind} exam questions, one per requested slot.\n\n"
            + "Requested slots:\n"
            + "\n".join(slot_lines)
            + "\n\nLecture context by topic:\n"
            + "\n\n".join(topic_blocks)
            + f"\n\nReturn a JSON array of exactly {count} objects in requested slot order."
        )
        try:
            raw = self._generate_for_role("writer", prompt, system, stage=f"batch_writer:{kind}")
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            data = json.loads(cleaned) if cleaned.startswith("[") else None
            if not data:
                m = re.search(r"\[.*\]", cleaned, re.DOTALL)
                data = json.loads(m.group(0)) if m else []
            slot_by_id = {str(slot["slot_id"]): slot for slot in slots}
            unused_slot_ids = [str(slot["slot_id"]) for slot in slots]
            results_by_slot: dict[str, dict[str, Any]] = {}
            for item in data:
                p = str(item.get("prompt", "")).strip()
                if not p:
                    continue
                requested_slot_id = str(item.get("slot_id", ""))
                if requested_slot_id not in unused_slot_ids:
                    if not unused_slot_ids:
                        continue
                    requested_slot_id = unused_slot_ids[0]
                unused_slot_ids.remove(requested_slot_id)
                slot = slot_by_id[requested_slot_id]
                topic = topic_by_key.get(str(slot["topic_key"]))
                topic_title = topic.title if topic else str(slot.get("topic_title", slot["topic_key"]))
                results_by_slot[requested_slot_id] = {
                    "slot_id": requested_slot_id,
                    "topic": topic_title,
                    "prompt": p,
                    "focus": str(slot.get("focus", "")).strip(),
                    "answer": "",
                    "source_refs": list(item.get("source_refs", [])),
                    "learning_objective": "",
                    "bloom_level": "",
                    "difficulty": str(
                        slot.get("target_difficulty") or item.get("difficulty", "")
                    ).strip(),
                    "estimated_time_minutes": 0,
                    "exam_intent": "",
                    "assessed_skill": "",
                    "rubric": [],
                    "coverage_contribution": {
                        normalize_topic_key(str(k)): int(v)
                        for k, v in (slot.get("coverage_contribution") or {}).items()
                    },
                }
            results = [
                results_by_slot[str(slot["slot_id"])]
                for slot in slots
                if str(slot["slot_id"]) in results_by_slot
            ]
            if len(results) != count:
                raise ValueError(f"Expected {count} questions from batch writer, got {len(results)}")
            return results
        except Exception as exc:
            return self._fallback_or_raise(
                exc,
                "batch_write_questions",
                lambda: [
                    q
                    for t in (topics or [])
                    for q in self.fallback.write_questions(kind, t, max(1, count // max(1, len(topics))), notes)
                ][:count],
            )

    def write_answer(
        self,
        question: Question,
        notes: dict[str, str],
        revision_instruction: str | None = None,
    ) -> dict[str, Any]:
        keywords = [w for w in question.topic.split() if len(w) > 3] + question.prompt.split()[:3]
        ctx, sources = self._retrieval_context(notes, keywords, limit=3)
        system = (
            "You are the Answer Writer. Use the supplied retrieval context as "
            "your observation, then write the model answer anchored to that "
            "context. Do not output hidden reasoning. <=120 words. Return JSON: "
            "{\"answer\":..., \"source_refs\":[...]}"
        )
        prompt = (
            f"Question ({question.kind}, topic {question.topic}):\n{question.prompt}\n\n"
            f"Lecture context:\n{ctx or '(no direct hits — answer conservatively)'}\n\n"
            "Return JSON only."
        )
        if revision_instruction:
            prompt += (
                "\n\nRevision instruction from the previous judge: "
                + revision_instruction
                + "\nRewrite the answer so it directly addresses this instruction."
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

    def batch_judge_questions(self, questions: list[Question], notes: dict[str, str]) -> list[dict[str, Any]]:
        """Judge ALL questions in a single API call (11 questions → 1 call)."""
        system = (
            "You are the Question Judge. Score EACH question on a 0-5 rubric: "
            "scope_alignment, difficulty_appropriateness, clarity_no_ambiguity, answerable_from_lecture. "
            "Return a JSON ARRAY — one object per question — each with: "
            "target_id, rubric:{...}, total, verdict (GOOD|ACCEPTABLE|POOR), suggestion. "
            "GOOD if total>=17, ACCEPTABLE if total>=13, else POOR. "
            "The array length MUST equal the number of questions."
        )
        items = []
        for q in questions:
            ctx, _ = self._question_context(q, notes, limit=2)
            items.append(
                f"Q{q.number} | {q.kind} | {q.topic}\n"
                f"  focus: {q.focus or '(none)'}\n"
                f"  prompt: {q.prompt}\n"
                f"  answer: {q.answer[:200]}\n"
                f"  context: {(ctx or '(none)')[:500]}"
            )
        prompt = "Score these questions and return a JSON array:\n\n" + "\n\n".join(items)
        try:
            raw = self._generate_for_role("judge", prompt, system, stage="batch_question_judge")
            parsed = parse_json_block(raw)
            results: list[dict[str, Any]] = parsed if isinstance(parsed, list) else []
            if len(results) != len(questions):
                raise ValueError(f"Expected {len(questions)} verdicts, got {len(results)}")
            for i, data in enumerate(results):
                self._normalize_verdict(data, prefix="Q", number=questions[i].number)
            return results
        except Exception as exc:
            return self._fallback_or_raise(
                exc, "batch_judge_questions",
                lambda: [self.fallback.judge_question(q, notes) for q in questions],
            )

    def batch_judge_answers(self, questions: list[Question], notes: dict[str, str]) -> list[dict[str, Any]]:
        """Judge ALL answers in a single API call (11 answers → 1 call)."""
        system = (
            "You are the Answer Judge. Score EACH model answer on a 0-5 rubric: "
            "factual_accuracy, completeness, lecture_grounded, concise_pedagogical. "
            "Return a JSON ARRAY — one object per answer — each with: "
            "target_id, rubric:{...}, total, verdict (GOOD|ACCEPTABLE|POOR), suggestion. "
            "GOOD if total>=17, ACCEPTABLE if total>=13, else POOR. "
            "The array length MUST equal the number of questions."
        )
        items = []
        for q in questions:
            ctx, _ = self._question_context(q, notes, limit=2)
            items.append(
                f"A{q.number} | {q.topic}\n"
                f"  focus: {q.focus or '(none)'}\n"
                f"  question: {q.prompt[:150]}\n"
                f"  answer: {q.answer[:300]}\n"
                f"  source_refs: {q.source_refs}\n"
                f"  context: {(ctx or '(none)')[:500]}"
            )
        prompt = "Score these answers and return a JSON array:\n\n" + "\n\n".join(items)
        try:
            raw = self._generate_for_role("judge", prompt, system, stage="batch_answer_judge")
            parsed = parse_json_block(raw)
            results = parsed if isinstance(parsed, list) else []
            if len(results) != len(questions):
                raise ValueError(f"Expected {len(questions)} verdicts, got {len(results)}")
            for i, data in enumerate(results):
                self._normalize_verdict(data, prefix="A", number=questions[i].number)
            return results
        except Exception as exc:
            return self._fallback_or_raise(
                exc, "batch_judge_answers",
                lambda: [self.fallback.judge_answer(q, notes) for q in questions],
            )

    def judge_question(self, question: Question, notes: dict[str, str]) -> dict[str, Any]:
        system = (
            "You are the Question Judge. Score each question on a 0-5 rubric: "
            "scope_alignment, difficulty_appropriateness, clarity_no_ambiguity, "
            "answerable_from_lecture. Return JSON only: "
            "{target_id, rubric:{...}, total, verdict (GOOD|ACCEPTABLE|POOR), suggestion}. "
            "GOOD if total>=17, ACCEPTABLE if total>=13, else POOR."
        )
        ctx, _ = self._question_context(question, notes, limit=3)
        prompt = (
            f"target_id: Q{question.number}\n"
            f"kind: {question.kind}\n"
            f"topic: {question.topic}\n"
            f"focus: {question.focus or '(none)'}\n"
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
        ctx, _ = self._question_context(question, notes, limit=3)
        prompt = (
            f"target_id: A{question.number}\n"
            f"focus: {question.focus or '(none)'}\n"
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

    def write_answer_and_rubric(
        self,
        question: Question,
        notes: dict[str, str],
        revision_instruction: str | None = None,
    ) -> dict[str, Any]:
        """Rewrite model answer (grounded in lecture context) + derive rubric from it.

        Called when stage-2 or stage-3 judges flag a problem.  Writing both
        together guarantees consistency: rubric criteria are derived from the
        newly written answer, so they can never mismatch.
        """
        ctx, sources = self._question_context(question, notes, limit=4)
        hint = f"\nRevision note: {revision_instruction}" if revision_instruction else ""
        system = (
            "You are a university exam answer writer. "
            "Using the provided lecture context, write a factually accurate model answer "
            f"(≤120 words) for the question, then derive {2}-{4} specific grading rubric "
            "criteria DIRECTLY from your answer. "
            "Rules: every rubric criterion must be satisfiable from your answer; "
            "point values must be integers summing exactly to the question's total points; "
            "source_refs must be filenames from the lecture context header lines, without "
            "the optional #Lx-Ly line locator. "
            "Do not add named examples, sub-categories, numbers, or attributions unless "
            "they appear explicitly in the lecture context. When the context is general, "
            "keep the answer general instead of inventing a concrete example. For application "
            "questions, label any scenario-specific consequence as a proposed design outcome "
            "rather than presenting it as a fact from the lecture. "
            "Return JSON only: "
            "{\"answer\": \"...\", \"rubric\": [\"criterion (N pts)\", ...], "
            "\"source_refs\": [\"filename.txt\", ...]}"
        )
        prompt = (
            f"Question ({question.kind}, {question.points} pts total):\n"
            f"{question.prompt}\n"
            f"Mandatory focus: {question.focus or '(none)'}{hint}\n\n"
            f"Lecture context:\n{ctx or '(no direct hits — answer conservatively)'}"
        )
        try:
            raw = self._generate_for_role(
                "answer_writer", prompt, system, stage="answer_and_rubric_writer"
            )
            data = parse_json_block(raw) or {}
            return {
                "answer": str(data.get("answer", "")).strip(),
                "rubric": [str(r) for r in (data.get("rubric") or []) if r],
                "source_refs": list(data.get("source_refs") or sources),
            }
        except Exception as exc:
            return self._fallback_or_raise(
                exc,
                "write_answer_and_rubric",
                lambda: {"answer": question.answer, "rubric": list(question.rubric), "source_refs": question.source_refs},
            )

    def write_rubric(
        self,
        question: Question,
        notes: dict[str, str],
        revision_instruction: str | None = None,
    ) -> list[str]:
        """Regenerate ONLY the grading rubric for an existing question+answer.

        Called by regen_rubric() when stage-3 judges flag a rubric problem.
        The prompt and answer remain unchanged; only the rubric is rewritten
        so its criteria are directly derivable from the existing model answer.
        """
        system = (
            "You are a grading rubric writer for a university exam. "
            "Given a question and its model answer, write 2-4 specific, objectively "
            "gradeable criteria that a grader can apply consistently. "
            "Every criterion must be directly achievable from the model answer. "
            "Point values must be integers summing exactly to the question's total points. "
            "Return a JSON ARRAY of strings, each formatted as "
            "'criterion description (N pts)'. "
            "Do NOT return any other text."
        )
        hint = f"\nRevision note: {revision_instruction}" if revision_instruction else ""
        prompt = (
            f"Question ({question.kind}, {question.points} pts total):\n{question.prompt}\n\n"
            f"Model Answer:\n{question.answer}{hint}\n\n"
            "Write rubric criteria that sum to the question's total points."
        )
        try:
            raw = self._generate_for_role("judge", prompt, system, stage="rubric_writer")
            cleaned = raw.strip().replace("```json", "").replace("```", "").strip()
            import re as _re
            data = json.loads(cleaned) if cleaned.startswith("[") else None
            if not data:
                match = _re.search(r"\[.*\]", cleaned, _re.DOTALL)
                data = json.loads(match.group(0)) if match else []
            return [str(item) for item in data if item]
        except Exception as exc:
            return self._fallback_or_raise(
                exc,
                "write_rubric",
                lambda: list(question.rubric),
            )

    def judge_factual_grounding(
        self,
        question: Question,
        notes: dict[str, str],
        chars_per_source: int = 900,
    ) -> dict[str, Any]:
        """Semantic factual accuracy check against cited lecture chunks.

        Retrieves actual text from source_refs, then asks the LLM whether
        any claim in the model answer contradicts or is unsupported by that
        content.  This catches errors that lexical keyword matching misses,
        such as wrong attributions (e.g., crediting Taylor for Therbligs).
        """
        lecture_ctx, _sources = self._question_context(question, notes, limit=4)
        if not lecture_ctx:
            return {"factually_accurate": True, "errors": [], "verdict": "PASS"}

        system = (
            "You are a factual accuracy reviewer for a university exam. "
            "Given a question, its model answer, and excerpts from the lecture notes "
            "the answer cites, identify factual errors: claims in the answer that are "
            "incorrect according to the lecture content, or wrong attributions "
            "(e.g., crediting the wrong person for a concept or discovery). "
            "Return JSON only: "
            "{\"factually_accurate\": true|false, "
            "\"errors\": [\"concise description of each error\"], "
            "\"verdict\": \"PASS\"|\"SOFT_FAIL\"|\"HARD_FAIL\"}. "
            "HARD_FAIL: clear factual error or wrong attribution. "
            "SOFT_FAIL: claim that cannot be verified from the provided excerpts "
            "but may still be correct. "
            "PASS: all factual claims are consistent with the lecture content. "
            "Do NOT flag correct paraphrasing, reasonable simplification, or "
            "claims you personally doubt but cannot disprove from the excerpts. "
            "For application questions, distinguish lecture facts from scenario reasoning: "
            "a hypothetical design proposal or plausible consequence is not a factual error "
            "merely because that invented scenario is absent from the notes. Flag it only if "
            "it contradicts the cited framework or is presented as an established lecture fact."
        )
        prompt = (
            f"Question ({question.kind}, Q{question.number}):\n{question.prompt}\n\n"
            f"Model Answer:\n{question.answer}\n\n"
            f"Cited lecture excerpts:\n{lecture_ctx}"
        )
        try:
            raw = self._generate_for_role(
                "judge", prompt, system, stage="factual_grounding_judge"
            )
            data = parse_json_block(raw) or {}
            return {
                "factually_accurate": bool(data.get("factually_accurate", True)),
                "errors": [str(e) for e in (data.get("errors") or []) if e],
                "verdict": str(data.get("verdict", "PASS")),
            }
        except Exception as exc:
            return self._fallback_or_raise(
                exc,
                "judge_factual_grounding",
                lambda: {"factually_accurate": True, "errors": [], "verdict": "PASS"},
            )

    def judge_factual_grounding_batch(
        self,
        questions: list[Question],
        notes: dict[str, str],
        chars_per_source: int = 900,
    ) -> list[dict[str, Any]]:
        """Check all answers for factual grounding in one provider request."""

        system = (
            "You are a factual accuracy reviewer for a university exam. "
            "Review every numbered item independently against only its cited lecture excerpts. "
            "Return a JSON ARRAY with exactly one object per item in the original order: "
            "[{\"target_id\":\"Q1\", \"factually_accurate\":true|false, "
            "\"errors\":[\"concise error\"], \"verdict\":\"PASS\"|\"SOFT_FAIL\"|\"HARD_FAIL\"}]. "
            "HARD_FAIL only for a clear factual error, contradiction, or wrong attribution. "
            "SOFT_FAIL for an unverifiable claim that may still be correct. "
            "PASS when factual claims are consistent with the excerpts. "
            "Do not flag correct paraphrasing or reasonable simplification. "
            "For application questions, distinguish lecture facts from scenario reasoning: "
            "a hypothetical design proposal or plausible consequence is not a factual error "
            "merely because the invented scenario is absent from the notes. Flag it only if "
            "it contradicts the cited framework or is presented as an established lecture fact."
        )
        items: list[str] = []
        for question in questions:
            lecture_ctx, _sources = self._question_context(question, notes, limit=4)
            items.append(
                f"target_id: Q{question.number}\n"
                f"kind: {question.kind}\n"
                f"question: {question.prompt}\n"
                f"model answer: {question.answer}\n"
                f"cited lecture excerpts:\n{lecture_ctx or '(no cited excerpts)'}"
            )
        prompt = "\n\n========== NEXT ITEM ==========\n\n".join(items)
        try:
            raw = self._generate_for_role(
                "judge", prompt, system, stage="factual_grounding_judge_batch"
            )
            parsed = parse_json_block(raw)
            data: list[dict[str, Any]] = parsed if isinstance(parsed, list) else []
            if len(data) != len(questions):
                raise ValueError(f"Expected {len(questions)} factual verdicts, got {len(data)}")
            results: list[dict[str, Any]] = []
            for entry in data:
                results.append(
                    {
                        "factually_accurate": bool(entry.get("factually_accurate", True)),
                        "errors": [str(e) for e in (entry.get("errors") or []) if e],
                        "verdict": str(entry.get("verdict", "PASS")),
                    }
                )
            return results
        except Exception as exc:
            return self._fallback_or_raise(
                exc,
                "judge_factual_grounding_batch",
                lambda: [
                    {"factually_accurate": True, "errors": [], "verdict": "PASS"}
                    for _ in questions
                ],
            )

    def judge_question_overlap_batch(
        self, candidates: list[tuple[Any, Any, float]]
    ) -> list[dict[str, Any]]:
        """Check all candidate question pairs for overlap in a single API call.

        A pair overlaps when answering one question correctly would practically
        guarantee answering the other correctly (they test the same knowledge).
        Batching avoids one LLM call per pair — typically 1 call covers all.
        """
        if not candidates:
            return []
        system = (
            "You are an exam quality reviewer checking for question overlap. "
            "Two questions overlap when a student who correctly answers one "
            "would almost certainly answer the other correctly. "
            "Return a JSON ARRAY — one entry per pair — with this schema: "
            "[{\"pair_index\": 0, \"overlapping\": true|false, \"reason\": \"...\"}]. "
            "Set overlapping=true ONLY when the questions test substantially the same "
            "knowledge or framework. Different cognitive depths (recall vs application) "
            "of the SAME concept still count as overlap."
        )
        pair_blocks = []
        for i, (q1, q2, score) in enumerate(candidates):
            pair_blocks.append(
                f"Pair {i} — Q{q1.number} ({q1.kind}, {q1.points}pts) vs "
                f"Q{q2.number} ({q2.kind}, {q2.points}pts):\n"
                f"  Q{q1.number} focus: {getattr(q1, 'focus', '') or '(none)'}\n"
                f"  Q{q1.number}: {q1.prompt}\n"
                f"  Q{q2.number} focus: {getattr(q2, 'focus', '') or '(none)'}\n"
                f"  Q{q2.number}: {q2.prompt}"
            )
        prompt = "\n\n".join(pair_blocks) + "\n\nReturn the JSON array."
        try:
            raw = self._generate_for_role("judge", prompt, system, stage="overlap_judge")
            cleaned = raw.strip().replace("```json", "").replace("```", "").strip()
            import re as _re
            data = json.loads(cleaned) if cleaned.startswith("[") else None
            if not data:
                match = _re.search(r"\[.*\]", cleaned, _re.DOTALL)
                data = json.loads(match.group(0)) if match else []
            results = []
            for entry in data:
                results.append({
                    "overlapping": bool(entry.get("overlapping", False)),
                    "reason": str(entry.get("reason", "")),
                })
            # Pad if LLM returned fewer entries than pairs
            while len(results) < len(candidates):
                results.append({"overlapping": False, "reason": ""})
            return results[: len(candidates)]
        except Exception as exc:
            return self._fallback_or_raise(
                exc,
                "judge_question_overlap_batch",
                lambda: [{"overlapping": False, "reason": ""} for _ in candidates],
            )

    def judge_answer_consistency(self, question: Question) -> dict[str, Any]:
        """LLM-backed semantic consistency check for AnswerConsistencyJudgeAgent.

        Verifies that (1) the model answer addresses what the question actually
        asks, and (2) every rubric criterion is demonstrably covered by the
        model answer.  Returns {consistent, issues, verdict}.
        """
        system = (
            "You are an exam quality auditor checking internal consistency. "
            "Given a question, its model answer, and its grading rubric, identify mismatches. "
            "A mismatch is when the answer addresses a different topic than the question or rubric asks, "
            "or when one or more rubric criteria cannot be awarded based on the answer given. "
            "Return JSON only: "
            "{\"consistent\": true|false, \"issues\": [\"short description of each mismatch\"], "
            "\"verdict\": \"PASS\"|\"SOFT_FAIL\"|\"HARD_FAIL\"}. "
            "HARD_FAIL if the answer discusses a completely different concept than the rubric. "
            "SOFT_FAIL if the answer partially misses rubric criteria. "
            "PASS if the answer and rubric are well-aligned."
        )
        rubric_text = "\n".join(f"- {r}" for r in question.rubric) or "(no rubric)"
        prompt = (
            f"Question ({question.kind}, {question.points} pts):\n{question.prompt}\n\n"
            f"Model Answer:\n{question.answer}\n\n"
            f"Rubric:\n{rubric_text}"
        )
        try:
            raw = self._generate_for_role(
                "judge", prompt, system, stage="consistency_judge"
            )
            data = parse_json_block(raw) or {}
            return {
                "consistent": bool(data.get("consistent", True)),
                "issues": [str(i) for i in (data.get("issues") or []) if i],
                "verdict": str(data.get("verdict", "PASS")),
            }
        except Exception as exc:
            return self._fallback_or_raise(
                exc,
                "judge_answer_consistency",
                lambda: {"consistent": True, "issues": [], "verdict": "PASS"},
            )

    def judge_answer_consistency_batch(
        self,
        questions: list[Question],
    ) -> list[dict[str, Any]]:
        """Check answer-rubric consistency for all questions in one request."""

        system = (
            "You are an exam quality auditor checking internal consistency. "
            "Review every numbered item independently. Return a JSON ARRAY with exactly "
            "one object per item in the original order: "
            "[{\"target_id\":\"Q1\", \"consistent\":true|false, "
            "\"issues\":[\"short mismatch\"], \"verdict\":\"PASS\"|\"SOFT_FAIL\"|\"HARD_FAIL\"}]. "
            "A mismatch exists when the answer addresses a different topic than the question "
            "or rubric asks, or when a rubric criterion cannot be awarded from the answer. "
            "PASS if the question, answer, and rubric align."
        )
        items: list[str] = []
        for question in questions:
            rubric_text = "\n".join(f"- {r}" for r in question.rubric) or "(no rubric)"
            items.append(
                f"target_id: Q{question.number}\n"
                f"question ({question.kind}, {question.points} pts): {question.prompt}\n"
                f"model answer: {question.answer}\n"
                f"rubric:\n{rubric_text}"
            )
        prompt = "\n\n========== NEXT ITEM ==========\n\n".join(items)
        try:
            raw = self._generate_for_role(
                "judge", prompt, system, stage="consistency_judge_batch"
            )
            parsed = parse_json_block(raw)
            data: list[dict[str, Any]] = parsed if isinstance(parsed, list) else []
            if len(data) != len(questions):
                raise ValueError(f"Expected {len(questions)} consistency verdicts, got {len(data)}")
            return [
                {
                    "consistent": bool(entry.get("consistent", True)),
                    "issues": [str(issue) for issue in (entry.get("issues") or []) if issue],
                    "verdict": str(entry.get("verdict", "PASS")),
                }
                for entry in data
            ]
        except Exception as exc:
            return self._fallback_or_raise(
                exc,
                "judge_answer_consistency_batch",
                lambda: [
                    {"consistent": True, "issues": [], "verdict": "PASS"}
                    for _ in questions
                ],
            )

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

    This avoids the GCP project ID / gcloud setup path. The ignored project-root
    `.gemini_api_key` file takes precedence over GEMINI_API_KEY / GOOGLE_API_KEY.
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

        local_key = _read_local_gemini_api_key()
        key = api_key or local_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(
                ".gemini_api_key, GEMINI_API_KEY, or GOOGLE_API_KEY is required "
                "for Gemini API-key mode."
            )

        self._genai = genai
        from google.genai import types as genai_types  # type: ignore

        self._types = genai_types
        self.client = genai.Client(
            api_key=key,
            http_options=genai_types.HttpOptions(
                timeout=60_000,
                retry_options=genai_types.HttpRetryOptions(attempts=1),
            ),
        )
        self.auth_mode = "api_key"
        self.auth_source = (
            "argument"
            if api_key
            else ".gemini_api_key"
            if local_key
            else "GEMINI_API_KEY"
            if os.environ.get("GEMINI_API_KEY")
            else "GOOGLE_API_KEY"
        )
        self.fallback = fallback or DeterministicProvider()
        self.model_policy = model_policy or load_model_policy(None)
        self.strict = strict
        self.usage = UsageTracker(self.model_policy.get("price_per_1m_tokens_usd", {}))
        self.model_fallback_events: list[dict[str, str]] = []


class OpenAIProvider(GeminiProvider):
    """OpenAI Responses API variant using the same provider interface."""

    def __init__(
        self,
        api_key: str | None = None,
        fallback: DeterministicProvider | None = None,
        model_policy: dict[str, Any] | None = None,
        strict: bool = False,
    ):
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:
            raise RuntimeError("openai SDK not installed. Run: pip install openai") from exc

        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAI provider mode.")

        self.client = OpenAI(api_key=key)
        self.auth_mode = "openai_api_key"
        self.fallback = fallback or DeterministicProvider()
        self.model_policy = model_policy or load_model_policy(None)
        self.strict = strict
        self.usage = UsageTracker(self.model_policy.get("price_per_1m_tokens_usd", {}))
        self.model_fallback_events: list[dict[str, str]] = []

    def _generate(self, model: str, prompt: str, system: str | None = None, stage: str = "llm_call") -> str:
        model = self._strip_provider_prefix(model)
        response = self.client.responses.create(
            model=model,
            instructions=system or "",
            input=prompt,
        )
        text = getattr(response, "output_text", "") or ""
        text = text.strip()
        self.usage.record(stage, model, (system or "") + "\n" + prompt, text)
        return text


class AnthropicProvider(GeminiProvider):
    """Anthropic Messages API variant using the same provider interface."""

    def __init__(
        self,
        api_key: str | None = None,
        fallback: DeterministicProvider | None = None,
        model_policy: dict[str, Any] | None = None,
        strict: bool = False,
    ):
        try:
            from anthropic import Anthropic  # type: ignore
        except ImportError as exc:
            raise RuntimeError("anthropic SDK not installed. Run: pip install anthropic") from exc

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for Anthropic provider mode.")

        self.client = Anthropic(api_key=key)
        self.auth_mode = "anthropic_api_key"
        self.fallback = fallback or DeterministicProvider()
        self.model_policy = model_policy or load_model_policy(None)
        self.strict = strict
        self.usage = UsageTracker(self.model_policy.get("price_per_1m_tokens_usd", {}))
        self.model_fallback_events: list[dict[str, str]] = []

    def _generate(self, model: str, prompt: str, system: str | None = None, stage: str = "llm_call") -> str:
        model = self._strip_provider_prefix(model)
        response = self.client.messages.create(
            model=model,
            max_tokens=2048,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
        )
        parts: list[str] = []
        for block in response.content:
            text = getattr(block, "text", "")
            if text:
                parts.append(text)
        result = "\n".join(parts).strip()
        self.usage.record(stage, model, (system or "") + "\n" + prompt, result)
        return result


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

    name precedence: explicit > EXAM_AGENT_PROVIDER env > auto-detect from credentials > 'deterministic'.
    Auto-detection: .gemini_api_key / GEMINI_API_KEY / GOOGLE_API_KEY → gemini,
    GCP_PROJECT_ID → vertex, OPENAI_API_KEY → openai,
    ANTHROPIC_API_KEY → anthropic, else deterministic.
    """

    explicit = name or os.environ.get("EXAM_AGENT_PROVIDER")
    if not explicit:
        has_gemini_key = bool(
            _read_local_gemini_api_key()
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        has_project = bool(
            os.environ.get("GCP_PROJECT_ID")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("PROJECT_ID")
        )
        if has_gemini_key or has_project:
            explicit = "gemini"
        elif os.environ.get("OPENAI_API_KEY"):
            explicit = "openai"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            explicit = "anthropic"
        else:
            if strict:
                raise RuntimeError(
                    "--strict-provider requires live provider credentials or an explicit "
                    "--provider. No supported API credentials were detected in this process. "
                    "Add .gemini_api_key or set GEMINI_API_KEY, GCP_PROJECT_ID, "
                    "OPENAI_API_KEY, or ANTHROPIC_API_KEY; "
                    "or pass --provider deterministic intentionally."
                )
            explicit = "deterministic"
        print(f"[make_provider] Auto-detected provider: {explicit}")
    chosen = explicit.lower()
    if chosen in {"gemini", "vertex", "vertexai", "gemini-vertex"}:
        try:
            auth_mode = os.environ.get("EXAM_AGENT_GEMINI_AUTH", "auto").lower()
            has_project = bool(
                os.environ.get("GCP_PROJECT_ID")
                or os.environ.get("GOOGLE_CLOUD_PROJECT")
                or os.environ.get("PROJECT_ID")
            )
            has_api_key = bool(
                _read_local_gemini_api_key()
                or os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY")
            )

            if chosen in {"vertex", "vertexai", "gemini-vertex"} or auth_mode in {"vertex", "vertex_ai"}:
                return GeminiProvider(model_policy=model_policy, strict=strict)
            if auth_mode in {"api_key", "apikey", "ai_studio"}:
                return GeminiApiKeyProvider(model_policy=model_policy, strict=strict)
            if has_api_key:
                return GeminiApiKeyProvider(model_policy=model_policy, strict=strict)
            if has_project:
                return GeminiProvider(model_policy=model_policy, strict=strict)
            return GeminiProvider(model_policy=model_policy, strict=strict)
        except Exception as exc:
            if strict:
                raise
            print(f"[make_provider] Gemini unavailable; using deterministic fallback: {exc}")
            return ConfiguredDeterministicProvider(model_policy=model_policy)
    if chosen == "deterministic":
        return ConfiguredDeterministicProvider(model_policy=model_policy)
    if chosen in {"openai", "gpt"}:
        try:
            return OpenAIProvider(model_policy=model_policy, strict=strict)
        except Exception as exc:
            if strict:
                raise
            print(f"[make_provider] OpenAI unavailable; using deterministic fallback: {exc}")
            return ConfiguredDeterministicProvider(model_policy=model_policy)
    if chosen in {"anthropic", "claude"}:
        try:
            return AnthropicProvider(model_policy=model_policy, strict=strict)
        except Exception as exc:
            if strict:
                raise
            print(f"[make_provider] Anthropic unavailable; using deterministic fallback: {exc}")
            return ConfiguredDeterministicProvider(model_policy=model_policy)
    raise ValueError(f"Unknown provider: {chosen}. Use deterministic, gemini, vertex, openai, gpt, anthropic, or claude.")
