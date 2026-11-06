/**
 * The function library inside a flow's Functions tab: the functions found in
 * the tab (with the state of library copies), Save to library, Insert from
 * library, Update / Compare. Library functions are COPIED into the flow, so
 * the flow never depends on the library; provenance lets updates be pulled in
 * deliberately.
 */
import { useEffect, useMemo, useState } from 'react'
import { BookMarked, Download, GitCompare, Library, Plus, Search, Settings2 } from 'lucide-react'
import { api, type FlowFunction, type LibraryEntry, type LibraryResult } from '@/utils/api'
import { DiffView, Modal, StateBadge, errText } from './parts'
import { LibraryManager } from './LibraryManager'

interface Props {
  functions: string
  imports: string
  provenance: Record<string, any>
  /** Store new Functions/Imports/provenance for the flow (and save). */
  onApply: (r: LibraryResult) => void
}

export function FunctionsLibrary({ functions, imports, provenance, onApply }: Props) {
  const [found, setFound] = useState<FlowFunction[]>([])
  const [parseError, setParseError] = useState('')
  const [saving, setSaving] = useState<FlowFunction | null>(null)
  const [comparing, setComparing] = useState<FlowFunction | null>(null)
  const [picking, setPicking] = useState(false)
  const [managing, setManaging] = useState(false)
  const [tick, setTick] = useState(0)                         // refetch after library changes

  useEffect(() => {
    const t = setTimeout(() => {
      api.library.functionsIn(functions, provenance)
        .then((r) => { setFound(r.functions); setParseError(r.error ?? '') })
        .catch(() => {})
    }, 400)
    return () => clearTimeout(t)
  }, [functions, provenance, tick])

  const apply = (r: LibraryResult) => { onApply(r); setTick((t) => t + 1) }

  return (
    <div className="shrink-0 rounded-lg border border-canvas-border bg-canvas-bg p-2.5 space-y-2 text-xs">
      <div className="flex items-center gap-2">
        <Library size={12} className="text-accent" />
        <span className="text-slate-300 font-medium flex-1">Functions in this flow</span>
        <button className="btn-ghost px-2 py-0.5 text-xs gap-1" onClick={() => setPicking(true)}><Plus size={11} /> From library</button>
        <button className="text-subtle hover:text-muted" title="Manage the library" onClick={() => setManaging(true)}><Settings2 size={13} /></button>
      </div>
      {parseError && <div className="text-amber-300/90">{parseError}</div>}
      {!parseError && found.length === 0 && <div className="text-subtle">No functions yet. Write a def above, or insert one from the library.</div>}
      {found.map((f) => {
        const lib = f.library
        return (
          <div key={f.name} className="flex items-center gap-2 flex-wrap">
            <code className="text-slate-200">{f.name}</code>
            {lib && <StateBadge state={lib.state} />}
            {lib?.deprecated && <StateBadge state="deprecated" note={lib.deprecation_note} />}
            <span className="flex-1" />
            {lib?.state === 'update' || lib?.state === 'modified' ? (
              <button className="text-accent hover:underline flex items-center gap-1" onClick={() => setComparing(f)}>
                <GitCompare size={11} /> {lib.state === 'update' ? 'Update' : 'Compare'}
              </button>
            ) : null}
            {!lib || lib.state === 'deleted' || lib.state === 'modified' ? (
              <button className="text-muted hover:text-slate-200 flex items-center gap-1" onClick={() => setSaving(f)}>
                <BookMarked size={11} /> Save to library
              </button>
            ) : null}
          </div>
        )
      })}

      {saving && <SaveDialog fn={saving} imports={imports} onClose={() => setSaving(null)} onSaved={() => { setSaving(null); setTick((t) => t + 1) }} />}
      {comparing && <CompareDialog fn={comparing} functions={functions} imports={imports} provenance={provenance}
                                    onClose={() => setComparing(null)} onApply={(r) => { apply(r); setComparing(null) }} />}
      {picking && <PickDialog functions={functions} imports={imports} provenance={provenance}
                              onClose={() => setPicking(false)} onApply={(r) => { apply(r); setPicking(false) }} />}
      {managing && <Modal title="Function library" wide onClose={() => { setManaging(false); setTick((t) => t + 1) }}><LibraryManager /></Modal>}
    </div>
  )
}

// ── Save to library ──────────────────────────────────────────────────────────

