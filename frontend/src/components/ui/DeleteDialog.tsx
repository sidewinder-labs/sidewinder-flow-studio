/**
 * Confirmation for deleting a project or a flow. Asks the backend what the
 * delete would affect (scheduled flows, persisted tables, a run in progress)
 * and offers to drop the persisted tables too. A project's name must be
 * typed to confirm.
 */
import { useEffect, useState } from 'react'
import { Trash2, X, AlertTriangle, Clock } from 'lucide-react'
import { api, type DeletePreview } from '@/utils/api'

interface Props {
  kind: 'project' | 'flow'
  projectId: string
  flowId?: string
  name: string
  onConfirm: (dropPersisted: boolean) => Promise<void>
  onClose: () => void
}

export function DeleteDialog({ kind, projectId, flowId, name, onConfirm, onClose }: Props) {
  const [preview, setPreview] = useState<DeletePreview | null>(null)
  const [typed, setTyped] = useState('')
  const [dropTables, setDropTables] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.projects.deletePreview(projectId, flowId).then(setPreview).catch((e) => setError(String(e?.message ?? e)))
  }, [projectId, flowId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && !busy && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, busy])

  const scheduled = preview?.flows.filter((f) => f.scheduled) ?? []
  const tables = preview?.persisted_tables ?? []
  const nameOk = kind === 'flow' || typed.trim() === name.trim()
  const canDelete = !!preview && !preview.running && nameOk && !busy

  const confirm = async () => {
    setBusy(true)
    setError('')
    try {
      await onConfirm(dropTables && tables.length > 0)
      onClose()
    } catch (e: any) {
      // The backend refuses while a flow runs: show its message, not the status code
      const msg = String(e?.message ?? e)
      const detail = msg.match(/"detail":"([^"]+)"/)?.[1]
      setError(detail ?? msg)
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[9998] bg-black/60 flex items-center justify-center p-4"
         onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="w-full max-w-md bg-surface-raised border border-canvas-border rounded-2xl shadow-node p-6 space-y-4">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-xl bg-red-900/40 flex items-center justify-center shrink-0">
            <Trash2 size={16} className="text-red-400" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-semibold text-slate-100">Delete {kind}</h2>
            <div className="text-sm text-slate-300 truncate" title={name}>{name}</div>
          </div>
          <button className="text-subtle hover:text-muted" onClick={onClose} disabled={busy}><X size={16} /></button>
        </div>

        <ul className="text-xs text-muted space-y-1.5 list-disc pl-4">
          {kind === 'project' ? (
            <li>The project moves to the <span className="text-slate-300">Trash</span> (in the project picker), where
              you can restore it. Restored flows come back turned off.</li>
          ) : (
            <li>The flow is removed from the project and the project is saved.</li>
          )}
          <li>Cached node outputs are deleted; running the flow again recreates them.</li>
          <li>Run history is kept.</li>
        </ul>

        {!preview && !error && <div className="text-xs text-subtle">Checking what this affects…</div>}

        {preview?.running && (
          <div className="flex gap-2 rounded-lg border border-red-500/30 bg-red-950/30 p-2.5 text-xs text-red-200">
            <AlertTriangle size={14} className="shrink-0 mt-0.5 text-red-400" />
            <span>{kind === 'project' ? 'A flow of this project' : 'This flow'} is running. Stop it, or wait for it
              to finish, then delete.</span>
          </div>
        )}

        {scheduled.length > 0 && (
          <div className="flex gap-2 rounded-lg border border-amber-500/30 bg-amber-950/20 p-2.5 text-xs text-amber-200/90">
            <Clock size={14} className="shrink-0 mt-0.5 text-amber-400" />
            <span>
              {kind === 'flow' ? 'This flow is scheduled; its schedule stops.'
                : `Scheduled flows stop: ${scheduled.map((f) => f.name).join(', ')}.`}
            </span>
          </div>
        )}

        {tables.length > 0 && (
          <label className="flex gap-2 items-start text-xs text-slate-300 cursor-pointer">
            <input type="checkbox" className="mt-0.5" checked={dropTables} onChange={(e) => setDropTables(e.target.checked)} />
            <span>
              Also delete persisted tables:{' '}
              <span className="font-mono text-slate-200">{tables.join(', ')}</span>
              <span className="block text-subtle mt-0.5">Off by default. Deleted tables can't be restored.</span>
            </span>
          </label>
        )}

        {kind === 'project' && (
          <div className="space-y-1">
            <label className="text-xs text-muted">Type the project name to confirm</label>
            <input className="input text-sm" value={typed} autoFocus placeholder={name}
                   onChange={(e) => setTyped(e.target.value)}
                   onKeyDown={(e) => e.key === 'Enter' && canDelete && confirm()} />
          </div>
        )}

        {error && <div className="text-xs text-red-300 whitespace-pre-wrap">{error}</div>}

        <div className="flex justify-end gap-2 pt-1">
          <button className="btn-ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn-danger gap-1.5" onClick={confirm} disabled={!canDelete}
                  autoFocus={kind === 'flow'}>
            <Trash2 size={12} /> {busy ? 'Deleting…' : `Delete ${kind}`}
          </button>
        </div>
      </div>
    </div>
  )
}
