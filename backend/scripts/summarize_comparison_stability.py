"""Aggregate repeated real-PDF comparison reports into a stability release gate."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from itertools import combinations
from pathlib import Path

DEFAULT_THRESHOLDS = {
    "min_round_pass_rate": 1.0,
    "min_case_pass_rate": 1.0,
    "min_fact_pass_rate": 1.0,
    "max_fact_omission_rate": 0.0,
    "min_refusal_rate": 1.0,
}


def _rate(numerator: int, denominator: int, *, empty: float = 1.0) -> float:
    # Keep full precision for release thresholds: rounding must not hide a miss.
    return numerator / denominator if denominator else empty


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _evidence_set(case_result: dict) -> set[tuple[str, int, tuple[str, ...]]]:
    document_names = {
        document_id: filename
        for filename, document_id in case_result.get("document_ids", {}).items()
    }
    signatures = set()
    for evidence in case_result.get("response", {}).get("evidence", []):
        signatures.add(
            (
                document_names.get(evidence.get("document_id"), evidence.get("document_id", "")),
                int(evidence.get("page_number") or 0),
                tuple(evidence.get("block_ids") or []),
            )
        )
    return signatures


def _mean_pairwise_jaccard(sets: list[set]) -> float | None:
    if len(sets) < 2:
        return None
    scores = []
    for left, right in combinations(sets, 2):
        union = left | right
        scores.append(len(left & right) / len(union) if union else 1.0)
    return round(statistics.mean(scores), 4)


def summarize(dataset: dict, reports: list[dict], report_paths: list[str] | None = None) -> dict:
    if not reports:
        raise ValueError("At least one comparison report is required")
    cases = {case["case_id"]: case for case in dataset["cases"]}
    if not cases or len(cases) != len(dataset["cases"]):
        raise ValueError("The dataset must contain non-empty, unique case IDs")
    thresholds = {**DEFAULT_THRESHOLDS, **dataset.get("stability_thresholds", {})}
    failures: list[str] = []
    expected_metadata = (
        dataset["dataset_id"],
        dataset["expected_provider"],
        dataset["required_retrieval_mode"],
    )
    for index, report in enumerate(reports, start=1):
        metadata = (
            report.get("dataset_id"),
            report.get("provider"),
            report.get("required_retrieval_mode"),
        )
        if metadata != expected_metadata:
            failures.append(f"round_{index}:metadata_mismatch")
        if not report.get("model") or report.get("model") != reports[0].get("model"):
            failures.append(f"round_{index}:model_mismatch")

    grouped: dict[str, list[dict]] = {case_id: [] for case_id in cases}
    for report_index, report in enumerate(reports, start=1):
        case_results = report.get("cases", [])
        by_id = {item["case_id"]: item for item in case_results}
        if len(by_id) != len(case_results):
            failures.append(f"round_{report_index}:duplicate_case")
        for unexpected in sorted(set(by_id) - set(cases)):
            failures.append(f"round_{report_index}:unexpected_case:{unexpected}")
        for case_id in cases:
            if case_id not in by_id:
                failures.append(f"round_{report_index}:missing_case:{case_id}")
                continue
            grouped[case_id].append(by_id[case_id])

    total_case_runs = 0
    passed_case_runs = 0
    total_fact_observations = 0
    passed_fact_observations = 0
    total_refusal_observations = 0
    passed_refusal_observations = 0
    case_summaries = []
    for case_id, case in cases.items():
        observations = grouped[case_id]
        expected_observations = len(reports)
        total_case_runs += expected_observations
        passed_case_runs += sum(bool(item.get("passed")) for item in observations)
        fact_ids = [fact["fact_id"] for fact in case.get("required_claim_facts", [])]
        fact_ids.extend(fact["fact_id"] for fact in case.get("required_cross_document_facts", []))
        fact_rates = {}
        for fact_id in fact_ids:
            hits = sum(bool(item.get("facts", {}).get(fact_id)) for item in observations)
            total_fact_observations += expected_observations
            passed_fact_observations += hits
            fact_rates[fact_id] = {
                "hits": hits,
                "observations": len(observations),
                "expected_observations": expected_observations,
                "hit_rate": _rate(hits, expected_observations),
            }
        refusal = None
        if case.get("must_refuse"):
            refusal_hits = sum(
                bool(item.get("passed"))
                and bool(item.get("response", {}).get("insufficient_evidence"))
                and not item.get("response", {}).get("audit", {}).get("cross_document_claim_count")
                for item in observations
            )
            total_refusal_observations += expected_observations
            passed_refusal_observations += refusal_hits
            refusal = {
                "hits": refusal_hits,
                "observations": len(observations),
                "expected_observations": expected_observations,
                "rate": _rate(refusal_hits, expected_observations),
            }
        latencies = [int(item.get("latency_ms") or 0) for item in observations]
        claim_counts = [len(item.get("response", {}).get("claims", [])) for item in observations]
        cross_claim_counts = [
            int(item.get("response", {}).get("audit", {}).get("cross_document_claim_count") or 0)
            for item in observations
        ]
        evidence_sets = [_evidence_set(item) for item in observations]
        case_summaries.append(
            {
                "case_id": case_id,
                "expected_refusal": bool(case.get("must_refuse")),
                "rounds_observed": len(observations),
                "rounds_expected": expected_observations,
                "rounds_passed": sum(bool(item.get("passed")) for item in observations),
                "pass_rate": _rate(
                    sum(bool(item.get("passed")) for item in observations),
                    expected_observations,
                ),
                "fact_stability": fact_rates,
                "refusal_stability": refusal,
                "claim_count_range": [min(claim_counts), max(claim_counts)]
                if claim_counts
                else None,
                "cross_document_claim_count_range": (
                    [min(cross_claim_counts), max(cross_claim_counts)]
                    if cross_claim_counts
                    else None
                ),
                "evidence_pairwise_jaccard": _mean_pairwise_jaccard(evidence_sets),
                "latency_ms": {
                    "median": round(statistics.median(latencies)) if latencies else None,
                    "p95": _percentile(latencies, 0.95),
                    "maximum": max(latencies) if latencies else None,
                },
                "failure_counts": {
                    failure: sum(failure in item.get("failures", []) for item in observations)
                    for failure in sorted(
                        {failure for item in observations for failure in item.get("failures", [])}
                    )
                },
            }
        )

    rounds_passed = sum(report.get("status") == "passed" for report in reports)
    metrics = {
        "round_pass_rate": _rate(rounds_passed, len(reports)),
        "case_pass_rate": _rate(passed_case_runs, total_case_runs),
        "fact_pass_rate": _rate(passed_fact_observations, total_fact_observations),
        "fact_omission_rate": _rate(
            total_fact_observations - passed_fact_observations,
            total_fact_observations,
            empty=0.0,
        ),
        "refusal_rate": _rate(passed_refusal_observations, total_refusal_observations),
    }
    threshold_checks = {
        "round_pass_rate": metrics["round_pass_rate"] >= thresholds["min_round_pass_rate"],
        "case_pass_rate": metrics["case_pass_rate"] >= thresholds["min_case_pass_rate"],
        "fact_pass_rate": metrics["fact_pass_rate"] >= thresholds["min_fact_pass_rate"],
        "fact_omission_rate": (
            metrics["fact_omission_rate"] <= thresholds["max_fact_omission_rate"]
        ),
        "refusal_rate": metrics["refusal_rate"] >= thresholds["min_refusal_rate"],
    }
    failures.extend(f"threshold:{name}" for name, passed in threshold_checks.items() if not passed)
    return {
        "schema_version": "1.0",
        "dataset_id": dataset["dataset_id"],
        "model": reports[0].get("model"),
        "status": "passed" if not failures else "failed",
        "rounds_requested": len(reports),
        "rounds_passed": rounds_passed,
        "report_paths": report_paths or [],
        "thresholds": thresholds,
        "threshold_checks": threshold_checks,
        "metrics": metrics,
        "counts": {
            "case_observations": total_case_runs,
            "fact_observations": total_fact_observations,
            "fact_hits": passed_fact_observations,
            "fact_omissions": total_fact_observations - passed_fact_observations,
            "refusal_observations": total_refusal_observations,
            "refusal_hits": passed_refusal_observations,
        },
        "cases": case_summaries,
        "failures": list(dict.fromkeys(failures)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.reports]
    summary = summarize(dataset, reports, [str(path) for path in args.reports])
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.strict and summary["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
