"""Worker-level singletons shared across all Celery tasks in a process.

Initialised once via ``worker_process_init`` signal, torn down on
``worker_process_shutdown``.  For tests or direct ``task.call()`` usage
the lazy :func:`ensure` helper bootstraps on first access.
"""
from __future__ import annotations

import asyncio
import logging
import os

import redis
import redis.cluster as redis_cluster
from celery.signals import worker_process_init, worker_process_shutdown
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

logger = logging.getLogger("[task]")


class WorkerResources:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.engine = None
        self.session_factory: async_sessionmaker[AsyncSession] | None = None
        self.redis_client: redis.Redis | None = None
        self.pipeline = None  # IndexingPipeline — set in init()

    @property
    def ready(self) -> bool:
        return self.loop is not None and not self.loop.is_closed()

    def init(self) -> None:
        # Deferred imports to avoid circular dependency at module load time
        from app.indexing.pipeline import IndexingPipeline
        from app.llm.client import LLMClient

        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine(
            settings.async_database_url,
            pool_size=3,
            max_overflow=2,
            echo=settings.database_echo,
        )
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        if settings.redis_cluster:
            self.redis_client = redis_cluster.RedisCluster.from_url(settings.redis_url)
        else:
            self.redis_client = redis.Redis.from_url(settings.redis_url)

        llm_client = LLMClient.from_settings(settings)
        self.pipeline = IndexingPipeline(
            self.session_factory, self.redis_client, llm_client
        )
        logger.info(
            "Worker resources initialised (pid=%d, llm_provider=%s)",
            os.getpid(),
            llm_client.provider,
        )

    def shutdown(self) -> None:
        if self.loop and not self.loop.is_closed():
            if self.engine:
                self.loop.run_until_complete(self.engine.dispose())
            self.loop.close()
        if self.redis_client:
            self.redis_client.close()
        logger.info("Worker resources shut down")


_resources = WorkerResources()


@worker_process_init.connect
def _on_worker_init(**_kwargs) -> None:
    _resources.init()


@worker_process_shutdown.connect
def _on_worker_shutdown(**_kwargs) -> None:
    _resources.shutdown()


def ensure() -> WorkerResources:
    """Return the singleton, lazily initialising if signals haven't fired."""
    if not _resources.ready:
        _resources.init()
    return _resources
