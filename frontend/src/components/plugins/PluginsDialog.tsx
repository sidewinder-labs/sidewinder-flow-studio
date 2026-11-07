/**
 * Plugins menu: what's bundled and installed, what the official catalog
 * offers for this SFS and platform, updates, the Enterprise licence, and safe
 * mode. Every pack is signed by Sidewinder Labs and checked before install and
 * at every start. Changes take effect after a restart.
 */
import { useEffect, useRef, useState } from 'react'
import { BadgeCheck, Download, KeyRound, Power, RefreshCw, RotateCcw, ShieldAlert, Trash2, Upload } from 'lucide-react'
import { api, type InstalledPack, type PluginOffer, type PluginsView } from '@/utils/api'
import { Modal, errText } from '@/components/library/parts'

type Tab = 'available' | 'installed' | 'updates'

function Edition({ e }: { e?: string }) {
  return e === 'enterprise'
    ? <span className="text-[11px] rounded px-1.5 py-0.5 bg-violet-500/15 text-violet-300">Enterprise</span>
    : <span className="text-[11px] rounded px-1.5 py-0.5 bg-sky-500/15 text-sky-300">Community</span>
}

const Official = () => (
  <span className="text-[11px] rounded px-1.5 py-0.5 bg-emerald-500/15 text-emerald-300 flex items-center gap-1"
        title="Signed by Sidewinder Labs; checked before install and at every start">
    <BadgeCheck size={10} /> Official
  </span>
)

