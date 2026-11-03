import { useState, useRef, useEffect } from 'react'
import { Play, Save, Plus, FolderOpen, ChevronDown, Square, RefreshCw, Compass, BookOpen, Trash2, RotateCcw, ArrowLeft } from 'lucide-react'
import { useStore } from '@/store'
import { api, type TrashedProject } from '@/utils/api'
import { AboutDialog } from './AboutDialog'
import { DeleteDialog } from './DeleteDialog'

export function TopBar() {
  const {
    projects, activeProject, activeFlowId, running,
    loadProjects, openProject, closeProject, createProject, deleteProject,
    saveProject, runFlow, clearExecState,
    addFlow, deleteFlow, toggleFlowActive, setActiveFlow,
    renameFlow, activeFlow,
    runError, clearRunError,
    view, openExplore, closeExplore,
  } = useStore()

  const [showProjects, setShowProjects] = useState(false)
  const [showAbout, setShowAbout] = useState(false)
  const [newName, setNewName] = useState('')
  // Per-flow inline rename state: flowId → editing name
  const [editingFlowId, setEditingFlowId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState('')
  const renameInputRef = useRef<HTMLInputElement>(null)

  const flow = activeFlow()

  // Delete confirmation (project or flow), and the Trash view of the picker
  const [deleting, setDeleting] = useState<
    { kind: 'project' | 'flow'; projectId: string; flowId?: string; name: string } | null>(null)
  const [trash, setTrash] = useState<TrashedProject[] | null>(null)
  const [purging, setPurging] = useState<string | null>(null)
  const openTrash = () => api.projects.trash().then(setTrash).catch(() => setTrash([]))
  const restore = async (id: string) => {
    await api.projects.restore(id)
    await Promise.all([loadProjects(), openTrash()])
  }
  const purge = async (id: string) => {
    if (purging !== id) { setPurging(id); return }          // second click confirms
    setPurging(null)
    await api.projects.purge(id)
    await openTrash()
  }

  const handleCreate = async () => {
    if (!newName.trim()) return
    await createProject(newName.trim())
    setNewName('')
    setShowProjects(false)
  }

  // Example flows shipped by plugins (from /api/capabilities)
  const [examples, setExamples] = useState<{ name: string; description: string }[]>([])
  const handleOpen = async () => {
    await loadProjects()
    setTrash(null)
    setShowProjects(true)
    api.capabilities().then((c) => setExamples(c?.examples ?? [])).catch(() => {})
  }
  const handleExample = async (name: string) => {
    const project = await api.projects.fromExample(name)
    await loadProjects()
    await openProject(project.id)
    setShowProjects(false)
  }

  const startRename = (e: React.MouseEvent, flowId: string, currentName: string) => {
    e.stopPropagation()
    setEditingFlowId(flowId)
    setEditingName(currentName)
    setTimeout(() => renameInputRef.current?.select(), 50)
  }

  const commitRename = () => {
    if (editingFlowId && editingName.trim()) {
      renameFlow(editingFlowId, editingName.trim())
    }
    setEditingFlowId(null)
  }

  // Close project dropdown when clicking outside
  useEffect(() => {
    if (!showProjects) return
    const handler = (e: MouseEvent) => {
      if (!(e.target as Element).closest('.project-picker')) {
        setShowProjects(false)
      }
    }
    window.addEventListener('mousedown', handler)
    return () => window.removeEventListener('mousedown', handler)
  }, [showProjects])

  return (
    <>
    {runError && (
      <div role="alert"
        className="flex items-start gap-3 px-4 py-2 bg-red-950/70 border-b border-red-500/40 text-red-200 text-xs z-30 shrink-0">
        <span className="font-semibold shrink-0">⚠</span>
        <span className="flex-1 whitespace-pre-wrap break-words">{runError}</span>
        <button onClick={clearRunError} className="shrink-0 text-red-300 hover:text-white" title="Dismiss">✕</button>
      </div>
    )}
    <header className="flex items-center gap-2 px-4 py-2 bg-surface border-b border-canvas-border z-20 shrink-0">
      {/* Brand — click for About */}
      <div
        className="flex items-center gap-2 mr-3 shrink-0 cursor-pointer group"
        onClick={() => setShowAbout(true)}
        title="About Sidewinder Flow Studio"
      >
        <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-accent to-blue-600 flex items-center justify-center group-hover:brightness-110 transition-all">
          <span className="text-canvas-bg text-xs font-bold">FS</span>
        </div>
        <span className="text-sm font-semibold text-slate-200 hidden sm:block group-hover:text-accent transition-colors">
          Sidewinder Flow Studio
        </span>
      </div>

      {showAbout && <AboutDialog onClose={() => setShowAbout(false)} />}
      {deleting && (
        <DeleteDialog
          {...deleting}
          onClose={() => setDeleting(null)}
          onConfirm={async (dropPersisted) => {
            if (deleting.kind === 'project') await deleteProject(deleting.projectId, dropPersisted)
            else await deleteFlow(deleting.flowId!, dropPersisted)
          }}
        />
      )}

      {/* Project picker */}
      <div className="relative shrink-0 project-picker">
        <button
          className="btn-ghost border border-canvas-border gap-1.5 max-w-44"
          onClick={handleOpen}
        >
          <FolderOpen size={13} />
          <span className="truncate text-xs">{activeProject?.name ?? 'Open Project'}</span>
          <ChevronDown size={11} />
        </button>

        {showProjects && (
          <div className="absolute top-full left-0 mt-1 w-72 bg-surface-raised border border-canvas-border rounded-xl shadow-node z-50 overflow-hidden">
            <div className="p-2 border-b border-canvas-border">
              <div className="flex gap-1">
                <input
                  className="input flex-1 text-xs py-1"
                  placeholder="New project name…"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                  autoFocus
                />
                <button className="btn-pri px-2 py-1" onClick={handleCreate}>
                  <Plus size={12} />
                </button>
              </div>
            </div>
            {trash !== null ? (
              <div>
                <div className="flex items-center gap-2 px-3 py-2 border-b border-canvas-border">
                  <button className="text-subtle hover:text-muted" title="Back to projects" onClick={() => setTrash(null)}>
                    <ArrowLeft size={13} />
                  </button>
                  <span className="text-xs text-subtle uppercase tracking-wider flex-1">Trash</span>
                </div>
                <div className="max-h-72 overflow-y-auto">
                  {trash.length === 0 ? (
                    <div className="p-3 text-xs text-muted italic">The trash is empty</div>
                  ) : trash.map((t) => (
                    <div key={t.id} className="flex items-center gap-2 px-3 py-2 text-sm text-slate-300">
                      <div className="flex-1 min-w-0">
                        <div className="truncate">{t.name}</div>
                        <div className="text-xs text-subtle">
                          deleted {t.deleted_at ? new Date(t.deleted_at).toLocaleString() : ''}
                        </div>
                      </div>
                      <button className="text-subtle hover:text-accent" title="Restore (flows come back turned off)"
                              onClick={() => restore(t.id)}>
                        <RotateCcw size={13} />
                      </button>
                      <button
                        className={purging === t.id ? 'text-xs text-red-400 font-medium' : 'text-subtle hover:text-red-400'}
                        title="Delete permanently"
                        onClick={() => purge(t.id)}
                        onBlur={() => setPurging(null)}
                      >
                        {purging === t.id ? 'Delete forever?' : <Trash2 size={13} />}
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ) : (<>
            <div className="max-h-56 overflow-y-auto">
              {projects.length === 0 ? (
                <div className="p-3 text-xs text-muted italic">No projects yet</div>
              ) : (
                projects.map((p) => (
                  <div
                    key={p.id}
                    className={`group flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-surface-overlay text-sm
                      ${activeProject?.id === p.id ? 'text-accent' : 'text-slate-300'}`}
                    onClick={() => { openProject(p.id); setShowProjects(false) }}
                  >
                    <FolderOpen size={12} className="shrink-0" />
                    <span className="flex-1 truncate">{p.name}</span>
                    <span className="text-xs text-subtle group-hover:hidden">{p.updated_at?.slice(0, 10)}</span>
                    <button
                      className="hidden group-hover:block text-subtle hover:text-red-400"
                      title="Delete project"
                      onClick={(e) => {
                        e.stopPropagation()
                        setShowProjects(false)
                        setDeleting({ kind: 'project', projectId: p.id, name: p.name })
                      }}
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                ))
              )}
            </div>
            {examples.length > 0 && (
              <div className="border-t border-canvas-border">
                <div className="px-3 pt-2 pb-1 text-xs text-subtle uppercase tracking-wider">Start from an example</div>
                <div className="max-h-40 overflow-y-auto pb-1">
                  {examples.map((ex) => (
                    <div
                      key={ex.name}
                      className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-surface-overlay text-xs text-slate-300"
                      title={ex.description}
                      onClick={() => handleExample(ex.name)}
                    >
                      <BookOpen size={12} className="shrink-0 text-violet-400" />
                      <span className="flex-1 truncate">{ex.name}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="border-t border-canvas-border p-2 flex gap-1">
              {activeProject && (
                <button
                  className="btn-ghost flex-1 text-xs text-muted justify-center"
                  onClick={() => { closeProject(); setShowProjects(false) }}
                >
                  Close project
                </button>
              )}
              <button className="btn-ghost flex-1 text-xs text-muted justify-center gap-1.5" onClick={openTrash}>
                <Trash2 size={11} /> Trash
              </button>
            </div>
            </>)}
          </div>
        )}
      </div>

      {/* Flow tabs */}
      {activeProject && (
        <div className="flex items-center gap-1 flex-1 overflow-x-auto min-w-0">
          {activeProject.flows.map((f) => (
            <div
              key={f.id}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs cursor-pointer transition-colors shrink-0 group
                ${activeFlowId === f.id
                  ? 'border-accent/50 bg-accent/10 text-accent'
                  : 'border-canvas-border text-muted hover:text-slate-300'}`}
              onClick={() => { setActiveFlow(f.id); closeExplore() }}
            >
              {/* Active/inactive dot */}
              <div
                className={`w-1.5 h-1.5 rounded-full shrink-0 cursor-pointer
                  ${f.active ? 'bg-status-ok' : 'bg-subtle'}`}
                title={f.active ? 'Active — click to deactivate' : 'Inactive — click to activate'}
                onClick={(e) => { e.stopPropagation(); toggleFlowActive(f.id) }}
              />

              {/* Flow name — double-click to rename */}
              {editingFlowId === f.id ? (
                <input
                  ref={renameInputRef}
                  className="bg-transparent border-b border-accent outline-none text-xs w-24 text-accent"
                  value={editingName}
                  onChange={(e) => setEditingName(e.target.value)}
                  onBlur={commitRename}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitRename()
                    if (e.key === 'Escape') setEditingFlowId(null)
                    e.stopPropagation()
                  }}
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <span
                  className="max-w-24 truncate"
                  title="Double-click to rename"
                  onDoubleClick={(e) => startRename(e, f.id, f.name)}
                >
                  {f.name}
                </span>
              )}

              {f.next_run && (
                <span className="text-xs text-muted hidden lg:block">
                  ⏰ {f.next_run.slice(11, 16)}
                </span>
              )}

              {activeProject.flows.length > 1 && (
                <span
                  className="opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:text-red-400 ml-0.5 leading-none transition-opacity"
                  title="Delete flow"
                  onClick={(e) => {
                    e.stopPropagation()
                    setDeleting({ kind: 'flow', projectId: activeProject.id, flowId: f.id, name: f.name })
                  }}
                >
                  ×
                </span>
              )}
            </div>
          ))}

          <button
            className="btn-ghost px-2 py-1 text-xs shrink-0"
            onClick={addFlow}
            title="Add new flow"
          >
            <Plus size={11} />
          </button>
        </div>
      )}

      {/* Right actions */}
      <div className="flex items-center gap-2 ml-auto shrink-0">
        <button
          className={view === 'explore' ? 'btn-pri gap-1.5' : 'btn-ghost gap-1.5 border border-canvas-border'}
          onClick={() => (view === 'explore' ? closeExplore() : openExplore())}
          title={view === 'explore' ? 'Back to flows' : 'Explore cached node outputs and persisted tables'}
        >
          <Compass size={13} /> <span className="text-xs">Explore</span>
        </button>
        {activeProject && (
          <>
            <button
              className="btn-ghost"
              onClick={saveProject}
              title="Save project (Ctrl+S)"
            >
              <Save size={14} />
            </button>

            {running ? (
              <button
                className="btn-danger flex items-center gap-1.5"
                onClick={clearExecState}
                title="Stop execution"
              >
                <Square size={12} fill="currentColor" /> Stop
              </button>
            ) : (
              <>
                <button
                  className="btn-ghost"
                  onClick={() => { clearExecState(); runFlow() }}
                  disabled={!activeFlowId}
                  title="Clear cache and re-run from start"
                >
                  <RefreshCw size={13} />
                </button>
                <button
                  className="btn-pri gap-1.5"
                  onClick={() => runFlow()}
                  disabled={!activeFlowId}
                >
                  <Play size={13} /> Run Flow
                </button>
              </>
            )}
          </>
        )}
      </div>
    </header>
    </>
  )
}
