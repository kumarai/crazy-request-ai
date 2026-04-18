import { useAuthStore } from "@/store/authStore"
import type {
  ConversationSummary,
  ConversationTotals,
  CreateCredentialRequest,
  CreateSourceRequest,
  Credential,
  Job,
  LLMSettings,
  Source,
  StoredChunk,
  StoredMessage,
  UpdateCredentialRequest,
  UpdateLLMSettingsRequest,
  UpdateSourceRequest,
} from "./types"

const BASE = `${import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"}/v1`

const authHeaders = (): Record<string, string> => {
  const key = useAuthStore.getState().apiKey
  return key ? { "X-API-Key": key } : {}
}

const request = async <T>(path: string, init?: RequestInit): Promise<T> => {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...((init?.headers as Record<string, string>) ?? {}),
    },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.error || body.detail || `HTTP ${res.status}`)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

// Health
export const healthCheck = () =>
  request<{ status: string; db: string; redis: string; version: string }>("/health")

// Sources
export const listSources = () => request<Source[]>("/sources")

export const createSource = (body: CreateSourceRequest) =>
  request<Source>("/sources", { method: "POST", body: JSON.stringify(body) })

export const updateSource = (id: string, body: UpdateSourceRequest) =>
  request<Source>(`/sources/${id}`, { method: "PUT", body: JSON.stringify(body) })

export const deleteSource = (id: string) => request<void>(`/sources/${id}`, { method: "DELETE" })

export const syncSource = (id: string, mode = "incremental") =>
  request<{ job_id: string; status: string }>(`/sources/${id}/sync?mode=${mode}`, {
    method: "POST",
  })

// Re-embed without re-parsing — keeps summary/purpose, rebuilds embed_input + embedding.
// Useful when switching embedding model (e.g. OpenAI → Voyage).
export const reembedSource = (id: string) =>
  request<{ job_id: string; status: string }>(`/sources/${id}/reembed`, {
    method: "POST",
  })

export const getSourceStatus = (id: string) => request<Job>(`/sources/${id}/status`)

// List indexed chunks for a source — shows embed_input so users can debug
// retrieval quality by seeing exactly what the embedder produced.
export const listSourceChunks = (
  sourceId: string,
  opts: { limit?: number; q?: string } = {},
) => {
  const p = new URLSearchParams()
  if (opts.limit) p.set("limit", String(opts.limit))
  if (opts.q) p.set("q", opts.q)
  const qs = p.toString()
  return request<StoredChunk[]>(`/sources/${sourceId}/chunks${qs ? `?${qs}` : ""}`)
}

// Credentials
export const listCredentials = () => request<Credential[]>("/credentials")

export const createCredential = (body: CreateCredentialRequest) =>
  request<Credential>("/credentials", { method: "POST", body: JSON.stringify(body) })

export const updateCredential = (id: string, body: UpdateCredentialRequest) =>
  request<Credential>(`/credentials/${id}`, { method: "PUT", body: JSON.stringify(body) })

export const deleteCredential = (id: string) =>
  request<void>(`/credentials/${id}`, { method: "DELETE" })

// Jobs
export const listJobs = (sourceId?: string, limit = 20) => {
  const params = new URLSearchParams()
  if (sourceId) params.set("source_id", sourceId)
  params.set("limit", String(limit))
  return request<Job[]>(`/jobs?${params}`)
}

export const getJob = (id: string) => request<Job>(`/jobs/${id}`)

// File Uploads
export const getUploadUrl = (sourceId: string, filename: string, contentType: string) =>
  request<{ upload_url: string; object_key: string }>(`/sources/${sourceId}/upload-url`, {
    method: "POST",
    body: JSON.stringify({ filename, content_type: contentType }),
  })

export const listSourceFiles = (sourceId: string) =>
  request<{ key: string; name: string; size: number; last_modified: string }[]>(
    `/sources/${sourceId}/files`,
  )

export const deleteSourceFile = (sourceId: string, filename: string) =>
  request<{ deleted: string }>(`/sources/${sourceId}/files/${filename}`, { method: "DELETE" })

// LLM / embedding / rerank settings
export const getLLMSettings = () => request<LLMSettings>("/settings/llm")

export const updateLLMSettings = (body: UpdateLLMSettingsRequest) =>
  request<LLMSettings>("/settings/llm", {
    method: "PUT",
    body: JSON.stringify(body),
  })

// Support — log a universal-link click as an action message in the transcript.
// Server looks up label/URL from the catalog using the topic, so a client
// can't log arbitrary URLs.
export interface ActionClickResponse {
  message_id: string
  topic: string
  url: string
  label: string
}

export const postActionClick = (
  conversationId: string,
  customerId: string,
  topic: string,
) =>
  request<ActionClickResponse>(
    `/support/conversations/${conversationId}/action_click`,
    {
      method: "POST",
      headers: { "X-Customer-Id": customerId },
      body: JSON.stringify({ topic }),
    },
  )

// Running token + USD totals for an entire support conversation. UI calls
// this after each ``done`` event to refresh the conversation-level badge.
export const getConversationTotals = (
  conversationId: string,
  customerId: string,
) =>
  request<ConversationTotals>(
    `/support/conversations/${conversationId}/totals`,
    { headers: { "X-Customer-Id": customerId } },
  )

export const listConversations = (customerId: string, limit = 50) =>
  request<ConversationSummary[]>(
    `/support/conversations?limit=${limit}`,
    { headers: { "X-Customer-Id": customerId } },
  )

export const getConversationMessages = (
  conversationId: string,
  customerId: string,
  limit = 200,
) =>
  request<StoredMessage[]>(
    `/support/conversations/${conversationId}/messages?limit=${limit}`,
    { headers: { "X-Customer-Id": customerId } },
  )
