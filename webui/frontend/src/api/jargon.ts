import { fetchJson } from './client'
import { holymanUpdateCheckPath, type HolymanUpdateCheckPayload } from './jargonUpdate'

export type { HolymanUpdateCheckPayload } from './jargonUpdate'

export interface JargonItem {
  id: number
  word: string
  meaning: string
  frequency: number
  status: 'pending' | 'confirmed' | 'rejected'
  group_id?: string
  is_global: boolean
  source?: string
  source_memory_id?: number
  source_context?: string
  candidate_type?: string
  reject_reason?: string
}

export interface JargonsFilters {
  page?: number
  size?: number
  status?: string
  group_id?: string
  search?: string
  include_rejected?: boolean
}

export interface JargonsResponse {
  items: JargonItem[]
  total: number
  pending_count: number
}

export interface JargonEvidencePayload {
  ok: boolean
  jargon?: JargonItem
  anchor?: { id: string; content: string }
  messages: Array<{
    id: string
    role: string
    content: string
    sender_name?: string
    sender_id?: string
    timestamp: number
  }>
  fallback_contexts?: string[]
  used_fallback: boolean
}

export interface HolymanCategory {
  id: string
  label: string
  count: number
}

export interface HolymanStatusPayload {
  local_version: string
  remote_version: string
  asset_status: string
  update_available?: boolean
  is_update_available?: boolean
  update_check?: HolymanUpdateCheckPayload
  checked_at?: string
  update_cached?: boolean
  warning?: string
  categories: HolymanCategory[]
  local_count?: number
  items_count?: number
  concepts_count?: number
  examples_count?: number
  corpus_count?: number
  candidates_count?: number
  layers?: {
    catchphrases?: any[]
    concepts?: any[]
    quotes_knowledge?: any[]
    corpus?: { items?: any[]; count?: number; reference_only?: boolean; [key: string]: any }
    candidates?: any[]
    blocked?: Record<string, any>
  }
  items?: any[]
  corpus?: any[]
  blocked?: Record<string, any>
  corpus_summary?: { count?: number; reference_only?: boolean; [key: string]: any }
  manifest?: any
  manifest_summary?: {
    source_count?: number
    parse_statuses?: Record<string, number>
    repo?: string
  }
  quality_report?: any
  quality_summary?: {
    status?: string
    declared_corpus_count?: number
    parsed_corpus_count?: number
    error_count?: number
  }
  phrases?: any[] // 新增用于 TAB 下列出广域语料
  concepts?: any[] // 新增分层
  examples?: any[]
  candidates?: any[]
}

function pageOffset(filters: JargonsFilters): { limit: number; offset: number } {
  const limit = Math.max(1, Number(filters.size || 50))
  const page = Math.max(1, Number(filters.page || 1))
  return { limit, offset: (page - 1) * limit }
}

export function listJargons(filters: JargonsFilters): Promise<JargonsResponse> {
  const params = new URLSearchParams()
  const { limit, offset } = pageOffset(filters)
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  if (filters.status) params.set('status', filters.status)
  if (filters.group_id) params.set('group_id', filters.group_id)
  if (filters.search) params.set('search', filters.search)
  if (filters.include_rejected) params.set('include_rejected', 'true')

  return fetchJson<JargonsResponse>(`/api/jargon?${params.toString()}`)
}

export function createJargon(payload: Partial<JargonItem>): Promise<{ ok: boolean; id: number }> {
  return fetchJson<{ ok: boolean; id: number }>('/api/jargon', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateJargon(id: number, payload: Partial<JargonItem>): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/jargon/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function deleteJargon(id: number): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/jargon/${id}`, {
    method: 'DELETE',
  })
}

export function reviewJargon(id: number, action: 'approve' | 'reject', rejectReason = ''): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/jargon/${id}/review/${action}`, {
    method: 'POST',
    body: JSON.stringify({ reject_reason: rejectReason }),
  })
}

export function toggleJargonGlobal(id: number): Promise<{ ok: boolean; is_global: boolean }> {
  return fetchJson<{ ok: boolean; is_global: boolean }>(`/api/jargon/${id}/toggle_global`, {
    method: 'POST',
  })
}

