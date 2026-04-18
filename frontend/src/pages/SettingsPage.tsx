import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { getLLMSettings, updateLLMSettings } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/reui/badge"
import { Alert, AlertDescription, AlertTitle } from "@/components/reui/alert"
import type { LLMSettings, UpdateLLMSettingsRequest } from "@/api/types"

// Static option lists. These mirror what the backend supports in
// llm/client.py — MODEL_DEFAULTS keys, plus the dedicated embed provider
// we added for voyage-code-3.
const CHAT_PROVIDERS = ["openai", "anthropic", "google", "ollama"]
const EMBEDDING_PROVIDERS = ["voyage", "openai", "google", "ollama"]
const RERANK_PROVIDERS = ["llm"]

export const SettingsPage = () => {
  const qc = useQueryClient()
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["llm-settings"],
    queryFn: getLLMSettings,
  })

  return (
    <div className="p-6 space-y-4 max-w-3xl">
      <h2 className="text-xl font-semibold">Settings</h2>

      {isLoading && <p className="text-sm text-muted-foreground">Loading settings…</p>}

      {isError && (
        <Alert variant="destructive">
          <AlertTitle>Failed to load settings</AlertTitle>
          <AlertDescription>{(error as Error)?.message}</AlertDescription>
        </Alert>
      )}

      {data && (
        <ProviderForm
          // Remount when server state changes so local form state re-derives
          // from settings. Avoids an in-effect setState pattern.
          key={`${data.active_provider}|${data.active_embedding_provider}|${data.active_rerank_provider}`}
          settings={data}
          onSaved={() => qc.invalidateQueries({ queryKey: ["llm-settings"] })}
        />
      )}
    </div>
  )
}

const ProviderForm = ({
  settings,
  onSaved,
}: {
  settings: LLMSettings
  onSaved: () => void
}) => {
  const [chat, setChat] = useState(settings.active_provider)
  const [embed, setEmbed] = useState(settings.active_embedding_provider ?? "voyage")
  const [rerank, setRerank] = useState(settings.active_rerank_provider ?? "llm")

  const mut = useMutation({
    mutationFn: (body: UpdateLLMSettingsRequest) => updateLLMSettings(body),
    onSuccess: onSaved,
  })

  const dirty =
    chat !== settings.active_provider ||
    embed !== (settings.active_embedding_provider ?? "voyage") ||
    rerank !== (settings.active_rerank_provider ?? "llm")

  return (
    <Card>
      <CardHeader>
        <CardTitle>LLM & Retrieval Providers</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <Section
          title="Chat / Generation"
          description="Provider used for answering queries, HyDE, summaries, and fallback rerank."
        >
          <ProviderSelect value={chat} options={CHAT_PROVIDERS} onChange={setChat} />
          <ConfiguredProviders providers={settings.providers.map((p) => p.id)} highlight={chat} />
        </Section>

        <Section
          title="Embedding"
          description="Provider used to embed chunks at index time and queries at retrieval time. Switching providers requires a Re-embed of each source (Sources page → Re-embed)."
        >
          <ProviderSelect value={embed} options={EMBEDDING_PROVIDERS} onChange={setEmbed} />
          <div className="flex items-center gap-2 text-xs text-muted-foreground flex-wrap">
            <span>Currently:</span>
            <Badge variant="info-light" size="sm">
              {settings.embedding_model || "—"}
            </Badge>
            {settings.embedding_dim && (
              <Badge variant="outline" size="sm">
                {settings.embedding_dim}d
              </Badge>
            )}
          </div>
        </Section>

        <Section
          title="Rerank"
          description="Reranker applied after RRF fusion. 'llm' scores candidates via the chat provider."
        >
          <ProviderSelect value={rerank} options={RERANK_PROVIDERS} onChange={setRerank} />
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>Currently:</span>
            <Badge variant="info-light" size="sm">
              {settings.rerank_model || "—"}
            </Badge>
          </div>
        </Section>

        <div className="flex items-center gap-2 pt-2">
          <Button
            disabled={!dirty || mut.isPending}
            onClick={() =>
              mut.mutate({
                provider: chat,
                embedding_provider: embed,
                rerank_provider: rerank,
              })
            }
          >
            {mut.isPending ? "Saving…" : dirty ? "Save" : "No changes"}
          </Button>
          {mut.error && (
            <span className="text-xs text-destructive">
              {(mut.error as Error).message}
            </span>
          )}
          {mut.isSuccess && !dirty && (
            <span className="text-xs text-green-600">Saved</span>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

const Section = ({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: React.ReactNode
}) => (
  <div className="space-y-2 border-t pt-4 first:border-0 first:pt-0">
    <div>
      <Label className="text-sm font-semibold">{title}</Label>
      <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
    </div>
    <div className="space-y-2">{children}</div>
  </div>
)

const ProviderSelect = ({
  value,
  options,
  onChange,
}: {
  value: string
  options: string[]
  onChange: (v: string) => void
}) => (
  <select
    value={value}
    onChange={(e) => onChange(e.target.value)}
    className="flex h-8 w-full max-w-xs rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm"
  >
    {options.map((o) => (
      <option key={o} value={o}>
        {o}
      </option>
    ))}
  </select>
)

const ConfiguredProviders = ({
  providers,
  highlight,
}: {
  providers: string[]
  highlight: string
}) =>
  providers.length > 0 ? (
    <div className="flex items-center gap-1.5 text-xs text-muted-foreground flex-wrap">
      <span>Configured:</span>
      {providers.map((p) => (
        <Badge key={p} variant={p === highlight ? "success" : "outline"} size="sm">
          {p}
        </Badge>
      ))}
    </div>
  ) : null
