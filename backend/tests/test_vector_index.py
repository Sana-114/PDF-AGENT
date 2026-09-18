import math

from qdrant_client import QdrantClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base
from app.embeddings.hashing import HashEmbeddingProvider
from app.models.chunk import DocumentChunk  # noqa: F401
from app.models.document import Document, DocumentStatus
from app.schemas.agent import EvidenceAnchor
from app.services.chunking import replace_document_chunks
from app.services.retrieval import HybridRetriever, _reciprocal_rank_fusion
from app.services.vector_index import QdrantVectorIndex


def _parsed_document() -> dict:
    return {
        "pages": [
            {
                "page_number": 1,
                "blocks": [
                    {
                        "block_id": "p1-b1",
                        "type": "heading",
                        "text": "Training configuration",
                        "bbox": [10, 10, 300, 30],
                    },
                    {
                        "block_id": "p1-b2",
                        "type": "text",
                        "text": "The Adam optimizer uses learning rate 0.001.",
                        "bbox": [10, 40, 400, 80],
                    },
                ],
            },
            {
                "page_number": 2,
                "blocks": [
                    {
                        "block_id": "p2-b1",
                        "type": "heading",
                        "text": "Dataset",
                        "bbox": [10, 10, 300, 30],
                    },
                    {
                        "block_id": "p2-b2",
                        "type": "text",
                        "text": "The corpus contains fifty thousand translation pairs.",
                        "bbox": [10, 40, 400, 80],
                    },
                ],
            },
        ]
    }


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        math.sqrt(sum(value * value for value in left))
        * math.sqrt(sum(value * value for value in right))
    )


def test_hash_embeddings_are_deterministic_and_similarity_sensitive() -> None:
    provider = HashEmbeddingProvider(dimensions=64)
    query, related, unrelated = provider.embed_dense(
        [
            "Adam optimizer learning rate",
            "learning rate for the Adam optimizer",
            "protein folding experiment",
        ]
    )

    assert len(query) == 64
    assert query == provider.embed_dense(["Adam optimizer learning rate"])[0]
    assert _cosine(query, related) > _cosine(query, unrelated)
    sparse = provider.embed_sparse("Adam Adam optimizer")
    assert sparse.indices == sorted(sparse.indices)
    assert len(sparse.indices) == len(sparse.values)


def test_qdrant_indexes_and_queries_named_dense_sparse_vectors() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    config = Settings(
        vector_search_enabled=True,
        vector_collection="test_chunks",
        embedding_dimensions=64,
        vector_prefetch_k=6,
    )
    vector_index = QdrantVectorIndex(
        config,
        client=QdrantClient(location=":memory:"),
        embedder=HashEmbeddingProvider(dimensions=64),
    )
    with Session(engine) as session:
        document = Document(
            original_filename="vector.pdf",
            storage_key="vector.pdf",
            size_bytes=100,
            sha256="d" * 64,
            status=DocumentStatus.READY,
            title="Vector Paper",
        )
        session.add(document)
        session.flush()
        replace_document_chunks(session, document.id, _parsed_document())
        session.commit()

        assert vector_index.index_document(session, document.id) == 2
        results = vector_index.search(
            session,
            "What learning rate was used by Adam?",
            [document.id],
            limit=2,
        )

        assert results[0].page_number == 1
        assert results[0].chunk_id
        assert results[0].retrieval_mode == "vector"
        assert "0.001" in results[0].quote

        vector_index.delete_document(document.id)
        assert vector_index._document_point_count(document.id) == 0


def test_vector_index_respects_configured_embedding_batch_size() -> None:
    class RecordingEmbedder(HashEmbeddingProvider):
        def __init__(self) -> None:
            super().__init__(dimensions=64)
            self.batch_sizes: list[int] = []

        def embed_dense(self, texts: list[str]) -> list[list[float]]:
            self.batch_sizes.append(len(texts))
            return super().embed_dense(texts)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    config = Settings(
        vector_search_enabled=True,
        vector_collection="test_embedding_batches",
        embedding_dimensions=64,
        embedding_batch_size=1,
    )
    embedder = RecordingEmbedder()
    vector_index = QdrantVectorIndex(
        config,
        client=QdrantClient(location=":memory:"),
        embedder=embedder,
    )
    with Session(engine) as session:
        document = Document(
            original_filename="batches.pdf",
            storage_key="batches.pdf",
            size_bytes=100,
            sha256="f" * 64,
            status=DocumentStatus.READY,
            title="Batch Paper",
        )
        session.add(document)
        session.flush()
        replace_document_chunks(session, document.id, _parsed_document())
        session.commit()

        assert vector_index.index_document(session, document.id) == 2

    assert embedder.batch_sizes == [1, 1]


def test_hybrid_retriever_falls_back_when_vector_store_fails() -> None:
    class FailingVectorIndex:
        enabled = True

        def search(self, session, question, document_ids, limit):
            del session, question, document_ids, limit
            raise ConnectionError("offline")

    class DisabledReranker:
        enabled = False
        candidate_k = 12

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        document = Document(
            original_filename="fallback.pdf",
            storage_key="fallback.pdf",
            size_bytes=100,
            sha256="e" * 64,
            status=DocumentStatus.READY,
            title="Fallback Paper",
        )
        session.add(document)
        session.flush()
        replace_document_chunks(session, document.id, _parsed_document())
        session.commit()

        results = HybridRetriever(
            session,
            vector_index=FailingVectorIndex(),
            reranker=DisabledReranker(),  # type: ignore[arg-type]
        ).search(
            "Adam learning rate", [document.id], top_k=2
        )

    assert results[0].retrieval_mode == "lexical"
    assert "0.001" in results[0].quote


def test_application_rrf_preserves_strong_exact_match() -> None:
    def evidence(chunk_id: str, quote: str, score: float, mode: str) -> EvidenceAnchor:
        return EvidenceAnchor(
            evidence_id="candidate",
            chunk_id=chunk_id,
            document_id="doc-1",
            page_number=1,
            quote=quote,
            score=score,
            retrieval_mode=mode,
        )

    target = evidence("target", "UAV-Human exact reference", 0.55, "lexical")
    distractor = evidence("distractor", "generic reference", 0.43, "lexical")
    lexical = [target, evidence("other", "other", 0.48, "lexical"), distractor]
    vector = [
        evidence("vector-only", "vector", 0.56, "vector"),
        evidence("distractor", "generic reference", 0.5, "vector"),
        evidence("v3", "v3", 0.4, "vector"),
        evidence("v4", "v4", 0.35, "vector"),
        evidence("v5", "v5", 0.34, "vector"),
        evidence("target", "UAV-Human exact reference", 0.33, "vector"),
    ]

    fused = _reciprocal_rank_fusion(lexical, vector, top_k=3)

    assert fused[0].chunk_id == "target"
    assert fused[0].retrieval_mode == "hybrid"
    assert [item.evidence_id for item in fused] == ["E1", "E2", "E3"]
