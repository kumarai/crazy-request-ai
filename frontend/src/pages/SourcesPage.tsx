import { useCallback, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/reui/badge"
import { Alert, AlertTitle } from "@/components/reui/alert"
import { buildTelecomSampleFiles } from "@/lib/telecomSampleData"
import {
  createSource,
  deleteSource,
  deleteSourceFile,
  getSourceStatus,
  getUploadUrl,
  listCredentials,
  listSourceChunks,
  listSourceFiles,
  listSources,
  reembedSource,
  syncSource,
  updateSource,
} from "@/api/client"
import type { Source, StoredChunk } from "@/api/types"

export const SourcesPage = () => {
  const qc = useQueryClient()
  const { data: sources, isLoading } = useQuery({
    queryKey: ["sources"],
    queryFn: listSources,
  })
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<Source | null>(null)

  const deleteMut = useMutation({
    mutationFn: deleteSource,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sources"] }),
    onError: (err) => console.error("Delete failed:", err),
  })

  const syncMut = useMutation({
    mutationFn: (id: string) => syncSource(id, "incremental"),
    onError: (err) => console.error("Sync failed:", err),
  })
  const fullSyncMut = useMutation({
    mutationFn: (id: string) => syncSource(id, "full"),
    onError: (err) => console.error("Full sync failed:", err),
  })
  const reembedMut = useMutation({
    mutationFn: (id: string) => reembedSource(id),
    onError: (err) => console.error("Re-embed failed:", err),
  })

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Sources</h2>
        <Button
          onClick={() => {
            setShowForm(!showForm)
            setEditing(null)
          }}
        >
          {showForm ? "Cancel" : "+ Add Source"}
        </Button>
      </div>

      {(showForm || editing) && (
        <SourceForm
          source={editing}
          onDone={() => {
            setShowForm(false)
            setEditing(null)
            qc.invalidateQueries({ queryKey: ["sources"] })
          }}
        />
      )}

      {isLoading && <p className="text-sm text-muted-foreground">Loading...</p>}

      <div className="space-y-2">
        {sources?.map((s) => (
          <SourceRow
            key={s.id}
            source={s}
            onEdit={() => {
              setEditing(s)
              setShowForm(false)
            }}
            onDelete={() => {
              if (confirm(`Delete "${s.name}"?`)) deleteMut.mutate(s.id)
            }}
            onSync={() => syncMut.mutate(s.id)}
            onFullSync={() => fullSyncMut.mutate(s.id)}
            onReembed={() => {
              if (
                confirm(
                  `Re-embed "${s.name}"? Rebuilds vectors with the current embedding model without re-parsing or re-summarizing.`,
                )
              ) {
                reembedMut.mutate(s.id)
              }
            }}
          />
        ))}
      </div>

      {syncMut.error && (
        <Alert variant="destructive">
          <AlertTitle>Sync failed: {(syncMut.error as Error).message}</AlertTitle>
        </Alert>
      )}
      {fullSyncMut.error && (
        <Alert variant="destructive">
          <AlertTitle>Full sync failed: {(fullSyncMut.error as Error).message}</AlertTitle>
        </Alert>
      )}
      {reembedMut.error && (
        <Alert variant="destructive">
          <AlertTitle>Re-embed failed: {(reembedMut.error as Error).message}</AlertTitle>
        </Alert>
      )}
      {syncMut.data && (
        <Alert variant="success">
          <AlertTitle>Sync dispatched — Job ID: {syncMut.data.job_id}</AlertTitle>
        </Alert>
      )}
      {fullSyncMut.data && (
        <Alert variant="success">
          <AlertTitle>Full sync dispatched — Job ID: {fullSyncMut.data.job_id}</AlertTitle>
        </Alert>
      )}
      {reembedMut.data && (
        <Alert variant="success">
          <AlertTitle>Re-embed dispatched — Job ID: {reembedMut.data.job_id}</AlertTitle>
        </Alert>
      )}
    </div>
  )
}

const FILE_SOURCE_TYPES = new Set(["support", "api_docs", "json"])

