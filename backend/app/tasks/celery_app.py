from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import settings


def parse_cron(cron_str: str) -> dict:
    """Parse a 5-field cron string into celery crontab kwargs."""
    parts = cron_str.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron string: {cron_str!r}")
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day_of_month": parts[2],
        "month_of_year": parts[3],
        "day_of_week": parts[4],
    }


celery_app = Celery(
    "dev_knowledge_platform",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.include = [
    "app.tasks.index_tasks",
    "app.tasks.schedule_tasks",
]

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Key prefixes to isolate broker/backend on shared Redis DB 0
    # (GCP Memorystore only supports DB 0)
    broker_transport_options={"global_keyprefix": "celery:broker:"},
    result_backend_transport_options={"global_keyprefix": "celery:result:"},
    task_routes={
        "tasks.index_tasks.*": {"queue": "indexing"},
        "tasks.schedule_tasks.*": {"queue": "default"},
    },
)

celery_app.conf.beat_schedule = {
    "scheduled-sync": {
        "task": "tasks.schedule_tasks.run_scheduled_sync",
        "schedule": crontab(**parse_cron(settings.indexing_sync_schedule_cron)),
    }
}
