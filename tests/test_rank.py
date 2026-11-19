"""Built-in BM25 parity with rank-bm25 0.2.2 (Section 15, test 3)."""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import random
import unittest
from unittest.mock import patch

from webspell import rank, search
from webspell.extract import Document
from webspell.settings import DEFAULT_SETTINGS

HAVE_REFERENCE = importlib.util.find_spec("rank_bm25") is not None
VOCABULARY = [f"w{i}" for i in range(40)] + ["the", "a", "of", "evidence", "数据", "é"]


def reference_scores(corpus, query):
    from rank_bm25 import BM25Okapi

    return [float(score) for score in BM25Okapi(corpus).get_scores(query)]


@unittest.skipUnless(HAVE_REFERENCE, "needs rank-bm25 from the dev extra")
class ParityTests(unittest.TestCase):
    def assertParity(self, corpus, query):
        built_in = rank.bm25_scores(corpus, query)
        expected = reference_scores(corpus, query)
        # Bit-identical, not approximately equal.
        self.assertEqual([score.hex() for score in built_in], [score.hex() for score in expected])

    def test_seeded_random_corpora(self):
        generator = random.Random(20261116)
        for case in range(3000):
            size = generator.randint(1, 80)
            corpus = []
            for _ in range(size):
                length = generator.choice([0, generator.randint(1, 5), generator.randint(5, 120)])
                corpus.append([generator.choice(VOCABULARY[:generator.randint(2, len(VOCABULARY))])
                               for _ in range(length)])
            if not any(corpus):
                corpus[0] = ["w0"]
            query = [generator.choice(VOCABULARY + ["absent", "missing"])
                     for _ in range(generator.randint(0, 12))]
            with self.subTest(case=case):
                self.assertParity(corpus, query)

    def test_named_edge_cases(self):
        cases = {
            "stopword-only query": ([["the", "a", "evidence"], ["of", "the"]], ["the", "of"]),
            "repeated terms": ([["x", "y"], ["x", "x", "z"]], ["x", "x", "x", "y"]),
            "empty passages": ([[], ["x"], [], ["x", "y"]], ["x"]),
            "tied scores": ([["x", "y"], ["x", "y"], ["x", "y"]], ["x"]),
            "one-passage corpus": ([["x", "y", "z"]], ["x", "q"]),
            "absent terms": ([["x"], ["y"]], ["absent", "missing"]),
            "negative idf": ([["x"], ["x"], ["x", "y"]], ["x", "y"]),
        }
        for name, (corpus, query) in cases.items():
            with self.subTest(case=name):
                self.assertParity(corpus, query)

    def test_selected_passages_identical(self):
        """The full chunk-score-select path picks the same passages with either scorer."""
        generator = random.Random(7)
        one_word = replace(DEFAULT_SETTINGS, chunk=replace(DEFAULT_SETTINGS.chunk, words=4, overlap=1))
        for case in range(200):
            count = generator.randint(1, 8)
            sources = search.supplied([f"https://example.org/{i}" for i in range(count)], 8)
            docs = [Document("fetched", origin="fetched_page", units=((None, " ".join(
                generator.choice(VOCABULARY[:12]) for _ in range(generator.randint(0, 40)))),))
                for _ in range(count)]
            query = " ".join(generator.choice(VOCABULARY[:14]) for _ in range(generator.randint(1, 4)))
            limit = generator.randint(1, 20)
            ours = rank.rank(query, sources, docs, one_word, limit)
            with patch.object(rank, "bm25_scores", reference_scores):
                theirs = rank.rank(query, sources, docs, one_word, limit)
            with self.subTest(case=case):
                self.assertEqual(ours, theirs)


if __name__ == "__main__":
    unittest.main()
