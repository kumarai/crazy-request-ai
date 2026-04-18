import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/reui/badge"
import { Alert, AlertTitle } from "@/components/reui/alert"
import { createCredential, deleteCredential, listCredentials, updateCredential } from "@/api/client"
import type { Credential } from "@/api/types"

export const CredentialsPage = () => {
  const qc = useQueryClient()
  const { data: creds, isLoading } = useQuery({
    queryKey: ["credentials"],
    queryFn: listCredentials,
  })
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<Credential | null>(null)

  const deleteMut = useMutation({
    mutationFn: deleteCredential,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["credentials"] }),
  })

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Credentials</h2>
        <Button
          onClick={() => {
            setShowForm(!showForm)
            setEditing(null)
          }}
        >
          {showForm ? "Cancel" : "+ Add Credential"}
        </Button>
      </div>

      {(showForm || editing) && (
        <CredentialForm
          credential={editing}
          onDone={() => {
            setShowForm(false)
            setEditing(null)
            qc.invalidateQueries({ queryKey: ["credentials"] })
          }}
        />
      )}

      {isLoading && <p className="text-sm text-muted-foreground">Loading...</p>}

      <div className="space-y-2">
        {creds?.map((c) => (
          <Card key={c.id} size="sm">
            <CardContent className="flex items-center justify-between">
              <div className="space-y-1">
                <div className="font-medium text-sm">{c.name}</div>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant="info-light" size="sm">
                    {c.credential_type}
                  </Badge>
                  {c.description && <span>{c.description}</span>}
                  {c.updated_at && <span>Updated: {new Date(c.updated_at).toLocaleString()}</span>}
                </div>
              </div>
              <div className="flex items-center gap-1">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setEditing(c)
                    setShowForm(false)
                  }}
                >
                  Edit
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={() => {
                    if (confirm(`Delete "${c.name}"?`)) deleteMut.mutate(c.id)
                  }}
                >
                  Delete
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
        {creds?.length === 0 && (
          <Alert variant="info">
            <AlertTitle>No credentials yet. Add one to authenticate with private repos or APIs.</AlertTitle>
          </Alert>
        )}
      </div>
    </div>
  )
}

const CredentialForm = ({
  credential,
  onDone,
}: {
  credential: Credential | null
  onDone: () => void
}) => {
  const isEdit = !!credential
  const [name, setName] = useState(credential?.name ?? "")
  const [credType, setCredType] = useState(credential?.credential_type ?? "token")
  const [description, setDescription] = useState(credential?.description ?? "")

  // Simple token/ssh value
  const [value, setValue] = useState("")

  // OAuth2 fields
  const [clientId, setClientId] = useState("")
  const [clientSecret, setClientSecret] = useState("")
  const [tokenUrl, setTokenUrl] = useState("")
  const [scope, setScope] = useState("")

  const buildValue = () => {
    if (credType === "oauth2") {
      return JSON.stringify({
        client_id: clientId,
        client_secret: clientSecret,
        token_url: tokenUrl,
        scope: scope || undefined,
      })
    }
    return value
  }

  const createMut = useMutation({
    mutationFn: () =>
      createCredential({ name, credential_type: credType, value: buildValue(), description }),
    onSuccess: onDone,
  })

  const updateMut = useMutation({
    mutationFn: () => {
      const val = buildValue()
      return updateCredential(credential!.id, {
        name: name || undefined,
        credential_type: credType || undefined,
        value: val || undefined,
        description,
      })
    },
    onSuccess: onDone,
  })

  const mut = isEdit ? updateMut : createMut

  const canSubmit = () => {
    if (!name.trim()) return false
    if (isEdit) return true // editing doesn't require re-entering value
    if (credType === "oauth2") return clientId.trim() && clientSecret.trim() && tokenUrl.trim()
    return value.trim()
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{isEdit ? "Edit Credential" : "New Credential"}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 max-w-lg">
        <div>
          <Label>Name</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="gitlab-main" />
        </div>
        <div>
          <Label>Type</Label>
          <select
            value={credType}
            onChange={(e) => setCredType(e.target.value)}
            className="flex h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm"
          >
            <option value="token">Token (Bearer / API Key)</option>
            <option value="ssh">SSH Key</option>
            <option value="oauth2">OAuth2 Client Credentials</option>
          </select>
        </div>

        {credType === "oauth2" ? (
          <>
            <div>
              <Label>Token URL</Label>
              <Input
                value={tokenUrl}
                onChange={(e) => setTokenUrl(e.target.value)}
                placeholder="https://auth.example.com/oauth/token"
              />
            </div>
            <div>
              <Label>Client ID</Label>
              <Input
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                placeholder="abc123"
              />
            </div>
            <div>
              <Label>Client Secret</Label>
              <Input
                type="password"
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                placeholder={isEdit ? "Enter new secret to change" : "Client secret"}
              />
            </div>
            <div>
              <Label>Scope (optional)</Label>
              <Input
                value={scope}
                onChange={(e) => setScope(e.target.value)}
                placeholder="read:api write:api"
              />
            </div>
          </>
        ) : (
          <div>
            <Label>{isEdit ? "New Value (leave empty to keep)" : "Value"}</Label>
            <Input
              type="password"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={isEdit ? "Enter new value to change" : "Token or SSH key"}
            />
          </div>
        )}

        <div>
          <Label>Description (optional)</Label>
          <Input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="GitLab main org token"
          />
        </div>
        <Button onClick={() => mut.mutate()} disabled={!canSubmit() || mut.isPending}>
          {mut.isPending ? "Saving..." : isEdit ? "Update" : "Create"}
        </Button>
        {mut.error && (
          <Alert variant="destructive">
            <AlertTitle>{(mut.error as Error).message}</AlertTitle>
          </Alert>
        )}
      </CardContent>
    </Card>
  )
}
