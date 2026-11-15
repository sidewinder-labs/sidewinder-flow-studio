import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { DataNodeProps } from './nodeProps'
import { Zap, Clock, ChevronRight } from 'lucide-react'
import { useStore } from '@/store'
import type { TriggerData } from '@/types'
import { StatusIcon } from '@/components/ui/NodeStatus'

export const TriggerNode = memo(({ id, data, selected }: DataNodeProps<TriggerData>) => {
  const { execState, setSelectedNode } = useStore()
  const state = execState[id]
  const isSchedule = data.run_mode === 'schedule'

  return (
    <div
      className={`node-card border-node-triggerBorder bg-node-trigger
        ${selected ? 'ring-2 ring-accent ring-offset-1 ring-offset-canvas-bg' : ''}`}
      onClick={() => setSelectedNode(id)}
    >
      <div className="node-header bg-node-triggerBorder/20 text-sky-300">
        {isSchedule ? <Clock size={12} /> : <Zap size={12} />}
        {isSchedule ? 'Scheduled Trigger' : 'Manual Trigger'}
        <span className="ml-auto"><StatusIcon status={state?.status ?? 'idle'} /></span>
      </div>

      <div className="node-body">
        <div className="text-sm font-medium text-slate-200">{data.label}</div>

        {isSchedule ? (
          <div className="space-y-0.5">
            <div className="font-mono text-xs text-sky-300 bg-canvas-bg/60 border border-canvas-border rounded px-2 py-1">
              {data.cron || '— no cron set —'}
            </div>
            <div className="text-xs text-muted">{data.timezone}</div>
          </div>
        ) : (
          <div className="text-xs text-muted italic">Click Run to trigger</div>
        )}
      </div>

      <div className="node-footer border-node-triggerBorder/30" onClick={() => setSelectedNode(id)}>
        <ChevronRight size={10} /> Configure
      </div>

      {/* Trigger has only output handle */}
      <Handle type="source" position={Position.Right} id="out" />
    </div>
  )
})
TriggerNode.displayName = 'TriggerNode'