export function getJargonEvidence(id: number, before = 15, after = 15): Promise<JargonEvidencePayload> {
  return fetchJson<JargonEvidencePayload>(`/api/jargon/${id}/context?before=${before}&after=${after}`)
}

export function getHolymanStatus(): Promise<HolymanStatusPayload> {
  return fetchJson<HolymanStatusPayload>('/api/jargon/holyman')
}

export function checkHolymanUpdate(force = false): Promise<HolymanUpdateCheckPayload> {
  return fetchJson<HolymanUpdateCheckPayload>(holymanUpdateCheckPath(force))
}

export interface HolymanSyncPayload {
  use_proxy?: boolean
}

export interface HolymanSyncPreviewPayload {
  ok: boolean
  will_update: boolean
  asset_status: string
  local_version: string
  remote_version: string
  local_content_hash: string
  remote_content_hash: string
  local_counts: Record<string, number>
  remote_counts: Record<string, number>
  delta_counts: Record<string, number>
  samples?: {
    added_phrases?: string[]
    removed_phrases?: string[]
    changed_phrases?: string[]
  }
  safety?: {
    asset_type?: string
    runtime_policy?: string
    corpus_reference_only?: boolean
    corpus_safe_for_prompt?: boolean
    activatable_scope?: string
    statement?: string
  }
  quality_report?: any
  error?: string
}

function postHolymanLong<T>(path: string, payload: HolymanSyncPayload = {}, timeoutMs = 120000): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  return fetchJson<T>(path, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal: controller.signal,
  }).finally(() => window.clearTimeout(timeout))
}

export function previewHolymanSync(payload: HolymanSyncPayload = { use_proxy: true }): Promise<HolymanSyncPreviewPayload> {
  return postHolymanLong<HolymanSyncPreviewPayload>('/api/jargon/holyman/sync/preview', payload)
}

export function syncHolymanAssets(payload: HolymanSyncPayload = { use_proxy: true }): Promise<any> {
  return postHolymanLong<any>('/api/jargon/holyman/sync', payload)
}

export interface ToggleHolymanPhrasePayload {
  word: string
  meaning?: string
  activate: boolean
}

export interface ToggleHolymanPhraseResponse {
  ok: boolean
  db_id?: number
  error?: string
}

export function toggleHolymanPhrase(payload: ToggleHolymanPhrasePayload): Promise<ToggleHolymanPhraseResponse> {
  return fetchJson<ToggleHolymanPhraseResponse>('/api/jargon/holyman/toggle', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export interface BatchReviewHolymanCandidatesPayload {
  ids: Array<number | string>
  words: string[]
  action: 'approve' | 'reject'
}

export interface BatchReviewHolymanCandidatesResponse {
  ok: boolean
  reviewed_count: number
  blocked_count: number
  action: string
  error?: string
}

export function batchReviewHolymanCandidates(
  payload: BatchReviewHolymanCandidatesPayload,
): Promise<BatchReviewHolymanCandidatesResponse> {
  return fetchJson<BatchReviewHolymanCandidatesResponse>('/api/jargon/holyman/candidates/batch-review', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export interface AddHolymanBlocklistPayload {
  word: string
  reason?: string
}

export interface AddHolymanBlocklistResponse {
  ok: boolean
  word: string
  error?: string
}

export function addHolymanBlocklist(payload: AddHolymanBlocklistPayload): Promise<AddHolymanBlocklistResponse> {
  return fetchJson<AddHolymanBlocklistResponse>('/api/jargon/holyman/blocklist', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function batchDeleteJargons(ids: number[]): Promise<{ ok: boolean; deleted: number }> {
  const res = await fetchJson<{ ok: boolean; deleted?: number; deleted_count?: number }>('/api/jargon/batch-delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })
  return { ok: res.ok, deleted: res.deleted ?? res.deleted_count ?? 0 }
}

export async function batchReviewJargons(ids: number[], action: 'approve' | 'reject'): Promise<{ ok: boolean; reviewed: number }> {
  const res = await fetchJson<{ ok: boolean; reviewed?: number; reviewed_count?: number }>('/api/jargon/batch-review', {
    method: 'POST',
    body: JSON.stringify({ ids, action }),
  })
  return { ok: res.ok, reviewed: res.reviewed ?? res.reviewed_count ?? 0 }
}
