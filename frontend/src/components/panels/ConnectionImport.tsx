/**
 * Import connections (and variables) from an sfs-connections export. Names
 * that exist here are skipped, overwritten (keeping the stored password and
 * secret options) or renamed. Imported connections wait for their credentials.
 */
import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { api, type ConnectionBundle, type ConnectionImportPreview, type ConnectionImportResult } from '@/utils/api'
import { Modal, errText } from '@/components/library/parts'

type Choice = 'skip' | 'overwrite' | 'rename'

export function ConnectionImport({ bundle, onClose, onDone }: {
  bundle: ConnectionBundle; onClose: () => void; onDone: () => void
}) {
  const [pre, setPre] = useState<ConnectionImportPreview | null>(null)
  const [choice, setChoice] = useState<Record<string, Choice>>({})
  const [renameTo, setRenameTo] = useState<Record<string, string>>({})
  const [vars, setVars] = useState(true)
  const [result, setResult] = useState<ConnectionImportResult | null>(null)
  const [error, setError] = useState('')

  useEffect(() => { api.connections.importPreview(bundle).then(setPre).catch((e) => setError(errText(e))) }, [bundle])

  const run = async () => {
    const on_conflict: Record<string, string> = {}
    for (const c of pre?.connections ?? []) {
      if (!c.exists) continue
      const ch = choice[c.name] ?? 'skip'
      on_conflict[c.name] = ch === 'rename' ? `rename:${renameTo[c.name] || `${c.name}_imported`}` : ch
    }
    try { setResult(await api.connections.importAll(bundle, on_conflict, vars)); onDone() }
    catch (e) { setError(errText(e)) }
  }

  const needs = (r: { password?: boolean; extras?: string[] }) =>
    [r.password ? 'password' : '', ...(r.extras ?? [])].filter(Boolean).join(', ')

  return (
    <Modal title="Import connections" onClose={onClose}>
      <div className="space-y-3 text-xs text-slate-300">
        {error && <div className="rounded-lg border border-red-500/30 bg-red-950/30 p-2.5 text-red-200">{error}</div>}
        {result ? (<>
          <div className="space-y-1">
            {result.created.length > 0 && <div>Added: {result.created.join(', ')}</div>}
            {Object.keys(result.renamed).length > 0 && <div>Added under new names: {Object.entries(result.renamed).map(([a, b]) => `${a} → ${b}`).join(', ')}</div>}
            {result.overwritten.length > 0 && <div>Overwritten (passwords kept): {result.overwritten.join(', ')}</div>}
            {result.skipped.length > 0 && <div className="text-muted">Skipped: {result.skipped.join(', ')}</div>}
            {vars && <div className="text-muted">Variables: {result.variables_added} added, {result.variables_skipped} already here.</div>}
          </div>
          <div className="text-subtle">Connections marked <em>needs credentials</em> work once you edit them and enter the password or secret options.</div>
          <div className="flex justify-end"><button className="btn-pri" onClick={onClose}>Done</button></div>
        </>) : pre && (<>
          <div className="space-y-1.5">
            {pre.connections.map((c) => (
              <div key={c.name} className="flex items-center gap-2">
                <code className="w-36 truncate text-slate-200" title={c.name}>{c.name}</code>
                <span className="text-subtle w-20 truncate">{c.dialect}</span>
                {c.exists ? (
                  <div className="flex-1 flex gap-1.5">
                    <select className="input text-xs py-1 w-28" value={choice[c.name] ?? 'skip'}
                            onChange={(e) => setChoice({ ...choice, [c.name]: e.target.value as Choice })}>
                      <option value="skip">Skip</option>
                      <option value="overwrite">Overwrite</option>
                      <option value="rename">Rename</option>
                    </select>
                    {choice[c.name] === 'rename' && (
                      <input className="input text-xs py-1 flex-1" placeholder={`${c.name}_imported`}
                             value={renameTo[c.name] ?? ''} onChange={(e) => setRenameTo({ ...renameTo, [c.name]: e.target.value })} />
                    )}
                    {choice[c.name] === 'overwrite' && <span className="text-subtle self-center">keeps the password here</span>}
                  </div>
                ) : <span className="flex-1 text-muted">{needs(c.requires) ? `new · will need ${needs(c.requires)}` : 'new'}</span>}
              </div>
            ))}
          </div>
          {pre.variables > 0 && (
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={vars} onChange={(e) => setVars(e.target.checked)} className="mt-0.5" />
              <span>Also import {pre.variables} variable{pre.variables === 1 ? '' : 's'}
                {pre.variables_existing.length > 0 && <> ({pre.variables_existing.length} already here are kept as they are)</>}</span>
            </label>
          )}
          <div className="text-subtle flex gap-1.5"><AlertTriangle size={11} className="shrink-0 mt-0.5" />
            The file holds no passwords or secret options. Variables are plain text and must not hold secrets.</div>
          <div className="flex justify-end gap-2">
            <button className="btn-ghost" onClick={onClose}>Cancel</button>
            <button className="btn-pri" onClick={run}>Import</button>
          </div>
        </>)}
      </div>
    </Modal>
  )
}
