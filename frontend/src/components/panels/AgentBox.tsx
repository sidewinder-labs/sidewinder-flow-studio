/**
 * AgentBox — AI assistant for generating processor / chart code.
 *
 * Sends the user's natural-language request plus the upstream DataFrame
 * schema to the backend, which calls the Anthropic API. The generated code
 * is shown for review and can be inserted into the node's editor.
 */
import { useState } from 'react'
import { Sparkles, Loader2, Check, X, ArrowRight } from 'lucide-react'
import { useStore } from '@/store'
import { api } from '@/utils/api'

interface AgentBoxProps {
  nodeId: string
  nodeType: 'processor' | 'chart' | 'report'
  engine?: string   // selects prompt style: duckdb, polars, else pandas
  onInsert: (code: string) => void   // called when user accepts generated code
}

// Find the immediate upstream node id (first parent) for schema context
function findUpstreamNodeId(nodeId: string): string | undefined {
  const { activeProject, activeFlowId } = useStore.getState()
  const flow = activeProject?.flows.find((f) => f.id === activeFlowId)
  if (!flow) return undefined
  const edge = (flow.edges as any[]).find((e) => e.target === nodeId)
  return edge?.source
}

export function AgentBox({ nodeId, nodeType, engine = 'pandas', onInsert }: AgentBoxProps) {
  const { activeProject } = useStore()
  const [open, setOpen] = useState(false)
  const [request, setRequest] = useState('')
  const [loading, setLoading] = useState(false)
  const [generated, setGenerated] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hadContext, setHadContext] = useState(false)
  const [providerName, setProviderName] = useState<string | null>(null)

  const placeholder = nodeType === 'chart'
    ? 'e.g. "bar chart of average salary by hire year for the Development department"'
    : nodeType === 'report'
      ? 'e.g. "KPI row with headcount and average salary, salary-by-department bar chart, detail table"'
      : engine === 'duckdb'
        ? 'e.g. "average current salary per department, highest first" — generates DuckDB SQL'
        : engine === 'polars'
          ? 'e.g. "salaries since 1990, average amount by year" — generates lazy Polars'
          : 'e.g. "join employees to salaries, then average salary by gender"'

  const generate = async () => {
    if (!request.trim() || !activeProject) return
    setLoading(true)
    setError(null)
    setGenerated(null)
    try {
      const upstream = findUpstreamNodeId(nodeId)
      const res = await api.agent.generate(activeProject.id, request.trim(), nodeType, upstream, engine)
      if (res.error) {
        setError(res.error)
      } else {
        setGenerated(res.code)
        setHadContext(res.had_context)
        setProviderName(res.provider)
      }
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const accept = () => {
    if (generated) onInsert(generated)
    setGenerated(null)
    setRequest('')
    setOpen(false)
  }

  if (!open) {
    return (
      <button
        className="flex items-center gap-1.5 w-full justify-center py-2 rounded-lg border border-dashed
                   border-accent/40 text-accent/90 text-xs font-medium
                   hover:bg-accent/10 hover:border-accent/60 transition-colors"
        onClick={() => setOpen(true)}
      >
        <Sparkles size={13} /> Generate with AI
      </button>
    )
  }

  return (
    <div className="rounded-lg border border-accent/30 bg-accent/5 p-3 space-y-2.5">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-accent">
          <Sparkles size={12} /> AI Assistant
        </span>
        <button className="text-subtle hover:text-muted" onClick={() => setOpen(false)}>
          <X size={13} />
        </button>
      </div>

      <textarea
        className="input text-xs font-normal min-h-[60px] resize-y w-full"
        placeholder={placeholder}
        value={request}
        onChange={(e) => setRequest(e.target.value)}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) generate()
        }}
        autoFocus
      />

      {!generated && (
        <button
          className="btn-pri w-full text-xs"
          onClick={generate}
          disabled={loading || !request.trim()}
        >
          {loading
            ? <><Loader2 size={12} className="animate-spin" /> Generating…</>
            : <><Sparkles size={12} /> Generate code</>}
        </button>
      )}

      {error && (
        <div className="text-xs text-red-300 bg-red-950/30 border border-red-900/50 rounded p-2">
          {error}
        </div>
      )}

      {generated && (
        <div className="space-y-2">
          <div className="flex items-center gap-1.5 text-xs text-muted">
            {hadContext
              ? <><Check size={11} className="text-status-ok" /> Generated using your data schema</>
              : <>Generated (no upstream data — run the flow first for schema-aware code)</>}
            {providerName && <span className="text-subtle">· {providerName}</span>}
          </div>
          <pre className="text-xs font-mono text-slate-300 bg-canvas-bg rounded p-2.5
                          overflow-auto max-h-48 border border-canvas-border whitespace-pre-wrap">
            {generated}
          </pre>
          <div className="flex gap-2">
            <button className="btn-pri flex-1 text-xs" onClick={accept}>
              <ArrowRight size={12} /> Insert into editor
            </button>
            <button className="btn-ghost text-xs" onClick={() => setGenerated(null)}>
              Retry
            </button>
          </div>
        </div>
      )}

      <div className="text-xs text-subtle">⌘/Ctrl + Enter to generate</div>
    </div>
  )
}
