import { useEffect, useMemo, useRef, useState } from "react"
import type React from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/reui/badge"
import { Alert, AlertTitle, AlertDescription } from "@/components/reui/alert"
import type {
  ActionLink,
  ActionResultEvent as ActionResultEventType,
  CardItem,
  CardsEvent,
  ChunkPreview,
  CodeEvent,
  DoneEvent,
  FollowupQuestion,
  InteractiveActionsEvent,
  SSEEvent,
  ScopeWarnEvent,
  SpecialistInfoEvent,
  TextEvent,
  ThinkingEvent,
  ToolCallEvent,
} from "@/api/types"
import { CardGrid } from "./CardGrid"
import { InteractiveActions } from "./InteractiveActions"
import { ActionResult } from "./ActionResult"

export interface Message {
  id: string
  role: "user" | "assistant" | "action"
  query?: string
  events: SSEEvent[]
}

let _msgId = 0
export const nextMsgId = () => `msg-${++_msgId}`

export interface TranscriptProps {
  messages: Message[]
  onFollowup: (q: string) => void
  onActionClick: (action: ActionLink) => void
  // Called after a server action (pay/place_order/book/etc.) resolves.
  // Parent page should append the event to its message state so the
  // success / error banner renders inline with the conversation.
  onActionResult?: (event: ActionResultEventType) => void
  // Called when a product card's "Order now" button is clicked — the
  // parent should submit a new turn (not just fill the textbox) so the
  // order specialist can start the quote → payment → confirm flow.
  onOrderNow?: (card: CardItem) => void
  // Called when a payment-method or appointment-slot card is clicked.
  // Parent auto-submits a confirmation turn. If omitted, the renderer
  // falls back to pre-filling the input (legacy behavior).
  onCardSelect?: (card: CardItem) => void
  // Called from an appointment card's CTAs. Parent auto-submits a
  // reschedule / cancel intent so the appointment specialist can drive
  // the flow.
  onAppointmentReschedule?: (card: CardItem) => void
  onAppointmentCancel?: (card: CardItem) => void
  scrollRef?: React.RefObject<HTMLDivElement | null>
  emptyState?: React.ReactNode
}

export const Transcript = ({
  messages,
  onFollowup,
  onActionClick,
  onActionResult,
  onOrderNow,
  onCardSelect,
  onAppointmentReschedule,
  onAppointmentCancel,
  scrollRef,
  emptyState,
}: TranscriptProps) => {
  // ScrollArea from shadcn/base-ui renders a Root with a nested
  // Viewport — the Viewport is the actual scrollable element. The
  // caller's ``scrollRef`` attaches to the Root, so ``scrollTo`` on
  // it is a no-op. We grab the Viewport via its ``data-slot`` here
  // and auto-scroll to the bottom whenever the transcript changes
  // (new messages OR new streamed events into an existing message).
  const rootRef = useRef<HTMLDivElement>(null)

  // A signature that changes on every message/event append — drives
  // the auto-scroll effect. We hash the last message's event count
  // plus its last-event JSON length so streamed text deltas tick it
  // too, not just full-message appends.
  const scrollSig = useMemo(() => {
    const last = messages[messages.length - 1]
    const lastEvCount = last?.events.length ?? 0
    const lastEv = last?.events[lastEvCount - 1]
    const lastEvSize =
      lastEv && "content" in lastEv && typeof lastEv.content === "string"
        ? lastEv.content.length
        : 0
    return `${messages.length}:${lastEvCount}:${lastEvSize}`
  }, [messages])

  useEffect(() => {
    const root = rootRef.current
    if (!root) return
    const viewport = root.querySelector(
      '[data-slot="scroll-area-viewport"]',
    ) as HTMLElement | null
    if (!viewport) return
    // ``smooth`` on every delta makes streaming feel jittery; use
    // instant scrolling which is still visually fine at 60 Hz.
    viewport.scrollTop = viewport.scrollHeight
  }, [scrollSig])

  // Expose the root ref back to the parent for anything that still
  // needs it (mainly the ``scrollRef`` prop contract). If the parent
  // calls ``scrollTo`` on it, it's a no-op — but we honour the API.
  useEffect(() => {
    if (scrollRef && rootRef.current) {
      (scrollRef as React.MutableRefObject<HTMLDivElement | null>).current =
        rootRef.current
    }
  }, [scrollRef])

  return (
    <ScrollArea ref={rootRef} className="flex-1 min-h-0 p-4 space-y-4">
      {messages.length === 0 && emptyState}
      {messages.map((msg) => (
        <div key={msg.id} className="mb-4">
          {msg.role === "user" ? (
            <div className="flex justify-end mb-2">
              <div className="bg-primary text-primary-foreground rounded-lg px-4 py-2 max-w-[70%] text-sm">
                {msg.query}
              </div>
            </div>
          ) : msg.role === "action" ? (
            <div className="flex justify-end mb-2">
              <div className="bg-muted text-muted-foreground rounded-lg px-4 py-2 max-w-[70%] text-xs italic">
                {msg.query}
              </div>
            </div>
          ) : (
            <AssistantMessage
              msg={msg}
              onFollowup={onFollowup}
              onActionClick={onActionClick}
              onActionResult={onActionResult}
              onOrderNow={onOrderNow}
              onCardSelect={onCardSelect}
              onAppointmentReschedule={onAppointmentReschedule}
              onAppointmentCancel={onAppointmentCancel}
            />
          )}
        </div>
      ))}
    </ScrollArea>
  )
}

