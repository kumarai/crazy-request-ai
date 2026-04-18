from __future__ import annotations

import json
import logging
from uuid import UUID

from app.db.repositories.checkpoints import CheckpointsRepository
from app.db.repositories.sources import SourcesRepository
from app.tasks.celery_app import celery_app
from app.tasks.worker_resources import ensure as _ensure

logger = logging.getLogger("[task]")


# ------------------------------------------------------------------
# Shared task body
# ------------------------------------------------------------------
def _run_index_task(
    self,
    source_id: str,
    mode: str,
    job_id: str | None,
    index_fn_name: str,
):
    """Shared logic for all index tasks. Uses the job_id from the API caller
    instead of creating a duplicate row."""
    log_prefix = f"[task:{index_fn_name}:{source_id}:{self.request.id}]"
    logger.info("%s Starting (mode=%s, job_id=%s)", log_prefix, mode, job_id)

    res = _ensure()
    loop = res.loop
    redis_client = res.redis_client
    resolved_job_id: UUID | None = None

    sources_repo = SourcesRepository(res.session_factory)
    checkpoints_repo = CheckpointsRepository(res.session_factory)

    try:
        source = loop.run_until_complete(
            sources_repo.get_source(UUID(source_id))
        )
        if not source:
            raise ValueError(f"Source {source_id} not found")

        # Use existing job row if provided (from API), else create one (schedule)
        if job_id:
            resolved_job_id = UUID(job_id)
        else:
            job = loop.run_until_complete(
                checkpoints_repo.create_job(
                    UUID(source_id),
                    celery_task_id=self.request.id,
                    triggered_by="schedule",
                )
            )
            resolved_job_id = job["id"]

        loop.run_until_complete(
            checkpoints_repo.update_job_status(resolved_job_id, "running")
        )
        redis_client.publish(
            f"job:{resolved_job_id}", json.dumps({"status": "running"})
        )

        # Dispatch to the right pipeline method
        if index_fn_name == "index_repo":
            stats = loop.run_until_complete(
                res.pipeline.index_git_repo(source, mode=mode)
            )
        elif index_fn_name == "index_wiki":
            stats = loop.run_until_complete(
                res.pipeline.index_wiki(source, mode=mode)
            )
        elif index_fn_name == "index_api":
            stats = loop.run_until_complete(
                res.pipeline.index_api(source, mode=mode)
            )
        else:
            stats = loop.run_until_complete(
                res.pipeline.index_generic(source, mode=mode)
            )

        stats_dict = {
            "files_processed": stats.files_processed,
            "chunks_created": stats.chunks_created,
            "chunks_deleted": stats.chunks_deleted,
            "chunks_unchanged": getattr(stats, "chunks_unchanged", 0),
            "dependencies_created": getattr(stats, "dependencies_created", 0),
            "embedding_model": res.pipeline._embedder._model,
            "embedding_dim": _get_embedding_dim(),
            "errors": stats.errors,
        }
        loop.run_until_complete(
            checkpoints_repo.update_job_status(
                resolved_job_id, "done", stats=stats_dict
            )
        )
        loop.run_until_complete(sources_repo.update_last_synced(UUID(source_id)))
        redis_client.publish(
            f"job:{resolved_job_id}",
            json.dumps({"status": "done", "stats": stats_dict}),
        )

        logger.info("%s Done: %s", log_prefix, stats_dict)
        return stats_dict

    except Exception as exc:
        logger.error("%s Failed: %s", log_prefix, exc, exc_info=True)
        if resolved_job_id:
            try:
                loop.run_until_complete(
                    checkpoints_repo.update_job_status(
                        resolved_job_id, "failed", error=str(exc)
                    )
                )
                redis_client.publish(
                    f"job:{resolved_job_id}",
                    json.dumps({"status": "failed", "error": str(exc)}),
                )
            except Exception:
                pass
        self.retry(exc=exc, countdown=60 * 2**self.request.retries)


# ------------------------------------------------------------------
# Task definitions
# ------------------------------------------------------------------
@celery_app.task(bind=True, name="tasks.index_tasks.index_repo", max_retries=3)
def index_repo(
    self, source_id: str, mode: str = "incremental", job_id: str | None = None
) -> dict:
    return _run_index_task(self, source_id, mode, job_id, "index_repo")


