import { fetchJson } from './client'
import type { OperationState } from '@/components/shared/types'
import { waitForMaintenanceJob, type MaintenanceJob } from './maintenance'

export interface ImportSourceItem {
  id: string
  name: string
  count: number
  type?: string
  has_adapter: boolean
  description: string
  imported_pct?: number
  remaining?: number
}

export interface ImportSourcesPayload {
  sources: ImportSourceItem[]
}

export interface ImportPreflightPayload {
  source: ImportSourceItem & { target?: string }
  preview: {
    total_count: number
    estimated_imported: number
    estimated_remaining: number
    limit: number
    re_embed: boolean
    extract_tags: boolean
  }
  preflight_token: string
  checked_at: number
  source_status: 'available' | 'unknown'
}

export interface ImportAcceptedPayload {
  ok: boolean
  accepted: true
  request_id: string
  job_id: string
  status: string
  operation: { id: string; kind: string; status: OperationState }
  revision: null
}

export interface ImportRequestOptions {
  limit: number
  extract_tags: boolean
  tag_batch_size: number
  tag_write_policy: 'missing_only'
}

export function getImportSources(): Promise<ImportSourcesPayload> {
  return fetchJson<ImportSourcesPayload>('/api/import/sources')
}

export function preflightImport(sourceId: string, options: ImportRequestOptions): Promise<ImportPreflightPayload> {
  return fetchJson<ImportPreflightPayload>('/api/import/preflight', {
    method: 'POST',
    body: JSON.stringify({ source_id: sourceId, ...options }),
  })
}

export function startImport(sourceId: string, preflightToken: string): Promise<ImportAcceptedPayload> {
  return fetchJson<ImportAcceptedPayload>('/api/import/start', {
    method: 'POST',
    body: JSON.stringify({ source_id: sourceId, preflight_token: preflightToken }),
  })
}

export function waitForImportJob(
  jobId: string,
  onUpdate: (job: MaintenanceJob) => void,
  signal?: AbortSignal,
): Promise<MaintenanceJob> {
  return waitForMaintenanceJob(jobId, onUpdate, signal)
}
