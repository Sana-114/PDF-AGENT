"""Replay preserved model outputs through current guards; never generate or retry."""

import argparse
import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.harness import ResearchAgent  # noqa: E402
from app.llm.base import GeneratedAnswer, GeneratedClaim  # noqa: E402
from app.llm.model_fields import render_valid_numeric_fields, requested_numeric_fields  # noqa: E402
from app.schemas.agent import AskRequest, EvidenceAnchor  # noqa: E402
from app.services.answer_completeness import current_question, missing_fields  # noqa: E402
from app.services.claim_semantics import claim_semantic_conflicts  # noqa: E402
from app.services.table_comparison import (  # noqa: E402
    table_bleu_claims,
    table_bleu_proof_published,
)
from scripts.evaluate_real_pdf_comparison import _check_cross_document_fact  # noqa: E402
from scripts.review_official_first_round import check_reviewed_fact  # noqa: E402

TARGETS = ("bert-inheritance", "gpt-parameter-evolution", "context-evolution")
AVAILABLE_CASES = (*TARGETS, "sixth-author", "adam-settings", "base-gpus", "bleu-improvement",
                   "layernorm-reference", "gpt2-ln-change")


class SavedRegistry:
    def __init__(self, evidence):
        self.evidence = evidence

    async def execute(self, *_):
        return self.evidence


class SavedProvider:
    name = "saved_response_replay"

    def __init__(self, model, raw):
        self.model, self.raw = model, raw

    async def generate_grounded_answer(self, *_):
        return GeneratedAnswer(answer=self.raw["answer"], claims=[
            GeneratedClaim(**item) for item in self.raw["claims"]
        ], field_rejections=self.raw.get("field_rejections", {}),
            citation_normalizations=self.raw.get("citation_normalizations", {}))


