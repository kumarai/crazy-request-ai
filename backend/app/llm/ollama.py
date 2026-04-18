from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def normalize_ollama_base_url(base_url: str) -> str:
    """Return an OpenAI-compatible Ollama base URL.

    Ollama's OpenAI-compatible endpoints live under ``/v1``. If the user
    supplies only a host (for example ``http://localhost:11434`` or
    ``https://ollama.example.com``), append ``/v1`` automatically.

    If a non-root path is already present, leave it unchanged so custom
    reverse-proxy layouts continue to work.
    """
    if not base_url:
        return ""

    parsed = urlsplit(base_url)
    path = parsed.path or ""
    if path in ("", "/"):
        return urlunsplit(
            (parsed.scheme, parsed.netloc, "/v1", parsed.query, parsed.fragment)
        )
    return base_url.rstrip("/")
