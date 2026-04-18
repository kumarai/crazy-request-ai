// Banner shown above the support chat. When the user is a guest,
// prompts them to sign in. When signed in, shows the customer_id
// with a logout link. Keeps the login affordance visible without
// taking a full header row.
import { useState } from "react"
import { logout } from "@/api/auth"
import { useSessionStore } from "@/store/sessionStore"
import { Button } from "@/components/ui/button"
import { LoginDialog } from "./LoginDialog"

export function SessionBanner() {
  const { customerId, isGuest, clear } = useSessionStore()
  const [loginOpen, setLoginOpen] = useState(false)

  const handleLogout = async () => {
    await logout()
    clear()
  }

  if (!customerId) return null

  if (isGuest) {
    return (
      <>
        <div className="flex items-center justify-between gap-3 px-4 py-2 bg-muted/60 border-b text-sm">
          <span>
            You're chatting as a <strong>guest</strong>. Sign in to view your
            bill, place orders, or schedule appointments.
          </span>
          <Button size="sm" onClick={() => setLoginOpen(true)}>
            Sign in
          </Button>
        </div>
        <LoginDialog open={loginOpen} onOpenChange={setLoginOpen} />
      </>
    )
  }

  return (
    <div className="flex items-center justify-between gap-3 px-4 py-2 bg-muted/60 border-b text-sm">
      <span>
        Signed in as <strong>{customerId}</strong>
      </span>
      <Button size="sm" variant="outline" onClick={handleLogout}>
        Sign out
      </Button>
    </div>
  )
}