async def replay(report_path: Path, dataset_path: Path, output: Path,
                 expectations_path: Path | None = None, case_ids: list[str] | None = None,
                 review_policy_path: Path | None = None, *, rerender_model_fields: bool = False) -> dict:
    if output.exists():
        raise ValueError("Do not overwrite any preserved report")
    report_bytes, dataset_bytes = report_path.read_bytes(), dataset_path.read_bytes()
    report, dataset = json.loads(report_bytes), json.loads(dataset_bytes)
    if hashlib.sha256(dataset_bytes).hexdigest() != report["dataset_sha256"]:
        raise ValueError("Original dataset SHA differs")
    selected = list(TARGETS) if case_ids is None else case_ids
    if (not selected or len(set(selected)) != len(selected)
            or not set(selected).issubset(AVAILABLE_CASES)):
        raise ValueError("Replay selection must contain unique known original target IDs")
    expected = None
    if expectations_path is not None:
        expected = json.loads(expectations_path.read_text(encoding="utf-8"))["cases"]
        if not isinstance(expected, dict) or set(expected) != set(selected):
            raise ValueError("Guard expectations must cover exactly the selected original targets")
        for item in expected.values():
            if (not isinstance(item, dict) or type(item.get("insufficient_evidence")) is not bool
                    or not isinstance(item.get("missing_fields"), list)
                    or any(not isinstance(field, str) or not field.strip()
                           for field in item["missing_fields"])):
                raise ValueError("Guard expectations require a boolean and explicit field names")
            if (not isinstance(item.get("required_semantic_rejections", []), list)
                    or any(not isinstance(reason, str) or not reason
                           for reason in item.get("required_semantic_rejections", []))):
                raise ValueError("Semantic rejection expectations must be explicit reason names")
    cases = {case["case_id"]: case for case in dataset["cases"]}
    if (len(cases) != len(dataset["cases"]) or not set(selected).issubset(cases)
            or any(sum(item["case_id"] == case_id for item in report["cases"]) != 1
                   for case_id in selected)):
        raise ValueError("Every selected original target must occur exactly once")
    overrides = {}
    if review_policy_path is not None:
        overrides = json.loads(review_policy_path.read_text(encoding="utf-8")).get(
            "fact_overrides", {})
        if not isinstance(overrides, dict):
            raise ValueError("Replay fact policy must be an object")
        for case_id, additions in overrides.items():
            if case_id not in cases or not isinstance(additions, dict):
                raise ValueError("Replay fact policy names an unknown original case")
            known = {fact["fact_id"] for fact in cases[case_id].get("facts", [])}
            for fact_id, item in additions.items():
                # Policies may tighten subject/operand association, never change
                # expected values, scientific derivations or source identity/pages.
                if (fact_id not in known or not isinstance(item, dict) or not set(item).issubset(
                    {"claim_contains_any_groups", "source_text_contains_all"}
                )):
                    raise ValueError("Replay fact policy cannot alter original gold or sources")
                for name, value in item.items():
                    groups = value if name == "claim_contains_any_groups" else [value]
                    if (not isinstance(groups, list) or not groups or any(
                        not isinstance(group, list) or not group
                        or any(not isinstance(option, str) or not option.strip()
                               for option in group)
                        for group in groups
                    )):
                        raise ValueError("Replay fact policy requires nonempty string groups")
    documents = {doc["filename"]: doc["id"] for doc in report["documents"]}
    results = []
    for original in report["cases"]:
        case_id = original["case_id"]
        if case_id not in selected:
            continue
        case = cases[case_id]
        evidence = [EvidenceAnchor.model_validate(item)
                    for item in original["response"]["evidence"]]
        scope = {documents[name] for name in case["documents"]}
        if any(item.document_id not in scope for item in evidence):
            raise ValueError("Preserved evidence is outside the original paper scope")
        raw = original["raw_generated"]
        schema_replayed = False
        if (raw is None or rerender_model_fields) and isinstance(original.get("raw_provider_output"), dict):
            provider_raw = original["raw_provider_output"]
            if set(provider_raw) == {"answer", "model_fields"}:
                citation_normalizations = {} if rerender_model_fields else None
                rendered, rejections = render_valid_numeric_fields(
                    provider_raw["model_fields"], requested_numeric_fields(case["question"]),
                    {item.evidence_id for item in evidence},
                    chinese=bool(re.search(r"[\u4e00-\u9fff]", case["question"])),
                    citation_normalizations=citation_normalizations,
                )
                raw = {"answer": provider_raw["answer"],
                       "claims": [{"text": item.text, "evidence_ids": item.evidence_ids}
                                  for item in rendered], "field_rejections": rejections,
                       "citation_normalizations": citation_normalizations or {}}
                schema_replayed = True
        if rerender_model_fields and not schema_replayed:
            raise ValueError("Explicit numeric-field rerender requires preserved complete model_fields output")
        if raw is None:
            raise ValueError("No complete saved model output can be replayed; never regenerate")

        # The registry's immutable saved evidence is the entire original scope.
        # No database, retrieval, API credentials, or model factory is used.
        response = await ResearchAgent(None, registry=SavedRegistry(evidence),
                                       provider=SavedProvider(report["model"], raw)).ask(
            AskRequest(question=case["question"], top_k=18)
        )
        anchors = {item.evidence_id: item for item in evidence}
        rejected_semantic = [{"claim_index": index, "reasons": errors}
                             for index, claim in enumerate(raw["claims"]) if (errors :=
                             claim_semantic_conflicts(claim["text"], [anchors[key] for key in
                                                      claim["evidence_ids"] if key in anchors],
                                                      evidence))]
        facts = {fact["fact_id"]: check_reviewed_fact(
            {**fact, **overrides.get(case_id, {}).get(fact["fact_id"], {})}, response, documents)
                 for fact in case.get("facts", [])}
        facts.update({fact["fact_id"]: _check_cross_document_fact(fact, response, documents)
                      for fact in case.get("cross_facts", [])})
        proof = table_bleu_claims(current_question(case["question"]), response.evidence)
        missing = missing_fields(case["question"], response.claims, response.evidence,
                                 bleu_proof_published=table_bleu_proof_published(
                                     response.claims, proof))
        if expected is not None:
            expectation = expected[case_id]
            repair_check_passed = (
                response.insufficient_evidence == expectation["insufficient_evidence"]
                and set(expectation["missing_fields"]) == set(missing)
                and set(expectation.get("required_semantic_rejections", [])).issubset({
                    reason for item in rejected_semantic for reason in item["reasons"]})
            )
        elif case_id == "context-evolution":
            repair_check_passed = response.insufficient_evidence and not facts["gpt3-context"]
        elif case_id == "gpt-parameter-evolution":
            repair_check_passed = (not response.insufficient_evidence and all(facts.values())
                                   and len(rejected_semantic) >= 2)
        else:
            repair_check_passed = not response.insufficient_evidence and all(facts.values())
        results.append({"case_id": case_id, "model_calls": 0, "facts": facts,
                        "model_field_schema_replayed": schema_replayed,
                        "semantic_rejections": rejected_semantic,
                        "missing_fields": missing,
                        "response": response.model_dump(),
                        "repair_check_passed": repair_check_passed})
    if (len(results) != len(selected)
            or {item["case_id"] for item in results} != set(selected)):
        raise ValueError("Every selected original failure must be present exactly once")
    outcome = {"evaluation_kind": "offline_saved_output_guard_replay", "model_calls": 0,
               "is_new_end_to_end_generation": False,
               "selected_case_ids": selected,
               "runtime_code_sha256": {str(path.relative_to(Path(__file__).parents[1])):
                   hashlib.sha256(path.read_bytes()).hexdigest() for path in [Path(__file__),
                       *sorted((Path(__file__).parents[1] / "app").rglob("*.py"))]},
               "guard_expectations_sha256": (
                   hashlib.sha256(expectations_path.read_bytes()).hexdigest()
                   if expectations_path is not None else None),
               "fact_review_policy_sha256": (
                   hashlib.sha256(review_policy_path.read_bytes()).hexdigest()
                   if review_policy_path is not None else None),
               "fact_policy_is_scientific_source_review": False,
               "original_report_sha256": hashlib.sha256(report_bytes).hexdigest(),
               "dataset_sha256": report["dataset_sha256"], "cases": results,
               "status": "passed" if all(item["repair_check_passed"] for item in results)
               else "failed"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    return outcome


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--guard-expectations", type=Path,
                        help="Explicit expected completeness flags; not answer correctness gold")
    parser.add_argument("--case-ids", nargs="+", choices=AVAILABLE_CASES,
                        help="Select unchanged saved targets; default is all three")
    parser.add_argument("--review-policy", type=Path,
                        help="Tighten only fact subject/operand association; no source verdict")
    parser.add_argument("--rerender-model-fields", action="store_true",
                        help="Replay exact saved numeric fields with current positive inline citation normalization")
    args = parser.parse_args()
    outcome = asyncio.run(replay(args.report, args.dataset, args.output,
                                args.guard_expectations, args.case_ids, args.review_policy,
                                rerender_model_fields=args.rerender_model_fields))
    print(json.dumps({"status": outcome["status"], "model_calls": 0,
                      "checks": [{"case_id": item["case_id"], "passed": item["repair_check_passed"],
                                  "semantic_rejections": len(item["semantic_rejections"]),
                                  "incomplete": item["response"]["insufficient_evidence"]}
                                 for item in outcome["cases"]]}))
    if outcome["status"] != "passed":
        raise SystemExit(1)
