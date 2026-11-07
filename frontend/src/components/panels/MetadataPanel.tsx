import { LibraryManager } from '@/components/library/LibraryManager'
import { Modal } from '@/components/library/parts'
import { useState } from 'react'
import { Plus, Trash2, Check, X, TestTube2, Pencil, Download, Upload, KeyRound } from 'lucide-react'
import { useStore } from '@/store'
import { api, type ConnectionBundle } from '@/utils/api'
import { downloadJson, pickJson } from '@/utils/download'
import { ConnectionImport } from './ConnectionImport'
import { useConnectionTypes } from '@/utils/engines'
import type { ConnectionType } from '@/utils/api'
import { AgentProvidersPanel } from './AgentProvidersPanel'
import { useEffect as _useEffect, useState as _useState } from 'react'
import type { RunRow } from '@/types'

function RunsPanel() {
  const { activeProject } = useStore()
  const [runs, setRuns] = _useState<RunRow[]>([])
  const load = async () => { try { setRuns(await api.runs.list(activeProject?.id)) } catch {} }
  _useEffect(() => { load() }, [activeProject?.id])

  const color = (s: string) => s === 'ok' ? 'text-status-ok' : s === 'error' ? 'text-status-error'
    : s === 'stopped' ? 'text-amber-400' : 'text-muted'
  const icon = (s: string) => s === 'ok' ? '✓' : s === 'error' ? '✗' : s === 'stopped' ? '⊘' : '⋯'

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted">Recent runs (manual + scheduled)</p>
        <button className="btn-ghost text-xs" onClick={load}>↻</button>
      </div>
      {runs.length === 0 && <p className="text-xs text-subtle">No runs yet.</p>}
      {runs.map((r) => (
        <div key={r.id} className="rounded-lg border border-canvas-border p-2 text-xs space-y-1">
          <div className="flex items-center gap-2">
            <span className={color(r.status)}>{icon(r.status)}</span>
            <span className="font-medium text-slate-200 flex-1 truncate">{r.flow_name || r.flow_id}</span>
            <span className="text-subtle">{r.trigger === 'schedule' ? '🕐' : '▶'}</span>
            {r.attempt > 1 && <span className="text-amber-400">try {r.attempt}</span>}
          </div>
          <div className="flex items-center gap-2 text-subtle">
            {r.started_at && <span>{new Date(r.started_at).toLocaleString()}</span>}
            {r.duration_ms != null && <span>· {(r.duration_ms/1000).toFixed(1)}s</span>}
            {r.nodes_ok != null && <span>· {r.nodes_ok}✓{r.nodes_error ? ` ${r.nodes_error}✗` : ''}</span>}
          </div>
          {r.error && <div className="text-red-300 bg-red-950/30 rounded p-1 break-words">{r.error.slice(0,200)}</div>}
        </div>
      ))}
    </div>
  )
}

const DIALECTS = [
  'postgresql','mysql','sqlite','mssql','oracle','druid',
  'snowflake','bigquery','clickhouse','redshift','adls',
  'starrocks','doris','trino','databricks',
]

// Per-dialect field hints — host/database mean different things per engine
const DIALECT_HINTS: Record<string, { host: string; port: string; database: string; extra: string }> = {
  postgresql: { host: 'localhost',                    port: '5432', database: 'mydb',            extra: '' },
  mysql:      { host: 'localhost',                    port: '3306', database: 'mydb',            extra: '' },
  sqlite:     { host: '',                             port: '',     database: '/path/to.db',     extra: '' },
  mssql:      { host: 'server.domain',                port: '1433', database: 'mydb',            extra: 'driver=ODBC Driver 18 for SQL Server' },
  oracle:     { host: 'host.domain',                  port: '1521', database: 'SERVICE_NAME',    extra: '' },
  druid:      { host: 'broker-host',                  port: '8082', database: 'druid/v2/sql',    extra: '' },
  snowflake:  { host: 'xy12345.eu-central-1 (account)', port: '',   database: 'DB/SCHEMA',       extra: 'warehouse=COMPUTE_WH&role=ANALYST' },
  bigquery:   { host: 'gcp-project-id',               port: '',     database: 'dataset',         extra: 'credentials_path=/path/service-account.json' },
  clickhouse: { host: 'ch-host',                      port: '8123', database: 'default',         extra: 'protocol=https (for port 8443)' },
  redshift:   { host: 'cluster.xxxx.region.redshift.amazonaws.com', port: '5439', database: 'dev', extra: '' },
  adls:       { host: 'storageaccountname',           port: '',     database: 'container',       extra: 'sas_token=... or client_id/client_secret/tenant_id' },
  starrocks:  { host: 'fe-host',                       port: '9030', database: 'mydb',            extra: '' },
  doris:      { host: 'fe-host',                       port: '9030', database: 'mydb',            extra: '' },
  trino:      { host: 'coordinator-host',              port: '8080', database: 'catalog/schema',  extra: 'http_scheme=https (for TLS)' },
  databricks: { host: 'dbc-xxxx.cloud.databricks.com', port: '',     database: '',                extra: 'http_path=/sql/1.0/warehouses/xxx&catalog=main&schema=default' },
}

