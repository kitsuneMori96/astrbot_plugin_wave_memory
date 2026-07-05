import { fetchJson } from './client'

export interface JargonItem {
  id: number
  word: string
  meaning: string
  frequency: number
  status: 'pending' | 'confirmed' | 'rejected'
  group_id?: string
  is_global: boolean
  source_memory_id?: number
  source_context?: string
}

export interface JargonsFilters {
  page?: number
  size?: number
  status?: string
  group_id?: string
  search?: string
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
  categories: HolymanCategory[]
  local_count?: number
  items_count?: number
  concepts_count?: number
  examples_count?: number
  corpus_count?: number
  candidates_count?: number
  phrases?: any[] // 新增用于 TAB 下列出广域语料
  concepts?: any[] // 新增分层
  examples?: any[]
  candidates?: any[]
}

export function listJargons(filters: JargonsFilters): Promise<JargonsResponse> {
  const params = new URLSearchParams()
  if (filters.page) params.append('page', String(filters.page))
  if (filters.size) params.append('size', String(filters.size))
  if (filters.status) params.append('status', filters.status)
  if (filters.group_id) params.append('group_id', filters.group_id)
  if (filters.search) params.append('search', filters.search)

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
  return fetchJson<{ ok: boolean; is_global: boolean }>(`/api/jargon/${id}/toggle-global`, {
    method: 'POST',
  })
}

export function getJargonEvidence(id: number, before = 15, after = 15): Promise<JargonEvidencePayload> {
  return fetchJson<JargonEvidencePayload>(`/api/jargon/${id}/evidence?before=${before}&after=${after}`)
}

export function getHolymanStatus(): Promise<any> {
  return fetchJson<any>('/api/jargon/holyman')
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

export function batchDeleteJargons(ids: number[]): Promise<{ ok: boolean; deleted: number }> {
  return fetchJson<{ ok: boolean; deleted: number }>('/api/jargon/batch/delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })
}

export function batchReviewJargons(ids: number[], action: 'approve' | 'reject'): Promise<{ ok: boolean; reviewed: number }> {
  return fetchJson<{ ok: boolean; reviewed: number }>('/api/jargon/batch/review', {
    method: 'POST',
    body: JSON.stringify({ ids, action }),
  })
}
