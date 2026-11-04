/**
 * The help panel: guides (docs/ bundled), the function reference and examples
 * (from /api/capabilities, generated from the code), and the engines (from the
 * registry). Opened with the ? in the top bar, F1, Ctrl+K outside the code
 * editor, or openHelp(target) from anywhere. Works offline: nothing is fetched
 * beyond the app's own backend.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { marked } from 'marked'
import { BookOpen, Code2, Cpu, FlaskConical, Keyboard, Search, X, ExternalLink, Copy, Check, Rocket } from 'lucide-react'
import { useEngines, useHelpData, type EngineInfo } from '@/utils/engines'
import type { HelperDoc, Snippet } from '@/utils/api'
import { ALL_PAGES, GUIDES, engineGuide, resolveLink, type GuidePage } from './guides'
import { useCodeTarget } from './insert'

/** Open the help panel, optionally at "helper:fast_read", "engine:polars", "example:read-csv", a guide page id… */
export function openHelp(target?: string) {
  window.dispatchEvent(new CustomEvent('sfs:help', { detail: target }))
}

const SHORTCUTS: [string, string][] = [
  ['F1', 'Open help (anywhere)'],
  ['Ctrl+K', 'Search help (outside the code editor)'],
  ['Esc', 'Close help or a dialog'],
  ['Ctrl+S', 'Save the project'],
  ['Delete', 'Delete the selected nodes or edges'],
  ['Shift + drag', 'Select several nodes'],
  ['Ctrl + click', 'Add a node to the selection'],
  ['Double-click a flow tab', 'Rename the flow'],
  ['Double-click a note', 'Edit the note'],
  ['Right-click a Processor', 'Persist its output, resume from it'],
  ['Ctrl+Enter', 'Run the query (Explore SQL editor)'],
  ['Ctrl+F · Ctrl+H', 'Find · replace (code editor)'],
  ['Ctrl+/', 'Comment or uncomment lines (code editor)'],
  ['Alt+↑ · Alt+↓', 'Move lines up or down (code editor)'],
]

const ENGINE_LABEL: Record<string, string> = {
  pandas: 'pandas', polars: 'Polars', duckdb: 'DuckDB', sql: 'SQL pushdown', ibis: 'Ibis', chart: 'Chart node',
  fast_read: 'Database read',
}

type Item = { id: string; title: string; subtitle: string; text: string; kind: string }

