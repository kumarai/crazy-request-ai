"""Customer-support orchestrator with router + specialist dispatch.

Flow: resolve context -> load history -> route -> dispatch specialist ->
validate -> persist -> stream SSE. Supports single-hop handoff.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncGenerator
from uuid import UUID

from app.config import settings
from app.llm.pricing import UsageAccumulator
from app.db.repositories.chunks import ChunksRepository
from app.db.repositories.conversations import ConversationsRepository
from app.db.repositories.sources import SourcesRepository
from app.llm.client import LLMClient
from app.rag.prompt_builder import PromptBuilder
from app.rag.retriever import Retriever
from app.streaming.events import (
    ActionLink,
    ActionsEvent,
    CardItem,
    CardsEvent,
    ChunkPreview,
    EventType,
    FollowupQuestion,
    FollowupsEvent,
    InteractiveAction,
    InteractiveActionsEvent,
    SourcesEvent,
    SpecialistInfoEvent,
    SupportDoneEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
)
from app.support.action_registry import ActionRegistry
from app.support.agents.proposal_tools import (
    AUTH_REQUIRED_KINDS,
    extract_proposals,
)
from app.support.faq_cache import (
    CachedAnswer,
    FaqCache,
    is_cacheable_specialist,
    is_cacheable_tool_set,
    reply_looks_customer_specific,
)
from app.support.action_catalog import resolve_actions
from app.agents.intent_agent import classify_intent
from app.agents.language_agent import (
    UNSUPPORTED_LANGUAGE_REPLY,
    language_directive,
)
from app.agents.smalltalk_agent import smalltalk_agent
from app.agents.summarize_agent import summarize_agent
from app.streaming.sse import sse_event
from app.support.agents.off_topic_agent import support_off_topic_agent
from app.support.agents.registry import SPECIALIST_REGISTRY, get_specialist
from app.support.agents.router_agent import route as route_query
from app.support.agents.support_agent import (
    SupportAgentDeps,
    build_user_message,
)
from app.support.agents.synthesizer import (
    SynthesizerInput,
    build_synthesizer_message,
    synthesizer_agent,
)
from app.support.branch_executor import run_branch
from app.support.decomposer import Decomposition, decompose
from app.support.agents.validator_agent import (
    build_faithfulness_context,
    build_followups_actions_context,
    is_verifiable_response,
    support_faithfulness_agent,
    support_followups_actions_agent,
)
from app.support.customer_context import CustomerContext
from app.support.history import HistoryAssembler, HistoryContext
from app.support.retrieval_query import build_support_retrieval_query
from app.support.session_cache import SessionCache, SessionState

logger = logging.getLogger("[support]")

_STRONG_SINGLE_HIT_THRESHOLD = 0.85


_AFFIRMATIVE_RE = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok(?:ay)?|please|continue|"
    r"go ahead|do it|resume|sounds good|let'?s (?:go|do (?:it|that)))\b",
    re.IGNORECASE,
)


def _looks_affirmative(query: str) -> bool:
    """True when the customer's reply reads as "yes, proceed".

    Used to detect when a user is confirming a pending-intent resume
    offer from the account specialist ("Want me to continue your
    iPhone 16 Pro order?"). Kept narrow: we only replay on explicit
    affirmatives so an ambiguous follow-up never silently triggers a
    checkout / booking / payment path.
    """
    return bool(_AFFIRMATIVE_RE.match(query or ""))


def _cards_from_tool_outputs(tool_outputs: list[dict]) -> list[CardsEvent]:
    """Convert relevant tool responses into ``CardsEvent``s.

    Keeps specialist prompts simple — they don't need to emit cards
    explicitly; calling the right MCP tool is enough. The orchestrator
    inspects the captured tool outputs and turns the structured
    responses into typed card payloads for the frontend.
    """
    events: list[CardsEvent] = []
    for entry in tool_outputs:
        tool = entry.get("tool")
        output = entry.get("output") or {}
        if tool == "order_list_catalog":
            items = output.get("items") if isinstance(output, dict) else None
            items = items or (output if isinstance(output, list) else [])
            cards = [
                CardItem(
                    kind="product",
                    id=str(it.get("sku") or it.get("id") or ""),
                    title=str(it.get("name") or ""),
                    subtitle=str(it.get("summary") or ""),
                    image_url=it.get("image_url"),
                    badges=[f"${it['price']:.2f}"] if it.get("price") else [],
                    metadata={"category": it.get("category"), "price": it.get("price")},
                )
                for it in items
                if isinstance(it, dict)
            ]
            if cards:
                events.append(
                    CardsEvent(kind="product", prompt="Here's what's available:", cards=cards)
                )
        elif tool == "payment_method_list":
            items = output.get("items") if isinstance(output, dict) else None
            items = items or (output if isinstance(output, list) else [])
            cards = [
                CardItem(
                    kind="payment_method",
                    id=str(it.get("id") or ""),
                    title=str(it.get("label") or ""),
                    subtitle=f"{it.get('kind', '')} ending {it.get('last4', '')}",
                    badges=["Default"] if it.get("is_default") else [],
                    metadata={"kind": it.get("kind"), "last4": it.get("last4")},
                )
                for it in items
                if isinstance(it, dict)
            ]
            if cards:
                events.append(
                    CardsEvent(
                        kind="payment_method",
                        prompt="Which payment method would you like to use?",
                        cards=cards,
                    )
                )
        elif tool == "appointment_list_slots":
            items = output.get("items") if isinstance(output, dict) else None
            items = items or (output if isinstance(output, list) else [])
            cards = [
                CardItem(
                    kind="appointment_slot",
                    id=str(it.get("id") or ""),
                    title=str(it.get("slot_start") or ""),
                    subtitle=f"Tech: {it.get('tech_name', 'TBD')}",
                    badges=[it.get("topic", "")] if it.get("topic") else [],
                    metadata={
                        "slot_start": it.get("slot_start"),
                        "slot_end": it.get("slot_end"),
                    },
                )
                for it in items
                if isinstance(it, dict)
            ]
            if cards:
                events.append(
                    CardsEvent(
                        kind="appointment_slot",
                        prompt="Pick a time that works:",
                        cards=cards,
                    )
                )
        elif tool == "appointment_list":
            items = output.get("items") if isinstance(output, dict) else None
            items = items or (output if isinstance(output, list) else [])
            cards = [
                CardItem(
                    kind="appointment",
                    id=str(it.get("id") or ""),
                    title=str(it.get("slot_start") or ""),
                    subtitle=f"Tech: {it.get('tech_name', 'TBD')}",
                    badges=[
                        b for b in (
                            it.get("topic", ""),
                            it.get("status", ""),
                        ) if b
                    ],
                    metadata={
                        "topic": it.get("topic"),
                        "slot_start": it.get("slot_start"),
                        "slot_end": it.get("slot_end"),
                        "status": it.get("status"),
                    },
                )
                for it in items
                if isinstance(it, dict)
            ]
            if cards:
                events.append(
                    CardsEvent(
                        kind="appointment",
                        prompt="Your upcoming appointments:",
                        cards=cards,
                    )
                )
    return events


def _format_history_for_recap(history: HistoryContext) -> str:
    """Render loaded history as grounding text for the summarize agent.

    Includes the rolling summary (compacted older turns) plus the
    verbatim recent turns. The agent is instructed to ground only in
    this text. Returns a stable "Conversation so far: (none yet)"
    string when the chat is empty so the agent can say so naturally.
    """
    parts: list[str] = []
    if history.rolling_summary:
        parts.append(f"Earlier in this chat (summary):\n{history.rolling_summary}")

    turn_lines = []
    for turn in history.recent_turns:
        role = turn.get("role", "unknown")
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        turn_lines.append(f"{role}: {content}")

    if turn_lines:
        parts.append("Recent messages:\n" + "\n".join(turn_lines))

    if not parts:
        return "Conversation so far: (no prior messages in this chat yet)"
    return "Conversation so far:\n\n" + "\n\n".join(parts)


def _parse_faithfulness_output(output: str) -> bool:
    """Parse the faithfulness agent's JSON output. Defaults to False on
    parse failure (fail closed)."""
    try:
        parsed = json.loads(output)
        return bool(parsed.get("faithful", False))
    except json.JSONDecodeError:
        # Last-ditch keyword sniff so a model that returns prose around
        # the JSON still gives us a usable signal.
        lower = output.lower()
        return '"faithful"' in lower and "true" in lower


def _parse_followups_actions_output(
    output: str,
) -> tuple[list[FollowupQuestion], list[ActionLink]]:
    """Parse the followups+actions agent's JSON output. Each field
    degrades independently — a malformed entry doesn't kill the whole
    list."""
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return [], []

    followups: list[FollowupQuestion] = []
    raw_followups = parsed.get("followups") or []
    if isinstance(raw_followups, list):
        for q in raw_followups:
            if not isinstance(q, dict):
                continue
            question = q.get("question")
            if not question:
                continue
            followups.append(
                FollowupQuestion(
                    question=question,
                    category=q.get("category") or "next_step",
                )
            )

    actions: list[ActionLink] = []
    raw_topics = parsed.get("action_topics") or []
    if isinstance(raw_topics, list):
        topic_strs = [t for t in raw_topics if isinstance(t, str)]
        if topic_strs:
            resolved = resolve_actions(topic_strs)
            actions = [
                ActionLink(
                    label=a.label,
                    topic=a.topic,
                    url=str(a.url) if a.url is not None else None,
                    inline_query=a.inline_query,
                )
                for a in resolved
            ]

    return followups, actions


def _strip_identifier_lines(block: str) -> str:
    """Drop identifier-bearing lines from a prompt_builder-rendered block.

    For wiki/generic chunks the renderer prepends:
      ``### {qualified_name}``
      ``> {purpose}``         (optional)
      ``> Source: {url}``     (optional)
      ``[blank]``
      ``{content}``

    Customer-facing replies should never reference qualified names or
    internal URLs, so we strip those lines and return only the body.
    Truncation markers from prompt_builder are kept.
    """
    out: list[str] = []
    for line in block.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("### ") or stripped.startswith("> "):
            continue
        out.append(line)
    # Collapse the leading blank left after stripping the header.
    while out and not out[0].strip():
        out.pop(0)
    return "\n".join(out)


def _build_knowledge_base_refusal(
    response_language: str,
    escalation_contact: dict | None,
) -> str:
    """Return the canned KB-only refusal used for unsupported support turns.

    This is the safe fallback when retrieval is out of scope or when a
    generated draft cannot be validated against retrieved context.
    """
    escalation_contact = escalation_contact or {}
    contact_value = escalation_contact.get("value", "")
    contact_hours = escalation_contact.get("hours", "24/7")
    contact_url = escalation_contact.get("url", "")

    if response_language == "es":
        return (
            "No tengo información específica sobre eso en nuestra base "
            "de conocimientos. Por favor comunícate con nuestro equipo "
            f"de soporte al {contact_value} (disponible {contact_hours})"
            + (f" o visita {contact_url}." if contact_url else ".")
        )

    return (
        "I don't have specific information about that in our "
        "knowledge base. Please contact our support team at "
        f"{contact_value} (available {contact_hours})"
        + (f" or visit {contact_url}." if contact_url else ".")
    )


def _passes_support_scope_gate(
    *,
    top_score: float,
    coverage: int,
    scope_threshold: float,
    min_coverage_chunks: int,
) -> bool:
    """Return ``True`` when support retrieval is strong enough to answer.

    The default rule still requires multiple in-scope chunks. The only
    exception is a single chunk that scores as an obvious exact match,
    which is common for compact support KBs that store one article per
    issue.
    """
    if top_score < scope_threshold:
        return False

    if coverage >= min_coverage_chunks:
        return True

    return coverage >= 1 and top_score >= max(
        scope_threshold,
        _STRONG_SINGLE_HIT_THRESHOLD,
    )


class SupportOrchestrator:
    def __init__(
        self,
        conversations_repo: ConversationsRepository,
        chunks_repo: ChunksRepository,
        sources_repo: SourcesRepository,
        session_cache: SessionCache,
        llm_client: LLMClient,
        redis: Any,
    ) -> None:
        self._conv_repo = conversations_repo
        self._chunks_repo = chunks_repo
        self._sources_repo = sources_repo
        self._cache = session_cache
        self._llm = llm_client
        self._redis = redis
        self._history = HistoryAssembler(
            conversations_repo, session_cache, llm_client
        )
        self._retriever = Retriever(
            chunks_repo, llm_client,
            sources_repo=sources_repo,
            redis=redis,
        )
        self._prompt_builder = PromptBuilder()

    async def stream(
        self,
        query: str,
        customer: CustomerContext,
        conversation_id: UUID,
        source_ids: list[str] | None = None,
        provider: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        start_time = time.time()
        active_provider = provider or self._llm.provider
        tools_called: list[str] = []
        # Running total (tokens + USD cost) across every LLM call in this
        # turn. Each ``add()`` takes the per-call usage + the model string so
        # cost can be computed against the right pricing row.
        turn_usage = UsageAccumulator()

        # Step 0: Intent + language classification. ONE LLM call returns
        # both. The hard-rule regex catches obvious English smalltalk
        # for free (and tags ``language="en"``); LLM fallback is one
        # small-model call that returns both fields. Doing this before
        # history loading lets us short-circuit "hi" / "thanks" turns
        # without the DB read or the potential compaction LLM call
        # inside ``maybe_compact``.
        intent_model = self._llm.agent_model("intent", active_provider)
        intent_decision, intent_usage = await classify_intent(
            query=query, model=intent_model
        )
        turn_usage.add(intent_usage, intent_model)

        # Unsupported language → bilingual rejection regardless of intent.
        # We don't have prompts/tooling for other languages and faking
        # English would be worse than a polite refusal.
        if intent_decision.language == "unsupported":
            logger.info(
                "Rejecting unsupported language for conv %s (intent was %s)",
                conversation_id,
                intent_decision.intent,
            )
            async for event in self._stream_unsupported_language_reply(
                conversation_id=conversation_id,
                query=query,
                turn_usage=turn_usage,
                start_time=start_time,
            ):
                yield event
            return

        response_language = intent_decision.language

        if intent_decision.intent in ("smalltalk", "off_topic"):
            # Smalltalk path: skip history load + cache write entirely.
            # The cache already holds the right state from the last real
            # turn; rewriting the same values back is a no-op.
            async for event in self._stream_cheap_reply(
                intent=intent_decision.intent,
                query=query,
                conversation_id=conversation_id,
                active_provider=active_provider,
                turn_usage=turn_usage,
                history=None,
                start_time=start_time,
                response_language=response_language,
            ):
                yield event
            return

        if intent_decision.intent == "capabilities":
            # User asked "what can you help with?" — answer with a
            # static capabilities summary, no LLM, no retrieval, no
            # tool calls. Used to misroute as a vague support query
            # that triggered ``get_recent_tickets`` and answered with
            # the wrong thing.
            async for event in self._stream_capabilities_reply(
                conversation_id=conversation_id,
                query=query,
                turn_usage=turn_usage,
                start_time=start_time,
                response_language=response_language,
            ):
                yield event
            return

        # Step 1: Load history (real-pipeline path only)
        yield sse_event(
            EventType.THINKING,
            ThinkingEvent(
                stage="loading", message="Loading conversation context..."
            ).model_dump_json(),
        )

        history = await self._history.load_context(conversation_id)
        history = await self._history.maybe_compact(conversation_id, history)

        # Pending intent: set on a prior guest-gated turn (see auth
        # gate below). If the customer has since signed in and is now
        # confirming a resume, we replay the stored query against the
        # original specialist. Read it here so both the resume check
        # and the account-agent deps injection can share one fetch.
        pending_intent: dict | None = None
        try:
            pending_intent = await self._conv_repo.get_pending_intent(
                conversation_id
            )
        except Exception as e:
            logger.error("Failed to load pending_intent: %s", e)

        if intent_decision.intent == "summarize":
            # User asked "summarize our chat" — stream a recap grounded
            # in the loaded history. No routing, no retrieval: the chat
            # transcript itself is the source. Goes after history load
            # (capabilities short-circuits before it; summarize needs it).
            async for event in self._stream_summarize_reply(
                conversation_id=conversation_id,
                query=query,
                active_provider=active_provider,
                turn_usage=turn_usage,
                history=history,
                start_time=start_time,
                response_language=response_language,
            ):
                yield event
            return

        # Step 1.8: Multi-agent decomposition (cold-start only).
        # Short continuation follow-ups ("80015", "yes", "use the Visa")
        # stay on the single-specialist path because the existing
        # router handles continuity via ``last_specialist``. When the
        # customer's opening message spans multiple specialist domains
        # (e.g. "fix my internet after my card got rejected and I paid
        # late"), we split it, run branches in parallel, and
        # synthesize. Decomposer returns a single sub-query for
        # single-topic questions — in that case we fall through to the
        # normal routing + validation + cards path so nothing regresses.
        _NON_TOPICAL_SPECIALISTS = {
            "smalltalk", "off_topic", "capabilities", "summarize",
            "unsupported_language",
        }
        should_decompose = (
            not history.last_specialist
            or history.last_specialist in _NON_TOPICAL_SPECIALISTS
        )
        if should_decompose:
            decomp_model = self._llm.agent_model("router", active_provider)
            decomposition, decomp_usage = await decompose(
                query, model=decomp_model
            )
            turn_usage.add(decomp_usage, decomp_model)
            if len(decomposition.sub_queries) > 1:
                logger.info(
                    "Multi-agent path: %d sub-queries for conv %s",
                    len(decomposition.sub_queries),
                    conversation_id,
                )
                async for event in self._stream_multi_agent_reply(
                    query=query,
                    customer=customer,
                    conversation_id=conversation_id,
                    decomposition=decomposition,
                    source_ids=source_ids,
                    history=history,
                    active_provider=active_provider,
                    turn_usage=turn_usage,
                    start_time=start_time,
                    response_language=response_language,
                ):
                    yield event
                return

        # Step 2: Route
        yield sse_event(
            EventType.THINKING,
            ThinkingEvent(
                stage="routing", message="Determining the right specialist..."
            ).model_dump_json(),
        )

        router_model = self._llm.agent_model("router", active_provider)
        decision, router_usage = await route_query(
            query=query,
            customer_plan=customer.plan,
            customer_services=customer.services,
            history_summary=history.rolling_summary,
            last_specialist=history.last_specialist,
            model=router_model,
        )
        turn_usage.add(router_usage, router_model)

        specialist_name = decision.specialist
        router_confidence = decision.confidence

        # Post-login resume: the account specialist offered to continue
        # a previously-gated intent ("Want me to continue your iPhone
        # order?"); an affirmative reply from a now-authed customer
        # short-circuits back to the original specialist with the
        # original query. Clear ``pending_intent_json`` so a later
        # ambiguous "yes" doesn't accidentally re-replay it.
        if (
            pending_intent
            and not customer.is_guest
            and history.last_specialist == "account"
            and _looks_affirmative(query)
            and pending_intent.get("specialist") in SPECIALIST_REGISTRY
        ):
            original_query = pending_intent.get("query") or query
            original_specialist = pending_intent["specialist"]
            logger.info(
                "Resuming pending intent: specialist=%s (original query %r)",
                original_specialist,
                original_query[:80],
            )
            specialist_name = original_specialist
            router_confidence = 1.0
            # Re-run retrieval/generation against the *original* question
            # so the specialist has the right context, not just a bare
            # "yes". The user's actual "yes" still gets persisted as the
            # user message below via ``query``.
            query = original_query
            try:
                await self._conv_repo.update_conversation(
                    conversation_id, pending_intent_json=None
                )
            except Exception as e:
                logger.error("Failed to clear pending_intent: %s", e)
            pending_intent = None

        # Step 1.5: FAQ cache — if this specialist + query is cacheable
        # and we have a hit, short-circuit. Skips router retrieval +
        # generation + validation entirely for common repeat questions
        # like "what can you help with" or "is there an outage".
        faq_cache = FaqCache(self._redis)
        cache_zip = customer.zip_code if specialist_name == "outage" else None
        cached = await faq_cache.get(specialist_name, query, zip_code=cache_zip)
        if cached is not None:
            logger.info(
                "FAQ cache hit: specialist=%s query=%r",
                specialist_name, query[:60],
            )
            async for event in self._stream_cached_reply(
                cached=cached,
                conversation_id=conversation_id,
                query=query,
                turn_usage=turn_usage,
                start_time=start_time,
                router_confidence=router_confidence,
            ):
                yield event
            return

        # Step 2.0: Auth gate. Guests hitting a specialist that needs an
        # authenticated customer (billing, bill_pay, order-placement,
        # appointments) are handed off to the ``account`` specialist.
        # The account agent explains the gate, proposes a Sign-in
        # button, and (post-login) offers to resume the original
        # intent. We stash ``pending_intent`` on the conversation so
        # the resume path after sign-in has the original request.
        _AUTH_GATED_SPECIALISTS = {"billing", "bill_pay", "appointment"}
        # ``order`` browses catalog freely as a guest; the write path
        # is blocked at the action endpoint, not here.
        if (
            customer.is_guest
            and specialist_name in _AUTH_GATED_SPECIALISTS
        ):
            logger.info(
                "Auth gate: guest hit %s — handing off to account "
                "specialist for sign-in",
                specialist_name,
            )
            try:
                await self._conv_repo.update_conversation(
                    conversation_id,
                    pending_intent_json={
                        "specialist": specialist_name,
                        "query": query,
                        "ts": time.time(),
                    },
                )
            except Exception as e:
                logger.error("Failed to persist pending_intent: %s", e)
            specialist_name = "account"
            router_confidence = 1.0

        # Step 2: Retrieve
        yield sse_event(
            EventType.THINKING,
            ThinkingEvent(
                stage="retrieving", message="Searching knowledge base..."
            ).model_dump_json(),
        )

        retrieval_query = build_support_retrieval_query(query)
        if retrieval_query != query:
            logger.info(
                "Support retrieval rewrite: %r -> %r",
                query,
                retrieval_query,
            )

        # Per-specialist top_k — narrower slicers save tokens on the
        # rerank stage and keep the specialist focused. Falls back to
        # the global default when the specialist didn't configure one.
        # Also stash ``requires_kb_grounding`` so the scope gate below
        # knows whether to refuse on a dry KB or let the specialist
        # run tool-driven.
        try:
            _pre_spec = get_specialist(specialist_name)
            effective_top_k = _pre_spec.top_k or settings.rag_top_k_final
            requires_kb = _pre_spec.requires_kb_grounding
        except KeyError:
            effective_top_k = settings.rag_top_k_final
            requires_kb = True

        retrieval_start = time.time()
        try:
            chunks, scope_confidence, total_searched, top_score = (
                await self._retriever.retrieve(
                    query=retrieval_query,
                    source_ids=source_ids or customer.allowed_source_ids or None,
                    language="text",
                    top_k=effective_top_k,
                    include_wiki=True,
                    include_code=False,
                    # HyDE is off for support: hypothetical-code
                    # generation produces nonsense for plain-English
                    # support KBs, and the code-flavored expansion
                    # prompt fetches wrong-domain candidates that the
                    # reranker has to filter back out. Hybrid search
                    # (multi-vector + BM25 + reranker) carries recall
                    # for typical support queries on its own.
                    use_hyde=False,
                    use_query_expansion=False,
                )
            )
        except Exception as e:
            logger.error("Support retrieval failed: %s", e, exc_info=True)
            yield sse_event(
                EventType.ERROR,
                json.dumps({
                    "type": "error",
                    "message": "Retrieval failed",
                    "code": "retrieval_failed",
                }),
            )
            yield sse_event(
                EventType.DONE,
                SupportDoneEvent(
                    total_chunks_used=0,
                    sources_used=[],
                    faithfulness_passed=False,
                    latency_ms=int((time.time() - start_time) * 1000),
                    conversation_id=str(conversation_id),
                    specialist_used=specialist_name,
                    router_confidence=router_confidence,
                    input_tokens=turn_usage.usage.input_tokens or None,
                    output_tokens=turn_usage.usage.output_tokens or None,
                    total_tokens=turn_usage.usage.total_tokens or None,
                    llm_requests=turn_usage.usage.requests or None,
                    cost_usd=turn_usage.cost_usd,
                ).model_dump_json(),
            )
            return

        retrieval_ms = int((time.time() - retrieval_start) * 1000)

        # Step 2.2: Observability — stream the chunks retrieval selected
        # (post-rerank, already trimmed to ``rag_top_k_final``). Emitted
        # before the scope gate so borderline answers still reveal what
        # the retriever considered. The frontend renders these under the
        # answer via the existing ``SourcesPanel``.
        source_previews = [
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
        if source_previews:
            yield sse_event(
                EventType.SOURCES,
                SourcesEvent(
                    chunks=source_previews,
                    total_searched=total_searched,
                ).model_dump_json(),
            )

        # Step 2.5: Pre-generation scope gate. EITHER condition triggers
        # a refusal:
        #   (a) top-scoring chunk below the scope threshold — nothing in
        #       the KB is strong enough to ground an answer.
        #   (b) fewer than ``rag_min_coverage_chunks`` chunks clear the
        #       threshold — one lone hit usually means the top chunk is
        #       a near-miss surrounded by unrelated neighbours, which is
        #       exactly the setup where the LLM stitches articles
        #       together and confabulates. Refuse rather than let the
        #       specialist paper over weak coverage.
        coverage = sum(
            1 for c in chunks if (c.score or 0) >= settings.rag_scope_threshold
        )
        # Scope gate only applies to knowledge-grounded specialists.
        # Tool-driven ones (appointment, bill_pay, order, outage) can
        # answer correctly with zero KB chunks — they rely on MCP tool
        # output for the facts they cite. Refusing them when the KB is
        # silent produces the "I don't have specific information"
        # response for legitimate requests like "I need to schedule an
        # appointment" (see Phase A bug report).
        scope_passes = _passes_support_scope_gate(
            top_score=top_score,
            coverage=coverage,
            scope_threshold=settings.rag_scope_threshold,
            min_coverage_chunks=settings.rag_min_coverage_chunks,
        )
        if requires_kb and not scope_passes:
            logger.info(
                "Support scope gate: top_score=%.3f coverage=%d/%d "
                "threshold=%.3f for conv %s — refusing (KB-grounded specialist)",
                top_score,
                coverage,
                settings.rag_min_coverage_chunks,
                settings.rag_scope_threshold,
                conversation_id,
            )
            try:
                spec_config = get_specialist(specialist_name)
            except KeyError:
                spec_config = get_specialist("general")
            from app.support.tools.escalation import get_escalation_contact

            escalation_contact = await get_escalation_contact(spec_config.domain)
            async for event in self._stream_out_of_scope_reply(
                conversation_id=conversation_id,
                query=query,
                turn_usage=turn_usage,
                history=history,
                start_time=start_time,
                response_language=response_language,
                escalation_contact=escalation_contact,
                specialist_name=specialist_name,
                router_confidence=router_confidence,
                retrieval_ms=retrieval_ms,
            ):
                yield event
            return
        if not requires_kb and not scope_passes:
            logger.info(
                "Tool-driven specialist %s running with empty KB "
                "(top_score=%.3f coverage=%d) — grounding via tools only",
                specialist_name, top_score, coverage,
            )
        if coverage < settings.rag_min_coverage_chunks:
            logger.info(
                "Support scope gate override: allowing strong single hit "
                "top_score=%.3f coverage=%d/%d threshold=%.3f for conv %s",
                top_score,
                coverage,
                settings.rag_min_coverage_chunks,
                settings.rag_scope_threshold,
                conversation_id,
            )

        # Build context from retrieved chunks. Strip identifier lines
        # the prompt_builder injects (qualified names, source URLs) so
        # internal IDs don't leak into customer-facing replies. The
        # support agent used to cite things like
        # "billing-porting-and-roaming.json" because we showed it those
        # identifiers. Truncation markers (`// ... (truncated)`) are
        # preserved.
        _prompt, prompt_chunks = self._prompt_builder.assemble(query, chunks)
        retrieved_context = "\n---\n".join(
            f"[Source {i}]\n{_strip_identifier_lines(block)}"
            for i, (_chunk, block) in enumerate(prompt_chunks, start=1)
        )

        # Step 3: Run specialist (+ possible single-hop handoff).
        #
        # We buffer the generated draft and validate it before emitting any
        # customer-visible text. Support answers must be grounded in the KB,
        # so showing an unsafe draft first and warning later is not good
        # enough.
        generation_start = time.time()
        tool_outputs: list[dict] = []
        draft_response: str = ""
        final_response: str = ""
        grounding_fallback_used = False

        # Support at most one handoff hop (MVP).
        for hop in range(2):
            try:
                spec_config = get_specialist(specialist_name)
            except KeyError:
                # Router can classify to Phase-B specialists that aren't
                # registered yet. Route those to ``general`` so the
                # customer still gets a reply — the orchestrator hands
                # unauthenticated users to the ``account`` specialist
                # earlier, so they never reach this fallback.
                logger.warning(
                    "Unknown specialist %s, falling back to general",
                    specialist_name,
                )
                specialist_name = "general"
                spec_config = get_specialist("general")

            agent = spec_config.agent
            specialist_model = self._llm.agent_model(
                spec_config.model_slot, active_provider
            )

            yield sse_event(
                EventType.SPECIALIST_INFO,
                SpecialistInfoEvent(
                    specialist=specialist_name, confidence=router_confidence
                ).model_dump_json(),
            )
            yield sse_event(
                EventType.THINKING,
                ThinkingEvent(
                    stage="generating",
                    message=f"Generating response ({specialist_name} specialist)...",
                ).model_dump_json(),
            )

            # Tools mutate tool_outputs via deps. On handoff the second hop
            # gets its own list so we only record the tools the final
            # specialist actually called.
            tool_outputs = []
            deps = SupportAgentDeps(
                customer=customer,
                history=history,
                retrieved_context=retrieved_context,
                tool_outputs=tool_outputs,
                # Only the ``account`` specialist reads this — it powers
                # the resume offer after a sign-in. Other specialists
                # ignore it.
                pending_intent=(
                    pending_intent if specialist_name == "account" else None
                ),
            )

            # Pre-fetch the specialist domain's escalation contact and
            # inject it into the user message — saves the tool round-trip
            # the agent used to make on every turn that mentioned escalation.
            from app.support.tools.escalation import get_escalation_contact

            escalation_contact = await get_escalation_contact(spec_config.domain)
            user_message = build_user_message(
                query,
                customer,
                history,
                retrieved_context,
                escalation_contact=escalation_contact,
                language_directive=language_directive(response_language),
            )

            hop_text = ""
            try:
                if spec_config.structured_handoff:
                    # Structured-output agents (e.g. outage → OutageOutput)
                    # can't use ``stream_text`` — pydantic-ai rejects it
                    # when the output is not a string. Run non-streaming
                    # and use the typed output directly. A one-shot
                    # ``await agent.run(...)`` is fine here since these
                    # replies are short.
                    run_result = await agent.run(
                        user_message, model=specialist_model, deps=deps
                    )
                    hop_output = run_result.output
                    turn_usage.add(run_result.usage(), specialist_model)
                else:
                    async with agent.run_stream(
                        user_message, model=specialist_model, deps=deps
                    ) as stream_result:
                        async for chunk in stream_result.stream_text(delta=True):
                            if not chunk:
                                continue
                            hop_text += chunk
                        # Stream is done — final output and usage are now available.
                        try:
                            hop_output = await stream_result.get_output()
                        except Exception:
                            # Some providers don't support get_output after
                            # stream_text; fall back to what we accumulated.
                            hop_output = hop_text
                        turn_usage.add(stream_result.usage(), specialist_model)
            except Exception as e:
                logger.error(
                    "Specialist %s failed: %s", specialist_name, e, exc_info=True
                )
                yield sse_event(
                    EventType.ERROR,
                    json.dumps(
                        {
                            "type": "error",
                            "message": "Generation failed",
                            "code": "generation_failed",
                        }
                    ),
                )
                yield sse_event(
                    EventType.DONE,
                    SupportDoneEvent(
                        total_chunks_used=len(chunks),
                        sources_used=list({c.source_name for c in chunks}),
                        faithfulness_passed=False,
                        latency_ms=int((time.time() - start_time) * 1000),
                        conversation_id=str(conversation_id),
                        specialist_used=specialist_name,
                        router_confidence=router_confidence,
                        input_tokens=turn_usage.usage.input_tokens or None,
                        output_tokens=turn_usage.usage.output_tokens or None,
                        total_tokens=turn_usage.usage.total_tokens or None,
                        llm_requests=turn_usage.usage.requests or None,
                        cost_usd=turn_usage.cost_usd,
                    ).model_dump_json(),
                )
                return

            # Specialists that opt into structured handoff return a
            # pydantic model with ``reply`` + ``handoff_to`` instead of
            # plain text. Read the handoff decision from the typed field
            # rather than regex-sniffing the reply string.
            structured_handoff_to: str | None = None
            if spec_config.structured_handoff and not isinstance(hop_output, str):
                try:
                    draft_response = getattr(hop_output, "reply", "") or hop_text
                    structured_handoff_to = getattr(hop_output, "handoff_to", None)
                except Exception:
                    draft_response = hop_text
            else:
                draft_response = hop_output if isinstance(hop_output, str) else hop_text

            handoff_to = structured_handoff_to or self._detect_handoff(
                draft_response, specialist_name
            )
            if handoff_to and hop == 0:
                logger.info(
                    "Handoff from %s to %s", specialist_name, handoff_to
                )
                yield sse_event(
                    EventType.THINKING,
                    ThinkingEvent(
                        stage="routing",
                        message=f"Transferring to {handoff_to} specialist...",
                    ).model_dump_json(),
                )
                specialist_name = handoff_to
                continue  # stream the next specialist

            if handoff_to and hop >= 1:
                # Two hops = give up, point to a human.
                from app.support.tools.escalation import get_escalation_contact

                contact = await get_escalation_contact(handoff_to)
                escalation_note = (
                    "\n\nI'm unable to fully resolve this across our specialist "
                    "areas. Please contact our support team for further "
                    f"assistance: {contact.get('value', 'our support line')} "
                    f"({contact.get('hours', '24/7')})"
                )
                draft_response += escalation_note
            break

        tools_called = [t["tool"] for t in tool_outputs]
        generation_ms = int((time.time() - generation_start) * 1000)

        # Step 4: Validate the draft before showing it to the customer.
        validation_start = time.time()
        verifiable = is_verifiable_response(draft_response)
        faith_model = self._llm.agent_model(
            spec_config.faithfulness_model_slot, active_provider
        )
        followups_model = self._llm.agent_model("followup", active_provider)

        async def _run_faithfulness() -> bool:
            if not verifiable:
                # Nothing to verify; don't burn a call.
                return True
            try:
                ctx = build_faithfulness_context(
                    query=query,
                    answer=draft_response,
                    retrieved_chunks=retrieved_context,
                    tool_outputs=tool_outputs,
                    history_summary=history.rolling_summary,
                    recent_turns=history.recent_turns,
                )
                result = await support_faithfulness_agent.run(
                    ctx, model=faith_model
                )
                turn_usage.add(result.usage(), faith_model)
                return _parse_faithfulness_output(result.output)
            except Exception as e:
                logger.error("Faithfulness check failed: %s", e, exc_info=True)
                return False  # fail closed

        async def _run_followups_actions() -> tuple[
            list[FollowupQuestion], list[ActionLink]
        ]:
            try:
                ctx = build_followups_actions_context(
                    query=query,
                    answer=draft_response,
                    history_summary=history.rolling_summary,
                )
                result = await support_followups_actions_agent.run(
                    ctx, model=followups_model
                )
                turn_usage.add(result.usage(), followups_model)
                return _parse_followups_actions_output(result.output)
            except Exception as e:
                logger.error(
                    "Followups+actions generation failed: %s", e, exc_info=True
                )
                return [], []

        followup_questions: list[FollowupQuestion] = []
        suggested_actions: list[ActionLink] = []
        response_is_grounded = True

        if verifiable:
            response_is_grounded, (
                followup_questions,
                suggested_actions,
            ) = await asyncio.gather(
                _run_faithfulness(),
                _run_followups_actions(),
            )

            if not response_is_grounded:
                # For tool-driven specialists, faithfulness is meant to
                # verify against tool outputs, not KB chunks. The KB
                # refusal doesn't make sense here — the specialist
                # already has the right answer from MCP. Log the
                # failure but keep the draft; we still persist the
                # ``response_is_grounded=False`` signal in DONE.
                if not requires_kb:
                    logger.warning(
                        "Tool-driven specialist %s failed faithfulness "
                        "for conv %s — keeping draft (no KB fallback)",
                        specialist_name, conversation_id,
                    )
                    final_response = draft_response
                else:
                    logger.warning(
                        "Support draft failed faithfulness for conv %s; sending KB refusal",
                        conversation_id,
                    )
                    final_response = _build_knowledge_base_refusal(
                        response_language, escalation_contact
                    )
                    followup_questions = []
                    suggested_actions = []
                    grounding_fallback_used = True
                    response_is_grounded = True
            else:
                final_response = draft_response
        else:
            final_response = draft_response

        validation_ms = int((time.time() - validation_start) * 1000)

        # Post-hoc tool transparency — one event per tool the final specialist
        # actually invoked. These render after the text, matching the
        # existing UI behavior even though the calls happened before output.
        yield sse_event(
            EventType.TEXT,
            TextEvent(content=final_response).model_dump_json(),
        )

        for tool_name in tools_called:
            yield sse_event(
                EventType.TOOL_CALL,
                ToolCallEvent(
                    tool_name=tool_name, status="success"
                ).model_dump_json(),
            )

        # Rich cards — auto-emit based on which tools ran. Covers the
        # common specialist flows:
        #   • order catalog browse -> product cards
        #   • bill_pay / order -> payment-method cards when picking a card
        #   • appointment scheduling -> slot cards
        # Specialists describe the choice in text; cards make it
        # tappable on the frontend.
        for card_event in _cards_from_tool_outputs(tool_outputs):
            yield sse_event(EventType.CARDS, card_event.model_dump_json())

        # Dynamic interactive actions — the specialist's write-proposing
        # tools (``propose_pay``, ``propose_place_order``, …) recorded
        # structured proposals in ``tool_outputs`` without mutating
        # anything. We now mint one entry per proposal in the Redis
        # ActionRegistry and surface the confirmation buttons. The
        # customer clicking a button is the single place where the
        # underlying MCP write tool actually runs.
        proposals = extract_proposals(tool_outputs)
        if proposals:
            action_registry = ActionRegistry(self._redis)
            interactive: list[InteractiveAction] = []
            for p in proposals:
                kind = p.get("kind", "")
                # Belt-and-suspenders: guests shouldn't reach here (the
                # auth gate earlier hands off to ``account``), but if a
                # specialist somehow proposed a write-action for a
                # guest, refuse at the mint step.
                if customer.is_guest and kind in AUTH_REQUIRED_KINDS:
                    logger.warning(
                        "Dropped write proposal for guest: kind=%s", kind
                    )
                    continue
                try:
                    action = await action_registry.create(
                        kind=kind,
                        customer_id=customer.customer_id,
                        conversation_id=str(conversation_id),
                        payload=p.get("payload") or {},
                    )
                except Exception as e:
                    logger.error("ActionRegistry.create failed: %s", e)
                    continue
                interactive.append(
                    InteractiveAction(
                        label=p.get("label") or kind.replace("_", " ").title(),
                        action_id=action.action_id,
                        kind=kind,
                        confirm_text=p.get("confirm_text"),
                        payload=p.get("payload") or {},
                        expires_at=action.expires_at,
                    )
                )
            if interactive:
                yield sse_event(
                    EventType.INTERACTIVE_ACTIONS,
                    InteractiveActionsEvent(
                        actions=interactive
                    ).model_dump_json(),
                )

        if followup_questions:
            yield sse_event(
                EventType.FOLLOWUPS,
                FollowupsEvent(questions=followup_questions).model_dump_json(),
            )

        if suggested_actions:
            yield sse_event(
                EventType.ACTIONS,
                ActionsEvent(actions=suggested_actions).model_dump_json(),
            )

        citations_payload: dict = {
            "specialist": specialist_name,
            "router_confidence": router_confidence,
        }
        if source_previews:
            citations_payload["sources"] = {
                "total_searched": total_searched,
                "chunks": [p.model_dump() for p in source_previews],
            }
        if followup_questions:
            citations_payload["followups"] = [
                q.model_dump() if hasattr(q, "model_dump") else dict(q)
                for q in followup_questions
            ]
        if suggested_actions:
            citations_payload["actions"] = [
                a.model_dump() if hasattr(a, "model_dump") else dict(a)
                for a in suggested_actions
            ]
        if grounding_fallback_used:
            citations_payload["grounding_fallback_used"] = True

        assistant_msg: dict | None = None
        try:
            await self._conv_repo.add_message(conversation_id, "user", query)
            assistant_msg = await self._conv_repo.add_message(
                conversation_id,
                "assistant",
                final_response,
                specialist_used=specialist_name,
                citations_json=citations_payload,
                input_tokens=turn_usage.usage.input_tokens or None,
                output_tokens=turn_usage.usage.output_tokens or None,
                cost_usd=turn_usage.cost_usd,
            )
            for tool_out in tool_outputs:
                await self._conv_repo.add_tool_call(
                    conversation_id=conversation_id,
                    tool_name=tool_out["tool"],
                    input_json=tool_out.get("input"),
                    output_json=tool_out.get("output"),
                    message_id=assistant_msg.get("id"),
                )
            await self._conv_repo.update_conversation(
                conversation_id, last_specialist=specialist_name
            )
            await self._cache.set(
                str(conversation_id),
                SessionState(
                    rolling_summary=history.rolling_summary,
                    last_specialist=specialist_name,
                    unresolved_facts=history.unresolved_facts,
                ),
            )
        except Exception as e:
            logger.error("Failed to persist support turn: %s", e, exc_info=True)

        # Store in FAQ cache on the way out. Strict guard: only when the
        # specialist is cacheable, only if all tools called are on the
        # allowlist, only if grounding passed, and only if the reply
        # doesn't leak the customer id. Interactive actions kill caching
        # (the ids are one-shot + conversation-scoped).
        if (
            is_cacheable_specialist(specialist_name)
            and response_is_grounded
            and not grounding_fallback_used
            and is_cacheable_tool_set(tools_called)
            and not reply_looks_customer_specific(final_response, customer.customer_id)
            and not proposals  # no write-proposals in the turn
        ):
            cache_zip = customer.zip_code if specialist_name == "outage" else None
            await faq_cache.put(
                specialist_name,
                query,
                CachedAnswer(
                    specialist=specialist_name,
                    reply=final_response,
                    followups=[q.model_dump() for q in followup_questions],
                    actions=[a.model_dump() for a in suggested_actions],
                    tools_called=list(tools_called),
                ),
                zip_code=cache_zip,
            )

        yield sse_event(
            EventType.DONE,
            SupportDoneEvent(
                total_chunks_used=len(chunks),
                sources_used=list({c.source_name for c in chunks}),
                faithfulness_passed=response_is_grounded,
                latency_ms=int((time.time() - start_time) * 1000),
                retrieval_ms=retrieval_ms,
                generation_ms=generation_ms,
                validation_ms=validation_ms,
                specialist_used=specialist_name,
                router_confidence=router_confidence,
                conversation_id=str(conversation_id),
                tools_called=tools_called,
                input_tokens=turn_usage.usage.input_tokens or None,
                output_tokens=turn_usage.usage.output_tokens or None,
                total_tokens=turn_usage.usage.total_tokens or None,
                llm_requests=turn_usage.usage.requests or None,
                cost_usd=turn_usage.cost_usd,
            ).model_dump_json(),
        )

        await self._maybe_generate_title(
            conversation_id=conversation_id,
            query=query,
            answer=final_response,
            active_provider=active_provider,
        )

    async def _maybe_generate_title(
        self,
        *,
        conversation_id: UUID,
        query: str,
        answer: str,
        active_provider: str,
    ) -> None:
        """Generate a 5–8 word title once per conversation.

        No-ops when a title already exists — keeps the sidebar label
        stable across later turns instead of churning as the topic drifts.
        Uses the ``summary`` slot (cheap model) and caps output at a
        handful of tokens, so the marginal cost per new conversation is
        negligible.
        """
        try:
            existing = await self._conv_repo.get_conversation(conversation_id)
            if existing and existing.get("title"):
                return

            model = self._llm.resolve_model("summary", active_provider)
            raw = await self._llm.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You write a 5-8 word title summarizing a "
                            "customer support conversation. Return ONLY "
                            "the title — no quotes, no trailing "
                            "punctuation, no prefix like 'Title:'."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Customer question: {query.strip()[:500]}\n"
                            f"Support answer: {answer.strip()[:500]}"
                        ),
                    },
                ],
                model=model,
                provider=active_provider,
                temperature=0,
                max_tokens=30,
            )
            title = raw.strip().strip('"').strip("'").rstrip(".").strip()
            if not title:
                return
            if len(title) > 80:
                title = title[:80].rstrip() + "…"
            await self._conv_repo.update_conversation(
                conversation_id, title=title
            )
        except Exception as e:
            logger.error("Title generation failed: %s", e, exc_info=True)

    async def _stream_cheap_reply(
        self,
        *,
        intent: str,
        query: str,
        conversation_id: UUID,
        active_provider: str,
        turn_usage: UsageAccumulator,
        history: HistoryContext | None,
        start_time: float,
        response_language: str = "en",
    ) -> AsyncGenerator[dict, None]:
        """Cheap path for non-support intents: skip retrieval + faithfulness.

        Picks the agent + fallback message based on ``intent``:
        - smalltalk: warm greeting/thanks/bye
        - off_topic: gentle decline + redirect to in-scope topics

        Streams the reply, persists the turn (so history stays coherent),
        and emits ``done`` with ``specialist_used`` set to the intent so
        the client can label it consistently.

        ``history`` is optional. When the orchestrator decided to skip
        history loading (the common case for smalltalk), we also skip
        the cache write — it would just rewrite the same values back.

        ``response_language`` ("en" or "es") flips the agent's reply
        language to match the customer's input.
        """
        if intent == "smalltalk":
            agent = smalltalk_agent
            fallback_text = (
                "Hi! I'm here to help with your account, billing, or any "
                "service issues. What can I help you with?"
                if response_language == "en"
                else (
                    "¡Hola! Estoy aquí para ayudarte con tu cuenta, "
                    "facturación o cualquier problema de servicio. "
                    "¿En qué puedo ayudarte?"
                )
            )
        else:
            agent = support_off_topic_agent
            fallback_text = (
                "I can't help with that, but I can answer questions about "
                "your internet, TV, mobile, devices, or billing. What can "
                "I help you with?"
                if response_language == "en"
                else (
                    "No puedo ayudar con eso, pero sí puedo responder "
                    "preguntas sobre tu internet, TV, móvil, dispositivos "
                    "o facturación. ¿En qué puedo ayudarte?"
                )
            )

        cheap_model = self._llm.agent_model("smalltalk", active_provider)

        # Prepend the response-language directive so the agent replies
        # in the customer's language. The smalltalk + off_topic system
        # prompts stay English; this single line is enough.
        agent_input = f"{language_directive(response_language)}\n\n{query}"

        yield sse_event(
            EventType.SPECIALIST_INFO,
            SpecialistInfoEvent(
                specialist=intent, confidence=1.0
            ).model_dump_json(),
        )

        generation_start = time.time()
        full_response = ""
        try:
            async with agent.run_stream(
                agent_input, model=cheap_model
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

        try:
            await self._conv_repo.add_message(conversation_id, "user", query)
            await self._conv_repo.add_message(
                conversation_id,
                "assistant",
                full_response,
                specialist_used=intent,
                input_tokens=turn_usage.usage.input_tokens or None,
                output_tokens=turn_usage.usage.output_tokens or None,
                cost_usd=turn_usage.cost_usd,
            )
            # Only refresh the session cache when we actually loaded
            # history — otherwise we'd be writing stale defaults over
            # the live state. Skipping the write is safe: smalltalk
            # turns don't change rolling_summary / last_specialist /
            # unresolved_facts.
            if history is not None:
                await self._cache.set(
                    str(conversation_id),
                    SessionState(
                        rolling_summary=history.rolling_summary,
                        last_specialist=history.last_specialist,
                        unresolved_facts=history.unresolved_facts,
                    ),
                )
        except Exception as e:
            logger.error("Failed to persist %s turn: %s", intent, e, exc_info=True)

        yield sse_event(
            EventType.DONE,
            SupportDoneEvent(
                total_chunks_used=0,
                sources_used=[],
                faithfulness_passed=True,
                latency_ms=int((time.time() - start_time) * 1000),
                generation_ms=generation_ms,
                specialist_used=intent,
                router_confidence=1.0,
                conversation_id=str(conversation_id),
                tools_called=[],
                input_tokens=turn_usage.usage.input_tokens or None,
                output_tokens=turn_usage.usage.output_tokens or None,
                total_tokens=turn_usage.usage.total_tokens or None,
                llm_requests=turn_usage.usage.requests or None,
                cost_usd=turn_usage.cost_usd,
            ).model_dump_json(),
        )

    async def _stream_out_of_scope_reply(
        self,
        *,
        conversation_id: UUID,
        query: str,
        turn_usage: UsageAccumulator,
        history: HistoryContext,
        start_time: float,
        response_language: str,
        escalation_contact: dict,
        specialist_name: str,
        router_confidence: float,
        retrieval_ms: int,
    ) -> AsyncGenerator[dict, None]:
        """Static refusal when the KB has nothing relevant to ground a reply.

        Triggered by the pre-generation scope gate (top retrieval score
        below ``rag_scope_threshold``). Replies in the customer's
        language, points them at the right escalation contact, persists
        the turn, and emits ``done``. No LLM call.
        """
        reply = _build_knowledge_base_refusal(
            response_language, escalation_contact
        )

        yield sse_event(
            EventType.SPECIALIST_INFO,
            SpecialistInfoEvent(
                specialist=specialist_name, confidence=router_confidence
            ).model_dump_json(),
        )
        yield sse_event(
            EventType.TEXT,
            TextEvent(content=reply).model_dump_json(),
        )

        # Persist + cache update so history stays coherent if the
        # customer rephrases on the next turn.
        try:
            await self._conv_repo.add_message(conversation_id, "user", query)
            await self._conv_repo.add_message(
                conversation_id,
                "assistant",
                reply,
                specialist_used=specialist_name,
            )
            await self._conv_repo.update_conversation(
                conversation_id, last_specialist=specialist_name
            )
            await self._cache.set(
                str(conversation_id),
                SessionState(
                    rolling_summary=history.rolling_summary,
                    last_specialist=specialist_name,
                    unresolved_facts=history.unresolved_facts,
                ),
            )
        except Exception as e:
            logger.error(
                "Failed to persist out-of-scope turn: %s", e, exc_info=True
            )

        yield sse_event(
            EventType.DONE,
            SupportDoneEvent(
                total_chunks_used=0,
                sources_used=[],
                # Truthful: nothing to verify, but also nothing made up.
                faithfulness_passed=True,
                latency_ms=int((time.time() - start_time) * 1000),
                retrieval_ms=retrieval_ms,
                specialist_used=specialist_name,
                router_confidence=router_confidence,
                conversation_id=str(conversation_id),
                tools_called=[],
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
        conversation_id: UUID,
        query: str,
        turn_usage: UsageAccumulator,
        start_time: float,
        response_language: str,
    ) -> AsyncGenerator[dict, None]:
        """Static capabilities summary for "what can you help with?" turns.

        No LLM call, no retrieval, no tools. The reply is hand-curated
        so the customer sees an accurate, fast list of what we actually
        support — derived from the available tools + escalation paths,
        not invented by the agent. Static text per language; update
        this method when the tool registry changes shape.
        """
        if response_language == "es":
            reply = (
                "Puedo ayudarte con:\n\n"
                "• **Facturación y pagos** — consultar tu saldo, ver "
                "facturas, revisar cargos recientes\n"
                "• **Problemas de servicio** — solucionar problemas de "
                "internet, TV, voz o móvil\n"
                "• **Cortes de servicio** — verificar si hay un corte "
                "conocido en tu área\n"
                "• **Dispositivos** — consultar dispositivos en tu "
                "cuenta y su estado\n"
                "• **Información de cuenta** — ver tu plan y detalles "
                "de servicio\n"
                "• **Tickets recientes** — consultar el estado de tus "
                "tickets de soporte\n\n"
                "Para cambios de cuenta (actualizaciones, cambios de "
                "plan, reembolsos), puedo conectarte con un agente. "
                "¿En qué puedo ayudarte hoy?"
            )
        else:
            reply = (
                "I can help you with:\n\n"
                "• **Billing & payments** — check your balance, view "
                "invoices, see recent charges\n"
                "• **Service issues** — troubleshoot internet, TV, "
                "voice, or mobile problems\n"
                "• **Outages** — check if there's a known outage in "
                "your area\n"
                "• **Devices** — look up devices on your account and "
                "their status\n"
                "• **Account info** — see your plan and service "
                "details\n"
                "• **Recent tickets** — check the status of your "
                "support tickets\n\n"
                "For account changes (upgrades, plan changes, "
                "refunds), I can connect you with a human agent. "
                "What can I help you with today?"
            )

        yield sse_event(
            EventType.SPECIALIST_INFO,
            SpecialistInfoEvent(
                specialist="capabilities", confidence=1.0
            ).model_dump_json(),
        )
        yield sse_event(
            EventType.TEXT,
            TextEvent(content=reply).model_dump_json(),
        )

        # Persist so the conversation history reflects what the
        # customer was shown — useful context for the next turn.
        try:
            await self._conv_repo.add_message(conversation_id, "user", query)
            await self._conv_repo.add_message(
                conversation_id,
                "assistant",
                reply,
                specialist_used="capabilities",
            )
        except Exception as e:
            logger.error(
                "Failed to persist capabilities turn: %s", e, exc_info=True
            )

        yield sse_event(
            EventType.DONE,
            SupportDoneEvent(
                total_chunks_used=0,
                sources_used=[],
                faithfulness_passed=True,
                latency_ms=int((time.time() - start_time) * 1000),
                specialist_used="capabilities",
                router_confidence=1.0,
                conversation_id=str(conversation_id),
                tools_called=[],
                input_tokens=turn_usage.usage.input_tokens or None,
                output_tokens=turn_usage.usage.output_tokens or None,
                total_tokens=turn_usage.usage.total_tokens or None,
                llm_requests=turn_usage.usage.requests or None,
                cost_usd=turn_usage.cost_usd,
            ).model_dump_json(),
        )

    async def _stream_summarize_reply(
        self,
        *,
        conversation_id: UUID,
        query: str,
        active_provider: str,
        turn_usage: UsageAccumulator,
        history: HistoryContext,
        start_time: float,
        response_language: str,
    ) -> AsyncGenerator[dict, None]:
        """Chat-recap path for ``summarize`` intent turns.

        Grounds the reply in the loaded ``history`` (rolling summary +
        recent turns). No retrieval, no tools, no faithfulness check —
        the transcript is the source, and the agent is instructed not
        to invent anything beyond it. Uses the ``summary`` model slot
        (cheap) since this is a recap, not a generation task.

        First-turn case (empty history) is handled by the agent's
        system prompt: it replies with a single sentence saying there's
        nothing yet to recap.
        """
        transcript = _format_history_for_recap(history)
        agent_input = (
            f"{language_directive(response_language)}\n\n"
            f"{transcript}\n\n"
            f"User asked: {query}"
        )

        summary_model = self._llm.agent_model("summary", active_provider)

        yield sse_event(
            EventType.SPECIALIST_INFO,
            SpecialistInfoEvent(
                specialist="summarize", confidence=1.0
            ).model_dump_json(),
        )

        generation_start = time.time()
        full_response = ""
        try:
            async with summarize_agent.run_stream(
                agent_input, model=summary_model
            ) as stream_result:
                async for chunk in stream_result.stream_text(delta=True):
                    if not chunk:
                        continue
                    full_response += chunk
                    yield sse_event(
                        EventType.TEXT,
                        TextEvent(content=chunk).model_dump_json(),
                    )
                turn_usage.add(stream_result.usage(), summary_model)
        except Exception as e:
            logger.error("Summarize agent failed: %s", e, exc_info=True)
            full_response = (
                "I couldn't generate a summary of our chat just now — "
                "please try again in a moment."
                if response_language == "en"
                else (
                    "No pude generar un resumen de nuestra conversación "
                    "ahora mismo — por favor intenta de nuevo."
                )
            )
            yield sse_event(
                EventType.TEXT,
                TextEvent(content=full_response).model_dump_json(),
            )

        generation_ms = int((time.time() - generation_start) * 1000)

        try:
            await self._conv_repo.add_message(conversation_id, "user", query)
            await self._conv_repo.add_message(
                conversation_id,
                "assistant",
                full_response,
                specialist_used="summarize",
                input_tokens=turn_usage.usage.input_tokens or None,
                output_tokens=turn_usage.usage.output_tokens or None,
                cost_usd=turn_usage.cost_usd,
            )
        except Exception as e:
            logger.error(
                "Failed to persist summarize turn: %s", e, exc_info=True
            )

        yield sse_event(
            EventType.DONE,
            SupportDoneEvent(
                total_chunks_used=0,
                sources_used=[],
                faithfulness_passed=True,
                latency_ms=int((time.time() - start_time) * 1000),
                generation_ms=generation_ms,
                specialist_used="summarize",
                router_confidence=1.0,
                conversation_id=str(conversation_id),
                tools_called=[],
                input_tokens=turn_usage.usage.input_tokens or None,
                output_tokens=turn_usage.usage.output_tokens or None,
                total_tokens=turn_usage.usage.total_tokens or None,
                llm_requests=turn_usage.usage.requests or None,
                cost_usd=turn_usage.cost_usd,
            ).model_dump_json(),
        )

    async def _stream_cached_reply(
        self,
        *,
        cached: CachedAnswer,
        conversation_id: UUID,
        query: str,
        turn_usage: UsageAccumulator,
        start_time: float,
        router_confidence: float,
    ) -> AsyncGenerator[dict, None]:
        """Replay a cached FAQ answer — zero LLM cost, still persists the turn.

        Emits the same event envelope as a live turn so the frontend
        renders identically; the only difference is ``tools_called``
        only contains read-only tools we allow to cache (outage
        lookups) plus an extra ``cost_usd=0`` signal.
        """
        yield sse_event(
            EventType.SPECIALIST_INFO,
            SpecialistInfoEvent(
                specialist=cached.specialist, confidence=router_confidence
            ).model_dump_json(),
        )
        yield sse_event(
            EventType.TEXT,
            TextEvent(content=cached.reply).model_dump_json(),
        )
        for tool_name in cached.tools_called:
            yield sse_event(
                EventType.TOOL_CALL,
                ToolCallEvent(
                    tool_name=tool_name, status="success"
                ).model_dump_json(),
            )
        if cached.followups:
            yield sse_event(
                EventType.FOLLOWUPS,
                FollowupsEvent(
                    questions=[FollowupQuestion(**q) for q in cached.followups]
                ).model_dump_json(),
            )
        if cached.actions:
            yield sse_event(
                EventType.ACTIONS,
                ActionsEvent(
                    actions=[ActionLink(**a) for a in cached.actions]
                ).model_dump_json(),
            )

        try:
            await self._conv_repo.add_message(conversation_id, "user", query)
            await self._conv_repo.add_message(
                conversation_id,
                "assistant",
                cached.reply,
                specialist_used=cached.specialist,
                citations_json={"faq_cache_hit": True},
            )
        except Exception as e:
            logger.error("Failed to persist cached turn: %s", e, exc_info=True)

        yield sse_event(
            EventType.DONE,
            SupportDoneEvent(
                total_chunks_used=0,
                sources_used=[],
                faithfulness_passed=True,
                latency_ms=int((time.time() - start_time) * 1000),
                specialist_used=cached.specialist,
                router_confidence=router_confidence,
                conversation_id=str(conversation_id),
                tools_called=cached.tools_called,
                input_tokens=turn_usage.usage.input_tokens or None,
                output_tokens=turn_usage.usage.output_tokens or None,
                total_tokens=turn_usage.usage.total_tokens or None,
                llm_requests=turn_usage.usage.requests or None,
                # Zero additional cost: the router run (if any) was
                # already accounted for in ``turn_usage``; everything
                # else was free.
                cost_usd=turn_usage.cost_usd,
            ).model_dump_json(),
        )

    async def _stream_multi_agent_reply(
        self,
        *,
        query: str,
        customer: CustomerContext,
        conversation_id: UUID,
        decomposition: Decomposition,
        source_ids: list[str] | None,
        history: HistoryContext,
        active_provider: str,
        turn_usage: UsageAccumulator,
        start_time: float,
        response_language: str,
    ) -> AsyncGenerator[dict, None]:
        """Run the customer query as 2-3 parallel specialist branches and
        synthesize a single reply.

        Skips the single-specialist path's validation / cards /
        interactive-actions / FAQ cache — those concerns assume one
        specialist owns the turn. The final answer is persisted with
        ``specialist_used="multi"`` and per-branch sources in
        ``citations_json`` so the transcript viewer can still explain
        where the content came from.
        """
        branches = decomposition.sub_queries

        yield sse_event(
            EventType.THINKING,
            ThinkingEvent(
                stage="routing",
                message=(
                    f"Splitting into {len(branches)} specialist "
                    f"{'parts' if len(branches) > 1 else 'part'}…"
                ),
            ).model_dump_json(),
        )

        # Emit SPECIALIST_INFO per branch up front so the UI can show
        # which specialists are about to run. Confidence is 1.0 — the
        # decomposer already picked; no router tie-break happens here.
        for sq in branches:
            yield sse_event(
                EventType.SPECIALIST_INFO,
                SpecialistInfoEvent(
                    specialist=sq.specialist, confidence=1.0
                ).model_dump_json(),
            )

        yield sse_event(
            EventType.THINKING,
            ThinkingEvent(
                stage="generating",
                message=f"Running {len(branches)} specialists in parallel…",
            ).model_dump_json(),
        )

        generation_start = time.time()
        branch_tasks = [
            run_branch(
                sub_query=sq,
                index=i,
                customer=customer,
                history=history,
                retriever=self._retriever,
                prompt_builder=self._prompt_builder,
                llm=self._llm,
                active_provider=active_provider,
                source_ids=source_ids,
                turn_usage=turn_usage,
                response_language=response_language,
            )
            for i, sq in enumerate(branches)
        ]
        branch_results = await asyncio.gather(*branch_tasks)
        generation_ms = int((time.time() - generation_start) * 1000)

        # Merge + emit all branches' retrieved sources so the frontend
        # "Sources" panel shows the full evidence set behind the
        # synthesized answer.
        all_sources: list[ChunkPreview] = []
        seen_ids: set[str] = set()
        for node in branch_results:
            for src in node.sources:
                if src.id in seen_ids:
                    continue
                seen_ids.add(src.id)
                all_sources.append(src)
        if all_sources:
            yield sse_event(
                EventType.SOURCES,
                SourcesEvent(
                    chunks=all_sources,
                    total_searched=len(all_sources),
                ).model_dump_json(),
            )

        # Tool-call transparency per branch (same event shape the
        # single-specialist path uses). Preserves the existing UI
        # rendering without new plumbing.
        tools_called: list[str] = []
        for node in branch_results:
            for tc in node.tool_calls:
                tools_called.append(tc.name)
                yield sse_event(
                    EventType.TOOL_CALL,
                    ToolCallEvent(
                        tool_name=tc.name, status="success"
                    ).model_dump_json(),
                )

        # Synthesize the branches into one reply.
        yield sse_event(
            EventType.THINKING,
            ThinkingEvent(
                stage="generating",
                message="Combining specialist answers…",
            ).model_dump_json(),
        )
        synth_model = self._llm.agent_model("followup", active_provider)
        synth_inputs = [
            SynthesizerInput(
                specialist=n.specialist or "unknown",
                sub_query=n.sub_query or "",
                sub_answer=n.output_text or "",
                status=n.status,
            )
            for n in branch_results
        ]
        synth_msg = build_synthesizer_message(query, synth_inputs)
        try:
            synth_result = await synthesizer_agent.run(
                synth_msg, model=synth_model
            )
            final_response = synth_result.output or ""
            turn_usage.add(synth_result.usage(), synth_model)
        except Exception as e:
            logger.error("Multi-agent synthesizer failed: %s", e, exc_info=True)
            # Fallback: concatenate per-branch answers, labeled. Not
            # pretty, but better than a hard error after we already ran
            # retrieval + generation on every branch.
            final_response = "\n\n".join(
                f"**{n.specialist}**: {n.output_text or '(no reply)'}"
                for n in branch_results
            )

        yield sse_event(
            EventType.TEXT,
            TextEvent(content=final_response).model_dump_json(),
        )

        # Persist the turn. One user message + one assistant message
        # with a composite ``specialist_used`` so the transcript makes
        # the multi-agent provenance auditable. Per-branch sources go
        # into ``citations_json.branches``.
        citations_payload: dict = {
            "specialist": "multi",
            "branches": [
                {
                    "specialist": n.specialist,
                    "sub_query": n.sub_query,
                    "status": n.status,
                    "timing_ms": n.timing_ms,
                    "sources": [s.model_dump() for s in n.sources],
                    "tool_calls": [
                        tc.model_dump() for tc in n.tool_calls
                    ],
                }
                for n in branch_results
            ],
        }
        if all_sources:
            citations_payload["sources"] = {
                "total_searched": len(all_sources),
                "chunks": [s.model_dump() for s in all_sources],
            }

        assistant_msg: dict | None = None
        try:
            await self._conv_repo.add_message(conversation_id, "user", query)
            assistant_msg = await self._conv_repo.add_message(
                conversation_id,
                "assistant",
                final_response,
                specialist_used="multi",
                citations_json=citations_payload,
                input_tokens=turn_usage.usage.input_tokens or None,
                output_tokens=turn_usage.usage.output_tokens or None,
                cost_usd=turn_usage.cost_usd,
            )
            for node in branch_results:
                for tc in node.tool_calls:
                    await self._conv_repo.add_tool_call(
                        conversation_id=conversation_id,
                        tool_name=tc.name,
                        input_json=tc.input,
                        output_json=tc.output,
                        message_id=assistant_msg.get("id") if assistant_msg else None,
                    )
            # Intentionally do NOT set ``last_specialist`` — the next
            # turn's router should route based on the follow-up content,
            # not on "multi", which isn't a real specialist. Session
            # rolling summary stays untouched.
        except Exception as e:
            logger.error(
                "Failed to persist multi-agent turn: %s", e, exc_info=True
            )

        yield sse_event(
            EventType.DONE,
            SupportDoneEvent(
                total_chunks_used=len(all_sources),
                sources_used=sorted({s.source_name for s in all_sources}),
                faithfulness_passed=True,  # no validator on multi-agent v1
                latency_ms=int((time.time() - start_time) * 1000),
                generation_ms=generation_ms,
                specialist_used="multi",
                router_confidence=1.0,
                conversation_id=str(conversation_id),
                tools_called=tools_called,
                input_tokens=turn_usage.usage.input_tokens or None,
                output_tokens=turn_usage.usage.output_tokens or None,
                total_tokens=turn_usage.usage.total_tokens or None,
                llm_requests=turn_usage.usage.requests or None,
                cost_usd=turn_usage.cost_usd,
            ).model_dump_json(),
        )

        await self._maybe_generate_title(
            conversation_id=conversation_id,
            query=query,
            answer=final_response,
            active_provider=active_provider,
        )

    async def _stream_unsupported_language_reply(
        self,
        *,
        conversation_id: UUID,
        query: str,
        turn_usage: UsageAccumulator,
        start_time: float,
    ) -> AsyncGenerator[dict, None]:
        """Static bilingual rejection for queries in unsupported languages.

        No LLM call — the reply text is canned. Persists the user's
        message + the rejection so the conversation history stays
        coherent if they retry in English/Spanish on the next turn.
        """
        yield sse_event(
            EventType.SPECIALIST_INFO,
            SpecialistInfoEvent(
                specialist="unsupported_language", confidence=1.0
            ).model_dump_json(),
        )
        yield sse_event(
            EventType.TEXT,
            TextEvent(content=UNSUPPORTED_LANGUAGE_REPLY).model_dump_json(),
        )

        # Persist so the next turn has context if the customer retries
        # in a supported language.
        try:
            await self._conv_repo.add_message(conversation_id, "user", query)
            await self._conv_repo.add_message(
                conversation_id,
                "assistant",
                UNSUPPORTED_LANGUAGE_REPLY,
                specialist_used="unsupported_language",
            )
        except Exception as e:
            logger.error(
                "Failed to persist unsupported-language turn: %s", e, exc_info=True
            )

        yield sse_event(
            EventType.DONE,
            SupportDoneEvent(
                total_chunks_used=0,
                sources_used=[],
                faithfulness_passed=True,
                latency_ms=int((time.time() - start_time) * 1000),
                specialist_used="unsupported_language",
                router_confidence=1.0,
                conversation_id=str(conversation_id),
                tools_called=[],
                input_tokens=turn_usage.usage.input_tokens or None,
                output_tokens=turn_usage.usage.output_tokens or None,
                total_tokens=turn_usage.usage.total_tokens or None,
                llm_requests=turn_usage.usage.requests or None,
                cost_usd=turn_usage.cost_usd,
            ).model_dump_json(),
        )

    def _detect_handoff(self, response: str, current_specialist: str) -> str | None:
        """Detect if the specialist's response suggests a handoff.

        Looks for patterns like "billing specialist would be more appropriate"
        or "technical specialist should handle this".
        """
        response_lower = response.lower()
        handoff_targets = {
            name for name in SPECIALIST_REGISTRY if name != current_specialist
        }

        for target in handoff_targets:
            patterns = [
                f"{target} specialist",
                f"handled by {target}",
                f"transfer to {target}",
                f"{target} team",
            ]
            if any(p in response_lower for p in patterns):
                return target

        return None
