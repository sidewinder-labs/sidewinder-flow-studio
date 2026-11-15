/**
 * AI Notes — connectable design-time nodes that describe a flow in plain
 * language + structured fields, then compile to real processors via
 * "Build with AI". Three kinds:
 *   ai_start — structured source spec (connection + sql | table | file)
 *   ai_step  — natural-language transform (+ optional output columns)
 *   ai_end   — structured load spec (connection + table + replace/append/custom)
 */
import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { DataNodeProps } from './nodeProps'
import { Sparkles, DatabaseZap, Wand2, HardDriveDownload } from 'lucide-react'
import { useStore } from '@/store'

const base =
  'node-card border-dashed !border-violet-500/60 bg-violet-950/25 min-w-[180px] max-w-[260px]'
const ring = (sel: boolean) =>
  sel ? 'ring-2 ring-violet-400 ring-offset-1 ring-offset-canvas-bg' : ''

function Header({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div className="node-header bg-violet-600/20 text-violet-300">
      {icon} {title}
      <Sparkles size={10} className="ml-auto opacity-70" />
    </div>
  )
}

export const AiStartNode = memo(({ id, data, selected }: DataNodeProps<any>) => {
  const { setSelectedNode } = useStore()
  const mode = data.source_mode ?? 'sql'
  const summary =
    mode === 'sql' ? (data.sql ? `SQL: ${String(data.sql).slice(0, 60)}…` : 'SQL not set')
    : mode === 'table' ? (data.table ? `${data.table} (${data.columns || '*'})` : 'table not set')
    : (data.file_path || 'file not set')
  return (
    <div className={`${base} ${ring(selected)}`} onClick={() => setSelectedNode(id)}>
      <Header icon={<DatabaseZap size={12} />} title="AI Start" />
      <div className="node-body">
        <div className="text-sm font-medium text-slate-200">{data.label ?? 'Source'}</div>
        <div className="text-xs text-violet-200/80">{data.connection || '⚠ pick a connection'}</div>
        <div className="text-xs text-muted truncate">{summary}</div>
      </div>
      <Handle type="source" position={Position.Right} id="out" />
    </div>
  )
})
AiStartNode.displayName = 'AiStartNode'

export const AiStepNode = memo(({ id, data, selected }: DataNodeProps<any>) => {
  const { setSelectedNode } = useStore()
  return (
    <div className={`${base} ${ring(selected)}`} onClick={() => setSelectedNode(id)}>
      <Header icon={<Wand2 size={12} />} title="AI Step" />
      <div className="node-body">
        <div className="text-sm font-medium text-slate-200">{data.label ?? 'Transform'}</div>
        <div className="text-xs text-muted line-clamp-3">
          {data.logic || <span className="italic opacity-60">describe the transformation…</span>}
        </div>
        {data.output_columns && (
          <div className="text-xs font-mono text-violet-300/80 truncate">→ {data.output_columns}</div>
        )}
      </div>
      <Handle type="target" position={Position.Left} id="in" />
      <Handle type="source" position={Position.Right} id="out" />
    </div>
  )
})
AiStepNode.displayName = 'AiStepNode'

export const AiEndNode = memo(({ id, data, selected }: DataNodeProps<any>) => {
  const { setSelectedNode } = useStore()
  const tmode = data.target_mode ?? 'db'
  const summary = tmode === 'db'
    ? `${data.table || '⚠ table?'} · ${data.write_mode || 'replace'}`
    : (data.file_path || '⚠ file path?')
  return (
    <div className={`${base} ${ring(selected)}`} onClick={() => setSelectedNode(id)}>
      <Header icon={<HardDriveDownload size={12} />} title="AI End" />
      <div className="node-body">
        <div className="text-sm font-medium text-slate-200">{data.label ?? 'Load'}</div>
        <div className="text-xs text-violet-200/80">{data.connection || (tmode === 'db' ? '⚠ pick a connection' : 'file target')}</div>
        <div className="text-xs text-muted truncate">{summary}</div>
      </div>
      <Handle type="target" position={Position.Left} id="in" />
    </div>
  )
})
AiEndNode.displayName = 'AiEndNode'
