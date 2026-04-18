// On app mount: hit /v1/whoami so the backend either confirms the
// existing session cookie or mints a fresh guest one. Updates both
// sessionStore (new) and supportStore (legacy) so the existing chat
// wiring keeps working without changes. Renders nothing.
import { useEffect } from "react"
import { whoami } from "@/api/auth"
import { useSessionStore } from "@/store/sessionStore"
import { useSupportStore } from "@/store/supportStore"

export function SessionBootstrap() {
  const setSession = useSessionStore((s) => s.setSession)
  const setCustomerId = useSupportStore((s) => s.setCustomerId)
  const supportCustomerId = useSupportStore((s) => s.customerId)

  useEffect(() => {
    let cancelled = false
    whoami()
      .then((me) => {
        if (cancelled) return
        setSession(me.customer_id, me.is_guest)
        // Keep the legacy support store in sync so existing page code
        // that reads customerId continues to work. Only overwrite when
        // empty or when it diverges — we don't want to steamroll a
        // customerId the user manually typed mid-session.
        if (!supportCustomerId || supportCustomerId !== me.customer_id) {
          setCustomerId(me.customer_id)
        }
      })
      .catch((err) => {
        // whoami failures are non-fatal — the page will still load;
        // user just won't have a resolved session.
        console.error("[auth] whoami failed:", err)
      })
    return () => {
      cancelled = true
    }
  }, [setSession, setCustomerId, supportCustomerId])

  return null
}
