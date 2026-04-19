"""Synthesizer agent: merges parallel specialist replies into one answer.

Only used by the debug-mode parallel orchestrator when decomposition
produces 2+ sub-queries. Takes the original user query plus each
specialist's sub-answer and returns a single coherent reply.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic_ai import Agent

logger = logging.getLogger("[support]")


@dataclass
class SynthesizerInput:
    specialist: str
    sub_query: str
    sub_answer: str
    status: str  # "ok" | "auth_skipped" | "error"


@dataclass
class SynthesizerDeps:
    pass


_SYNTHESIZER_SYSTEM_PROMPT = """\
You merge several specialist replies into one coherent answer for the \
customer. Each specialist handled one slice of the original question; \
your job is to weave the slices together so the customer gets a single, \
helpful response.

RULES:
1. Preserve every factual claim the specialists made. Do not add new \
facts, costs, timelines, or instructions.
2. Order the answer the way the customer raised the topics in their \
original message.
3. Use headings or bullet points when the topics are clearly distinct; \
use prose when they're tightly related.
4. Attribute sparingly. Do not say "the technical specialist said…" — \
just give the customer the answer.
5. If a specialist was SKIPPED because the customer wasn't signed in, \
note that slice requires sign-in (one short sentence) and move on.
6. If a specialist ERRORED, say you couldn't check that part right now \
and offer to retry.
7. Keep it tight. Match the length of the longest sub-answer, not the \
sum of all of them.

Output plain text only — no JSON wrapper.
"""


synthesizer_agent = Agent(
    model="openai:gpt-4o-mini",  # overridden at .run() via agent_model("followup")
    output_type=str,
    system_prompt=_SYNTHESIZER_SYSTEM_PROMPT,
    deps_type=SynthesizerDeps,
)


def build_synthesizer_message(
    original_query: str,
    inputs: list[SynthesizerInput],
) -> str:
    parts = [f'Original customer message: "{original_query}"', "", "Specialist replies:"]
    for i, inp in enumerate(inputs, start=1):
        status_tag = ""
        if inp.status == "auth_skipped":
            status_tag = " [SKIPPED — requires sign-in]"
        elif inp.status == "error":
            status_tag = " [ERROR — could not complete]"
        parts.append(
            f"\n{i}. {inp.specialist}{status_tag}\n"
            f"   Sub-query: {inp.sub_query}\n"
            f"   Reply: {inp.sub_answer or '(no reply)'}"
        )
    return "\n".join(parts)
