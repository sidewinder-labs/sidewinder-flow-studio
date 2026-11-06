/**
 * Shared pieces of the function library UI: a modal frame, state badges and
 * a line diff (for Update and Compare).
 */
import { useEffect } from 'react'
import { X } from 'lucide-react'
import type { CopyState } from '@/utils/api'

export function Modal({ title, onClose, children, wide }: {
  title: React.ReactNode; onClose: () => void; children: React.ReactNode; wide?: boolean
}) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-[9500] bg-black/60 flex items-center justify-center p-4"
         onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`w-full ${wide ? 'max-w-5xl h-[85vh]' : 'max-w-xl max-h-[85vh]'} flex flex-col bg-surface-raised border border-canvas-border rounded-2xl shadow-node`}
           onKeyDown={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-5 py-3 border-b border-canvas-border shrink-0">
          <div className="text-sm font-semibold text-slate-100 flex-1 min-w-0">{title}</div>
          <button className="text-subtle hover:text-muted" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto p-5">{children}</div>
      </div>
    </div>
  )
}

const STATE: Record<CopyState | 'deprecated', [string, string, string]> = {
  current:    ['Library · up to date', 'bg-emerald-500/15 text-emerald-300', 'Same as the library version'],
  update:     ['Update available', 'bg-accent/15 text-accent', 'The library has a newer version'],
  modified:   ['Modified here', 'bg-amber-500/15 text-amber-300', 'Changed in this flow since it was inserted'],
  deleted:    ['Library entry deleted', 'bg-slate-500/15 text-slate-400', 'Removed from the library; this copy keeps working'],
  deprecated: ['Deprecated in library', 'bg-red-500/15 text-red-300', 'Marked deprecated in the library'],
}

export function StateBadge({ state, note }: { state: CopyState | 'deprecated'; note?: string }) {
  const [label, cls, title] = STATE[state]
  return <span className={`text-[11px] rounded px-1.5 py-0.5 whitespace-nowrap ${cls}`} title={note ? `${title}: ${note}` : title}>{label}</span>
}

/** Line diff by longest common subsequence: [' ' | '-' | '+', line]. */
export function lineDiff(a: string, b: string): [' ' | '-' | '+', string][] {
  const x = a.replace(/\s+$/, '').split('\n'), y = b.replace(/\s+$/, '').split('\n')
  const m = x.length, n = y.length
  const L: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0))
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1])
  const out: [' ' | '-' | '+', string][] = []
  let i = 0, j = 0
  while (i < m && j < n) {
    if (x[i] === y[j]) { out.push([' ', x[i]]); i++; j++ }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push(['-', x[i++]])
    else out.push(['+', y[j++]])
  }
  while (i < m) out.push(['-', x[i++]])
  while (j < n) out.push(['+', y[j++]])
  return out
}

export function DiffView({ from, to, fromLabel, toLabel }: { from: string; to: string; fromLabel: string; toLabel: string }) {
  const rows = lineDiff(from, to)
  const changed = rows.some(([op]) => op !== ' ')
  return (
    <div className="space-y-1.5">
      <div className="flex gap-4 text-[11px] text-subtle">
        <span><span className="text-red-300">−</span> {fromLabel}</span>
        <span><span className="text-emerald-300">+</span> {toLabel}</span>
      </div>
      <pre className="text-xs font-mono bg-canvas-bg border border-canvas-border rounded-lg p-2 overflow-x-auto">
        {!changed && <div className="text-subtle px-1">No differences.</div>}
        {changed && rows.map(([op, line], k) => (
          <div key={k} className={op === '-' ? 'bg-red-950/40 text-red-200' : op === '+' ? 'bg-emerald-950/40 text-emerald-200' : 'text-slate-400'}>
            <span className="select-none inline-block w-4 text-center">{op === ' ' ? '' : op}</span>{line || ' '}
          </div>
        ))}
      </pre>
    </div>
  )
}

/** The API's error text without the "API 400:" prefix and JSON wrapping. */
export function errText(e: unknown): string {
  const m = String((e as any)?.message ?? e)
  return m.match(/"detail":"([^"]+)"/)?.[1] ?? m.replace(/^API \d+: /, '')
}
