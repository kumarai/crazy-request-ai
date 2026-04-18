import { useCallback, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { getLLMSettings, listSources, updateLLMSettings } from "@/api/client"
import { streamDevChat } from "@/api/sse"
import type { ActionLink, SSEEvent } from "@/api/types"
import {
  Transcript,
  type Message,
  nextMsgId,
} from "@/components/chat/Transcript"

export const DevChatPage = () => {
  const [query, setQuery] = useState("")
  const [messages, setMessages] = useState<Message[]>([])
  const [streaming, setStreaming] = useState(false)
  // Last submitted query — preserved for the Regenerate button so users
  // can retry without retyping when faithfulness fails or the response
  // is weak.
  const [lastQuery, setLastQuery] = useState<string>("")
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const { data: sources } = useQuery({ queryKey: ["sources"], queryFn: listSources })
  const { data: llmSettings } = useQuery({
    queryKey: ["llm-settings"],
    queryFn: getLLMSettings,
  })

  const [selectedProvider, setSelectedProvider] = useState<string | "">("")
  const activeProvider = selectedProvider || llmSettings?.active_provider || ""

  const handleProviderChange = async (provider: string) => {
    setSelectedProvider(provider)
    try {
      await updateLLMSettings({ provider })
    } catch (err) {
      console.error("Failed to update provider:", err)
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
      const userMsg: Message = { id: nextMsgId(), role: "user", query: q, events: [] }
      const assistantMsg: Message = { id: nextMsgId(), role: "assistant", events: [] }
      setMessages((prev) => [...prev, userMsg, assistantMsg])
      setLastQuery(q)
      setStreaming(true)

      const ctrl = streamDevChat(
        { query: q, provider: selectedProvider || undefined },
        {
          onEvent: addEvent,
          onError: () => setStreaming(false),
          onClose: () => setStreaming(false),
        },
      )
      abortRef.current = ctrl
    },
    [selectedProvider, addEvent],
  )

  const handleSubmit = () => {
    const q = query.trim()
    if (!q || streaming) return
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

  // DevChat doesn't persist action clicks server-side (no conversation
  // record). Locally append a transcript bubble so the user sees the
  // click in their history; the `<a target="_blank">` already opens the
  // URL.
  const handleActionClick = (action: ActionLink) => {
    const clickMsg: Message = {
      id: nextMsgId(),
      role: "action",
      query: `Clicked: ${action.label}`,
      events: [],
    }
    setMessages((prev) => [...prev, clickMsg])
  }

  const handleRegenerate = () => {
    if (!lastQuery || streaming) return
    runQuery(lastQuery)
  }

  return (
    <div className="flex flex-col h-full">
      <Transcript
        messages={messages}
        onFollowup={handleFollowup}
        onActionClick={handleActionClick}
        scrollRef={scrollRef}
        emptyState={
          <div className="flex items-center justify-center h-full text-muted-foreground">
            <div className="text-center space-y-2">
              <p className="text-lg font-medium">Ask about your codebase</p>
              <p className="text-sm">Query indexed repos, wikis, and docs</p>
            </div>
          </div>
        }
      />

      <div className="border-t p-4 shrink-0">
        <div className="flex gap-2">
          <Textarea
            placeholder="Ask about your codebase..."
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
          />
          {streaming ? (
            <Button variant="destructive" onClick={handleCancel} className="self-end">
              Cancel
            </Button>
          ) : (
            <Button onClick={handleSubmit} className="self-end" disabled={!query.trim()}>
              Send
            </Button>
          )}
        </div>
        <div className="flex items-center gap-3 mt-1">
          {sources && sources.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {sources.length} source{sources.length > 1 ? "s" : ""} indexed
            </p>
          )}
          {lastQuery && !streaming && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleRegenerate}
              className="h-6 px-2 text-xs"
            >
              Regenerate
            </Button>
          )}
          {llmSettings && llmSettings.providers.length > 0 && (
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-muted-foreground">Model:</span>
              <select
                value={activeProvider}
                onChange={(e) => handleProviderChange(e.target.value)}
                className="h-6 text-xs rounded border border-input bg-transparent px-1.5"
              >
                {llmSettings.providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
