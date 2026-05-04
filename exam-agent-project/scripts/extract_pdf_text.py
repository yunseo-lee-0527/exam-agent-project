from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader


def extract_pdf_text(raw_dir: Path, processed_dir: Path) -> None:
    processed_dir.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(raw_dir.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"No PDF files found in {raw_dir}")

    for pdf in pdfs:
        reader = PdfReader(str(pdf))
        pages: list[str] = []
        for page_number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            pages.append(f"\n\n--- Page {page_number} ---\n{text}")

        target = processed_dir / f"{pdf.stem}.txt"
        target.write_text("".join(pages), encoding="utf-8")
        print(f"{pdf.name}: {len(reader.pages)} pages -> {target.name}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    extract_pdf_text(root / "lecture_notes" / "raw", root / "lecture_notes" / "processed")


if __name__ == "__main__":
    main()
