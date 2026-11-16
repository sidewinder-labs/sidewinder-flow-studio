/**
 * View as Python / Convert to code node — for engines that declare `to_code`.
 *
 * The backend generates plain pandas-node code doing the same work as the
 * engine's form (using the last run's upstream frames where it needs them).
 * View shows it read-only; Convert switches the node to the pandas engine with
 * that code in the cell.
 */
import { useEffect, useRef, useState } from 'react'
import Editor from '@monaco-editor/react'
import { Code2, Copy, X, Wand2 } from 'lucide-react'
import { api } from '@/utils/api'
import { useTheme, monacoTheme } from '@/hooks/useTheme'

function errorText(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e)
  const body = msg.replace(/^API \d+: /, '')
  try { return JSON.parse(body).detail ?? body } catch { return body }
}

export function ViewAsPython({ engine, engineLabel, data, projectId, parentIds, onConvert }: {
  engine: string
  engineLabel: string
  data: Record<string, unknown>
  projectId?: string
  parentIds: string[]
  onConvert: (code: string) => void
}) {
  const [code, setCode] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const generate = async () => {
    setBusy(true); setError('')
    try {
      const r = await api.toCode({ engine, node_data: data, project_id: projectId, parent_ids: parentIds })
      return r.code
    } catch (e) {
      setError(errorText(e))
      return null
    } finally {
      setBusy(false)
    }
  }

  const view = async () => { const c = await generate(); if (c) setCode(c) }
  const convert = async (c?: string | null) => {
    const generated = c ?? await generate()
    if (!generated) return
    if (!window.confirm(`Switch this node to the pandas engine with the generated code?\n\n` +
                        `The ${engineLabel} form settings stay in the node but are no longer used.`)) return
    setCode(null)
    onConvert(generated)
  }

  return (
    <div className="shrink-0 space-y-1.5">
      <div className="flex gap-2">
        <button className="btn-ghost border border-canvas-border flex-1 text-xs" onClick={view} disabled={busy}
          title="Show this node's work as plain Python (read-only)">
          <Code2 size={12} /> View as Python
        </button>
        <button className="btn-ghost border border-canvas-border flex-1 text-xs" onClick={() => convert()} disabled={busy}
          title="Replace the form with the equivalent code in a pandas node">
          <Wand2 size={12} /> Convert to code node
        </button>
      </div>
      {error && <div className="text-xs text-red-400" role="alert">{error}</div>}
      {code !== null && (
        <CodeDialog title={`${engineLabel} as Python`} code={code}
          onClose={() => setCode(null)} onConvert={() => convert(code)} />
      )}
    </div>
  )
}

function CodeDialog({ title, code, onClose, onConvert }: {
  title: string; code: string; onClose: () => void; onConvert: () => void
}) {
  const theme = useTheme()
  const ref = useRef<HTMLDivElement>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const copy = async () => {
    try { await navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1500) }
    catch { /* clipboard unavailable */ }
  }

  return (
    <div className="fixed inset-0 z-[9998] bg-black/60 flex items-center justify-center p-4"
      onMouseDown={(e) => { if (!ref.current?.contains(e.target as Node)) onClose() }}>
      <div ref={ref} role="dialog" aria-label={title}
        className="w-full max-w-4xl h-[80vh] bg-surface-raised border border-canvas-border rounded-2xl shadow-node p-4 flex flex-col gap-3"
        onKeyDown={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 shrink-0">
          <Code2 size={14} className="text-accent" />
          <h2 className="text-sm font-semibold text-slate-100 flex-1">{title}</h2>
          <button className="btn-ghost text-xs" onClick={copy}><Copy size={12} /> {copied ? 'Copied' : 'Copy'}</button>
          <button className="btn-ghost text-xs" onClick={onConvert}><Wand2 size={12} /> Convert to code node</button>
          <button className="text-subtle hover:text-muted" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>
        <p className="text-xs text-muted shrink-0">
          The same work as the form, as plain code. Read-only here — convert the node to edit it.
        </p>
        <div className="flex-1 min-h-0 rounded-lg overflow-hidden border border-canvas-border">
          <Editor height="100%" defaultLanguage="python" value={code} theme={monacoTheme(theme)}
            options={{ readOnly: true, minimap: { enabled: false }, fontSize: 12, scrollBeyondLastLine: false }} />
        </div>
      </div>
    </div>
  )
}
