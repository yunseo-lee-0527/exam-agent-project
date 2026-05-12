from __future__ import annotations

from pathlib import Path

from ingest_materials import ingest_materials


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    ingest_materials(
        root / "lecture_notes" / "raw",
        root / "lecture_notes" / "processed",
        root / "outputs",
    )


if __name__ == "__main__":
    main()
