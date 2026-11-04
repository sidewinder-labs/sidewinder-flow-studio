/**
 * The guides: the Markdown in the repository's docs/ folder, bundled at build
 * time (no runtime fetch, so the help works offline and in the desktop app).
 * Each guide is split at its "## " headings into pages, so search and links can
 * land on a section.
 *
 * Builds must be able to see docs/ next to frontend/: the Docker builds copy it
 * there (see frontend/Dockerfile and Dockerfile.single).
 */
const raw = import.meta.glob(['../../../../docs/*.md', '!**/HELPERS.md'], { query: '?raw', import: 'default', eager: true }) as
  Record<string, string>

export interface GuidePage {
  id: string          // "CAPABILITIES#reading-data-fast-fast_read"
  doc: string         // "CAPABILITIES"
  docTitle: string
  title: string
  markdown: string
}

/** In-app order; HELPERS.md is left out (the Functions section shows the same, live). */
export const GUIDE_ORDER = ['GETTING_STARTED', 'CAPABILITIES', 'CURVE_FITTING', 'PLUGINS', 'INSTALLATION']

export const slug = (s: string) =>
  s.toLowerCase().replace(/`/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')

function split(doc: string, md: string): GuidePage[] {
  const docTitle = md.match(/^# (.+)$/m)?.[1]?.trim() ?? doc
  const pages: GuidePage[] = []
  const parts = md.split(/^## /m)
  const intro = parts[0].replace(/^# .+$/m, '').trim()
  if (intro) pages.push({ id: `${doc}#`, doc, docTitle, title: docTitle, markdown: intro })
  for (const part of parts.slice(1)) {
    const nl = part.indexOf('\n')
    const title = (nl < 0 ? part : part.slice(0, nl)).trim()
    const body = nl < 0 ? '' : part.slice(nl + 1).replace(/\n---\s*$/, '').trim()
    pages.push({ id: `${doc}#${slug(title)}`, doc, docTitle, title, markdown: body })
  }
  return pages
}

const byDoc: Record<string, GuidePage[]> = {}
for (const [path, md] of Object.entries(raw)) {
  const doc = path.split('/').pop()!.replace(/\.md$/, '')
  if (GUIDE_ORDER.includes(doc)) byDoc[doc] = split(doc, md)
}

export const GUIDES: { doc: string; title: string; pages: GuidePage[] }[] = GUIDE_ORDER
  .filter((d) => byDoc[d])
  .map((d) => ({ doc: d, title: byDoc[d][0]?.docTitle ?? d, pages: byDoc[d] }))

export const ALL_PAGES: GuidePage[] = GUIDES.flatMap((g) => g.pages)

/** The page a Markdown link points at: "CAPABILITIES.md#scheduling", "docs/PLUGINS.md", "#x". */
export function resolveLink(href: string, fromDoc: string): GuidePage | null {
  const m = href.match(/(?:^|\/)([A-Z_]+)\.md(?:#(.*))?$/) ?? (href.startsWith('#') ? [href, fromDoc, href.slice(1)] : null)
  if (!m) return null
  const [, doc, anchor] = m
  const pages = byDoc[doc]
  if (!pages) return null
  return (anchor && pages.find((p) => p.id === `${doc}#${slug(anchor)}` || slug(p.title) === slug(anchor))) || pages[0]
}

/** The guide page for an engine, for the "?" beside the engine picker. */
export function engineGuide(): GuidePage | null {
  return ALL_PAGES.find((p) => p.doc === 'CAPABILITIES' && /engines/i.test(p.title)) ?? null
}
