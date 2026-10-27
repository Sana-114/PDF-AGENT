"""Two fixed public-paper checks against an isolated current-source HTTP API.

No uploads, production collections or automatic retries. Native PDF premises
are checked before either billed request; final semantic review stays separate.
"""

import argparse
import asyncio
import json
import re
from pathlib import Path

import httpx
import pymupdf
from official_functional_inputs import TEMP_ROOT, digest, isolated_output, write_json
from verify_official_output_features import select_sources
from verify_official_runtime_reader import validate_api
from verify_running_qa_counterexamples import checks as counterexample_checks

FILES = ["1706.03762v1.pdf", "2106.09685v2.pdf", "2305.14314v1.pdf"]
POLICY_FILES = ["2305.18290v3.pdf", "2203.02155v1.pdf"]


def saved_retrieval_cases(path: Path, fixture: Path, rows: list[dict], tasks: list) -> list:
    """Pin old retrieval only; never read a saved answer into the generation prompt."""
    from evaluate_official_all_document_qa import source_anchor_valid

    from app.schemas.agent import EvidenceAnchor
    from app.services.chunking import build_chunk_drafts

    path = path.resolve()
    if not path.is_relative_to(TEMP_ROOT) or not path.is_file():
        raise ValueError("Saved retrieval must be an isolated acceptance report")
    previous = json.loads(path.read_bytes())
    if previous.get("sources") != rows or len(previous.get("cases", [])) != len(tasks):
        raise ValueError("Saved source/AST identities or case count changed")
    cache = {}
    for row in rows:
        ast = json.loads((fixture / "parsed" / (row["document_id"] + ".json")).read_bytes())
        cache[row["document_id"]] = (ast, build_chunk_drafts(ast))
    result = []
    for old, (case_id, request) in zip(previous["cases"], tasks, strict=True):
        if old["case_id"] != case_id or old["question"] != request["question"]:
            raise ValueError("Saved retrieval belongs to a different fixed question")
        evidence = [EvidenceAnchor.model_validate(e) for e in old["response"]["evidence"]]
        if not evidence or len(evidence) > request["top_k"]:
            raise ValueError("Saved evidence empty or exceeds the fixed evidence budget")
        for e in evidence:
            if (e.document_id not in request["document_ids"]
                    or e.recall_mode != "hybrid" or e.retrieval_mode != "reranked"):
                raise ValueError("Saved evidence outside the public question scope/provenance")
            ast, drafts = cache[e.document_id]
            if not source_anchor_valid(e, ast, drafts):
                raise ValueError("Saved retrieval quote/position is not in the pinned AST")
        result.append(evidence)
    return result


async def generated_from_saved(tasks: list, pools: list, report: dict, root: Path, suite: str,
                               seed: dict | None):
    from inspect_runtime_source import inspect_source

    from app.agent.harness import ResearchAgent
    from app.llm.factory import get_llm_provider
    from app.schemas.agent import AskRequest

    provider = get_llm_provider()
    if provider.name != "deepseek":
        raise ValueError("Fixed-evidence generation requires real DeepSeek")
    calls, original = [], provider._post_response

    async def recorded(payload):
        if len(calls) >= len(tasks):
            raise ValueError("Fixed-evidence suite prohibits extra model requests")
        record = {"request": payload, "status": "started"}
        calls.append(record)
        write_json(root / "calls.json", calls)
        try:
            response = await original(payload)
            record.update(status="returned", response=response)
            return response
        except Exception as exc:
            record.update(status="failed", error=re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)))
            raise
        finally:
            write_json(root / "calls.json", calls)

    provider._post_response = recorded
    report["runtime_source"] = inspect_source(Path(__file__).resolve().parents[1])
    class Registry:
        def __init__(self, evidence):
            self.evidence = sorted(evidence, key=lambda e: int(e.evidence_id.removeprefix("E")))

        async def execute(self, name, args, context):
            return [e for e in self.evidence if e.document_id in args["document_ids"]]

    for (case_id, request), pool in zip(tasks, pools, strict=True):
        original_generate = provider.generate_grounded_answer

        async def checked_generate(question, evidence, expected=pool, delegate=original_generate):
            if [e.model_dump() for e in evidence] != [e.model_dump() for e in expected]:
                raise ValueError("Fixed evidence identity/order changed before billing")
            return await delegate(question, evidence)

        provider.generate_grounded_answer = checked_generate
        report["generation_requests"] += 1
        write_json(root / "report.json", report)
        answer = await ResearchAgent(None, registry=Registry(pool), provider=provider).ask(
            AskRequest(**request)
        )
        provider.generate_grounded_answer = original_generate
        payload = answer.model_dump(mode="json")
        checks = (policy_checks(payload, request["document_ids"],
                                counterexample=case_id == tasks[1][0]) if suite == "policy" else
                  comparison_checks(payload, request["document_ids"]) if case_id == tasks[0][0]
                  else counterexample_checks(seed, payload, request["document_ids"][0]))
        report["cases"].append({"case_id": case_id, "question": request["question"],
                                "response": payload, "checks": checks,
                                "status": "source_checks_passed" if all(checks.values())
                                else "failed"})
        report["cloud_calls"] = len(calls)
        write_json(root / "report.json", report)


