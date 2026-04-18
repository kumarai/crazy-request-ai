import { useCallback, useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { ScrollArea } from "@/components/ui/scroll-area"
import { streamSupport } from "@/api/sse"
import {
  getConversationMessages,
  listConversations,
  postActionClick,
} from "@/api/client"
import type {
  ActionLink,
  CardItem,
  ConversationSummary,
  SSEEvent,
  StoredMessage,
} from "@/api/types"
import {
  Transcript,
  type Message,
  nextMsgId,
} from "@/components/chat/Transcript"
import { useSupportStore } from "@/store/supportStore"
import { SessionBanner } from "@/components/auth/SessionBanner"
import { LoginDialog } from "@/components/auth/LoginDialog"
import { useSessionStore } from "@/store/sessionStore"
import { whoami } from "@/api/auth"

export const SupportChatPage = () => {
  const qc = useQueryClient()
  const { customerId, setCustomerId, conversationId, setConversationId } =
    useSupportStore()
  const clearSession = useSessionStore((s) => s.clear)
  const setSession = useSessionStore((s) => s.setSession)
  const [customerInput, setCustomerInput] = useState(customerId)

  const [query, setQuery] = useState("")
  const [messages, setMessages] = useState<Message[]>([])
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auth-gate modal. Opens when the account specialist's sign_in
  // interactive action is clicked, or when the user hits the Sign-in
  // button in the session banner directly.
  const [authDialog, setAuthDialog] = useState<{
    open: boolean
    message?: string
    allowGuest: boolean
  }>({ open: false, allowGuest: true })

  // Past conversations for this customer — powers the sidebar switcher.
  const convListQuery = useQuery({
    queryKey: ["support-conversations", customerId],
    queryFn: () => listConversations(customerId),
    enabled: Boolean(customerId),
  })

  // On load (or when switching to an existing conversation), fetch its
  // persisted transcript so the user sees prior turns. We only run when
  // there's an ID but no in-memory messages — avoids clobbering a live
  // conversation mid-stream.
  const loadMessagesMut = useMutation({
    mutationFn: (id: string) => getConversationMessages(id, customerId),
    onSuccess: (rows) => {
      setMessages(rows.map(storedMessageToUI).filter((m): m is Message => m !== null))
    },
  })
  const loadMessages = loadMessagesMut.mutate

  useEffect(() => {
    if (!customerId || !conversationId) return
    if (messages.length > 0) return
    loadMessages(conversationId)
    // We intentionally key off customerId + conversationId. Fetching again
    // when `messages` grows would wipe the live transcript.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customerId, conversationId])

  const saveCustomer = () => {
    const trimmed = customerInput.trim()
    if (trimmed !== customerId) {
      setCustomerId(trimmed)
      setMessages([])
    }
  }

  const addEvent = useCallback((event: SSEEvent) => {
    setMessages((prev) => {
      const updated = [...prev]
      const last = updated[updated.length - 1]
      if (last && last.role === "assistant") {
        updated[updated.length - 1] = { ...last, events: [...last.events, event] }
      }
      return updated
    })
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })
  }, [])

  const runQuery = useCallback(
    (q: string) => {
      if (!customerId) return
      const userMsg: Message = { id: nextMsgId(), role: "user", query: q, events: [] }
      const assistantMsg: Message = { id: nextMsgId(), role: "assistant", events: [] }
      setMessages((prev) => [...prev, userMsg, assistantMsg])
      setStreaming(true)

      const ctrl = streamSupport(
        { query: q },
        { customerId, conversationId },
        {
          onOpen: (response) => {
            const cid = response.headers.get("X-Conversation-Id")
            if (cid && cid !== conversationId) {
              setConversationId(cid)
              // New conversation just got an ID — refresh the sidebar.
              qc.invalidateQueries({
                queryKey: ["support-conversations", customerId],
              })
            }
          },
          onEvent: addEvent,
          onError: () => setStreaming(false),
          onClose: () => {
            setStreaming(false)
            // Refresh preview + updated_at in the sidebar after each turn.
            qc.invalidateQueries({
              queryKey: ["support-conversations", customerId],
            })
          },
        },
      )
      abortRef.current = ctrl
    },
    [customerId, conversationId, addEvent, setConversationId, qc],
  )

  const handleSubmit = () => {
    const q = query.trim()
    if (!q || streaming || !customerId) return
    setQuery("")
    runQuery(q)
  }

  const handleCancel = () => {
    abortRef.current?.abort()
    setStreaming(false)
  }

  const handleFollowup = (question: string) => {
    setQuery(question)
  }

  // Button-click result: append an ACTION_RESULT event to the current
  // assistant message so the inline success/error banner renders in
  // the transcript. Also refresh the conversation list so the sidebar
  // picks up the action_result row we persisted server-side.
  const handleActionResult = useCallback(
    (result: SSEEvent) => {
      // Session-management kinds need side effects the transcript
      // renderer can't do on its own.
      if (result.type === "action_result") {
        if (
          result.kind === "sign_in" &&
          (result.detail as { open_login_dialog?: boolean } | undefined)
            ?.open_login_dialog
        ) {
          setAuthDialog({
            open: true,
            message: result.message,
            allowGuest: true,
          })
          // Sign-in action returns ``pending`` — don't render a banner
          // for it, the LoginDialog is the feedback.
          return
        }
        if (
          result.kind === "sign_out" &&
          (result.detail as { cleared_session?: boolean } | undefined)
            ?.cleared_session
        ) {
          clearSession()
          // Refresh whoami so the banner flips to guest immediately
          // and the next turn picks up a fresh guest cookie.
          whoami()
            .then((me) => setSession(me.customer_id, me.is_guest))
            .catch((err) => console.error("whoami after sign_out failed:", err))
        }
      }

      setMessages((prev) => {
        const updated = [...prev]
        const last = updated[updated.length - 1]
        if (last && last.role === "assistant") {
          updated[updated.length - 1] = {
            ...last,
            events: [...last.events, result],
          }
        }
        return updated
      })
      qc.invalidateQueries({
        queryKey: ["support-conversations", customerId],
      })
    },
    [qc, customerId, clearSession, setSession],
  )

  const handleActionClick = (action: ActionLink) => {
    // Inline action: feed the canned query back into the chat as a
    // new user turn. The right specialist renders the answer inline
    // (product cards, balance, outage status, etc.) — no new tab,
    // no page navigation.
    if (action.inline_query) {
      runQuery(action.inline_query)
      return
    }

    // External action: ``<a target="_blank">`` already opened the URL.
    // Drop a transcript marker and persist the click so history
    // replays include it.
    const clickMsg: Message = {
      id: nextMsgId(),
      role: "action",
      query: `Clicked: ${action.label}`,
      events: [],
    }
    setMessages((prev) => [...prev, clickMsg])

    if (customerId && conversationId) {
      postActionClick(conversationId, customerId, action.topic).catch((err) => {
        console.error("Failed to log action click:", err)
      })
    }
  }

  const handleOrderNow = useCallback(
    (card: CardItem) => {
      // Auto-submit an order-intent query so the order specialist can
      // start the quote → list_payment_methods → propose_place_order
      // flow immediately. Include the SKU so the specialist doesn't
      // have to re-search the catalog.
      const skuHint = card.id ? ` (sku ${card.id})` : ""
      runQuery(`I'd like to order the ${card.title}${skuHint}.`)
    },
    [runQuery],
  )

  // Auto-submit when the customer taps a payment method or
  // appointment slot card. The specialist's preceding prompt asked
  // which one — the click is the answer. Making them press Enter
  // afterwards is pointless friction.
  //
  // The phrase is deliberately specialist-neutral: no "Visa", "pay",
  // "bill", or other brand/domain keywords that the router's LLM
  // would associate with bill_pay. "Selected <kind> (id ...)" is a
  // continuation verb that the router's continuity rule keeps on
  // whichever specialist asked the question.
  const handleCardSelect = useCallback(
    (card: CardItem) => {
      let phrase: string
      if (card.kind === "payment_method") {
        phrase = `Selected payment method id ${card.id}. Continue.`
      } else if (card.kind === "appointment_slot") {
        phrase = `Selected slot id ${card.id}. Continue.`
      } else {
        phrase = `Selected ${card.title}. Continue.`
      }
      runQuery(phrase)
    },
    [runQuery],
  )

  const handleAppointmentReschedule = useCallback(
    (card: CardItem) => {
      // Triggers the appointment specialist to list open slots for the
      // same topic and then propose_reschedule_appointment.
      const topic = String(card.metadata?.topic ?? "")
      const topicHint = topic ? ` (topic ${topic})` : ""
      runQuery(
        `Reschedule appointment id ${card.id}${topicHint}. Show me new available slots.`,
      )
    },
    [runQuery],
  )

  const handleAppointmentCancel = useCallback(
    (card: CardItem) => {
      const when = card.title || String(card.metadata?.slot_start ?? "")
      const whenHint = when ? ` (${when})` : ""
      runQuery(
        `Cancel appointment id ${card.id}${whenHint}.`,
      )
    },
    [runQuery],
  )

  const handleNewConversation = () => {
    setConversationId(undefined)
    setMessages([])
  }

  const handleSelectConversation = (id: string) => {
    if (id === conversationId) return
    setConversationId(id)
    setMessages([])
    loadMessages(id)
  }

  const customerSet = Boolean(customerId)

  return (
    // ``min-h-0`` on the outer + inner flex containers lets the
    // Transcript shrink below content size so its internal scroll
    // takes over instead of pushing the input field off-screen.
    // Without these, flex items default to min-height: auto (=
    // content size) and ``<main>``'s overflow-auto ends up scrolling
    // the whole page.
    <div className="flex h-full min-h-0">
      {/* Sidebar */}
      {customerSet && (
        <div className="w-64 border-r flex flex-col shrink-0 min-h-0">
          <div className="px-3 py-2 border-b flex items-center justify-between">
            <span className="text-xs font-semibold">Conversations</span>
            <Button
              size="xs"
              variant="outline"
              onClick={handleNewConversation}
              disabled={streaming}
            >
              + New
            </Button>
          </div>
          <ScrollArea className="flex-1">
            <ConversationList
              conversations={convListQuery.data ?? []}
              activeId={conversationId}
              onSelect={handleSelectConversation}
              isLoading={convListQuery.isLoading}
            />
          </ScrollArea>
        </div>
      )}

      {/* Main chat panel */}
      <div className="flex flex-col flex-1 min-w-0 min-h-0">
        <SessionBanner />
        <LoginDialog
          open={authDialog.open}
          onOpenChange={(open) =>
            setAuthDialog((prev) => ({ ...prev, open }))
          }
          headerMessage={authDialog.message}
          allowContinueAsGuest={authDialog.allowGuest}
        />
        <div className="border-b px-4 py-2 flex items-center gap-2 shrink-0">
          <span className="text-xs text-muted-foreground whitespace-nowrap">
            Customer ID:
          </span>
          <Input
            placeholder="e.g. cust-001"
            value={customerInput}
            onChange={(e) => setCustomerInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveCustomer()}
            className="max-w-[200px] h-7 text-xs"
          />
          <Button
            size="xs"
            variant={
              customerInput.trim() === customerId && customerId ? "secondary" : "default"
            }
            onClick={saveCustomer}
          >
            Save
          </Button>
          {customerSet && (
            <>
              <span className="text-[10px] text-green-600 whitespace-nowrap">● set</span>
              {conversationId && (
                <span className="text-[10px] text-muted-foreground ml-2 truncate">
                  conv: {conversationId.slice(0, 8)}…
                </span>
              )}
            </>
          )}
        </div>

        <Transcript
          messages={messages}
          onFollowup={handleFollowup}
          onActionClick={handleActionClick}
          onActionResult={handleActionResult}
          onOrderNow={handleOrderNow}
          onCardSelect={handleCardSelect}
          onAppointmentReschedule={handleAppointmentReschedule}
          onAppointmentCancel={handleAppointmentCancel}
          scrollRef={scrollRef}
          emptyState={
            <div className="flex items-center justify-center h-full text-muted-foreground">
              <div className="text-center space-y-2">
                <p className="text-lg font-medium">Customer support chat</p>
                <p className="text-sm">
                  {customerSet
                    ? "Ask about billing, internet, devices, or your account"
                    : "Set a Customer ID above to start"}
                </p>
              </div>
            </div>
          }
        />

        <div className="border-t p-4 shrink-0">
          <div className="flex gap-2">
            <Textarea
              placeholder={
                customerSet ? "How can we help?" : "Set a Customer ID above first"
              }
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  handleSubmit()
                }
              }}
              rows={2}
              className="resize-none"
              disabled={!customerSet}
            />
            {streaming ? (
              <Button variant="destructive" onClick={handleCancel} className="self-end">
                Cancel
              </Button>
            ) : (
              <Button
                onClick={handleSubmit}
                className="self-end"
                disabled={!query.trim() || !customerSet}
              >
                Send
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// Map a persisted message row to the shape the Transcript expects.
// System rows aren't rendered, so drop them. Assistant rows expand
// their citations_json snapshot back into SSE events so action buttons,
// followup chips, scope warnings, and specialist info re-render.
const storedMessageToUI = (m: StoredMessage): Message | null => {
  if (m.role === "user") {
    return { id: m.id, role: "user", query: m.content, events: [] }
  }
  if (m.role === "assistant") {
    const events: SSEEvent[] = []
    const c = (m.citations_json ?? {}) as Record<string, unknown>

    if (typeof c.specialist === "string") {
      events.push({
        type: "specialist_info",
        specialist: c.specialist,
        confidence:
          typeof c.router_confidence === "number" ? c.router_confidence : 1,
      })
    }

    if (
      c.sources &&
      typeof c.sources === "object" &&
      Array.isArray((c.sources as { chunks?: unknown }).chunks)
    ) {
      const s = c.sources as {
        total_searched?: number
        chunks: unknown[]
      }
      events.push({
        type: "sources",
        total_searched: s.total_searched ?? 0,
        chunks: s.chunks as import("@/api/types").ChunkPreview[],
      })
    }

    if (m.content) {
      events.push({ type: "text", content: m.content })
    }

    if (typeof c.scope_warn === "string" && c.scope_warn) {
      events.push({ type: "scope_warn", message: c.scope_warn })
    }

    if (Array.isArray(c.followups) && c.followups.length > 0) {
      events.push({
        type: "followups",
        questions: c.followups as { question: string; category: string }[],
      })
    }

    if (Array.isArray(c.actions) && c.actions.length > 0) {
      events.push({
        type: "actions",
        actions: c.actions as ActionLink[],
      })
    }

    return { id: m.id, role: "assistant", events }
  }
  if (m.role === "action") {
    return { id: m.id, role: "action", query: m.content, events: [] }
  }
  return null
}

const ConversationList = ({
  conversations,
  activeId,
  onSelect,
  isLoading,
}: {
  conversations: ConversationSummary[]
  activeId: string | undefined
  onSelect: (id: string) => void
  isLoading: boolean
}) => {
  if (isLoading) {
    return <p className="p-3 text-xs text-muted-foreground">Loading…</p>
  }
  if (conversations.length === 0) {
    return (
      <p className="p-3 text-xs text-muted-foreground">
        No past conversations yet.
      </p>
    )
  }
  return (
    <ul className="divide-y">
      {conversations.map((c) => (
        <li key={c.id}>
          <button
            type="button"
            onClick={() => onSelect(c.id)}
            className={`w-full text-left px-3 py-2 hover:bg-accent text-xs ${
              c.id === activeId ? "bg-accent" : ""
            }`}
          >
            <div className="font-medium truncate">
              {c.title || c.preview || "(empty)"}
            </div>
            <div className="text-[10px] text-muted-foreground mt-0.5 flex gap-2">
              <span>{formatDate(c.updated_at)}</span>
              <span>·</span>
              <span>{c.message_count} msgs</span>
              <span>·</span>
              <span>{formatCost(c.cost_usd)}</span>
            </div>
          </button>
        </li>
      ))}
    </ul>
  )
}

const formatCost = (usd: number) => {
  if (!usd) return "$0"
  if (usd < 0.01) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
}

const formatDate = (iso: string | null) => {
  if (!iso) return ""
  const d = new Date(iso)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
  }
  return d.toLocaleDateString([], { month: "short", day: "numeric" })
}