export function PluginsDialog({ onClose }: { onClose: () => void }) {
  const [v, setV] = useState<PluginsView | null>(null)
  const [tab, setTab] = useState<Tab>('installed')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [licenceText, setLicenceText] = useState('')
  const [askLicenceFor, setAskLicenceFor] = useState<PluginOffer | null>(null)
  const [restarting, setRestarting] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const run = async (label: string, fn: () => Promise<PluginsView>) => {
    setBusy(label); setError('')
    try { setV(await fn()) } catch (e) { setError(errText(e)) } finally { setBusy(null) }
  }
  useEffect(() => {
    run('load', api.plugins.get).then(() => run('refresh', api.plugins.refresh))   // cached first, then live
  }, [])

  const install = (o: PluginOffer, version?: string) => {
    if (o.edition === 'enterprise' && !v?.licence) { setAskLicenceFor(o); return }
    run(`install:${o.id}`, () => api.plugins.install(o.id, version))
  }
  const saveLicence = async () => {
    await run('licence', () => api.plugins.licence(licenceText))
    setLicenceText('')
    if (askLicenceFor) { const o = askLicenceFor; setAskLicenceFor(null); run(`install:${o.id}`, () => api.plugins.install(o.id)) }
  }
  const restart = async () => {
    setRestarting(true)
    try { await api.plugins.restart() } catch (e) { setError(errText(e)); setRestarting(false); return }
    const t0 = Date.now()
    const poll = async () => {                       // wait for the backend to come back, then reload
      try { await api.plugins.get(); window.location.reload() }
      catch { if (Date.now() - t0 < 120_000) setTimeout(poll, 1500); else setRestarting(false) }
    }
    setTimeout(poll, 2500)
  }

  const count = { available: v?.available.length ?? 0, installed: (v?.installed.length ?? 0) + (v?.bundled.length ?? 0),
                  updates: v?.updates.length ?? 0 }

  return (
    <Modal wide onClose={onClose} title={<span className="flex items-center gap-2">Plugins
      {v && <span className="text-xs font-normal text-subtle">SFS {v.sfs_version} · {v.platform} · {v.python_abi}</span>}</span>}>
      <div className="flex flex-col gap-3 h-full min-h-0 text-xs text-slate-300">
        {v?.safe_mode && (
          <div className="rounded-lg border border-amber-500/30 bg-amber-950/30 p-2.5 flex items-center gap-2 text-amber-100">
            <ShieldAlert size={14} /> Safe mode: no plugin loads at startup.
            <button className="btn-ghost text-xs ml-auto" onClick={() => run('safe', () => api.plugins.safeMode(false))}>Turn off</button>
          </div>
        )}
        {!!v?.restart_needed.length && (
          <div className="rounded-lg border border-accent/30 bg-accent/10 p-2.5 flex items-center gap-2 text-slate-200">
            <Power size={13} className="text-accent" />
            <span className="flex-1">Restart SFS to apply: {v.restart_needed.join(', ')}.</span>
            {v.can_restart
              ? <button className="btn-pri text-xs" disabled={restarting} onClick={restart}>{restarting ? 'Restarting…' : 'Restart now'}</button>
              : <span className="text-subtle">Stop and start the backend.</span>}
          </div>
        )}
        {error && <div className="rounded-lg border border-red-500/30 bg-red-950/30 p-2.5 text-red-200 break-words">{error}</div>}

        <div className="flex items-center gap-1">
          {(['installed', 'available', 'updates'] as Tab[]).map((t) => (
            <button key={t} onClick={() => setTab(t)}
              className={`rounded-md px-3 py-1 capitalize ${tab === t ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-300'}`}>
              {t}{count[t] ? ` (${count[t]})` : ''}
            </button>
          ))}
          <span className="flex-1" />
          <span className="text-subtle">
            {v?.catalog.error ? <span className="text-amber-300" title={v.catalog.error}>Catalog offline</span>
              : v?.catalog.generated_at ? `Catalog ${new Date(v.catalog.generated_at).toLocaleDateString()}` : 'No catalog yet'}
          </span>
          <button className="btn-ghost text-xs gap-1" disabled={!!busy} onClick={() => run('refresh', api.plugins.refresh)}
                  title={v?.catalog.url}>
            <RefreshCw size={11} className={busy === 'refresh' ? 'animate-spin' : ''} /> Check
          </button>
          <button className="btn-ghost text-xs gap-1" onClick={() => fileRef.current?.click()} title="Install a signed pack file (offline)">
            <Upload size={11} /> From file
          </button>
          <input ref={fileRef} type="file" accept=".zip" className="hidden"
                 onChange={(e) => { const f = e.target.files?.[0]; if (f) run('upload', () => api.plugins.upload(f)); e.target.value = '' }} />
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto space-y-1.5">
          {!v && <div className="text-subtle">Loading…</div>}
          {v && tab === 'installed' && (<>
            {v.installed.map((p) => <InstalledRow key={p.id} p={p} busy={busy} run={run} />)}
            {v.bundled.map((b) => (
              <div key={b.id} className="rounded-lg border border-canvas-border p-2.5 flex items-center gap-2">
                <span className="text-slate-200 font-medium">{b.id}</span>
                <span className="text-subtle">{b.version}</span>
                <span className="text-[11px] rounded px-1.5 py-0.5 bg-slate-500/15 text-slate-300">Bundled</span>
                {b.error && <span className="text-[11px] rounded px-1.5 py-0.5 bg-red-500/15 text-red-300" title={b.error}>
                  {b.error === 'safe mode' ? 'Not loaded (safe mode)' : 'Failed'}</span>}
                <span className="flex-1" />
                <span className="text-subtle">ships with SFS</span>
              </div>
            ))}
            {!v.installed.length && !v.bundled.length && <div className="text-subtle">No plugins.</div>}
          </>)}
          {v && tab === 'available' && (
            v.available.length ? v.available.map((o) => (
              <OfferRow key={o.id} o={o} action={<button className="btn-pri text-xs gap-1" disabled={!!busy} onClick={() => install(o)}>
                <Download size={11} /> {busy === `install:${o.id}` ? 'Installing…' : `Install ${o.versions[0]}`}</button>} />
            )) : <div className="text-subtle">{v.catalog.generated_at ? 'Everything the catalog offers for this SFS is installed.'
              : 'Check the catalog to see what can be installed (needs a connection).'}</div>
          )}
          {v && tab === 'updates' && (
            v.updates.length ? v.updates.map((o) => (
              <OfferRow key={o.id} o={o} sub={`installed ${o.installed} → ${o.versions[0]}`}
                action={<button className="btn-pri text-xs gap-1" disabled={!!busy} onClick={() => install(o)}>
                  <Download size={11} /> {busy === `install:${o.id}` ? 'Updating…' : 'Update'}</button>} />
            )) : <div className="text-subtle">No updates for this SFS version.</div>
          )}
        </div>

        {(askLicenceFor || tab !== 'installed' || v?.licence) && (
          <div className="border-t border-canvas-border pt-3 space-y-2">
            <div className="flex items-center gap-2 text-slate-200"><KeyRound size={13} className="text-violet-300" /> Enterprise licence</div>
            {v?.licence && !v.licence.error ? (
              <div className="flex items-center gap-2">
                <span>{v.licence.evaluation ? 'Evaluation licence' : 'Licence'} for <strong>{v.licence.licensee}</strong>
                  {v.licence.expires ? `, until ${v.licence.expires}` : ', no expiry'}
                  {v.licence.features?.length ? ` · ${v.licence.features.join(', ')}` : ''}
                  {v.licence.expired && <span className="text-red-300"> · expired</span>}</span>
                <span className="flex-1" />
                <button className="btn-ghost text-xs" onClick={() => run('licence', api.plugins.removeLicence)}>Remove</button>
              </div>
            ) : (
              <div className="space-y-1.5">
                {askLicenceFor && <div className="text-violet-200">{askLicenceFor.name ?? askLicenceFor.id} is an Enterprise plugin: paste your licence key to install it.</div>}
                {v?.licence?.error && <div className="text-red-300">{v.licence.error}</div>}
                <div className="flex gap-2">
                  <input className="input text-xs font-mono flex-1" placeholder="SFS-LICENSE-1.…" value={licenceText}
                         onChange={(e) => setLicenceText(e.target.value)} />
                  <button className="btn-pri text-xs" disabled={!licenceText.trim() || !!busy} onClick={saveLicence}>Save</button>
                </div>
              </div>
            )}
          </div>
        )}

        <div className="border-t border-canvas-border pt-2 flex items-center gap-2 text-subtle">
          <span>Plugins come only from the official catalog, signed by Sidewinder Labs.</span>
          <span className="flex-1" />
          {v && !v.safe_mode && (
            <button className="btn-ghost text-xs gap-1" onClick={() => run('safe', () => api.plugins.safeMode(true))}
                    title="Start SFS with no plugins, e.g. if one stops it from working">
              <ShieldAlert size={11} /> Next start: safe mode
            </button>
          )}
        </div>
      </div>
    </Modal>
  )
}

