import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { KeyRound, Loader2, ShieldAlert } from 'lucide-react'
import { api, AUTH_REQUIRED, errorMessage, type AuthStatus } from '@/utils/api'

/** Sign-in around the whole app.
 *
 *  Before the first sign-in it shows the sign-in (or first-run setup) screen
 *  instead of the app. When a session ends later (an API call answers 401),
 *  the same screen appears over the app, so unsaved work on the canvas stays.
 *  With sign-in off (desktop, SFS_AUTH=none) it renders the app straight away. */

interface AuthContext { status: AuthStatus | null; refresh: () => Promise<void>; signOut: () => Promise<void> }
const Ctx = createContext<AuthContext>({ status: null, refresh: async () => {}, signOut: async () => {} })
export const useAuth = () => useContext(Ctx)

export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [unreachable, setUnreachable] = useState<string | null>(null)
  const [everIn, setEverIn] = useState(false)
  const [lost, setLost] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const s = await api.auth.status()
      setStatus(s); setUnreachable(null)
      if (s.signed_in) { setEverIn(true); setLost(false) }
    } catch (e) { setUnreachable(errorMessage(e)) }
  }, [])

  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    const onLost = () => { setLost(true); refresh() }
    window.addEventListener(AUTH_REQUIRED, onLost)
    return () => window.removeEventListener(AUTH_REQUIRED, onLost)
  }, [refresh])

  const signOut = useCallback(async () => {
    try { await api.auth.logout() } finally { window.location.reload() }   // drop everything held in memory
  }, [])

  if (!status) {
    return (
      <Screen>
        {unreachable
          ? <p className="text-sm text-status-error">SFS isn't answering: {unreachable}</p>
          : <Loader2 className="animate-spin text-muted mx-auto" size={20} />}
      </Screen>
    )
  }

  const signedIn = status.signed_in && !lost
  return (
    <Ctx.Provider value={{ status, refresh, signOut }}>
      {everIn && children}
      {!signedIn && (
        <div className={everIn ? 'fixed inset-0 z-[100] bg-canvas-bg/90 backdrop-blur-sm' : ''}>
          <Screen>
            {status.needs_setup
              ? <SetupForm onDone={refresh} />
              : <SignInForm status={status} again={everIn} onDone={refresh} />}
          </Screen>
        </div>
      )}
    </Ctx.Provider>
  )
}

function Screen({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen w-full flex items-center justify-center bg-canvas-bg p-4">
      <div className="w-full max-w-sm">
        <div className="flex items-center justify-center gap-2 mb-6">
          <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-accent to-blue-600 flex items-center justify-center">
            <span className="text-canvas-bg text-sm font-bold">FS</span>
          </div>
          <span className="text-base font-semibold text-slate-200">Sidewinder Flow Studio</span>
        </div>
        <div className="panel space-y-4">{children}</div>
        <PlainHttpWarning />
      </div>
    </div>
  )
}

/** A password sent over plain HTTP across a network can be read on the way. */
function PlainHttpWarning() {
  const { protocol, hostname } = window.location
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(hostname)
  if (protocol === 'https:' || local) return null
  return (
    <p className="mt-3 flex gap-2 text-[11px] text-amber-300">
      <ShieldAlert size={13} className="shrink-0 mt-px" />
      <span>This connection isn't encrypted, so the password can be read on the network.
        Put SFS behind HTTPS (a reverse proxy such as Caddy or Traefik).</span>
    </p>
  )
}

function useSubmit(action: () => Promise<unknown>, onDone: () => void) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setError(null)
    try { await action(); onDone() } catch (err) { setError(errorMessage(err)) } finally { setBusy(false) }
  }
  return { busy, error, submit }
}

function SignInForm({ status, again, onDone }: { status: AuthStatus; again: boolean; onDone: () => void }) {
  const [password, setPassword] = useState('')
  const { busy, error, submit } = useSubmit(() => api.auth.login(password), onDone)
  const hasPassword = status.methods.some((m) => m.type === 'password')
  return (
    <form onSubmit={submit} className="space-y-3">
      <div>
        <h1 className="text-sm font-semibold text-slate-100">{again ? 'Your session ended' : 'Sign in'}</h1>
        <p className="text-xs text-muted mt-0.5">
          {again ? 'Sign in again to carry on. Unsaved changes on the canvas are still here.'
                 : 'This SFS is protected with a password.'}
        </p>
      </div>
      {hasPassword ? (
        <>
          <div>
            <label className="label" htmlFor="sfs-password">Password</label>
            <input id="sfs-password" className="input" type="password" autoFocus autoComplete="current-password"
              value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          {error && <p className="text-xs text-status-error" role="alert">{error}</p>}
          <button className="btn-pri w-full justify-center" disabled={busy || !password}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : <KeyRound size={14} />} Sign in
          </button>
          <p className="text-[11px] text-muted">
            Forgot it? On the server, run <code className="font-mono">python -m app.auth set-password</code>
            {' '}(in Docker: <code className="font-mono">docker compose exec -it backend python -m app.auth set-password</code>).
          </p>
        </>
      ) : (
        <p className="text-xs text-muted">No sign-in method is available. Check the SFS log.</p>
      )}
    </form>
  )
}

function SetupForm({ onDone }: { onDone: () => void }) {
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [again, setAgain] = useState('')
  const mismatch = again.length > 0 && again !== password
  const { busy, error, submit } = useSubmit(() => api.auth.setup(code, password), onDone)
  return (
    <form onSubmit={submit} className="space-y-3">
      <div>
        <h1 className="text-sm font-semibold text-slate-100">Set a password for this SFS</h1>
        <p className="text-xs text-muted mt-0.5">
          Anyone who can open this page could otherwise use SFS, and node code runs on the server.
        </p>
      </div>
      <div>
        <label className="label" htmlFor="sfs-setup-code">Setup code</label>
        <input id="sfs-setup-code" className="input font-mono uppercase tracking-widest" autoFocus autoComplete="off"
          placeholder="ABCD-EFGH" value={code} onChange={(e) => setCode(e.target.value)} />
        <p className="text-[11px] text-muted mt-1">
          SFS prints it to its log when it starts. In Docker: <code className="font-mono">docker compose logs backend</code>
        </p>
      </div>
      <div>
        <label className="label" htmlFor="sfs-new-password">New password</label>
        <input id="sfs-new-password" className="input" type="password" autoComplete="new-password"
          value={password} onChange={(e) => setPassword(e.target.value)} />
        <p className="text-[11px] text-muted mt-1">At least 8 characters. A few unrelated words work well.</p>
      </div>
      <div>
        <label className="label" htmlFor="sfs-new-password-2">Same password again</label>
        <input id="sfs-new-password-2" className="input" type="password" autoComplete="new-password"
          value={again} onChange={(e) => setAgain(e.target.value)} />
        {mismatch && <p className="text-[11px] text-status-error mt-1">The two passwords don't match.</p>}
      </div>
      {error && <p className="text-xs text-status-error" role="alert">{error}</p>}
      <button className="btn-pri w-full justify-center" disabled={busy || !code || password.length < 8 || password !== again}>
        {busy ? <Loader2 size={14} className="animate-spin" /> : <KeyRound size={14} />} Set password and sign in
      </button>
    </form>
  )
}
