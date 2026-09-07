import hashlib
import math
from collections import Counter

from app.embeddings.base import SparseEmbedding
from app.services.text_features import tokenize


class HashEmbeddingProvider:
    """Deterministic zero-key baseline for exercising the full vector pipeline."""

    name = "hash"
    model = "hash-ngram-v1"

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions < 32:
            raise ValueError("向量维度不能小于 32。")
        self.dimensions = dimensions

    def embed_dense(self, texts: list[str]) -> list[list[float]]:
        return [self._dense_vector(text) for text in texts]

    def embed_sparse(self, text: str) -> SparseEmbedding:
        frequencies: Counter[int] = Counter(
            self._hash_index(token, 2**32 - 1) for token in tokenize(text)
        )
        indices = sorted(frequencies)
        return SparseEmbedding(
            indices=indices,
            values=[round(1.0 + math.log(frequencies[index]), 6) for index in indices],
        )

    def _dense_vector(self, text: str) -> list[float]:
        tokens = tokenize(text)
        features = [
            *tokens,
            *(f"{left}::{right}" for left, right in zip(tokens, tokens[1:], strict=False)),
        ]
        vector = [0.0] * self.dimensions
        for feature, frequency in Counter(features).items():
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(frequency))
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector

    @staticmethod
    def _hash_index(value: str, modulo: int) -> int:
        digest = hashlib.blake2b(value.encode("utf-8"), digest_size=4).digest()
        return int.from_bytes(digest, "big") % modulo
