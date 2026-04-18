from __future__ import annotations

import logging
from datetime import datetime, timezone

from croniter import croniter

from app.db.repositories.checkpoints import CheckpointsRepository
from app.db.repositories.sources import SourcesRepository
from app.tasks.celery_app import celery_app
from app.tasks.worker_resources import ensure as _ensure

logger = logging.getLogger("[task]")


def _is_source_due(source: dict) -> bool:
    """Check if a source should be synced now based on its per-source schedule.

    Sources without a custom schedule are always due (governed by the global cron).
    Sources *with* a schedule are only due if ``croniter`` says the next fire time
    after ``last_synced_at`` is in the past.
    """
    schedule = (source.get("config") or {}).get("schedule")
    if not schedule:
        return True

    last_synced = source.get("last_synced_at")
    if not last_synced:
        return True  # never synced — always due

    if not isinstance(last_synced, datetime):
        return True  # can't compare, be safe

    now = datetime.now(timezone.utc)
    next_run = croniter(schedule, last_synced).get_next(datetime)
    # Make next_run tz-aware if croniter returned naive
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=timezone.utc)
    return now >= next_run


@celery_app.task(name="tasks.schedule_tasks.run_scheduled_sync")
def run_scheduled_sync() -> dict:
    """Load all active sources and dispatch index tasks as needed."""
    logger.info("[schedule] Running scheduled sync")

    res = _ensure()
    loop = res.loop

    sources_repo = SourcesRepository(res.session_factory)
    checkpoints_repo = CheckpointsRepository(res.session_factory)

    sources = loop.run_until_complete(sources_repo.list_active_sources())
    dispatched = 0
    skipped_schedule = 0
    skipped_running = 0

    for source in sources:
        source_id = str(source["id"])
        source_type = source["source_type"]

        # --- Per-source schedule gate ---
        if not _is_source_due(source):
            skipped_schedule += 1
            logger.debug("[schedule] Skipping %s — not due per its schedule", source_id)
            continue

        # --- Dedup: skip if a job is already pending/running ---
        latest_job = loop.run_until_complete(
            checkpoints_repo.get_latest_job(source["id"])
        )
        if latest_job and latest_job.get("status") in ("pending", "running"):
            skipped_running += 1
            logger.info(
                "[schedule] Skipping %s — already has a %s job",
                source_id,
                latest_job["status"],
            )
            continue

        # --- Determine mode ---
        checkpoint = loop.run_until_complete(
            checkpoints_repo.get_index_checkpoint(source["id"])
        )
        mode = "incremental" if checkpoint else "full"

        # --- Dispatch ---
        if source_type == "git_repo":
            from app.tasks.index_tasks import index_repo

            index_repo.apply_async(args=[source_id, mode], queue="indexing")
            dispatched += 1

        elif source_type == "gitlab_wiki":
            from app.tasks.index_tasks import index_wiki

            index_wiki.apply_async(args=[source_id, mode], queue="indexing")
            dispatched += 1

        elif source_type == "api":
            from app.tasks.index_tasks import index_api

            index_api.apply_async(args=[source_id, mode], queue="indexing")
            dispatched += 1

        elif source_type in ("support", "api_docs", "json"):
            from app.tasks.index_tasks import index_source

            index_source.apply_async(args=[source_id, mode], queue="indexing")
            dispatched += 1

    logger.info(
        "[schedule] Dispatched %d, skipped %d (schedule), %d (already running)",
        dispatched,
        skipped_schedule,
        skipped_running,
    )
    return {
        "dispatched": dispatched,
        "skipped_schedule": skipped_schedule,
        "skipped_running": skipped_running,
    }
