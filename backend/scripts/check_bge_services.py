"""Smoke-test the optional local BGE embedding and reranker services."""

import argparse
import json
import os
from typing import Any

import httpx


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--embedding-url",
        default=os.getenv("EMBEDDING_BASE_URL", "http://localhost:8001/v1"),
    )
    parser.add_argument(
        "--reranker-url",
        default=os.getenv("RERANKER_BASE_URL", "http://localhost:8002"),
    )
    parser.add_argument(
        "--embedding-model", default=os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    )
    parser.add_argument(
        "--dimensions", type=int, default=int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))
    )
    parser.add_argument(
        "--timeout", type=float, default=float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "120"))
    )
    return parser.parse_args()


def _rank_items(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        ranks = body.get("ranks", body.get("results"))
        if isinstance(ranks, list):
            return ranks
    raise RuntimeError("Reranker returned an unsupported response shape.")


def main() -> None:
    args = _parse_args()
    embedding_url = args.embedding_url.rstrip("/").removesuffix("/v1")
    reranker_url = args.reranker_url.rstrip("/")

    with httpx.Client(timeout=args.timeout) as client:
        embedding_info = client.get(f"{embedding_url}/info")
        embedding_info.raise_for_status()
        reranker_info = client.get(f"{reranker_url}/info")
        reranker_info.raise_for_status()

        embedding_response = client.post(
            f"{embedding_url}/v1/embeddings",
            json={
                "model": args.embedding_model,
                "input": ["科研助手需要可验证的原文证据。"],
                "encoding_format": "float",
            },
        )
        embedding_response.raise_for_status()
        embedding_body = embedding_response.json()
        vector = embedding_body["data"][0]["embedding"]
        if len(vector) != args.dimensions:
            raise RuntimeError(
                f"Expected {args.dimensions} embedding dimensions, got {len(vector)}."
            )

        reranker_response = client.post(
            f"{reranker_url}/rerank",
            json={
                "query": "训练使用了什么 GPU？",
                "texts": [
                    "实验使用 8 张 NVIDIA P100 GPU 训练模型。",
                    "该论文提出了一种新的注意力机制。",
                ],
                "truncate": True,
                "raw_scores": False,
                "return_text": False,
            },
        )
        reranker_response.raise_for_status()
        ranks = _rank_items(reranker_response.json())
        if not ranks or int(ranks[0]["index"]) != 0:
            raise RuntimeError("Reranker did not rank the evidence sentence first.")

    print(
        json.dumps(
            {
                "status": "ok",
                "embedding_dimensions": len(vector),
                "reranker_top_index": int(ranks[0]["index"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
