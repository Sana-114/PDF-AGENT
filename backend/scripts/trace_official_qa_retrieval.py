"""Trace original-question retrieval stages in a read-only public index, with no LLM.

Preserve lexical/vector recall, reranker input/output, final selection and the
same document-interleaving budget used by ask. Gold is applied only afterwards.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.rerankers import get_reranker  # noqa: E402
from app.schemas.agent import AnswerClaim  # noqa: E402
from app.services.retrieval import HybridRetriever  # noqa: E402
from scripts.evaluate_official_first_round import (  # noqa: E402
    ReadOnlyOfficialIndex,
    restore_saved_documents,
    select_cases,
    validate_saved_index,
)
from scripts.evaluate_real_pdf_comparison import _check_fact  # noqa: E402


def dumps(items):
    return [item.model_dump(mode="json") for item in items]


class RecordingStage:
    def __init__(self, delegate):
        self.delegate = delegate
        self.records = []

    def __getattr__(self, key):
        return getattr(self.delegate, key)

    def search(self, session, question, document_ids, limit):
        result = self.delegate.search(session, question, document_ids, limit)
        self.records.append({"question": question, "evidence": dumps(result)})
        return result

    def rerank(self, question, evidence, top_k):
        result = self.delegate.rerank(question, evidence, top_k)
        self.records.append({"question": question, "input": dumps(evidence),
                             "output": dumps(result)})
        return result


def run(args):
    if args.output.exists():
        raise ValueError("Never overwrite a diagnostic or historical report")
    dataset_bytes, report_bytes = args.dataset.read_bytes(), args.report.read_bytes()
    dataset, previous = json.loads(dataset_bytes), json.loads(report_bytes)
    cases = select_cases(dataset, args.case_ids, 9)
    settings.vector_collection = validate_saved_index(
        previous, dataset, hashlib.sha256(dataset_bytes).hexdigest()
    )
    if (not settings.vector_search_enabled or settings.embedding_model != "BAAI/bge-m3"
            or settings.reranker_provider != "tei"):
        raise ValueError("Actual configured BGE vector retrieval and TEI are required")
    index = ReadOnlyOfficialIndex()
    if (index.collection_name != previous["temporary_vector_collection_actual"]
            or not index.client.collection_exists(index.collection_name)):
        raise ValueError("Public index is missing or its namespace changed; never recreate")
    result = {
        "evaluation_kind": "readonly_original_question_retrieval_stage_trace",
        "timestamp": datetime.now(UTC).isoformat(), "model_calls": 0, "parser_calls": 0,
        "production_library_modified": False, "vector_index_modified": False,
        "is_generated_answer_validation": False,
        "source_report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "temporary_vector_collection": index.collection_name, "cases": [],
        "code_sha256": {str(p.relative_to(Path(__file__).parents[1])):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [Path(__file__), *sorted((Path(__file__).parents[1] / "app").rglob("*.py"))]},
    }
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ids = restore_saved_documents(session, index, previous["documents"])
        for case in cases:
            pools, traces = [], []
            for name in case["documents"]:
                vector, reranker = RecordingStage(index), RecordingStage(get_reranker())
                retriever = HybridRetriever(session, vector_index=vector, reranker=reranker)
                lexical_records, lexical_search = [], retriever.lexical.search

                def recorded_lexical(question, document_ids, top_k, *,
                                     _search=lexical_search, _records=lexical_records, **kwargs):
                    found = _search(question, document_ids, top_k, **kwargs)
                    _records.append({"question": question, "options": {
                        key: sorted(value) if isinstance(value, set) else value
                        for key, value in kwargs.items()
                    }, "evidence": dumps(found)})
                    return found

                retriever.lexical.search = recorded_lexical
                selected = retriever.search(case["question"], [ids[name]], 20)
                if any(item.document_id != ids[name] or item.recall_mode != "hybrid"
                       or item.retrieval_mode != "reranked" for item in selected):
                    raise ValueError("Scoped retrieval degraded; keep this as a real failure")
                pool = [item for item in selected if item.source_type != "reference"]
                pools.append(pool)
                traces.append({"filename": name, "document_id": ids[name],
                               "lexical": lexical_records, "vector": vector.records,
                               "reranker": reranker.records, "selected": dumps(selected)})
            evidence = []
            for rank in range(20):
                for pool in pools:
                    if rank < len(pool) and len(evidence) < args.top_k:
                        evidence.append(pool[rank].model_copy(update={
                            "evidence_id": f"E{len(evidence) + 1}"}))
            strong = [item for item in evidence if item.score >= settings.rag_min_score]
            # Excerpt-presence probe only, never an invented/published LLM answer.
            probe = SimpleNamespace(evidence=strong, claims=[AnswerClaim(
                text=item.quote, evidence_ids=[item.evidence_id]) for item in strong])
            facts = {fact["fact_id"]: _check_fact(fact, probe, ids)
                     for fact in case.get("facts", [])}
            result["cases"].append({"case_id": case["case_id"], "question": case["question"],
                                    "per_document": traces, "final_evidence": dumps(strong),
                                    "source_fact_recall": facts})
            print(json.dumps({"case_id": case["case_id"], "source_fact_recall": facts}), flush=True)
    engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--case-ids", nargs="+", required=True)
    parser.add_argument("--top-k", type=int, choices=range(1, 21), default=18)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args)
