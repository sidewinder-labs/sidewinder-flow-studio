import { create } from 'zustand'
import type { Project, Connection, Variable, FlowMeta, NodeExecState, WsEvent, ExploreSource } from '@/types'
import { api } from '@/utils/api'

// ── Canvas sync callbacks ─────────────────────────────────────────────────────
// These are registered by the Canvas component so operations from outside
// (context menu, props panel) can push directly into React Flow's internal state.
// Without these, store updates and RF state diverge.

type SetNodesFn = (updater: (prev: any[]) => any[]) => void
type SetEdgesFn = (updater: (prev: any[]) => any[]) => void
type GetNodesFn = () => any[]
type GetEdgesFn = () => any[]

let _setNodes: SetNodesFn | null = null
let _setEdges: SetEdgesFn | null = null
let _getNodes: GetNodesFn | null = null
let _getEdges: GetEdgesFn | null = null

export function registerCanvasCallbacks(
  setNodes: SetNodesFn,
  setEdges: SetEdgesFn,
  getNodes: GetNodesFn,
  getEdges: GetEdgesFn,
) {
  _setNodes = setNodes
  _setEdges = setEdges
  _getNodes = getNodes
  _getEdges = getEdges
}

export function unregisterCanvasCallbacks() {
  _setNodes = null; _setEdges = null
  _getNodes = null; _getEdges = null
}

/** Resolves once React Flow holds node `id`. Canvas updates are async, and
 *  saveProject() reads nodes from the canvas, so saving straight after
 *  addNodeToCanvas() would drop the new node. Resolves at once without a canvas. */
export function waitForCanvasNode(id: string, timeoutMs = 2000): Promise<void> {
  return new Promise((resolve) => {
    const t0 = Date.now()
    const check = () => {
      if (!_getNodes || _getNodes().some((n) => n.id === id) || Date.now() - t0 > timeoutMs) resolve()
      else setTimeout(check, 16)
    }
    check()
  })
}

// Legacy — kept for compatibility, now uses the full callbacks
export function registerCanvasNodeUpdater(fn: (nodeId: string, data: object) => void) {}
export function unregisterCanvasNodeUpdater() {}

// ── Types ─────────────────────────────────────────────────────────────────────

type SidebarTab = 'properties' | 'imports' | 'output' | 'metadata'

interface AppState {
  projects: Project[]
  activeProject: Project | null
  activeFlowId: string | null
  connections: Connection[]
  variables: Variable[]
  execState: Record<string, NodeExecState>
  running: boolean
  runError: string | null
  clearRunError: () => void
  selectedNodeId: string | null
  sidebarTab: SidebarTab
  sidebarVisible: boolean
  sidebarWidth: number
  pinnedOutputNodeId: string | null   // which node's output to show in Output tab

  // Top-level view: the flow editor, or Explore on a source
  view: 'flows' | 'explore'
  exploreSource: ExploreSource | null
  openExplore: (source?: ExploreSource | null) => void
  closeExplore: () => void

  loadProjects: () => Promise<void>
  openProject: (id: string) => Promise<void>
  closeProject: () => void
  createProject: (name: string, description?: string) => Promise<void>
  saveProject: () => Promise<void>
  deleteProject: (id: string, dropPersisted?: boolean) => Promise<void>

  activeFlow: () => FlowMeta | null
  setActiveFlow: (id: string) => void
  addFlow: () => void
  deleteFlow: (id: string, dropPersisted?: boolean) => Promise<void>
  toggleFlowActive: (id: string) => void
  renameFlow: (flowId: string, name: string) => void

