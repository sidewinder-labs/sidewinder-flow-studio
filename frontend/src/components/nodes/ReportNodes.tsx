/**
 * ExploreNode & ReportNode. Explore caches the incoming frame and opens it in
 * the in-app Explore view; Report publishes an authored Streamlit dashboard.
 */
import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { DataNodeProps } from './nodeProps'
import { Compass, LayoutDashboard, ExternalLink } from 'lucide-react'
import { useStore } from '@/store'
import { StatusIcon } from '@/components/ui/NodeStatus'
import type { ExploreData, ReportData } from '@/types'

function ReportishNode({
  id, data, selected, kind,
}: DataNodeProps<ExploreData | ReportData> & { kind: 'explore' | 'report' }) {
  const { execState, setSelectedNode } = useStore()
  const state = execState[id]
  const isExplore = kind === 'explore'

  const border = isExplore ? 'border-teal-500/50' : 'border-pink-500/50'
  const bg     = isExplore ? 'bg-teal-950/40'     : 'bg-pink-950/40'
  const text   = isExplore ? 'text-teal-300'      : 'text-pink-300'
  const ring   = isExplore ? 'ring-teal-400'      : 'ring-pink-400'

  return (
    <div
      className={`node-card ${border} ${bg} ${selected ? `ring-2 ${ring} ring-offset-1 ring-offset-canvas-bg` : ''}`}
      onClick={() => setSelectedNode(id)}
    >
      <Handle type="target" position={Position.Left} id="in" />
      <div className={`node-header ${isExplore ? 'bg-teal-500/15' : 'bg-pink-500/15'} ${text}`}>
        {isExplore ? <Compass size={12} /> : <LayoutDashboard size={12} />}
        {isExplore ? 'Explore' : 'Report'}
        <span className="ml-auto"><StatusIcon status={state?.status ?? 'idle'} /></span>
      </div>
      <div className="node-body">
        <div className="text-sm font-medium text-slate-200 truncate">{data.label}</div>
        {isExplore ? (
          <button
            onClick={(e) => { e.stopPropagation(); openNodeInExplore(id) }}
            className={`inline-flex items-center gap-1 text-xs mt-1 ${text} underline underline-offset-2 hover:brightness-125`}
            title="Open this node's output from the last run in Explore"
          >
            Open in Explore <Compass size={10} />
          </button>
        ) : state?.report_url ? (
          <a
            href={state.report_url}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
            className={`inline-flex items-center gap-1 text-xs mt-1 ${text} underline underline-offset-2 hover:brightness-125`}
          >
            Open dashboard <ExternalLink size={10} />
          </a>
        ) : (
          <div className="text-xs text-muted italic">
            Run flow to publish{(data as ReportData).report_name ? ` → ${(data as ReportData).report_name}` : ''}
          </div>
        )}
      </div>
    </div>
  )
}

/** Open an Explore node's cached frame (written on every run) in the Explore view. */
export function openNodeInExplore(nodeId: string) {
  const { activeProject, openExplore } = useStore.getState()
  if (activeProject) openExplore({ kind: 'frame', project_id: activeProject.id, node_id: nodeId })
}

export const ExploreNode = memo((p: DataNodeProps<ExploreData>) => (
  <ReportishNode {...p} kind="explore" />
))
export const ReportNode = memo((p: DataNodeProps<ReportData>) => (
  <ReportishNode {...p} kind="report" />
))
