import { fetchJson } from './client'
import type { OperationState, PageResponse } from '@/components/shared/types'

export type MaintenanceJobStatus = 'pending' | 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | string
export type MaintenanceJobKind = 'maintenance.import.run' | 'maintenance.tag_backfill.run' | 'maintenance.tag_audit.run' | string

export interface MaintenanceJob {
  run_id: string
  request_id: string
  status: MaintenanceJobStatus
  kind?: MaintenanceJobKind
  cursor?: Record<string, unknown> | null
  progress?: Record<string, unknown> | null
  result?: Record<string, unknown> | null
  error_code?: string | null
  error_message?: string | null
  created_at?: number
  updated_at?: number
  operation?: { id?: string; kind?: MaintenanceJobKind; status?: OperationState }
  checkpoint_url?: string
  logs_url?: string
  cancel_url?: string
}

export interface MaintenanceCheckpoint {
  job_id: string
  status: MaintenanceJobStatus
  checkpoint?: Record<string, unknown> | null
  progress?: Record<string, unknown> | null
  updated_at?: number | null
  source?: string
}

export interface MaintenanceJobAccepted {
  ok: boolean
  accepted: true
  request_id: string
  job_id: string
  status: MaintenanceJobStatus
  operation?: { id?: string; kind?: MaintenanceJobKind; status?: OperationState }
}

export type TagAuditStrategy = 'mixed' | 'low_quality' | 'high_freq'

export interface TagBackfillRequest {
  tag_batch_size: number
  skip_short_min_length: number
}

export interface MaintenanceJobDetail {
  item: MaintenanceJob
  checked_at: number | null
  source: string
}

export interface MaintenanceLog {
  level: string
  event: string
  at?: number | null
  data?: unknown
}

export function listMaintenanceJobs(limit: number, offset: number): Promise<PageResponse<MaintenanceJob>> {
  return fetchJson<PageResponse<MaintenanceJob>>(`/api/maintenance/jobs?limit=${limit}&offset=${offset}`)
}

export function getMaintenanceJob(jobId: string, signal?: AbortSignal): Promise<MaintenanceJobDetail> {
  return fetchJson<MaintenanceJobDetail>(`/api/maintenance/jobs/${encodeURIComponent(jobId)}`, { signal })
}

export function getMaintenanceLogs(jobId: string, limit: number, offset: number): Promise<PageResponse<MaintenanceLog>> {
  return fetchJson<PageResponse<MaintenanceLog>>(`/api/maintenance/jobs/${encodeURIComponent(jobId)}/logs?limit=${limit}&offset=${offset}`)
}

export function getMaintenanceCheckpoint(jobId: string): Promise<MaintenanceCheckpoint> {
  return fetchJson<MaintenanceCheckpoint>(`/api/maintenance/jobs/${encodeURIComponent(jobId)}/checkpoint`)
}

export function startTagBackfill(options: TagBackfillRequest): Promise<MaintenanceJobAccepted> {
  const params = new URLSearchParams({
    tag_batch_size: String(Math.max(1, Math.min(50, Math.round(options.tag_batch_size)))),
    tag_write_policy: 'missing_only',
    skip_short_min_length: String(Math.max(0, Math.round(options.skip_short_min_length))),
  })
  return fetchJson<MaintenanceJobAccepted>(`/api/tags/batch-extract?${params.toString()}`, { method: 'POST' })
}

export function startTagAudit(strategy: TagAuditStrategy, totalCount: number): Promise<MaintenanceJobAccepted> {
  const params = new URLSearchParams({
    strategy,
    total_count: String(Math.max(10, Math.min(2000, Math.round(totalCount)))),
  })
  return fetchJson<MaintenanceJobAccepted>(`/api/tags/audit/trigger?${params.toString()}`, { method: 'POST' })
}

export async function waitForMaintenanceJob(
  jobId: string,
  onUpdate: (job: MaintenanceJob) => void,
  signal?: AbortSignal,
): Promise<MaintenanceJob> {
  while (!signal?.aborted) {
    let item: MaintenanceJob
    try {
      item = (await getMaintenanceJob(jobId, signal)).item
    } catch (error) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
      throw error
    }
    onUpdate(item)
    if (['succeeded', 'failed', 'cancelled'].includes(item.status)) return item
    await new Promise<void>((resolve, reject) => {
      const onAbort = () => {
        window.clearTimeout(timer)
        reject(new DOMException('Aborted', 'AbortError'))
      }
      const timer = window.setTimeout(() => {
        signal?.removeEventListener('abort', onAbort)
        resolve()
      }, 1500)
      signal?.addEventListener('abort', onAbort, { once: true })
    })
  }
  throw new DOMException('Aborted', 'AbortError')
}

export function cancelMaintenanceJob(jobId: string): Promise<{
  ok: boolean
  operation: { id: string; kind: string; status: OperationState }
  revision: number | null
  item: MaintenanceJob
}> {
  return fetchJson(`/api/maintenance/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' })
}
