import { useCallback, useMemo, useState } from "react"
import { useMutation } from "@tanstack/react-query"
import {
  Background,
  Controls,
  type Edge,
  type Node,
  type NodeMouseHandler,
  ReactFlow,
  ReactFlowProvider,
} from "@xyflow/react"
import dagre from "@dagrejs/dagre"
import type { ReactNode } from "react"
import "@xyflow/react/dist/style.css"

import { postDebugQuery } from "@/api/client"
import type { DebugTrace, DebugTraceNode } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/reui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"

const NODE_WIDTH = 240
const NODE_HEIGHT = 96

const statusColor: Record<DebugTraceNode["status"], string> = {
  ok: "border-emerald-500",
  auth_skipped: "border-amber-500",
  error: "border-red-500",
}

const kindBg: Record<DebugTraceNode["kind"], string> = {
  decomposer: "bg-sky-50",
  specialist: "bg-white",
  synthesizer: "bg-violet-50",
}

type FlowNodeData = { trace: DebugTraceNode; label: ReactNode }
type FlowNode = Node<FlowNodeData>

const layout = (trace: DebugTrace): { nodes: FlowNode[]; edges: Edge[] } => {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: "LR", nodesep: 40, ranksep: 80 })
  g.setDefaultEdgeLabel(() => ({}))
  trace.nodes.forEach((n) => g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT }))
  trace.edges.forEach((e) => g.setEdge(e.from_id, e.to_id))
  dagre.layout(g)

  const nodes: FlowNode[] = trace.nodes.map((n) => {
    const pos = g.node(n.id)
    const label = (
      <div className="flex flex-col items-stretch text-xs text-left">
        <div className="flex items-center justify-between gap-2">
          <span className="uppercase tracking-wide text-[10px] text-muted-foreground">
            {n.kind}
          </span>
          <span className="text-[10px] text-muted-foreground">{n.timing_ms}ms</span>
        </div>
        <div className="font-medium truncate">
          {n.specialist ?? (n.kind === "decomposer" ? "decompose" : "synthesize")}
        </div>
        {n.sub_query && (
          <div className="text-[11px] text-muted-foreground line-clamp-2">{n.sub_query}</div>
        )}
        <div className="mt-1 flex gap-1 flex-wrap">
          {n.status !== "ok" && (
            <Badge
              variant={n.status === "auth_skipped" ? "warning-light" : "destructive-light"}
              size="sm"
            >
              {n.status.replace("_", " ")}
            </Badge>
          )}
          {n.sources.length > 0 && (
            <Badge variant="info-light" size="sm">
              {n.sources.length} docs
            </Badge>
          )}
          {n.tool_calls.length > 0 && (
            <Badge variant="secondary" size="sm">
              {n.tool_calls.length} tools
            </Badge>
          )}
        </div>
      </div>
    )
    // The default react-flow node renders `data.label`. We stash the raw
    // trace node alongside so the inspector can look it up by id.
    return {
      id: n.id,
      position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 },
      data: { trace: n, label },
      style: {
        width: NODE_WIDTH,
        minHeight: NODE_HEIGHT,
        padding: 8,
        borderRadius: 8,
        borderWidth: 2,
      },
      className: `${kindBg[n.kind]} ${statusColor[n.status]}`,
    }
  })

  const edges: Edge[] = trace.edges.map((e, i) => ({
    id: `e${i}`,
    source: e.from_id,
    target: e.to_id,
    animated: false,
  }))

  return { nodes, edges }
}

