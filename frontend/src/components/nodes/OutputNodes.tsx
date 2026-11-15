import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { DataNodeProps } from './nodeProps'
import { OctagonX, Table2, BarChart3, ChevronRight, ScanSearch } from 'lucide-react'
import { useStore } from '@/store'
import { StatusIcon, RowPill, DurationBadge } from '@/components/ui/NodeStatus'
import type { StopData, TableOutData, ChartOutData } from '@/types'

// ── Stop Node ────────────────────────────────────────────────────────────────

export const StopNode = memo(({ id, data, selected }: DataNodeProps<StopData>) => {
  const { execState, setSelectedNode, setSidebarTab, inspectNode } = useStore()
  const state = execState[id]

  const handleInspect = (e: React.MouseEvent) => {
    e.stopPropagation()
    inspectNode(id)
  }

  return (
    <div
      className={`node-card border-node-stopBorder bg-node-stop
        ${selected ? 'ring-2 ring-orange-400 ring-offset-1 ring-offset-canvas-bg' : ''}`}
      onClick={() => setSelectedNode(id)}
    >
      <div className="node-header bg-node-stopBorder/20 text-orange-300">
        <OctagonX size={12} />
        {data.mode === 'hard' ? 'Stop' : 'Tap'}
        <span className="ml-auto"><StatusIcon status={state?.status ?? 'idle'} /></span>
      </div>

      <div className="node-body">
        <div className="text-sm font-medium text-slate-200">{data.label}</div>
        <div className="text-xs text-muted">
          {data.mode === 'hard' ? 'Halts flow here' : 'Observe without stopping'}
        </div>
        {state?.status === 'stopped' && state.rows_out !== undefined && (
          <div className="flex items-center gap-2">
            <RowPill rows={state.rows_out} color="text-orange-300" />
            {state.duration_ms !== undefined && <DurationBadge ms={state.duration_ms} />}
            <button
              className="ml-auto text-xs text-accent hover:text-accent-dim underline"
              onClick={handleInspect}
            >
              Inspect →
            </button>
          </div>
        )}
      </div>

      <div className="node-footer border-node-stopBorder/30" onClick={() => setSelectedNode(id)}>
        <ChevronRight size={10} /> Configure
      </div>

      <Handle type="target" position={Position.Left}  id="in" />
      {/* Tap mode: flow continues; hard stop: no output handle needed but we add it for flexibility */}
      <Handle type="source" position={Position.Right} id="out" style={{ opacity: data.mode === 'hard' ? 0.3 : 1 }} />
    </div>
  )
})
StopNode.displayName = 'StopNode'


// ── Table Output Node ─────────────────────────────────────────────────────────

export const TableOutNode = memo(({ id, data, selected }: DataNodeProps<TableOutData>) => {
  const { execState, setSelectedNode, setSidebarTab, inspectNode } = useStore()
  const state = execState[id]

  return (
    <div
      className={`node-card border-node-tableOutBorder bg-node-tableOut
        ${selected ? 'ring-2 ring-indigo-400 ring-offset-1 ring-offset-canvas-bg' : ''}`}
      onClick={() => inspectNode(id)}
    >
      <div className="node-header bg-node-tableOutBorder/20 text-indigo-300">
        <Table2 size={12} />
        Table View
        <span className="ml-auto"><StatusIcon status={state?.status ?? 'idle'} /></span>
      </div>

      <div className="node-body">
        <div className="text-sm font-medium text-slate-200">{data.label}</div>
        {state?.table_total !== undefined ? (
          <div className="flex items-center gap-2">
            <RowPill rows={state.table_total} color="text-indigo-300" />
            {state.duration_ms !== undefined && <DurationBadge ms={state.duration_ms} />}
          </div>
        ) : (
          <div className="text-xs text-muted italic">Run flow to populate</div>
        )}
        {state?.table_schema && (
          <div className="text-xs text-muted">
            {state.table_schema.length} columns
          </div>
        )}
      </div>

      <Handle type="target" position={Position.Left} id="in" />
    </div>
  )
})
TableOutNode.displayName = 'TableOutNode'


// ── Profile Node ──────────────────────────────────────────────────────────────

export const ProfileNode = memo(({ id, data, selected }: DataNodeProps<any>) => {
  const { execState, inspectNode } = useStore()
  const state = execState[id]

  return (
    <div
      className={`node-card border-cyan-600/50 bg-cyan-950/30
        ${selected ? 'ring-2 ring-cyan-400 ring-offset-1 ring-offset-canvas-bg' : ''}`}
      onClick={() => inspectNode(id)}
    >
      <div className="node-header bg-cyan-600/20 text-cyan-300">
        <ScanSearch size={12} />
        Profile
        <span className="ml-auto"><StatusIcon status={state?.status ?? 'idle'} /></span>
      </div>

      <div className="node-body">
        <div className="text-sm font-medium text-slate-200">{data.label ?? 'Profile'}</div>
        {state?.table_total !== undefined ? (
          <div className="flex items-center gap-2">
            <RowPill rows={state.table_total} color="text-cyan-300" />
            {state.duration_ms !== undefined && <DurationBadge ms={state.duration_ms} />}
          </div>
        ) : (
          <div className="text-xs text-muted italic">Run to profile columns</div>
        )}
        <div className="text-xs text-muted">nulls · distinct · top values · stats</div>
      </div>

      <Handle type="target" position={Position.Left} id="in" />
    </div>
  )
})
ProfileNode.displayName = 'ProfileNode'


// ── Chart Output Node ─────────────────────────────────────────────────────────

export const ChartOutNode = memo(({ id, data, selected }: DataNodeProps<ChartOutData>) => {
  const { execState, setSelectedNode, setSidebarTab, inspectNode } = useStore()
  const state = execState[id]
  const firstLine = data.code?.split('\n').find((l) => l.trim() && !l.trim().startsWith('#')) ?? ''

  return (
    <div
      className={`node-card border-node-chartOutBorder bg-node-chartOut
        ${selected ? 'ring-2 ring-fuchsia-400 ring-offset-1 ring-offset-canvas-bg' : ''}`}
      onClick={() => inspectNode(id)}
    >
      <div className="node-header bg-node-chartOutBorder/20 text-fuchsia-300">
        <BarChart3 size={12} />
        Chart View
        <span className="ml-auto"><StatusIcon status={state?.status ?? 'idle'} /></span>
      </div>

      <div className="node-body">
        <div className="text-sm font-medium text-slate-200">{data.label}</div>
        {firstLine ? (
          <div className="font-mono text-xs text-fuchsia-300/70 bg-canvas-bg/60 border border-canvas-border rounded px-2 py-1 truncate">
            {firstLine.slice(0, 50)}{firstLine.length > 50 ? '…' : ''}
          </div>
        ) : (
          <div className="text-xs text-muted italic">No chart code — click to edit</div>
        )}
        {state?.status === 'ok' && state.chart_type && (
          <div className="text-xs text-fuchsia-400">
            ✓ {state.chart_type} chart ready
          </div>
        )}
      </div>

      <div className="node-footer border-node-chartOutBorder/30"
        onClick={() => inspectNode(id)}>
        <ChevronRight size={10} /> View Chart
      </div>

      <Handle type="target" position={Position.Left} id="in" />
    </div>
  )
})
ChartOutNode.displayName = 'ChartOutNode'
