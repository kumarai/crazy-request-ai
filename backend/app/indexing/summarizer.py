from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from app.indexing.parsers.base import ParsedChunk

if TYPE_CHECKING:
    from app.llm.client import LLMClient

logger = logging.getLogger("[indexing]")

_CODE_PROMPT = """\
You are indexing code for a semantic search system used by developers
finding existing code to reuse or extend.

Respond with ONLY valid JSON — no markdown, no code fences, no explanation:

{{
  "summary": "2-3 sentences: what this does, how it works, why it exists. Mention key libraries, patterns, base classes.",
  "purpose": "One specific sentence: exact problem solved. Not generic. Example: validates Stripe webhook signatures and updates subscription status in PostgreSQL",
  "signature": "clean signature with types: processPayment(dto: PaymentDto): Promise<PaymentResult>",
  "reuse_signal": "Use this when you need to [action] [technology] [domain]",
  "domain_tags": ["stripe", "payment", "webhook", "postgresql"],
  "complexity": "simple|moderate|complex",
  "imports_used": ["external-packages-only"]
}}

MANDATORY: reuse_signal MUST start with "Use this when you need to"
and include the specific action, technology, and business domain.
Example: "Use this when you need to process a Stripe payment intent and record the result in PostgreSQL with idempotency key deduplication"

Code ({qualified_name} in {file_path}):
```{language}
{content}
```"""

_WIKI_PROMPT = """\
Respond with ONLY valid JSON:
{{
  "summary": "2-3 sentences: what this section documents",
  "purpose": "One sentence: what does a developer learn from this?",
  "reuse_signal": "Read this when you need to [specific situation]",
  "domain_tags": ["labels"]
}}

Content ({qualified_name}):
{content}"""

_FALLBACK_CODE = {
    "summary": "No summary available",
    "purpose": "Purpose could not be determined",
    "signature": "",
    "reuse_signal": "Use this when you need to review this code",
    "domain_tags": [],
    "complexity": "moderate",
    "imports_used": [],
}

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2  # seconds

_FALLBACK_WIKI = {
    "summary": "No summary available",
    "purpose": "Purpose could not be determined",
    "reuse_signal": "Read this when you need context on this topic",
    "domain_tags": [],
}


class Summarizer:
    def __init__(
        self,
        llm: LLMClient,
        model: str = "gpt-4o-mini",
        max_concurrent: int = 8,
    ) -> None:
        self._llm = llm
        self._model = model
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def summarize_chunks(
        self, chunks: list[ParsedChunk]
    ) -> list[ParsedChunk]:
        tasks = [self._summarize_one(chunk) for chunk in chunks]
        return await asyncio.gather(*tasks)

    async def _summarize_one(self, chunk: ParsedChunk) -> ParsedChunk:
        async with self._semaphore:
            is_code = chunk.source_type == "code"
            content_text = (chunk.content_with_context or chunk.content)[:8000]

            if is_code:
                prompt = _CODE_PROMPT.format(
                    qualified_name=chunk.qualified_name,
                    file_path=chunk.file_path,
                    language=chunk.language,
                    content=content_text,
                )
                fallback = _FALLBACK_CODE
            else:
                prompt = _WIKI_PROMPT.format(
                    qualified_name=chunk.qualified_name,
                    content=content_text,
                )
                fallback = _FALLBACK_WIKI

            data = None
            for attempt in range(_MAX_RETRIES):
                try:
                    raw = await self._llm.chat(
                        messages=[{"role": "user", "content": prompt}],
                        model=self._model,
                        temperature=0,
                        max_tokens=600,
                    )
                    raw = raw.strip()
                    if raw.startswith("```"):
                        raw = raw.split("\n", 1)[-1]
                        if raw.endswith("```"):
                            raw = raw[:-3]
                    data = json.loads(raw)
                    break
                except json.JSONDecodeError as e:
                    logger.warning(
                        "Summary JSON parse failed for %s (attempt %d): %s",
                        chunk.qualified_name, attempt + 1, e,
                    )
                    break  # retrying won't fix bad JSON from a deterministic prompt
                except Exception as e:
                    if attempt < _MAX_RETRIES - 1:
                        delay = _RETRY_BASE_DELAY * (2 ** attempt)
                        logger.warning(
                            "Summary API error for %s (attempt %d, retry in %ds): %s",
                            chunk.qualified_name, attempt + 1, delay, e,
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.warning(
                            "Summary failed for %s after %d attempts: %s",
                            chunk.qualified_name, _MAX_RETRIES, e,
                        )
            if data is None:
                data = fallback

            chunk.summary = data.get("summary", fallback["summary"])
            chunk.purpose = data.get("purpose", fallback["purpose"])
            chunk.reuse_signal = data.get(
                "reuse_signal", fallback["reuse_signal"]
            )
            chunk.domain_tags = data.get("domain_tags", fallback["domain_tags"])

            if is_code:
                chunk.signature = data.get("signature", "")
                chunk.complexity = data.get("complexity", "moderate")
                chunk.imports_used = data.get("imports_used", [])

            return chunk
