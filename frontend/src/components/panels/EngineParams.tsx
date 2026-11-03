/**
 * EngineParams — renders the form fields an engine declares in its EngineSpec.
 *
 * Nothing here is engine-specific: fields come from /api/capabilities, so a
 * newly installed plugin's parameters appear with no frontend change. Values
 * are stored in the node's data under each param's `key`.
 *
 * Supported types: string | text | number | boolean | select | column | connection
 * (text is a multi-line, monospaced field, e.g. for SQL)
 * Unknown types fall back to a text input rather than rendering nothing.
 * Select options are plain strings, or {value, label?, group?} objects; options
 * that share a group are shown under that heading.
 */
import { useEffect, useState } from 'react'
import { useStore } from '@/store'
import { api } from '@/utils/api'

export interface EngineParam {
  key: string
  label?: string
  type?: string
  default?: unknown
  required?: boolean
  options?: (string | SelectOption)[]
  help?: string
}

export interface SelectOption {
  value: string
  label?: string
  group?: string
}

/** Options as objects, grouped in first-seen order ('' = no heading). */
function groupOptions(options: (string | SelectOption)[]): [string, SelectOption[]][] {
  const groups = new Map<string, SelectOption[]>()
  for (const o of options) {
    const opt = typeof o === 'string' ? { value: o } : o
    const key = opt.group ?? ''
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(opt)
  }
  return [...groups.entries()]
}

interface Props {
  params: EngineParam[]
  data: Record<string, any>
  nodeId: string
  onChange: (key: string, value: unknown) => void
}

export function EngineParams({ params, data, nodeId, onChange }: Props) {
  const { activeProject, activeFlowId, connections } = useStore()
  const [columns, setColumns] = useState<{ name: string; dtype: string }[]>([])

  const needsColumns = params.some((p) => p.type === 'column')

  // Fetch upstream columns only if some param actually needs them.
  useEffect(() => {
    if (!needsColumns || !activeProject?.id || !activeFlowId) return
    const flow = activeProject.flows.find((f) => f.id === activeFlowId)
    const parent = ((flow?.edges as any[]) ?? [])
      .filter((e) => e.target === nodeId)
      .sort((a, b) => String(a.source).localeCompare(String(b.source)))[0]
    if (!parent) { setColumns([]); return }
    api.frameColumns(activeProject.id, parent.source)
      .then((r) => setColumns(r.columns ?? []))
      .catch(() => setColumns([]))
  }, [nodeId, needsColumns, activeProject?.id, activeFlowId])

  if (!params?.length) return null

  return (
    <div className="shrink-0 space-y-2 rounded-lg border border-canvas-border bg-canvas-bg/40 p-2.5">
      {params.map((p) => {
        const value = data[p.key] ?? p.default ?? ''
        const label = p.label ?? p.key
        const id = `param-${nodeId}-${p.key}`

        let field: React.ReactNode
        switch (p.type) {
          case 'boolean':
            field = (
              <label className="flex items-center gap-2 cursor-pointer">
                <input id={id} type="checkbox" checked={!!data[p.key]}
                  onChange={(e) => onChange(p.key, e.target.checked)} />
                <span className="text-xs text-slate-300">{label}</span>
              </label>
            )
            break

          case 'number':
            field = (
              <input id={id} type="number" className="input text-xs" value={value as any}
                onChange={(e) => onChange(p.key, e.target.value)} />
            )
            break

          case 'select':
            field = (
              <select id={id} className="input text-xs" value={value as any}
                onChange={(e) => onChange(p.key, e.target.value)}>
                {!p.required && <option value="">—</option>}
                {groupOptions(p.options ?? []).map(([group, opts]) => {
                  const items = opts.map((o) => <option key={o.value} value={o.value}>{o.label ?? o.value}</option>)
                  return group ? <optgroup key={group} label={group}>{items}</optgroup> : items
                })}
              </select>
            )
            break

          case 'column':
            field = columns.length ? (
              <select id={id} className="input text-xs font-mono" value={value as any}
                onChange={(e) => onChange(p.key, e.target.value)}>
                <option value="">— pick a column —</option>
                {columns.map((c) => (
                  <option key={c.name} value={c.name}>{c.name} ({c.dtype})</option>
                ))}
              </select>
            ) : (
              // Upstream hasn't run yet — degrade to free text rather than block
              <input id={id} className="input text-xs font-mono" value={value as any}
                placeholder="column name (run upstream for a dropdown)"
                onChange={(e) => onChange(p.key, e.target.value)} />
            )
            break

          case 'connection':
            field = (
              <select id={id} className="input text-xs" value={value as any}
                onChange={(e) => onChange(p.key, e.target.value)}>
                <option value="">— pick a connection —</option>
                {connections.map((c: any) => (
                  <option key={c.id} value={c.name}>{c.name} ({c.dialect})</option>
                ))}
              </select>
            )
            break

          case 'text':
            field = (
              <textarea id={id} rows={4} spellCheck={false}
                className="input text-xs font-mono resize-y leading-relaxed" value={value as any}
                onChange={(e) => onChange(p.key, e.target.value)} />
            )
            break

          default: // string and anything unrecognised
            field = (
              <input id={id} className="input text-xs" value={value as any}
                onChange={(e) => onChange(p.key, e.target.value)} />
            )
        }

        return (
          <div key={p.key}>
            {p.type !== 'boolean' && (
              <label className="label" htmlFor={id}>
                {label}{p.required && <span className="text-red-400"> *</span>}
              </label>
            )}
            {field}
            {p.help && <p className="text-xs text-muted mt-0.5">{p.help}</p>}
          </div>
        )
      })}
    </div>
  )
}
