from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
from pathlib import Path


def check(name: str, passed: bool, detail: str) -> dict[str, object]:
    return {"name": name, "passed": passed, "detail": detail}


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
        "exam_blueprint.json",
        "model_policy.json",
        "lecture_notes/processed",
    ]
    for rel in required_files:
        path = root / rel
        results.append(check(rel, path.exists(), str(path)))

    results.append(
        check(
            "google-genai package",
            importlib.util.find_spec("google.genai") is not None,
            "Install with: python -m pip install google-genai",
        )
    )

    gcloud_path = shutil.which("gcloud")
    results.append(
        check(
            "Google Cloud CLI",
            gcloud_path is not None,
            gcloud_path or "Install from https://cloud.google.com/sdk/docs/install",
        )
    )

    project_id = (
        os.environ.get("GCP_PROJECT_ID")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("PROJECT_ID")
    )
    results.append(
        check(
            "Vertex AI project ID",
            bool(project_id),
            "Found a project ID environment variable." if project_id else "Set with cmd: set GCP_PROJECT_ID=your-project-id",
        )
    )

    location = (
        os.environ.get("GCP_LOCATION")
        or os.environ.get("GOOGLE_CLOUD_LOCATION")
        or os.environ.get("LOCATION")
        or "us-central1"
    )
    results.append(check("Vertex AI location", bool(location), f"Using {location}"))

    adc_path = Path.home() / "AppData" / "Roaming" / "gcloud" / "application_default_credentials.json"
    results.append(
        check(
            "Application Default Credentials",
            adc_path.exists(),
            str(adc_path) if adc_path.exists() else "Run: gcloud auth application-default login",
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
