import json
import logging
from functools import lru_cache
from typing import Any

from qdrant_client import QdrantClient, models
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.embeddings import get_embedding_provider
from app.embeddings.base import EmbeddingProvider
from app.models.chunk import DocumentChunk
from app.models.document import Document, DocumentStatus
from app.schemas.agent import EvidenceAnchor
from app.services.chunking import ensure_document_chunks
from app.services.text_features import infer_source_type

logger = logging.getLogger(__name__)


class QdrantVectorIndex:
    dense_vector_name = "dense"
    sparse_vector_name = "sparse"

    def __init__(
        self,
        config: Settings = settings,
        *,
        client: QdrantClient | None = None,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self.config = config
        self.enabled = config.vector_search_enabled
        self.embedder = embedder or get_embedding_provider(config)
        self.collection_name = (
            f"{config.vector_collection}_{self.embedder.index_namespace}_"
            f"{self.embedder.dimensions}"
        )
        self.client = client or QdrantClient(
            url=config.qdrant_url,
            timeout=config.vector_timeout_seconds,
        )
        self._collection_ready = False

    def ensure_collection(self) -> None:
        if self._collection_ready:
            return
        if not self.client.collection_exists(self.collection_name):
            try:
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config={
                        self.dense_vector_name: models.VectorParams(
                            size=self.embedder.dimensions,
                            distance=models.Distance.COSINE,
                        )
                    },
                    sparse_vectors_config={
                        self.sparse_vector_name: models.SparseVectorParams(
                            index=models.SparseIndexParams(on_disk=False),
                            modifier=models.Modifier.IDF,
                        )
                    },
                )
            except Exception:
                if not self.client.collection_exists(self.collection_name):
                    raise
            try:
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="document_id",
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            except Exception as exc:
                logger.info("Qdrant payload index already exists or is unavailable: %s", exc)
        self._collection_ready = True

    def index_document(self, session: Session, document_id: str) -> int:
        if not self.enabled:
            return 0
        self.ensure_collection()
        document = session.get(Document, document_id)
        if document is None:
            return 0
        chunks = session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
        self.delete_document(document_id)
        if not chunks:
            return 0

        batch_size = self.config.embedding_batch_size
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset : offset + batch_size]
            texts = [_index_text(chunk) for chunk in batch]
            dense_vectors = self.embedder.embed_dense(texts)
            points = [
                self._point(chunk, document.title, text, dense)
                for chunk, text, dense in zip(batch, texts, dense_vectors, strict=True)
            ]
            self.client.upsert(
                collection_name=self.collection_name,
                points=points,
                wait=True,
            )
        return len(chunks)

    def ensure_documents(self, session: Session, document_ids: list[str] | None) -> int:
        if not self.enabled:
            return 0
        ensure_document_chunks(session, document_ids)
        query = select(Document.id).where(Document.status == DocumentStatus.READY)
        if document_ids:
            query = query.where(Document.id.in_(document_ids))
        indexed = 0
        for document_id in session.scalars(query).all():
            expected = session.scalar(
                select(func.count()).select_from(DocumentChunk).where(
                    DocumentChunk.document_id == document_id
                )
            ) or 0
            actual = self._document_point_count(document_id)
            if actual != expected:
                indexed += self.index_document(session, document_id)
        return indexed

    def search(
        self,
        session: Session,
        question: str,
        document_ids: list[str] | None,
        limit: int,
    ) -> list[EvidenceAnchor]:
        if not self.enabled:
            return []
        self.ensure_collection()
        self.ensure_documents(session, document_ids)
        dense = self.embedder.embed_dense([question])[0]
        sparse = self.embedder.embed_sparse(question)
        if not sparse.indices:
            return []
        query_filter = _document_filter(document_ids)
        prefetch_limit = max(limit, self.config.vector_prefetch_k)
        response = self.client.query_points(
            collection_name=self.collection_name,
            prefetch=[
                models.Prefetch(
                    query=dense,
                    using=self.dense_vector_name,
                    filter=query_filter,
                    limit=prefetch_limit,
                ),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=sparse.indices,
                        values=sparse.values,
                    ),
                    using=self.sparse_vector_name,
                    filter=query_filter,
                    limit=prefetch_limit,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        return [
            _point_to_evidence(point, index)
            for index, point in enumerate(response.points, start=1)
            if point.payload
        ]

    def delete_document(self, document_id: str) -> None:
        if not self.enabled:
            return
        self.ensure_collection()
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=_document_filter([document_id]),
            wait=True,
        )

    def _document_point_count(self, document_id: str) -> int:
        self.ensure_collection()
        return int(
            self.client.count(
                collection_name=self.collection_name,
                count_filter=_document_filter([document_id]),
                exact=True,
            ).count
        )

    def _point(
        self,
        chunk: DocumentChunk,
        document_title: str | None,
        text: str,
        dense: list[float],
    ) -> models.PointStruct:
        block_ids = json.loads(chunk.block_ids_json)
        sparse = self.embedder.embed_sparse(text)
        return models.PointStruct(
            id=chunk.id,
            vector={
                self.dense_vector_name: dense,
                self.sparse_vector_name: models.SparseVector(
                    indices=sparse.indices,
                    values=sparse.values,
                ),
            },
            payload={
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "document_title": document_title,
                "page_number": chunk.page_number,
                "block_ids": block_ids,
                "bbox": json.loads(chunk.bbox_json) if chunk.bbox_json else None,
                "section": chunk.section,
                "source_type": infer_source_type(chunk.section, block_ids),
                "text": chunk.text,
            },
        )


def _index_text(chunk: DocumentChunk) -> str:
    return f"{chunk.section or ''}\n{chunk.text}".strip()


def _document_filter(document_ids: list[str] | None) -> models.Filter | None:
    if not document_ids:
        return None
    return models.Filter(
        must=[
            models.FieldCondition(
                key="document_id",
                match=models.MatchAny(any=document_ids),
            )
        ]
    )


def _point_to_evidence(point: Any, index: int) -> EvidenceAnchor:
    payload = point.payload or {}
    return EvidenceAnchor(
        evidence_id=f"V{index}",
        chunk_id=str(payload.get("chunk_id") or point.id),
        document_id=str(payload.get("document_id", "")),
        document_title=payload.get("document_title"),
        page_number=int(payload.get("page_number", 1)),
        block_ids=[str(value) for value in payload.get("block_ids", [])],
        bbox=payload.get("bbox"),
        section=payload.get("section"),
        source_type=str(payload.get("source_type", "text")),
        retrieval_mode="vector",
        quote=str(payload.get("text", "")),
        score=round(min(1.0, max(0.0, float(point.score))), 4),
    )


@lru_cache
def get_vector_index() -> QdrantVectorIndex:
    return QdrantVectorIndex()


def index_document_safely(session: Session, document_id: str) -> int:
    try:
        return get_vector_index().index_document(session, document_id)
    except Exception as exc:
        logger.warning("Qdrant indexing skipped for document %s: %s", document_id, exc)
        return 0


def delete_document_index_safely(document_id: str) -> None:
    try:
        get_vector_index().delete_document(document_id)
    except Exception as exc:
        logger.warning("Qdrant cleanup skipped for document %s: %s", document_id, exc)
