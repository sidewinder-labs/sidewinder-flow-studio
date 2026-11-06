/**
 * Library manager: every entry with version, last update, tags and usage
 * (which flows hold a copy, and in what state). Edit, deprecate, delete,
 * export and import. Opened from a flow's Functions tab and from Meta → Library.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import Editor from '@monaco-editor/react'
import { Archive, Download, Pencil, Search, Trash2, Upload } from 'lucide-react'
import { api, type LibraryEntry } from '@/utils/api'
import { StateBadge, errText } from './parts'

type Filter = 'all' | 'unused' | 'deprecated'

export function LibraryManager() {
  const [entries, setEntries] = useState<LibraryEntry[]>([])
  const [filter, setFilter] = useState<Filter>('all')
  const [q, setQ] = useState('')
  const [selId, setSelId] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const load = () => api.library.list().then(setEntries).catch(() => {})
  useEffect(() => { load() }, [])
  const sel = entries.find((e) => e.id === selId) ?? null
  const shown = entries.filter((e) =>
    (filter === 'all' || (filter === 'unused' ? (e.usage?.count ?? 0) === 0 : e.deprecated)) &&
    (!q || `${e.name} ${e.description} ${e.tags.join(' ')}`.toLowerCase().includes(q.toLowerCase())))

  const exportAll = async () => {
    const data = await api.library.exportAll()
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
    const a = Object.assign(document.createElement('a'), { href: url, download: 'sfs-library.json' })
    a.click(); URL.revokeObjectURL(url)
  }
  const importFile = async (f: File) => {
    try {
      const r = await api.library.importAll(JSON.parse(await f.text()))
      setNotice(`Imported ${r.functions_added} function${r.functions_added === 1 ? '' : 's'} (${r.functions_skipped} already here) and ${r.custom_processors_added} custom processor${r.custom_processors_added === 1 ? '' : 's'}.`)
      load()
    } catch (e) { setNotice(errText(e)) }
  }

  return (
    <div className="flex gap-4 h-full min-h-0 text-xs">
      <div className="w-80 shrink-0 flex flex-col gap-2 min-h-0">
        <div className="relative">
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-subtle" />
          <input className="input pl-7 text-xs" placeholder="Search the library" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="flex gap-1">
          {(['all', 'unused', 'deprecated'] as Filter[]).map((f) => (
            <button key={f} onClick={() => setFilter(f)}
              className={`rounded px-2 py-0.5 capitalize ${filter === f ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-300'}`}>{f}</button>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto space-y-0.5">
          {shown.length === 0 && <div className="text-subtle p-2">{entries.length ? 'Nothing here.' : 'The library is empty. In a flow\'s Functions tab, use Save to library.'}</div>}
          {shown.map((e) => (
            <button key={e.id} onClick={() => setSelId(e.id)}
              className={`block w-full text-left rounded-md px-2 py-1.5 ${selId === e.id ? 'bg-accent/15' : 'hover:bg-surface-overlay'}`}>
              <div className="flex items-center gap-1.5">
                <code className="text-slate-200">{e.name}</code>
                <span className="text-subtle">v{e.version}</span>
                {e.deprecated && <StateBadge state="deprecated" />}
                <span className="flex-1" />
                <span className={(e.usage?.count ?? 0) ? 'text-muted' : 'text-subtle'}>{e.usage?.count ?? 0} flow{e.usage?.count === 1 ? '' : 's'}</span>
              </div>
              <div className="text-subtle truncate">{e.tags.join(', ') || e.description}</div>
            </button>
          ))}
        </div>
        <div className="flex gap-2 border-t border-canvas-border pt-2">
          <button className="btn-ghost text-xs gap-1" onClick={exportAll} title="Library functions and custom processors, as one file"><Download size={11} /> Export library</button>
          <button className="btn-ghost text-xs gap-1" onClick={() => fileRef.current?.click()}><Upload size={11} /> Import</button>
          <input ref={fileRef} type="file" accept=".json,application/json" className="hidden"
                 onChange={(e) => { const f = e.target.files?.[0]; if (f) importFile(f); e.target.value = '' }} />
        </div>
        {notice && <div className="text-muted">{notice}</div>}
      </div>
      <div className="flex-1 min-w-0 overflow-y-auto">
        {sel ? <EntryDetail key={sel.id + sel.version} entry={sel} onChanged={load} onDeleted={() => { setSelId(null); load() }} />
             : <div className="text-subtle">Pick an entry to see its code, where it is used, and to edit it.</div>}
      </div>
    </div>
  )
}

function EntryDetail({ entry, onChanged, onDeleted }: { entry: LibraryEntry; onChanged: () => void; onDeleted: () => void }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({ code: entry.code, imports: entry.imports, description: entry.description, tags: entry.tags.join(', ') })
  const [depNote, setDepNote] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState('')
  const copies = entry.usage?.copies ?? []
  const byState = useMemo(() => copies.reduce<Record<string, number>>((m, c) => ({ ...m, [c.state]: (m[c.state] ?? 0) + 1 }), {}), [copies])

  const save = () => api.library.edit(entry.id, { ...draft, tags: draft.tags.split(',').map((t) => t.trim()).filter(Boolean) })
    .then(() => { setEditing(false); onChanged() }).catch((e) => setError(errText(e)))

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <code className="text-base text-slate-100">{entry.name}</code>
        <span className="text-subtle">version {entry.version} · updated {new Date(entry.updated_at).toLocaleString()}</span>
        {entry.deprecated && <StateBadge state="deprecated" note={entry.deprecation_note} />}
      </div>
      {entry.deprecated && entry.deprecation_note && <p className="text-red-300">{entry.deprecation_note}</p>}

      {!editing ? (
        <>
          {entry.description && <p className="text-slate-300">{entry.description}</p>}
          {entry.tags.length > 0 && <div className="text-muted">Tags: {entry.tags.join(', ')}</div>}
          {entry.imports.trim() && <pre className="font-mono bg-canvas-bg border border-canvas-border rounded-lg p-2">{entry.imports.trim()}</pre>}
          <pre className="font-mono bg-canvas-bg border border-canvas-border rounded-lg p-2 overflow-x-auto">{entry.code}</pre>
        </>
      ) : (
        <div className="space-y-2">
          <div className="rounded-lg border border-accent/30 bg-accent/5 p-2 text-slate-300">
            Saving makes version {entry.version + 1}. {copies.length} flow{copies.length === 1 ? '' : 's'} hold{copies.length === 1 ? 's' : ''} a copy
            and will show <em>Update available</em>; nothing changes in them until their owner updates.
          </div>
          <div><label className="label">Description</label><input className="input text-xs" value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></div>
          <div><label className="label">Tags</label><input className="input text-xs" value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} /></div>
          <div><label className="label">Imports</label><textarea className="input text-xs font-mono" rows={3} spellCheck={false} value={draft.imports} onChange={(e) => setDraft({ ...draft, imports: e.target.value })} /></div>
          <div className="h-64 rounded-lg overflow-hidden border border-canvas-border" onKeyDown={(e) => e.stopPropagation()}>
            <Editor height="100%" defaultLanguage="python" theme="vs-dark" value={draft.code}
                    onChange={(v) => setDraft({ ...draft, code: v ?? '' })}
                    options={{ minimap: { enabled: false }, fontSize: 12, scrollBeyondLastLine: false, automaticLayout: true }} />
          </div>
          {error && <div className="text-red-300">{error}</div>}
          <div className="flex justify-end gap-2">
            <button className="btn-ghost" onClick={() => { setEditing(false); setError('') }}>Cancel</button>
            <button className="btn-pri" onClick={save}>Save version {entry.version + 1}</button>
          </div>
        </div>
      )}

      <div>
        <div className="label">Used in {copies.length} flow{copies.length === 1 ? '' : 's'}
          {entry.usage?.last_inserted && <> · last inserted {new Date(entry.usage.last_inserted).toLocaleDateString()}</>}</div>
        {copies.length === 0 && <div className="text-subtle">No flow holds a copy: safe to delete.</div>}
        {copies.map((c, i) => (
          <div key={i} className="flex items-center gap-2 py-0.5">
            <span className="text-slate-300 truncate">{c.project} → {c.flow}</span>
            {c.function !== entry.name && <code className="text-subtle">{c.function}</code>}
            <span className="flex-1" />
            {(['current', 'update', 'modified', 'deleted'] as const).includes(c.state as any) && <StateBadge state={c.state as any} />}
          </div>
        ))}
        {copies.length > 0 && <div className="text-subtle mt-1">Copies are independent: nothing here can break these flows. {Object.entries(byState).map(([s, n]) => `${n} ${s}`).join(' · ')}</div>}
      </div>

      {!editing && (
        <div className="flex flex-wrap gap-2 border-t border-canvas-border pt-3">
          <button className="btn-ghost gap-1" onClick={() => setEditing(true)}><Pencil size={11} /> Edit</button>
          {entry.deprecated
            ? <button className="btn-ghost gap-1" onClick={() => api.library.deprecate(entry.id, '', false).then(onChanged)}><Archive size={11} /> Un-deprecate</button>
            : <button className="btn-ghost gap-1" onClick={() => setDepNote(depNote === null ? '' : null)}><Archive size={11} /> Deprecate</button>}
          <button className="btn-ghost gap-1 text-red-300" onClick={() => setConfirmDelete(true)}><Trash2 size={11} /> Delete</button>
        </div>
      )}
      {depNote !== null && (
        <div className="flex gap-2 items-center">
          <input className="input text-xs flex-1" placeholder='Note, e.g. "use parse_dates_v2"' value={depNote} onChange={(e) => setDepNote(e.target.value)} autoFocus />
          <button className="btn-pri" onClick={() => api.library.deprecate(entry.id, depNote).then(() => { setDepNote(null); onChanged() })}>Deprecate</button>
        </div>
      )}
      {confirmDelete && (
        <div className="rounded-lg border border-red-500/30 bg-red-950/20 p-2.5 space-y-2 text-red-100/90">
          {copies.length > 0 ? (
            <div>{copies.length} flow{copies.length === 1 ? ' holds' : 's hold'} a copy. They keep working after a delete, but lose the link
              to updates. <strong>Deprecate</strong> keeps the entry and tells those flows it is outdated.</div>
          ) : <div>No flow uses it. Delete it for good?</div>}
          <div className="flex gap-2">
            {copies.length > 0 && <button className="btn-pri" onClick={() => { setConfirmDelete(false); setDepNote('') }}>Deprecate instead</button>}
            <button className="btn-danger" onClick={() => api.library.remove(entry.id).then(onDeleted)}>Delete</button>
            <button className="btn-ghost" onClick={() => setConfirmDelete(false)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  )
}
