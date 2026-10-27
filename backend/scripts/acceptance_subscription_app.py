"""Isolated Celery transport probe; never use this bootstrap in production.

Only routing, result TTL and Beat interval are accelerated. The application
tasks, discovery requests, database writes and BGE responses are unchanged.
"""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from celery.signals import task_postrun, task_prerun

queue = os.environ.get("PDFAGENT_ACCEPTANCE_QUEUE", "")
if not re.fullmatch(r"acceptance_subscriptions_[a-f0-9]{32}", queue):
    raise RuntimeError("Requires an explicit isolated acceptance queue")
events = Path(os.environ["PDFAGENT_ACCEPTANCE_EVENTS"])

from app.workers.celery_app import celery_app  # noqa: E402

if not celery_app.conf.broker_url.endswith("/14"):
    raise RuntimeError("Only the dedicated acceptance Redis DB 14 is allowed")
celery_app.conf.update(
    task_always_eager=False,
    task_default_queue=queue,
    task_routes={"recommendations.*": {"queue": queue}},
    broker_transport_options={"global_keyprefix": queue + ":"},
    result_backend_transport_options={"global_keyprefix": queue + ":"},
    result_expires=3600,
    beat_schedule={
        "acceptance-periodic-refresh": {
            "task": "recommendations.refresh_all",
            "schedule": 15.0,
            "options": {"queue": queue},
        }
    },
    beat_max_loop_interval=1,
)


def record(stage, sender=None, task_id=None, **kwargs):
    if not sender or not sender.name.startswith("recommendations."):
        return
    payload = {
        "stage": stage,
        "task": sender.name,
        "task_id": task_id,
        "pid": os.getpid(),
        "at": datetime.now(UTC).isoformat(),
    }
    if stage == "finished":
        payload.update(state=kwargs.get("state"), result=kwargs.get("retval"))
    events.parent.mkdir(parents=True, exist_ok=True)
    with events.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


@task_prerun.connect
def before_task(sender=None, task_id=None, **kwargs):
    record("started", sender, task_id, **kwargs)


@task_postrun.connect
def after_task(sender=None, task_id=None, **kwargs):
    record("finished", sender, task_id, **kwargs)
