/**
 * Output panel — execution log + node output viewer.
 *
 * The log shows all nodes that have execution state in the current run.
 * Clicking a table/chart/stop row shows its output below.
 * Selection is explicit — no auto-fallback that overrides your click.
 */
import { useState, useMemo, useEffect } from 'react'
import { useStore } from '@/store'
import { api } from '@/utils/api'
import {
  CheckCircle, XCircle, Loader2, OctagonX,
  Clock, ChevronDown, ChevronRight, ArrowRight,
} from 'lucide-react'
import type { NodeStatus, ColumnSchema } from '@/types'

// ── Data table ────────────────────────────────────────────────────────────────

function DataTable({ schema, rows, total }: {
  schema: ColumnSchema[]
  rows: Record<string, unknown>[]
  total: number
}) {
  const [filter, setFilter] = useState('')
  const [sortCol, setSortCol] = useState<string | null>(null)
  const [sortAsc, setSortAsc] = useState(true)
  const [page, setPage] = useState(0)
  const PAGE = 50

  const filtered = useMemo(() => {
    if (!filter) return rows
    const q = filter.toLowerCase()
    return rows.filter((r) =>
      Object.values(r).some((v) => String(v ?? '').toLowerCase().includes(q))
    )
  }, [rows, filter])

  const sorted = useMemo(() => {
    if (!sortCol) return filtered
    return [...filtered].sort((a, b) => {
      const cmp = String(a[sortCol] ?? '').localeCompare(String(b[sortCol] ?? ''), undefined, { numeric: true })
      return sortAsc ? cmp : -cmp
    })
  }, [filtered, sortCol, sortAsc])

  const paged = sorted.slice(page * PAGE, (page + 1) * PAGE)
  const pages = Math.ceil(sorted.length / PAGE)

  if (!schema.length) return <div className="p-4 text-xs text-muted italic">No columns in result.</div>

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center gap-2 p-2 border-b border-canvas-border shrink-0">
        <input
          className="input flex-1 py-1 text-xs"
          placeholder="Filter rows…"
          value={filter}
          onChange={(e) => { setFilter(e.target.value); setPage(0) }}
        />
        <span className="text-xs text-muted whitespace-nowrap tabular-nums shrink-0">
          {filtered.length.toLocaleString()} / {total.toLocaleString()} rows
        </span>
      </div>
      <div className="flex-1 overflow-auto">
        <table className="text-xs w-full">
          <thead className="sticky top-0 bg-surface-raised z-10">
            <tr>
              {schema.map((col) => (
                <th
                  key={col.name}
                  className="px-2 py-1.5 text-left font-medium whitespace-nowrap border-b border-canvas-border
                             cursor-pointer select-none hover:text-slate-200 text-muted"
                  onClick={() => { setSortCol(col.name); setSortAsc(sortCol === col.name ? !sortAsc : true) }}
                >
                  <div className="flex items-center gap-1">
                    {col.name}
                    {sortCol === col.name && <span className="text-accent">{sortAsc ? '↑' : '↓'}</span>}
                  </div>
                  <div className="font-mono text-subtle font-normal opacity-60">{col.dtype}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paged.map((row, i) => (
              <tr key={i} className="border-b border-canvas-border/30 hover:bg-surface-raised">
                {schema.map((col) => (
                  <td
                    key={col.name}
                    className="px-2 py-1 font-mono text-slate-300 whitespace-nowrap max-w-[200px] truncate"
                    title={String(row[col.name] ?? '')}
                  >
                    {row[col.name] === null || row[col.name] === undefined
                      ? <span className="text-subtle italic">null</span>
                      : String(row[col.name])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-between p-2 border-t border-canvas-border text-xs text-muted shrink-0">
          <button className="btn-ghost py-0.5 text-xs" onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0}>← Prev</button>
          <span className="tabular-nums">{page + 1} / {pages}</span>
          <button className="btn-ghost py-0.5 text-xs" onClick={() => setPage(Math.min(pages - 1, page + 1))} disabled={page >= pages - 1}>Next →</button>
        </div>
      )}
    </div>
  )
}

// ── Chart renderer ────────────────────────────────────────────────────────────

function ChartRenderer({ chartType, payload }: { chartType: string; payload: string }) {
  if (chartType === 'png') {
    return (
      <div className="p-2">
        <img src={`data:image/png;base64,${payload}`} alt="chart"
             className="max-w-full rounded-lg border border-canvas-border" />
      </div>
    )
  }

  // ECharts — render inline using the echarts library loaded via CDN
  if (chartType === 'echarts') {
    return <EChartsRenderer payload={payload} />
  }

  if (chartType === 'plotly') {
    try {
      const spec = JSON.parse(payload)
      return (
        <div className="p-3 space-y-2">
          <div className="text-xs text-muted">
            Plotly · {spec.data?.length ?? 0} trace(s) · type: {spec.data?.[0]?.type ?? 'unknown'}
          </div>
          <pre className="text-xs font-mono text-slate-400 bg-canvas-bg rounded p-2 overflow-auto max-h-64 border border-canvas-border">
            {JSON.stringify(spec.data?.[0] ?? spec, null, 2).slice(0, 1000)}
          </pre>
        </div>
      )
    } catch {
      return <div className="text-xs text-red-400 p-3">Invalid Plotly spec</div>
    }
  }

  return <div className="text-xs text-muted p-3">Unknown chart type: {chartType}</div>
}

// ECharts rendered inline using the bundled echarts-for-react library
function EChartsRenderer({ payload }: { payload: string }) {
  const [error, setError] = useState<string | null>(null)
  const [ReactECharts, setReactECharts] = useState<any>(null)
  const [option, setOption] = useState<any>(null)

  useEffect(() => {
    // Parse the spec
    let parsed: any
    try {
      parsed = JSON.parse(payload)
    } catch {
      setError('Invalid ECharts JSON spec')
      return
    }
    setOption(parsed)

    // Dynamically import the bundled echarts-for-react
    import('echarts-for-react')
      .then((mod) => setReactECharts(() => mod.default))
      .catch((e) => setError('Failed to load echarts module: ' + e.message))
  }, [payload])

  if (error) {
    return (
      <div className="p-3 space-y-2">
        <div className="text-xs text-red-400 mb-1">Chart error: {error}</div>
        <pre className="text-xs font-mono text-slate-400 bg-canvas-bg rounded p-2 overflow-auto max-h-48 border border-canvas-border">
          {payload.slice(0, 800)}
        </pre>
      </div>
    )
  }

  if (!ReactECharts || !option) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted px-3 py-4">
        <Loader2 size={12} className="animate-spin" /> Loading chart…
      </div>
    )
  }

  return (
    <div className="p-2">
      <ReactECharts
        option={{ backgroundColor: 'transparent', ...option }}
        theme="dark"
        style={{ width: '100%', height: 360 }}
        notMerge={true}
        lazyUpdate={true}
      />
    </div>
  )
}

// ── Error view — surface the clean message, collapse the full traceback ───────

function ErrorView({ detail }: { detail: string }) {
  const [showFull, setShowFull] = useState(false)

  // Parse the Python traceback to extract the most useful parts:
  //  - The final error line (ExceptionType: message)
  //  - The line in the user's code that triggered it (line N, in <module>)
  const lines = detail.trim().split('\n')
  const errorLine = lines[lines.length - 1] ?? detail

  // Find the user-code frame: '  File "<string>", line N, in <module>'
  let userCodeHint: string | null = null
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes('"<string>"')) {
      const m = lines[i].match(/line (\d+)/)
      const codeLine = lines[i + 1]?.trim()
      if (m) {
        userCodeHint = `Line ${m[1]} in your code` + (codeLine ? `:  ${codeLine}` : '')
      }
    }
  }

  // Split exception type from message
  const errMatch = errorLine.match(/^(\w+(?:Error|Exception|Warning)):\s*(.*)$/)
  const errType = errMatch?.[1] ?? 'Error'
  const errMsg  = errMatch?.[2] ?? errorLine

  return (
    <div className="p-3 space-y-3">
      {/* Clean error headline */}
      <div className="rounded-lg border border-red-900/60 bg-red-950/30 p-3 space-y-1.5">
        <div className="flex items-center gap-2">
          <XCircle size={14} className="text-red-400 shrink-0" />
          <span className="text-sm font-semibold text-red-300">{errType}</span>
        </div>
        <div className="text-xs text-red-200/90 font-mono leading-relaxed break-words">
          {errMsg}
        </div>
        {userCodeHint && (
          <div className="text-xs text-amber-300/80 font-mono pt-1 border-t border-red-900/40 mt-1.5 break-words">
            ↳ {userCodeHint}
          </div>
        )}
      </div>

      {/* Collapsible full traceback */}
      <button
        className="flex items-center gap-1 text-xs text-muted hover:text-slate-300 transition-colors"
        onClick={() => setShowFull(!showFull)}
      >
        {showFull ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        {showFull ? 'Hide' : 'Show'} full traceback
      </button>

      {showFull && (
        <pre className="text-xs text-slate-400 font-mono bg-canvas-bg rounded p-3 overflow-auto
                        whitespace-pre-wrap break-all border border-canvas-border max-h-80">
          {detail}
        </pre>
      )}
    </div>
  )
}

// ── Node output viewer ────────────────────────────────────────────────────────

function NodeOutputView({ nodeId }: { nodeId: string }) {
  const { execState, activeProject } = useStore()
  const state = execState[nodeId]

  // For processor/trigger nodes (which don't push inline table data), fetch
  // their cached frame on demand so you can inspect any node's output.
  const [fetched, setFetched] = useState<{ schema: any[]; rows: any[]; total: number } | null>(null)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [fetching, setFetching] = useState(false)

  const needsFetch =
    state &&
    state.status !== 'running' &&
    state.status !== 'error' &&
    state.status !== 'blocked' &&
    !state.chart_type &&
    !(state.table_schema && Array.isArray(state.table_rows) && (state.table_schema as any[]).length > 0)

  useEffect(() => {
    setFetched(null)
    setFetchError(null)
    if (!needsFetch || !activeProject) return
    let cancelled = false
    setFetching(true)
    api.execute.inspectFrame(activeProject.id, nodeId, 500)
      .then((res) => { if (!cancelled) setFetched(res as any) })
      .catch((e) => { if (!cancelled) setFetchError(e.message) })
      .finally(() => { if (!cancelled) setFetching(false) })
    return () => { cancelled = true }
  }, [nodeId, needsFetch, activeProject?.id])

  if (!state) return <div className="p-4 text-xs text-muted italic">No data — run the flow first.</div>

  if (state.status === 'running') {
    return (
      <div className="flex items-center justify-center p-6 gap-2 text-muted text-sm">
        <Loader2 size={14} className="animate-spin" /> Executing…
      </div>
    )
  }

  if (state.status === 'blocked') {
    return (
      <div className="p-4 text-xs text-muted italic flex items-center gap-2">
        <OctagonX size={13} className="text-status-stopped opacity-60" />
        This node was blocked by an upstream hard stop and did not run.
      </div>
    )
  }

  if (state.status === 'error') {
    return <ErrorView detail={state.detail ?? 'Unknown error'} />
  }

  // Chart — must check first before table, as chart nodes don't have table data
  if (state.chart_type && state.chart_payload) {
    return <ChartRenderer chartType={state.chart_type} payload={state.chart_payload} />
  }

  // Inline table data (table_out, stop)
  if (state.table_schema && Array.isArray(state.table_rows) && (state.table_schema as any[]).length > 0) {
    return (
      <DataTable
        schema={state.table_schema as ColumnSchema[]}
        rows={state.table_rows as Record<string, unknown>[]}
        total={state.table_total ?? (state.table_rows as any[]).length}
      />
    )
  }

  // Fetched frame (processor/trigger nodes)
  if (fetching) {
    return (
      <div className="flex items-center justify-center p-6 gap-2 text-muted text-sm">
        <Loader2 size={14} className="animate-spin" /> Loading frame…
      </div>
    )
  }

  if (fetched && fetched.schema.length > 0) {
    return (
      <DataTable
        schema={fetched.schema as ColumnSchema[]}
        rows={fetched.rows as Record<string, unknown>[]}
        total={fetched.total}
      />
    )
  }

  if (fetchError) {
    return (
      <div className="p-4 text-xs text-muted italic">
        {state.rows_out !== undefined
          ? `${state.rows_out.toLocaleString()} rows passed through. (Frame not cached for inspection.)`
          : 'No cached data for this node.'}
      </div>
    )
  }

  // Fallback
  return (
    <div className="p-4 text-xs text-muted italic">
      {state.rows_out !== undefined
        ? `${state.rows_out.toLocaleString()} rows passed through.`
        : 'No visual output for this node.'}
    </div>
  )
}

// ── Status icons ──────────────────────────────────────────────────────────────

const STATUS_ICON: Record<NodeStatus, React.ReactNode> = {
  idle:    <span className="w-3 h-3 inline-block shrink-0" />,
  running: <Loader2 size={12} className="text-status-running animate-spin shrink-0" />,
  ok:      <CheckCircle size={12} className="text-status-ok shrink-0" />,
  error:   <XCircle size={12} className="text-status-error shrink-0" />,
  stopped: <OctagonX size={12} className="text-status-stopped shrink-0" />,
  blocked: <OctagonX size={12} className="text-muted shrink-0 opacity-50" />,
}

const OUTPUT_NODE_TYPES = new Set(['table_out', 'chart_out', 'stop'])

// ── Main OutputPanel ──────────────────────────────────────────────────────────

const READ_PATH: Record<string, string> = {
  connectorx: 'ConnectorX',
  'clickhouse-arrow': 'ClickHouse Arrow',
  sqlalchemy: 'SQLAlchemy',
}

export function OutputPanel() {
  const { execState, activeProject, activeFlowId, running, pinnedOutputNodeId } = useStore()
  // Local pin (from clicking log rows) — overridden by store pin (from Inspect actions)
  const [localPin, setLocalPin] = useState<string | null>(null)
  const pinnedNodeId = pinnedOutputNodeId ?? localPin

  // Clear local pin when switching flows or starting a new run
  useEffect(() => { setLocalPin(null) }, [activeFlowId])
  useEffect(() => { if (running) setLocalPin(null) }, [running])

  // When the store pin changes (Inspect clicked), clear the local pin so store wins
  useEffect(() => {
    if (pinnedOutputNodeId) setLocalPin(null)
  }, [pinnedOutputNodeId])

  const flow = activeProject?.flows.find((f) => f.id === activeFlowId)
  const nodes = (flow?.nodes as any[]) ?? []

  // Only show nodes that have execution state in this run
  const executedNodes = nodes.filter((n) => !!execState[n.id])
  const hasResults = executedNodes.length > 0

  // The node whose output to display:
  // 1. User explicitly pinned a node → use it
  // 2. No pin → use the first output node that completed (stop > table > chart, in flow order)
  const autoNode = executedNodes.find(
    (n) => OUTPUT_NODE_TYPES.has(n.type) &&
           execState[n.id]?.status !== 'idle' &&
           execState[n.id]?.status !== 'running'
  )
  const displayNodeId = pinnedNodeId ?? autoNode?.id ?? null

  if (!hasResults) {
    return (
      <div className="p-4 text-xs text-muted italic">
        Run the flow to see execution details and output.
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* ── Execution log ──────────────────────────────────────── */}
      <div className="shrink-0 border-b border-canvas-border">
        <div className="px-3 py-1.5 text-xs font-semibold text-muted uppercase tracking-wider border-b border-canvas-border">
          Execution Log
        </div>
        <div className="overflow-y-auto" style={{ maxHeight: 200 }}>
          {executedNodes.map((n) => {
            const s = execState[n.id]
            const isOutputNode = OUTPUT_NODE_TYPES.has(n.type)
            const isPinned = pinnedNodeId === n.id
            const isDisplay = displayNodeId === n.id

            return (
              <div key={n.id}>
                <div
                  className={`flex items-center gap-2 px-3 py-2 border-b border-canvas-border/40 transition-colors
                    ${isOutputNode ? 'cursor-pointer hover:bg-surface-raised' : 'cursor-default'}
                    ${isDisplay && isOutputNode ? 'bg-surface-raised border-l-2 border-l-accent' : ''}`}
                  onClick={() => {
                    if (!isOutputNode) return
                    // Clicking a log row uses the local pin; clear any store pin
                    useStore.setState({ pinnedOutputNodeId: null })
                    setLocalPin(isPinned ? null : n.id)
                  }}
                >
                  {STATUS_ICON[s?.status ?? 'idle']}

                  <span className="flex-1 text-xs font-medium text-slate-200 truncate">
                    {n.data?.label ?? n.type}
                  </span>

                  {/* Row counts — only for nodes that move data */}
                  {s?.rows_in !== undefined && s?.rows_out !== undefined && (
                    <span className="flex items-center gap-1 text-xs tabular-nums shrink-0">
                      <span className="text-muted">{s.rows_in.toLocaleString()}</span>
                      <ArrowRight size={9} className="text-subtle" />
                      <span className="text-accent font-medium">{s.rows_out.toLocaleString()}</span>
                    </span>
                  )}

                  {/* Duration */}
                  {s?.duration_ms !== undefined && (
                    <span className="text-xs text-subtle tabular-nums flex items-center gap-0.5 shrink-0 ml-1">
                      <Clock size={9} />
                      {s.duration_ms < 1000 ? `${s.duration_ms}ms` : `${(s.duration_ms / 1000).toFixed(1)}s`}
                    </span>
                  )}

                  {/* Chevron for output nodes */}
                  {isOutputNode && (
                    <span className="text-muted ml-1 shrink-0">
                      {isDisplay ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                    </span>
                  )}
                </div>

                {/* fast_read: which path each read took, so speed differences are explainable */}
                {s?.reads && s.reads.length > 0 && (
                  <div className="px-3 py-1 text-xs text-subtle border-b border-canvas-border/50">
                    {s.reads.map((r, i) => (
                      <div key={i} title={r.note}>
                        Read via <span className="text-slate-300">{READ_PATH[r.path] ?? r.path}</span>
                        {r.partitions > 1 && <> · {r.partitions} parallel slices</>}
                        {r.queries > 1 && <> · {r.queries} tables</>}
                        {r.note && <span className="block text-amber-300/80">{r.note}</span>}
                      </div>
                    ))}
                  </div>
                )}

                {/* Inline error — just the final exception line, click row for full */}
                {s?.status === 'error' && s.detail && (
                  <div className="px-3 py-1.5 text-xs text-red-300 font-mono bg-red-950/30
                                  border-b border-red-900/40 break-words">
                    {s.detail.trim().split('\n').pop()}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* ── Output content ─────────────────────────────────────── */}
      <div className="flex-1 overflow-hidden flex flex-col min-h-0">
        {displayNodeId ? (
          <>
            <div className="px-3 py-1.5 text-xs font-semibold text-muted uppercase tracking-wider
                            border-b border-canvas-border shrink-0 flex items-center justify-between">
              <span>
                {executedNodes.find((n) => n.id === displayNodeId)?.data?.label ?? 'Output'}
                {!pinnedNodeId && <span className="ml-2 text-subtle font-normal normal-case">(auto)</span>}
              </span>
              {pinnedNodeId && (
                <button
                  className="text-subtle hover:text-muted text-xs"
                  onClick={() => { setLocalPin(null); useStore.setState({ pinnedOutputNodeId: null }) }}
                  title="Unpin — auto-select first output node"
                >
                  unpin ×
                </button>
              )}
            </div>
            <div className="flex-1 overflow-auto min-h-0">
              <NodeOutputView nodeId={displayNodeId} />
            </div>
          </>
        ) : (
          <div className="p-4 text-xs text-muted italic">
            Click a Table View, Chart View, or Stop node in the log above to view its output.
          </div>
        )}
      </div>
    </div>
  )
}
