import { useEffect, useState } from "react"
import { Link, Outlet, useLocation } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { getLLMSettings } from "@/api/client"
import { Badge } from "@/components/reui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useAuthStore } from "@/store/authStore"
import { cn } from "@/lib/utils"

const NAV = [
  { to: "/devchat", label: "Dev Chat" },
  { to: "/support", label: "Support Chat" },
  { to: "/sources", label: "Sources" },
  { to: "/credentials", label: "Credentials" },
  { to: "/jobs", label: "Jobs" },
  { to: "/settings", label: "Settings" },
]

export const Layout = () => {
  const location = useLocation()
  const { apiKey, setApiKey } = useAuthStore()
  const [keyInput, setKeyInput] = useState(apiKey)
  const [showKey, setShowKey] = useState(false)

  useEffect(() => {
    setKeyInput(apiKey)
  }, [apiKey])

  const saveKey = () => setApiKey(keyInput.trim())

  const { data: settings } = useQuery({
    queryKey: ["llm-settings"],
    queryFn: getLLMSettings,
    refetchOnMount: false,
    retry: false,
  })

  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <aside className="w-56 shrink-0 border-r bg-sidebar text-sidebar-foreground flex flex-col">
        <div className="p-4 border-b">
          <h1 className="text-lg font-bold tracking-tight">DevKnowledge</h1>
          <p className="text-xs text-muted-foreground">RAG Platform</p>
        </div>
        <nav className="flex-1 p-2 space-y-1">
          {NAV.map((n) => (
            <Link
              key={n.to}
              to={n.to}
              className={cn(
                "block rounded-md px-3 py-2 text-sm transition-colors hover:bg-sidebar-accent",
                location.pathname === n.to && "bg-sidebar-accent font-medium",
              )}
            >
              {n.label}
            </Link>
          ))}
        </nav>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar: API key (optional) + provider indicators */}
        <header className="h-14 border-b flex items-center gap-3 px-4 shrink-0 overflow-x-auto">
          {/* API key — optional; leave empty if backend auth is disabled */}
          <div className="flex items-center gap-1.5 shrink-0">
            <span className="text-xs text-muted-foreground whitespace-nowrap">API Key:</span>
            <Input
              type={showKey ? "text" : "password"}
              placeholder="Optional"
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && saveKey()}
              className="max-w-[160px] h-7 text-xs"
            />
            <Button
              size="xs"
              variant="ghost"
              onClick={() => setShowKey(!showKey)}
              className="text-muted-foreground"
            >
              {showKey ? "hide" : "show"}
            </Button>
            <Button
              size="xs"
              variant={apiKey === keyInput.trim() && apiKey ? "secondary" : "default"}
              onClick={saveKey}
            >
              Save
            </Button>
            {apiKey && <span className="text-[10px] text-green-600 whitespace-nowrap">● set</span>}
          </div>

          {/* Provider indicators */}
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground flex-wrap ml-auto">
            {settings ? (
              <>
                <span>Chat:</span>
                <Badge variant="info-light" size="sm">
                  {settings.active_provider}
                </Badge>
                <span className="ml-1">Embed:</span>
                <Badge variant="info-light" size="sm">
                  {settings.active_embedding_provider ?? "—"}
                  {settings.embedding_model ? ` · ${settings.embedding_model}` : ""}
                  {settings.embedding_dim ? ` (${settings.embedding_dim}d)` : ""}
                </Badge>
                <span className="ml-1">Rerank:</span>
                <Badge variant="info-light" size="sm">
                  {settings.active_rerank_provider ?? "—"}
                  {settings.rerank_model ? ` · ${settings.rerank_model}` : ""}
                </Badge>
              </>
            ) : (
              <span>Settings unavailable</span>
            )}
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