def policy_tasks(fixture: Path, rows: list[dict]) -> list:
    """Independent native premises checked locally, never inserted as evidence."""
    premises = [
        ("optimal policy", "in closed form", "simple classification loss",
         "sampling from the LM during fine-tuning"),
        ("supervised", "reinforcement learning from human"),
    ]
    for row, required in zip(rows, premises, strict=True):
        with pymupdf.open(fixture / "uploads" / (row["document_id"] + ".pdf")) as pdf:
            native = " ".join(pdf[0].get_text().split())
            if any(phrase not in native for phrase in required):
                raise ValueError("Independent public policy-learning premise absent")
    return [
        ("dpo-instructgpt-object-and-stage", {
            "question": "比较 DPO 与 InstructGPT 的偏好学习流程：DPO 原文中的闭式结果具体"
                        "对应什么对象，模型通过什么目标学习，以及免去的采样指什么对象、处于"
                        "哪个阶段？比较结论引用两篇原文，并保留必要限定。",
            "document_ids": [row["document_id"] for row in rows], "top_k": 20,
        }),
        ("dpo-reject-global-analytic-and-sampling-free", {
            "question": "DPO 的闭式结果和免采样，是否意味着神经网络参数能一次闭式求出，"
                        "而且数据准备、模型微调和推理的整个流程都完全不需要任何采样？"
                        "请依据原文解释这些说法的边界，不用常识补充未报告的实现细节。",
            "document_ids": [rows[0]["document_id"]], "top_k": 20,
        }),
    ]


def policy_checks(payload: dict, document_ids: list[str], *, counterexample=False) -> dict:
    anchors = {item["evidence_id"]: item for item in payload["evidence"]}
    claims = payload["claims"]
    used = {key for claim in claims for key in claim["evidence_ids"]}
    scope = set(document_ids)
    text = " ".join(claim["text"] for claim in claims)
    result = {
        "real_deepseek_not_fallback": payload["provider"] == "deepseek",
        "not_insufficient": not payload["insufficient_evidence"],
        "published_sources_scoped_and_hybrid_reranked": bool(used) and all(
            key in anchors and anchors[key]["document_id"] in scope
            and anchors[key]["recall_mode"] == "hybrid"
            and anchors[key]["retrieval_mode"] == "reranked" for key in used),
        "policy_object_and_learning_objective_present": all(re.search(p, text, re.I) for p in
            (r"策略|\bpolicy\b", r"闭式|closed[- ]form",
             r"分类损失|classification loss|二[值元](?:交叉熵|分类)|"
             r"二分类[/／\s]*交叉熵|binary[ -]cross[ -]entropy")),
        "sampling_object_and_stage_in_same_published_claim": any(
            all(re.search(p, claim["text"], re.I) for p in
                (r"采样|sampling", r"语言模型|\bLM\b|language model", r"微调|fine[- ]tuning"))
            for claim in claims),
    }
    if counterexample:
        result["explicitly_rejects_overgeneralization"] = bool(re.search(
            r"不意味着|不等于|并非|而非|并未声称|并不表示|并不表明|不能|不是|不代表|不支持|未(?:给出|提供|报告)|"
            r"does not (?:mean|imply)|not equivalent|cannot", text, re.I))
    else:
        result["one_comparison_cites_both_originals"] = any(
            {anchors[key]["document_id"] for key in claim["evidence_ids"] if key in anchors}
            == scope for claim in claims)
    return result


def comparison_checks(payload: dict, document_ids: list[str]) -> dict:
    anchors = {e["evidence_id"]: e for e in payload["evidence"]}
    used = {key for c in payload["claims"] for key in c["evidence_ids"]}
    required = set(document_ids)
    cross = any(
        {anchors[key]["document_id"] for key in c["evidence_ids"] if key in anchors} == required
        for c in payload["claims"]
    )
    text = " ".join(c["text"] for c in payload["claims"])
    return {
        "real_deepseek_not_fallback": payload["provider"] == "deepseek",
        "not_insufficient": not payload["insufficient_evidence"],
        "one_claim_has_both_originals": cross,
        "all_published_sources_in_scope": bool(used) and all(
            key in anchors and anchors[key]["document_id"] in required for key in used),
        "all_used_anchors_hybrid_reranked": bool(used) and all(
            key in anchors and anchors[key]["recall_mode"] == "hybrid"
            and anchors[key]["retrieval_mode"] == "reranked" for key in used),
        "quantization_and_memory_methods": all(re.search(pattern, text, re.I) for pattern in (
            r"4[\s-]*(?:bits?|位|比特)", r"NF4|NormalFloat", r"double|双重量化|二次量化",
            r"paged|分页", r"冻结|frozen|freez",
        )),
    }


