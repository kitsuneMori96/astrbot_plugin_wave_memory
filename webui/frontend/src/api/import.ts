import { fetchJson } from './client'

export interface ImportSourceItem {
  id: string
  name: string
  count: number
  has_adapter: boolean
  description: string
  imported_pct?: number
  remaining?: number
}

export interface ImportSourcesPayload {
  sources: ImportSourceItem[]
}

export function getImportSources(): Promise<ImportSourcesPayload> {
  return fetchJson<ImportSourcesPayload>('/api/memories/import/sources')
}
