"""A small dependency-free BM25 retriever over knowledge chunks."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .knowledge import Chunk

STOPWORDS = frozenset(
    "a an and are as at be but by for from has have in is it its of on or so that the their then these they this to was with your you can do not".split()
)


def stem(token: str) -> str:
    """Very small suffix stemmer: puppies->puppy, walks->walk (keeps 'ss' words intact)."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> List[str]:
    """Lower-case, split on non-alphanumerics, drop stopwords, stem."""
    return [stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]


class BM25Index:
    """BM25 ranking (k1=1.5, b=0.75). Headings count double so titles matter."""

    def __init__(self, chunks: Sequence[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        """Index the given chunks."""
        self.k1, self.b = k1, b
        self.chunks = list(chunks)
        self._tf: Dict[str, Counter] = {}
        self._len: Dict[str, int] = {}
        for c in self.chunks:
            tokens = tokenize(c.heading) * 2 + tokenize(c.text)
            self._tf[c.chunk_id] = Counter(tokens)
            self._len[c.chunk_id] = len(tokens)
        self._avg = sum(self._len.values()) / max(len(self._len), 1)
        df: Counter = Counter()
        for tf in self._tf.values():
            df.update(tf.keys())
        n = len(self.chunks)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def score(self, query_tokens: Iterable[str], chunk: Chunk) -> float:
        """BM25 score of one chunk for already-tokenized query terms."""
        tf = self._tf[chunk.chunk_id]
        length = self._len[chunk.chunk_id]
        total = 0.0
        for term in query_tokens:
            if term not in tf:
                continue
            f = tf[term]
            denom = f + self.k1 * (1 - self.b + self.b * length / (self._avg or 1))
            total += self._idf.get(term, 0.0) * f * (self.k1 + 1) / denom
        return total

    def search(self, query: str, candidates: Optional[Sequence[Chunk]] = None, top_k: int = 3) -> List[Tuple[Chunk, float]]:
        """Return the top_k (chunk, score) pairs with a positive score, best first (ties by chunk id)."""
        tokens = tokenize(query)
        pool = self.chunks if candidates is None else candidates
        scored = [(c, self.score(tokens, c)) for c in pool]
        scored = [(c, s) for c, s in scored if s > 0]
        scored.sort(key=lambda cs: (-cs[1], cs[0].chunk_id))
        return scored[:top_k]