export function MetadataPanel() {
  const { connections, variables, loadConnections, loadVariables } = useStore()
  const [tab, setTab] = useState<'conn' | 'vars' | 'ai' | 'runs' | 'lib'>('conn')
  const [libOpen, setLibOpen] = useState(false)
  const [showAdd, setShowAdd] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [testRes, setTestRes] = useState<Record<string,boolean|null>>({})
  const [form, setForm] = useState({ name:'', dialect:'postgresql', host:'', port:'', database:'', username:'', password:'', extra:'' })
  const [varForm, setVarForm] = useState({ key:'', value:'', description:'' })

  const blankForm = { name:'', dialect:'postgresql', host:'', port:'', database:'', username:'', password:'', extra:'' }
  // Plugin connection types (TM1, Power BI…): fields declared by the plugin
  const connTypes = useConnectionTypes()
  const ctype = connTypes.find((t) => t.dialect === form.dialect)
  const [pfields, setPfields] = useState<Record<string, any>>({})
  const [testMsg, setTestMsg] = useState<Record<string, string>>({})
  // Secret options stay on the server; the form lists their names and can remove them
  const [secrets, setSecrets] = useState<string[]>([])
  const [clearSecrets, setClearSecrets] = useState<string[]>([])
  const [connImport, setConnImport] = useState<ConnectionBundle | null>(null)
  const [ioError, setIoError] = useState('')
  const exportConns = async () => {
    try { downloadJson('sfs-connections.json', await api.connections.exportAll(true)) }
    catch (e) { setIoError(String((e as Error).message ?? e)) }
  }
  const importConns = async () => {
    setIoError('')
    try {
      const b = await pickJson() as ConnectionBundle
      if ((b as any)?.format !== 'sfs-connections') throw new Error("This isn't an SFS connections export.")
      setConnImport(b)
    } catch (e) { setIoError(String((e as Error).message ?? e)) }
  }

  const startEdit = (c: any) => {
    // Serialize existing extra_params back into the k=v&k2=v2 string
    const extraStr = c.extra_params
      ? Object.entries(c.extra_params).map(([k, v]) => `${k}=${v}`).join('&')
      : ''
    setSecrets(c.secret_extras ?? [])
    setClearSecrets([])
    setPfields({ ...(c.extra_params ?? {}) })
    setForm({
      name: c.name ?? '', dialect: c.dialect ?? 'postgresql',
      host: c.host ?? '', port: c.port ? String(c.port) : '',
      database: c.database ?? '', username: c.username ?? '',
      password: '',              // never pre-filled; blank = keep existing
      extra: extraStr,
    })
    setEditingId(c.id)
    setShowAdd(true)
  }

  const addConn = async () => {
    if (ctype) {
      // Secret fields left blank are kept by the server (like a blank password)
      const extra_params = Object.fromEntries(Object.entries(pfields).filter(([, v]) => v !== '' && v != null))
      const payload = { name: form.name, dialect: form.dialect, extra_params }
      if (editingId) await api.connections.update(editingId, payload)
      else await api.connections.create(payload)
      setShowAdd(false); setEditingId(null); setForm(blankForm); setPfields({}); setSecrets([])
      await loadConnections()
      return
    }
    // Parse "k=v&k2=v2" extras into a dict for extra_params
    const extra_params: Record<string, string> = {}
    form.extra.split('&').map((kv) => kv.trim()).filter(Boolean).forEach((kv) => {
      const i = kv.indexOf('=')
      if (i > 0) extra_params[kv.slice(0, i).trim()] = kv.slice(i + 1).trim()
    })
    const { extra, ...rest } = form
    const payload: any = {
      ...rest,
      port: form.port ? Number(form.port) : undefined,
      extra_params: Object.keys(extra_params).length ? extra_params : undefined,
    }
    // On edit, don't send an empty password (keeps the stored one). Secret
    // options not typed in are kept by the server; removed ones are listed.
    if (editingId && !form.password) delete payload.password
    if (editingId) {
      payload.extra_params = extra_params
      if (clearSecrets.length) payload.clear_secrets = clearSecrets
    }

    if (editingId) {
      await api.connections.update(editingId, payload)
    } else {
      await api.connections.create(payload)
    }
    setShowAdd(false)
    setEditingId(null)
    setForm(blankForm)
    setSecrets([]); setClearSecrets([])
    await loadConnections()
  }

  const testConn = async (id: string) => {
    setTestRes((r) => ({ ...r, [id]: null }))
    const res = await api.connections.test(id)
    setTestRes((r) => ({ ...r, [id]: res.ok }))
    setTestMsg((m) => ({ ...m, [id]: res.ok ? ((res as any).message || '') : (res.error || 'Failed') }))
  }

  const addVar = async () => {
    await api.metadata.createVar({ ...varForm, scope: 'global' })
    setVarForm({ key:'', value:'', description:'' })
    await loadVariables()
  }

  return (
    <div className="p-4 space-y-3">
      <div className="flex gap-1 bg-canvas-bg rounded-lg p-1">
        {([['conn','Connections'],['vars','Variables'],['ai','AI'],['runs','Runs'],['lib','Library']] as const).map(([t,l]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`flex-1 py-1.5 rounded text-xs font-medium transition-colors
              ${tab === t ? 'bg-surface-raised text-accent' : 'text-muted hover:text-slate-300'}`}>
            {l}
          </button>
        ))}
      </div>

      {tab === 'conn' && (
        <>
          {connections.map((c) => (
            <div key={c.id} className="rounded-lg border border-canvas-border p-3 space-y-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-slate-200 flex-1 truncate">{c.name}</span>
                <span className="text-xs font-mono text-muted bg-surface-overlay rounded px-1.5 py-0.5">{c.dialect}</span>
                <button onClick={() => testConn(c.id)} className="btn-ghost p-1" title="Test">
                  {testRes[c.id] === null ? <div className="w-3 h-3 border border-accent border-t-transparent rounded-full animate-spin" /> :
                   testRes[c.id] === true  ? <Check size={12} className="text-status-ok" /> :
                   testRes[c.id] === false ? <X size={12} className="text-status-error" /> :
                   <TestTube2 size={12} />}
                </button>
                <button onClick={() => startEdit(c)} className="btn-ghost p-1" title="Edit">
                  <Pencil size={12} />
                </button>
                <button onClick={async () => { await api.connections.delete(c.id); loadConnections() }}
                  className="btn-ghost p-1 text-red-400" title="Delete"><Trash2 size={12} /></button>
              </div>
              <div className="text-xs text-muted font-mono">
                {c.host}{c.port ? `:${c.port}` : ''}{c.database ? `/${c.database}` : ''}
              </div>
              {testMsg[c.id] && testRes[c.id] !== null && (
                <div className={`text-[11px] break-words ${testRes[c.id] ? 'text-status-ok' : 'text-red-300'}`}>{testMsg[c.id]}</div>
              )}
              {c.needs_credentials && (
                <button onClick={() => startEdit(c)}
                  className="text-[11px] rounded px-1.5 py-0.5 bg-amber-500/15 text-amber-300 flex items-center gap-1"
                  title="Imported without its credentials. Edit to enter them.">
                  <KeyRound size={10} /> needs {[c.needs?.password ? 'password' : '', ...(c.needs?.extras ?? [])].filter(Boolean).join(', ')}
                </button>
              )}
            </div>
          ))}

          {showAdd ? (
            <div className="rounded-lg border border-accent/30 p-3 space-y-2">
              {editingId && <div className="text-xs text-accent font-medium">Editing connection</div>}
              <div className="grid grid-cols-2 gap-2">
                <div className="col-span-2"><label className="label">Name</label>
                  <input className="input" value={form.name} onChange={(e) => setForm({...form,name:e.target.value})} /></div>
                <div className="col-span-2"><label className="label">Dialect</label>
                  <select className="input" value={form.dialect} onChange={(e) => setForm({...form,dialect:e.target.value})}>
                    {DIALECTS.map((d) => <option key={d}>{d}</option>)}
                    {connTypes.length > 0 && <optgroup label="From plugins">
                      {connTypes.map((t) => <option key={t.dialect} value={t.dialect}>{t.label}</option>)}
                    </optgroup>}</select></div>
                {ctype ? (
                  <PluginFields ctype={ctype} values={pfields} onChange={setPfields} stored={secrets} />
                ) : (<>
                <div><label className="label">Host</label>
                  <input className="input font-mono" value={form.host} onChange={(e) => setForm({...form,host:e.target.value})} placeholder={DIALECT_HINTS[form.dialect]?.host ?? 'localhost'} /></div>
                <div><label className="label">Port</label>
                  <input className="input font-mono" value={form.port} onChange={(e) => setForm({...form,port:e.target.value})} placeholder={DIALECT_HINTS[form.dialect]?.port ?? ''} /></div>
                <div><label className="label">Database</label>
                  <input className="input font-mono" value={form.database} onChange={(e) => setForm({...form,database:e.target.value})} placeholder={DIALECT_HINTS[form.dialect]?.database ?? ''} /></div>
                <div><label className="label">Username</label>
                  <input className="input font-mono" value={form.username} onChange={(e) => setForm({...form,username:e.target.value})} /></div>
                <div className="col-span-2"><label className="label">Password</label>
                  <input type="password" className="input" value={form.password} onChange={(e) => setForm({...form,password:e.target.value})}
                    placeholder={editingId ? 'leave blank to keep current' : (form.dialect === 'adls' ? 'account key' : '')} /></div>
                <div className="col-span-2"><label className="label">Extra options (k=v&k2=v2)</label>
                  <input className="input font-mono text-xs" value={form.extra} onChange={(e) => setForm({...form,extra:e.target.value})}
                    placeholder={DIALECT_HINTS[form.dialect]?.extra ?? ''} />
                  <div className="text-[11px] text-subtle mt-1">Options named like a secret (token, secret, key, password, credentials…) are
                    encrypted and never shown again.</div>
                  {secrets.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1.5 items-center text-[11px]">
                      <span className="text-muted">Stored secrets:</span>
                      {secrets.map((k) => {
                        const gone = clearSecrets.includes(k)
                        return (
                          <span key={k} className={`rounded px-1.5 py-0.5 font-mono flex items-center gap-1 ${gone ? 'bg-red-500/10 text-red-300 line-through' : 'bg-surface-overlay text-slate-300'}`}>
                            {k}
                            <button title={gone ? 'Keep it' : 'Remove it on save'} onClick={() =>
                              setClearSecrets(gone ? clearSecrets.filter((x) => x !== k) : [...clearSecrets, k])}>×</button>
                          </span>
                        )
                      })}
                      <span className="text-subtle">kept unless you type {secrets.length === 1 ? 'it' : 'one'} above to replace</span>
                    </div>
                  )}
                </div>
                </>)}
              </div>
              <div className="flex gap-2">
                <button className="btn-pri flex-1" onClick={addConn}>{editingId ? 'Save changes' : 'Add'}</button>
                <button className="btn-ghost" onClick={() => { setShowAdd(false); setEditingId(null); setForm(blankForm); setSecrets([]); setClearSecrets([]) }}>Cancel</button>
              </div>
            </div>
          ) : (
            <button className="btn-ghost w-full justify-center border border-dashed border-canvas-border"
              onClick={() => setShowAdd(true)}><Plus size={12} /> Add Connection</button>
          )}
          <div className="flex gap-2">
            <button className="btn-ghost flex-1 justify-center text-xs gap-1" onClick={exportConns}
              title="Names, hosts and options of every connection, plus variables. No passwords or secret options.">
              <Download size={11} /> Export connections</button>
            <button className="btn-ghost flex-1 justify-center text-xs gap-1" onClick={importConns}><Upload size={11} /> Import</button>
          </div>
          {ioError && <div className="text-xs text-red-300">{ioError}</div>}
          {connImport && <ConnectionImport bundle={connImport} onClose={() => setConnImport(null)}
                                           onDone={() => { loadConnections(); loadVariables() }} />}
        </>
      )}

      {tab === 'vars' && (
        <>
          {variables.map((v) => (
            <div key={v.id} className="rounded-lg border border-canvas-border p-3 flex gap-2">
              <div className="flex-1 min-w-0">
                <div className="font-mono text-xs text-accent">{v.key}</div>
                <div className="text-xs text-slate-300 truncate">{v.value}</div>
                {v.description && <div className="text-xs text-muted">{v.description}</div>}
              </div>
              <button onClick={async () => { await api.metadata.deleteVar(v.id); loadVariables() }}
                className="btn-ghost p-1 text-red-400 shrink-0"><Trash2 size={12} /></button>
            </div>
          ))}

          <p className="text-[11px] text-subtle">Variables are plain text and export with connections: don't put secrets in them.</p>
          <div className="rounded-lg border border-canvas-border p-3 space-y-2">
            <div><label className="label">Key</label>
              <input className="input font-mono" value={varForm.key} onChange={(e) => setVarForm({...varForm,key:e.target.value})} placeholder="MY_VAR" /></div>
            <div><label className="label">Value</label>
              <input className="input" value={varForm.value} onChange={(e) => setVarForm({...varForm,value:e.target.value})} /></div>
            <button className="btn-pri w-full" onClick={addVar}><Plus size={12} /> Add Variable</button>
          </div>
        </>
      )}
      {tab === 'ai' && <AgentProvidersPanel />}
      {tab === 'runs' && <RunsPanel />}
      {tab === 'lib' && (
        <div className="space-y-2">
          <p className="text-xs text-muted">Reusable functions. Inserting one copies it into a flow, so flows never depend on the library.</p>
          <button className="btn-ghost border border-canvas-border w-full text-xs" onClick={() => setLibOpen(true)}>Open the function library</button>
          {libOpen && <Modal title="Function library" wide onClose={() => setLibOpen(false)}><LibraryManager /></Modal>}
        </div>
      )}
    </div>
  )
}