function OfferRow({ o, action, sub }: { o: PluginOffer; action: React.ReactNode; sub?: string }) {
  return (
    <div className="rounded-lg border border-canvas-border p-2.5 flex items-start gap-3">
      <div className="flex-1 min-w-0 space-y-0.5">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-slate-200 font-medium">{o.name ?? o.id}</span>
          <Edition e={o.edition} /><Official />
          <span className="text-subtle">{sub ?? o.versions[0]}</span>
        </div>
        {o.description && <div className="text-muted">{o.description}</div>}
      </div>
      {action}
    </div>
  )
}

function InstalledRow({ p, busy, run }: { p: InstalledPack; busy: string | null
  run: (label: string, fn: () => Promise<PluginsView>) => Promise<void> }) {
  const failed = !!p.error
  return (
    <div className={`rounded-lg border p-2.5 space-y-1 ${failed ? 'border-red-500/30 bg-red-950/10' : 'border-canvas-border'}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-slate-200 font-medium">{p.name ?? p.id}</span>
        <span className="text-subtle">{p.version}</span>
        <Edition e={p.edition} /><Official />
        {failed && <span className="text-[11px] rounded px-1.5 py-0.5 bg-red-500/15 text-red-300">Failed</span>}
        {!failed && !p.enabled && <span className="text-[11px] rounded px-1.5 py-0.5 bg-slate-500/15 text-slate-400">Disabled</span>}
        {p.enabled && p.loaded && <span className="text-[11px] text-emerald-300">running</span>}
        <span className="flex-1" />
        {p.enabled
          ? <button className="btn-ghost text-xs" disabled={!!busy} onClick={() => run('act', () => api.plugins.act(p.id, 'disable'))}>Disable</button>
          : <button className="btn-ghost text-xs" disabled={!!busy} onClick={() => run('act', () => api.plugins.act(p.id, 'enable'))}>Enable</button>}
        {p.previous && (
          <button className="btn-ghost text-xs gap-1" disabled={!!busy} title={`Go back to ${p.previous}`}
                  onClick={() => run('act', () => api.plugins.act(p.id, 'rollback'))}><RotateCcw size={11} /> {p.previous}</button>
        )}
        <button className="btn-ghost text-xs text-red-300" disabled={!!busy} title="Remove"
                onClick={() => run('act', () => api.plugins.act(p.id, 'remove'))}><Trash2 size={11} /></button>
      </div>
      {p.description && <div className="text-muted">{p.description}</div>}
      {p.error && <div className="text-red-300 break-words">{p.error}</div>}
      {p.notice && <div className="text-amber-300">{p.notice}</div>}
    </div>
  )
}
