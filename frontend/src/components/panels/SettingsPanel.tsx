import { useEffect, useMemo, useState } from 'react'
import { Lock, Power, RotateCcw } from 'lucide-react'
import { api, type AppSetting, type SettingsView } from '@/utils/api'
import { setThemeChoice, useThemeChoice, type ThemeChoice } from '@/hooks/useTheme'

/** Meta → Settings: SFS settings edited in the app, no config file needed.
 *  A value set by the environment wins and is shown locked; secrets are never
 *  sent back, only whether one is stored. */
export function SettingsPanel() {
  const [v, setV] = useState<SettingsView | null>(null)
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const [saved, setSaved] = useState(false)

  const load = async () => { try { setV(await api.settings.get()) } catch (e) { setErrors({ _: String(e) }) } }
  useEffect(() => { load() }, [])

  const groups = useMemo(() => {
    const out: [string, AppSetting[]][] = []
    for (const s of v?.settings ?? []) {
      const g = out.find(([name]) => name === s.group)
      g ? g[1].push(s) : out.push([s.group, [s]])
    }
    return out
  }, [v])

  const dirty = Object.keys(draft).length > 0
  const set = (key: string, value: unknown) => { setSaved(false); setDraft((d) => ({ ...d, [key]: value })) }

  const save = async () => {
    setBusy(true); setErrors({})
    try {
      setV(await api.settings.save(draft)); setDraft({}); setSaved(true)
    } catch (e) {
      const m = String((e as Error)?.message ?? e).match(/^API \d+: (.*)$/s)
      try { setErrors(JSON.parse(m?.[1] ?? '').detail.errors) } catch { setErrors({ _: String(e) }) }
    } finally { setBusy(false) }
  }

  const restart = async () => {
    setRestarting(true)
    try { await api.settings.restart() } catch (e) { setErrors({ _: String(e) }); setRestarting(false); return }
    const t0 = Date.now()
    const poll = async () => {                       // wait for the backend to come back, then reload
      try { await api.settings.get(); window.location.reload() }
      catch { if (Date.now() - t0 < 120_000) setTimeout(poll, 1500); else setRestarting(false) }
    }
    setTimeout(poll, 2500)
  }

  if (!v) return <p className="text-xs text-subtle">{errors._ ?? 'Loading…'}</p>

  return (
    <div className="space-y-3 text-xs">
      <Appearance />
      <MemoryCard m={v.memory} />

      {v.restart_needed.length > 0 && (
        <div className="rounded-lg border border-accent/30 bg-accent/10 p-2 flex items-center gap-2 text-slate-200">
          <Power size={13} className="text-accent shrink-0" />
          <span className="flex-1">Restart SFS to apply: {v.restart_needed.join(', ')}.</span>
          {v.can_restart
            ? <button className="btn-pri text-xs" disabled={restarting} onClick={restart}>{restarting ? 'Restarting…' : 'Restart now'}</button>
            : <span className="text-subtle">Stop and start the backend.</span>}
        </div>
      )}
      {errors._ && <div className="rounded border border-red-500/30 bg-red-950/30 p-2 text-red-200 break-words">{errors._}</div>}

      {groups.map(([group, items]) => (
        <section key={group} className="space-y-2">
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted">{group}</h3>
          {items.map((s) => (
            <Field key={s.key} s={s} draft={draft} error={errors[s.key]} onChange={(val) => set(s.key, val)} />
          ))}
        </section>
      ))}

      <div className="sticky bottom-0 bg-canvas-bg/95 py-2 flex items-center gap-2 border-t border-canvas-border">
        <button className="btn-pri flex-1" disabled={!dirty || busy} onClick={save}>{busy ? 'Saving…' : 'Save'}</button>
        <button className="btn-ghost" disabled={!dirty || busy} onClick={() => { setDraft({}); setErrors({}) }}>Discard</button>
        {saved && !dirty && <span className="text-status-ok">Saved</span>}
      </div>
      <p className="text-[11px] text-subtle">
        Data folders, the metadata database and the secret key stay in the environment: SFS needs them to start.
      </p>
    </div>
  )
}

const THEMES: [ThemeChoice, string][] = [['dark', 'Dark'], ['light', 'Light'], ['system', 'Match system']]

