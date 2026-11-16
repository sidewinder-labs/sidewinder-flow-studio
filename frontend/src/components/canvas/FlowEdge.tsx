import { EdgeProps, getBezierPath, EdgeLabelRenderer } from '@xyflow/react'
import { useStore } from '@/store'

export function FlowEdge({
  id, sourceX, sourceY, targetX, targetY,
  sourcePosition, targetPosition, source, target,
}: EdgeProps) {
  const { execState, activeProject, activeFlowId } = useStore()
  const sourceState = execState[source]
  const targetState = execState[target]

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition,
    targetX, targetY, targetPosition,
  })

  // Determine if the source node is a hard stop (which severs this edge)
  const flow = activeProject?.flows.find((f) => f.id === activeFlowId)
  const sourceNode = (flow?.nodes as any[])?.find((n) => n.id === source)
  const isHardStop = sourceNode?.type === 'stop' && (sourceNode?.data?.mode ?? 'hard') === 'hard'

  // The edge is "severed" when:
  //  - source is a hard stop AND it actually ran (status stopped), OR
  //  - the target was blocked (downstream of a hard stop)
  const sourceStopped = sourceState?.status === 'stopped'
  const targetBlocked = targetState?.status === 'blocked'
  const isSevered = (isHardStop && sourceStopped) || targetBlocked

  const rowsOut = sourceState?.rows_out
  const isRunning = sourceState?.status === 'running'

  // Which variable name does this edge's data arrive as, in the target node?
  // Convention: first incoming edge → `df`, second → `df2`, third → `df3`, …
  // (matches the executor). Only meaningful for nodes that consume a frame.
  const targetNode = (flow?.nodes as any[])?.find((n) => n.id === target)
  const consumesFrame = targetNode && !['trigger', 'annotation'].includes(targetNode.type)
  let varName = ''
  if (consumesFrame) {
    const incoming = (flow?.edges as any[])
      ?.filter((e) => e.target === target)
      // stable order by source node id so numbering is deterministic
      .sort((a, b) => String(a.source).localeCompare(String(b.source))) ?? []
    const idx = incoming.findIndex((e) => e.source === source)
    varName = idx <= 0 ? 'df' : `df${idx + 1}`
  }

  // Edge colour logic
  // Edge colour logic (theme variables, tailwind.config.js)
  let stroke = 'rgb(var(--t-subtle))'                              // grey — not run
  if (isSevered)        stroke = 'rgb(var(--t-status-stopped))'    // orange — halted at stop
  else if (isRunning)   stroke = 'rgb(var(--t-status-running))'    // yellow — flowing now
  else if (rowsOut !== undefined) stroke = 'rgb(var(--t-accent))'  // blue — data passed

  return (
    <>
      <path
        id={id}
        className="react-flow__edge-path"
        d={edgePath}
        style={{
          stroke,
          strokeWidth: 2,
          strokeDasharray: isSevered ? '2 5' : isRunning ? '6 3' : undefined,
          opacity: isSevered ? 0.5 : 1,
          animation: isRunning ? 'flowDash 0.6s linear infinite' : undefined,
        }}
      />

      {/* Show variable name + row count if data actually flowed (not severed) */}
      {rowsOut !== undefined && !isSevered && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              pointerEvents: 'none',
            }}
            className="bg-surface-raised border border-accent/40 rounded-full
                       px-2 py-0.5 text-xs font-mono tabular-nums
                       shadow-lg select-none whitespace-nowrap flex items-center gap-1.5"
          >
            {varName && <span className="text-slate-400">{varName}</span>}
            <span className="text-accent">{rowsOut.toLocaleString()}</span>
          </div>
        </EdgeLabelRenderer>
      )}

      {/* Show a "halted" badge on severed edges */}
      {isSevered && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              pointerEvents: 'none',
            }}
            className="bg-orange-950/80 border border-orange-600/50 rounded-full
                       px-2 py-0.5 text-xs font-medium text-orange-300
                       shadow-lg select-none whitespace-nowrap flex items-center gap-1"
          >
            ⊗ halted
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}