// Assistant message wrapper: renders events + a copy button anchored to
// the accumulated text. Consecutive TextEvents (as emitted by streaming)
// are collapsed into one paragraph so the reader sees flowing text rather
// than one `<p>` per token.
const AssistantMessage = ({
  msg,
  onFollowup,
  onActionClick,
  onActionResult,
  onOrderNow,
  onCardSelect,
  onAppointmentReschedule,
  onAppointmentCancel,
}: {
  msg: Message
  onFollowup: (q: string) => void
  onActionClick: (action: ActionLink) => void
  onActionResult?: (event: ActionResultEventType) => void
  onOrderNow?: (card: CardItem) => void
  onCardSelect?: (card: CardItem) => void
  onAppointmentReschedule?: (card: CardItem) => void
  onAppointmentCancel?: (card: CardItem) => void
}) => {
  const accumulatedText = msg.events
    .filter((e): e is TextEvent => e.type === "text")
    .map((e) => e.content)
    .join("")

  return (
    <div className="space-y-2 group/assistant relative">
      {renderGroupedEvents(
        msg.events,
        msg.id,
        onFollowup,
        onActionClick,
        onActionResult,
        onOrderNow,
        onCardSelect,
        onAppointmentReschedule,
        onAppointmentCancel,
      )}
      {accumulatedText && <CopyButton text={accumulatedText} />}
    </div>
  )
}

const renderGroupedEvents = (
  events: SSEEvent[],
  msgId: string,
  onFollowup: (q: string) => void,
  onActionClick: (action: ActionLink) => void,
  onActionResult?: (event: ActionResultEventType) => void,
  onOrderNow?: (card: CardItem) => void,
  onCardSelect?: (card: CardItem) => void,
  onAppointmentReschedule?: (card: CardItem) => void,
  onAppointmentCancel?: (card: CardItem) => void,
) => {
  const out: React.ReactNode[] = []
  let textBuf = ""
  let textIdx = 0
  const flushText = () => {
    if (textBuf) {
      out.push(
        <p
          key={`${msgId}-text-${textIdx++}`}
          className="text-sm whitespace-pre-wrap"
        >
          {textBuf}
        </p>,
      )
      textBuf = ""
    }
  }
  // Backward-compat: older transcripts may contain a ``done`` event
  // followed by ``scope_warn`` from the earlier post-hoc validation
  // flow. Pre-scan and downgrade the badge so rehydrated history stays
  // internally consistent.
  const hasTrailingScopeWarn = events.some((e) => e.type === "scope_warn")
  events.forEach((evt, j) => {
    if (evt.type === "text") {
      textBuf += (evt as TextEvent).content
      return
    }
    flushText()
    const renderEvt =
      evt.type === "done" && hasTrailingScopeWarn
        ? { ...(evt as DoneEvent), faithfulness_passed: false }
        : evt
    out.push(
      <EventRenderer
        key={`${msgId}-evt-${j}`}
        event={renderEvt}
        onFollowup={onFollowup}
        onActionClick={onActionClick}
        onActionResult={onActionResult}
        onOrderNow={onOrderNow}
        onCardSelect={onCardSelect}
        onAppointmentReschedule={onAppointmentReschedule}
        onAppointmentCancel={onAppointmentCancel}
      />,
    )
  })
  flushText()
  return out
}