function SaveDialog({ fn, imports, onClose, onSaved }: { fn: FlowFunction; imports: string; onClose: () => void; onSaved: () => void }) {
  const [entries, setEntries] = useState<LibraryEntry[] | null>(null)
  const [description, setDescription] = useState('')
  const [tags, setTags] = useState('')
  const [needs, setNeeds] = useState('')
  const [mode, setMode] = useState<'version' | 'new'>('version')
  const [newName, setNewName] = useState(`${fn.name}_2`)
  const [error, setError] = useState('')

  useEffect(() => {
    api.library.list().then(setEntries).catch(() => setEntries([]))
    api.library.proposeImports(fn.source, imports).then((r) => setNeeds(r.imports)).catch(() => {})
  }, [fn, imports])
  const existing = entries?.find((e) => e.name === fn.name)
  useEffect(() => { if (existing) { setDescription(existing.description); setTags(existing.tags.join(', ')) } }, [existing?.id])

  const save = async () => {
    setError('')
    const tagList = tags.split(',').map((t) => t.trim()).filter(Boolean)
    try {
      if (existing && mode === 'version') {
        await api.library.edit(existing.id, { code: fn.source, imports: needs, description, tags: tagList })
      } else {
        const name = existing ? newName.trim() : fn.name
        const code = existing ? fn.source.replace(new RegExp(`^((?:async\\s+)?def|class)(\\s+)${fn.name}\\b`, 'm'), `$1$2${name}`) : fn.source
        await api.library.create({ name, code, imports: needs, description, tags: tagList })
      }
      onSaved()
    } catch (e) { setError(errText(e)) }
  }

  return (
    <Modal title={<>Save <code>{fn.name}</code> to the library</>} onClose={onClose}>
      <div className="space-y-3 text-xs">
        {existing && (
          <div className="rounded-lg border border-amber-500/30 bg-amber-950/20 p-2.5 space-y-1.5 text-amber-100/90">
            <div>The library already has <code>{fn.name}</code> (version {existing.version}, held by {existing.usage?.count ?? 0} flow{existing.usage?.count === 1 ? '' : 's'}).</div>
            <label className="flex items-center gap-2"><input type="radio" checked={mode === 'version'} onChange={() => setMode('version')} />
              Save as version {existing.version + 1} of that entry</label>
            <label className="flex items-center gap-2"><input type="radio" checked={mode === 'new'} onChange={() => setMode('new')} />
              Save as a new entry named <input className="input py-0.5 text-xs w-40 font-mono" value={newName} disabled={mode !== 'new'} onChange={(e) => setNewName(e.target.value)} /></label>
          </div>
        )}
        <div><label className="label">Description</label>
          <input className="input text-xs" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What it does, in a sentence" /></div>
        <div><label className="label">Tags (comma-separated)</label>
          <input className="input text-xs" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="dates, cleaning" /></div>
        <div><label className="label">Imports it needs (proposed from this flow's Imports)</label>
          <textarea className="input text-xs font-mono" rows={3} spellCheck={false} value={needs} onChange={(e) => setNeeds(e.target.value)} /></div>
        <div><label className="label">Code</label>
          <pre className="text-xs font-mono bg-canvas-bg border border-canvas-border rounded-lg p-2 max-h-56 overflow-auto">{fn.source}</pre></div>
        {error && <div className="text-red-300">{error}</div>}
        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-pri" onClick={save} disabled={!entries}>Save to library</button>
        </div>
      </div>
    </Modal>
  )
}

// ── Update / Compare ─────────────────────────────────────────────────────────

function CompareDialog({ fn, functions, imports, provenance, onClose, onApply }: {
  fn: FlowFunction; functions: string; imports: string; provenance: Record<string, any>
  onClose: () => void; onApply: (r: LibraryResult) => void
}) {
  const lib = fn.library!
  const [error, setError] = useState('')
  const update = () => api.library.updateCopy({ functions, imports, provenance, name: fn.name }).then(onApply).catch((e) => setError(errText(e)))
  return (
    <Modal title={<><code>{fn.name}</code>: this flow and library version {lib.latest_version}</>} wide onClose={onClose}>
      <div className="space-y-3 text-xs">
        {lib.state === 'modified' && (
          <div className="rounded-lg border border-amber-500/30 bg-amber-950/20 p-2.5 text-amber-100/90">
            This copy was changed in this flow. Updating replaces it with the library version and your changes are lost.
          </div>
        )}
        {lib.deprecated && <div className="text-red-300">Deprecated in the library{lib.deprecation_note ? `: ${lib.deprecation_note}` : ''}.</div>}
        <DiffView from={fn.source} to={lib.library_code ?? ''} fromLabel="this flow" toLabel={`library v${lib.latest_version}`} />
        {error && <div className="text-red-300">{error}</div>}
        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose}>Close</button>
          {(lib.latest_version ?? 0) >= (lib.version ?? 0) && (
            <button className={lib.state === 'modified' ? 'btn-danger' : 'btn-pri'} onClick={update}>
              {lib.state === 'modified' ? 'Overwrite with the library version' : `Update to version ${lib.latest_version}`}
            </button>
          )}
        </div>
      </div>
    </Modal>
  )
}

// ── Insert from library ──────────────────────────────────────────────────────

