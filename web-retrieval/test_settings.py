"""Tests for validated settings loading and last-known-good live reload."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from settings import SettingsManager, load_settings


class SettingsTests(unittest.TestCase):
    def test_partial_file_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.toml"
            path.write_text("[ranking]\ndefault_max_passages = 5\n", encoding="utf-8")

            settings = load_settings(path)

            self.assertEqual(settings.ranking.default_max_passages, 5)
            self.assertEqual(settings.search.num_results, 8)
            self.assertEqual(settings.fetch.max_document_chars, 40_000)

    def test_rejects_invalid_relationships(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.toml"
            path.write_text("[chunk]\nwords = 100\noverlap = 100\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "chunk.overlap"):
                load_settings(path)

    def test_reload_keeps_last_good_then_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.toml"
            path.write_text(
                "[ranking]\ndefault_max_passages = 5\nmax_passages = 12\n",
                encoding="utf-8",
            )
            manager = SettingsManager(path)

            path.write_text(
                "[ranking]\ndefault_max_passages = 7\nmax_passages = 12\n",
                encoding="utf-8",
            )
            self.assertEqual(manager.get().ranking.default_max_passages, 7)

            path.write_text("[ranking\ninvalid", encoding="utf-8")
            self.assertEqual(manager.get().ranking.default_max_passages, 7)

            path.write_text(
                "[ranking]\ndefault_max_passages = 9\nmax_passages = 12\n",
                encoding="utf-8",
            )
            self.assertEqual(manager.get().ranking.default_max_passages, 9)


if __name__ == "__main__":
    unittest.main()