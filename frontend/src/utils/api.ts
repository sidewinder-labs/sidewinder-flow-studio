import type { Project, Connection, Variable, AgentProvider, RunRow,
  ExploreSource, ExploreFrame, ExplorePersisted, ExploreField, ExploreQueryResult, ExploreTable, ExploreDrillConfig, BrowserSettings } from '@/types'

const BASE = '/api'

/** What deleting a project, or one of its flows, would affect. */
export interface DeletePreview {
  flows: { id: string; name: string; scheduled: boolean }[]
  persisted_tables: string[]
  running: boolean
}

/** One entry of the function reference (generated from the helpers' docstrings). */
export interface HelperDoc {
  name: string
  signature: string
  category: string
  engines: string[]
  sfs_specific: boolean
  source: string            // builtin | plugin:<dist> | the library (pandas, Polars, DuckDB)
  link: string | null
  library?: boolean         // from the user's function library (copied into flows on insert)
  deprecated?: boolean
  summary: string
  description: string
  args: { name: string; doc: string }[]
  returns: string
  example: string
}

/** A ready-to-insert code example for one engine. */
export interface Snippet {
  id: string
  topic: string
  topic_title: string
  engine: string
  title: string
  note?: string
  code: string
  needs: string[]
}

/** A function in the user's library (copied into flows on insert). */
export interface LibraryEntry {
  id: string
  name: string
  description: string
  code: string
  imports: string
  tags: string[]
  version: number
  content_hash: string
  deprecated: boolean
  deprecation_note: string
  updated_at: string
  usage?: { count: number; last_inserted: string | null;
            copies: { project_id: string; project: string; flow_id: string; flow: string; function: string; state: string }[] }
}

export type CopyState = 'current' | 'update' | 'modified' | 'deleted'

export interface FlowFunction {
  name: string
  kind: 'function' | 'class'
  source: string
  library?: { state: CopyState; library_id?: string; version?: number; latest_version?: number;
              deprecated?: boolean; deprecation_note?: string; library_code?: string; library_imports?: string }
}

export interface LibraryResult {
  functions: string; imports: string; provenance: Record<string, any>; added_imports: string[]; name: string
}

export interface TrashedProject { id: string; name: string; deleted_at: string | null; flows: number }

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`API ${res.status}: ${body}`)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

// ── Export / import ──────────────────────────────────────────────────────────

export interface FlowBundle {
  format: 'sfs-export'
  format_version: number
  sfs_version?: string
  kind: 'flow' | 'project'
  name: string
  description?: string
  flows: unknown[]
  manifest: { connections: string[]; custom_processors: string[]; engines: { name: string; plugin: string | null; version: string | null }[] }
  custom_processors?: { name: string; description?: string; engine?: string; code: string }[]
}

export interface ImportPreview {
  kind: 'flow' | 'project'
  name: string
  sfs_version?: string
  flows: string[]
  connections: string[]
  missing_connections: string[]
  local_connections: string[]
  missing_engines: { name: string; plugin: string | null; version: string | null }[]
  custom_processors: { name: string; here: boolean }[]
}

export interface ConnectionBundle {
  format: 'sfs-connections'
  format_version: number
  connections: { name: string; dialect: string; requires?: { password: boolean; extras: string[] } }[]
  variables?: { scope: string; key: string; value: string | null; description: string | null }[]
}

export interface ConnectionImportPreview {
  connections: { name: string; dialect: string; exists: boolean; requires: { password?: boolean; extras?: string[] } }[]
  variables: number
  variables_existing: string[]
}

export interface ConnectionImportResult {
  created: string[]; overwritten: string[]; renamed: Record<string, string>; skipped: string[]
  variables_added: number; variables_skipped: number
}

