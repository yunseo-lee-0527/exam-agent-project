from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from providers import _read_local_gemini_api_key  # noqa: E402


class ProviderConfigTests(unittest.TestCase):
    def test_local_gemini_key_file_is_trimmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / ".gemini_api_key"
            key_path.write_text("  local-test-key\n", encoding="utf-8")

            self.assertEqual(_read_local_gemini_api_key(key_path), "local-test-key")

    def test_missing_local_gemini_key_file_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / ".gemini_api_key"

            self.assertIsNone(_read_local_gemini_api_key(key_path))


if __name__ == "__main__":
    unittest.main()
