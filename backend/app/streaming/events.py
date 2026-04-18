from enum import Enum

from pydantic import BaseModel


class EventType(str, Enum):
    THINKING = "thinking"
    SOURCES = "sources"
    CODE = "code"
    TEXT = "text"
    WIKI = "wiki"
    FOLLOWUPS = "followups"
    SCOPE_WARN = "scope_warn"
    ERROR = "error"
    DONE = "done"
    # Customer-support events
    SPECIALIST_INFO = "specialist_info"
    TOOL_CALL = "tool_call"
    ESCALATION = "escalation"
    ACTIONS = "actions"
    # Phase A/B: auth + interactive UI
    CARDS = "cards"
    INTERACTIVE_ACTIONS = "interactive_actions"
    ACTION_RESULT = "action_result"


class ThinkingEvent(BaseModel):
    type: EventType = EventType.THINKING
    message: str
    stage: str  # retrieving | ranking | generating | checking | followups


class ChunkPreview(BaseModel):
    id: str
    qualified_name: str
    file_path: str
    source_type: str
    source_name: str
    score: float
    summary: str
    purpose: str
    reuse_signal: str


class SourcesEvent(BaseModel):
    type: EventType = EventType.SOURCES
    chunks: list[ChunkPreview]
    total_searched: int


class CodeEvent(BaseModel):
    type: EventType = EventType.CODE
    language: str
    content: str
    file_hint: str | None = None
    source_chunks: list[str] = []


class TextEvent(BaseModel):
    type: EventType = EventType.TEXT
    content: str


class WikiEvent(BaseModel):
    type: EventType = EventType.WIKI
    title: str
    url: str
    excerpt: str


class FollowupQuestion(BaseModel):
    question: str
    category: str  # dig_deeper | adjacent_concern | architecture


class FollowupsEvent(BaseModel):
    type: EventType = EventType.FOLLOWUPS
    questions: list[FollowupQuestion]


class ScopeWarnEvent(BaseModel):
    type: EventType = EventType.SCOPE_WARN
    message: str


class ErrorEvent(BaseModel):
    type: EventType = EventType.ERROR
    message: str
    code: str  # scope_exceeded | retrieval_failed | generation_failed


class DoneEvent(BaseModel):
    type: EventType = EventType.DONE
    total_chunks_used: int
    sources_used: list[str]
    faithfulness_passed: bool
    latency_ms: int
    retrieval_ms: int | None = None
    generation_ms: int | None = None
    validation_ms: int | None = None
    # Total LLM usage summed across every agent call in the turn
    # (router + generation/specialist + faithfulness + followup + suggester).
    # Null when the provider did not report usage for any call this turn.
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    llm_requests: int | None = None
    # Approximate USD cost, computed from the turn's tokens per call
    # against the provider's public pricing. Null when no call used a
    # priced model (e.g. Ollama self-hosted, or a model not in the
    # pricing table).
    cost_usd: float | None = None


# ------------------------------------------------------------------
# Customer-support-specific events
# ------------------------------------------------------------------
class SpecialistInfoEvent(BaseModel):
    type: EventType = EventType.SPECIALIST_INFO
    specialist: str
    confidence: float


class ToolCallEvent(BaseModel):
    type: EventType = EventType.TOOL_CALL
    tool_name: str
    status: str  # calling | success | error


class EscalationEvent(BaseModel):
    type: EventType = EventType.ESCALATION
    topic: str
    contact: dict  # {type, value} e.g. {"type": "phone", "value": "1-800-..."}


class ActionLink(BaseModel):
    """Clickable suggestion surfaced after a specialist reply.

    Exactly one of ``url`` / ``inline_query`` is set. ``url`` opens
    the destination in a new tab (external settings, handoff pages).
    ``inline_query`` feeds the string back into the chat as a new user
    turn so the answer renders inline (catalog, balance, outage
    status, etc.) instead of navigating away.
    """

    label: str                  # Button text, e.g. "Pay my bill"
    topic: str                  # Stable catalog key, e.g. "billing.pay"
    url: str | None = None
    inline_query: str | None = None


class ActionsEvent(BaseModel):
    type: EventType = EventType.ACTIONS
    actions: list[ActionLink]


class SupportDoneEvent(DoneEvent):
    specialist_used: str | None = None
    router_confidence: float | None = None
    conversation_id: str | None = None
    tools_called: list[str] = []


# ------------------------------------------------------------------
# Phase A/B events — rich interactive UI
# ------------------------------------------------------------------
class CardItem(BaseModel):
    kind: str           # "product" | "payment_method" | "appointment_slot"
    id: str             # stable identifier
    title: str
    subtitle: str | None = None
    image_url: str | None = None
    badges: list[str] = []
    metadata: dict = {}


class CardsEvent(BaseModel):
    type: EventType = EventType.CARDS
    prompt: str | None = None
    kind: str           # groups the cards for the frontend
    cards: list[CardItem]


class InteractiveAction(BaseModel):
    label: str
    action_id: str      # server-issued, one-shot
    kind: str           # "place_order" | "pay" | "book_appointment" | "cancel" | "enroll_autopay"
    confirm_text: str | None = None
    payload: dict = {}  # echoed back on click
    expires_at: str     # ISO timestamp


class InteractiveActionsEvent(BaseModel):
    type: EventType = EventType.INTERACTIVE_ACTIONS
    actions: list[InteractiveAction]


class ActionResultEvent(BaseModel):
    type: EventType = EventType.ACTION_RESULT
    action_id: str
    kind: str
    status: str         # "success" | "error" | "pending"
    message: str
    detail: dict = {}
