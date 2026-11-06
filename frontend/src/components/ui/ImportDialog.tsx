/**
 * Import a .sfs.json export: as a new project, or as flows of the open one.
 * Missing connections are mapped here, not fatal; missing plugin engines are
 * listed; schedules always come in turned off.
 */
import { useEffect, useState } from 'react'
import { AlertTriangle, Check } from 'lucide-react'
import { api, type FlowBundle, type ImportPreview } from '@/utils/api'
import type { Project } from '@/types'
import { Modal, errText } from '@/components/library/parts'

export function ImportDialog({ bundle, projectId, projectName, onClose, onDone }: {
  bundle: FlowBundle
  /** Import as flows of this project; otherwise as a new project. */
  projectId?: string
  projectName?: string
  onClose: () => void
  onDone: (p: Project) => void
}) {
  const [pre, setPre] = useState<ImportPreview | null>(null)
  const [map, setMap] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.projects.importPreview(bundle).then((p) => {
      setPre(p)
      // A local connection with the same name in another case is the likely match
      const guess: Record<string, string> = {}
      for (const m of p.missing_connections) {
        const hit = p.local_connections.find((c) => c.toLowerCase() === m.toLowerCase())
        if (hit) guess[m] = hit
      }
      setMap(guess)
    }).catch((e) => setError(errText(e)))
  }, [bundle])

  const run = async () => {
    setBusy(true)
    try { onDone(await api.projects.importBundle(bundle, map, projectId)) }
    catch (e) { setError(errText(e)); setBusy(false) }
  }

  const title = projectId ? `Import flows into ${projectName ?? 'this project'}` : 'Import as a new project'
  return (
    <Modal title={title} onClose={onClose}>
      <div className="space-y-4 text-xs text-slate-300">
        {error && <div className="rounded-lg border border-red-500/30 bg-red-950/30 p-2.5 text-red-200">{error}</div>}
        {!pre && !error && <div className="text-muted">Reading the file…</div>}
        {pre && (<>
          <div>
            <div className="text-sm text-slate-100">{pre.name || 'Untitled'}</div>
            <div className="text-subtle">
              {pre.kind === 'flow' ? 'One flow' : `${pre.flows.length} flow${pre.flows.length === 1 ? '' : 's'}`}
              {pre.sfs_version && <> · exported from SFS {pre.sfs_version}</>}
            </div>
            {pre.kind === 'project' && pre.flows.length > 1 && <div className="text-muted mt-1">{pre.flows.join(', ')}</div>}
          </div>

          {pre.connections.length > 0 && (
            <div className="space-y-1.5">
              <div className="label">Connections</div>
              {pre.connections.map((c) => {
                const missing = pre.missing_connections.includes(c)
                return (
                  <div key={c} className="flex items-center gap-2">
                    <code className="w-40 truncate text-slate-200" title={c}>{c}</code>
                    {missing ? (
                      <select className="input text-xs flex-1 py-1" value={map[c] ?? ''}
                              onChange={(e) => setMap({ ...map, [c]: e.target.value })}>
                        <option value="">Leave unresolved</option>
                        {pre.local_connections.map((l) => <option key={l} value={l}>Use {l}</option>)}
                      </select>
                    ) : <span className="text-emerald-300 flex items-center gap-1"><Check size={11} /> found here</span>}
                  </div>
                )
              })}
              {pre.missing_connections.some((c) => !map[c]) && (
                <div className="text-subtle">Nodes using an unresolved connection show “connection not found” until
                  you add one with that name in Meta → Connections, or edit the nodes.</div>
              )}
            </div>
          )}

          {pre.missing_engines.length > 0 && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-950/20 p-2.5 text-amber-100/90 flex gap-2">
              <AlertTriangle size={13} className="shrink-0 mt-0.5" />
              <div>Engines not installed here:{' '}
                {pre.missing_engines.map((e) => `${e.name}${e.plugin ? ` (plugin ${e.plugin}${e.version ? ` ${e.version}` : ''})` : ''}`).join(', ')}.
                {' '}Their nodes keep the engine and run on pandas until the plugin is installed.</div>
            </div>
          )}

          {pre.custom_processors.length > 0 && (
            <div className="text-muted">Custom processors in the file:{' '}
              {pre.custom_processors.map((c) => `${c.name}${c.here ? ' (already here)' : ''}`).join(', ')}.
              {pre.custom_processors.some((c) => !c.here) && ' New ones are added to your toolbar.'}</div>
          )}

          <div className="text-subtle">Imported flows get new ids and come in turned off, so no schedule starts until you turn it on.</div>

          <div className="flex justify-end gap-2 pt-1">
            <button className="btn-ghost" onClick={onClose}>Cancel</button>
            <button className="btn-pri" disabled={busy} onClick={run}>{busy ? 'Importing…' : 'Import'}</button>
          </div>
        </>)}
      </div>
    </Modal>
  )
}