def mechanism_tasks(fixture: Path, rows: list[dict]) -> tuple[list, dict]:
    """Keep the existing two questions and native premises unchanged."""
    policy_path = (Path(__file__).resolve().parents[1]
                   / "evals/official_runtime_counterexamples.json")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    seed = next(c for c in policy["cases"] if c["case_id"] == "unreported-training-seed")
    if rows[0]["source_sha256"] != policy["source_sha256"]:
        raise ValueError("The fixed absence premise belongs to a different public PDF")
    for row in rows:
        with pymupdf.open(fixture / "uploads" / (row["document_id"] + ".pdf")) as pdf:
            text = " ".join(page.get_text() for page in pdf)
            if row == rows[0] and re.search(seed["source_absence_regex"], text):
                raise ValueError("Native source reports seed; absence premise invalid")
            if row == rows[1] and "freezes the pre-" not in text:
                raise ValueError("LoRA freezing premise absent from native PDF")
            if row == rows[2] and any(s not in text for s in
                                     ("4-bit", "Double Quantization", "Paged Optimizers")):
                raise ValueError("QLoRA independent native premises absent")
    tasks = [
        ("lora-qlora-mechanism", {
            "question": "比较 LoRA 与 QLoRA：两者如何处理预训练权重？QLoRA 使用什么位宽量化，"
                        "以及 NF4、双重量化和分页优化器各自解决什么内存问题？分别引用两篇原文。",
            "document_ids": [r["document_id"] for r in rows[1:]], "top_k": 20,
        }),
        (seed["case_id"], {"question": seed["question"],
                           "document_ids": [rows[0]["document_id"]], "top_k": 18}),
    ]
    return tasks, seed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--api")
    mode.add_argument("--saved-retrieval", type=Path,
                      help="New generation from pinned old excerpts, NOT fresh BGE retrieval")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument("--suite", choices=["mechanism", "policy"], default="mechanism")
    args = parser.parse_args()
    api = validate_api(args.api) if args.api else None
    if not args.allow_cloud:
        raise ValueError("Explicit public-excerpt cloud allowance required")
    rows = select_sources(args.fixture, FILES if args.suite == "mechanism" else POLICY_FILES)
    if args.suite == "policy":
        tasks, seed = policy_tasks(args.fixture, rows), None
    else:
        tasks, seed = mechanism_tasks(args.fixture, rows)
    root = isolated_output(args.output)
    report = {"status": "running", "api": api, "sources": rows, "suite": args.suite,
              "automatic_retries": 0, "generation_requests": 0,
              "production_library_modified": False, "semantic_review": "pending", "cases": [],
              "server_source_sha256": None}
    if args.saved_retrieval:
        report.update(retrieval_scope="fixed_saved_BGE_evidence_not_new_retrieval", cloud_calls=0)
        try:
            pools = saved_retrieval_cases(args.saved_retrieval, args.fixture, rows, tasks)
            report["saved_retrieval_sha256"] = digest(args.saved_retrieval)
            asyncio.run(generated_from_saved(tasks, pools, report, root, args.suite, seed))
            report["status"] = "source_checks_passed" if all(
                c["status"] == "source_checks_passed" for c in report["cases"]) else "failed"
        except Exception as exc:
            report.update(status="failed", error=re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)))
        write_json(root / "report.json", report)
        print(json.dumps({"status": report["status"], "retrieval_scope": report["retrieval_scope"],
                          "cases": [{"case_id": c["case_id"], "checks": c["checks"]}
                                    for c in report["cases"]]}, ensure_ascii=False))
        return 0 if report["status"] == "source_checks_passed" else 1
    try:
        source_path = args.fixture / "server-source.json"
        if not source_path.is_file():
            raise ValueError("Isolated server has not completed startup: server-source.json absent")
        report["server_source_sha256"] = digest(source_path)
        with httpx.Client(base_url=api, timeout=180, trust_env=False) as client:
            status = client.get("/api/v1/agent/status")
            status.raise_for_status()
            report["agent_status"] = status.json()
            for case_id, request in tasks:
                report["generation_requests"] += 1
                write_json(root / "report.json", report)
                response = client.post("/api/v1/agent/ask", json=request)
                response.raise_for_status()
                payload = response.json()
                result = (policy_checks(payload, request["document_ids"],
                                        counterexample=case_id == tasks[1][0])
                          if args.suite == "policy" else
                          comparison_checks(payload, request["document_ids"])
                          if case_id == tasks[0][0]
                          else counterexample_checks(seed, payload, rows[0]["document_id"]))
                row = {"case_id": case_id, "question": request["question"],
                       "response": payload, "checks": result,
                       "status": "source_checks_passed" if all(result.values()) else "failed"}
                report["cases"].append(row)
                write_json(root / "report.json", report)
        report["status"] = "source_checks_passed" if all(
            row["status"] == "source_checks_passed" for row in report["cases"]) else "failed"
    except Exception as exc:
        report.update(status="failed", error=re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)))
    write_json(root / "report.json", report)
    print(json.dumps({"status": report["status"], "cases": [
        {"case_id": row["case_id"], "checks": row["checks"]} for row in report["cases"]]},
        ensure_ascii=False))
    return 0 if report["status"] == "source_checks_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
