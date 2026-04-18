from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import settings
from app.llm.client import LLMClient

router = APIRouter()

# Valid values for the embed/rerank provider selectors. These are deliberately
# static — available_providers() on LLMClient only reflects *chat* providers
# whose SDK was initialised, while embed/rerank providers are independent
# configuration knobs the operator sets via env / config.toml.
_VALID_EMBED_PROVIDERS = {"voyage", "openai", "google", "ollama"}
_VALID_RERANK_PROVIDERS = {"llm"}


class LLMSettingsResponse(BaseModel):
    active_provider: str
    active_embedding_provider: str | None = None
    active_rerank_provider: str | None = None
    embedding_model: str | None = None
    embedding_dim: int | None = None
    rerank_model: str | None = None
    providers: list[dict]


class LLMSettingsUpdate(BaseModel):
    provider: str | None = None
    embedding_provider: str | None = None
    rerank_provider: str | None = None


def _build_response(client: LLMClient) -> LLMSettingsResponse:
    # Read current embedding/rerank config from Settings. These are configured
    # via env / config.toml and read-only at runtime (updates below flip an
    # attribute on the running settings object for the current process).
    embed_provider = getattr(settings, "llm_provider_embedding", None)
    rerank_provider = getattr(settings, "llm_provider_rerank", None)
    embedding_model = (
        getattr(settings, "voyage_embedding_model", None)
        if embed_provider == "voyage"
        else client.resolve_model("embedding")
    )
    rerank_model = client.resolve_model("rerank")
    return LLMSettingsResponse(
        active_provider=client.provider,
        active_embedding_provider=embed_provider,
        active_rerank_provider=rerank_provider,
        embedding_model=embedding_model,
        embedding_dim=settings.llm_embedding_dimensions,
        rerank_model=rerank_model,
        providers=client.available_providers,
    )


@router.get("/settings/llm")
async def get_llm_settings(request: Request) -> LLMSettingsResponse:
    client: LLMClient = request.app.state.llm_client
    return _build_response(client)


@router.put("/settings/llm")
async def update_llm_settings(
    body: LLMSettingsUpdate, request: Request
) -> LLMSettingsResponse:
    client: LLMClient = request.app.state.llm_client

    if body.provider is not None:
        valid = {p["id"] for p in client.available_providers}
        if body.provider not in valid:
            raise HTTPException(
                status_code=400,
                detail=f"Provider '{body.provider}' not available. Available: {sorted(valid)}",
            )
        client.provider = body.provider

    if body.embedding_provider is not None:
        if body.embedding_provider not in _VALID_EMBED_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid embedding_provider. Must be one of: {sorted(_VALID_EMBED_PROVIDERS)}",
            )
        # Runtime-only update. Persist via config.toml for cross-process durability.
        settings.llm_provider_embedding = body.embedding_provider  # type: ignore[attr-defined]

    if body.rerank_provider is not None:
        if body.rerank_provider not in _VALID_RERANK_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid rerank_provider. Must be one of: {sorted(_VALID_RERANK_PROVIDERS)}",
            )
        settings.llm_provider_rerank = body.rerank_provider  # type: ignore[attr-defined]

    return _build_response(client)
