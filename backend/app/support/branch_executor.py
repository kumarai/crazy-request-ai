"""Run one specialist branch of a multi-agent query.

Shared by :class:`DebugOrchestrator` (off-chat debug tracing) and
:class:`SupportOrchestrator` (live support chat) so the two paths
stay in lockstep. The helper is intentionally narrow:

- takes an already-decided specialist (the decomposer picked it)
- runs the same retrieval + prompt + specialist-agent call the
  single-specialist path uses
- returns a :class:`TraceNode` capturing inputs, output, sources,
  tool calls, timing, and status (``ok`` / ``auth_skipped`` / ``error``)

Intentionally does NOT do: validation, cards, interactive actions,
FAQ cache, DB persistence. Callers layer those concerns on top of
the branch result.
"""
from __future__ import annotations

import logging
import time

from app.config import settings
from app.llm.client import LLMClient
from app.llm.pricing import UsageAccumulator
from app.rag.prompt_builder import PromptBuilder
from app.rag.retriever import Retriever
from app.streaming.events import ChunkPreview
from app.support.agents.registry import get_specialist
from app.support.agents.support_agent import SupportAgentDeps, build_user_message
from app.support.customer_context import CustomerContext
from app.support.debug_trace import TraceNode, TraceToolCall
from app.support.decomposer import SubQuery
from app.support.history import HistoryContext
from app.support.retrieval_query import build_support_retrieval_query
from app.support.tools.escalation import get_escalation_contact

logger = logging.getLogger("[support]")


def _strip_identifier_lines(block: str) -> str:
    """Mirror of the orchestrator helper — strip qualified-name / URL
    header lines the prompt_builder prepends to wiki-style chunks so
    they don't leak into customer-facing replies."""
    out: list[str] = []
    for line in block.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("### ") or stripped.startswith("> "):
            continue
        out.append(line)
    while out and not out[0].strip():
        out.pop(0)
    return "\n".join(out)


async def run_branch(
    *,
    sub_query: SubQuery,
    index: int,
    customer: CustomerContext,
    history: HistoryContext,
    retriever: Retriever,
    prompt_builder: PromptBuilder,
    llm: LLMClient,
    active_provider: str,
    source_ids: list[str] | None,
    turn_usage: UsageAccumulator,
    response_language: str | None = None,
) -> TraceNode:
    """Run one specialist branch and return its :class:`TraceNode`.

    Guests hitting an auth-required specialist get an
    ``auth_skipped`` node instead of being silently re-routed — that
    matches the visibility the debug UI needs, and in live chat the
    synthesizer is instructed to nudge the user to sign in.
    """
    node_id = f"branch_{index}_{sub_query.specialist}"
    specialist_name = sub_query.specialist

    try:
        spec_config = get_specialist(specialist_name)
    except KeyError:
        logger.warning(
            "Unknown specialist %s in branch — falling back to general",
            specialist_name,
        )
        specialist_name = "general"
        spec_config = get_specialist("general")

    # Auth gate: guests hitting an auth-required specialist skip the
    # branch. The callers surface this via status="auth_skipped" so
    # the synthesizer (or the debug UI) can call out the sign-in
    # requirement without blocking unrelated branches.
    if spec_config.requires_auth and customer.is_guest:
        logger.info(
            "Branch %s: auth-skipped (guest hit %s)",
            node_id, specialist_name,
        )
        return TraceNode(
            id=node_id,
            kind="specialist",
            specialist=specialist_name,
            sub_query=sub_query.sub_query,
            rationale=sub_query.rationale,
            status="auth_skipped",
            output_text=(
                f"Skipped: the {specialist_name} specialist requires "
                "sign-in. Please sign in to get that part answered."
            ),
            timing_ms=0,
        )

    t_start = time.time()
    try:
        retrieval_query = build_support_retrieval_query(sub_query.sub_query)
        effective_top_k = spec_config.top_k or settings.rag_top_k_final

        chunks, _scope_conf, _total, _top_score = await retriever.retrieve(
            query=retrieval_query,
            source_ids=source_ids or customer.allowed_source_ids or None,
            language="text",
            top_k=effective_top_k,
            include_wiki=True,
            include_code=False,
            use_hyde=False,
            use_query_expansion=False,
        )

        sources = [
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

        _prompt, prompt_chunks = prompt_builder.assemble(sub_query.sub_query, chunks)
        retrieved_context = "\n---\n".join(
            f"[Source {i}]\n{_strip_identifier_lines(block)}"
            for i, (_chunk, block) in enumerate(prompt_chunks, start=1)
        )

        tool_outputs: list[dict] = []
        escalation_contact = await get_escalation_contact(spec_config.domain)
        user_message = build_user_message(
            sub_query.sub_query,
            customer,
            history,
            retrieved_context,
            escalation_contact=escalation_contact,
            language_directive=None,
        )

        deps = SupportAgentDeps(
            customer=customer,
            history=history,
            retrieved_context=retrieved_context,
            tool_outputs=tool_outputs,
            pending_intent=None,
        )

        specialist_model = llm.agent_model(
            spec_config.model_slot, active_provider
        )
        agent = spec_config.agent
        run_result = await agent.run(
            user_message, model=specialist_model, deps=deps
        )
        turn_usage.add(run_result.usage(), specialist_model)
        if spec_config.structured_handoff and not isinstance(run_result.output, str):
            output_text = getattr(run_result.output, "reply", "") or ""
        else:
            output_text = (
                run_result.output if isinstance(run_result.output, str)
                else str(run_result.output)
            )

        tool_calls = [
            TraceToolCall(
                name=t.get("tool", "?"),
                input=t.get("input"),
                output=t.get("output"),
            )
            for t in tool_outputs
        ]

        return TraceNode(
            id=node_id,
            kind="specialist",
            specialist=specialist_name,
            sub_query=sub_query.sub_query,
            rationale=sub_query.rationale,
            output_text=output_text,
            sources=sources,
            tool_calls=tool_calls,
            timing_ms=int((time.time() - t_start) * 1000),
            status="ok",
        )
    except Exception as e:
        logger.error("Branch %s failed: %s", node_id, e, exc_info=True)
        return TraceNode(
            id=node_id,
            kind="specialist",
            specialist=specialist_name,
            sub_query=sub_query.sub_query,
            rationale=sub_query.rationale,
            status="error",
            error=str(e),
            timing_ms=int((time.time() - t_start) * 1000),
        )
