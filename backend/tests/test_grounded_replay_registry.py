import sys
from pathlib import Path

import pytest

from app.schemas.agent import EvidenceAnchor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_official_grounded_replay import ArchivedEvidenceRegistry  # noqa: E402


@pytest.mark.asyncio
async def test_archive_reads_are_scoped_copied_and_explicitly_not_fresh_retrieval():
    original = [EvidenceAnchor(evidence_id="E1", document_id="one", page_number=1,
                              quote="Only the selected public source.", score=0.9),
                EvidenceAnchor(evidence_id="E2", document_id="two", page_number=2,
                              quote="Other selected public source.", score=0.8)]
    reads = []
    registry = ArchivedEvidenceRegistry(original, ["one", "two"], reads)
    rows = await registry.execute("search_evidence", {"document_ids": ["one"]}, None)
    assert len(rows) == 1 and rows[0].document_id == "one"
    rows[0].quote = "Edited test copy"
    assert original[0].quote == "Only the selected public source."
    assert reads == [{"document_ids": ["one"], "not_live_retrieval": True}]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,scope", [
    ("translate", ["one"]), ("search_evidence", []), ("search_evidence", ["private"]),
])
async def test_archive_adapter_cannot_expand_scope_or_call_other_tools(name, scope):
    reads = []
    registry = ArchivedEvidenceRegistry([], ["one"], reads)
    with pytest.raises(AssertionError):
        await registry.execute(name, {"document_ids": scope}, None)
    assert not reads