export const api = {
  diagnostics: () => req<{ install: 'docker' | 'desktop' | 'source'; version: string; text: string }>('/diagnostics/'),

  projects: {
    list:   ()           => req<Project[]>('/projects/'),
    get:    (id: string) => req<Project>(`/projects/${id}`),
    create: (name: string, description?: string) =>
      req<Project>('/projects/', { method: 'POST', body: JSON.stringify({ name, description }) }),
    save: (id: string, payload: object) =>
      req<Project>(`/projects/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
    delete: (id: string, dropPersisted = false) =>
      req<{ dropped_tables: string[] }>(`/projects/${id}?drop_persisted=${dropPersisted}`, { method: 'DELETE' }),
    deletePreview: (id: string, flowId?: string) =>
      req<DeletePreview>(`/projects/${id}/delete-preview${flowId ? `?flow_id=${encodeURIComponent(flowId)}` : ''}`),
    trash:   () => req<TrashedProject[]>('/projects/trash/'),
    restore: (id: string) => req<Project>(`/projects/trash/${id}/restore`, { method: 'POST' }),
    purge:   (id: string) => req<void>(`/projects/trash/${id}`, { method: 'DELETE' }),
    fromExample: (example: string, name?: string) =>
      req<Project>('/projects/from-example', { method: 'POST', body: JSON.stringify({ example, name }) }),
    exportBundle: (id: string, flowId?: string) =>
      req<FlowBundle>(`/projects/${id}/export${flowId ? `?flow_id=${encodeURIComponent(flowId)}` : ''}`),
    importPreview: (bundle: FlowBundle) =>
      req<ImportPreview>('/projects/import/preview', { method: 'POST', body: JSON.stringify({ bundle }) }),
    importBundle: (bundle: FlowBundle, connection_map: Record<string, string | null>, project_id?: string) =>
      req<Project>('/projects/import', { method: 'POST', body: JSON.stringify({ bundle, connection_map, project_id }) }),
  },

  library: {
    list: (includeDeprecated = true) =>
      req<LibraryEntry[]>(`/function-library/?include_deprecated=${includeDeprecated}`),
    create: (e: { name: string; code: string; description?: string; imports?: string; tags?: string[] }) =>
      req<LibraryEntry>('/function-library/', { method: 'POST', body: JSON.stringify(e) }),
    edit: (id: string, e: Partial<Pick<LibraryEntry, 'name' | 'code' | 'description' | 'imports' | 'tags'>>) =>
      req<LibraryEntry>(`/function-library/${id}`, { method: 'PUT', body: JSON.stringify(e) }),
    deprecate: (id: string, note: string, deprecated = true) =>
      req<LibraryEntry>(`/function-library/${id}/deprecate`, { method: 'POST', body: JSON.stringify({ note, deprecated }) }),
    remove: (id: string) => req<void>(`/function-library/${id}`, { method: 'DELETE' }),
    functionsIn: (functions: string, provenance: Record<string, any> = {}) =>
      req<{ functions: FlowFunction[]; error?: string }>('/function-library/functions',
        { method: 'POST', body: JSON.stringify({ functions, provenance }) }),
    proposeImports: (code: string, flow_imports: string) =>
      req<{ imports: string }>('/function-library/propose-imports', { method: 'POST', body: JSON.stringify({ code, flow_imports }) }),
    insert: (b: { functions: string; imports: string; provenance: Record<string, any>; entry_id: string;
                  on_conflict?: 'ask' | 'replace' | 'rename'; new_name?: string }) =>
      req<LibraryResult>('/function-library/insert', { method: 'POST', body: JSON.stringify(b) }),
    updateCopy: (b: { functions: string; imports: string; provenance: Record<string, any>; name: string }) =>
      req<LibraryResult>('/function-library/update-copy', { method: 'POST', body: JSON.stringify(b) }),
    exportAll: () => req<object>('/function-library/export'),
    importAll: (data: object) =>
      req<{ functions_added: number; functions_skipped: number; custom_processors_added: number }>(
        '/function-library/import', { method: 'POST', body: JSON.stringify(data) }),
  },

  connections: {
    list:   () => req<Connection[]>('/connections/'),
    create: (data: object) =>
      req<Connection>('/connections/', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: string, data: object) =>
      req<Connection>(`/connections/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: string) => req<void>(`/connections/${id}`, { method: 'DELETE' }),
    test:   (id: string) =>
      req<{ ok: boolean; error?: string }>(`/connections/${id}/test`, { method: 'POST' }),
    exportAll: (variables = true) => req<ConnectionBundle>(`/connections/export?variables=${variables}`),
    importPreview: (bundle: ConnectionBundle) =>
      req<ConnectionImportPreview>('/connections/import/preview', { method: 'POST', body: JSON.stringify({ bundle }) }),
    importAll: (bundle: ConnectionBundle, on_conflict: Record<string, string>, variables: boolean) =>
      req<ConnectionImportResult>('/connections/import', { method: 'POST', body: JSON.stringify({ bundle, on_conflict, variables }) }),
  },

  metadata: {
    listVars:  (scope = 'global') => req<Variable[]>(`/metadata/variables?scope=${scope}`),
    createVar: (data: object) =>
      req<Variable>('/metadata/variables', { method: 'POST', body: JSON.stringify(data) }),
    deleteVar: (id: string) => req<void>(`/metadata/variables/${id}`, { method: 'DELETE' }),
  },

  execute: {
    // Send the full live flow state so the backend always runs what's on screen,
    // not a potentially stale on-disk version.
    run: (project_id: string, flow_id: string, flow_state: object, start_from_node?: string, resume_from_node?: string) =>
      req<{ status: string }>('/execute/run', {
        method: 'POST',
        body: JSON.stringify({ project_id, flow_id, flow_state, start_from_node, resume_from_node }),
      }),
    flushPersist: (table_name: string, target = 'local') =>
      req<{ flushed: string }>('/execute/flush-persist', {
        method: 'POST',
        body: JSON.stringify({ table_name, target }),
      }),
    inspectFrame: (project_id: string, node_id: string, limit = 500) =>
      req<{ schema: object[]; rows: object[]; total: number }>(
        `/execute/frame/${project_id}/${node_id}?limit=${limit}`
      ),
    clearCache: (project_id: string, flow_id: string) =>
      req<void>(`/execute/clear-cache?project_id=${project_id}&flow_id=${flow_id}`, { method: 'POST' }),
  },

  runs: {
    list: (project_id?: string, limit = 50) =>
      req<RunRow[]>(`/runs/${project_id ? `?project_id=${project_id}&limit=${limit}` : `?limit=${limit}`}`),
  },

  frameColumns: (project_id: string, node_id: string) =>
    req<{ ran: boolean; columns: { name: string; dtype: string }[] }>(
      `/metadata/frame-columns?project_id=${encodeURIComponent(project_id)}&node_id=${encodeURIComponent(node_id)}`),

  capabilities: () => req<{
    engines: { name: string; label: string; description: string; tier: string;
               language: string; needs_target_connection: boolean; source: string;
               available: boolean; unavailable_reason?: string }[]
    plugins: { name: string; loaded: boolean; error?: string }[]
    templates?: { kind: string; name: string; code: string; description: string; source: string }[]
    examples?: { name: string; description: string; source: string }[]
    helpers?: HelperDoc[]
    snippets?: Snippet[]
    features: Record<string, boolean>
  }>('/capabilities/'),

  toCode: (body: { engine: string; node_data: object; project_id?: string; parent_ids: string[] }) =>
    req<{ code: string }>('/capabilities/to-code', { method: 'POST', body: JSON.stringify(body) }),

  buildFlow: (flow_state: object) =>
    req<{ nodes: { node_id: string; label: string; code: string }[]; errors: any[] }>(
      '/agent/build-flow', { method: 'POST', body: JSON.stringify({ flow_state }) }),

  customProcessors: {
    list: () => req<any[]>('/custom-processors/'),
    create: (data: object) =>
      req<any>('/custom-processors/', { method: 'POST', body: JSON.stringify(data) }),
    delete: (id: string) => req<void>(`/custom-processors/${id}`, { method: 'DELETE' }),
  },

  explore: {
    sources: () =>
      req<{ frames: ExploreFrame[]; persisted: ExplorePersisted[]; connections: { name: string; dialect: string; browser?: BrowserSettings }[] }>(
        '/explore/sources'),
    /** Every visible object, or one schema's, or a search across schemas (run in the database). */
    tables: (connection: string, opts: { schema?: string; q?: string } = {}) =>
      req<{ tables: ExploreTable[]; truncated: boolean }>(`/explore/tables?${new URLSearchParams(
        { connection, ...(opts.schema != null ? { schema: opts.schema } : {}), ...(opts.q ? { q: opts.q } : {}) })}`),
    schemas: (connection: string) =>
      req<{ schemas: { schema: string; count: number | null }[]; settings: BrowserSettings }>(
        `/explore/schemas?${new URLSearchParams({ connection })}`),
    browserSettings: (connection: string, settings: Partial<BrowserSettings>) =>
      req<{ settings: BrowserSettings }>('/explore/browser-settings', { method: 'PUT', body: JSON.stringify({ connection, settings }) }),
    schema: (source: ExploreSource) =>
      req<{ fields: ExploreField[]; rows?: number | null }>(
        `/explore/schema?${new URLSearchParams(source as Record<string, string>)}`),
    query: (source: ExploreSource, payload: object) =>
      req<ExploreQueryResult>('/explore/query', { method: 'POST', body: JSON.stringify({ source, payload }) }),
    node: (source: ExploreSource, payload: object, mode: 'dataset' | 'chart', engine?: 'sql' | 'pandas') =>
      req<{ node: { label: string; engine: string; code: string; target_connection?: string }; notes: string[] }>(
        '/explore/node', { method: 'POST', body: JSON.stringify({ source, payload, mode, engine }) }),
    drillConfig: (source: ExploreSource) =>
      req<{ config: ExploreDrillConfig | null }>(`/explore/drill-config?${new URLSearchParams(source as Record<string, string>)}`),
    saveDrillConfig: (source: ExploreSource, config: ExploreDrillConfig | null) =>
      req<{ config: ExploreDrillConfig | null }>('/explore/drill-config',
        { method: 'PUT', body: JSON.stringify({ source, config }) }),
    drill: (source: ExploreSource, payload: object, row: Record<string, unknown>) =>
      req<{ rows: Record<string, unknown>[]; truncated: boolean; sql: string; applied: string[];
            unmapped: string[]; connection: string; dialect: string }>(
        '/explore/drill', { method: 'POST', body: JSON.stringify({ source, payload, row }) }),
  },

  agent: {
    generate: (project_id: string, request: string, node_type: 'processor' | 'chart' | 'report', upstream_node_id?: string, engine: string = 'pandas') =>
      req<{ code: string | null; error: string | null; provider: string | null; had_context: boolean }>('/agent/generate', {
        method: 'POST',
        body: JSON.stringify({ project_id, request, node_type, upstream_node_id, engine }),
      }),
    listProviders: () =>
      req<AgentProvider[]>('/agent/providers'),
    createProvider: (data: object) =>
      req<AgentProvider>('/agent/providers', { method: 'POST', body: JSON.stringify(data) }),
    updateProvider: (id: string, data: object) =>
      req<AgentProvider>(`/agent/providers/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    activateProvider: (id: string) =>
      req<{ ok: boolean }>(`/agent/providers/${id}/activate`, { method: 'POST' }),
    testProvider: (id: string) =>
      req<{ ok: boolean; error?: string; sample?: string }>(`/agent/providers/${id}/test`, { method: 'POST' }),
    deleteProvider: (id: string) =>
      req<void>(`/agent/providers/${id}`, { method: 'DELETE' }),
  },
  // Desktop edition only (features.desktop): data locations, first run, logs
  desktop: {
    info: () => req<DesktopInfo>('/desktop/'),
    welcomeSeen: () => req<void>('/desktop/welcome-seen', { method: 'POST' }),
    showWelcomeAgain: () => req<void>('/desktop/welcome-seen', { method: 'DELETE' }),
    log: (name: string) =>
      req<{ name: string; size: number; truncated: boolean; text: string }>(`/desktop/logs/${encodeURIComponent(name)}`),
    open: (folder: 'data' | 'projects' | 'logs') =>
      req<void>('/desktop/open', { method: 'POST', body: JSON.stringify({ folder }) }),
  },
}

export interface DesktopInfo {
  version: string
  data_dir: string
  projects_dir: string
  logs_dir: string
  secret_key: string
  database: string
  logs: { name: string; size: number }[]
  welcome_seen: boolean
}
