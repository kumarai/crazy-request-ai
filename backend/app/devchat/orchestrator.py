"""Developer-RAG (devchat) orchestrator.

Mirrors ``app.support.orchestrator.SupportOrchestrator`` but for the
knowledge-base / dev Q&A flow:

    intent gate -> retrieve (with scope gate) -> generate ->
    parse code/wiki blocks -> faithfulness + followups (parallel) -> done

No router, no specialists, no conversation history persistence — this
endpoint is stateless and one-shot.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, AsyncGenerator

from pydantic_ai.usage import RunUsage

from app.agents.faithfulness_agent import faithfulness_agent
from app.agents.followup_agent import followup_agent
from app.agents.generation_agent import generation_agent
from app.agents.intent_agent import classify_intent
from app.agents.smalltalk_agent import smalltalk_agent
from app.config import settings
from app.db.repositories.chunks import ChunksRepository
from app.db.repositories.sources import SourcesRepository
from app.devchat.agents.off_topic_agent import devchat_off_topic_agent
from app.llm.client import LLMClient
from app.llm.pricing import UsageAccumulator
from app.rag.prompt_builder import PromptBuilder
from app.rag.retriever import Retriever
from app.streaming.events import (
    ChunkPreview,
    CodeEvent,
    DoneEvent,
    EventType,
    FollowupQuestion,
    FollowupsEvent,
    ScopeWarnEvent,
    SourcesEvent,
    TextEvent,
    ThinkingEvent,
    WikiEvent,
)
from app.streaming.sse import sse_event

logger = logging.getLogger("[devchat]")

_CODE_BLOCK_RE = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)
_WIKI_REF_RE = re.compile(r"\[wiki:\s*(.+?)\]\((.+?)\)")


class DevChatOrchestrator:
    def __init__(
        self,
        chunks_repo: ChunksRepository,
        sources_repo: SourcesRepository,
        llm_client: LLMClient,
        redis: Any,
    ) -> None:
        self._chunks_repo = chunks_repo
        self._sources_repo = sources_repo
        self._llm = llm_client
        self._redis = redis
        self._retriever = Retriever(
            chunks_repo, llm_client,
            sources_repo=sources_repo,
            redis=redis,
        )
        self._prompt_builder = PromptBuilder()

    async def stream(
        self,
        query: str,
        language: str = "typescript",
        source_ids: list[str] | None = None,
        top_k: int = 8,
        include_wiki: bool = True,
        include_code: bool = True,
        provider: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        start_time = time.time()
        active_provider = provider or self._llm.provider

        # Running LLM usage + cost across every agent call this turn —
        # intent, generation, faithfulness, followups. Reported in DoneEvent.
        turn_usage = UsageAccumulator()

        # Step 0: Intent fast-path. Short-circuits the full pipeline for
        # smalltalk ("hi") and off-topic ("what's the weather").
        intent_model = self._llm.agent_model("intent", active_provider)
        intent_decision, intent_usage = await classify_intent(
            query=query, model=intent_model
        )
        turn_usage.add(intent_usage, intent_model)

        if intent_decision.intent in ("smalltalk", "off_topic"):
            async for event in self._stream_cheap_reply(
                intent=intent_decision.intent,
                query=query,
                active_provider=active_provider,
                turn_usage=turn_usage,
                start_time=start_time,
            ):
                yield event
            return

        if intent_decision.intent == "capabilities":
            async for event in self._stream_capabilities_reply(
                turn_usage=turn_usage,
                start_time=start_time,
            ):
                yield event
            return

        # Step 1: Retrieve
        yield sse_event(
            EventType.THINKING,
            ThinkingEvent(
                stage="retrieving",
                message="Searching indexed sources...",
            ).model_dump_json(),
        )

        retrieval_start = time.time()
        try:
            chunks, scope_confidence, total_searched, top_score = (
                await self._retriever.retrieve(
                    query=query,
                    source_ids=source_ids,
                    language=language,
                    top_k=top_k,
                    include_wiki=include_wiki,
                    include_code=include_code,
                )
            )
        except Exception as e:
            logger.error("Retrieval failed: %s", e, exc_info=True)
            yield sse_event(
                EventType.ERROR,
                '{"type":"error","message":"Retrieval failed","code":"retrieval_failed"}',
            )
            yield sse_event(
                EventType.DONE,
                DoneEvent(
                    total_chunks_used=0,
                    sources_used=[],
                    faithfulness_passed=False,
                    latency_ms=int((time.time() - start_time) * 1000),
                ).model_dump_json(),
            )
            return

        # Step 2: Scope gate — compare raw top_score against threshold
        if top_score < settings.rag_scope_threshold:
            yield sse_event(
                EventType.SCOPE_WARN,
                ScopeWarnEvent(
                    message="Query is outside the scope of indexed sources"
                ).model_dump_json(),
            )
            yield sse_event(
                EventType.DONE,
                DoneEvent(
                    total_chunks_used=0,
                    sources_used=[],
                    faithfulness_passed=False,
                    latency_ms=int((time.time() - start_time) * 1000),
                ).model_dump_json(),
            )
            return

        # Step 3: Emit sources
        previews = [
            ChunkPreview(
                id=c.id,
                qualified_name=c.qualified_name,
                file_path=c.file_path,
                source_type=c.source_type,
                source_name=c.source_name,
                score=c.score,
                summary=c.summary or "",
                purpose=c.purpose or "",
                reuse_signal=c.reuse_signal or "",
            )
            for c in chunks
        ]
        yield sse_event(
            EventType.SOURCES,
            SourcesEvent(
                chunks=previews,
                total_searched=total_searched,
            ).model_dump_json(),
        )

        # Step 4: Generate
        yield sse_event(
            EventType.THINKING,
            ThinkingEvent(
                stage="generating",
                message="Generating response...",
            ).model_dump_json(),
        )

        retrieval_ms = int((time.time() - retrieval_start) * 1000)
        prompt, prompt_chunks = self._prompt_builder.assemble(query, chunks)

        generation_model = self._llm.agent_model("generation", active_provider)
        generation_start = time.time()
        try:
            result = await generation_agent.run(prompt, model=generation_model)
            full_response = result.output
            turn_usage.add(result.usage(), generation_model)
        except Exception as e:
            logger.error("Generation failed: %s", e, exc_info=True)
            yield sse_event(
                EventType.ERROR,
                '{"type":"error","message":"Generation failed","code":"generation_failed"}',
            )
            yield sse_event(
                EventType.DONE,
                DoneEvent(
                    total_chunks_used=len(chunks),
                    sources_used=list({c.source_name for c in chunks}),
                    faithfulness_passed=False,
                    latency_ms=int((time.time() - start_time) * 1000),
                    input_tokens=turn_usage.usage.input_tokens or None,
                    output_tokens=turn_usage.usage.output_tokens or None,
                    total_tokens=turn_usage.usage.total_tokens or None,
                    llm_requests=turn_usage.usage.requests or None,
                    cost_usd=turn_usage.cost_usd,
                ).model_dump_json(),
            )
            return

        # Step 5: Parse + emit response events
        code_blocks = _CODE_BLOCK_RE.findall(full_response)
        wiki_refs = _WIKI_REF_RE.findall(full_response)

        for lang, code in code_blocks:
            yield sse_event(
                EventType.CODE,
                CodeEvent(
                    language=lang or language,
                    content=code.strip(),
                    source_chunks=[c.qualified_name for c in chunks[:3]],
                ).model_dump_json(),
            )

        for title, url in wiki_refs:
            yield sse_event(
                EventType.WIKI,
                WikiEvent(title=title, url=url, excerpt="").model_dump_json(),
            )

        text_only = _CODE_BLOCK_RE.sub("", full_response)
        text_only = _WIKI_REF_RE.sub("", text_only).strip()
        if text_only:
            yield sse_event(
                EventType.TEXT,
                TextEvent(content=text_only).model_dump_json(),
            )

        generation_ms = int((time.time() - generation_start) * 1000)

        # Step 6: Faithfulness + followups in parallel
        yield sse_event(
            EventType.THINKING,
            ThinkingEvent(
                stage="checking",
                message="Verifying accuracy & generating next steps...",
            ).model_dump_json(),
        )

        validation_start = time.time()

        # Use the rendered blocks the generator saw — prompt_builder may
        # have truncated content to fit the token budget. Faithfulness
        # must check against what was actually shown.
        chunk_context = "\n---\n".join(
            f"[{c.qualified_name}] ({c.file_path}:{c.start_line}-{c.end_line})\n{block}"
            for c, block in prompt_chunks
        )
        faithfulness_prompt = (
            f"Query: {query}\n\n"
            f"Source context (this is what the generation agent saw):\n{chunk_context}\n\n"
            f"Generated response:\n{full_response}"
        )

        chunk_names = [c.qualified_name for c in chunks]
        followup_prompt = (
            f"Original query: {query}\n\n"
            f"Response:\n{full_response}\n\n"
            f"Retrieved context qualified names:\n{chr(10).join(chunk_names)}"
        )

        faithfulness_model = self._llm.agent_model("followup", active_provider)
        followup_model = self._llm.agent_model("followup", active_provider)

        async def _run_faithfulness() -> tuple[bool, RunUsage]:
            try:
                faith_result = await faithfulness_agent.run(
                    faithfulness_prompt, model=faithfulness_model
                )
                return faith_result.output.passed, faith_result.usage()
            except Exception as e:
                logger.error("Faithfulness check failed: %s", e, exc_info=True)
                return False, RunUsage()

        async def _run_followups() -> tuple[list[FollowupQuestion], RunUsage]:
            try:
                followup_result = await followup_agent.run(
                    followup_prompt, model=followup_model
                )
                return [
                    FollowupQuestion(question=q.question, category=q.category)
                    for q in followup_result.output.questions
                ], followup_result.usage()
            except Exception as e:
                logger.error("Followup generation failed: %s", e, exc_info=True)
                return [], RunUsage()

        (faithfulness_passed, faith_usage), (questions, followup_usage) = (
            await asyncio.gather(_run_faithfulness(), _run_followups())
        )
        turn_usage.add(faith_usage, faithfulness_model)
        turn_usage.add(followup_usage, followup_model)

        validation_ms = int((time.time() - validation_start) * 1000)

        if not faithfulness_passed:
            yield sse_event(
                EventType.SCOPE_WARN,
                ScopeWarnEvent(
                    message="Response may contain information not fully supported by indexed sources"
                ).model_dump_json(),
            )

        if questions:
            yield sse_event(
                EventType.FOLLOWUPS,
                FollowupsEvent(questions=questions).model_dump_json(),
            )

        # Step 7: Done
        yield sse_event(
            EventType.DONE,
            DoneEvent(
                total_chunks_used=len(chunks),
                sources_used=list({c.source_name for c in chunks}),
                faithfulness_passed=faithfulness_passed,
                latency_ms=int((time.time() - start_time) * 1000),
                retrieval_ms=retrieval_ms,
                generation_ms=generation_ms,
                validation_ms=validation_ms,
                input_tokens=turn_usage.usage.input_tokens or None,
                output_tokens=turn_usage.usage.output_tokens or None,
                total_tokens=turn_usage.usage.total_tokens or None,
                llm_requests=turn_usage.usage.requests or None,
                cost_usd=turn_usage.cost_usd,
            ).model_dump_json(),
        )

    async def _stream_capabilities_reply(
        self,
        *,
        turn_usage: UsageAccumulator,
        start_time: float,
    ) -> AsyncGenerator[dict, None]:
        """Static capabilities summary for "what can you do?" turns.

        No LLM call, no retrieval. Devchat is English-only, so the
        reply isn't language-gated like the support version.
        """
        reply = (
            "I can help you with:\n\n"
            "• **Codebase questions** — find functions, classes, and "
            "patterns across your indexed repositories\n"
            "• **Documentation** — answer questions from your wiki "
            "and indexed docs\n"
            "• **Code generation** — write code that follows the "
            "patterns in your existing codebase\n"
            "• **Code explanations** — explain what specific functions "
            "or modules do, and how they fit together\n\n"
            "Try asking something specific about your indexed sources "
            "to get started."
        )

        yield sse_event(
            EventType.TEXT,
            TextEvent(content=reply).model_dump_json(),
        )
        yield sse_event(
            EventType.DONE,
            DoneEvent(
                total_chunks_used=0,
                sources_used=[],
                faithfulness_passed=True,
                latency_ms=int((time.time() - start_time) * 1000),
                input_tokens=turn_usage.usage.input_tokens or None,
                output_tokens=turn_usage.usage.output_tokens or None,
                total_tokens=turn_usage.usage.total_tokens or None,
                llm_requests=turn_usage.usage.requests or None,
                cost_usd=turn_usage.cost_usd,
            ).model_dump_json(),
        )

    async def _stream_cheap_reply(
        self,
        *,
        intent: str,
        query: str,
        active_provider: str,
        turn_usage: UsageAccumulator,
        start_time: float,
    ) -> AsyncGenerator[dict, None]:
        """Cheap streamed reply for smalltalk / off_topic. No retrieval."""
        if intent == "smalltalk":
            agent = smalltalk_agent
            fallback_text = (
                "Hi! Ask me anything about your indexed sources and I'll dig in."
            )
        else:
            agent = devchat_off_topic_agent
            fallback_text = (
                "I can't help with that, but I can answer questions about "
                "your indexed source code, docs, and wiki content."
            )

        cheap_model = self._llm.agent_model("smalltalk", active_provider)
        generation_start = time.time()
        full_response = ""
        try:
            async with agent.run_stream(
                query, model=cheap_model
            ) as stream_result:
                async for chunk in stream_result.stream_text(delta=True):
                    if not chunk:
                        continue
                    full_response += chunk
                    yield sse_event(
                        EventType.TEXT,
                        TextEvent(content=chunk).model_dump_json(),
                    )
                turn_usage.add(stream_result.usage(), cheap_model)
        except Exception as e:
            logger.error("Cheap reply agent failed (%s): %s", intent, e, exc_info=True)
            full_response = fallback_text
            yield sse_event(
                EventType.TEXT,
                TextEvent(content=full_response).model_dump_json(),
            )

        generation_ms = int((time.time() - generation_start) * 1000)
        yield sse_event(
            EventType.DONE,
            DoneEvent(
                total_chunks_used=0,
                sources_used=[],
                faithfulness_passed=True,
                latency_ms=int((time.time() - start_time) * 1000),
                generation_ms=generation_ms,
                input_tokens=turn_usage.usage.input_tokens or None,
                output_tokens=turn_usage.usage.output_tokens or None,
                total_tokens=turn_usage.usage.total_tokens or None,
                llm_requests=turn_usage.usage.requests or None,
                cost_usd=turn_usage.cost_usd,
            ).model_dump_json(),
        )