const SourceRow = ({
  source,
  onEdit,
  onDelete,
  onSync,
  onFullSync,
  onReembed,
}: {
  source: Source
  onEdit: () => void
  onDelete: () => void
  onSync: () => void
  onFullSync: () => void
  onReembed: () => void
}) => {
  const [statusData, setStatusData] = useState<{ status: string } | null>(null)
  const [showFiles, setShowFiles] = useState(false)
  const [showChunks, setShowChunks] = useState(false)

  const checkStatus = async () => {
    try {
      const job = await getSourceStatus(source.id)
      setStatusData(job)
    } catch {
      setStatusData(null)
    }
  }

  const repoUrl = (source.config?.repository_url as string) || ""
  const branch = (source.config?.branch as string) || ""
  const isFileSource = FILE_SOURCE_TYPES.has(source.source_type)

  return (
    <Card size="sm">
      <CardContent className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <div className="font-medium text-sm">{source.name}</div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground flex-wrap">
              <Badge variant="outline" size="sm">
                {source.source_type}
              </Badge>
              {source.is_active ? (
                <Badge variant="success" size="sm">
                  Active
                </Badge>
              ) : (
                <Badge variant="destructive" size="sm">
                  Inactive
                </Badge>
              )}
              {source.credential_id && (
                <Badge variant="info-light" size="sm">
                  Credential linked
                </Badge>
              )}
              {source.chunk_count !== undefined && <span>{source.chunk_count} chunks</span>}
              {source.last_synced_at && (
                <span>Synced: {new Date(source.last_synced_at).toLocaleString()}</span>
              )}
            </div>
            {repoUrl && (
              <div className="text-xs text-muted-foreground truncate max-w-md">
                {repoUrl}
                {branch && branch !== "main" ? ` (${branch})` : ""}
              </div>
            )}
            {statusData && (
              <div className="text-xs">
                Latest job:{" "}
                <Badge variant="info-light" size="xs">
                  {statusData.status}
                </Badge>
              </div>
            )}
          </div>
          <div className="flex items-center gap-1 flex-wrap justify-end">
            <Button size="sm" variant="outline" onClick={checkStatus}>
              Status
            </Button>
            {isFileSource && (
              <Button size="sm" variant="outline" onClick={() => setShowFiles(!showFiles)}>
                Files
              </Button>
            )}
            <Button size="sm" variant="outline" onClick={() => setShowChunks(!showChunks)}>
              Chunks
            </Button>
            <Button size="sm" variant="outline" onClick={onSync}>
              Sync
            </Button>
            <Button size="sm" variant="outline" onClick={onFullSync}>
              Full Sync
            </Button>
            <Button size="sm" variant="outline" onClick={onReembed}>
              Re-embed
            </Button>
            <Button size="sm" variant="outline" onClick={onEdit}>
              Edit
            </Button>
            <Button size="sm" variant="destructive" onClick={onDelete}>
              Delete
            </Button>
          </div>
        </div>
        {showFiles && isFileSource && (
          <FileUploader sourceId={source.id} sourceType={source.source_type} />
        )}
        {showChunks && <ChunkPreview sourceId={source.id} />}
      </CardContent>
    </Card>
  )
}

// ── Chunk preview — inspect exactly what the embedder indexed ───────

const ChunkPreview = ({ sourceId }: { sourceId: string }) => {
  const [q, setQ] = useState("")
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["source-chunks", sourceId, q],
    queryFn: () => listSourceChunks(sourceId, { limit: 50, q: q || undefined }),
  })

  return (
    <div className="border-t pt-3 space-y-2">
      <div className="flex items-center gap-2">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter by name or path (leave blank for 50 latest)"
          className="h-8 text-xs"
          onKeyDown={(e) => e.key === "Enter" && refetch()}
        />
        <Button size="sm" variant="outline" onClick={() => refetch()}>
          Reload
        </Button>
      </div>
      {isLoading && <p className="text-xs text-muted-foreground">Loading chunks…</p>}
      {data?.length === 0 && (
        <p className="text-xs text-muted-foreground">No chunks found.</p>
      )}
      <div className="space-y-1 max-h-96 overflow-auto">
        {data?.map((c) => (
          <ChunkRow
            key={c.id}
            chunk={c}
            expanded={expandedId === c.id}
            onToggle={() => setExpandedId(expandedId === c.id ? null : c.id)}
          />
        ))}
      </div>
    </div>
  )
}

