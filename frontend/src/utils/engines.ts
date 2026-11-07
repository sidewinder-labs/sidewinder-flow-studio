/**
 * /api/capabilities, fetched once and shared by every component that needs
 * the engine list (node cards, the processor panel) or the feature flags.
 */
import { useEffect, useState } from 'react'
import { api, type ConnectionType, type HelperDoc, type Snippet } from './api'

export type EngineInfo = Awaited<ReturnType<typeof api.capabilities>>['engines'][number] & {
  params?: { key: string; label?: string; type?: string; default?: unknown; required?: boolean;
             options?: (string | { value: string; label?: string })[] }[]
  hint?: string
  has_to_code?: boolean
}

let cache: EngineInfo[] | null = null
let features: Record<string, boolean> | null = null
let help: { helpers: HelperDoc[]; snippets: Snippet[] } | null = null
let connTypes: ConnectionType[] | null = null
let pending: Promise<EngineInfo[]> | null = null

export function loadEngines(): Promise<EngineInfo[]> {
  if (cache) return Promise.resolve(cache)
  pending ??= api.capabilities()
    .then((c) => {
      features = c?.features ?? {}
      help = { helpers: c?.helpers ?? [], snippets: c?.snippets ?? [] }
      connTypes = c?.connection_types ?? []
      return (cache = (c?.engines ?? []) as EngineInfo[])
    })
    .catch(() => { pending = null; return [] })
  return pending
}

/** Feature flags from /api/capabilities; empty until loaded. */
export function useFeatures(): Record<string, boolean> {
  const [f, setF] = useState<Record<string, boolean>>(features ?? {})
  useEffect(() => {
    let live = true
    if (!features) loadEngines().then(() => { if (live) setF(features ?? {}) })
    return () => { live = false }
  }, [])
  return f
}

export function useEngines(): EngineInfo[] {
  const [engines, setEngines] = useState<EngineInfo[]>(cache ?? [])
  useEffect(() => {
    let live = true
    if (!cache) loadEngines().then((e) => { if (live) setEngines(e) })
    return () => { live = false }
  }, [])
  return engines
}

/** "Model: Exponential decay + offset · x column: t_s" — a form engine's settings at a glance. */
export function paramSummary(engine: EngineInfo | undefined, data: Record<string, any>): string {
  if (!engine?.params?.length) return ''
  const parts: string[] = []
  for (const p of engine.params) {
    let v = data[p.key]
    // Defaults only for required fields (e.g. the model), so optional
    // fields left untouched don't clutter the card
    if ((v === undefined || v === null || v === '') && p.required) v = p.default
    if (v === undefined || v === null || v === '' || p.type === 'boolean') continue
    if (p.type === 'select' && p.options) {
      const opt = p.options.find((o) => (typeof o === 'string' ? o : o.value) === v)
      if (opt && typeof opt !== 'string' && opt.label) v = opt.label
    }
    parts.push(`${p.label ?? p.key}: ${v}`)
  }
  return parts.join(' · ')
}

/** Connection kinds plugins add; empty until loaded. */
export function useConnectionTypes(): ConnectionType[] {
  const [t, setT] = useState<ConnectionType[]>(connTypes ?? [])
  useEffect(() => {
    let live = true
    if (!connTypes) loadEngines().then(() => { if (live) setT(connTypes ?? []) })
    return () => { live = false }
  }, [])
  return t
}

/** The function reference and examples for the help panel; empty until loaded. */
export function useHelpData(): { helpers: HelperDoc[]; snippets: Snippet[] } {
  const [h, setH] = useState(help ?? { helpers: [], snippets: [] })
  useEffect(() => {
    let live = true
    if (!help) loadEngines().then(() => { if (live && help) setH(help) })
    return () => { live = false }
  }, [])
  return h
}
