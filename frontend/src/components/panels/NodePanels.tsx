/**
 * Properties panels — shown in sidebar when a node is selected.
 *
 * Bug fixes applied:
 *  1. useEffect([nodeId]) resets local state whenever the selected node changes
 *  2. Monaco editor uses key={nodeId} to force full remount on node switch,
 *     preventing stale content from the previous node
 *  3. save() is called explicitly AND on every meaningful change, not just onBlur
 *  4. HINT is only shown as placeholder overlay — never written into the code state
 */
import { useState, useEffect, useCallback } from 'react'
import { useStore } from '@/store'
import { api } from '@/utils/api'
import { EngineParams } from './EngineParams'
import { ViewAsPython } from './ViewAsPython'
import { AgentBox } from './AgentBox'
import Editor from '@monaco-editor/react'
import { RefreshCw, Save, BookmarkPlus, Compass, HelpCircle } from 'lucide-react'
import { openNodeInExplore } from '@/components/nodes/ReportNodes'
import { useInsertTarget } from '@/components/help/insert'
import { openHelp } from '@/components/help/HelpPanel'

const EDITOR_OPTS = {
  minimap: { enabled: false },
  fontSize: 12,
  lineNumbers: 'on' as const,
  scrollBeyondLastLine: false,
  wordWrap: 'on' as const,
  padding: { top: 8 },
  automaticLayout: true,
}

// Read node data directly from the store — always fresh
function useNodeData(nodeId: string) {
  const { activeProject, activeFlowId } = useStore()
  const flow = activeProject?.flows.find((f) => f.id === activeFlowId)
  return (flow?.nodes as any[])?.find((n) => n.id === nodeId)?.data ?? {}
}


// ── Trigger panel ─────────────────────────────────────────────────────────────

export function TriggerPanel({ nodeId }: { nodeId: string }) {
  const { activeFlowId, updateNodeData } = useStore()
  const data = useNodeData(nodeId)

  const [label, setLabel] = useState(data.label ?? 'Start')
  const [mode,  setMode]  = useState(data.run_mode ?? 'manual')
  const [cron,  setCron]  = useState(data.cron ?? '')
  const [tz,    setTz]    = useState(data.timezone ?? 'UTC')
  // Advanced scheduler options
  const [retries, setRetries]       = useState<number>(data.retries ?? 0)
  const [backoff, setBackoff]       = useState<number>(data.retry_backoff_sec ?? 60)
  const [alertOn, setAlertOn]       = useState<boolean>(data.alert_on_failure ?? false)
  const [alertEmail, setAlertEmail] = useState<string>(data.alert_email ?? '')
  const [weekdays, setWeekdays]     = useState<boolean>(data.weekdays_only ?? false)
  const [skipDates, setSkipDates]   = useState<string>(data.skip_dates ?? '')

  // Reset local state whenever the selected node changes
  useEffect(() => {
    setLabel(data.label ?? 'Start')
    setMode(data.run_mode ?? 'manual')
    setCron(data.cron ?? '')
    setTz(data.timezone ?? 'UTC')
    setRetries(data.retries ?? 0)
    setBackoff(data.retry_backoff_sec ?? 60)
    setAlertOn(data.alert_on_failure ?? false)
    setAlertEmail(data.alert_email ?? '')
    setWeekdays(data.weekdays_only ?? false)
    setSkipDates(data.skip_dates ?? '')
  }, [nodeId])

  const save = useCallback(() => {
    if (!activeFlowId) return
    updateNodeData(activeFlowId, nodeId, {
      label, run_mode: mode, cron, timezone: tz,
      retries, retry_backoff_sec: backoff, alert_on_failure: alertOn,
      alert_email: alertEmail, weekdays_only: weekdays, skip_dates: skipDates,
    })
  }, [activeFlowId, nodeId, label, mode, cron, tz, retries, backoff, alertOn, alertEmail, weekdays, skipDates, updateNodeData])

  return (
    <div className="p-4 space-y-4">
      <h3 className="text-xs font-semibold text-muted uppercase tracking-wider">Trigger</h3>

      <div>
        <label className="label">Label</label>
        <input
          className="input"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          onBlur={save}
        />
      </div>

      <div>
        <label className="label">Run Mode</label>
        <div className="flex gap-2">
          {(['manual', 'schedule'] as const).map((m) => (
            <button
              key={m}
              onClick={() => {
                setMode(m)
                if (activeFlowId) updateNodeData(activeFlowId, nodeId, { label, run_mode: m, cron, timezone: tz })
              }}
              className={`flex-1 py-2 rounded-lg border text-xs font-medium transition-colors
                ${mode === m
                  ? 'border-accent bg-accent/10 text-accent'
                  : 'border-canvas-border text-muted hover:border-subtle'}`}
            >
              {m === 'manual' ? '▶ Manual' : '🕐 Schedule'}
            </button>
          ))}
        </div>
      </div>

      {mode === 'schedule' && (
        <>
          <div>
            <label className="label">Cron Expression</label>
            <input
              className="input font-mono"
              value={cron}
              onChange={(e) => setCron(e.target.value)}
              onBlur={save}
              placeholder="0 6 * * 1-5"
            />
            <p className="text-xs text-muted mt-1">
              min hour day month weekday — e.g.{' '}
              <code className="text-accent">0 6 * * 1-5</code> = weekdays 06:00
            </p>
          </div>
          <div>
            <label className="label">Timezone</label>
            <input
              className="input font-mono"
              value={tz}
              onChange={(e) => setTz(e.target.value)}
              onBlur={save}
              placeholder="Europe/Athens"
            />
          </div>

          {/* ── Advanced scheduler ───────────────────────────────── */}
          <div className="rounded-lg border border-amber-500/30 bg-amber-950/20 p-3 space-y-3">
            <div className="text-xs text-amber-300 font-medium">Advanced scheduling</div>

            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="label">Retries</label>
                <input type="number" min={0} max={10} className="input text-xs"
                  value={retries} onChange={(e) => setRetries(Number(e.target.value))} onBlur={save} />
              </div>
              <div>
                <label className="label">Backoff (sec)</label>
                <input type="number" min={1} className="input text-xs"
                  value={backoff} onChange={(e) => setBackoff(Number(e.target.value))} onBlur={save} />
              </div>
            </div>
            <p className="text-xs text-muted -mt-1">Exponential: wait × 2^(attempt−1) between retries.</p>

            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={alertOn}
                onChange={(e) => { setAlertOn(e.target.checked); if (activeFlowId) updateNodeData(activeFlowId, nodeId, { alert_on_failure: e.target.checked }) }} />
              <span className="text-xs text-slate-300">Alert on failure (Slack + email)</span>
            </label>
            {alertOn && (
              <input className="input text-xs" value={alertEmail} onBlur={save}
                onChange={(e) => setAlertEmail(e.target.value)}
                placeholder="alert email (optional; Slack via backend .env)" />
            )}

            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={weekdays}
                onChange={(e) => { setWeekdays(e.target.checked); if (activeFlowId) updateNodeData(activeFlowId, nodeId, { weekdays_only: e.target.checked }) }} />
              <span className="text-xs text-slate-300">Weekdays only (skip Sat/Sun)</span>
            </label>

            <div>
              <label className="label">Skip dates</label>
              <input className="input text-xs font-mono" value={skipDates} onBlur={save}
                onChange={(e) => setSkipDates(e.target.value)}
                placeholder="2026-12-25, 2026-01-01" />
              <p className="text-xs text-muted mt-1">Comma-separated holidays to skip (YYYY-MM-DD).</p>
            </div>
          </div>
        </>
      )}

      <button className="btn-pri w-full" onClick={save}>
        <Save size={12} /> Save
      </button>
    </div>
  )
}