export function HelpPanel() {
  const [open, setOpen] = useState(false)
  const [view, setView] = useState('GETTING_STARTED#')
  const [query, setQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  const { helpers, snippets } = useHelpData()
  const engines = useEngines()

  useEffect(() => {
    const onOpen = (e: Event) => {
      const target = (e as CustomEvent<string | undefined>).detail
      setOpen(true)
      if (target) { setView(target); setQuery('') }
      setTimeout(() => searchRef.current?.focus(), 30)
    }
    const onKey = (e: KeyboardEvent) => {
      const inEditor = (e.target as Element | null)?.closest?.('.monaco-editor')
      if (e.key === 'F1') { e.preventDefault(); e.stopPropagation(); openHelp() }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && !inEditor) { e.preventDefault(); openHelp() }
      else if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('sfs:help', onOpen)
    window.addEventListener('keydown', onKey, true)       // capture: before Monaco takes F1
    return () => { window.removeEventListener('sfs:help', onOpen); window.removeEventListener('keydown', onKey, true) }
  }, [])

  const topics = useMemo(() => {
    const seen = new Map<string, string>()
    snippets.forEach((s) => seen.set(s.topic, s.topic_title))
    return [...seen]
  }, [snippets])

  const index: Item[] = useMemo(() => [
    ...ALL_PAGES.map((p) => ({ id: p.id, kind: 'Guide', title: p.title, subtitle: p.docTitle, text: p.markdown })),
    ...helpers.map((h) => ({ id: `helper:${h.name}`, kind: h.sfs_specific ? 'SFS helper' : 'Library function',
      title: h.name, subtitle: h.summary, text: `${h.signature} ${h.description} ${h.args.map((a) => a.doc).join(' ')}` })),
    ...engines.map((e) => ({ id: `engine:${e.name}`, kind: 'Engine', title: e.label, subtitle: e.description,
      text: `${e.hint ?? ''} ${e.name}` })),
    ...topics.map(([t, title]) => ({ id: `example:${t}`, kind: 'Examples', title, subtitle: 'Examples',
      text: snippets.filter((s) => s.topic === t).map((s) => `${s.title} ${s.code} ${s.note ?? ''}`).join(' ') })),
    { id: 'shortcuts', kind: 'Shortcuts', title: 'Keyboard shortcuts', subtitle: '', text: SHORTCUTS.flat().join(' ') },
  ], [helpers, engines, topics, snippets])

  const results = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return []
    const words = q.split(/\s+/)
    return index
      .map((it) => {
        const hay = `${it.title} ${it.subtitle} ${it.text}`.toLowerCase()
        if (!words.every((w) => hay.includes(w))) return null
        const score = (it.title.toLowerCase().includes(q) ? 0 : 1) + (it.kind === 'Guide' ? 0.5 : 0)
        return { it, score }
      })
      .filter(Boolean)
      .sort((a, b) => a!.score - b!.score)
      .slice(0, 40)
      .map((r) => r!.it)
  }, [query, index])

  if (!open) return null
  const go = (id: string) => { setView(id); setQuery('') }

  return (
    <div className="fixed inset-0 z-[9000] flex justify-end bg-black/40" onMouseDown={(e) => e.target === e.currentTarget && setOpen(false)}>
      <div className="w-full max-w-5xl h-full flex flex-col bg-surface-raised border-l border-canvas-border shadow-node">
        {/* header with search */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-canvas-border">
          <BookOpen size={15} className="text-accent shrink-0" />
          <span className="text-sm font-semibold text-slate-100 shrink-0">Help</span>
          <div className="relative flex-1 max-w-xl ml-3">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-subtle" />
            <input ref={searchRef} className="input pl-8 text-sm" placeholder="Search functions, engines, examples, guides…"
                   value={query} onChange={(e) => setQuery(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter' && results[0]) go(results[0].id) }} />
          </div>
          <span className="text-xs text-subtle hidden md:block">F1 · Ctrl+K</span>
          <button className="ml-auto text-subtle hover:text-muted" title="Close (Esc)" onClick={() => setOpen(false)}><X size={16} /></button>
        </div>

        <div className="flex-1 flex min-h-0">
          <Nav view={view} go={go} helpers={helpers} engines={engines} topics={topics} />
          <div className="flex-1 overflow-y-auto px-6 py-5">
            {query.trim() ? (
              <Results results={results} go={go} query={query} />
            ) : (
              <Content view={view} go={go} helpers={helpers} engines={engines} snippets={snippets} />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Navigation ───────────────────────────────────────────────────────────────

function NavItem({ id, view, go, children, sub }: { id: string; view: string; go: (id: string) => void; children: React.ReactNode; sub?: boolean }) {
  const active = view === id
  return (
    <button onClick={() => go(id)}
      className={`block w-full text-left truncate rounded-md px-2 py-1 text-xs ${sub ? 'pl-4' : ''}
        ${active ? 'bg-accent/15 text-accent' : 'text-muted hover:text-slate-200 hover:bg-surface-overlay'}`}>
      {children}
    </button>
  )
}

function Heading({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return <div className="flex items-center gap-1.5 px-2 pt-3 pb-1 text-[11px] uppercase tracking-wider text-subtle">{icon}{children}</div>
}

function Nav({ view, go, helpers, engines, topics }: { view: string; go: (id: string) => void; helpers: HelperDoc[];
  engines: EngineInfo[]; topics: [string, string][] }) {
  const start = GUIDES.find((g) => g.doc === 'GETTING_STARTED')
  const others = GUIDES.filter((g) => g.doc !== 'GETTING_STARTED')
  const [openDoc, setOpenDoc] = useState<string | null>(null)
  const sfs = helpers.filter((h) => h.sfs_specific)
  const lib = helpers.filter((h) => !h.sfs_specific)
  return (
    <nav className="w-60 shrink-0 overflow-y-auto border-r border-canvas-border px-2 pb-6">
      <Heading icon={<Rocket size={11} />}>Getting started</Heading>
      {start?.pages.map((p) => <NavItem key={p.id} id={p.id} view={view} go={go}>{p.title}</NavItem>)}

      <Heading icon={<Code2 size={11} />}>SFS helpers</Heading>
      {sfs.map((h) => <NavItem key={h.name} id={`helper:${h.name}`} view={view} go={go}><code>{h.name}</code></NavItem>)}
      <Heading icon={<Code2 size={11} />}>Library functions</Heading>
      {lib.map((h) => <NavItem key={h.name} id={`helper:${h.name}`} view={view} go={go}><code>{h.name}</code></NavItem>)}

      <Heading icon={<Cpu size={11} />}>Engines</Heading>
      {engines.map((e) => <NavItem key={e.name} id={`engine:${e.name}`} view={view} go={go}>{e.label}</NavItem>)}

      <Heading icon={<FlaskConical size={11} />}>Examples</Heading>
      {topics.map(([t, title]) => <NavItem key={t} id={`example:${t}`} view={view} go={go}>{title}</NavItem>)}

      <Heading icon={<BookOpen size={11} />}>Guides</Heading>
      {others.map((g) => (
        <div key={g.doc}>
          <button className="block w-full text-left rounded-md px-2 py-1 text-xs text-slate-300 hover:bg-surface-overlay"
                  onClick={() => { setOpenDoc(openDoc === g.doc ? null : g.doc); go(g.pages[0].id) }}>
            {g.title}
          </button>
          {(openDoc === g.doc || g.pages.some((p) => p.id === view)) &&
            g.pages.slice(1).map((p) => <NavItem key={p.id} id={p.id} view={view} go={go} sub>{p.title}</NavItem>)}
        </div>
      ))}

      <Heading icon={<Keyboard size={11} />}>Keyboard</Heading>
      <NavItem id="shortcuts" view={view} go={go}>Keyboard shortcuts</NavItem>
    </nav>
  )
}

function Results({ results, go, query }: { results: Item[]; go: (id: string) => void; query: string }) {
  if (!results.length) return <div className="text-sm text-muted">Nothing matches “{query}”.</div>
  return (
    <div className="space-y-1">
      {results.map((r) => (
        <button key={r.id} onClick={() => go(r.id)} className="block w-full text-left rounded-lg px-3 py-2 hover:bg-surface-overlay">
          <div className="flex items-baseline gap-2">
            <span className="text-sm text-slate-100">{r.title}</span>
            <span className="text-[11px] text-subtle">{r.kind}</span>
          </div>
          {r.subtitle && <div className="text-xs text-muted truncate">{r.subtitle}</div>}
        </button>
      ))}
    </div>
  )
}

// ── Pages ────────────────────────────────────────────────────────────────────

function Content({ view, go, helpers, engines, snippets }: { view: string; go: (id: string) => void;
  helpers: HelperDoc[]; engines: EngineInfo[]; snippets: Snippet[] }) {
  if (view === 'shortcuts') return <Shortcuts />
  if (view.startsWith('helper:')) {
    const h = helpers.find((x) => x.name === view.slice(7))
    return h ? <HelperPage h={h} go={go} snippets={snippets} /> : <Missing what="function" />
  }
  if (view.startsWith('engine:')) {
    const e = engines.find((x) => x.name === view.slice(7))
    return e ? <EnginePage e={e} go={go} snippets={snippets} /> : <Missing what="engine" />
  }
  if (view.startsWith('example:')) {
    const list = snippets.filter((s) => s.topic === view.slice(8))
    return list.length ? <ExamplesPage list={list} /> : <Missing what="example" />
  }
  const page = ALL_PAGES.find((p) => p.id === view) ?? ALL_PAGES[0]
  return page ? <Guide page={page} go={go} /> : <Missing what="page" />
}

function Missing({ what }: { what: string }) {
  return <div className="text-sm text-muted">This {what} isn't available in this version.</div>
}

function Badge({ children, tone = 'slate' }: { children: React.ReactNode; tone?: 'accent' | 'violet' | 'slate' | 'amber' }) {
  const cls = { accent: 'bg-accent/15 text-accent', violet: 'bg-violet-500/15 text-violet-300',
                slate: 'bg-slate-500/15 text-slate-300', amber: 'bg-amber-500/15 text-amber-300' }[tone]
  return <span className={`text-[11px] rounded px-1.5 py-0.5 ${cls}`}>{children}</span>
}

function HelperPage({ h, go, snippets }: { h: HelperDoc; go: (id: string) => void; snippets: Snippet[] }) {
  const used = snippets.filter((s) => new RegExp(`\\b${h.name.replace('.', '\\.')}\\b`).test(s.code))
  return (
    <article className="max-w-3xl space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-semibold text-slate-100 font-mono">{h.name}</h1>
        {h.sfs_specific
          ? <Badge tone="accent">SFS helper · only inside SFS{h.source.startsWith('plugin:') ? ` · ${h.source}` : ''}</Badge>
          : <Badge tone="violet">{h.source} · works anywhere Python runs</Badge>}
        <Badge>{h.category}</Badge>
        {h.engines.map((e) => <Badge key={e}>{ENGINE_LABEL[e] ?? e}</Badge>)}
      </div>
      <CodeBlock code={h.signature} />
      <p className="text-sm text-slate-200">{h.summary}</p>
      {h.description && <p className="text-sm text-slate-300 leading-relaxed">{h.description}</p>}
      {h.args.length > 0 && (
        <div>
          <h2 className="text-xs uppercase tracking-wider text-subtle mb-1.5">Arguments</h2>
          <dl className="text-sm space-y-1.5">
            {h.args.map((a) => (
              <div key={a.name} className="flex gap-3">
                <dt className="font-mono text-accent shrink-0 w-32">{a.name}</dt>
                <dd className="text-slate-300">{a.doc}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
      {h.returns && <p className="text-sm text-slate-300"><span className="text-subtle">Returns </span>{h.returns}</p>}
      {h.example && (
        <div>
          <h2 className="text-xs uppercase tracking-wider text-subtle mb-1.5">Example</h2>
          <CodeBlock code={h.example} />
        </div>
      )}
      {h.link && (
        <a className="inline-flex items-center gap-1 text-sm text-accent hover:underline" href={h.link} target="_blank" rel="noreferrer">
          Official documentation <ExternalLink size={12} />
        </a>
      )}
      {used.length > 0 && (
        <div className="text-sm text-muted">
          Used in examples:{' '}
          {[...new Map(used.map((s) => [s.topic, s.topic_title]))].map(([t, title], i) => (
            <span key={t}>{i > 0 && ', '}<button className="text-accent hover:underline" onClick={() => go(`example:${t}`)}>{title}</button></span>
          ))}
        </div>
      )}
    </article>
  )
}

function EnginePage({ e, go, snippets }: { e: EngineInfo; go: (id: string) => void; snippets: Snippet[] }) {
  const guide = engineGuide()
  const topics = [...new Map(snippets.filter((s) => s.engine === e.name).map((s) => [s.topic, s.topic_title]))]
  return (
    <article className="max-w-3xl space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-semibold text-slate-100">{e.label}</h1>
        <Badge>{e.language === 'sql' ? 'SQL' : 'Python'}</Badge>
        {e.source?.startsWith('plugin:') && <Badge tone="violet">from {e.source.slice(7)}</Badge>}
        {!e.available && <Badge tone="amber">unavailable: {e.unavailable_reason}</Badge>}
      </div>
      <p className="text-sm text-slate-200">{e.description}</p>
      {e.hint && <p className="text-sm text-slate-300 leading-relaxed whitespace-pre-line">{e.hint}</p>}
      {e.params && e.params.length > 0 && (
        <div>
          <h2 className="text-xs uppercase tracking-wider text-subtle mb-1.5">Settings (no code needed)</h2>
          <dl className="text-sm space-y-1">
            {e.params.map((p: any) => (
              <div key={p.key} className="flex gap-3">
                <dt className="text-slate-200 shrink-0 w-36">{p.label ?? p.key}{p.required ? ' *' : ''}</dt>
                <dd className="text-slate-400">{p.help ?? p.type}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
      {topics.length > 0 && (
        <div className="text-sm text-muted">
          Examples in {e.label}:{' '}
          {topics.map(([t, title], i) => (
            <span key={t}>{i > 0 && ', '}<button className="text-accent hover:underline" onClick={() => go(`example:${t}`)}>{title}</button></span>
          ))}
        </div>
      )}
      {guide && <button className="text-sm text-accent hover:underline" onClick={() => go(guide.id)}>All engines compared, in the guide →</button>}
    </article>
  )
}

function ExamplesPage({ list }: { list: Snippet[] }) {
  return (
    <article className="max-w-3xl space-y-6">
      <h1 className="text-xl font-semibold text-slate-100">{list[0].topic_title}</h1>
      {list.map((s) => <ExampleCard key={s.id} s={s} />)}
    </article>
  )
}

function ExampleCard({ s }: { s: Snippet }) {
  const target = useCodeTarget()
  const [done, setDone] = useState(false)
  const wantsChart = s.engine === 'chart'
  const fits = target && (wantsChart ? target.kind === 'chart' : target.kind === 'processor')
  const sameEngine = fits && (wantsChart || target!.engine === s.engine)
  const insert = (switchTo?: string) => { target!.insert(s.code, switchTo); setDone(true); setTimeout(() => setDone(false), 1500) }
  const why = !target ? `Open a ${wantsChart ? 'Chart View' : 'Processor'} node first (click it on the canvas)`
    : !fits ? `This example is for a ${wantsChart ? 'Chart View' : 'Processor'} node` : ''

  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-100">{s.title}</h2>
        <Badge>{ENGINE_LABEL[s.engine] ?? s.engine}</Badge>
        {s.needs.length > 0 && <span className="text-[11px] text-subtle">needs {s.needs.join(' and ')} from upstream</span>}
      </div>
      {s.note && <p className="text-sm text-slate-300">{s.note}</p>}
      <CodeBlock code={s.code} />
      <div className="flex flex-wrap items-center gap-2">
        {fits && sameEngine && (
          <button className="btn-pri text-xs" onClick={() => insert()}>{done ? '✓ Inserted' : 'Insert into node'}</button>
        )}
        {fits && !sameEngine && (
          <>
            <button className="btn-pri text-xs" onClick={() => insert(s.engine)}>
              {done ? '✓ Inserted' : `Switch node to ${ENGINE_LABEL[s.engine] ?? s.engine} and insert`}
            </button>
            <button className="btn-ghost text-xs" onClick={() => insert()}>Insert without switching</button>
          </>
        )}
        {!fits && <span className="text-xs text-subtle">{why}</span>}
      </div>
    </section>
  )
}

function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => navigator.clipboard?.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200) })
  return (
    <div className="relative group">
      <pre className="text-xs font-mono text-slate-200 bg-canvas-bg border border-canvas-border rounded-lg p-3 overflow-x-auto whitespace-pre">{code.replace(/\n$/, '')}</pre>
      <button className="absolute top-1.5 right-1.5 opacity-0 group-hover:opacity-100 text-subtle hover:text-slate-200 p-1"
              title="Copy" onClick={copy}>{copied ? <Check size={12} /> : <Copy size={12} />}</button>
    </div>
  )
}

function Guide({ page, go }: { page: GuidePage; go: (id: string) => void }) {
  const html = useMemo(() => marked.parse(page.markdown, { async: false, gfm: true }) as string, [page])
  const onClick = (e: React.MouseEvent) => {
    const a = (e.target as Element).closest('a')
    if (!a) return
    const href = a.getAttribute('href') ?? ''
    const local = resolveLink(href, page.doc)
    if (local) { e.preventDefault(); go(local.id) }
    else if (/^https?:/.test(href)) { a.setAttribute('target', '_blank'); a.setAttribute('rel', 'noreferrer') }
    else e.preventDefault()                                  // repo-relative links that aren't guides
  }
  return (
    <article className="max-w-3xl">
      <div className="text-[11px] uppercase tracking-wider text-subtle mb-1">{page.docTitle}</div>
      <h1 className="text-xl font-semibold text-slate-100 mb-4">{page.title}</h1>
      {/* Bundled docs from this repository: trusted content. */}
      <div className="help-prose" onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />
    </article>
  )
}

function Shortcuts() {
  return (
    <article className="max-w-xl">
      <h1 className="text-xl font-semibold text-slate-100 mb-4">Keyboard shortcuts</h1>
      <table className="w-full text-sm">
        <tbody>
          {SHORTCUTS.map(([k, what]) => (
            <tr key={k} className="border-b border-canvas-border/60">
              <td className="py-1.5 pr-4 font-mono text-xs text-accent whitespace-nowrap">{k}</td>
              <td className="py-1.5 text-slate-300">{what}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-subtle mt-3">On a Mac, use Cmd for Ctrl.</p>
    </article>
  )
}
