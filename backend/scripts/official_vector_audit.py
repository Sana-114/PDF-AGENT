"""Observe a dedicated acceptance collection without retrying or changing requests."""

import hashlib
import re
import traceback
from pathlib import Path
from time import perf_counter

from official_functional_inputs import write_json

OPERATIONS = {
    "collection_exists", "create_collection", "create_payload_index", "delete",
    "upsert", "count", "query_points", "get_collection", "scroll",
}


def error_chain(error):
    chain, seen = [], set()
    while isinstance(error, BaseException) and id(error) not in seen:
        seen.add(id(error))
        chain.append({"module": type(error).__module__, "type": type(error).__name__})
        error = error.__cause__ or getattr(error, "source", None) or error.__context__
    return chain


class AuditedQdrantClient:
    """No retries, timeout changes, extra network calls or collection cleanup."""

    def __init__(self, client, collection, records, save, output):
        if not collection.startswith("official_comparison_batch_"):
            raise ValueError("Audit only a dedicated official comparison collection")
        self.client, self.collection, self.records = client, collection, records
        self.save, self.output = save, Path(output)

    def __getattr__(self, name):
        method = getattr(self.client, name)
        if name not in OPERATIONS:
            return method

        def invoke(*args, **kwargs):
            collection = kwargs.get("collection_name", args[0] if args else None)
            if collection != self.collection:
                raise ValueError("Operation targets a collection outside this acceptance run")
            points = kwargs.get("points", []) if name == "upsert" else []
            row = {
                "operation": name, "status": "started", "automatic_retries": 0,
                "collection": collection, "point_count": len(points),
            }
            self.records.append(row)
            self.save()
            start = perf_counter()
            try:
                result = method(*args, **kwargs)
                row["status"] = "returned"
                return result
            except Exception as exc:
                row.update(status="failed", error_chain=error_chain(exc), frames=[
                    {"file": Path(frame.filename).name, "line": frame.lineno, "name": frame.name}
                    for frame in traceback.extract_tb(exc.__traceback__)[-16:]
                ])
                if name == "upsert":
                    try:
                        # Save the already-sent public batch, not keys, headers or
                        # a new embedding. A separate test can reproduce it later.
                        request = {"collection_name": collection, "wait": kwargs.get("wait"),
                                   "points": [p.model_dump(mode="json") for p in points]}
                        target = self.output / f"failed-upsert-{len(self.records):04d}.json"
                        write_json(target, request)
                        row.update(request_artifact=target.name, request_sha256=hashlib.sha256(
                            target.read_bytes()).hexdigest())
                    except Exception as snapshot_error:
                        row["snapshot_error_type"] = type(snapshot_error).__name__
                raise
            finally:
                row["duration_ms"] = round((perf_counter() - start) * 1000, 2)
                self.save()

        return invoke


class ReadOnlyAuditedQdrantClient(AuditedQdrantClient):
    """Allowlisted reads of one provenance-verified isolated runtime namespace.

    Deny unknown methods before accessing the underlying client, including raw
    HTTP handles. Payload verification must still precede any QA/cloud calls.
    """

    def __init__(self, client, collection, records, save, output):
        if not re.fullmatch(
            r"official_runtime_[a-f0-9]{32}_semantic_[a-f0-9]{10}_1024", collection
        ):
            raise ValueError("Read-only audit requires a known isolated runtime namespace")
        self.client, self.collection, self.records = client, collection, records
        self.save, self.output = save, Path(output)

    def __getattr__(self, name):
        if name not in {"collection_exists", "scroll", "count", "query_points", "get_collection"}:
            raise ValueError("Read-only audit forbids unapproved operation: " + name)
        return super().__getattr__(name)