@celery_app.task(bind=True, name="tasks.index_tasks.index_wiki", max_retries=3)
def index_wiki(
    self, source_id: str, mode: str = "incremental", job_id: str | None = None
) -> dict:
    return _run_index_task(self, source_id, mode, job_id, "index_wiki")


@celery_app.task(bind=True, name="tasks.index_tasks.index_api", max_retries=3)
def index_api(
    self, source_id: str, mode: str = "incremental", job_id: str | None = None
) -> dict:
    return _run_index_task(self, source_id, mode, job_id, "index_api")


@celery_app.task(bind=True, name="tasks.index_tasks.index_source", max_retries=3)
def index_source(
    self, source_id: str, mode: str = "incremental", job_id: str | None = None
) -> dict:
    return _run_index_task(self, source_id, mode, job_id, "index_source")


@celery_app.task(
    bind=True, name="tasks.index_tasks.reembed_source", max_retries=2
)
def reembed_source(
    self, source_id: str, job_id: str | None = None
) -> dict:
    """Rebuild embed_input + embedding for all existing chunks of a source.

    Keeps summary/purpose/reuse_signal intact — only touches the vector and
    the synthesized embed_input text. Use when switching embedding models.
    """
    log_prefix = f"[task:reembed:{source_id}:{self.request.id}]"
    logger.info("%s Starting (job_id=%s)", log_prefix, job_id)

    res = _ensure()
    loop = res.loop
    redis_client = res.redis_client
    resolved_job_id: UUID | None = None

    sources_repo = SourcesRepository(res.session_factory)
    checkpoints_repo = CheckpointsRepository(res.session_factory)

    try:
        source = loop.run_until_complete(
            sources_repo.get_source(UUID(source_id))
        )
        if not source:
            raise ValueError(f"Source {source_id} not found")

        if job_id:
            resolved_job_id = UUID(job_id)
        else:
            job = loop.run_until_complete(
                checkpoints_repo.create_job(
                    UUID(source_id),
                    celery_task_id=self.request.id,
                    triggered_by="schedule",
                )
            )
            resolved_job_id = job["id"]

        loop.run_until_complete(
            checkpoints_repo.update_job_status(resolved_job_id, "running")
        )
        redis_client.publish(
            f"job:{resolved_job_id}", json.dumps({"status": "running"})
        )

        stats = loop.run_until_complete(
            res.pipeline.reembed_source(source)
        )

        stats_dict = {
            "files_processed": stats.files_processed,
            "chunks_updated": stats.chunks_created,  # reembed reuses this field
            "errors": stats.errors,
            "embedding_model": res.pipeline._embedder._model,
            "embedding_dim": _get_embedding_dim(),
        }
        loop.run_until_complete(
            checkpoints_repo.update_job_status(
                resolved_job_id, "done", stats=stats_dict
            )
        )
        redis_client.publish(
            f"job:{resolved_job_id}",
            json.dumps({"status": "done", "stats": stats_dict}),
        )

        logger.info("%s Done: %s", log_prefix, stats_dict)
        return stats_dict

    except Exception as exc:
        logger.error("%s Failed: %s", log_prefix, exc, exc_info=True)
        if resolved_job_id:
            try:
                loop.run_until_complete(
                    checkpoints_repo.update_job_status(
                        resolved_job_id, "failed", error=str(exc)
                    )
                )
                redis_client.publish(
                    f"job:{resolved_job_id}",
                    json.dumps({"status": "failed", "error": str(exc)}),
                )
            except Exception:
                pass
        self.retry(exc=exc, countdown=60 * 2**self.request.retries)


def _get_embedding_dim() -> int:
    from app.config import settings

    return settings.llm_embedding_dimensions


@celery_app.task(bind=True, name="tasks.index_tasks.reindex_file", max_retries=2)
def reindex_file(self, file_path: str, source_id: str) -> dict:
    logger.info(
        "[task:reindex_file:%s:%s] Reindexing %s",
        source_id,
        self.request.id,
        file_path,
    )
    res = _ensure()
    try:
        res.loop.run_until_complete(res.pipeline.reindex_file(file_path, source_id))
        return {"file": file_path, "status": "done"}
    except Exception as exc:
        logger.error(
            "[task:reindex_file:%s:%s] Failed: %s",
            source_id,
            self.request.id,
            exc,
        )
        self.retry(exc=exc, countdown=60 * 2**self.request.retries)
