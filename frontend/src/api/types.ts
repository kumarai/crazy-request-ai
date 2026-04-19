// Mirrors backend Pydantic models

export interface Source {
  id: string
  name: string
  source_type: string
  config: Record<string, unknown>
  credential_id: string | null
  is_active: boolean
  created_at: string | null
  last_synced_at: string | null
  chunk_count?: number
}

export interface Credential {
  id: string
  name: string
  credential_type: string
  description: string | null
  created_at: string | null
  updated_at: string | null
}

export interface JobStats {
  files_processed?: number
  chunks_created?: number
  chunks_deleted?: number
  chunks_unchanged?: number
  dependencies_created?: number
  embedding_model?: string
  embedding_dim?: number
  rerank_model?: string
  errors?: string[]
}

export interface Job {
  id: string
  source_id: string
  celery_task_id: string | null
  status: string
  triggered_by: string
  started_at: string | null
  finished_at: string | null
  error: string | null
  stats: JobStats & Record<string, unknown>
}

// SSE event payloads
export interface ThinkingEvent {
  type: "thinking"
  message: string
  stage: string
}

export interface ChunkPreview {
  id: string
  qualified_name: string
  file_path: string
  source_type: string
  source_name: string
  score: number
  summary: string
  purpose: string
  reuse_signal: string
}

export interface SourcesEvent {
  type: "sources"
  chunks: ChunkPreview[]
  total_searched: number
}

export interface CodeEvent {
  type: "code"
  language: string
  content: string
  file_hint: string | null
  source_chunks: string[]
}

export interface TextEvent {
  type: "text"
  content: string
}

export interface WikiEvent {
  type: "wiki"
  title: string
  url: string
  excerpt: string
}

export interface FollowupQuestion {
  question: string
  category: string
}

export interface FollowupsEvent {
  type: "followups"
  questions: FollowupQuestion[]
}

// One of ``url`` or ``inline_query`` is set.
//   - ``inline_query`` feeds the string back into the chat as a new
//     user turn (rendered inline by the right specialist — catalog,
//     balance, outage status, etc.).
//   - ``url`` opens externally in a new tab (account settings,
//     external handoff pages).
export interface ActionLink {
  label: string
  topic: string
  url?: string | null
  inline_query?: string | null
}

export interface ActionsEvent {
  type: "actions"
  actions: ActionLink[]
}

export interface ScopeWarnEvent {
  type: "scope_warn"
  message: string
}

export interface ErrorEvent {
  type: "error"
  message: string
  code: string
}

export interface DoneEvent {
  type: "done"
  total_chunks_used: number
  sources_used: string[]
  faithfulness_passed: boolean
  latency_ms: number
  retrieval_ms?: number
  generation_ms?: number
  validation_ms?: number
  // LLM usage summed across every agent call in the turn.
  // Null when the provider did not report usage.
  input_tokens?: number | null
  output_tokens?: number | null
  total_tokens?: number | null
  llm_requests?: number | null
  // Approximate USD cost of the turn, summed across the per-call pricing
  // (different slots use different models). Null for self-hosted or
  // unpriced models.
  cost_usd?: number | null
}

// Matches backend ``SpecialistInfoEvent``.
export interface SpecialistInfoEvent {
  type: "specialist_info"
  specialist: string
  confidence: number
}

// Matches backend ``ToolCallEvent``.
export interface ToolCallEvent {
  type: "tool_call"
  tool_name: string
  status: "calling" | "success" | "error"
}

// Rich card — products, payment methods, appointment slots, and the
// customer's existing appointments (display + CTA buttons).
export interface CardItem {
  kind: "product" | "payment_method" | "appointment_slot" | "appointment"
  id: string
  title: string
  subtitle?: string | null
  image_url?: string | null
  badges?: string[]
  metadata?: Record<string, unknown>
}

export interface CardsEvent {
  type: "cards"
  prompt?: string | null
  kind: string
  cards: CardItem[]
}

// In-chat buttons that POST back to the server.
export interface InteractiveAction {
  label: string
  action_id: string
  kind:
    | "place_order"
    | "pay"
    | "book_appointment"
    | "cancel_appointment"
    | "reschedule_appointment"
    | "cancel_order"
    | "discard_order_draft"
    | "enroll_autopay"
    | "add_payment_method"
    | "set_default_payment_method"
    | "sign_in"
    | "sign_out"
  confirm_text?: string | null
  payload?: Record<string, unknown>
  expires_at: string
}

export interface InteractiveActionsEvent {
  type: "interactive_actions"
  actions: InteractiveAction[]
}

export interface ActionResultEvent {
  type: "action_result"
  action_id: string
  kind: string
  status: "success" | "error" | "pending"
  message: string
  detail?: Record<string, unknown>
}

export interface WhoAmI {
  customer_id: string
  is_guest: boolean
}

// GET /support/conversations/{id}/totals response.
export interface ConversationTotals {
  conversation_id: string
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: number
  message_count: number
}

// GET /support/conversations — one row per past conversation for a customer.
export interface ConversationSummary {
  id: string
  customer_id: string
  status: string
  created_at: string | null
  updated_at: string | null
  last_specialist: string | null
  title: string | null
  preview: string | null
  message_count: number
  cost_usd: number
}

// GET /support/conversations/{id}/messages — one row per persisted message.
export interface StoredMessage {
  id: string
  role: string
  content: string
  specialist_used: string | null
  citations_json: Record<string, unknown> | null
  created_at: string | null
}

export type SSEEvent =
  | ThinkingEvent
  | SourcesEvent
  | CodeEvent
  | TextEvent
  | WikiEvent
  | FollowupsEvent
  | ActionsEvent
  | SpecialistInfoEvent
  | ToolCallEvent
  | ScopeWarnEvent
  | ErrorEvent
  | DoneEvent
  | CardsEvent
  | InteractiveActionsEvent
  | ActionResultEvent

export interface CreateSourceRequest {
  name: string
  source_type: string
  config?: Record<string, unknown>
  credential_id?: string | null
}

export interface UpdateSourceRequest {
  name?: string
  source_type?: string
  config?: Record<string, unknown>
  credential_id?: string | null
  is_active?: boolean
}

export interface CreateCredentialRequest {
  name: string
  credential_type: string
  value: string
  description?: string
}

export interface UpdateCredentialRequest {
  name?: string
  credential_type?: string
  value?: string
  description?: string
}

// LLM / embedding / rerank settings
export interface LLMProvider {
  id: string
  name: string
  models: Record<string, string>
}

export interface LLMSettings {
  active_provider: string                  // chat/generation provider
  active_embedding_provider?: string       // voyage | openai | google | ollama
  active_rerank_provider?: string          // llm
  embedding_model?: string                 // e.g. "voyage-code-3"
  embedding_dim?: number                   // e.g. 1024
  rerank_model?: string                    // e.g. "gpt-4o-mini"
  providers: LLMProvider[]
}

export interface UpdateLLMSettingsRequest {
  provider?: string
  embedding_provider?: string
  rerank_provider?: string
}

// Stored chunk preview (what the embedder actually indexed)
export interface StoredChunk {
  id: string
  source_id: string
  source_type: string
  file_path: string
  language: string
  chunk_type: string
  name: string
  qualified_name: string
  start_line: number
  end_line: number
  summary: string | null
  purpose: string | null
  signature: string | null
  reuse_signal: string | null
  side_effects: string | null
  example_call: string | null
  domain_tags: string[]
  complexity: string | null
  embed_input: string | null
  content: string
  indexed_at: string | null
}
