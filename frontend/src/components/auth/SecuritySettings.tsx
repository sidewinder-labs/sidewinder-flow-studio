import { useEffect, useState } from 'react'
import { Check, Copy, KeyRound, LogOut, Plus, Trash2 } from 'lucide-react'
import { api, errorMessage, type ApiTokenRow } from '@/utils/api'
import { useAuth } from './AuthGate'

/** Meta → Settings → Security: who is signed in, sign out, the password and
 *  API tokens. Hidden when sign-in is off. */
export function SecuritySettings() {
  const { status, signOut } = useAuth()
  if (!status?.sign_in_required) return null
  const isToken = status.user?.kind === 'token'
  return (
    <section className="space-y-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted">Security</h3>
      <div className="rounded-lg border border-canvas-border bg-surface p-2.5 flex items-center gap-2">
        <span className="flex-1 text-slate-300">Signed in{status.user ? ` as ${status.user.name}` : ''}</span>
        {!isToken && <button className="btn-ghost text-xs" onClick={signOut}><LogOut size={12} /> Sign out</button>}
      </div>
      {status.methods.some((m) => m.type === 'password') && <PasswordChange fromEnv={!!status.password_from_env} />}
      <ApiTokens />
      <p className="text-[11px] text-subtle">
        The password decides who gets in, not what they can do: anyone signed in can run code on the server.
      </p>
    </section>
  )
}

function PasswordChange({ fromEnv }: { fromEnv: boolean }) {
  const [open, setOpen] = useState(false)
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  if (fromEnv) {
    return <p className="text-[11px] text-subtle">The password comes from SFS_PASSWORD: change it there and restart SFS.</p>
  }
  if (!open) {
    return (
      <div className="flex items-center gap-2">
        <button className="btn-ghost text-xs border border-canvas-border" onClick={() => { setOpen(true); setMsg(null) }}>
          <KeyRound size={12} /> Change password
        </button>
        {msg?.ok && <span className="text-status-ok text-[11px]">{msg.text}</span>}
      </div>
    )
  }
  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setMsg(null)
    try {
      await api.auth.changePassword(current, next)
      setOpen(false); setCurrent(''); setNext(''); setAgain('')
      setMsg({ ok: true, text: 'Password changed. Other browsers must sign in again.' })
    } catch (err) { setMsg({ ok: false, text: errorMessage(err) }) } finally { setBusy(false) }
  }
  return (
    <form onSubmit={save} className="rounded-lg border border-canvas-border bg-surface p-2.5 space-y-2">
      <input className="input" type="password" placeholder="Current password" autoComplete="current-password"
        aria-label="Current password" value={current} onChange={(e) => setCurrent(e.target.value)} autoFocus />
      <input className="input" type="password" placeholder="New password (8+ characters)" autoComplete="new-password"
        aria-label="New password" value={next} onChange={(e) => setNext(e.target.value)} />
      <input className="input" type="password" placeholder="New password again" autoComplete="new-password"
        aria-label="New password again" value={again} onChange={(e) => setAgain(e.target.value)} />
      {again && again !== next && <p className="text-[11px] text-status-error">The two new passwords don't match.</p>}
      {msg && !msg.ok && <p className="text-[11px] text-status-error" role="alert">{msg.text}</p>}
      <div className="flex gap-2">
        <button className="btn-pri text-xs" disabled={busy || !current || next.length < 8 || next !== again}>Change password</button>
        <button type="button" className="btn-ghost text-xs" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </form>
  )
}

const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString() : null)

function ApiTokens() {
  const [rows, setRows] = useState<ApiTokenRow[] | null>(null)
  const [name, setName] = useState('')
  const [days, setDays] = useState('')
  const [fresh, setFresh] = useState<{ name: string; token: string } | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async () => { try { setRows(await api.auth.tokens()) } catch (e) { setError(errorMessage(e)) } }
  useEffect(() => { load() }, [])

  const create = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      const t = await api.auth.createToken(name.trim(), days ? Number(days) : undefined)
      setFresh({ name: t.name, token: t.token }); setCopied(false); setName(''); setDays('')
      load()
    } catch (err) { setError(errorMessage(err)) }
  }
  const revoke = async (t: ApiTokenRow) => {
    try { await api.auth.revokeToken(t.id); load() } catch (e) { setError(errorMessage(e)) }
  }
  const copy = async () => {
    if (!fresh) return
    try { await navigator.clipboard.writeText(fresh.token); setCopied(true) } catch { /* select it by hand */ }
  }

  return (
    <div className="rounded-lg border border-canvas-border bg-surface p-2.5 space-y-2">
      <div>
        <div className="text-slate-300 font-medium">API tokens</div>
        <div className="text-[11px] text-subtle">
          For scripts and CI: send <code className="font-mono">Authorization: Bearer &lt;token&gt;</code>. A token can do
          everything you can.
        </div>
      </div>
      {fresh && (
        <div className="rounded border border-status-ok/40 bg-status-ok/10 p-2 space-y-1">
          <div className="text-[11px] text-slate-300">"{fresh.name}" created. Copy it now: it isn't shown again.</div>
          <div className="flex gap-1">
            <input className="input font-mono text-[11px] py-1" readOnly value={fresh.token} aria-label="New token"
              onFocus={(e) => e.currentTarget.select()} />
            <button className="btn-ghost text-xs" onClick={copy} title="Copy">
              {copied ? <Check size={12} className="text-status-ok" /> : <Copy size={12} />}
            </button>
          </div>
        </div>
      )}
      {rows && rows.length > 0 && (
        <ul className="divide-y divide-canvas-border">
          {rows.map((t) => (
            <li key={t.id} className="flex items-center gap-2 py-1.5">
              <div className="flex-1 min-w-0">
                <div className="text-slate-300 truncate">{t.name}</div>
                <div className="text-[11px] text-subtle font-mono">
                  {t.prefix}… · {t.last_used_at ? `used ${day(t.last_used_at)}` : 'never used'}
                  {t.expires_at ? ` · expires ${day(t.expires_at)}` : ''}
                </div>
              </div>
              <button className="btn-ghost text-xs text-status-error" onClick={() => revoke(t)} title={`Revoke ${t.name}`}>
                <Trash2 size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {rows && rows.length === 0 && !fresh && <div className="text-[11px] text-subtle">No tokens yet.</div>}
      <form onSubmit={create} className="flex gap-1">
        <input className="input py-1" placeholder="Name, e.g. CI deploy" aria-label="Token name"
          value={name} onChange={(e) => setName(e.target.value)} />
        <input className="input py-1 w-24" type="number" min={1} placeholder="Days" aria-label="Expires after days"
          title="Expires after this many days. Blank: no expiry." value={days} onChange={(e) => setDays(e.target.value)} />
        <button className="btn-ghost text-xs border border-canvas-border" disabled={!name.trim()}><Plus size={12} /> Create</button>
      </form>
      {error && <p className="text-[11px] text-status-error" role="alert">{error}</p>}
    </div>
  )
}