  // Canvas mutations — update BOTH store AND React Flow canvas state
  updateFlowCanvas: (flowId: string, nodes: unknown[], edges: unknown[]) => void
  updateFlowImports: (flowId: string, imports: string) => void
  updateFlowShared: (flowId: string, shared_functions: string) => void
  /** Functions, Imports and library provenance together (insert/update from the library); saves. */
  updateFlowLibrary: (flowId: string, v: { shared_functions: string; imports: string;
    function_provenance: Record<string, any> }) => Promise<void>
  applyBuiltFlow: (flowId: string, built: { node_id: string; label: string; code: string }[]) => void
  updateNodeData: (flowId: string, nodeId: string, data: object) => void
  addNodeToCanvas: (flowId: string, node: object) => void
  addEdgesToCanvas: (flowId: string, edges: object[]) => void
  removeNodesFromCanvas: (flowId: string, nodeIds: string[]) => void
  duplicateNode: (nodeId: string) => void
  addStopAfterNode: (sourceNodeId: string, mode: 'hard' | 'tap') => void

  loadConnections: () => Promise<void>
  loadVariables: () => Promise<void>

  runFlow: (startFromNode?: string, resumeFromNode?: string) => Promise<void>
  handleWsEvent: (event: WsEvent) => void
  clearExecState: () => void

  setSelectedNode: (id: string | null) => void
  setSidebarTab: (tab: SidebarTab) => void
  setSidebarVisible: (v: boolean) => void
  setSidebarWidth: (w: number) => void
  inspectNode: (nodeId: string) => void   // open output tab + pin this node
}

// ── Store ─────────────────────────────────────────────────────────────────────

