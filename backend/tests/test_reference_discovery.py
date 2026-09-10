import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.discovery import resolve_document_references
from app.core.config import Settings
from app.core.database import Base
from app.models.document import Document, DocumentStatus
from app.schemas.discovery import (
    PaperCandidate,
    ReferenceCandidateMatch,
    ReferenceResolution,
    ReferenceResolveRequest,
)
from app.services.paper_discovery import PaperDiscoveryError
from app.services.reference_discovery import (
    ReferenceDiscoveryService,
    reference_search_query,
    score_reference_candidate,
)


def _settings(tmp_path, concurrency: int = 2) -> Settings:
    return Settings(
        data_dir=tmp_path,
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
        translation_dir=tmp_path / "translations",
        reference_discovery_concurrency=concurrency,
        reference_match_threshold=0.55,
    )


def _paper(**overrides) -> PaperCandidate:
    values = {
        "source": "semantic_scholar",
        "source_id": "paper-1",
        "title": "Attention Is All You Need",
        "authors": ["Ashish Vaswani", "Noam Shazeer"],
        "year": 2017,
        "arxiv_id": "1706.03762",
        "pdf_url": "https://arxiv.org/pdf/1706.03762.pdf",
        "importable": True,
    }
    values.update(overrides)
    return PaperCandidate(**values)


def test_reference_search_query_prefers_exact_identifiers() -> None:
    assert reference_search_query("[7] A. Author. doi:10.1000/example. 2020.") == (
        "10.1000/example"
    )
    assert reference_search_query("[11] arXiv preprint arXiv:1706.03762v7") == (
        "1706.03762v7"
    )
    assert reference_search_query("[3] A. Author. Useful Paper. 2022.") == (
        "A. Author. Useful Paper. 2022."
    )


def test_reference_match_score_uses_title_authors_and_year() -> None:
    score, reason = score_reference_candidate(
        "A. Vaswani, N. Shazeer et al. Attention Is All You Need. NeurIPS, 2017.",
        _paper(arxiv_id=None),
    )
    assert score >= 0.8
    assert "题名词覆盖" in reason
    exact_score, exact_reason = score_reference_candidate(
        "Attention Is All You Need, arXiv:1706.03762",
        _paper(),
    )
    assert exact_score == 1.0
    assert exact_reason == "arXiv ID 完全一致"


@pytest.mark.asyncio
async def test_resolve_many_preserves_order_and_bounds_concurrency(tmp_path) -> None:
    class FakeDiscovery:
        active = 0
        max_active = 0

        async def search(self, query: str, limit: int):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return "title", [_paper(source_id=query, title=query)], []

    discovery = FakeDiscovery()
    service = ReferenceDiscoveryService(discovery, _settings(tmp_path, concurrency=2))
    references = [
        {
            "reference_id": f"ref-{index}",
            "label": str(index),
            "text": f"[{index}] Paper Title {index}",
            "page_number": 8,
        }
        for index in range(1, 6)
    ]
    results = await service.resolve_many(references, candidates_per_reference=1)

    assert [item.reference_id for item in results] == [f"ref-{index}" for index in range(1, 6)]
    assert discovery.max_active == 2


@pytest.mark.asyncio
async def test_resolution_returns_explainable_errors(tmp_path) -> None:
    class FailingDiscovery:
        async def search(self, query: str, limit: int):
            raise PaperDiscoveryError("upstream unavailable")

    service = ReferenceDiscoveryService(FailingDiscovery(), _settings(tmp_path))
    result = await service.resolve_one(
        {
            "reference_id": "ref-1",
            "label": "1",
            "text": "[1] Missing paper",
            "page_number": 3,
        }
    )
    assert result.status == "error"
    assert result.warnings == ["upstream unavailable"]


@pytest.mark.asyncio
async def test_reference_resolve_endpoint_uses_persisted_ast(tmp_path, monkeypatch) -> None:
    from app.services import storage as storage_module

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    monkeypatch.setattr(storage_module.settings, "parsed_dir", parsed_dir)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    class FakeResolver:
        async def resolve_many(self, references, *, candidates_per_reference: int):
            assert len(references) == 1
            assert candidates_per_reference == 2
            return [
                ReferenceResolution(
                    reference_id="ref-1",
                    label="1",
                    text=references[0]["text"],
                    page_number=2,
                    status="matched",
                    query_kind="title",
                    candidates=[
                        ReferenceCandidateMatch(
                            paper=_paper(),
                            match_score=0.93,
                            match_reason="题名与作者匹配",
                        )
                    ],
                )
            ]

    with Session(engine) as session:
        document = Document(
            id="doc-ready",
            original_filename="source.pdf",
            storage_key="source.pdf",
            content_type="application/pdf",
            size_bytes=100,
            sha256="b" * 64,
            status=DocumentStatus.READY,
        )
        session.add(document)
        session.commit()
        storage_module.storage.parsed_path(document.id).write_text(
            '{"references":[{"reference_id":"ref-1","label":"1",'
            '"text":"A. Vaswani. Attention Is All You Need. 2017.",'
            '"page_number":2,"bbox":[0,0,1,1],"block_ids":["p2-b1"]}]}',
            encoding="utf-8",
        )
        response = await resolve_document_references(
            ReferenceResolveRequest(
                document_id=document.id,
                limit=1,
                candidates_per_reference=2,
            ),
            session,
            FakeResolver(),
        )

    assert response.total_references == 1
    assert response.attempted == 1
    assert response.matched == 1
    assert response.importable == 1
