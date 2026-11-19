"""Settings validation, from_mapping, and the server's last-known-good live reload."""
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

from webspell import Settings, load_settings


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

    def test_from_mapping_matches_load_settings(self) -> None:
        text = ('[search]\nsearxng_url = "http://localhost:8888/"\n[fetch]\nmax_pdf_pages = 3\n'
                '[output]\nmax_result_bytes = 8192\nascii_json = true\n')
        mapping = {"search": {"searxng_url": "http://localhost:8888/"}, "fetch": {"max_pdf_pages": 3},
                   "output": {"max_result_bytes": 8192, "ascii_json": True}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.toml"
            path.write_text(text, encoding="utf-8")
            self.assertEqual(load_settings(path), Settings.from_mapping(mapping))
        settings = Settings.from_mapping(mapping)
        self.assertEqual(settings.search.searxng_url, "http://localhost:8888")
        for bad in ({"output": {"max_result_bytes": 4095}}, {"output": {"max_result_bytes": "8192"}},
                    {"output": {"max_result_bytes": True}}, {"output": {"ascii_json": 1}},
                    {"search": {"searxng_url": "ftp://x"}}, {"search": []}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Settings.from_mapping(bad)

    def test_no_default_search_url_and_new_user_agent(self) -> None:
        settings = Settings.from_mapping({})
        self.assertEqual(settings.search.searxng_url, "")
        self.assertEqual(settings.fetch.user_agent, "webSpell/0.2")
        self.assertIsNone(settings.output.max_result_bytes)
        self.assertFalse(settings.output.ascii_json)

    @unittest.skipUnless(importlib.util.find_spec("fastmcp"), "needs the server extra")
    def test_reload_keeps_last_good_then_recovers(self) -> None:
        from webspell.server import SettingsManager

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

    @unittest.skipUnless(importlib.util.find_spec("fastmcp"), "needs the server extra")
    def test_manager_without_file_uses_defaults(self) -> None:
        from webspell.server import SettingsManager

        self.assertEqual(SettingsManager(None).get(), Settings())


if __name__ == "__main__":
    unittest.main()
