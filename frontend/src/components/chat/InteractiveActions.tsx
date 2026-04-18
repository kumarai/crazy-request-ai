// Renders an InteractiveActionsEvent — server-issued in-chat buttons
// that POST to /v1/support/action. On click: disable all buttons in
// the group, show a spinner on the clicked one, call the action
// endpoint, surface the ACTION_RESULT inline. Expired buttons (past
// `expires_at`) are greyed out.
import { useEffect, useState } from "react"
import type {
  ActionResultEvent,
  InteractiveAction,
  InteractiveActionsEvent,
} from "@/api/types"
import { Button } from "@/components/ui/button"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"

interface InteractiveActionsProps {
  event: InteractiveActionsEvent
  onResult: (result: ActionResultEvent) => void
}

// Per-action UI state:
//   idle    -> button enabled
//   pending -> this button shows a spinner, others in the group are disabled
//   done    -> this button greys out with a checkmark; other buttons greyed
//   error   -> inline error under the button; others re-enabled
type ActionStatus = "idle" | "pending" | "done" | "error"

export function InteractiveActions({ event, onResult }: InteractiveActionsProps) {
  const [status, setStatus] = useState<Record<string, ActionStatus>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [now, setNow] = useState(Date.now())

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 10_000)
    return () => clearInterval(t)
  }, [])

  // True while any button in the group is mid-flight — used to
  // disable siblings so a user can't double-submit the same choice
  // set (e.g. picking BOTH slots when the prompt is "pick one").
  const anyPending = Object.values(status).some((s) => s === "pending")
  const anyDone = Object.values(status).some((s) => s === "done")

  const handleClick = async (action: InteractiveAction) => {
    if (action.confirm_text && !window.confirm(action.confirm_text)) return
    setStatus((s) => ({ ...s, [action.action_id]: "pending" }))
    setErrors((e) => ({ ...e, [action.action_id]: "" }))
    try {
      const res = await fetch(`${API_BASE}/v1/support/action`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action_id: action.action_id }),
      })
      if (!res.ok) {
        const text = await res.text()
        setStatus((s) => ({ ...s, [action.action_id]: "error" }))
        setErrors((e) => ({ ...e, [action.action_id]: `Failed: ${res.status}` }))
        onResult({
          type: "action_result",
          action_id: action.action_id,
          kind: action.kind,
          status: "error",
          message: text || "Action failed",
        })
        return
      }
      const payload: ActionResultEvent = await res.json()
      setStatus((s) => ({ ...s, [action.action_id]: "done" }))
      onResult(payload)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setStatus((s) => ({ ...s, [action.action_id]: "error" }))
      setErrors((e) => ({ ...e, [action.action_id]: msg }))
      onResult({
        type: "action_result",
        action_id: action.action_id,
        kind: action.kind,
        status: "error",
        message: msg,
      })
    }
  }

  return (
    <div className="flex flex-wrap gap-2 mt-3">
      {event.actions.map((a) => {
        const expired = new Date(a.expires_at).getTime() < now
        const st: ActionStatus = status[a.action_id] ?? "idle"
        const isPending = st === "pending"
        const isDone = st === "done"
        // Disable this button when: expired, a sibling is pending,
        // another sibling already succeeded (one-of-N selection), or
        // it's already been resolved.
        const disabled =
          expired || isPending || isDone || (anyPending && !isPending) || (anyDone && !isDone)
        const err = errors[a.action_id]
        return (
          <div key={a.action_id} className="flex flex-col gap-1">
            <Button
              size="sm"
              disabled={disabled}
              onClick={() => handleClick(a)}
            >
              {isPending && (
                <span className="inline-block w-3 h-3 mr-2 border-2 border-current border-r-transparent rounded-full animate-spin" />
              )}
              {isDone && <span className="mr-2">✓</span>}
              {expired
                ? `${a.label} (expired)`
                : isDone
                  ? `${a.label} — done`
                  : a.label}
            </Button>
            {err && <span className="text-xs text-destructive">{err}</span>}
          </div>
        )
      })}
    </div>
  )
}
