import { fetchEventSource } from "@microsoft/fetch-event-source"
import { useAuthStore } from "@/store/authStore"
import type { QueryRequest, SSEEvent } from "./types"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"
const DEVCHAT_URL = `${API_BASE}/v1/devchat/query`
const SUPPORT_URL = `${API_BASE}/v1/support/query`

export interface SSECallbacks {
  onEvent: (event: SSEEvent) => void
  onError?: (err: unknown) => void
  onClose?: () => void
  // Called once with the response object on successful open. Used by the
  // support stream to read the X-Conversation-Id header the server sets
  // when a new conversation is created.
  onOpen?: (response: Response) => void
}

interface SSERequest {
  url: string
  body: unknown
  extraHeaders?: Record<string, string>
}

const streamSSE = (
  { url, body, extraHeaders }: SSERequest,
  callbacks: SSECallbacks,
): AbortController => {
  const ctrl = new AbortController()
  const apiKey = useAuthStore.getState().apiKey

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(extraHeaders ?? {}),
  }
  if (apiKey) headers["X-API-Key"] = apiKey

  fetchEventSource(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: ctrl.signal,
    openWhenHidden: true,
    // Include the session cookie (support_session) so the backend can
    // identify the customer without a header. Header fallback still
    // works for service-to-service callers.
    credentials: "include",

    onopen: async (response) => {
      if (!response.ok) {
        const text = await response.text()
        throw new Error(`Stream failed: ${response.status} ${text}`)
      }
      callbacks.onOpen?.(response)
    },

    onmessage: (msg) => {
      if (!msg.data) return
      try {
        const event: SSEEvent = JSON.parse(msg.data)
        callbacks.onEvent(event)
      } catch (err) {
        console.error("[SSE] Failed to parse event:", msg.data, err)
      }
    },

    onerror: (err) => {
      callbacks.onError?.(err)
    },

    onclose: () => {
      callbacks.onClose?.()
    },
  }).catch((err) => {
    if (err.name !== "AbortError") {
      callbacks.onError?.(err)
    }
  })

  return ctrl
}

// Developer-RAG (devchat) endpoint — stateless, no conversation history,
// no customer identity.
export const streamDevChat = (
  body: QueryRequest,
  callbacks: SSECallbacks,
): AbortController => streamSSE({ url: DEVCHAT_URL, body }, callbacks)

export interface SupportQueryRequest {
  query: string
  source_ids?: string[]
  provider?: string
}

export interface SupportStreamOptions {
  customerId: string
  // Omit on the first turn; pass back the value the server returned via
  // X-Conversation-Id (read in the onOpen callback) for subsequent turns.
  conversationId?: string
  // Force the server to start a fresh conversation even if conversationId
  // is provided.
  newConversation?: boolean
}

// Customer-support endpoint — stateful (per-customer conversations) and
// requires X-Customer-Id. The server returns X-Conversation-Id on the
// response; surface it via callbacks.onOpen.
export const streamSupport = (
  body: SupportQueryRequest,
  options: SupportStreamOptions,
  callbacks: SSECallbacks,
): AbortController => {
  const extraHeaders: Record<string, string> = {
    "X-Customer-Id": options.customerId,
  }
  if (options.conversationId) {
    extraHeaders["X-Conversation-Id"] = options.conversationId
  }
  if (options.newConversation) {
    extraHeaders["X-New-Conversation"] = "true"
  }
  return streamSSE({ url: SUPPORT_URL, body, extraHeaders }, callbacks)
}

