from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
from pathlib import Path


def check(name: str, passed: bool, detail: str) -> dict[str, object]:
    return {"name": name, "passed": passed, "detail": detail}


def has_module(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Check local setup for the exam-agent project.")
    parser.add_argument("--project-root", default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    results: list[dict[str, object]] = []

    required_files = [
        "src/main.py",
        "src/providers.py",
        "requirements.json",
        "model_policy.json",
        "lecture_notes/processed",
    ]
    for rel in required_files:
        path = root / rel
        results.append(check(rel, path.exists(), str(path)))

    blueprint_path = root / "exam_blueprint.json"
    results.append(
        check(
            "exam_blueprint.json (optional)",
            True,
            (
                f"{blueprint_path} found; Task 2 will use reproducible blueprint mode."
                if blueprint_path.exists()
                else "Not present; Task 2 will use the specialist writer path."
            ),
        )
    )

    results.append(
        check(
            "google-genai package",
            has_module("google.genai"),
            "Install with: python -m pip install google-genai",
        )
    )

    gemini_api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    project_id = (
        os.environ.get("GCP_PROJECT_ID")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("PROJECT_ID")
    )
    has_gemini_auth = bool(gemini_api_key or project_id)
    results.append(
        check(
            "Gemini authentication",
            has_gemini_auth,
            (
                f"GEMINI_API_KEY found (API-key mode)." if gemini_api_key
                else f"GCP project ID found (Vertex AI mode): {project_id}" if project_id
                else "Set GEMINI_API_KEY for AI Studio mode, or GCP_PROJECT_ID for Vertex AI mode."
            ),
        )
    )

    if not gemini_api_key:
        gcloud_path = shutil.which("gcloud")
        results.append(
            check(
                "Google Cloud CLI (Vertex AI mode only)",
                gcloud_path is not None,
                gcloud_path or "Not required if using GEMINI_API_KEY. Otherwise install from https://cloud.google.com/sdk/docs/install",
            )
        )

        adc_path = Path.home() / "AppData" / "Roaming" / "gcloud" / "application_default_credentials.json"
        results.append(
            check(
                "Application Default Credentials (Vertex AI mode only)",
                adc_path.exists(),
                str(adc_path) if adc_path.exists() else "Not required if using GEMINI_API_KEY. Otherwise run: gcloud auth application-default login",
            )
        )

    outputs = {"project_root": str(root), "checks": results}
    print(json.dumps(outputs, indent=2, ensure_ascii=False))

    failed = [item for item in results if not item["passed"]]
    if failed:
        print("\nSetup needs attention:")
        for item in failed:
            print(f"- {item['name']}: {item['detail']}")
        raise SystemExit(1)

    print("\nAll setup checks passed.")


if __name__ == "__main__":
    main()