const ChunkRow = ({
  chunk,
  expanded,
  onToggle,
}: {
  chunk: StoredChunk
  expanded: boolean
  onToggle: () => void
}) => (
  <div className="rounded border bg-muted/30 text-xs">
    <button
      className="w-full flex items-center justify-between px-2 py-1.5 text-left"
      onClick={onToggle}
    >
      <div className="flex items-center gap-2 min-w-0">
        <Badge variant="outline" size="xs">
          {chunk.chunk_type}
        </Badge>
        <span className="font-mono truncate">{chunk.qualified_name}</span>
        <span className="text-muted-foreground truncate">
          {chunk.file_path}:{chunk.start_line}
        </span>
      </div>
      <span>{expanded ? "▾" : "▸"}</span>
    </button>
    {expanded && (
      <div className="border-t px-2 py-2 space-y-2 font-mono text-[11px]">
        {chunk.purpose && (
          <Field label="Purpose" value={chunk.purpose} />
        )}
        {chunk.summary && <Field label="Summary" value={chunk.summary} />}
        {chunk.reuse_signal && <Field label="Use when" value={chunk.reuse_signal} />}
        {chunk.side_effects && <Field label="Side effects" value={chunk.side_effects} />}
        {chunk.example_call && <Field label="Example" value={chunk.example_call} />}
        {chunk.domain_tags?.length > 0 && (
          <Field label="Tags" value={chunk.domain_tags.join(", ")} />
        )}
        {chunk.embed_input && (
          <div>
            <div className="text-muted-foreground mb-1">embed_input (what the vector saw):</div>
            <pre className="bg-background rounded p-2 whitespace-pre-wrap break-words">
              {chunk.embed_input}
            </pre>
          </div>
        )}
      </div>
    )}
  </div>
)

const Field = ({ label, value }: { label: string; value: string }) => (
  <div>
    <span className="text-muted-foreground">{label}:</span>{" "}
    <span>{value}</span>
  </div>
)

// ── File upload/manage for file-based sources ───────────────────────

const FileUploader = ({
  sourceId,
  sourceType,
}: {
  sourceId: string
  sourceType: string
}) => {
  const qc = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState("")

  const { data: files, isLoading } = useQuery({
    queryKey: ["source-files", sourceId],
    queryFn: () => listSourceFiles(sourceId),
  })

  const handleUpload = useCallback(
    async (files: File[]) => {
      if (!files.length) return
      setUploading(true)
      setUploadError("")

      try {
        for (const file of files) {
          const { upload_url } = await getUploadUrl(
            sourceId,
            file.name,
            file.type || "application/octet-stream",
          )

          const resp = await fetch(upload_url, {
            method: "PUT",
            headers: { "Content-Type": file.type || "application/octet-stream" },
            body: file,
          })
          if (!resp.ok) {
            throw new Error(`Upload failed for ${file.name}: ${resp.status}`)
          }
        }
        qc.invalidateQueries({ queryKey: ["source-files", sourceId] })
      } catch (err) {
        setUploadError((err as Error).message)
      } finally {
        setUploading(false)
        if (fileInputRef.current) fileInputRef.current.value = ""
      }
    },
    [sourceId, qc],
  )

  const handleTelecomSampleUpload = useCallback(async () => {
    await handleUpload(buildTelecomSampleFiles())
  }, [handleUpload])

  const handleDelete = async (filename: string) => {
    if (!confirm(`Delete "${filename}"?`)) return
    await deleteSourceFile(sourceId, filename)
    qc.invalidateQueries({ queryKey: ["source-files", sourceId] })
  }

  return (
    <div className="border-t pt-3 space-y-2">
      <div className="flex items-center gap-2">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".json,.md,.txt,.yaml,.yml"
          onChange={(e) => void handleUpload(Array.from(e.target.files ?? []))}
          className="text-xs file:mr-2 file:rounded file:border-0 file:bg-primary file:px-2 file:py-1 file:text-xs file:text-primary-foreground file:cursor-pointer"
        />
        {sourceType === "json" && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => void handleTelecomSampleUpload()}
            disabled={uploading}
          >
            Add Telecom Sample
          </Button>
        )}
        {uploading && <span className="text-xs text-muted-foreground">Uploading...</span>}
      </div>
      {sourceType === "json" && (
        <p className="text-xs text-muted-foreground">
          The sample button generates telecom support JSON in the browser and uploads it here.
        </p>
      )}

      {uploadError && (
        <Alert variant="destructive">
          <AlertTitle>{uploadError}</AlertTitle>
        </Alert>
      )}

      {isLoading && <p className="text-xs text-muted-foreground">Loading files...</p>}

      {files && files.length > 0 && (
        <div className="space-y-1">
          {files.map((f) => (
            <div
              key={f.key}
              className="flex items-center justify-between text-xs bg-muted/50 rounded px-2 py-1"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className="truncate">{f.name}</span>
                <span className="text-muted-foreground shrink-0">
                  {f.size > 1024 ? `${(f.size / 1024).toFixed(1)} KB` : `${f.size} B`}
                </span>
              </div>
              <Button
                size="xs"
                variant="ghost"
                onClick={() => handleDelete(f.name)}
                className="text-destructive shrink-0"
              >
                Remove
              </Button>
            </div>
          ))}
        </div>
      )}

      {files && files.length === 0 && (
        <p className="text-xs text-muted-foreground">
          No files uploaded yet. Upload files then click Sync to index them.
        </p>
      )}
    </div>
  )
}

