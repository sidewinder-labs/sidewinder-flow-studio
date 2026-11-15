/**
 * RouterNode — conditional branching.
 *
 * One target handle in, and one source handle per configured branch plus a
 * default. Each branch has a Python expression evaluated against `df`; the
 * first truthy one wins and the other branches are skipped for this run.
 *
 * Handle ids are the branch labels, which is how the executor knows which
 * edges to follow (edge.sourceHandle === chosen label).
 */
import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { DataNodeProps } from './nodeProps'
import { GitBranch } from 'lucide-react'
import { useStore } from '@/store'
import { StatusIcon } from '@/components/ui/NodeStatus'

export interface RouterBranch { label: string; expr: string }
export interface RouterData {
  label?: string
  branches?: RouterBranch[]
  default_label?: string
}

export const RouterNode = memo(({ id, data, selected }: DataNodeProps<RouterData>) => {
  const { execState, setSelectedNode } = useStore()
  const state = execState[id] as any
  const branches = (data.branches ?? []).filter((b) => b.label?.trim())
  const defaultLabel = data.default_label?.trim() || 'else'
  const rows = [...branches.map((b) => b.label.trim()), defaultLabel]
  const chosen = state?.chosen as string | undefined

  // Space the handles evenly down the right edge
  const top = (i: number) => `${((i + 1) / (rows.length + 1)) * 100}%`

  return (
    <div
      className={`node-card border-amber-500/50 bg-amber-950/25 min-w-[190px]
        ${selected ? 'ring-2 ring-amber-400 ring-offset-1 ring-offset-canvas-bg' : ''}`}
      onClick={() => setSelectedNode(id)}
    >
      <div className="node-header bg-amber-600/20 text-amber-300">
        <GitBranch size={12} />
        Router
        <span className="ml-auto"><StatusIcon status={state?.status ?? 'idle'} /></span>
      </div>

      <div className="node-body">
        <div className="text-sm font-medium text-slate-200">{data.label ?? 'Router'}</div>

        {rows.map((lbl, i) => {
          const isDefault = i === rows.length - 1
          const taken = chosen === lbl
          return (
            <div key={lbl + i}
              className={`flex items-center justify-between gap-2 text-xs rounded px-1 py-0.5
                ${taken ? 'bg-amber-500/20 text-amber-200' : 'text-muted'}`}>
              <span className="font-mono truncate">{lbl}</span>
              <span className="truncate opacity-70 max-w-[90px]">
                {isDefault ? 'otherwise' : (branches[i]?.expr || '—')}
              </span>
            </div>
          )
        })}

        {chosen && (
          <div className="text-xs text-amber-300">→ took <span className="font-mono">{chosen}</span></div>
        )}
      </div>

      <Handle type="target" position={Position.Left} id="in" />
      {rows.map((lbl, i) => (
        <Handle
          key={lbl + i}
          type="source"
          position={Position.Right}
          id={lbl}
          style={{ top: top(i) }}
          title={lbl}
        />
      ))}
    </div>
  )
})
RouterNode.displayName = 'RouterNode'
