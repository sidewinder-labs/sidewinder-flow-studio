import { useCallback, useMemo, useEffect, useState, useRef } from 'react'
import {
  ReactFlow, Background, Controls, MiniMap, BackgroundVariant,
  addEdge, useNodesState, useEdgesState, useReactFlow, ReactFlowProvider,
  type Connection, type Node, type Edge, type NodeChange, type EdgeChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { TriggerNode }   from '@/components/nodes/TriggerNode'
import { ProcessorNode } from '@/components/nodes/ProcessorNode'
import { StopNode, TableOutNode, ChartOutNode, ProfileNode } from '@/components/nodes/OutputNodes'
import { ExploreNode, ReportNode } from '@/components/nodes/ReportNodes'
import { AnnotationNode } from '@/components/nodes/AnnotationNode'
import { AiStartNode, AiStepNode, AiEndNode } from '@/components/nodes/AiNotes'
import { RouterNode } from '@/components/nodes/RouterNode'
import { FlowEdge }      from './FlowEdge'
import { ContextMenu }   from './ContextMenu'
import { useStore, registerCanvasCallbacks, unregisterCanvasCallbacks } from '@/store'

const NODE_TYPES = {
  trigger:   TriggerNode,
  processor: ProcessorNode,
  stop:      StopNode,
  table_out: TableOutNode,
  chart_out: ChartOutNode,
  explore_out: ExploreNode,
  report_out: ReportNode,
  annotation: AnnotationNode,
  profile_out: ProfileNode,
  ai_start: AiStartNode,
  ai_step: AiStepNode,
  ai_end: AiEndNode,
  router: RouterNode,
}
const EDGE_TYPES = { default: FlowEdge }

const NODE_DEFAULTS: Record<string, object> = {
  trigger:   { label: 'Start', run_mode: 'manual', cron: '', timezone: 'UTC' },
  processor: { label: 'Processor', code: '' },
  stop:      { label: 'Stop', mode: 'hard' },
  table_out: { label: 'Table View' },
  chart_out: { label: 'Chart View', code: '' },
  explore_out: { label: 'Explore' },
  report_out:  { label: 'Report', report_name: '', code: '' },
  annotation: { text: 'Note', color: 'slate', fontSize: 14 },
  profile_out: { label: 'Profile', top_n: 5, hist_bins: 10 },
  ai_start: { label: 'Source', source_mode: 'sql' },
  ai_step:  { label: 'Transform', logic: '' },
  ai_end:   { label: 'Load', target_mode: 'db', write_mode: 'replace' },
  router:   { label: 'Router', branches: [{ label: 'match', expr: 'len(df) > 0' }], default_label: 'else' },
}

/** A saved node as React Flow 12 should see it. Files written by older
 *  versions can hold React Flow 11's measured size as width/height (and
 *  positionAbsolute); in 12, width/height fix a node's size, so a processor
 *  would stop growing with its content. Only annotations have a size: the one
 *  their user gave them, stored in `style`. */
function fromSaved(n: any): Node {
  const { width, height, positionAbsolute: _pa, measured: _m, ...rest } = n
  if (n.type !== 'annotation') return rest
  const w = n.style?.width ?? width, h = n.style?.height ?? height
  return { ...rest, ...(w != null ? { width: w } : {}), ...(h != null ? { height: h } : {}) }
}

/** What is saved: React Flow's runtime fields (measured size, drag and
 *  resize state) stay out of the flow file. The backend drops width/height as
 *  canvas state, so an annotation's size — which React Flow 12's resizer sets
 *  as width/height — is saved in `style`, as React Flow 11 kept it. */
function toSaved(n: Node): Node {
  const { measured: _m, dragging: _d, resizing: _r, ...rest } = n as any
  if (n.type !== 'annotation' || (n.width == null && n.height == null)) return rest
  return { ...rest, style: { ...n.style,
    ...(n.width != null ? { width: n.width } : {}), ...(n.height != null ? { height: n.height } : {}) } }
}
const savedNodes = (ns: Node[]) => ns.map(toSaved)

interface CanvasInnerProps { flowId: string }

function CanvasInner({ flowId }: CanvasInnerProps) {
  const { activeProject, updateFlowCanvas, setSelectedNode } = useStore()
  const { screenToFlowPosition } = useReactFlow()

  const [contextMenu, setContextMenu] = useState<{
    nodeId: string; nodeType: string; x: number; y: number
  } | null>(null)

  const flow = activeProject?.flows.find((f) => f.id === flowId)
  const initialNodes = useMemo(() => ((flow?.nodes ?? []) as any[]).map(fromSaved), [flowId])
  const initialEdges = useMemo(() => (flow?.edges ?? []) as Edge[], [flowId])

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges)

  // Keep refs so the get callbacks always return the latest value
  const nodesRef = useRef(nodes)
  const edgesRef = useRef(edges)
  useEffect(() => { nodesRef.current = nodes }, [nodes])
  useEffect(() => { edgesRef.current = edges }, [edges])

  // Register all four canvas callbacks so the store can:
  //  - push node data changes (updateNodeData, toggleStopMode)
  //  - add/remove nodes (addNodeToCanvas, removeNodesFromCanvas, duplicateNode)
  //  - add/remove edges (addEdgesToCanvas)
  //  - read current state before running (runFlow syncs RF → store)
  useEffect(() => {
    registerCanvasCallbacks(
      setNodes,
      setEdges,
      () => savedNodes(nodesRef.current),
      () => edgesRef.current,
    )
    return () => unregisterCanvasCallbacks()
  }, [setNodes, setEdges])

  // Persist canvas on every RF change (move, delete, select, etc.)
  const handleNodesChange = useCallback((changes: NodeChange[]) => {
    onNodesChange(changes)
    setTimeout(() => {
      setNodes((current) => {
        updateFlowCanvas(flowId, savedNodes(current), edgesRef.current)
        return current
      })
    }, 0)
  }, [onNodesChange, flowId, updateFlowCanvas, setNodes])

  const handleEdgesChange = useCallback((changes: EdgeChange[]) => {
    onEdgesChange(changes)
    setTimeout(() => {
      setEdges((current) => {
        updateFlowCanvas(flowId, savedNodes(nodesRef.current), current)
        return current
      })
    }, 0)
  }, [onEdgesChange, flowId, updateFlowCanvas, setEdges])

  const onConnect = useCallback((c: Connection) => {
    setEdges((prev) => {
      const newEdges = addEdge({ ...c, type: 'default' }, prev)
      updateFlowCanvas(flowId, savedNodes(nodesRef.current), newEdges)
      return newEdges
    })
  }, [flowId, setEdges, updateFlowCanvas])

  const onDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    const nodeType = event.dataTransfer.getData('application/fs-node-type')
    if (!nodeType) return
    const position = screenToFlowPosition({ x: event.clientX, y: event.clientY })
    const isAnnotation = nodeType === 'annotation'
    // If dragged from the Custom section, pre-fill code/engine/label
    let customData = {}
    const customRaw = event.dataTransfer.getData('application/fs-custom-processor')
    if (customRaw) {
      try { customData = JSON.parse(customRaw) } catch {}
    }
    const newNode: Node = {
      id: crypto.randomUUID(),
      type: nodeType,
      position,
      data: { ...(NODE_DEFAULTS[nodeType] ?? {}), ...customData },
      // Annotations render behind functional nodes and start with a usable size
      ...(isAnnotation
        ? { zIndex: -1, width: 220, height: 90 }
        : {}),
    }
    setNodes((prev) => {
      const newNodes = [...prev, newNode]
      updateFlowCanvas(flowId, savedNodes(newNodes), edgesRef.current)
      return newNodes
    })
  }, [flowId, setNodes, updateFlowCanvas, screenToFlowPosition])

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }, [])

  const onNodeContextMenu = useCallback((e: React.MouseEvent, node: Node) => {
    e.preventDefault()
    e.stopPropagation()
    setContextMenu({ nodeId: node.id, nodeType: node.type ?? 'processor', x: e.clientX, y: e.clientY })
  }, [])

  return (
    <>
      {contextMenu && (
        <ContextMenu
          nodeId={contextMenu.nodeId}
          nodeType={contextMenu.nodeType}
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={() => setContextMenu(null)}
        />
      )}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={onConnect}
        onPaneClick={() => { setSelectedNode(null); setContextMenu(null) }}
        onNodeClick={(_, n) => setSelectedNode(n.id)}
        onNodeContextMenu={onNodeContextMenu}
        onPaneContextMenu={(e) => e.preventDefault()}
        onDrop={onDrop}
        onDragOver={onDragOver}
        fitView
        deleteKeyCode="Delete"
        proOptions={{ hideAttribution: true }}
        colorMode="dark"
        className="bg-canvas-bg"
      >
        <Background variant={BackgroundVariant.Dots} gap={28} size={1} color="#1e2540" />
        <Controls className="!bg-surface !border-canvas-border" />
        <MiniMap
          nodeColor={(n) => ({
            trigger:   '#0ea5e9', processor: '#34d399',
            stop:      '#fb923c', table_out: '#818cf8', chart_out: '#e879f9',
            explore_out: '#2dd4bf', report_out: '#f472b6',
            annotation: '#475569', profile_out: '#22d3ee',
            ai_start: '#8b5cf6', ai_step: '#8b5cf6', ai_end: '#8b5cf6', router: '#f59e0b',
          }[n.type ?? ''] ?? '#2d3a52')}
          className="!bg-surface !border-canvas-border"
        />
      </ReactFlow>
    </>
  )
}

interface CanvasProps { flowId: string }
export function Canvas({ flowId }: CanvasProps) {
  return (
    <ReactFlowProvider>
      <CanvasInner flowId={flowId} />
    </ReactFlowProvider>
  )
}
