"""Unified LLM client supporting OpenAI, Anthropic, Google, and Ollama.

All provider SDKs are lazily imported so only the active one needs
to be installed.  Ollama uses the OpenAI SDK with a custom base_url,
so it requires no extra dependencies.

Embeddings fall back to OpenAI unless the active provider has its own
embedding support (Google, Ollama).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.llm.ollama import normalize_ollama_base_url

logger = logging.getLogger("[llm]")

# ── model defaults per provider ──────────────────────────────────────
MODEL_DEFAULTS: dict[str, dict[str, str]] = {
    "openai": {
        "generation": "gpt-4o",
        "summary": "gpt-4o-mini",
        "rerank": "gpt-4o-mini",
        "followup": "gpt-4o-mini",
        "embedding": "text-embedding-3-small",
        # Customer-support specialist slots
        "intent": "gpt-4o-mini",
        "router": "gpt-4o-mini",
        "smalltalk": "gpt-4o-mini",
        "technical": "gpt-4o",
        "billing": "gpt-4o",
        # Phase A/B specialists
        "general": "gpt-4o-mini",
        "outage": "gpt-4o-mini",
        "order": "gpt-4o-mini",
        "bill_pay": "gpt-4o",         # same stakes as billing info
        "appointment": "gpt-4o-mini",
    },
    "anthropic": {
        "generation": "claude-sonnet-4-20250514",
        "summary": "claude-haiku-4-5-20251001",
        "rerank": "claude-haiku-4-5-20251001",
        "followup": "claude-haiku-4-5-20251001",
        "embedding": "text-embedding-3-small",  # uses OpenAI fallback
        # Customer-support specialist slots
        "intent": "claude-haiku-4-5-20251001",
        "router": "claude-haiku-4-5-20251001",
        "smalltalk": "claude-haiku-4-5-20251001",
        "technical": "claude-sonnet-4-20250514",
        "billing": "claude-sonnet-4-20250514",
        # Phase A/B specialists
        "general": "claude-haiku-4-5-20251001",
        "outage": "claude-haiku-4-5-20251001",
        "order": "claude-haiku-4-5-20251001",
        "bill_pay": "claude-sonnet-4-20250514",
        "appointment": "claude-haiku-4-5-20251001",
    },
    "google": {
        "generation": "gemini-2.0-flash",
        "summary": "gemini-2.0-flash",
        "rerank": "gemini-2.0-flash",
        "followup": "gemini-2.0-flash",
        "embedding": "text-embedding-004",
        # Customer-support specialist slots
        "intent": "gemini-2.0-flash",
        "router": "gemini-2.0-flash",
        "smalltalk": "gemini-2.0-flash",
        "technical": "gemini-2.0-flash",
        "billing": "gemini-2.0-flash",
        # Phase A/B specialists
        "general": "gemini-2.0-flash",
        "outage": "gemini-2.0-flash",
        "order": "gemini-2.0-flash",
        "bill_pay": "gemini-2.0-flash",
        "appointment": "gemini-2.0-flash",
    },
    "ollama": {
        "generation": "llama3.1:8b",
        "summary": "llama3.1:8b",
        "rerank": "llama3.1:8b",
        "followup": "llama3.1:8b",
        "embedding": "nomic-embed-text-v2-moe",
        # Customer-support specialist slots
        "intent": "llama3.1:8b",
        "router": "llama3.1:8b",
        "smalltalk": "llama3.1:8b",
        "technical": "llama3.1:8b",
        "billing": "llama3.1:8b",
        # Phase A/B specialists
        "general": "llama3.1:8b",
        "outage": "llama3.1:8b",
        "order": "llama3.1:8b",
        "bill_pay": "llama3.1:8b",
        "appointment": "llama3.1:8b",
    },
}

_PYDANTIC_AI_PREFIX: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google-gla",
    "ollama": "openai",  # pydantic-ai talks to Ollama via OpenAI compat
}


class LLMClient:
    """Unified LLM client for chat completions and embeddings.

    Initialises SDK clients lazily so the application starts even if
    some provider packages are not installed.
    """

    def __init__(
        self,
        provider: str = "openai",
        openai_api_key: str = "",
        anthropic_api_key: str = "",
        google_project_id: str = "",
        google_location: str = "us-central1",
        google_api_key: str = "",
        ollama_base_url: str = "",
        ollama_api_key: str = "",
    ) -> None:
        self.provider = provider
        self._openai_key = openai_api_key
        self._anthropic_key = anthropic_api_key
        self._google_project_id = google_project_id
        self._google_location = google_location
        self._google_api_key = google_api_key
        self._raw_ollama_base_url = ollama_base_url
        self._ollama_base_url = normalize_ollama_base_url(ollama_base_url)
        self._ollama_api_key = ollama_api_key

        # SDK clients — created lazily on first use
        self._openai_client: Any | None = None
        self._anthropic_client: Any | None = None
        self._google_client: Any | None = None
        self._ollama_client: Any | None = None
        self._ollama_http_client: httpx.AsyncClient | None = None

        self._init_clients()

    # ── initialisation ───────────────────────────────────────────────

    def _init_clients(self) -> None:
        from openai import AsyncOpenAI

        # Always init OpenAI when a key exists (embedding fallback)
        if self._openai_key:
            self._openai_client = AsyncOpenAI(api_key=self._openai_key)

        # Ollama — OpenAI-compatible API. When behind a reverse proxy
        # (e.g. Cloudflare Access), the api_key is sent as a Bearer token.
        if self._ollama_base_url:
            if self._ollama_base_url != self._raw_ollama_base_url:
                logger.info(
                    "Normalizing Ollama base URL from %s to %s",
                    self._raw_ollama_base_url,
                    self._ollama_base_url,
                )
            self._ollama_client = AsyncOpenAI(
                base_url=self._ollama_base_url,
                api_key=self._ollama_api_key or "ollama",
            )
            self._ollama_http_client = httpx.AsyncClient(
                base_url=self._ollama_base_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {self._ollama_api_key or 'ollama'}",
                    "Accept": "application/json",
                    "User-Agent": "crazy-ai-ollama/1.0",
                },
                timeout=120,
            )

        if self._anthropic_key:
            try:
                from anthropic import AsyncAnthropic

                self._anthropic_client = AsyncAnthropic(api_key=self._anthropic_key)
            except ImportError:
                if self.provider == "anthropic":
                    raise ImportError(
                        "Anthropic provider selected but 'anthropic' package is not installed. "
                        "Run: pip install anthropic"
                    )

        if self._google_project_id or self._google_api_key:
            try:
                from google import genai

                self._google_client = genai.Client(
                    vertexai=bool(self._google_project_id),
                    project=self._google_project_id or None,
                    location=self._google_location if self._google_project_id else None,
                    api_key=self._google_api_key or None,
                )
            except ImportError:
                if self.provider == "google":
                    raise ImportError(
                        "Google provider selected but 'google-genai' package is not installed. "
                        "Run: pip install google-genai"
                    )

    @classmethod
    def from_settings(cls, settings: Any) -> "LLMClient":
        return cls(
            provider=settings.llm_provider,
            openai_api_key=settings.openai_api_key,
            anthropic_api_key=settings.anthropic_api_key,
            google_project_id=settings.google_project_id,
            google_location=settings.google_location,
            google_api_key=settings.google_api_key,
            ollama_base_url=settings.ollama_base_url,
            ollama_api_key=settings.ollama_api_key,
        )

    # ── model resolution ─────────────────────────────────────────────

    def resolve_model(self, role: str, provider: str | None = None) -> str:
        """Return the model name for *role* (generation, summary, rerank,
        followup, embedding) under the given *provider* (defaults to active)."""
        p = provider or self.provider
        return MODEL_DEFAULTS.get(p, MODEL_DEFAULTS["openai"]).get(role, "")

    def agent_model(self, role: str, provider: str | None = None) -> str:
        """Return the ``pydantic-ai`` model identifier (``provider:model``).

        For Ollama we return ``openai:<model>`` because pydantic-ai uses
        the OpenAI SDK under the hood.  The ``OPENAI_BASE_URL`` env var
        (set in config.py) tells it where to connect.
        """
        p = provider or self.provider
        model = self.resolve_model(role, p)
        prefix = _PYDANTIC_AI_PREFIX.get(p, "openai")
        return f"{prefix}:{model}"

    # ── chat completions ─────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        *,
        provider: str | None = None,
        temperature: float = 0,
        max_tokens: int = 1000,
    ) -> str:
        """Send a chat-completion request and return the text response."""
        p = provider or self.provider
        if p == "openai":
            return await self._openai_chat(messages, model, temperature, max_tokens)
        if p == "ollama":
            return await self._ollama_chat(messages, model, temperature, max_tokens)
        if p == "anthropic":
            return await self._anthropic_chat(messages, model, temperature, max_tokens)
        if p == "google":
            return await self._google_chat(messages, model, temperature, max_tokens)
        raise ValueError(f"Unknown LLM provider: {p}")

    # ── embeddings ───────────────────────────────────────────────────

    async def embed(
        self, texts: list[str], model: str | None = None
    ) -> list[list[float]]:
        """Embed a batch of texts."""
        model = model or self.resolve_model("embedding")

        if self.provider == "google" and self._google_client:
            return await self._google_embed(texts, model)

        if self.provider == "ollama" and self._ollama_http_client:
            return await self._ollama_embed(texts, model)

        if not self._openai_client:
            raise ValueError(
                "Embedding requires an OpenAI API key "
                "(or Google/Ollama configured as primary provider)"
            )
        resp = await self._openai_client.embeddings.create(model=model, input=texts)
        return [item.embedding for item in resp.data]

    # ── provider implementations ─────────────────────────────────────

    async def _openai_chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if not self._openai_client:
            raise ValueError("OpenAI client not initialised — check openai_api_key")
        resp = await self._openai_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    async def _ollama_chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if not self._ollama_http_client:
            raise ValueError("Ollama client not initialised — check ollama_base_url")
        resp = await self._ollama_http_client.post(
            "/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        return payload["choices"][0]["message"]["content"] or ""

    async def _ollama_embed(
        self, texts: list[str], model: str
    ) -> list[list[float]]:
        if not self._ollama_http_client:
            raise ValueError("Ollama client not initialised — check ollama_base_url")
        resp = await self._ollama_http_client.post(
            "/embeddings",
            json={"model": model, "input": texts},
        )
        resp.raise_for_status()
        payload = resp.json()
        return [item["embedding"] for item in payload["data"]]

    async def _anthropic_chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if not self._anthropic_client:
            raise ValueError("Anthropic client not initialised — check anthropic_api_key")

        system_parts: list[str] = []
        user_messages: list[dict] = []
        for msg in messages:
            if msg["role"] == "system":
                system_parts.append(msg["content"])
            else:
                user_messages.append({"role": msg["role"], "content": msg["content"]})

        if not user_messages:
            user_messages = [{"role": "user", "content": "Continue."}]

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": user_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)

        resp = await self._anthropic_client.messages.create(**kwargs)
        return resp.content[0].text

    async def _google_chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if not self._google_client:
            raise ValueError("Google client not initialised — check google config")

        from google.genai import types

        system_instruction: str | None = None
        contents: list[Any] = []
        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            else:
                role = "user" if msg["role"] == "user" else "model"
                contents.append(
                    types.Content(
                        role=role,
                        parts=[types.Part(text=msg["content"])],
                    )
                )

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system_instruction,
        )

        resp = await self._google_client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        return resp.text or ""

    async def _google_embed(
        self, texts: list[str], model: str
    ) -> list[list[float]]:
        if not self._google_client:
            raise ValueError("Google client not initialised")
        result = await self._google_client.aio.models.embed_content(
            model=model,
            contents=texts,
        )
        return [e.values for e in result.embeddings]

    # ── lifecycle ────────────────────────────────────────────────────

    async def close(self) -> None:
        if self._openai_client:
            await self._openai_client.close()
        if self._ollama_client:
            await self._ollama_client.close()
        if self._ollama_http_client:
            await self._ollama_http_client.aclose()
        if self._anthropic_client:
            await self._anthropic_client.close()

    # ── introspection (for settings API) ─────────────────────────────

    @property
    def available_providers(self) -> list[dict]:
        providers = []
        if self._openai_client:
            providers.append(
                {"id": "openai", "name": "OpenAI", "models": MODEL_DEFAULTS["openai"]}
            )
        if self._anthropic_client:
            providers.append(
                {"id": "anthropic", "name": "Anthropic", "models": MODEL_DEFAULTS["anthropic"]}
            )
        if self._google_client:
            providers.append(
                {"id": "google", "name": "Google Vertex AI", "models": MODEL_DEFAULTS["google"]}
            )
        if self._ollama_client:
            providers.append(
                {"id": "ollama", "name": "Ollama (local)", "models": MODEL_DEFAULTS["ollama"]}
            )
        return providers
