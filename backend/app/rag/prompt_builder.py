from __future__ import annotations

import logging

import tiktoken

from app.agents.models import RetrievedChunk

logger = logging.getLogger("[rag]")

# Token budgets — leaves headroom for the system prompt and response.
_DEFAULT_MAX_CONTEXT_TOKENS = 100_000  # safe for GPT-4o / Claude / Gemini
_RESERVED_FOR_RESPONSE = 4_096
_RESERVED_FOR_RULES = 500  # approximate size of the rules section


def _count_tokens(text: str, encoding: tiktoken.Encoding) -> int:
    return len(encoding.encode(text, disallowed_special=()))


class PromptBuilder:
    def __init__(self, max_context_tokens: int = _DEFAULT_MAX_CONTEXT_TOKENS) -> None:
        self._max_context = max_context_tokens
        try:
            self._enc = tiktoken.encoding_for_model("gpt-4o")
        except KeyError:
            self._enc = tiktoken.get_encoding("cl100k_base")

    def assemble(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        source_names: dict[str, str] | None = None,
    ) -> tuple[str, list[tuple[RetrievedChunk, str]]]:
        """Build the generation prompt.

        Returns (prompt, included_chunks) where each included_chunks entry is
        (chunk, rendered_block) — the exact text the generator sees for that
        chunk. Callers (e.g. faithfulness) should verify against this rendered
        text, not the raw chunk, since content may be truncated to fit budget.
        """
        source_names = source_names or {}

        # Budget = max context - response reserve - rules reserve - query
        query_tokens = _count_tokens(query, self._enc)
        budget = (
            self._max_context
            - _RESERVED_FOR_RESPONSE
            - _RESERVED_FOR_RULES
            - query_tokens
        )

        # Chunks are already sorted by score (descending) from reranker.
        # Greedily include highest-scored chunks until budget is exhausted.
        wiki_blocks: list[tuple[RetrievedChunk, str]] = []
        code_blocks: list[tuple[RetrievedChunk, str]] = []
        included: list[tuple[RetrievedChunk, str]] = []
        used_tokens = 0
        skipped = 0

        for chunk in chunks:
            block = self._format_chunk(chunk, source_names)
            block_tokens = _count_tokens(block, self._enc)

            if used_tokens + block_tokens > budget:
                # Try truncating the chunk content to fit remaining budget
                remaining = budget - used_tokens
                if remaining > 200:  # worth including a truncated version
                    block = self._format_chunk(
                        chunk, source_names, max_content_tokens=remaining - 100
                    )
                    block_tokens = _count_tokens(block, self._enc)
                    if used_tokens + block_tokens <= budget:
                        self._bucket(chunk, block, wiki_blocks, code_blocks)
                        included.append((chunk, block))
                        used_tokens += block_tokens
                        continue
                skipped += 1
                continue

            self._bucket(chunk, block, wiki_blocks, code_blocks)
            included.append((chunk, block))
            used_tokens += block_tokens

        if skipped:
            logger.info(
                "Prompt builder: included %d chunks (%d tokens), "
                "dropped %d to stay within %d-token budget",
                len(included),
                used_tokens,
                skipped,
                self._max_context,
            )

        # Assemble final prompt
        parts: list[str] = []

        if wiki_blocks:
            parts.append("## Architecture and Domain Context")
            parts.append("(From indexed documentation)\n")
            for _, block in wiki_blocks:
                parts.append(block)
                parts.append("\n---\n")

        if code_blocks:
            parts.append("## Relevant Existing Code")
            parts.append("(Follow these patterns exactly)\n")
            for _, block in code_blocks:
                parts.append(block)
                parts.append("\n---\n")

        parts.append("## Task")
        parts.append(query)
        parts.append("")
        parts.append("Rules:")
        parts.append("- Follow the architecture from the documentation above")
        parts.append(
            "- Use EXACTLY the same patterns and base classes as the code above"
        )
        parts.append(
            "- Reuse existing types, error classes, and utilities shown above"
        )
        parts.append(
            "- Cite every implementation decision: (source: file_path:line)"
        )
        parts.append(
            "- If context is insufficient for any part, say so explicitly"
        )

        return "\n".join(parts), included

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _bucket(
        chunk: RetrievedChunk,
        block: str,
        wiki: list[tuple[RetrievedChunk, str]],
        code: list[tuple[RetrievedChunk, str]],
    ) -> None:
        if chunk.source_type in ("wiki", "generic"):
            wiki.append((chunk, block))
        else:
            code.append((chunk, block))

    def _format_chunk(
        self,
        chunk: RetrievedChunk,
        source_names: dict[str, str],
        max_content_tokens: int | None = None,
    ) -> str:
        content = chunk.content_with_context or chunk.content

        # Optionally truncate content to fit budget
        if max_content_tokens is not None:
            tokens = self._enc.encode(content, disallowed_special=())
            if len(tokens) > max_content_tokens:
                content = self._enc.decode(tokens[:max_content_tokens]) + "\n// ... (truncated)"

        if chunk.source_type in ("wiki", "generic"):
            url = chunk.metadata.get("url", "")
            lines = [f"### {chunk.qualified_name}"]
            if chunk.purpose:
                lines.append(f"> {chunk.purpose}")
            if url:
                lines.append(f"> Source: {url}")
            lines.append("")
            lines.append(content)
            return "\n".join(lines)

        # Code chunk
        source_name = (
            source_names.get(chunk.source_id)
            or chunk.source_name
            or chunk.source_id
        )
        lang = chunk.language
        lines = [f"```{lang}"]
        lines.append(f"// [{chunk.chunk_type}] {chunk.qualified_name}")
        lines.append(
            f"// {chunk.file_path}:{chunk.start_line}-{chunk.end_line}"
            f"  |  source: {source_name}"
        )
        if chunk.purpose:
            lines.append(f"// Purpose: {chunk.purpose}")
        if chunk.reuse_signal:
            lines.append(f"// Use when: {chunk.reuse_signal}")
        lines.append(content)
        lines.append("```")
        return "\n".join(lines)
