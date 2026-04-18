// Login dialog — single customer_id field, mock auth.
//
// Opens when the user clicks "Sign in" in the guest banner or clicks
// the account specialist's sign_in interactive action in the chat.
import { useState } from "react"
import { login } from "@/api/auth"
import { useSessionStore } from "@/store/sessionStore"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

interface LoginDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  // Optional override for the default heading — used when the dialog
  // is opened in response to the account specialist's sign_in action
  // so we can show the reason inline ("to place your order", etc.).
  headerMessage?: string
  allowContinueAsGuest?: boolean
}

export function LoginDialog({
  open,
  onOpenChange,
  headerMessage,
  allowContinueAsGuest = true,
}: LoginDialogProps) {
  const [customerId, setCustomerId] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const setSession = useSessionStore((s) => s.setSession)

  const handleLogin = async () => {
    setError(null)
    setLoading(true)
    try {
      const me = await login(customerId.trim())
      setSession(me.customer_id, me.is_guest)
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed")
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Sign in</DialogTitle>
        </DialogHeader>
        {headerMessage && (
          <p className="text-sm text-muted-foreground">{headerMessage}</p>
        )}
        <div className="flex flex-col gap-3 mt-2">
          <label className="text-sm font-medium" htmlFor="customer-id">
            Customer ID
          </label>
          <Input
            id="customer-id"
            placeholder="cust_001"
            value={customerId}
            onChange={(e) => setCustomerId(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleLogin()
            }}
            autoFocus
          />
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2 mt-2">
            {allowContinueAsGuest && (
              <Button
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={loading}
              >
                Continue as guest
              </Button>
            )}
            <Button
              onClick={handleLogin}
              disabled={loading || !customerId.trim()}
            >
              {loading ? "Signing in..." : "Sign in"}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground mt-2">
            Dev mock: any non-empty customer ID works. Try{" "}
            <code>cust_001</code>, <code>cust_002</code>, or <code>cust_003</code>.
          </p>
        </div>
      </DialogContent>
    </Dialog>
  )
}