const EventRenderer = ({
  event,
  onFollowup,
  onActionClick,
  onActionResult,
  onOrderNow,
  onCardSelect,
  onAppointmentReschedule,
  onAppointmentCancel,
}: {
  event: SSEEvent
  onFollowup: (q: string) => void
  onActionClick: (action: ActionLink) => void
  onActionResult?: (event: ActionResultEventType) => void
  onOrderNow?: (card: CardItem) => void
  onCardSelect?: (card: CardItem) => void
  onAppointmentReschedule?: (card: CardItem) => void
  onAppointmentCancel?: (card: CardItem) => void
}) => {
  switch (event.type) {
    case "thinking":
      return <ThinkingBubble event={event as ThinkingEvent} />
    case "sources":
      return <SourcesPanel chunks={(event as { chunks: ChunkPreview[] }).chunks} />
    case "code":
      return <CodeBlock event={event as CodeEvent} />
    case "wiki":
      return (
        <a
          href={(event as { url: string }).url}
          target="_blank"
          rel="noreferrer"
          className="text-sm text-info-foreground underline block"
        >
          {(event as { title: string }).title}
        </a>
      )
    case "specialist_info": {
      const s = event as SpecialistInfoEvent
      return (
        <div className="flex items-center gap-2">
          <Badge variant="secondary" size="sm" radius="full">
            {s.specialist} specialist
          </Badge>
          <span className="text-xs text-muted-foreground">
            {Math.round(s.confidence * 100)}% confidence
          </span>
        </div>
      )
    }
    case "tool_call": {
      const t = event as ToolCallEvent
      return (
        <div className="text-xs text-muted-foreground flex items-center gap-1.5">
          <span>🔧</span>
          <span className="font-mono">{t.tool_name}</span>
          <span className="text-muted-foreground/60">· {t.status}</span>
        </div>
      )
    }
    case "scope_warn":
      return (
        <Alert variant="warning">
          <AlertTitle>{(event as ScopeWarnEvent).message}</AlertTitle>
        </Alert>
      )
    case "error":
      return (
        <Alert variant="destructive">
          <AlertTitle>{(event as { message: string }).message}</AlertTitle>
        </Alert>
      )
    case "followups":
      return (
        <div className="flex flex-wrap gap-2 mt-2">
          {(event as { questions: FollowupQuestion[] }).questions.map((q) => (
            <button
              key={q.question}
              onClick={() => onFollowup(q.question)}
              className="text-xs border rounded-full px-3 py-1 hover:bg-accent transition-colors text-left"
            >
              {q.question}
            </button>
          ))}
        </div>
      )
    case "actions":
      return (
        <div className="flex flex-wrap gap-2 mt-2">
          {(event as { actions: ActionLink[] }).actions.map((a) => {
            // Inline actions: click feeds ``inline_query`` back into the
            // chat as a new user turn. The right specialist then renders
            // the answer inline (catalog cards, balance, slots, etc.)
            // so the customer never leaves the conversation.
            if (a.inline_query) {
              return (
                <button
                  key={a.topic}
                  type="button"
                  onClick={() => onActionClick(a)}
                  className="text-xs bg-primary text-primary-foreground rounded-md px-3 py-1.5 hover:opacity-90 transition-opacity"
                >
                  {a.label}
                </button>
              )
            }
            // External actions: settings pages / handoff — open in a
            // new tab because we have no inline renderer for them.
            return (
              <a
                key={a.topic}
                href={a.url ?? "#"}
                target="_blank"
                rel="noreferrer noopener"
                onClick={() => onActionClick(a)}
                className="text-xs bg-primary text-primary-foreground rounded-md px-3 py-1.5 hover:opacity-90 transition-opacity inline-flex items-center gap-1"
              >
                <span>{a.label}</span>
                <span aria-hidden="true">↗</span>
              </a>
            )
          })}
        </div>
      )
    case "cards":
      return (
        <CardGrid
          event={event as CardsEvent}
          onSelect={(card) => {
            if (onCardSelect) onCardSelect(card)
            else onFollowup(`I'll go with: ${card.title}`)
          }}
          onOrderNow={onOrderNow}
          onAppointmentReschedule={onAppointmentReschedule}
          onAppointmentCancel={onAppointmentCancel}
        />
      )
    case "interactive_actions":
      return (
        <InteractiveActions
          event={event as InteractiveActionsEvent}
          onResult={(result) => onActionResult?.(result)}
        />
      )
    case "action_result":
      return <ActionResult event={event as ActionResultEventType} />
    case "done": {
      const d = event as DoneEvent
      const totalTokens = d.total_tokens ?? null
      const costUsd = d.cost_usd ?? null
      return (
        <div className="flex items-center gap-2 text-xs text-muted-foreground mt-2 pt-2 border-t flex-wrap">
          <Badge variant={d.faithfulness_passed ? "success" : "warning"} size="sm" radius="full">
            {d.faithfulness_passed ? "Faithful" : "Unverified"}
          </Badge>
          <Badge variant="outline" size="sm">
            {d.total_chunks_used} chunks
          </Badge>
          <Badge variant="outline" size="sm">
            {d.latency_ms}ms
          </Badge>
          {totalTokens != null && (
            <Badge
              variant="outline"
              size="sm"
              title={
                d.input_tokens != null && d.output_tokens != null
                  ? `${d.input_tokens} in / ${d.output_tokens} out` +
                    (d.llm_requests != null ? ` · ${d.llm_requests} req` : "")
                  : undefined
              }
            >
              {totalTokens.toLocaleString()} tokens
            </Badge>
          )}
          {costUsd != null && (
            <Badge variant="outline" size="sm" title="Approximate USD cost">
              ${costUsd < 0.01 ? costUsd.toFixed(4) : costUsd.toFixed(3)}
            </Badge>
          )}
          {(d.retrieval_ms != null || d.generation_ms != null || d.validation_ms != null) && (
            <span className="text-muted-foreground/60">
              {[
                d.retrieval_ms != null ? `search ${d.retrieval_ms}ms` : null,
                d.generation_ms != null ? `gen ${d.generation_ms}ms` : null,
                d.validation_ms != null ? `verify ${d.validation_ms}ms` : null,
              ]
                .filter(Boolean)
                .join(" / ")}
            </span>
          )}
        </div>
      )
    }
    default:
      return null
  }
}