/** The colour theme: a preference of this browser, applied at once. */
function Appearance() {
  const choice = useThemeChoice()
  return (
    <section className="space-y-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted">Appearance</h3>
      <div className="flex gap-1 p-0.5 rounded-lg bg-canvas-bg border border-canvas-border" role="radiogroup" aria-label="Theme">
        {THEMES.map(([value, label]) => (
          <button key={value} role="radio" aria-checked={choice === value} onClick={() => setThemeChoice(value)}
            className={`flex-1 py-1 rounded-md transition-colors ${choice === value
              ? 'bg-surface-raised text-accent font-semibold' : 'text-muted hover:text-slate-200'}`}>
            {label}
          </button>
        ))}
      </div>
      <p className="text-[11px] text-subtle">Saved in this browser.</p>
    </section>
  )
}

function Field({ s, draft, error, onChange }: {
  s: AppSetting; draft: Record<string, unknown>; error?: string; onChange: (v: unknown) => void
}) {
  const inDraft = s.key in draft
  const current = inDraft ? draft[s.key] : s.value
  const placeholder = s.placeholder || (s.default != null ? String(s.default) : '')
  const disabled = s.locked
  let input: React.ReactNode

  if (s.type === 'bool') {
    const checked = Boolean(current ?? s.default)
    input = (
      <label className="flex items-center gap-2 text-slate-300">
        <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
        {s.label}
      </label>
    )
  } else if (s.type === 'secret') {
    const clearing = inDraft && draft[s.key] === null
    input = (
      <div className="flex gap-1">
        <input type="password" className="input flex-1" autoComplete="new-password" disabled={disabled || clearing}
               value={inDraft && typeof draft[s.key] === 'string' ? String(draft[s.key]) : ''}
               placeholder={clearing ? 'will be cleared' : s.is_set ? 'stored: leave blank to keep' : ''}
               onChange={(e) => onChange(e.target.value)} />
        {s.is_set && !disabled && (
          <button className="btn-ghost text-[11px]" title="Remove the stored value" onClick={() => onChange(null)}>Clear</button>
        )}
      </div>
    )
  } else {
    input = (
      <input className="input font-mono text-xs" disabled={disabled} placeholder={placeholder}
             type={s.type === 'int' || s.type === 'float' ? 'number' : 'text'}
             value={current == null ? '' : String(current)} onChange={(e) => onChange(e.target.value)} />
    )
  }

  return (
    <div className={`rounded-lg border p-2 space-y-1 ${error ? 'border-red-500/50' : 'border-canvas-border'}`}>
      {s.type !== 'bool' && (
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-slate-200 flex-1">{s.label}</span>
          {s.applies === 'restart' && <span className="rounded bg-surface-raised px-1 text-[10px] text-muted">restart</span>}
          {s.source === 'app' && !inDraft && !s.locked && (
            <button className="text-subtle hover:text-slate-300" title="Back to the default" onClick={() => onChange(null)}>
              <RotateCcw size={11} />
            </button>
          )}
        </div>
      )}
      {input}
      {s.locked && (
        <div className="flex items-center gap-1 text-amber-300/90 text-[11px]">
          <Lock size={10} /> Set by the environment ({s.key}); change it there.
        </div>
      )}
      {error && <div className="text-red-300 text-[11px]">{error}</div>}
      {s.help && <div className="text-[11px] text-subtle leading-snug">{s.help}</div>}
    </div>
  )
}

function MemoryCard({ m }: { m: SettingsView['memory'] }) {
  const gb = (n: number | null) => n == null ? '?' : `${(n / 2 ** 30).toFixed(1)} GB`
  if (m.total == null) return null
  const pct = (n: number | null) => n == null ? 0 : Math.min(100, (n / m.total!) * 100)
  return (
    <div className="rounded-lg border border-canvas-border p-2 space-y-1.5">
      <div className="flex justify-between text-muted">
        <span>Memory: {gb(m.total)}</span>
        <span>SFS uses {gb(m.used)} · budget {m.budget == null ? 'off' : gb(m.budget)}</span>
      </div>
      <div className="relative h-1.5 rounded bg-surface-raised overflow-hidden">
        <div className="absolute inset-y-0 left-0 bg-accent" style={{ width: `${pct(m.used)}%` }} />
        {m.budget != null && <div className="absolute inset-y-0 w-0.5 bg-amber-400" style={{ left: `${pct(m.budget)}%` }} />}
      </div>
    </div>
  )
}
