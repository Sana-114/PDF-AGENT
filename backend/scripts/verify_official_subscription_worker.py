"""Live arXiv/BGE + external Redis/Worker/Beat in an isolated public fixture.

Reuses an installed image with current app source mounted read-only. No image
build, production writes, private PDF, LLM, default queue or Redis FLUSH commands.
"""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from inspect_runtime_source import inspect_source
from official_functional_inputs import isolated_output, write_json
from verify_official_output_features import configure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = isolated_output(args.output_dir)
    project = Path(__file__).resolve().parents[2]
    token = uuid4().hex
    queue = "acceptance_subscriptions_" + token
    names = {kind: "pdfagent-acceptance-" + kind + "-" + token[:10] for kind in ("worker", "beat")}
    configure(root)
    os.environ.update(
        REDIS_URL="redis://127.0.0.1:7379/14",
        CELERY_TASK_ALWAYS_EAGER="false",
        LLM_PROVIDER="mock",
        LLM_API_KEY="",
        GITHUB_CODE_LOOKUP_LIMIT="0",
        EMBEDDING_PROVIDER="openai-compatible",
        EMBEDDING_MODEL="BAAI/bge-m3",
        EMBEDDING_DIMENSIONS="1024",
        EMBEDDING_BASE_URL="http://127.0.0.1:8001/v1",
        NO_PROXY="localhost,127.0.0.1,::1",
        PDFAGENT_ACCEPTANCE_QUEUE=queue,
        PDFAGENT_ACCEPTANCE_EVENTS=str(root / "worker-events.jsonl"),
    )
    report = {
        "status": "running",
        "runtime_source": inspect_source(project / "backend"),
        "checks": {},
        "cases": [],
        "cloud_calls": 0,
        "automatic_retries": 0,
        "production_library_modified": False,
        "production_containers_modified": False,
        "scope": "Live arXiv/BGE with real external Worker/Redis and 15s accelerated Beat; "
        "not long-duration/default-interval/multi-user/public-deployment acceptance",
        "queue": queue,
        "containers": names,
        "cleanup": {},
    }
    started = []

    def docker(*command, check=True):
        result = subprocess.run(
            ["docker", *command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        )
        if check and result.returncode:
            raise RuntimeError("Docker command failed: " + result.stderr.strip()[:500])
        return result.stdout.strip()

    def save():
        write_json(root / "report.json", report)

    def events():
        path = root / "worker-events.jsonl"
        if not path.exists():
            return []
        # Ignore only the currently incomplete last write while a task runs.
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.endswith("}")
        ]

    def wait_event(predicate, seconds=110):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            match = next((item for item in events() if predicate(item)), None)
            if match:
                return match
            time.sleep(0.25)
        raise TimeoutError("External task did not reach the expected state")

    def launch(kind):
        environment = {
            "DATABASE_URL": "sqlite:////app/data/acceptance.db",
            "DATA_DIR": "/app/data",
            "UPLOAD_DIR": "/app/data/uploads",
            "PARSED_DIR": "/app/data/parsed",
            "TRANSLATION_DIR": "/app/data/translations",
            "REDIS_URL": "redis://redis:6379/14",
            "CELERY_TASK_ALWAYS_EAGER": "false",
            "LLM_PROVIDER": "mock",
            "LLM_API_KEY": "",
            "VECTOR_SEARCH_ENABLED": "false",
            "GITHUB_CODE_LOOKUP_LIMIT": "0",
            "EMBEDDING_PROVIDER": "openai-compatible",
            "EMBEDDING_MODEL": "BAAI/bge-m3",
            "EMBEDDING_DIMENSIONS": "1024",
            "EMBEDDING_BASE_URL": "http://bge-embedding/v1",
            "NO_PROXY": "localhost,127.0.0.1,redis,bge-embedding",
            "PYTHONPATH": "/acceptance:/app",
            "PDFAGENT_ACCEPTANCE_QUEUE": queue,
            "PDFAGENT_ACCEPTANCE_EVENTS": "/app/data/worker-events.jsonl",
        }
        arguments = [
            "run",
            "-d",
            "--pull=never",
            "--name",
            names[kind],
            "--network",
            "pdfagent_default",
            "--label",
            "pdfagent.acceptance-run=" + token,
            "--mount",
            f"type=bind,source={project / 'backend/app'},target=/app/app,readonly",
            "--mount",
            f"type=bind,source={project / 'backend/scripts'},target=/acceptance,readonly",
            "--mount",
            f"type=bind,source={root},target=/app/data",
        ]
        for key, value in environment.items():
            arguments += ["-e", f"{key}={value}"]
        arguments += [
            "pdfagent-backend:latest",
            "celery",
            "-A",
            "acceptance_subscription_app:celery_app",
        ]
        if kind == "worker":
            arguments += [
                "worker",
                "--pool=solo",
                "--concurrency=1",
                "--queues=" + queue,
                "--hostname=acceptance@%h",
                "--loglevel=INFO",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
            ]
        else:
            arguments += [
                "beat",
                "--loglevel=INFO",
                "--schedule=/app/data/beat-state",
                "--max-interval=1",
            ]
        docker(*arguments)
        started.append(kind)

    try:
        size = docker("exec", "pdfagent-redis-1", "redis-cli", "-n", "14", "DBSIZE")
        if size != "0":
            raise ValueError("Dedicated Redis DB 14 must be empty; never clear existing data")
        from acceptance_subscription_app import celery_app
        from fastapi.testclient import TestClient

        from app.main import app

        launch("worker")
        worker_source = json.loads(
            docker(
                "exec",
                names["worker"],
                "python",
                "-c",
                "import json; from pathlib import Path; "
                "from inspect_runtime_source import inspect_source; "
                "print(json.dumps(inspect_source(Path('/app'))))",
            )
        )
        report["worker_source"] = worker_source
        assert worker_source["app_sha256"] == report["runtime_source"]["app_sha256"]
        report["checks"]["external_worker_current_app_source"] = True
        with TestClient(app) as client:
            records = {}
            for label, query, category, active in (
                ("active_a", "large language model", "cs.CL", True),
                ("active_b", "retrieval", "cs.IR", True),
                ("inactive", "scientific reasoning", "cs.AI", False),
            ):
                response = client.post(
                    "/api/v1/recommendations/subscriptions",
                    json={
                        "name": "Acceptance " + label,
                        "query": query,
                        "category": category,
                        "max_results": 2,
                        "active": active,
                        "refresh_now": False,
                    },
                )
                assert response.status_code == 201
                records[label] = response.json()
            write_json(root / "subscriptions-created.json", records)
            a, b = records["active_a"]["id"], records["active_b"]["id"]
            response = client.post(f"/api/v1/recommendations/subscriptions/{a}/refresh")
            assert response.status_code == 202 and response.json()["status"] == "queued"
            first = wait_event(
                lambda e: e["stage"] == "finished"
                and e.get("result", {}).get("subscription_id") == a
            )
            report["cases"].append({"case": "external-manual-refresh", "event": first})
            assert first["state"] == "SUCCESS" and first["result"]["error"] is None
            assert first["result"]["ranking_provider"] == "openai-compatible"
            assert first["result"]["ranking_warning"] is None
            feed = client.get("/api/v1/recommendations", params={"subscription_id": a}).json()
            write_json(root / "feed-first.json", feed)
            assert len(feed["items"]) == 2
            first_ids = {item["id"] for item in feed["items"]}
            for item, feedback in zip(feed["items"], ["like", "dislike"], strict=True):
                response = client.post(
                    f"/api/v1/recommendations/{item['id']}/feedback", json={"feedback": feedback}
                )
                assert response.status_code == 200 and response.json()["feedback"] == feedback
            report["checks"]["external_queue_not_eager_live_feed_and_bge"] = True
            save()
            print("External worker: real feed/BGE and feedback passed", flush=True)
            launch("beat")
            wait_event(
                lambda e: e["stage"] == "started" and e["task"] == "recommendations.refresh_all",
                seconds=45,
            )
            docker("stop", "--time", "5", names["beat"])
            periodic = wait_event(
                lambda e: e["stage"] == "finished" and e["task"] == "recommendations.refresh_all"
            )
            report["cases"].append({"case": "external-accelerated-beat", "event": periodic})
            assert periodic["state"] == "SUCCESS" and periodic["result"]["refreshed"] == 2
            results = periodic["result"]["results"]
            assert {item["subscription_id"] for item in results} == {a, b}
            assert all(
                item["error"] is None
                and item["ranking_provider"] == "openai-compatible"
                and item["ranking_warning"] is None
                for item in results
            )
            report["checks"]["real_beat_dispatch_active_only_live_bge"] = True
            after = client.get("/api/v1/recommendations", params={"subscription_id": a}).json()
            other = client.get("/api/v1/recommendations", params={"subscription_id": b}).json()
            write_json(root / "feed-after-beat-a.json", after)
            write_json(root / "feed-after-beat-b.json", other)
            assert {item["id"] for item in after["items"]} == first_ids
            assert {item["feedback"] for item in after["items"]} == {"like", "dislike"}
            assert len(other["items"]) == 2
            assert all(x["feedback"] == "neutral" for x in other["items"])
            assert not first_ids & {item["id"] for item in other["items"]}
            inactive = client.get(
                "/api/v1/recommendations", params={"subscription_id": records["inactive"]["id"]}
            ).json()
            assert inactive["total"] == 0
            report["checks"]["feedback_dedup_and_subscription_record_isolation"] = True
            assert (
                client.post(
                    "/api/v1/recommendations/subscriptions",
                    json={"name": " ", "query": "retrieval"},
                ).status_code
                == 422
            )
            assert (
                client.post(
                    "/api/v1/recommendations/subscriptions", json={"name": "Invalid", "query": ""}
                ).status_code
                == 422
            )
            assert client.get("/api/v1/recommendations", params={"limit": 0}).status_code == 422
            assert (
                client.post(
                    f"/api/v1/recommendations/{next(iter(first_ids))}/feedback",
                    json={"feedback": "invalid"},
                ).status_code
                == 422
            )
            report["checks"]["invalid_name_filter_limit_feedback_rejected"] = True
            assert client.delete(f"/api/v1/recommendations/subscriptions/{a}").status_code == 204
            assert (
                client.get("/api/v1/recommendations", params={"subscription_id": a}).json()["total"]
                == 0
            )
            assert (
                client.get("/api/v1/recommendations", params={"subscription_id": b}).json()["total"]
                == 2
            )
            assert (
                client.post(f"/api/v1/recommendations/subscriptions/{a}/refresh").status_code == 404
            )
            assert client.delete(f"/api/v1/recommendations/subscriptions/{a}").status_code == 404
            assert (
                client.post(
                    f"/api/v1/recommendations/{next(iter(first_ids))}/feedback",
                    json={"feedback": "like"},
                ).status_code
                == 404
            )
            report["checks"]["delete_cascade_does_not_touch_other_subscription"] = True
            assert (
                len(
                    [
                        e
                        for e in events()
                        if e["task"] == "recommendations.refresh_all" and e["stage"] == "finished"
                    ]
                )
                == 1
            )
            report["checks"]["one_real_scheduled_dispatch_no_task_retry"] = True
            # Inspect actual broker configuration, not a claimed eager result.
            assert not celery_app.conf.task_always_eager
        report["status"] = "passed_scoped_checks"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
    finally:
        for kind in reversed(started):
            try:
                actual = docker(
                    "inspect",
                    names[kind],
                    "--format",
                    '{{index .Config.Labels "pdfagent.acceptance-run"}}',
                )
                if actual != token:
                    raise RuntimeError("Container identity changed; refusing cleanup")
                # docker emits logs on stderr; fetch both streams for this own container.
                captured = subprocess.run(
                    ["docker", "logs", names[kind]],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                )
                (root / f"{kind}.log").write_text(
                    captured.stdout + captured.stderr, encoding="utf-8"
                )
                docker("stop", "--time", "5", names[kind])
                docker("rm", names[kind])
                report["cleanup"][kind] = "own isolated container removed; logs retained"
            except Exception as exc:
                report["cleanup"][kind] = type(exc).__name__ + ": " + str(exc)[:300]
        # Only this UUID namespace, after its own workers are stopped. No FLUSHDB.
        try:
            import redis

            with redis.Redis.from_url("redis://127.0.0.1:7379/14") as broker:
                keys = list(broker.scan_iter(match=queue + ":*"))
                if any(not key.startswith((queue + ":").encode()) for key in keys):
                    raise RuntimeError("Unsafe broker cleanup target")
                if keys:
                    broker.delete(*keys)
                report["cleanup"]["own_namespaced_redis_keys_removed"] = len(keys)
        except Exception as exc:
            report["cleanup"]["redis_error"] = type(exc).__name__
        save()
    print(
        json.dumps(
            {
                "status": report["status"],
                "checks": report["checks"],
                "error": report.get("error"),
                "cleanup": report["cleanup"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if report["status"] == "passed_scoped_checks" else 1


if __name__ == "__main__":
    raise SystemExit(main())
