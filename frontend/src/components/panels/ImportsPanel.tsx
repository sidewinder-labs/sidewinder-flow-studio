import { useState, useEffect } from 'react'
import Editor from '@monaco-editor/react'
import { useStore } from '@/store'
import { useHelpData } from '@/utils/engines'
import { openHelp } from '@/components/help/HelpPanel'

const DEFAULT_IMPORTS = 'import pandas as pd\nimport sqlalchemy as sa\n'
const FUNCTIONS_HINT = `# Define reusable functions here — they're available in EVERY node of this flow.
# (Node code runs in its own scope, so put shared helpers here instead of
#  redefining them in each processor.)
#
# def classify_risk(row):
#     if row["dpd"] > 90: return "default"
#     if row["dpd"] > 30: return "watch"
#     return "current"
#
# def normalize_msisdn(s):
#     import re
#     s = re.sub(r"\\D", "", str(s))
#     return s[-9:] if len(s) >= 9 else None
#
# Then in any node:  df["risk"] = df.apply(classify_risk, axis=1)
`

export function ImportsPanel() {
  const { activeProject, activeFlowId, updateFlowImports, updateFlowShared } = useStore()
  const { helpers } = useHelpData()
  const flow = activeProject?.flows.find((f) => f.id === activeFlowId)
  const [tab, setTab] = useState<'imports' | 'functions'>('imports')
  const [imports, setImports] = useState(flow?.imports ?? DEFAULT_IMPORTS)
  const [funcs, setFuncs] = useState((flow as any)?.shared_functions ?? '')

  useEffect(() => {
    setImports(flow?.imports ?? DEFAULT_IMPORTS)
    setFuncs((flow as any)?.shared_functions ?? '')
  }, [activeFlowId])

  const save = () => {
    if (!activeFlowId) return
    updateFlowImports(activeFlowId, imports)
    updateFlowShared(activeFlowId, funcs)
  }

  const code = tab === 'imports' ? imports : funcs
  const setCode = tab === 'imports' ? setImports : setFuncs

  return (
    <div className="p-4 space-y-3 h-full flex flex-col">
      <div>
        <h3 className="text-xs font-semibold text-muted uppercase tracking-wider">Flow Preamble</h3>
        <p className="text-xs text-muted mt-1">
          Both run before every node in this flow. Imports for libraries/constants;
          Functions for reusable helpers shared across all nodes.
        </p>
      </div>

      <div className="flex gap-1 shrink-0">
        {([['imports','Imports'],['functions','Functions']] as const).map(([t,l]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-1 rounded text-xs font-medium transition-colors
              ${tab === t ? 'bg-accent/15 text-accent' : 'text-muted hover:text-slate-300'}`}>
            {l}
          </button>
        ))}
      </div>

      <div className="flex-1 rounded-lg overflow-hidden border border-canvas-border min-h-[200px]"
        onKeyDown={(e) => e.stopPropagation()}>
        <Editor
          key={`${activeFlowId}-${tab}`}
          height="100%"
          defaultLanguage="python"
          value={code}
          onChange={(v) => setCode(v ?? '')}
          options={{
            minimap: { enabled: false }, fontSize: 12, scrollBeyondLastLine: false,
            padding: { top: 8 }, automaticLayout: true,
            placeholder: tab === 'functions' ? FUNCTIONS_HINT : undefined,
          } as any}
          theme="vs-dark"
        />
      </div>

      <button className="btn-pri w-full shrink-0" onClick={save}>Save Preamble</button>

      <div className="text-xs text-muted bg-canvas-bg rounded-lg border border-canvas-border p-3 space-y-1 shrink-0">
        <div className="text-slate-300 font-medium mb-1">Injected in every node (click for help):</div>
        <div><code className="text-accent">pd</code>, <code className="text-accent">sa</code> — pandas, sqlalchemy</div>
        {helpers.filter((h) => h.sfs_specific).map((h) => (
          <button key={h.name} className="block text-left hover:text-slate-300" onClick={() => openHelp(`helper:${h.name}`)}>
            <code className="text-accent">{h.name}</code> — {h.summary}
          </button>
        ))}
      </div>
    </div>
  )
}
