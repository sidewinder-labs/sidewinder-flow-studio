/**
 * AboutDialog — short description of SFS, version, and credits.
 * Opened by clicking the SFS logo in the top bar.
 */
import { useEffect, useRef, useState } from 'react'
import { X, GitBranch, Zap, Database, Sparkles, HardDrive } from 'lucide-react'
import { useFeatures } from '@/utils/engines'
import { api, type DesktopInfo } from '@/utils/api'
import { DataLocations } from '@/components/desktop/DataLocations'

export const SFS_VERSION = '1.37'

export function AboutDialog({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const { desktop } = useFeatures()
  const [info, setInfo] = useState<DesktopInfo | null>(null)
  useEffect(() => { if (desktop) api.desktop.info().then(setInfo).catch(() => {}) }, [desktop])
  const showWelcome = async () => {
    await api.desktop.showWelcomeAgain().catch(() => {})
    onClose()
    window.dispatchEvent(new Event('sfs:show-welcome'))
  }

  useEffect(() => {
    const handler = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent && e.key !== 'Escape') return
      if (e instanceof MouseEvent && ref.current?.contains(e.target as Node)) return
      onClose()
    }
    window.addEventListener('mousedown', handler)
    window.addEventListener('keydown', handler)
    return () => {
      window.removeEventListener('mousedown', handler)
      window.removeEventListener('keydown', handler)
    }
  }, [onClose])

  return (
    <div className="fixed inset-0 z-[9998] bg-black/60 flex items-center justify-center p-4">
      <div
        ref={ref}
        className={`w-full ${info ? 'max-w-xl' : 'max-w-md'} bg-surface-raised border border-canvas-border rounded-2xl shadow-node p-6 space-y-4`}
      >
        {/* Header */}
        <div className="flex items-start gap-3">
          <div className="w-11 h-11 rounded-xl bg-gradient-to-br from-accent to-blue-600 flex items-center justify-center shrink-0">
            <span className="text-canvas-bg text-sm font-bold">FS</span>
          </div>
          <div className="flex-1">
            <h2 className="text-base font-semibold text-slate-100">Sidewinder Flow Studio</h2>
            <div className="text-xs text-muted font-mono">v{SFS_VERSION}</div>
          </div>
          <button className="text-subtle hover:text-muted" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        {/* Description */}
        <p className="text-sm text-slate-300 leading-relaxed">
          A visual Python notebook-as-DAG for building, debugging, and scheduling data
          pipelines. Think NiFi's canvas meets Jupyter's cells — every node is code,
          data flows as Arrow between nodes, and you can inspect any edge mid-flight.
        </p>

        {/* Feature list */}
        <div className="space-y-2 text-xs text-muted">
          <div className="flex items-start gap-2">
            <GitBranch size={13} className="text-accent shrink-0 mt-0.5" />
            <span>Branching DAGs with per-branch stops, taps, and surgical re-runs from any node</span>
          </div>
          <div className="flex items-start gap-2">
            <Database size={13} className="text-accent shrink-0 mt-0.5" />
            <span>Per-node engines — pandas for flexibility, DuckDB SQL over Arrow (zero-copy) for volume</span>
          </div>
          <div className="flex items-start gap-2">
            <Sparkles size={13} className="text-accent shrink-0 mt-0.5" />
            <span>AI agent generates transforms and charts from your live data schema — local (Ollama) or cloud (Anthropic)</span>
          </div>
          <div className="flex items-start gap-2">
            <Zap size={13} className="text-accent shrink-0 mt-0.5" />
            <span>Cron scheduling, encrypted connections, live row counts, tables and charts inline</span>
          </div>
        </div>

        {/* Desktop edition: where is my data */}
        {info && (
          <div className="pt-3 border-t border-canvas-border space-y-2">
            <div className="flex items-center gap-2 text-sm font-medium text-slate-200">
              <HardDrive size={14} className="text-accent" /> Where is my data
            </div>
            <DataLocations info={info} />
            <button className="text-xs text-accent hover:underline" onClick={showWelcome}>
              Show the welcome screen again
            </button>
          </div>
        )}

        {/* Footer */}
        <div className="pt-2 border-t border-canvas-border flex items-center justify-between text-xs text-subtle">
          <span>Named for the snake — and the missile. 🐍</span>
          <span className="font-mono">Arrow · FastAPI · React Flow</span>
        </div>
      </div>
    </div>
  )
}