const CopyButton = ({ text }: { text: string }) => {
  const [copied, setCopied] = useState(false)
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch (err) {
      console.error("Copy failed:", err)
    }
  }
  return (
    <button
      onClick={handleCopy}
      aria-label="Copy message"
      className="absolute top-0 right-0 opacity-0 group-hover/assistant:opacity-100 transition-opacity text-xs text-muted-foreground border rounded px-2 py-0.5 hover:bg-accent"
    >
      {copied ? "Copied" : "Copy"}
    </button>
  )
}

const ThinkingBubble = ({ event }: { event: ThinkingEvent }) => (
  <Alert variant="info" className="py-2">
    <AlertDescription className="flex items-center gap-2">
      <span className="inline-block h-2 w-2 rounded-full bg-info animate-pulse" />
      {event.message}
    </AlertDescription>
  </Alert>
)

const SourcesPanel = ({ chunks }: { chunks: ChunkPreview[] }) => {
  const [open, setOpen] = useState(false)
  return (
    <Card size="sm">
      <CardHeader className="cursor-pointer" onClick={() => setOpen(!open)}>
        <CardTitle>
          {chunks.length} sources retrieved {open ? "▾" : "▸"}
        </CardTitle>
      </CardHeader>
      {open && (
        <CardContent className="space-y-2">
          {chunks.map((c) => (
            <div key={c.id} className="border rounded-md p-2 text-xs space-y-0.5">
              <div className="font-medium">{c.qualified_name}</div>
              <div className="text-muted-foreground">{c.file_path}</div>
              <div className="text-muted-foreground">{c.purpose}</div>
              <Badge variant="outline" size="xs">
                score: {c.score.toFixed(3)}
              </Badge>
            </div>
          ))}
        </CardContent>
      )}
    </Card>
  )
}

const CodeBlock = ({ event }: { event: CodeEvent }) => (
  <div className="rounded-lg overflow-hidden border">
    <div className="bg-muted px-3 py-1 text-xs text-muted-foreground flex justify-between">
      <Badge variant="secondary" size="xs">
        {event.language}
      </Badge>
      {event.file_hint && <span>{event.file_hint}</span>}
    </div>
    <pre className="bg-card p-3 overflow-x-auto text-xs">
      <code>{event.content}</code>
    </pre>
  </div>
)