// ── Processor panel ───────────────────────────────────────────────────────────

const PROCESSOR_HINT = `# Incoming DataFrame is available as \`df\`; leave the result in \`df\`.
# Multiple inputs (joins/unions): 2nd upstream = \`df2\`, 3rd = \`df3\`, … (\`dfs\` = list of all)
#   df = df.merge(df2, on='key', how='left')     # join
#   df = pd.concat(dfs, ignore_index=True)       # union all inputs
#
# pd, sa and the SFS helpers need no import: fast_read, fast_write,
# get_engine, read_files, vars, …  Press F1 for all of them, with examples.
#
# df = fast_read("postgres_local", table="employees.employee")
# df = df[df['gender'] == 'F']
`

const POLARS_HINT = `# Polars engine: \`df\` is a LazyFrame scanned from the upstream frame.
# Multiple inputs: df2, df3, … (\`dfs\` = list). \`pl\` is pre-injected.
# Stay lazy — no .collect(); SFS collects (streaming) at the end.
#
# df = df.filter(pl.col("from_date") >= pl.date(1990, 1, 1))
# df = df.with_columns(pl.col("from_date").dt.year().alias("year"))
# df = df.group_by("year").agg(pl.col("amount").mean().alias("avg_salary"))
#
# Join:  df = df.join(df2, left_on="employee_id", right_on="id", how="left")
# Union: df = pl.concat(dfs)
`

const SQL_HINT = `-- SQL pushdown: runs IN the target warehouse (pick a connection above).
-- SFS transpiles dialects with SQLGlot. SELECT/WITH results become the node output;
-- CREATE/COPY/LOAD/MERGE execute without fetching (warehouse-to-warehouse orchestration).
--
-- SELECT d.dept_name, AVG(s.amount) AS avg_salary
-- FROM employees.salary s
-- JOIN employees.department_employee de ON de.employee_id = s.employee_id
-- JOIN employees.department d ON d.id = de.department_id
-- WHERE s.to_date = DATE '9999-01-01' AND de.to_date = DATE '9999-01-01'
-- GROUP BY d.dept_name
--
-- Bulk transfer example (Snowflake → GCS):
-- COPY INTO @gcs_stage/salary FROM (SELECT * FROM salary) FILE_FORMAT=(TYPE=PARQUET);
`

const IBIS_HINT = `# Ibis pushdown: pandas-like Python compiled to warehouse SQL.
# Pre-injected: ibis, con (warehouse backend), df (upstream frame as memtable, if any).
#
# t = con.table("salary", database="employees")
# df = (t.filter(t.from_date >= ibis.date(1990, 1, 1))
#         .group_by(year=t.from_date.year())
#         .aggregate(avg_salary=t.amount.mean()))
# # leave the expression in df — SFS executes it in the warehouse
`