export const useStore = create<AppState>((set, get) => ({
  projects: [],
  activeProject: null,
  activeFlowId: null,
  connections: [],
  variables: [],
  execState: {},
  running: false,
  runError: null,
  clearRunError: () => set({ runError: null }),
  selectedNodeId: null,
  sidebarTab: 'properties',
  sidebarVisible: true,
  sidebarWidth: 380,
  pinnedOutputNodeId: null,

  view: 'flows',
  exploreSource: null,
  openExplore: (source) => set((s) => ({ view: 'explore', exploreSource: source === undefined ? s.exploreSource : source })),
  closeExplore: () => set({ view: 'flows' }),

  // ── Projects ───────────────────────────────────────────────────────────────

  loadProjects: async () => set({ projects: await api.projects.list() }),

  openProject: async (id) => {
    const project = await api.projects.get(id)
    set({ activeProject: project, activeFlowId: project.flows[0]?.id ?? null, execState: {} })
  },

  closeProject: () => set({ activeProject: null, activeFlowId: null, execState: {} }),

  createProject: async (name, description) => {
    const project = await api.projects.create(name, description)
    set((s) => ({
      projects: [...s.projects, project],
      activeProject: project,
      activeFlowId: project.flows[0]?.id ?? null,
    }))
  },

  saveProject: async () => {
    const { activeProject } = get()
    if (!activeProject) return
    // Sync latest canvas state into store before saving
    if (_getNodes && _getEdges && get().activeFlowId) {
      const nodes = _getNodes()
      const edges = _getEdges()
      const flowId = get().activeFlowId!
      const flows = activeProject.flows.map((f) =>
        f.id === flowId ? { ...f, nodes, edges } : f
      )
      const updated = { ...activeProject, flows }
      set({ activeProject: updated })
      await api.projects.save(updated.id, { flows: updated.flows, name: updated.name })
      return
    }
    await api.projects.save(activeProject.id, { flows: activeProject.flows, name: activeProject.name })
  },

  deleteProject: async (id, dropPersisted = false) => {
    await api.projects.delete(id, dropPersisted)
    const { activeProject } = get()
    set((s) => ({
      projects: s.projects.filter((p) => p.id !== id),
      ...(activeProject?.id === id ? { activeProject: null, activeFlowId: null } : {}),
    }))
  },

  // ── Flows ──────────────────────────────────────────────────────────────────

  activeFlow: () => {
    const { activeProject, activeFlowId } = get()
    return activeProject?.flows.find((f) => f.id === activeFlowId) ?? null
  },

  setActiveFlow: (id) => set({ activeFlowId: id, selectedNodeId: null, execState: {} }),

  addFlow: () => {
    set((s) => {
      if (!s.activeProject) return {}
      const newFlow: FlowMeta = {
        id: crypto.randomUUID(),
        name: `Flow ${s.activeProject.flows.length + 1}`,
        active: true,
        imports: 'import pandas as pd\nimport sqlalchemy as sa\n',
        nodes: [{
          id: crypto.randomUUID(),
          type: 'trigger',
          position: { x: 80, y: 200 },
          data: { label: 'Start', run_mode: 'manual', cron: '', timezone: 'UTC' },
        }],
        edges: [],
      }
      return {
        activeProject: { ...s.activeProject, flows: [...s.activeProject.flows, newFlow] },
        activeFlowId: newFlow.id,
      }
    })
  },

  // Saved straight away, so the backend stops the flow's schedule and removes
  // its cached frames now (the canvas keeps the store's flows up to date).
  deleteFlow: async (id, dropPersisted = false) => {
    const { activeProject, activeFlowId } = get()
    if (!activeProject) return
    const flows = activeProject.flows.filter((f) => f.id !== id)
    await api.projects.save(activeProject.id, {
      flows, name: activeProject.name, drop_persisted: dropPersisted,
    })
    set({
      activeProject: { ...activeProject, flows },
      activeFlowId: activeFlowId === id ? (flows[0]?.id ?? null) : activeFlowId,
    })
  },

  toggleFlowActive: (id) => {
    set((s) => {
      if (!s.activeProject) return {}
      const flows = s.activeProject.flows.map((f) => f.id === id ? { ...f, active: !f.active } : f)
      return { activeProject: { ...s.activeProject, flows } }
    })
  },

  renameFlow: (flowId, name) => {
    set((s) => {
      if (!s.activeProject) return {}
      const flows = s.activeProject.flows.map((f) => f.id === flowId ? { ...f, name } : f)
      return { activeProject: { ...s.activeProject, flows } }
    })
  },

  // ── Canvas mutations ────────────────────────────────────────────────────────

  updateFlowCanvas: (flowId, nodes, edges) => {
    set((s) => {
      if (!s.activeProject) return {}
      const flows = s.activeProject.flows.map((f) => f.id === flowId ? { ...f, nodes, edges } : f)
      return { activeProject: { ...s.activeProject, flows } }
    })
  },

  updateFlowImports: (flowId, imports) => {
    set((s) => {
      if (!s.activeProject) return {}
      const flows = s.activeProject.flows.map((f) => f.id === flowId ? { ...f, imports } : f)
      return { activeProject: { ...s.activeProject, flows } }
    })
  },

  updateFlowShared: (flowId: string, shared_functions: string) => {
    set((s) => {
      if (!s.activeProject) return {}
      const flows = s.activeProject.flows.map((f) => f.id === flowId ? { ...f, shared_functions } : f)
      return { activeProject: { ...s.activeProject, flows } }
    })
  },

  updateFlowLibrary: async (flowId, v) => {
    set((s) => {
      if (!s.activeProject) return {}
      const flows = s.activeProject.flows.map((f) => f.id === flowId ? { ...f, ...v } : f)
      return { activeProject: { ...s.activeProject, flows } }
    })
    await get().saveProject()
  },

  applyBuiltFlow: (flowId, built) => {
    const byId: Record<string, { label: string; code: string }> = {}
    built.forEach((b) => { byId[b.node_id] = b })
    const convert = (n: any) =>
      byId[n.id]
        ? { ...n, type: 'processor',
            data: { ...n.data, label: byId[n.id].label, code: byId[n.id].code, engine: 'pandas' } }
        : n
    // Update the canvas immediately
    _setNodes?.((prev) => prev.map(convert))
    // And the store (source of truth)
    set((s) => {
      if (!s.activeProject) return {}
      const flows = s.activeProject.flows.map((f) => {
        if (f.id !== flowId) return f
        return { ...f, nodes: (f.nodes as any[]).map(convert) }
      })
      return { activeProject: { ...s.activeProject, flows } }
    })
  },

  updateNodeData: (flowId, nodeId, data) => {
    // 1. Push into React Flow canvas (immediate visual update on node card)
    _setNodes?.((prev) =>
      prev.map((n) => n.id === nodeId ? { ...n, data: { ...n.data, ...data } } : n)
    )
    // 2. Update Zustand store (source of truth for persistence & run)
    set((s) => {
      if (!s.activeProject) return {}
      const flows = s.activeProject.flows.map((f) => {
        if (f.id !== flowId) return f
        const nodes = (f.nodes as any[]).map((n) =>
          n.id === nodeId ? { ...n, data: { ...n.data, ...data } } : n
        )
        return { ...f, nodes }
      })
      return { activeProject: { ...s.activeProject, flows } }
    })
  },

  addNodeToCanvas: (flowId, node) => {
    const n = node as any
    // Push into React Flow
    _setNodes?.((prev) => [...prev, n])
    // Update store
    set((s) => {
      if (!s.activeProject) return {}
      const flows = s.activeProject.flows.map((f) =>
        f.id === flowId ? { ...f, nodes: [...(f.nodes as any[]), n] } : f
      )
      return { activeProject: { ...s.activeProject, flows } }
    })
  },

  addEdgesToCanvas: (flowId, newEdges) => {
    _setEdges?.((prev) => {
      // Remove any edges that are being replaced (same source/target)
      const filtered = prev.filter((e: any) =>
        !newEdges.some((ne: any) =>
          (ne.source === e.source && ne.target === e.target) || ne.id === e.id
        )
      )
      return [...filtered, ...(newEdges as any[])]
    })
    set((s) => {
      if (!s.activeProject) return {}
      const flows = s.activeProject.flows.map((f) => {
        if (f.id !== flowId) return f
        const existing = (f.edges as any[]).filter((e: any) =>
          !newEdges.some((ne: any) =>
            (ne.source === e.source && ne.target === e.target) || ne.id === e.id
          )
        )
        return { ...f, edges: [...existing, ...newEdges] }
      })
      return { activeProject: { ...s.activeProject, flows } }
    })
  },

  removeNodesFromCanvas: (flowId, nodeIds) => {
    _setNodes?.((prev) => prev.filter((n: any) => !nodeIds.includes(n.id)))
    _setEdges?.((prev) => prev.filter((e: any) => !nodeIds.includes(e.source) && !nodeIds.includes(e.target)))
    set((s) => {
      if (!s.activeProject) return {}
      const flows = s.activeProject.flows.map((f) => {
        if (f.id !== flowId) return f
        return {
          ...f,
          nodes: (f.nodes as any[]).filter((n: any) => !nodeIds.includes(n.id)),
          edges: (f.edges as any[]).filter((e: any) => !nodeIds.includes(e.source) && !nodeIds.includes(e.target)),
        }
      })
      return { activeProject: { ...s.activeProject, flows } }
    })
  },

  duplicateNode: (nodeId) => {
    const { activeProject, activeFlowId } = get()
    if (!activeProject || !activeFlowId) return
    const nodes = _getNodes?.() ?? []
    const source = nodes.find((n: any) => n.id === nodeId)
    if (!source) return
    const clone = {
      ...source,
      id: crypto.randomUUID(),
      position: { x: source.position.x + 40, y: source.position.y + 40 },
      data: { ...source.data, label: (source.data.label ?? '') + ' (copy)' },
      selected: false,
    }
    get().addNodeToCanvas(activeFlowId, clone)
  },

  addStopAfterNode: (sourceNodeId: string, mode: 'hard' | 'tap') => {
    const { activeProject, activeFlowId } = get()
    if (!activeProject || !activeFlowId) return

    const currentNodes = _getNodes?.() ?? []
    const currentEdges = _getEdges?.() ?? []
    const sourceNode = currentNodes.find((n: any) => n.id === sourceNodeId)
    if (!sourceNode) return

    const newNodeId = crypto.randomUUID()
    const newNode = {
      id: newNodeId,
      type: 'stop',
      position: { x: sourceNode.position.x + 300, y: sourceNode.position.y },
      data: { label: mode === 'hard' ? 'Stop' : 'Tap', mode },
    }

    // Rewire: source→existing-targets becomes source→stop→existing-targets
    const outgoing = currentEdges.filter((e: any) => e.source === sourceNodeId)
    const others   = currentEdges.filter((e: any) => e.source !== sourceNodeId)
    const newEdges = [
      ...others,
      { id: crypto.randomUUID(), source: sourceNodeId, target: newNodeId, type: 'default' },
      ...outgoing.map((e: any) => ({ ...e, id: crypto.randomUUID(), source: newNodeId })),
    ]

    // Update RF canvas
    _setNodes?.((prev: any[]) => [...prev, newNode])
    _setEdges?.(() => newEdges)

    // Update store
    set((s) => {
      if (!s.activeProject) return {}
      const flows = s.activeProject.flows.map((f) => {
        if (f.id !== activeFlowId) return f
        return { ...f, nodes: [...(f.nodes as any[]), newNode], edges: newEdges }
      })
      return { activeProject: { ...s.activeProject, flows } }
    })
  },

  // ── Metadata ───────────────────────────────────────────────────────────────

  loadConnections: async () => set({ connections: await api.connections.list() }),
  loadVariables: async () => set({ variables: await api.metadata.listVars() }),

  // ── Execution ──────────────────────────────────────────────────────────────

  runFlow: async (startFromNode, resumeFromNode) => {
    const { activeProject, activeFlowId } = get()
    if (!activeProject || !activeFlowId) return

    // Sync latest canvas state from RF before running
    // This ensures node data changes (mode, code) are captured
    let liveFlow = activeProject.flows.find((f) => f.id === activeFlowId)
    if (!liveFlow) return

    if (_getNodes && _getEdges) {
      const currentNodes = _getNodes()
      const currentEdges = _getEdges()
      liveFlow = { ...liveFlow, nodes: currentNodes, edges: currentEdges }
    }

    set({ running: true, runError: null, execState: {}, sidebarTab: 'output', sidebarVisible: true })
    try {
      await api.execute.run(activeProject.id, activeFlowId, liveFlow, startFromNode, resumeFromNode)
    } catch (e: any) {
      // Previously only logged to the console, so a failed start looked like
      // "nothing happened". Surface the backend's reason instead.
      console.error('Run flow error:', e)
      let msg = String(e?.message ?? e)
      const m = msg.match(/^API (\d+): (.*)$/s)
      if (m) {
        try { msg = JSON.parse(m[2]).detail ?? m[2] } catch { msg = m[2] }
        msg = `Run failed to start (HTTP ${m[1]}): ${msg}`
      } else if (/Failed to fetch|NetworkError/i.test(msg)) {
        msg = 'Run failed to start: the backend is unreachable. Is the backend container running?'
      }
      set({ running: false, runError: msg })
    }
  },

  handleWsEvent: (event) => {
    switch (event.type) {
      case 'node_start':
        set((s) => ({ execState: { ...s.execState, [event.node_id]: { status: 'running' } } }))
        break
      case 'node_complete':
        set((s) => ({
          execState: {
            ...s.execState,
            [event.node_id]: {
              status: 'ok',
              rows_in: event.rows_in,
              rows_out: event.rows_out,
              duration_ms: event.duration_ms,
              ...(event.reads ? { reads: event.reads } : {}),
            },
          },
        }))
        break
      case 'node_error':
        set((s) => ({
          execState: {
            ...s.execState,
            [event.node_id]: { status: 'error', detail: event.detail, duration_ms: event.duration_ms },
          },
        }))
        break
      case 'node_routed':
        set((s) => ({
          execState: {
            ...s.execState,
            [event.node_id]: {
              status: 'ok',
              rows_out: event.row_count,
              duration_ms: event.duration_ms,
              chosen: event.chosen,
            },
          },
        }))
        break
      case 'node_stopped':
        set((s) => ({
          execState: {
            ...s.execState,
            [event.node_id]: {
              status: event.mode === 'hard' ? 'stopped' : 'ok',
              rows_out: event.row_count,
              duration_ms: event.duration_ms,
              table_schema: event.schema,
              table_rows: event.rows,
              table_total: event.total,
            },
          },
        }))
        break
      case 'chart_ready':
        set((s) => ({
          execState: {
            ...s.execState,
            [event.node_id]: {
              status: 'ok',
              duration_ms: event.duration_ms,
              chart_type: event.chart_type,
              chart_payload: event.payload,
            },
          },
        }))
        break
      case 'table_ready':
        set((s) => ({
          execState: {
            ...s.execState,
            [event.node_id]: {
              status: 'ok',
              duration_ms: event.duration_ms,
              table_schema: event.schema,
              table_rows: event.rows,
              table_total: event.total,
            },
          },
        }))
        break
      case 'explore_ready' as any:
        set((s) => ({
          execState: {
            ...s.execState,
            [(event as any).node_id]: {
              status: 'ok',
              rows_in: (event as any).rows, rows_out: (event as any).rows,
              duration_ms: (event as any).duration_ms,
            },
          },
        }))
        break
      case 'report_ready' as any:
        set((s) => ({
          execState: {
            ...s.execState,
            [(event as any).node_id]: {
              status: 'ok',
              rows_in: (event as any).rows, rows_out: (event as any).rows,
              duration_ms: (event as any).duration_ms,
              report_name: (event as any).report,
              report_kind: (event as any).kind,
              report_url: (event as any).url,
            },
          },
        }))
        break
      case 'node_persisted' as any: {
        const ev = event as any
        // Record the persist timestamp on the node so the UI can offer Resume
        const { activeFlowId } = get()
        if (activeFlowId) {
          const flow = get().activeProject?.flows.find((f) => f.id === activeFlowId)
          const node = (flow?.nodes as any[])?.find((n) => n.id === ev.node_id)
          const prev = node?.data?.persist ?? { enabled: true, target: ev.target, table_name: ev.table }
          get().updateNodeData(activeFlowId, ev.node_id, {
            persist: { ...prev, table_name: ev.table, target: ev.target, last_persisted: ev.ts },
          })
        }
        break
      }
      case 'flow_complete':
        set({ running: false })
        break
      case 'node_blocked' as any:
        set((s) => ({
          execState: {
            ...s.execState,
            [(event as any).node_id]: { status: 'blocked' as any },
          },
        }))
        break
    }
  },

  clearExecState: () => set({ execState: {}, running: false }),

  setSelectedNode: (id) => set((s) => {
    // If the clicked node has execution output, pin it in the Output tab too.
    // This lets you click any Processor/Table/Chart/Stop to see its output,
    // in addition to selecting from the log.
    const hasOutput = id ? !!s.execState[id] : false
    return {
      selectedNodeId: id,
      sidebarTab: id && s.sidebarTab !== 'output' ? 'properties' : s.sidebarTab,
      sidebarVisible: id ? true : s.sidebarVisible,
      // Pin output if the node has state; otherwise leave current pin
      pinnedOutputNodeId: hasOutput ? id : s.pinnedOutputNodeId,
    }
  }),

  setSidebarTab: (tab) => set({ sidebarTab: tab }),
  setSidebarVisible: (v) => set({ sidebarVisible: v }),
  setSidebarWidth: (w) => set({ sidebarWidth: Math.max(280, Math.min(1200, w)) }),

  inspectNode: (nodeId) => set({
    pinnedOutputNodeId: nodeId,
    sidebarTab: 'output',
    sidebarVisible: true,
  }),
}))