function PickDialog({ functions, imports, provenance, onClose, onApply }: {
  functions: string; imports: string; provenance: Record<string, any>
  onClose: () => void; onApply: (r: LibraryResult) => void
}) {
  const [entries, setEntries] = useState<LibraryEntry[]>([])
  const [q, setQ] = useState('')
  const [tag, setTag] = useState<string | null>(null)
  const [showDeprecated, setShowDeprecated] = useState(false)
  const [sel, setSel] = useState<LibraryEntry | null>(null)
  const [conflict, setConflict] = useState<string | null>(null)
  const [newName, setNewName] = useState('')
  const [error, setError] = useState('')

  useEffect(() => { api.library.list().then(setEntries).catch(() => {}) }, [])
  const tags = useMemo(() => [...new Set(entries.flatMap((e) => e.tags))].sort(), [entries])
  const shown = entries.filter((e) => (showDeprecated || !e.deprecated) && (!tag || e.tags.includes(tag)) &&
    (!q || `${e.name} ${e.description} ${e.tags.join(' ')}`.toLowerCase().includes(q.toLowerCase())))

  const insert = (on_conflict: 'ask' | 'replace' | 'rename' = 'ask') => {
    if (!sel) return
    setError('')
    api.library.insert({ functions, imports, provenance, entry_id: sel.id, on_conflict, new_name: newName || undefined })
      .then(onApply)
      .catch((e) => {
        const name = String(e?.message ?? '').match(/"conflict":"([^"]+)"/)?.[1]
        if (name) { setConflict(name); setNewName(`${name}_2`) } else setError(errText(e))
      })
  }

  return (
    <Modal title="Insert a function from the library" wide onClose={onClose}>
      <div className="flex gap-4 h-full min-h-0 text-xs">
        <div className="w-72 shrink-0 flex flex-col gap-2 min-h-0">
          <div className="relative">
            <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-subtle" />
            <input className="input pl-7 text-xs" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
          </div>
          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {tags.map((t) => (
                <button key={t} onClick={() => setTag(tag === t ? null : t)}
                  className={`rounded px-1.5 py-0.5 ${tag === t ? 'bg-accent/20 text-accent' : 'bg-surface-overlay text-muted'}`}>{t}</button>
              ))}
            </div>
          )}
          <label className="flex items-center gap-1.5 text-subtle"><input type="checkbox" checked={showDeprecated} onChange={(e) => setShowDeprecated(e.target.checked)} /> Show deprecated</label>
          <div className="flex-1 overflow-y-auto space-y-0.5">
            {shown.length === 0 && <div className="text-subtle p-2">{entries.length ? 'Nothing matches.' : 'The library is empty. Save a function from a flow\'s Functions tab to start one.'}</div>}
            {shown.map((e) => (
              <button key={e.id} onClick={() => { setSel(e); setConflict(null); setError('') }}
                className={`block w-full text-left rounded-md px-2 py-1.5 ${sel?.id === e.id ? 'bg-accent/15' : 'hover:bg-surface-overlay'}`}>
                <div className="flex items-center gap-1.5"><code className="text-slate-200">{e.name}</code>
                  {e.deprecated && <StateBadge state="deprecated" note={e.deprecation_note} />}</div>
                {e.description && <div className="text-muted truncate">{e.description}</div>}
              </button>
            ))}
          </div>
        </div>
        <div className="flex-1 min-w-0 overflow-y-auto space-y-3">
          {!sel && <div className="text-subtle">Pick a function to see its code.</div>}
          {sel && (
            <>
              <div className="flex items-center gap-2"><code className="text-sm text-slate-100">{sel.name}</code>
                <span className="text-subtle">version {sel.version} · {sel.tags.join(', ')}</span></div>
              {sel.description && <p className="text-slate-300">{sel.description}</p>}
              {sel.deprecated && <p className="text-red-300">Deprecated{sel.deprecation_note ? `: ${sel.deprecation_note}` : ''}.</p>}
              {sel.imports.trim() && <><div className="label">Imports (added to the flow if missing)</div>
                <pre className="font-mono bg-canvas-bg border border-canvas-border rounded-lg p-2">{sel.imports.trim()}</pre></>}
              <div className="label">Code (copied into this flow; your copy is yours to edit)</div>
              <pre className="font-mono bg-canvas-bg border border-canvas-border rounded-lg p-2 overflow-x-auto">{sel.code}</pre>
              {conflict ? (
                <div className="rounded-lg border border-amber-500/30 bg-amber-950/20 p-2.5 space-y-2 text-amber-100/90">
                  <div>This flow already has a function called <code>{conflict}</code>.</div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button className="btn-danger" onClick={() => insert('replace')}>Replace it</button>
                    <span>or insert as</span>
                    <input className="input py-0.5 text-xs w-44 font-mono" value={newName} onChange={(e) => setNewName(e.target.value)} />
                    <button className="btn-pri" onClick={() => insert('rename')}>Insert under this name</button>
                    <button className="btn-ghost" onClick={() => setConflict(null)}>Cancel</button>
                  </div>
                </div>
              ) : (
                <button className="btn-pri gap-1.5" onClick={() => insert()}><Download size={12} /> Insert into this flow</button>
              )}
              {error && <div className="text-red-300">{error}</div>}
            </>
          )}
        </div>
      </div>
    </Modal>
  )
}
