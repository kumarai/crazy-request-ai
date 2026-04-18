from __future__ import annotations

from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from app.llm.ollama import normalize_ollama_base_url


class RepositoryConfig(BaseModel):
    repository_url: str
    directory: list[str] = ["*"]
    is_internal: bool = True
    credential_id: str = ""  # UUID referencing credentials table; empty = public
    branch: str = "main"
    wiki_enabled: bool = False
    schedule: str | None = None


class GenericSourceConfig(BaseModel):
    name: str
    source_type: str
    path: str
    credential_id: str = ""  # UUID referencing credentials table; empty = no auth
    schedule: str | None = None


class Settings(BaseSettings):
    # --- database ---
    database_url: str
    database_pool_min: int = 2
    database_pool_max: int = 15
    database_echo: bool = False

    # --- redis ---
    redis_url: str
    redis_cluster: bool = False

    # --- celery ---
    celery_broker_url: str
    celery_result_backend: str

    # --- llm provider ---
    llm_provider: str = "openai"  # "openai" | "anthropic" | "google" | "ollama"
    llm_embedding_dimensions: int = 1536

    # --- embedding / rerank providers (independent of chat provider) ---
    # "voyage" | "openai" | "google" | "ollama"
    llm_provider_embedding: str = "openai"
    # "llm" (scored via the chat provider)
    llm_provider_rerank: str = "llm"

    # --- voyage (managed embedding API) ---
    voyage_api_key: str = ""
    voyage_embedding_model: str = "voyage-code-3"

    # --- openai ---
    openai_api_key: str = ""

    # --- anthropic ---
    anthropic_api_key: str = ""

    # --- google ---
    google_project_id: str = ""
    google_location: str = "us-central1"
    google_api_key: str = ""

    # --- ollama ---
    ollama_base_url: str = ""   # e.g. "http://localhost:11434/v1" or remote URL
    ollama_api_key: str = ""    # sent as Authorization: Bearer <token> for Ollama/OpenAI-compatible auth

    # --- object storage (file uploads) ---
    storage_provider: str = "s3"          # "s3" (MinIO/S3/DO Spaces) or "gcs"
    storage_endpoint: str = ""            # internal endpoint (e.g. "http://minio:9000")
    storage_public_endpoint: str = ""     # browser-reachable (e.g. "http://localhost:9000")
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_bucket: str = "devknowledge"
    storage_region: str = "us-east-1"
    gcs_credentials_json: str = ""        # GCS service account JSON (optional)

    # --- security ---
    security_encryption_key: str = ""
    security_api_keys: list[str] = []          # query + read access
    security_admin_api_keys: list[str] = []    # source/credential/job mutation access
    security_rate_limit_per_minute: int = 60

    # --- rag ---
    rag_scope_threshold: float = 0.35
    rag_min_coverage_chunks: int = 2
    rag_vector_candidates: int = 40
    rag_bm25_candidates: int = 40
    rag_expansion_queries: int = 4
    rag_top_k_after_fusion: int = 20
    rag_top_k_final: int = 6

    # --- customer support ---
    support_history_budget_tokens: int = 4000
    support_verbatim_tail_turns: int = 4
    support_summary_max_tokens: int = 800
    support_compaction_trigger_pct: float = 0.8

    # --- ivfflat ---
    ivfflat_probes: int = 10
    ivfflat_lists: int = 100

    # --- indexing ---
    indexing_repos_base_dir: str = "/repos"
    indexing_generic_allowed_roots: list[str] = ["/data"]
    indexing_sync_schedule_cron: str = "0 2 * * *"

    # --- source configs (from TOML arrays) ---
    repositories: list[RepositoryConfig] = []
    generic_sources: list[GenericSourceConfig] = []

    @property
    def async_database_url(self) -> str:
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    model_config = SettingsConfigDict(
        toml_file="config.toml",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )


settings = Settings()

# Propagate API keys / base URLs to env so pydantic-ai Agent() picks them
# up at import time regardless of which provider is active.
import os as _os

if settings.openai_api_key:
    _os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
if settings.anthropic_api_key:
    _os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
if settings.google_api_key:
    _os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key)

# When Ollama is active, point the OpenAI SDK (and pydantic-ai) at the
# Ollama server. The SDK sends OPENAI_API_KEY as Authorization: Bearer <token>.
if settings.ollama_base_url and settings.llm_provider == "ollama":
    _os.environ.setdefault(
        "OPENAI_BASE_URL",
        normalize_ollama_base_url(settings.ollama_base_url),
    )
    _os.environ.setdefault("OPENAI_API_KEY", settings.ollama_api_key or "ollama")
