"""Three bounded public/synthetic academic translation checks via the real API.

Source-grounded prose is audited manually; protected strings are checked exactly.
No private text, full document transmission or automatic generation retries.
"""

import argparse
import json
import re
from pathlib import Path
from unittest.mock import patch

from inspect_runtime_source import inspect_source
from official_functional_inputs import isolated_output, write_json
from prepare_official_browser_fixture import saved_inputs
from verify_official_output_features import configure


def run(args):
    if not args.allow_cloud:
        raise ValueError("Explicit public/synthetic cloud authorization required")
    row = saved_inputs(args.fixture, ["1706.03762v1.pdf"])[0]
    root = isolated_output(args.output_dir)
    configure(root)
    from fastapi.testclient import TestClient

    from app.api.routes import writing
    from app.llm.factory import get_llm_provider
    from app.main import app

    provider = get_llm_provider()
    if provider.name != "deepseek":
        raise ValueError("Actual DeepSeek required")
    cases = [
        (
            "public-abstract-en-zh",
            {
                "text": row["parsed"]["abstract"],
                "source_language": "en",
                "target_language": "zh",
                "document_type": "abstract",
            },
            [],
        ),
        (
            "public-method-summary-zh-en",
            {
                "text": "Transformer是一种完全基于注意力机制的序列转换模型，不使用循环或卷积。"
                "编码器和解码器采用堆叠的自注意力与逐位置前馈网络。"
                "这里概述的是已发表的方法，而不是本项目的新实验结果。",
                "source_language": "zh",
                "target_language": "en",
                "document_type": "abstract",
                "glossary": [{"source": "自注意力", "target": "self-attention"}],
            },
            ["self-attention"],
        ),
        (
            "synthetic-paper-protected-content",
            {
                "text": "## 方法\n\n本文拟研究检索增强生成，尚未开展新实验。"
                "以下数值仅用于软件测试，不是实验发现。"
                "假设目标函数为 $L = -\\log p(y|x)$，示例得分为 91.5% [11]。\n\n"
                "代码 `beta_2 = 0.98` 必须原样保留。参考地址为 https://example.org/paper 。",
                "source_language": "zh",
                "target_language": "en",
                "document_type": "paper",
                "glossary": [
                    {"source": "检索增强生成", "target": "retrieval-augmented generation"}
                ],
            },
            [
                "$L = -\\log p(y|x)$",
                "91.5%",
                "[11]",
                "`beta_2 = 0.98`",
                "https://example.org/paper",
                "retrieval-augmented generation",
            ],
        ),
    ]
    calls = []
    original = provider._post_response

    async def record(payload):
        record = {"request": payload, "status": "started"}
        calls.append(record)
        write_json(root / "calls.json", calls)
        try:
            raw = await original(payload)
            record.update(status="returned", response=raw)
            return raw
        except Exception as exc:
            record.update(status="failed", error=re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)))
            raise
        finally:
            write_json(root / "calls.json", calls)

    provider._post_response = record
    report = {
        "status": "running",
        "cases": [],
        "cloud_calls": 0,
        "automatic_retries": 0,
        "runtime_source": inspect_source(Path(__file__).resolve().parents[1]),
        "public_source_sha256": row["row"]["source_sha256"],
        "production_library_modified": False,
        "semantic_review": "pending",
    }
    with (
        patch.object(writing, "get_llm_provider", return_value=provider),
        TestClient(app) as client,
    ):
        for name, request, protected in cases:
            before = len(calls)
            response = client.post("/api/v1/writing/translate", json=request)
            result = response.json()
            text = result.get("translation", "")
            checks = {
                "api_200": response.status_code == 200,
                "actual_deepseek": result.get("provider") == "deepseek",
                "one_generation": len(calls) - before == 1,
                "protected_exact": all(item in text for item in protected),
                "nonempty_translation": bool(text.strip()),
            }
            write_json(root / (name + ".json"), {"request": request, "response": result})
            report["cases"].append(
                {
                    "case": name,
                    "checks": checks,
                    "status": "checks_passed" if all(checks.values()) else "failed",
                }
            )
            report["cloud_calls"] = len(calls)
            write_json(root / "report.json", report)
    report["status"] = (
        "checks_passed_pending_semantic_review"
        if all(c["status"] == "checks_passed" for c in report["cases"])
        else "held"
    )
    write_json(root / "report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return int(report["status"] == "held")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-cloud", action="store_true")
    raise SystemExit(run(parser.parse_args()))
