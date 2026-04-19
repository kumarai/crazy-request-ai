"""Parallel multi-agent orchestrator used by the dev-only debug endpoint.

Pipeline:

    decompose -> [specialist_1 | specialist_2 | ...] -> synthesize

Compared to ``SupportOrchestrator``, this path is intentionally
read-only:

- no DB persistence of the turn
- no FAQ cache read / write
- no cards / interactive-actions emission
- no validator / faithfulness / followups pass
- no title generation

It exists to power the ``/debug/query`` endpoint, which returns a
complete ``Trace`` (DAG + per-node details) the frontend renders as a
graph. Production support chat continues to flow through
``SupportOrchestrator``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.llm.client import LLMClient
from app.llm.pricing import UsageAccumulator
from app.db.repositories.chunks import ChunksRepository
from app.db.repositories.sources import SourcesRepository
from app.rag.prompt_builder import PromptBuilder
from app.rag.retriever import Retriever
from app.support.agents.synthesizer import (
    SynthesizerInput,
    build_synthesizer_message,
    synthesizer_agent,
)
from app.support.branch_executor import run_branch
from app.support.customer_context import CustomerContext
from app.support.debug_trace import Trace, TraceNode, TraceRecorder
from app.support.decomposer import decompose
from app.support.history import HistoryContext

logger = logging.getLogger("[support]")


class DebugOrchestrator:
    def __init__(
        self,
        chunks_repo: ChunksRepository,
        sources_repo: SourcesRepository,
        llm_client: LLMClient,
        redis: Any,
    ) -> None:
        self._llm = llm_client
        self._retriever = Retriever(
            chunks_repo, llm_client,
            sources_repo=sources_repo,
            redis=redis,
        )
        self._prompt_builder = PromptBuilder()

    async def run(
        self,
        query: str,
        customer: CustomerContext,
        source_ids: list[str] | None = None,
        provider: str | None = None,
    ) -> Trace:
        recorder = TraceRecorder(query)
        active_provider = provider or self._llm.provider
        turn_usage = UsageAccumulator()

        # --- Decompose --------------------------------------------------
        decomposer_id = "decomposer"
        router_model = self._llm.agent_model("router", active_provider)
        with recorder.time_node() as dtimer:
            decomposition, decomp_usage = await decompose(query, model=router_model)
        turn_usage.add(decomp_usage, router_model)

        recorder.add_node(
            TraceNode(
                id=decomposer_id,
                kind="decomposer",
                sub_query=query,
                output_text="\n".join(
                    f"- [{sq.specialist}] {sq.sub_query} ({sq.rationale})"
                    for sq in decomposition.sub_queries
                ),
                timing_ms=dtimer["ms"],
            )
        )

        # --- Run specialist branches in parallel ------------------------
        branches = decomposition.sub_queries
        empty_history = HistoryContext()
        branch_tasks = [
            run_branch(
                sub_query=sq,
                index=i,
                customer=customer,
                history=empty_history,
                retriever=self._retriever,
                prompt_builder=self._prompt_builder,
                llm=self._llm,
                active_provider=active_provider,
                source_ids=source_ids,
                turn_usage=turn_usage,
            )
            for i, sq in enumerate(branches)
        ]
        branch_results = await asyncio.gather(*branch_tasks)

        for node in branch_results:
            recorder.add_node(node)
            recorder.add_edge(decomposer_id, node.id)

        # --- Synthesize (only when >1 branch) ---------------------------
        if len(branch_results) == 1:
            recorder.set_final_answer(branch_results[0].output_text or "")
        else:
            synth_id = "synthesizer"
            followup_model = self._llm.agent_model("followup", active_provider)
            synth_inputs = [
                SynthesizerInput(
                    specialist=n.specialist or "unknown",
                    sub_query=n.sub_query or "",
                    sub_answer=n.output_text or "",
                    status=n.status,
                )
                for n in branch_results
            ]
            msg = build_synthesizer_message(query, synth_inputs)
            with recorder.time_node() as stimer:
                try:
                    result = await synthesizer_agent.run(msg, model=followup_model)
                    final_text = result.output or ""
                    turn_usage.add(result.usage(), followup_model)
                    synth_status = "ok"
                    synth_err: str | None = None
                except Exception as e:
                    logger.error("Synthesizer failed: %s", e, exc_info=True)
                    # Fall back to concatenating the per-branch replies
                    # so the debug page still shows a final answer.
                    final_text = "\n\n".join(
                        f"**{n.specialist}**: {n.output_text or '(no reply)'}"
                        for n in branch_results
                    )
                    synth_status = "error"
                    synth_err = str(e)

            recorder.add_node(
                TraceNode(
                    id=synth_id,
                    kind="synthesizer",
                    output_text=final_text,
                    timing_ms=stimer["ms"],
                    status=synth_status,
                    error=synth_err,
                )
            )
            for n in branch_results:
                recorder.add_edge(n.id, synth_id)
            recorder.set_final_answer(final_text)

        recorder.set_cost(turn_usage.cost_usd)
        return recorder.finalize()
