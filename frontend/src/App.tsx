import { HelpPanel } from '@/components/help/HelpPanel'
import { Suspense, lazy, useEffect, useState } from 'react'
import { TopBar }     from '@/components/ui/TopBar'
import { Canvas }     from '@/components/canvas/Canvas'
import { NodeToolbar }from '@/components/canvas/NodeToolbar'
import { Sidebar }    from '@/components/panels/Sidebar'
import { useStore }   from '@/store'
import { useFlowWebSocket } from '@/hooks/useFlowWebSocket'
import type { WsEvent } from '@/types'
import { sourceFromHash } from '@/components/explore/source'
import { DesktopWelcome } from '@/components/desktop/DesktopWelcome'
import { useFeatures } from '@/utils/engines'
import { api, type DesktopInfo } from '@/utils/api'

// Graphic Walker is ~4 MB: load it only when Explore is opened.
const ExploreView = lazy(() => import('@/components/explore/ExploreView'))

function FlowCanvas({ flowId }: { flowId: string }) {
  const { handleWsEvent } = useStore()
  useFlowWebSocket(flowId, (e: WsEvent) => handleWsEvent(e))
  return <Canvas key={flowId} flowId={flowId} />
}

function EmptyState() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center p-8">
      <div className="text-5xl select-none">⚡</div>
      <div>
        <div className="text-xl font-semibold text-slate-300 mb-1">No project open</div>
        <div className="text-sm text-muted max-w-sm">
          Create or open a project from the top bar to start building visual Python data flows.
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3 mt-2 text-xs text-muted max-w-md">
        {[
          ['Trigger', 'Schedule or manually start a flow'],
          ['Processor', 'Write Python — df in, df out'],
          ['Stop / Tap', 'Inspect DataFrames mid-flow'],
          ['Table View', 'See your data as a table'],
          ['Chart View', 'Plotly, Matplotlib, ECharts'],
          ['Connections', 'Any SQLAlchemy-compatible DB'],
        ].map(([title, desc]) => (
          <div key={title} className="bg-surface rounded-lg p-2.5 border border-canvas-border text-left">
            <div className="font-medium text-slate-300 mb-0.5">{title}</div>
            <div>{desc}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function App() {
  const { loadProjects, loadConnections, loadVariables, activeProject, activeFlowId, view, openExplore } = useStore()

  useEffect(() => {
    loadProjects()
    loadConnections()
    loadVariables()
    // Deep link: #/explore?kind=frame&project_id=…&node_id=…
    if (window.location.hash.startsWith('#/explore')) openExplore(sourceFromHash(window.location.hash))
  }, [])

  // Desktop edition: the welcome screen until the user has closed it once
  const { desktop } = useFeatures()
  const [welcome, setWelcome] = useState<DesktopInfo | null>(null)
  useEffect(() => {
    if (!desktop) return
    const check = () => api.desktop.info().then((i) => { if (!i.welcome_seen) setWelcome(i) }).catch(() => {})
    check()
    window.addEventListener('sfs:show-welcome', check)
    return () => window.removeEventListener('sfs:show-welcome', check)
  }, [desktop])

  // Ctrl+S to save
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault()
        useStore.getState().saveProject()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden">
      <TopBar />
      <HelpPanel />
      {welcome && <DesktopWelcome info={welcome} onClose={() => setWelcome(null)} />}
      {view === 'explore' && (
        <Suspense fallback={<div className="flex-1 p-6 text-sm text-muted">Loading Explore…</div>}>
          <ExploreView />
        </Suspense>
      )}
      {/* Kept mounted while exploring so the canvas keeps its state. */}
      <div className={`flex flex-1 overflow-hidden ${view === 'explore' ? 'hidden' : ''}`}>
        {activeProject && activeFlowId ? (
          <>
            <div className="flex flex-col flex-1 overflow-hidden min-w-0">
              <NodeToolbar />
              <div className="flex-1 relative">
                <FlowCanvas flowId={activeFlowId} />
              </div>
            </div>
            <Sidebar />
          </>
        ) : (
          <EmptyState />
        )}
      </div>
    </div>
  )
}
