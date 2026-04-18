from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from redis.asyncio.cluster import RedisCluster as AsyncRedisCluster
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.middleware.auth import APIKeyMiddleware
from app.api.middleware.logging import RequestLoggingMiddleware
from app.api.middleware.rate_limit import RateLimitMiddleware
from app.api.routes.auth import router as auth_router
from app.api.routes.support_actions import router as support_actions_router
from app.api.routes.support_cost import router as support_cost_router
from app.api.routes.chunks import router as chunks_router
from app.api.routes.credentials import router as credentials_router
from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.devchat import router as devchat_router
from app.api.routes.settings import router as settings_router
from app.api.routes.sources import router as sources_router
from app.api.routes.support import router as support_router
from app.api.routes.uploads import router as uploads_router
from app.config import settings
from app.db.pool import create_engine, create_session_factory
from app.indexing.pipeline import IndexingPipeline
from app.llm.client import LLMClient
from app.storage import create_storage_client
from app.support.mcp_client import build_mcp_client

# Configure structured JSON logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("[api]")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    engine = create_engine(
        settings.async_database_url,
        pool_size=settings.database_pool_max,
        pool_min=settings.database_pool_min,
        echo=settings.database_echo,
    )
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)

    if settings.redis_cluster:
        app.state.redis = AsyncRedisCluster.from_url(
            settings.redis_url, decode_responses=True
        )
    else:
        app.state.redis = aioredis.from_url(
            settings.redis_url, decode_responses=True
        )

    # Unified LLM client (supports OpenAI, Anthropic, Google, Ollama)
    llm_client = LLMClient.from_settings(settings)
    app.state.llm_client = llm_client

    # MCP client for customer-support tools (billing, outage, orders, ...)
    app.state.mcp_client = await build_mcp_client()
    from app.support.tools._mcp_bridge import set_mcp
    set_mcp(app.state.mcp_client)

    # Object storage (MinIO / S3 / GCS) for file uploads
    if settings.storage_endpoint or settings.storage_provider == "gcs":
        app.state.storage_client = create_storage_client(settings)
    else:
        app.state.storage_client = None

    pipeline = IndexingPipeline(
        app.state.session_factory, app.state.redis, llm_client
    )

    # Singleton bootstrap: only one worker runs this, others skip
    acquired = await app.state.redis.set(
        "bootstrap:lock", "1", nx=True, ex=300
    )
    if acquired:
        try:
            await pipeline.bootstrap_from_config()
        finally:
            await app.state.redis.delete("bootstrap:lock")
    else:
        logger.info("Bootstrap skipped — another worker holds the lock")

    logger.info(
        "Application started (LLM provider=%s)", llm_client.provider
    )
    yield

    # Shutdown
    await llm_client.close()
    await app.state.mcp_client.close()
    await engine.dispose()
    await app.state.redis.aclose()
    logger.info("Application shut down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Developer Knowledge Platform",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Middleware (order matters: outermost first). Credentials-aware CORS
    # so the signed ``support_session`` cookie can ride cross-origin
    # from the Vite dev server (3000) to the FastAPI server (8000).
    # Wildcard origins are incompatible with credentials; list dev + prod
    # origins explicitly. Additional origins can be added via env.
    dev_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    extra_origins = os.environ.get("CORS_EXTRA_ORIGINS", "")
    all_origins = dev_origins + [
        o.strip() for o in extra_origins.split(",") if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=all_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=[
            "X-API-Key", "Content-Type",
            "X-Customer-Id", "X-Conversation-Id", "X-New-Conversation",
        ],
        expose_headers=[
            "X-Conversation-Id", "X-Is-Guest", "X-Conversation-Rebound",
        ],
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(APIKeyMiddleware)

    # Routes
    app.include_router(health_router, prefix="/v1", tags=["Health"])
    app.include_router(devchat_router, prefix="/v1", tags=["DevChat"])
    app.include_router(sources_router, prefix="/v1", tags=["Sources"])
    app.include_router(credentials_router, prefix="/v1", tags=["Credentials"])
    app.include_router(chunks_router, prefix="/v1", tags=["Debug"])
    app.include_router(jobs_router, prefix="/v1", tags=["Jobs"])
    app.include_router(settings_router, prefix="/v1", tags=["Settings"])
    app.include_router(uploads_router, prefix="/v1", tags=["Uploads"])
    app.include_router(support_router, prefix="/v1", tags=["Support"])
    app.include_router(support_actions_router, prefix="/v1", tags=["Support"])
    app.include_router(support_cost_router, prefix="/v1", tags=["Support"])
    app.include_router(auth_router, prefix="/v1", tags=["Auth"])

    return app


app = create_app()