// ── Shared form for create + edit ────────────────────────────────────

const SOURCE_TYPES = [
  { value: "git_repo", label: "Git Repository" },
  { value: "gitlab_wiki", label: "GitLab Wiki" },
  { value: "api", label: "Remote API" },
  { value: "support", label: "Support Articles" },
  { value: "api_docs", label: "API Docs" },
  { value: "json", label: "JSON files" },
]

const GIT_TYPES = new Set(["git_repo", "gitlab_wiki"])
const GENERIC_TYPES = new Set(["support", "api_docs", "json"])

const SourceForm = ({ source, onDone }: { source: Source | null; onDone: () => void }) => {
  const isEdit = !!source
  const config = source?.config ?? {}
  const pag = (config.pagination as Record<string, unknown>) ?? {}

  const [name, setName] = useState(source?.name ?? "")
  const [sourceType, setSourceType] = useState(source?.source_type ?? "git_repo")

  // Git fields
  const [repoUrl, setRepoUrl] = useState((config.repository_url as string) ?? "")
  const [branch, setBranch] = useState((config.branch as string) ?? "main")
  const [directory, setDirectory] = useState(
    Array.isArray(config.directory) ? (config.directory as string[]).join(", ") : "*",
  )
  const [wikiEnabled, setWikiEnabled] = useState((config.wiki_enabled as boolean) ?? false)

  // Generic source fields
  const [path, setPath] = useState((config.path as string) ?? "")

  // API fields
  const [apiUrl, setApiUrl] = useState((config.url as string) ?? "")
  const [method, setMethod] = useState((config.method as string) ?? "GET")
  const [headers, setHeaders] = useState(
    config.headers ? JSON.stringify(config.headers, null, 2) : "",
  )
  const [queryParams, setQueryParams] = useState(
    config.query_params ? JSON.stringify(config.query_params, null, 2) : "",
  )
  const [body, setBody] = useState(config.body ? JSON.stringify(config.body, null, 2) : "")
  const [authStrategy, setAuthStrategy] = useState((config.auth_strategy as string) ?? "none")
  const [authHeaderName, setAuthHeaderName] = useState(
    (config.auth_header_name as string) ?? "X-API-Key",
  )
  const [authQueryParam, setAuthQueryParam] = useState(
    (config.auth_query_param_name as string) ?? "api_key",
  )
  const [responseFormat, setResponseFormat] = useState(
    (config.response_format as string) ?? "json",
  )
  const [dataPath, setDataPath] = useState((config.data_path as string) ?? "")
  const [contentFields, setContentFields] = useState(
    Array.isArray(config.content_fields) ? (config.content_fields as string[]).join(", ") : "",
  )
  const [nameField, setNameField] = useState((config.name_field as string) ?? "")
  const [idField, setIdField] = useState((config.id_field as string) ?? "")
  const [pagType, setPagType] = useState((pag.type as string) ?? "none")
  const [cursorPath, setCursorPath] = useState((pag.cursor_path as string) ?? "")
  const [cursorParam, setCursorParam] = useState((pag.cursor_param as string) ?? "cursor")
  const [maxPages, setMaxPages] = useState(String(pag.max_pages ?? 50))

  // Shared
  const [credentialId, setCredentialId] = useState(source?.credential_id ?? "")
  const [isActive, setIsActive] = useState(source?.is_active ?? true)

  const { data: credentials } = useQuery({
    queryKey: ["credentials"],
    queryFn: listCredentials,
  })

  const isGitType = GIT_TYPES.has(sourceType)
  const isApiType = sourceType === "api"
  const isGenericType = GENERIC_TYPES.has(sourceType)

  const tryParseJson = (s: string) => {
    if (!s.trim()) return undefined
    try {
      return JSON.parse(s)
    } catch {
      return undefined
    }
  }

  const buildConfig = () => {
    if (isGitType) {
      return {
        repository_url: repoUrl,
        branch,
        directory: directory
          .split(",")
          .map((d) => d.trim())
          .filter(Boolean),
        wiki_enabled: wikiEnabled,
      }
    }
    if (isApiType) {
      const cfg: Record<string, unknown> = {
        url: apiUrl,
        method,
        response_format: responseFormat,
        auth_strategy: authStrategy,
        data_path: dataPath || undefined,
        name_field: nameField || undefined,
        id_field: idField || undefined,
        content_fields: contentFields
          ? contentFields
              .split(",")
              .map((f) => f.trim())
              .filter(Boolean)
          : undefined,
        headers: tryParseJson(headers),
        query_params: tryParseJson(queryParams),
        body: tryParseJson(body),
      }
      if (authStrategy === "header") cfg.auth_header_name = authHeaderName
      if (authStrategy === "query_param") cfg.auth_query_param_name = authQueryParam
      if (pagType !== "none") {
        cfg.pagination = {
          type: pagType,
          cursor_path: cursorPath || undefined,
          cursor_param: cursorParam || undefined,
          max_pages: parseInt(maxPages) || 50,
        }
      }
      return cfg
    }
    return { path }
  }

  const createMut = useMutation({
    mutationFn: () =>
      createSource({
        name,
        source_type: sourceType,
        config: buildConfig(),
        credential_id: credentialId || null,
      }),
    onSuccess: onDone,
  })

  const updateMut = useMutation({
    mutationFn: () =>
      updateSource(source!.id, {
        name,
        source_type: sourceType,
        config: buildConfig(),
        credential_id: credentialId || null,
        is_active: isActive,
      }),
    onSuccess: onDone,
  })

  const mut = isEdit ? updateMut : createMut
  const canSubmit =
    name.trim() &&
    (isGitType ? repoUrl.trim() : isApiType ? apiUrl.trim() : true)

  return (
    <Card>
      <CardHeader>
        <CardTitle>{isEdit ? "Edit Source" : "New Source"}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 max-w-lg">
        <div>
          <Label>Name</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="my-repo" />
        </div>

        <div>
          <Label>Type</Label>
          <select
            value={sourceType}
            onChange={(e) => setSourceType(e.target.value)}
            className="flex h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm"
          >
            {SOURCE_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </div>

        {isGitType && (
          <>
            <div>
              <Label>Repository URL</Label>
              <Input
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                placeholder="https://gitlab.com/org/my-repo"
              />
            </div>
            <div>
              <Label>Branch</Label>
              <Input value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="main" />
            </div>
            <div>
              <Label>Directory filter (comma-separated, prefix ! to exclude)</Label>
              <Input
                value={directory}
                onChange={(e) => setDirectory(e.target.value)}
                placeholder="src, lib, !tests, !node_modules"
              />
            </div>
            {sourceType === "git_repo" && (
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={wikiEnabled}
                  onChange={(e) => setWikiEnabled(e.target.checked)}
                  className="rounded"
                />
                Enable wiki indexing (GitLab only)
              </label>
            )}
          </>
        )}

        {isApiType && (
          <>
            <div>
              <Label>API URL</Label>
              <Input
                value={apiUrl}
                onChange={(e) => setApiUrl(e.target.value)}
                placeholder="https://api.example.com/v1/resources"
              />
            </div>
            <div>
              <Label>Method</Label>
              <select
                value={method}
                onChange={(e) => setMethod(e.target.value)}
                className="flex h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm"
              >
                <option value="GET">GET</option>
                <option value="POST">POST</option>
                <option value="PUT">PUT</option>
              </select>
            </div>
            <div>
              <Label>Auth Strategy</Label>
              <select
                value={authStrategy}
                onChange={(e) => setAuthStrategy(e.target.value)}
                className="flex h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm"
              >
                <option value="none">None (public)</option>
                <option value="bearer">Bearer Token</option>
                <option value="header">Custom Header</option>
                <option value="query_param">Query Parameter</option>
                <option value="oauth2">OAuth2 (from credential)</option>
              </select>
            </div>
            {authStrategy === "header" && (
              <div>
                <Label>Auth Header Name</Label>
                <Input
                  value={authHeaderName}
                  onChange={(e) => setAuthHeaderName(e.target.value)}
                  placeholder="X-API-Key"
                />
              </div>
            )}
            {authStrategy === "query_param" && (
              <div>
                <Label>Auth Query Param Name</Label>
                <Input
                  value={authQueryParam}
                  onChange={(e) => setAuthQueryParam(e.target.value)}
                  placeholder="api_key"
                />
              </div>
            )}
            <div>
              <Label>Response Format</Label>
              <select
                value={responseFormat}
                onChange={(e) => setResponseFormat(e.target.value)}
                className="flex h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm"
              >
                <option value="json">JSON</option>
                <option value="xml">XML</option>
                <option value="text">Plain Text</option>
              </select>
            </div>
            <div>
              <Label>Data Path (jmespath expression to extract items)</Label>
              <Input
                value={dataPath}
                onChange={(e) => setDataPath(e.target.value)}
                placeholder="results or data.items"
              />
            </div>
            <div>
              <Label>Content Fields (comma-separated fields to index)</Label>
              <Input
                value={contentFields}
                onChange={(e) => setContentFields(e.target.value)}
                placeholder="title, body, description"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <Label>Name Field</Label>
                <Input
                  value={nameField}
                  onChange={(e) => setNameField(e.target.value)}
                  placeholder="title"
                />
              </div>
              <div>
                <Label>ID Field (for dedup)</Label>
                <Input
                  value={idField}
                  onChange={(e) => setIdField(e.target.value)}
                  placeholder="id"
                />
              </div>
            </div>
            <div>
              <Label>Headers (JSON, optional)</Label>
              <textarea
                value={headers}
                onChange={(e) => setHeaders(e.target.value)}
                placeholder={'{"Accept": "application/json"}'}
                rows={2}
                className="flex w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm font-mono"
              />
            </div>
            <div>
              <Label>Query Params (JSON, optional)</Label>
              <textarea
                value={queryParams}
                onChange={(e) => setQueryParams(e.target.value)}
                placeholder={'{"per_page": 100}'}
                rows={2}
                className="flex w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm font-mono"
              />
            </div>
            {method !== "GET" && (
              <div>
                <Label>Request Body (JSON — use for GraphQL queries)</Label>
                <textarea
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  placeholder={'{"query": "{ issues { nodes { title body } } }"}'}
                  rows={4}
                  className="flex w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm font-mono"
                />
              </div>
            )}
            <div>
              <Label>Pagination</Label>
              <select
                value={pagType}
                onChange={(e) => setPagType(e.target.value)}
                className="flex h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm"
              >
                <option value="none">None (single request)</option>
                <option value="cursor">Cursor-based</option>
                <option value="offset">Offset-based</option>
                <option value="link_header">Link Header</option>
              </select>
            </div>
            {pagType === "cursor" && (
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <Label>Cursor Path (jmespath)</Label>
                  <Input
                    value={cursorPath}
                    onChange={(e) => setCursorPath(e.target.value)}
                    placeholder="meta.next_cursor"
                  />
                </div>
                <div>
                  <Label>Cursor Param</Label>
                  <Input
                    value={cursorParam}
                    onChange={(e) => setCursorParam(e.target.value)}
                    placeholder="cursor or variables.after"
                  />
                </div>
              </div>
            )}
            {pagType !== "none" && (
              <div>
                <Label>Max Pages</Label>
                <Input
                  value={maxPages}
                  onChange={(e) => setMaxPages(e.target.value)}
                  placeholder="50"
                  type="number"
                />
              </div>
            )}
          </>
        )}

        {isGenericType && (
          <div>
            <Label>Path</Label>
            <Input
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="/data/support-articles"
            />
          </div>
        )}

        <div>
          <Label>Credential (optional)</Label>
          <select
            value={credentialId}
            onChange={(e) => setCredentialId(e.target.value)}
            className="flex h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm"
          >
            <option value="">None (public)</option>
            {credentials?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.credential_type})
              </option>
            ))}
          </select>
        </div>

        {isEdit && (
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={isActive}
              onChange={(e) => setIsActive(e.target.checked)}
              className="rounded"
            />
            Active
          </label>
        )}

        <Button onClick={() => mut.mutate()} disabled={!canSubmit || mut.isPending}>
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
