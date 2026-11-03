import { memo } from 'react'
import { Handle, Position, NodeProps } from 'reactflow'
import { Code2, ChevronRight, ArrowRight } from 'lucide-react'
import { useStore } from '@/store'
import type { ProcessorData } from '@/types'
import { StatusIcon, RowPill, DurationBadge } from '@/components/ui/NodeStatus'
import { useEngines, paramSummary } from '@/utils/engines'

export const ProcessorNode = memo(({ id, data, selected }: NodeProps<ProcessorData>) => {
  const { execState, setSelectedNode } = useStore()
  const state = execState[id]
  const nodeEngine = (data as any).engine ?? 'pandas'
  const isDuck = nodeEngine === 'duckdb'
  const isPolars = nodeEngine === 'polars'
  const isSql = nodeEngine === 'sql'
  const isIbis = nodeEngine === 'ibis'
  const isRead = nodeEngine === 'fast_read'
  // Plugin engines: show which one, and (for form-driven ones) their settings
  const engines = useEngines()
  const pluginEngine = engines.find((e) => e.name === nodeEngine && e.source?.startsWith('plugin:'))
  // Any form-driven engine (plugin or built-in, like Database read) shows its settings
  const formEngine = engines.find((e) => e.name === nodeEngine && e.params?.length)
  const summary = paramSummary(formEngine, data as any)
  const firstLine = data.code?.split('\n').find((l) => {
    const t = l.trim()
    return t && !t.startsWith('#') && !t.startsWith('--')
  }) ?? ''

  return (
    <div
      className={`node-card border-node-processorBorder bg-node-processor
        ${selected ? 'ring-2 ring-emerald-400 ring-offset-1 ring-offset-canvas-bg' : ''}`}
      onClick={() => setSelectedNode(id)}
    >
      <div className="node-header bg-node-processorBorder/20 text-emerald-300">
        <Code2 size={12} />
        Processor
        {isDuck && (
          <span className="text-xs font-mono bg-yellow-500/15 text-yellow-300 rounded px-1 py-px leading-none"
                title="DuckDB engine — SQL over Arrow">
            🦆 SQL
          </span>
        )}
        {isPolars && (
          <span className="text-xs font-mono bg-sky-500/15 text-sky-300 rounded px-1 py-px leading-none"
                title="Polars engine — lazy, streaming">
            🐻‍❄️ pl
          </span>
        )}
        {isSql && (
          <span className="text-xs font-mono bg-amber-500/15 text-amber-300 rounded px-1 py-px leading-none"
                title="SQL pushdown — runs in the warehouse">⚡ SQL</span>
        )}
        {isIbis && (
          <span className="text-xs font-mono bg-amber-500/15 text-amber-300 rounded px-1 py-px leading-none"
                title="Ibis pushdown — runs in the warehouse">⚡ ibis</span>
        )}
        {isRead && (
          <span className="text-xs font-mono bg-cyan-500/15 text-cyan-300 rounded px-1 py-px leading-none"
                title="Database read — fast, no code; any engine can follow">⇣ read</span>
        )}
        {pluginEngine && (
          <span className="text-xs font-mono bg-violet-500/15 text-violet-300 rounded px-1 py-px leading-none truncate max-w-[9rem]"
                title={`${pluginEngine.label} — ${pluginEngine.description} (from ${pluginEngine.source})`}>
            🧩 {pluginEngine.label}
          </span>
        )}
        <span className="ml-auto"><StatusIcon status={state?.status ?? 'idle'} /></span>
      </div>

      <div className="node-body">
        <div className="text-sm font-medium text-slate-200 truncate">{data.label}</div>

        {firstLine ? (
          <div className="font-mono text-xs text-emerald-300/70 bg-canvas-bg/60 border border-canvas-border rounded px-2 py-1 truncate">
            {firstLine.slice(0, 55)}{firstLine.length > 55 ? '…' : ''}
          </div>
        ) : summary ? (
          <div className="text-xs text-slate-400 line-clamp-2 break-words" title={summary}>{summary}</div>
        ) : (
          <div className="text-xs text-muted italic">No code — click to edit</div>
        )}

        {state && state.status !== 'idle' && state.status !== 'running' && (
          <div className="flex items-center gap-1.5 flex-wrap pt-0.5">
            {state.rows_in !== undefined && (
              <>
                <RowPill rows={state.rows_in} color="text-muted" />
                <ArrowRight size={10} className="text-muted" />
                <RowPill rows={state.rows_out!} />
              </>
            )}
            {state.duration_ms !== undefined && <DurationBadge ms={state.duration_ms} />}
          </div>
        )}
      </div>

      <div className="node-footer border-node-processorBorder/30" onClick={() => setSelectedNode(id)}>
        <ChevronRight size={10} /> {summary && !firstLine ? 'Edit' : 'Edit Code'}
      </div>

      <Handle type="target" position={Position.Left}  id="in" />
      <Handle type="source" position={Position.Right} id="out" />
    </div>
  )
})
ProcessorNode.displayName = 'ProcessorNode'