const DUCKDB_HINT = `-- DuckDB engine: write SQL. The upstream frame is a table named df.
-- Multiple inputs: 2nd upstream = df2, 3rd = df3, … (join/union them in SQL)
--   SELECT * FROM df JOIN df2 USING (key)
--   SELECT * FROM df UNION ALL SELECT * FROM df2
-- Result of the query becomes this node's output. Zero-copy over Arrow;
-- spills to disk automatically for larger-than-RAM queries.
--
-- Examples:
-- SELECT * FROM df WHERE to_date = DATE '9999-01-01'   -- current salaries
--
-- SELECT date_trunc('year', from_date) AS year,
--        AVG(amount) AS avg_salary
-- FROM df GROUP BY 1 ORDER BY 1
--
-- Window functions, QUALIFY, PIVOT, list/struct types all supported.
`


// Engine list comes from /api/capabilities so plugin-provided engines appear
// automatically. This static list is only a fallback if the call fails.
const FALLBACK_ENGINES = [
  { name: 'pandas', label: 'pandas', description: 'Python · full flexibility', tier: 'free', language: 'python', needs_target_connection: false, source: 'builtin', available: true },
  { name: 'polars', label: 'Polars', description: 'Lazy · streaming · fast', tier: 'free', language: 'python', needs_target_connection: false, source: 'builtin', available: true },
  { name: 'duckdb', label: 'DuckDB', description: 'SQL · zero-copy Arrow', tier: 'free', language: 'sql', needs_target_connection: false, source: 'builtin', available: true },
  { name: 'sql', label: 'SQL ⚡', description: 'Pushdown · SQLGlot · warehouse', tier: 'free', language: 'sql', needs_target_connection: true, source: 'builtin', available: true },
  { name: 'ibis', label: 'Ibis ⚡', description: 'Pushdown · pandas-like · warehouse', tier: 'free', language: 'python', needs_target_connection: true, source: 'builtin', available: true },
] as any[]

let _capsCache: any[] | null = null

