"""Chunking and a built-in BM25 that reproduces ``rank_bm25.BM25Okapi`` exactly.

The scorer performs the same double-precision operations in the same order as
rank-bm25 0.2.2, so scores are bit-identical (checked against rank-bm25 by the
parity test). No NumPy.
"""
from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:  # pragma: no cover
    from .extract import Document
    from .search import Source
    from .settings import Settings

K1 = 1.5
B = 0.75
EPSILON = 0.25
_TOKEN = re.compile(r"\w+")


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def bm25_scores(corpus: Sequence[Sequence[str]], query: Sequence[str],
                k1: float = K1, b: float = B, epsilon: float = EPSILON) -> list[float]:
    """Okapi BM25 scores of ``query`` against each tokenised passage of ``corpus``."""
    doc_freqs: list[dict[str, int]] = []
    doc_len: list[int] = []
    nd: dict[str, int] = {}
    num_doc = 0
    for document in corpus:
        doc_len.append(len(document))
        num_doc += len(document)
        frequencies: dict[str, int] = {}
        for word in document:
            frequencies[word] = frequencies.get(word, 0) + 1
        doc_freqs.append(frequencies)
        for word in frequencies:
            nd[word] = nd.get(word, 0) + 1
    corpus_size = len(doc_freqs)
    avgdl = num_doc / corpus_size

    idf: dict[str, float] = {}
    idf_sum = 0
    negative: list[str] = []
    for word, freq in nd.items():
        value = math.log(corpus_size - freq + 0.5) - math.log(freq + 0.5)
        idf[word] = value
        idf_sum += value
        if value < 0:
            negative.append(word)
    average_idf = idf_sum / len(idf)
    eps = epsilon * average_idf
    for word in negative:
        idf[word] = eps

    scores = [0.0] * corpus_size
    norms = [k1 * (1 - b + b * length / avgdl) for length in doc_len]
    for term in query:
        weight = idf.get(term) or 0
        for index, frequencies in enumerate(doc_freqs):
            frequency = frequencies.get(term) or 0
            scores[index] += weight * (frequency * (k1 + 1) / (frequency + norms[index]))
    return scores


def rank(query: str, sources: Sequence["Source"], docs: Sequence["Document"],
         settings: "Settings", limit: int) -> list[dict[str, Any]]:
    """Chunk fetched documents, score them and select passages (0.1.1 rules)."""
    candidates = []
    keys = []
    stride = settings.chunk.words - settings.chunk.overlap
    for index, (source, doc) in enumerate(zip(sources, docs), 1):
        for page, text in doc.units:
            words = text.split()
            for start in range(0, len(words), stride):
                candidates.append({
                    "source_id": f"s{index}", "title": source.fields["title"], "url": source.url.visible,
                    "text": " ".join(words[start:start + settings.chunk.words]), "origin": doc.origin,
                    "truncated": doc.truncated, **({"page": page} if page is not None else {}),
                })
                keys.append((doc.final or source.url).key)
    if not candidates:
        return []
    corpus = [tokens(candidate["text"]) for candidate in candidates]
    scores = (bm25_scores(corpus, tokens(query)) if any(corpus) else [0.0] * len(corpus))
    selected = []
    used: dict[str, int] = {}
    seen = set()
    for index in sorted(range(len(scores)), key=lambda i: -scores[i]):
        candidate, key = candidates[index], keys[index]
        if used.get(key, 0) >= settings.ranking.max_passages_per_source or candidate["text"] in seen:
            continue
        selected.append({**candidate, "score": round(float(scores[index]), 3)})
        used[key] = used.get(key, 0) + 1
        seen.add(candidate["text"])
        if len(selected) >= limit:
            break
    return selected
