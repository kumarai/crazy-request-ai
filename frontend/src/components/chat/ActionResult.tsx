// Renders an ACTION_RESULT event — the server's reply after a button
// click. Green for success, red for error, amber for pending.
import type { ActionResultEvent } from "@/api/types"

interface ActionResultProps {
  event: ActionResultEvent
}

export function ActionResult({ event }: ActionResultProps) {
  const color =
    event.status === "success"
      ? "text-green-700 bg-green-50 border-green-300"
      : event.status === "error"
        ? "text-destructive bg-destructive/10 border-destructive/40"
        : "text-amber-700 bg-amber-50 border-amber-300"
  return (
    <div
      className={`mt-2 rounded-md border px-3 py-2 text-sm ${color}`}
      role="status"
    >
      {event.message}
    </div>
  )
}