const DebugPageInner = () => {
  const [query, setQuery] = useState(
    "How to fix my internet connection after my credit card got rejected and I pay my bill late?",
  )
  const [customerId, setCustomerId] = useState("")
  const [trace, setTrace] = useState<DebugTrace | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const runMutation = useMutation({
    mutationFn: (q: string) => postDebugQuery(q, { customerId: customerId.trim() || undefined }),
    onSuccess: (t) => {
      setTrace(t)
      setSelectedId(null)
    },
  })

  const { nodes, edges } = useMemo(() => {
    if (!trace) return { nodes: [] as FlowNode[], edges: [] as Edge[] }
    return layout(trace)
  }, [trace])

  const selected = useMemo(
    () => trace?.nodes.find((n) => n.id === selectedId) ?? null,
    [trace, selectedId],
  )

  const onNodeClick: NodeMouseHandler = useCallback((_, node) => {
    setSelectedId(node.id)
  }, [])

  const handleRun = () => {
    const q = query.trim()
    if (!q) return
    runMutation.mutate(q)
  }

  return (
    <div className="flex flex-col h-full">
      {/* Query strip */}
      <div className="p-4 border-b bg-muted/30 space-y-2">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold">Debug — Multi-Agent Trace</h2>
          {trace && (
            <>
              <Badge variant="info-light" size="sm">
                {trace.total_latency_ms}ms
              </Badge>
              {trace.total_cost_usd !== null && (
                <Badge variant="secondary" size="sm">
                  ${trace.total_cost_usd.toFixed(4)}
                </Badge>
              )}
              <Badge variant="secondary" size="sm">
                {trace.nodes.length} nodes
              </Badge>
            </>
          )}
        </div>
        <div className="flex gap-2 items-start">
          <Textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleRun()
            }}
            placeholder="Enter a compound support query..."
            className="flex-1 min-h-[72px]"
          />
          <div className="flex flex-col gap-2 shrink-0">
            <input
              type="text"
              value={customerId}
              onChange={(e) => setCustomerId(e.target.value)}
              placeholder="customer_id (blank = guest)"
              className="h-8 rounded border px-2 text-xs w-52"
            />
            <Button onClick={handleRun} disabled={runMutation.isPending || !query.trim()}>
              {runMutation.isPending ? "Running…" : "Run (⌘/Ctrl+Enter)"}
            </Button>
          </div>
        </div>
        {runMutation.isError && (
          <div className="text-xs text-red-600">{(runMutation.error as Error).message}</div>
        )}
      </div>

      {/* Main: DAG + inspector */}
      <div className="flex-1 flex min-h-0">
        <div className="flex-1 min-w-0 relative">
          {trace ? (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodeClick={onNodeClick}
              fitView
              proOptions={{ hideAttribution: true }}
            >
              <Background />
              <Controls />
            </ReactFlow>
          ) : (
            <div className="h-full flex items-center justify-center text-sm text-muted-foreground">
              Enter a query and click Run to see the agent graph.
            </div>
          )}
        </div>

        {/* Inspector sidebar */}
        <aside className="w-[420px] border-l shrink-0 flex flex-col min-h-0">
          <div className="p-3 border-b text-sm font-medium">
            {selected ? `Node: ${selected.id}` : "Final Answer"}
          </div>
          <ScrollArea className="flex-1">
            <div className="p-3 text-sm space-y-3">
              {selected ? (
                <NodeInspector node={selected} />
              ) : trace ? (
                <pre className="whitespace-pre-wrap text-sm">{trace.final_answer}</pre>
              ) : (
                <div className="text-muted-foreground text-xs">
                  Run a query, then click a node to inspect it.
                </div>
              )}
            </div>
          </ScrollArea>
        </aside>
      </div>
    </div>
  )
}

const NodeInspector = ({ node }: { node: DebugTraceNode }) => (
  <div className="space-y-3">
    <div className="flex flex-wrap gap-1.5">
      <Badge variant="secondary" size="sm">
        {node.kind}
      </Badge>
      {node.specialist && (
        <Badge variant="info-light" size="sm">
          {node.specialist}
        </Badge>
      )}
      <Badge
        variant={
          node.status === "ok"
            ? "success-light"
            : node.status === "auth_skipped"
              ? "warning-light"
              : "destructive-light"
        }
        size="sm"
      >
        {node.status}
      </Badge>
      <Badge variant="secondary" size="sm">
        {node.timing_ms}ms
      </Badge>
    </div>

    {node.sub_query && (
      <Section title="Sub-query">
        <p>{node.sub_query}</p>
      </Section>
    )}
    {node.rationale && (
      <Section title="Rationale">
        <p className="text-muted-foreground">{node.rationale}</p>
      </Section>
    )}
    {node.output_text && (
      <Section title="Output">
        <pre className="whitespace-pre-wrap text-xs">{node.output_text}</pre>
      </Section>
    )}
    {node.error && (
      <Section title="Error">
        <pre className="whitespace-pre-wrap text-xs text-red-600">{node.error}</pre>
      </Section>
    )}
    {node.sources.length > 0 && (
      <Section title={`Retrieved documents (${node.sources.length})`}>
        <ul className="space-y-2">
          {node.sources.map((s) => (
            <li key={s.id} className="border rounded p-2 bg-muted/40">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium truncate">
                  {s.qualified_name || s.file_path}
                </span>
                <span className="text-[10px] text-muted-foreground shrink-0">
                  {s.score.toFixed(3)}
                </span>
              </div>
              <div className="text-[10px] text-muted-foreground truncate">
                {s.source_name} · {s.file_path}
              </div>
              {s.summary && (
                <p className="text-xs text-muted-foreground mt-1 line-clamp-3">{s.summary}</p>
              )}
            </li>
          ))}
        </ul>
      </Section>
    )}
    {node.tool_calls.length > 0 && (
      <Section title={`Tool calls (${node.tool_calls.length})`}>
        <ul className="space-y-1">
          {node.tool_calls.map((t, i) => (
            <li key={i} className="text-xs">
              <span className="font-mono">{t.name}</span>
              {t.output !== undefined && (
                <pre className="whitespace-pre-wrap text-[10px] text-muted-foreground bg-muted/30 rounded p-1 mt-0.5">
                  {JSON.stringify(t.output, null, 2).slice(0, 500)}
                </pre>
              )}
            </li>
          ))}
        </ul>
      </Section>
    )}
  </div>
)

const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <div>
    <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">{title}</div>
    <div className="text-sm">{children}</div>
  </div>
)

export const DebugPage = () => (
  <ReactFlowProvider>
    <DebugPageInner />
  </ReactFlowProvider>
)
