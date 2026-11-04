/**
 * Where the help panel's "Insert into node" goes: the code editor of the
 * Processor or Chart node whose panel is open. The panel registers its editor
 * while mounted; the help panel reads the current target.
 */
import { useEffect, useRef, useSyncExternalStore } from 'react'

export interface CodeTarget {
  nodeId: string
  kind: 'processor' | 'chart'
  engine: string                                   // 'chart' for chart nodes
  /** Insert at the cursor; with switchTo, change the node's engine and append. */
  insert: (code: string, switchTo?: string) => void
}

let target: CodeTarget | null = null
const listeners = new Set<() => void>()
const notify = () => listeners.forEach((l) => l())

export function useCodeTarget(): CodeTarget | null {
  return useSyncExternalStore(
    (l) => { listeners.add(l); return () => { listeners.delete(l) } },
    () => target,
  )
}

/** Join an example to existing code with a blank line between. */
export function appendCode(existing: string, snippet: string): string {
  return existing.trim() ? `${existing.replace(/\s*$/, '')}\n\n${snippet}` : snippet
}

/**
 * Register a panel's Monaco editor as the insert target while the panel is
 * open. `setAll(code, engine)` stores new code (and engine) for the node;
 * insertion at the cursor goes through the editor so undo works.
 */
export function useInsertTarget(opts: {
  nodeId: string
  kind: 'processor' | 'chart'
  engine: string
  code: string
  setAll: (code: string, engine?: string) => void
}) {
  const editorRef = useRef<any>(null)
  const latest = useRef(opts)
  latest.current = opts

  useEffect(() => {
    const me: CodeTarget = {
      nodeId: opts.nodeId,
      kind: opts.kind,
      engine: opts.engine,
      insert: (code, switchTo) => {
        const { code: current, engine, setAll } = latest.current
        const ed = editorRef.current
        if (switchTo && switchTo !== engine) {
          setAll(appendCode(current, code), switchTo)      // the editor remounts for the new engine
          return
        }
        if (ed) {
          const sel = ed.getSelection()
          ed.executeEdits('sfs-help', [{ range: sel, text: code, forceMoveMarkers: true }])
          ed.focus()
        } else {
          setAll(appendCode(current, code))
        }
      },
    }
    target = me
    notify()
    return () => { if (target === me) { target = null; notify() } }
  }, [opts.nodeId, opts.kind, opts.engine])

  return editorRef
}
