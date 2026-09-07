import logging
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal, init_db
from app.models.document import Document, DocumentStatus
from app.parsers import get_parser
from app.parsers.checkpoint import write_json_atomic
from app.services.chunking import replace_document_chunks
from app.services.fingerprints import (
    bottom_k_signature,
    extract_arxiv_identity,
    signature_from_json,
    signature_similarity,
    signature_to_json,
    title_similarity,
)
from app.services.storage import storage
from app.services.vector_index import index_document_safely
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _recommend_version(current: Document, existing: Document) -> str:
    if current.arxiv_version and existing.arxiv_version:
        if current.arxiv_version < existing.arxiv_version:
            return (
                f"库中已有更新的 arXiv v{existing.arxiv_version}，"
                f"建议保留现有版本并将本次 v{current.arxiv_version} 作为历史版本。"
            )
        if current.arxiv_version > existing.arxiv_version:
            return (
                f"当前文件是更新的 arXiv v{current.arxiv_version}，"
                f"建议用它替换库中的 v{existing.arxiv_version}。"
            )
    return "两份文献正文高度重合，请确认覆盖现有版本或同时保留。"


def _find_semantic_duplicate(session, current: Document) -> None:
    current_signature = signature_from_json(current.semantic_signature)
    candidates = session.scalars(
        select(Document).where(
            Document.id != current.id,
            Document.status == DocumentStatus.READY,
            Document.semantic_signature.is_not(None),
        )
    ).all()

    best: tuple[float, Document] | None = None
    for candidate in candidates:
        same_arxiv = bool(current.arxiv_id and current.arxiv_id == candidate.arxiv_id)
        identity_score = 1.0 if same_arxiv else title_similarity(current.title, candidate.title)
        content_score = signature_similarity(
            current_signature, signature_from_json(candidate.semantic_signature)
        )
        combined_score = 0.55 * identity_score + 0.45 * content_score
        qualifies = (same_arxiv and content_score >= 0.30) or (
            identity_score >= 0.92 and content_score >= 0.55
        )
        if qualifies and (best is None or combined_score > best[0]):
            best = combined_score, candidate

    if best:
        score, candidate = best
        current.duplicate_of_id = candidate.id
        current.duplicate_score = round(score, 4)
        current.duplicate_recommendation = _recommend_version(current, candidate)


@celery_app.task(
    name="documents.parse", autoretry_for=(OSError,), retry_backoff=True, max_retries=3
)
def parse_document(document_id: str) -> dict[str, str]:
    init_db()
    settings.ensure_directories()

    with SessionLocal() as session:
        document = session.get(Document, document_id)
        if document is None:
            return {"document_id": document_id, "status": "missing"}
        document.status = DocumentStatus.PROCESSING
        document.error_message = None
        session.commit()

        try:
            source_path = str(storage.document_path(document.storage_key))
            parser = get_parser(source_path)
            checkpoint_dir = storage.checkpoint_dir(document.id)

            def record_progress(completed_pages: int, page_count: int) -> None:
                logger.info(
                    "Document parse checkpoint saved",
                    extra={
                        "document_id": document.id,
                        "completed_pages": completed_pages,
                        "page_count": page_count,
                    },
                )

            parsed = parser.parse(
                source_path,
                checkpoint_dir=checkpoint_dir,
                batch_size=settings.parse_batch_pages,
                source_fingerprint=document.sha256,
                progress_callback=record_progress,
            )
            signature = bottom_k_signature(parsed.full_text)
            arxiv_id, arxiv_version = extract_arxiv_identity(parsed.full_text)

            document.title = parsed.title
            document.page_count = len(parsed.pages)
            document.arxiv_id = arxiv_id
            document.arxiv_version = arxiv_version
            document.semantic_signature = signature_to_json(signature)
            document.parser_name = parser.name
            document.parser_version = parser.version

            output = parsed.to_dict()
            output["document_id"] = document.id
            output["arxiv_id"] = arxiv_id
            output["arxiv_version"] = arxiv_version
            output["parser"] = {"name": parser.name, "version": parser.version}
            output["processing"] = {
                "mode": "checkpointed_page_batches",
                "batch_size_pages": settings.parse_batch_pages,
            }
            diagnostics = getattr(parser, "diagnostics", None)
            if diagnostics is not None:
                output["pdf_diagnostics"] = diagnostics.to_dict()
            processing_metadata = getattr(parser, "processing_metadata", None)
            if processing_metadata is not None:
                output["ocr"] = processing_metadata()
            write_json_atomic(Path(storage.parsed_path(document.id)), output)
            replace_document_chunks(session, document.id, output)
            session.flush()
            index_document_safely(session, document.id)

            document.status = DocumentStatus.READY
            _find_semantic_duplicate(session, document)
            session.commit()
            try:
                storage.clear_checkpoint(document.id)
            except OSError:
                logger.warning(
                    "Completed parse checkpoint cleanup failed",
                    exc_info=True,
                    extra={"document_id": document.id},
                )
            return {"document_id": document_id, "status": "ready"}
        except Exception as exc:
            logger.exception("Document parsing failed", extra={"document_id": document_id})
            document.status = DocumentStatus.FAILED
            document.error_message = str(exc)[:2000]
            session.commit()
            raise
