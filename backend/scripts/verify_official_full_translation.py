"""One-attempt-per-batch real translation of the public official Transformer v1.

All responses and failed checkpoints remain local. Resume is explicit and only
allowed for the exact same source, AST and provider; no automatic cloud retries.
"""

import argparse
import asyncio
import json
import re
from collections import Counter
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from official_functional_inputs import PROJECT, digest, isolated_output, load_inputs, write_json


async def run(args: argparse.Namespace) -> int:
    from app.llm.factory import get_llm_provider
    from app.services.academic_translation import PROTECTED_SPAN_RE
    from app.services.document_translation import run_document_translation
    from app.services.document_translation_export import build_document_translation_export
    from app.services.translation_store import TranslationStore

    if not args.allow_cloud:
        raise ValueError("Real cloud translation requires --allow-cloud")
    selected = next(
        x
        for x in load_inputs(args.corpus, args.parse_report)
        if x["row"]["filename"] == "1706.03762v1.pdf"
    )
    provider = get_llm_provider()
    if provider.name != "deepseek":
        raise ValueError("This acceptance requires the configured real DeepSeek provider")
    root = isolated_output(args.output_dir, resume=args.resume)
    identity = {
        "filename": selected["source"].name,
        "source_sha256": selected["row"]["source_sha256"],
        "ast_sha256": selected["ast_sha256"],
        "provider": provider.name,
        "model": provider.model,
        "page_count": selected["row"]["page_count"],
    }
    identity_path = root / "identity.json"
    if args.resume:
        if json.loads(identity_path.read_text(encoding="utf-8")) != identity:
            raise ValueError("Resume identity changed; use a new output directory")
    else:
        write_json(identity_path, identity)
    report = {
        "identity": identity,
        "status": "running",
        "automatic_retries": 0,
        "cloud_calls_this_attempt": 0,
        "pages": [],
        "checks": [],
        "scope": "All nonempty AST blocks, typed formula/code passthrough, aligned exports; "
        "not proof of perfect formula extraction or every sentence's translation quality",
    }
    attempts = sorted((root / "attempts").glob("attempt-*.json"))
    attempt_path = root / "attempts" / f"attempt-{len(attempts) + 1:03d}.json"
    write_json(attempt_path, report)
    original_post = provider._post_response

    async def recording_post(payload: dict) -> dict:
        call_index = len(list((root / "calls").glob("call-*.json"))) + 1
        record = {"index": call_index, "status": "started", "request": payload}
        call_path = root / "calls" / f"call-{call_index:03d}.json"
        write_json(call_path, record)
        report["cloud_calls_this_attempt"] += 1
        write_json(attempt_path, report)
        print(
            f"cloud request {call_index}: {payload.get('text', {}).get('format', {}).get('name')}",
            flush=True,
        )
        try:
            raw = await original_post(payload)
            record.update(status="returned", response=raw)
            return raw
        except Exception as exc:
            record.update(status="failed", error=re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)))
            raise
        finally:
            write_json(call_path, record)

    provider._post_response = recording_post
    document_id = str(uuid5(NAMESPACE_URL, "official-acceptance:" + identity["source_sha256"]))
    store = TranslationStore(root / "translations")
    try:
        status = await run_document_translation(
            document_id=document_id,
            parsed=selected["parsed"],
            source_fingerprint=identity["source_sha256"],
            target_language="zh",
            provider=provider,
            store=store,
        )
        pages = []
        for original_page in selected["parsed"]["pages"]:
            number = original_page["page_number"]
            page = store.read_page(
                document_id, "zh", number, source_fingerprint=identity["source_sha256"]
            )
            expected = [
                b
                for b in original_page["blocks"]
                if b.get("block_id") and b.get("text", "").strip()
            ]
            errors = []
            if page is None:
                errors.append("missing page")
                page = {"segments": []}
            actual = page["segments"]
            if [b["block_id"] for b in expected] != [b["block_id"] for b in actual]:
                errors.append("block coverage/order mismatch")
            if page.get("truncated"):
                errors.append("truncated page")
            pairs = list(zip(expected, actual, strict=False))
            for source, item in pairs:
                text = source["text"].strip()
                if item["source_text"] != text or item.get("bbox") != source.get("bbox"):
                    errors.append(f"source alignment: {source['block_id']}")
                if source.get("type") in {"formula", "code"}:
                    if item["translation"] != text:
                        errors.append(f"changed typed formula/code: {source['block_id']}")
                for token, count in Counter(PROTECTED_SPAN_RE.findall(text)).items():
                    # Restoration already guarantees every placeholder exactly once.
                    if item["translation"].count(token) < count:
                        errors.append(f"lost protected span: {source['block_id']}")
                if "__PAPERPILOT_" in item["translation"]:
                    errors.append(f"unrestored placeholder: {source['block_id']}")
            report["pages"].append(
                {
                    "page_number": number,
                    "expected_blocks": len(expected),
                    "actual_blocks": len(actual),
                    "failures": errors,
                }
            )
            pages.append(page)
        manifest = store.read_manifest(document_id, "zh")
        exports = []
        for fmt in ("markdown", "html"):
            for mode in ("bilingual", "translation"):
                export = build_document_translation_export(
                    title=selected["parsed"]["title"],
                    original_filename=identity["filename"],
                    manifest=manifest,
                    pages=pages,
                    output_format=fmt,
                    mode=mode,
                )
                path = root / "exports" / f"{mode}-{export.filename}"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(export.content, encoding="utf-8")
                exports.append(
                    {"path": str(path), "sha256": digest(path), "mode": mode, "format": fmt}
                )
        report.update(
            status="passed" if all(not p["failures"] for p in report["pages"]) else "failed",
            translation_status=status,
            exports=exports,
            document_id=document_id,
        )
    except Exception as exc:
        report.update(
            status="failed",
            error=re.sub(r"sk-[\w-]{16,}", "[redacted]", str(exc)),
            checkpoint=store.read_manifest(document_id, "zh"),
        )
    report["source_unchanged"] = digest(selected["source"]) == identity["source_sha256"]
    if not report["source_unchanged"]:
        report["status"] = "failed"
    write_json(attempt_path, report)
    write_json(root / "report.json", report)
    print(
        json.dumps(
            {k: report[k] for k in ("status", "cloud_calls_this_attempt", "source_unchanged")}
        ),
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=PROJECT / "科研小助手/pdf")
    parser.add_argument("--parse-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument("--resume", action="store_true")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
