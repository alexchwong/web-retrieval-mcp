"""Tests for validated settings loading and last-known-good live reload."""
from __future__ import annotations

import os
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

            def write_new_revision(content: str) -> None:
                # The runtime deliberately detects edits by (mtime_ns, size).
                # Advance the timestamp explicitly so fast CI file writes are
                # observable on filesystems with coarse or cached timestamps.
                previous_mtime = path.stat().st_mtime_ns
                path.write_text(content, encoding="utf-8")
                current = path.stat()
                os.utime(
                    path,
                    ns=(current.st_atime_ns, max(previous_mtime, current.st_mtime_ns) + 1_000_000_000),
                )

            write_new_revision(
                "[ranking]\ndefault_max_passages = 7\nmax_passages = 12\n"
            )
            self.assertEqual(manager.get().ranking.default_max_passages, 7)

            write_new_revision("[ranking\ninvalid")
            self.assertEqual(manager.get().ranking.default_max_passages, 7)

            write_new_revision(
                "[ranking]\ndefault_max_passages = 9\nmax_passages = 12\n"
            )
            self.assertEqual(manager.get().ranking.default_max_passages, 9)


if __name__ == "__main__":
    unittest.main()