/** The fields a plugin's connection type declares. Secret fields are never
 *  pre-filled: blank keeps the stored value. `show_when` hides fields that
 *  belong to another mode (e.g. TM1 authentication). */
function PluginFields({ ctype, values, onChange, stored }: {
  ctype: ConnectionType; values: Record<string, any>; onChange: (v: Record<string, any>) => void; stored: string[]
}) {
  const val = (k: string, d?: unknown) => (values[k] ?? d ?? '')
  const visible = (f: ConnectionType['fields'][number]) =>
    !f.show_when || Object.entries(f.show_when).every(([k, allowed]) => allowed.includes(String(val(k, ctype.fields.find((x) => x.key === k)?.default))))
  return (
    <>
      {ctype.description && <p className="col-span-2 text-[11px] text-muted">{ctype.description}</p>}
      {ctype.fields.filter(visible).map((f) => {
        const set = (v: unknown) => onChange({ ...values, [f.key]: v })
        const label = <label className="label">{f.label ?? f.key}{f.required && <span className="text-red-400"> *</span>}</label>
        let input: React.ReactNode
        if (f.type === 'boolean') {
          const checked = values[f.key] ?? f.default ?? false
          return (
            <label key={f.key} className="col-span-2 flex items-center gap-2 text-xs text-slate-300">
              <input type="checkbox" checked={!!checked} onChange={(e) => set(e.target.checked)} /> {f.label ?? f.key}
            </label>
          )
        }
        if (f.type === 'select') {
          input = (
            <select className="input text-xs" value={String(val(f.key, f.default))} onChange={(e) => set(e.target.value)}>
              {(f.options ?? []).map((o) => {
                const opt = typeof o === 'string' ? { value: o, label: o } : o
                return <option key={opt.value} value={opt.value}>{opt.label ?? opt.value}</option>
              })}
            </select>
          )
        } else if (f.type === 'secret') {
          input = <input type="password" className="input" value={values[f.key] ?? ''} onChange={(e) => set(e.target.value)}
                         placeholder={stored.includes(f.key) ? 'stored: leave blank to keep' : ''} autoComplete="new-password" />
        } else {
          input = <input type={f.type === 'number' ? 'number' : 'text'} className="input font-mono text-xs"
                         value={String(val(f.key))} placeholder={f.default != null ? String(f.default) : ''}
                         onChange={(e) => set(f.type === 'number' && e.target.value !== '' ? Number(e.target.value) : e.target.value)} />
        }
        return (
          <div key={f.key} className="col-span-2">
            {label}{input}
            {f.help && <div className="text-[11px] text-subtle mt-0.5">{f.help}</div>}
          </div>
        )
      })}
    </>
  )
}