export function ProcessorPanel({ nodeId }: { nodeId: string }) {
  const { activeFlowId, updateNodeData, activeProject, runFlow, connections } = useStore()
  const flow = activeProject?.flows.find((f) => f.id === activeFlowId)
  const data = useNodeData(nodeId)

  const [engineList, setEngineList] = useState<any[]>(_capsCache ?? FALLBACK_ENGINES)
  // Current engine's declared capabilities (drives the UI, not hardcoded names)
  useEffect(() => {
    if (_capsCache) return
    api.capabilities()
      .then((c) => { if (c?.engines?.length) { _capsCache = c.engines; setEngineList(c.engines) } })
      .catch(() => {/* keep fallback */})
  }, [])

  const [label,  setLabel]  = useState<string>(data.label ?? 'Processor')
  const [code,   setCode]   = useState<string>(data.code  ?? '')
  const [engine, setEngine] = useState<string>(data.engine ?? 'pandas')
  // The active engine's declared capabilities — drives target-connection and
  // editor-language UI so plugin engines work without frontend changes.
  const engineSpec = engineList.find((e) => e.name === engine)
  const [saved,  setSaved]  = useState(false)
  const persist = (data.persist ?? { enabled: false, target: 'local', table_name: '' }) as any
  const [targetConn, setTargetConn] = useState<string>(data.target_connection ?? '')
  const [readDialect, setReadDialect] = useState<string>(data.sql_read_dialect ?? '')
  useEffect(() => {
    setTargetConn(data.target_connection ?? '')
    setReadDialect(data.sql_read_dialect ?? '')
  }, [nodeId])

  const savePersist = (p: any) => {
    if (activeFlowId) updateNodeData(activeFlowId, nodeId, { persist: p })
  }

  // ── Key fix: reset state when nodeId changes ──────────────────────
  useEffect(() => {
    const fresh = useStore.getState().activeProject?.flows
      .find((f) => f.id === useStore.getState().activeFlowId)
      ?.nodes as any[]
    const node = fresh?.find((n: any) => n.id === nodeId)
    setLabel(node?.data?.label ?? 'Processor')
    setCode(node?.data?.code ?? '')
    setEngine(node?.data?.engine ?? 'pandas')
    setSaved(false)
  }, [nodeId])

  const save = useCallback(() => {
    if (!activeFlowId) return
    updateNodeData(activeFlowId, nodeId, { label, code, engine, target_connection: targetConn, sql_read_dialect: readDialect })
    setSaved(true)
    setTimeout(() => setSaved(false), 1500)
  }, [activeFlowId, nodeId, label, code, engine, updateNodeData])

  // Auto-save 600ms after the user stops typing in the editor
  useEffect(() => {
    const t = setTimeout(() => {
      if (activeFlowId && code !== (data.code ?? '')) {
        updateNodeData(activeFlowId, nodeId, { label, code, engine })
      }
    }, 600)
    return () => clearTimeout(t)
  }, [code])

  const switchEngine = (next: string) => {
    setEngine(next)
    if (activeFlowId) updateNodeData(activeFlowId, nodeId, { label, code, engine: next, target_connection: targetConn, sql_read_dialect: readDialect })
  }
  // The help panel's "Insert into node" targets this editor while it is open
  const editorRef = useInsertTarget({
    nodeId, kind: 'processor', engine, code,
    setAll: (next, nextEngine) => {
      setCode(next)
      if (nextEngine) setEngine(nextEngine)
      if (activeFlowId) updateNodeData(activeFlowId, nodeId, { label, code: next, engine: nextEngine ?? engine })
    },
  })
  const saveTarget = (c: string) => {
    setTargetConn(c)
    if (activeFlowId) updateNodeData(activeFlowId, nodeId, { target_connection: c })
  }

  const saveAsCustom = async () => {
    const name = window.prompt('Name this custom processor (appears in the toolbar):', label)
    if (!name) return
    const description = window.prompt('Optional description:', '') ?? ''
    try {
      await api.customProcessors.create({ name, description, engine, code })
      window.dispatchEvent(new CustomEvent('sfs:custom-processors-changed'))
    } catch (e) {
      window.alert('Could not save custom processor.')
    }
  }

  // Which variables will this node receive? First parent → df, then df2, df3, …
  // (matches the executor's multi-input convention). Shown so you know the names
  // available before writing code — and which one to reassign (`df = ...`).
  const inputVars: { name: string; from: string }[] = (() => {
    const incoming = ((flow?.edges as any[]) ?? [])
      .filter((e) => e.target === nodeId)
      .sort((a, b) => String(a.source).localeCompare(String(b.source)))
    return incoming.map((e, i) => {
      const parent = (flow?.nodes as any[])?.find((n) => n.id === e.source)
      return {
        name: i === 0 ? 'df' : `df${i + 1}`,
        from: parent?.data?.label ?? parent?.type ?? 'source',
      }
    })
  })()

  return (
    <div className="p-4 space-y-3 min-h-full flex flex-col">
      <h3 className="text-xs font-semibold text-muted uppercase tracking-wider shrink-0">
        Processor
      </h3>

      <div className="shrink-0">
        <label className="label">Label</label>
        <input
          className="input"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          onBlur={save}
        />
      </div>

      {/* ── Engine selector (from /api/capabilities — includes plugins) ── */}
      <div className="shrink-0">
        <div className="flex items-center justify-between">
          <label className="label">Engine</label>
          <button className="text-subtle hover:text-accent mb-1" title="How this engine works (F1)"
                  onClick={() => openHelp(`engine:${engine}`)}>
            <HelpCircle size={13} />
          </button>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {engineList.map((e) => (
            <button
              key={e.name}
              onClick={() => e.available && switchEngine(e.name as any)}
              disabled={!e.available}
              className={`flex-1 py-2 px-2 rounded-lg border text-left transition-colors
                ${engine === e.name
                  ? 'border-accent bg-accent/10'
                  : 'border-canvas-border hover:border-subtle'}
                ${!e.available ? 'opacity-40 cursor-not-allowed' : ''}`}
              title={e.available
                ? `${e.description}${e.source?.startsWith('plugin:') ? ` — from ${e.source}` : ''}`
                : `Unavailable: ${e.unavailable_reason}`}
            >
              <div className={`text-xs font-semibold flex items-center gap-1 ${engine === e.name ? 'text-accent' : 'text-slate-300'}`}>
                {e.label}
                {e.source?.startsWith('plugin:') && <span className="text-violet-400" title="provided by a plugin">🧩</span>}
              </div>
              <div className="text-xs text-muted leading-tight mt-0.5">
                {e.available ? e.description : e.unavailable_reason}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* ── Persist checkpoint ──────────────────────────────────── */}
      <div className="shrink-0 rounded-lg border border-canvas-border p-2.5 space-y-2">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={!!persist.enabled}
            onChange={(e) => savePersist({ ...persist, enabled: e.target.checked })}
          />
          <span className="text-xs font-medium text-slate-300">Persist output</span>
          {persist.last_persisted && (
            <span className="text-xs text-subtle ml-auto">
              saved {new Date(persist.last_persisted).toLocaleString()}
            </span>
          )}
        </label>
        {persist.enabled && (
          <div className="grid grid-cols-2 gap-2">
            <select
              className="input text-xs"
              value={persist.target || 'local'}
              onChange={(e) => savePersist({ ...persist, target: e.target.value })}
            >
              <option value="local">Local DuckDB</option>
              {connections.map((c) => <option key={c.id} value={c.name}>{c.name}</option>)}
            </select>
            <input
              className="input text-xs font-mono"
              value={persist.table_name || ''}
              onChange={(e) => savePersist({ ...persist, table_name: e.target.value })}
              placeholder="table name (auto from label)"
            />
          </div>
        )}
      </div>


      {engineSpec?.needs_target_connection && (
        <div className="shrink-0 space-y-2 rounded-lg border border-amber-500/30 bg-amber-950/20 p-2.5">
          <div className="text-xs text-amber-300 font-medium">⚡ Pushdown — runs in the warehouse</div>
          <div>
            <label className="label">Target connection</label>
            <select className="input text-xs" value={targetConn} onChange={(e) => saveTarget(e.target.value)}>
              <option value="">— pick a connection —</option>
              {connections.map((c) => <option key={c.id} value={c.name}>{c.name} ({c.dialect})</option>)}
            </select>
          </div>
          {engine === 'sql' && (
            <div>
              <label className="label">Write dialect (optional)</label>
              <select className="input text-xs" value={readDialect}
                onChange={(e) => { setReadDialect(e.target.value); if (activeFlowId) updateNodeData(activeFlowId, nodeId, { sql_read_dialect: e.target.value }) }}>
                <option value="">same as target</option>
                {['postgresql','mysql','mssql','oracle','snowflake','bigquery','clickhouse','redshift','duckdb'].map((d) =>
                  <option key={d} value={d}>{d}</option>)}
              </select>
              <p className="text-xs text-muted mt-1">The dialect you write in — SFS transpiles to the target.</p>
            </div>
          )}
        </div>
      )}

      <div className="shrink-0">
        <AgentBox
          nodeId={nodeId}
          nodeType="processor"
          engine={engine}
          onInsert={(generatedCode) => {
            setCode(generatedCode)
            if (activeFlowId) updateNodeData(activeFlowId, nodeId, { label, code: generatedCode, engine })
          }}
        />
      </div>

      {engineSpec?.params?.length > 0 && (
        <EngineParams
          params={engineSpec.params}
          data={data}
          nodeId={nodeId}
          onChange={(key, value) => {
            if (activeFlowId) updateNodeData(activeFlowId, nodeId, { [key]: value })
          }}
        />
      )}

      {engineSpec?.has_to_code && (
        <ViewAsPython
          engine={engine}
          engineLabel={engineSpec.label}
          data={data}
          projectId={activeProject?.id}
          parentIds={((flow?.edges as any[]) ?? []).filter((e) => e.target === nodeId).map((e) => e.source)}
          onConvert={(generated) => {
            setCode(generated)
            setEngine('pandas')
            if (activeFlowId) updateNodeData(activeFlowId, nodeId, { label, code: generated, engine: 'pandas' })
          }}
        />
      )}

      <div className="flex-1 flex flex-col min-h-[360px]">
        {inputVars.length > 0 && (
          <div className="flex items-center gap-1.5 flex-wrap mb-1.5 shrink-0">
            <span className="text-xs text-muted">Inputs:</span>
            {inputVars.map((iv) => (
              <span key={iv.name}
                className="text-xs font-mono bg-accent/10 text-accent rounded px-1.5 py-0.5"
                title={`from "${iv.from}" — reassign with ${iv.name} = ${iv.name}[...]`}>
                {iv.name}
                <span className="text-slate-500"> ← {iv.from}</span>
              </span>
            ))}
          </div>
        )}
        <div className="flex items-center justify-between mb-1 shrink-0">
          <label className="label mb-0">
            {engine === 'sql' ? 'SQL' : engine === 'ibis' ? 'Ibis (Python)' : engine === 'duckdb' ? 'SQL (DuckDB)'
              : engineSpec?.params?.length ? 'Python Code (optional — runs instead of the form)' : 'Python Code'}
          </label>
          <button
            className="btn-ghost text-xs py-0.5"
            onClick={() => { save(); runFlow(nodeId) }}
            title="Save then re-run from this node"
          >
            <RefreshCw size={10} /> Re-run from here
          </button>
        </div>

        {/* key includes engine so Monaco remounts with the right language
            when switching pandas ↔ duckdb. onKeyDown stopPropagation prevents
            React Flow from intercepting Space/Delete while typing. The panel's
            height follows its content, so a percentage height has nothing to
            resolve against: Monaco is pinned to the box with absolute inset-0. */}
        <div
          className="flex-1 relative rounded-lg overflow-hidden border border-canvas-border min-h-[320px]"
          onKeyDown={(e) => e.stopPropagation()}
        >
          <div className="absolute inset-0">
          <Editor
            key={`${nodeId}-${engine}`}
            height="100%"
            defaultLanguage={engineSpec?.language === 'sql' ? 'sql' : 'python'}
            value={code}
            onChange={(v) => setCode(v ?? '')}
            onMount={(ed) => { editorRef.current = ed }}
            options={{
              ...EDITOR_OPTS,
              placeholder: engine === 'duckdb' ? DUCKDB_HINT : engine === 'polars' ? POLARS_HINT : engine === 'sql' ? SQL_HINT : engine === 'ibis' ? IBIS_HINT
                : (engineSpec?.source?.startsWith('plugin:') && engineSpec?.hint) || PROCESSOR_HINT,
            } as any}
            theme="vs-dark"
          />
          </div>
        </div>
      </div>

      <div className="flex gap-2 shrink-0">
        <button className="btn-pri flex-1" onClick={save}>
          <Save size={12} />
          {saved ? '✓ Saved' : 'Save'}
        </button>
        <button className="btn-ghost border border-canvas-border" onClick={saveAsCustom}
          title="Save this processor to the Custom library for reuse in any flow">
          <BookmarkPlus size={12} /> Save as custom
        </button>
      </div>

      {flow?.imports && (
        <div className="text-xs text-muted bg-canvas-bg rounded-lg border border-canvas-border p-2 shrink-0">
          <span className="text-subtle font-semibold">Flow imports active</span> — edit in the Imports tab
        </div>
      )}
    </div>
  )
}


// ── Stop panel ────────────────────────────────────────────────────────────────

export function StopPanel({ nodeId }: { nodeId: string }) {
  const { activeFlowId, updateNodeData } = useStore()
  const data = useNodeData(nodeId)

  const [label, setLabel] = useState(data.label ?? 'Stop')
  const [mode,  setMode]  = useState<'hard' | 'tap'>(data.mode ?? 'hard')

  useEffect(() => {
    setLabel(data.label ?? 'Stop')
    setMode(data.mode ?? 'hard')
  }, [nodeId])

  const save = useCallback(() => {
    if (!activeFlowId) return
    updateNodeData(activeFlowId, nodeId, { label, mode })
  }, [activeFlowId, nodeId, label, mode, updateNodeData])

  return (
    <div className="p-4 space-y-4">
      <h3 className="text-xs font-semibold text-muted uppercase tracking-wider">Stop / Tap</h3>

      <div>
        <label className="label">Label</label>
        <input
          className="input"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          onBlur={save}
        />
      </div>

      <div>
        <label className="label">Mode</label>
        <div className="space-y-2">
          {([
            ['hard', 'Hard Stop',      'Flow halts here. Inspect the DataFrame, then resume manually.'],
            ['tap',  'Tap (observe)',  'Flow continues past this node. Use to inspect mid-flow without blocking.'],
          ] as const).map(([m, title, desc]) => (
            <label
              key={m}
              className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors
                ${mode === m
                  ? 'border-orange-500/60 bg-orange-900/20'
                  : 'border-canvas-border hover:border-subtle'}`}
            >
              <input
                type="radio"
                name={`stop_mode_${nodeId}`}
                checked={mode === m}
                onChange={() => {
                  setMode(m)
                  if (activeFlowId) updateNodeData(activeFlowId, nodeId, { label, mode: m })
                }}
                className="mt-0.5"
              />
              <div>
                <div className="text-sm font-medium text-slate-200">{title}</div>
                <div className="text-xs text-muted">{desc}</div>
              </div>
            </label>
          ))}
        </div>
      </div>

      <button className="btn-pri w-full" onClick={save}>
        <Save size={12} /> Save
      </button>
    </div>
  )
}


// ── Chart Out panel ───────────────────────────────────────────────────────────

const CHART_HINT = `# df is the incoming DataFrame (with several inputs: df2, df3, … and the list dfs)
# Set \`result\` to a Plotly figure, ECharts dict, or draw with matplotlib.
#
# ── Plotly ──────────────────────────────────────────────
# import plotly.express as px
# result = px.line(df, x='year', y='avg_salary', color='dept_name')
#
# ── Matplotlib / Seaborn ────────────────────────────────
# import matplotlib.pyplot as plt
# fig, ax = plt.subplots(figsize=(10,5))
# df.groupby('dept_name')['amount'].mean().plot(kind='bar', ax=ax)
#
# ── ECharts ─────────────────────────────────────────────
# result = {
#   "xAxis": {"data": df['dept_name'].tolist()},
#   "yAxis": {},
#   "series": [{"type": "bar", "data": df['avg_salary'].tolist()}]
# }
`

// Chart templates contributed by plugins (via /api/capabilities)
type ChartTemplate = { name: string; code: string; description: string; source: string }
let _chartTemplates: ChartTemplate[] | null = null

export function ChartOutPanel({ nodeId }: { nodeId: string }) {
  const { activeFlowId, updateNodeData } = useStore()
  const data = useNodeData(nodeId)

  const [templates, setTemplates] = useState<ChartTemplate[]>(_chartTemplates ?? [])
  useEffect(() => {
    if (_chartTemplates) return
    api.capabilities()
      .then((c) => {
        _chartTemplates = (c?.templates ?? []).filter((t) => t.kind === 'chart')
        setTemplates(_chartTemplates)
      })
      .catch(() => {/* no templates */})
  }, [])

  const [label, setLabel] = useState(data.label ?? 'Chart View')
  const [code,  setCode]  = useState(data.code  ?? '')
  const [saved, setSaved] = useState(false)
  const editorRef = useInsertTarget({
    nodeId, kind: 'chart', engine: 'chart', code,
    setAll: (next) => {
      setCode(next)
      if (activeFlowId) updateNodeData(activeFlowId, nodeId, { label, code: next })
    },
  })

  useEffect(() => {
    const fresh = useStore.getState().activeProject?.flows
      .find((f) => f.id === useStore.getState().activeFlowId)
      ?.nodes as any[]
    const node = fresh?.find((n: any) => n.id === nodeId)
    setLabel(node?.data?.label ?? 'Chart View')
    setCode(node?.data?.code ?? '')
    setSaved(false)
  }, [nodeId])

  const save = useCallback(() => {
    if (!activeFlowId) return
    updateNodeData(activeFlowId, nodeId, { label, code })
    setSaved(true)
    setTimeout(() => setSaved(false), 1500)
  }, [activeFlowId, nodeId, label, code, updateNodeData])

  useEffect(() => {
    const t = setTimeout(() => {
      if (activeFlowId && code !== (data.code ?? '')) {
        updateNodeData(activeFlowId, nodeId, { label, code })
      }
    }, 600)
    return () => clearTimeout(t)
  }, [code])

  return (
    <div className="p-4 space-y-3 h-full flex flex-col">
      <h3 className="text-xs font-semibold text-muted uppercase tracking-wider shrink-0">Chart View</h3>

      <div className="shrink-0">
        <label className="label">Label</label>
        <input
          className="input"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          onBlur={save}
        />
      </div>

      {templates.length > 0 && (
        <div className="shrink-0">
          <label className="label" htmlFor={`chart-template-${nodeId}`}>Start from a template</label>
          <select
            id={`chart-template-${nodeId}`}
            className="input text-xs"
            value=""
            onChange={(e) => {
              const t = templates.find((x) => x.name === e.target.value)
              if (!t) return
              if (code.trim() && !window.confirm(`Replace the chart code with "${t.name}"?`)) return
              setCode(t.code)
              if (activeFlowId) updateNodeData(activeFlowId, nodeId, { label, code: t.code })
            }}
          >
            <option value="">— pick a template —</option>
            {templates.map((t) => (
              <option key={`${t.source}:${t.name}`} value={t.name} title={t.description}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="shrink-0">
        <AgentBox
          nodeId={nodeId}
          nodeType="chart"
          onInsert={(generatedCode) => {
            setCode(generatedCode)
            if (activeFlowId) updateNodeData(activeFlowId, nodeId, { label, code: generatedCode })
          }}
        />
      </div>

      <div
        className="flex-1 min-h-[280px] rounded-lg overflow-hidden border border-canvas-border"
        onKeyDown={(e) => e.stopPropagation()}
      >
        <Editor
          key={nodeId}
          height="100%"
          defaultLanguage="python"
          value={code}
          onChange={(v) => setCode(v ?? '')}
          onMount={(ed) => { editorRef.current = ed }}
          options={{ ...EDITOR_OPTS, placeholder: CHART_HINT } as any}
          theme="vs-dark"
        />
      </div>

      <button className="btn-pri w-full shrink-0" onClick={save}>
        <Save size={12} /> {saved ? '✓ Saved' : 'Save'}
      </button>
    </div>
  )
}


// ── Table Out panel ───────────────────────────────────────────────────────────

export function TableOutPanel({ nodeId }: { nodeId: string }) {
  const { activeFlowId, updateNodeData } = useStore()
  const data = useNodeData(nodeId)
  const [label, setLabel] = useState(data.label ?? 'Table View')

  useEffect(() => { setLabel(data.label ?? 'Table View') }, [nodeId])

  const save = useCallback(() => {
    if (!activeFlowId) return
    updateNodeData(activeFlowId, nodeId, { label })
  }, [activeFlowId, nodeId, label, updateNodeData])

  return (
    <div className="p-4 space-y-4">
      <h3 className="text-xs font-semibold text-muted uppercase tracking-wider">Table View</h3>
      <div>
        <label className="label">Label</label>
        <input
          className="input"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          onBlur={save}
        />
      </div>
      <p className="text-xs text-muted">
        Connect to any processor to inspect its DataFrame as a sortable, filterable table.
        Data appears in the Output tab after running the flow.
      </p>
      <button className="btn-pri w-full" onClick={save}>
        <Save size={12} /> Save
      </button>
    </div>
  )
}


// ── Explore panel ─────────────────────────────────────────────────────────────

export function ExplorePanel({ nodeId }: { nodeId: string }) {
  const { activeFlowId, updateNodeData } = useStore()
  const data = useNodeData(nodeId)
  const [label, setLabel] = useState(data.label ?? 'Explore')

  useEffect(() => {
    setLabel(data.label ?? 'Explore')
  }, [nodeId])

  const save = useCallback(() => {
    if (!activeFlowId) return
    updateNodeData(activeFlowId, nodeId, { label })
  }, [activeFlowId, nodeId, label, updateNodeData])

  return (
    <div className="p-4 space-y-4">
      <h3 className="text-xs font-semibold text-muted uppercase tracking-wider">Explore</h3>
      <div>
        <label className="label">Label</label>
        <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} onBlur={save} />
        <p className="text-xs text-muted mt-1">
          Keeps the incoming DataFrame from each run and opens it in the Explore view:
          drag-and-drop charts, computed on the server. No code needed — run the flow,
          then open it here or from the link on the node.
        </p>
      </div>
      <button className="btn-pri w-full" onClick={() => { save(); openNodeInExplore(nodeId) }}>
        <Compass size={12} /> Open in Explore
      </button>
    </div>
  )
}

// ── Report panel (Streamlit dashboard) ────────────────────────────────────────

const REPORT_HINT = `# Streamlit dashboard code. Pre-injected: st, df (pandas), pd
# Runs inside the report viewer page — do NOT call st.set_page_config.
#
# c1, c2, c3 = st.columns(3)
# c1.metric("Rows", len(df))
# c2.metric("Avg salary", f"{df['amount'].mean():,.0f}")
#
# df['year'] = pd.to_datetime(df['from_date']).dt.year
# st.bar_chart(df.groupby('year')['amount'].mean())
# st.dataframe(df)
`

export function ReportPanel({ nodeId }: { nodeId: string }) {
  const { activeFlowId, updateNodeData } = useStore()
  const data = useNodeData(nodeId)
  const [label, setLabel] = useState(data.label ?? 'Report')
  const [name, setName] = useState(data.report_name ?? '')
  const [code, setCode] = useState(data.code ?? '')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    const fresh = useStore.getState().activeProject?.flows
      .find((f) => f.id === useStore.getState().activeFlowId)?.nodes as any[]
    const node = fresh?.find((n: any) => n.id === nodeId)
    setLabel(node?.data?.label ?? 'Report')
    setName(node?.data?.report_name ?? '')
    setCode(node?.data?.code ?? '')
    setSaved(false)
  }, [nodeId])

  const save = useCallback(() => {
    if (!activeFlowId) return
    updateNodeData(activeFlowId, nodeId, { label, report_name: name, code })
    setSaved(true); setTimeout(() => setSaved(false), 1500)
  }, [activeFlowId, nodeId, label, name, code, updateNodeData])

  useEffect(() => {
    const t = setTimeout(() => {
      if (activeFlowId && code !== (data.code ?? '')) {
        updateNodeData(activeFlowId, nodeId, { label, report_name: name, code })
      }
    }, 600)
    return () => clearTimeout(t)
  }, [code])

  return (
    <div className="p-4 space-y-3 h-full flex flex-col">
      <h3 className="text-xs font-semibold text-muted uppercase tracking-wider shrink-0">Report (Streamlit)</h3>
      <div className="shrink-0">
        <label className="label">Label</label>
        <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} onBlur={save} />
      </div>
      <div className="shrink-0">
        <label className="label">Report name</label>
        <input className="input font-mono" value={name} onChange={(e) => setName(e.target.value)} onBlur={save}
          placeholder="e.g. salary_dashboard (defaults to label)" />
      </div>
      <div className="shrink-0">
        <AgentBox
          nodeId={nodeId}
          nodeType="report"
          onInsert={(g) => { setCode(g); if (activeFlowId) updateNodeData(activeFlowId, nodeId, { label, report_name: name, code: g }) }}
        />
      </div>
      <div className="flex-1 min-h-[260px] rounded-lg overflow-hidden border border-canvas-border"
           onKeyDown={(e) => e.stopPropagation()}>
        <Editor
          key={nodeId}
          height="100%"
          defaultLanguage="python"
          value={code}
          onChange={(v) => setCode(v ?? '')}
          options={{ ...EDITOR_OPTS, placeholder: REPORT_HINT } as any}
          theme="vs-dark"
        />
      </div>
      <button className="btn-pri w-full shrink-0" onClick={save}>
        <Save size={12} /> {saved ? '\u2713 Saved' : 'Save'}
      </button>
      <p className="text-xs text-muted shrink-0">
        Leave code empty for a plain data table. The AI can draft the dashboard from your schema.
      </p>
    </div>
  )
}


// ── Router ────────────────────────────────────────────────────────────────────

export function RouterPanel({ nodeId }: { nodeId: string }) {
  const { activeFlowId, updateNodeData } = useStore()
  const data = useNodeData(nodeId)
  const [label, setLabel] = useState<string>(data.label ?? 'Router')
  const [branches, setBranches] = useState<any[]>(data.branches ?? [{ label: 'match', expr: 'len(df) > 0' }])
  const [defaultLabel, setDefaultLabel] = useState<string>(data.default_label ?? 'else')

  useEffect(() => {
    setLabel(data.label ?? 'Router')
    setBranches(data.branches ?? [{ label: 'match', expr: 'len(df) > 0' }])
    setDefaultLabel(data.default_label ?? 'else')
  }, [nodeId])

  const save = (next?: { branches?: any[]; default_label?: string; label?: string }) => {
    if (!activeFlowId) return
    updateNodeData(activeFlowId, nodeId, {
      label, branches, default_label: defaultLabel, ...next,
    })
  }

  const setBranch = (i: number, patch: any) => {
    const next = branches.map((b, j) => (j === i ? { ...b, ...patch } : b))
    setBranches(next); save({ branches: next })
  }
  const addBranch = () => {
    const next = [...branches, { label: `branch${branches.length + 1}`, expr: '' }]
    setBranches(next); save({ branches: next })
  }
  const removeBranch = (i: number) => {
    const next = branches.filter((_, j) => j !== i)
    setBranches(next); save({ branches: next })
  }

  return (
    <div className="p-4 space-y-3">
      <h3 className="text-xs font-semibold text-muted uppercase tracking-wider">Router</h3>
      <p className="text-xs text-muted">
        Branches are tested top to bottom; the first expression that is true wins and
        the other branches are skipped for that run. Expressions see the incoming
        frame as <code className="text-accent">df</code>.
      </p>

      <div>
        <label className="label">Label</label>
        <input className="input" value={label}
          onChange={(e) => setLabel(e.target.value)} onBlur={() => save()} />
      </div>

      <div className="space-y-2">
        <label className="label">Branches</label>
        {branches.map((b, i) => (
          <div key={i} className="rounded-lg border border-canvas-border p-2 space-y-1.5">
            <div className="flex items-center gap-2">
              <input className="input text-xs font-mono flex-1" value={b.label ?? ''}
                placeholder="name (handle)"
                onChange={(e) => setBranch(i, { label: e.target.value })} />
              <button className="btn-ghost p-1 text-red-400" title="Remove"
                onClick={() => removeBranch(i)}>✕</button>
            </div>
            <input className="input text-xs font-mono" value={b.expr ?? ''}
              placeholder="len(df) > 0"
              onChange={(e) => setBranch(i, { expr: e.target.value })} />
          </div>
        ))}
        <button className="btn-ghost text-xs w-full border border-canvas-border"
          onClick={addBranch}>+ Add branch</button>
      </div>

      <div>
        <label className="label">Default branch</label>
        <input className="input text-xs font-mono" value={defaultLabel}
          onChange={(e) => setDefaultLabel(e.target.value)}
          onBlur={() => save({ default_label: defaultLabel })} />
        <p className="text-xs text-muted mt-1">Taken when no expression matches.</p>
      </div>

      <div className="text-xs text-muted bg-canvas-bg rounded-lg border border-canvas-border p-2 space-y-0.5">
        <div className="text-slate-300 font-medium">Examples</div>
        <div><code className="text-accent">len(df) &gt; 0</code> — any rows?</div>
        <div><code className="text-accent">df.amount.sum() &gt; 10000</code> — threshold</div>
        <div><code className="text-accent">df.status.eq('error').any()</code> — contains errors</div>
        <div><code className="text-accent">vars['env'] == 'prod'</code> — global variable</div>
      </div>

      <button className="btn-pri w-full" onClick={() => save()}>
        <Save size={12} /> Save
      </button>
    </div>
  )
}
