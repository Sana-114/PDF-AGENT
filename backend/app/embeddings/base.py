from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class SparseEmbedding:
    indices: list[int]
    values: list[float]


class EmbeddingProvider(Protocol):
    name: str
    model: str
    dimensions: int
    index_namespace: str

    def embed_dense(self, texts: list[str]) -> list[list[float]]: ...

    def embed_sparse(self, text: str) -> SparseEmbedding: ...
