import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/reui/badge"
import { Alert, AlertTitle, AlertDescription } from "@/components/reui/alert"
import { listJobs } from "@/api/client"
import type { Job } from "@/api/types"

const STATUS_VARIANT: Record<string, "outline" | "info" | "success" | "destructive" | "warning"> = {
  pending: "outline",
  running: "info",
  done: "success",
  failed: "destructive",
}

export const JobsPage = () => {
  const [autoRefresh, setAutoRefresh] = useState(false)
  const {
    data: jobs,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => listJobs(undefined, 50),
    refetchInterval: autoRefresh ? 5000 : false,
  })

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Index Jobs</h2>
        <Button
          size="sm"
          variant={autoRefresh ? "default" : "outline"}
          onClick={() => setAutoRefresh(!autoRefresh)}
        >
          {autoRefresh ? "Stop Auto-refresh" : "Auto-refresh (5s)"}
        </Button>
      </div>

      {isLoading && <p className="text-sm text-muted-foreground">Loading...</p>}

      {isError && (
        <Alert variant="destructive">
          <AlertTitle>Failed to load jobs</AlertTitle>
          <AlertDescription>{(error as Error)?.message}</AlertDescription>
        </Alert>
      )}

      <div className="space-y-2">
        {jobs?.map((job) => (
          <JobRow key={job.id} job={job} />
        ))}
        {jobs?.length === 0 && (
          <Alert variant="info">
            <AlertTitle>No jobs yet</AlertTitle>
            <AlertDescription>
              Trigger a sync from the Sources page to start indexing.
            </AlertDescription>
          </Alert>
        )}
      </div>
    </div>
  )
}

const StatChip = ({ label, value }: { label: string; value: string | number }) => (
  <Badge variant="outline" size="sm">
    {label}: <span className="ml-1 font-mono">{value}</span>
  </Badge>
)

const JobRow = ({ job }: { job: Job }) => {
  const [expanded, setExpanded] = useState(false)
  const stats = job.stats || {}

  // Pick out known fields; everything else falls through to the raw JSON view below
  const filesProcessed = stats.files_processed
  const chunksCreated = stats.chunks_created
  const chunksDeleted = stats.chunks_deleted
  const chunksUnchanged = stats.chunks_unchanged
  const depsCreated = stats.dependencies_created
  const embeddingModel = stats.embedding_model
  const embeddingDim = stats.embedding_dim
  const rerankModel = stats.rerank_model

  return (
    <Card size="sm">
      <CardContent>
        <div
          className="flex items-center justify-between cursor-pointer"
          onClick={() => setExpanded(!expanded)}
        >
          <div className="flex items-center gap-3 flex-wrap">
            <Badge variant={STATUS_VARIANT[job.status] ?? "outline"} size="sm" radius="full">
              {job.status === "running" && (
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-white animate-pulse mr-1" />
              )}
              {job.status}
            </Badge>
            <span className="text-sm font-mono text-muted-foreground">{job.id.slice(0, 8)}...</span>
            <Badge variant="outline" size="xs">
              {job.triggered_by}
            </Badge>
            {filesProcessed !== undefined && (
              <StatChip label="files" value={filesProcessed} />
            )}
            {chunksCreated !== undefined && (
              <StatChip label="created" value={chunksCreated} />
            )}
            {chunksUnchanged !== undefined && (
              <StatChip label="unchanged" value={chunksUnchanged} />
            )}
            {chunksDeleted !== undefined && chunksDeleted > 0 && (
              <StatChip label="deleted" value={chunksDeleted} />
            )}
          </div>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            {job.started_at && <span>Started: {new Date(job.started_at).toLocaleString()}</span>}
            {job.finished_at && <span>Finished: {new Date(job.finished_at).toLocaleString()}</span>}
            <span>{expanded ? "▾" : "▸"}</span>
          </div>
        </div>

        {expanded && (
          <div className="mt-3 pt-3 border-t space-y-2 text-xs">
            <div>
              <strong>Job ID:</strong> {job.id}
            </div>
            <div>
              <strong>Source ID:</strong> {job.source_id}
            </div>
            {job.celery_task_id && (
              <div>
                <strong>Celery Task:</strong> {job.celery_task_id}
              </div>
            )}
            {(embeddingModel || rerankModel) && (
              <div className="flex flex-wrap gap-1">
                {embeddingModel && (
                  <Badge variant="info-light" size="sm">
                    Embedding: {embeddingModel}
                    {embeddingDim ? ` (${embeddingDim}d)` : ""}
                  </Badge>
                )}
                {rerankModel && (
                  <Badge variant="info-light" size="sm">
                    Rerank: {rerankModel}
                  </Badge>
                )}
                {depsCreated !== undefined && (
                  <Badge variant="outline" size="sm">
                    deps: {depsCreated}
                  </Badge>
                )}
              </div>
            )}
            {job.error && (
              <Alert variant="destructive">
                <AlertTitle>Error</AlertTitle>
                <AlertDescription>{job.error}</AlertDescription>
              </Alert>
            )}
            {Array.isArray(stats.errors) && stats.errors.length > 0 && (
              <Alert variant="destructive">
                <AlertTitle>Errors ({stats.errors.length})</AlertTitle>
                <AlertDescription>
                  <ul className="list-disc pl-4 space-y-0.5">
                    {(stats.errors as string[]).slice(0, 10).map((e, i) => (
                      <li key={i} className="font-mono text-[11px]">
                        {e}
                      </li>
                    ))}
                    {stats.errors.length > 10 && (
                      <li className="text-muted-foreground">
                        …and {stats.errors.length - 10} more
                      </li>
                    )}
                  </ul>
                </AlertDescription>
              </Alert>
            )}
            {Object.keys(stats).length > 0 && (
              <details>
                <summary className="cursor-pointer text-muted-foreground">Raw stats</summary>
                <pre className="bg-muted rounded-md p-2 mt-1 overflow-x-auto">
                  {JSON.stringify(stats, null, 2)}
                </pre>
              </details>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
