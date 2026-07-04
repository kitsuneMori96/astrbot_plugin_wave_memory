import { fetchJson } from './client'

export interface BeliefTag {
  id: number
  name: string
  type?: string
}

export interface BeliefItem {
  id: number
  content: string
  type: 'self' | 'other' | 'world' | 'value'
  status: 'pending' | 'active' | 'archived' | 'pending_legacy'
  source?: string
  confidence?: number
  timestamp?: number
  last_reinforced?: number
  bot_id?: string
  sources?: string[]
}

export interface BeliefsFilters {
  page?: number
  size?: number
  type?: string
  status?: string
  bot_id?: string
  search?: string
}

export interface BeliefsResponse {
  items: BeliefItem[]
  total: number
  pending_count: number
}

export interface EvidenceMessage {
  id: string
  role: 'anchor' | 'before' | 'after'
  content: string
  sender_id?: string
  sender_name?: string
  timestamp: number
}

export interface EvidencePayload {
  ok: boolean
  belief?: BeliefItem
  anchor?: { id: string; content: string }
  messages: EvidenceMessage[]
  used_fallback: boolean
}

export function listBeliefs(filters: BeliefsFilters): Promise<BeliefsResponse> {
  const params = new URLSearchParams()
  if (filters.page) params.append('page', String(filters.page))
  if (filters.size) params.append('size', String(filters.size))
  if (filters.type) params.append('type', filters.type)
  if (filters.status) params.append('status', filters.status)
  if (filters.bot_id) params.append('bot_id', filters.bot_id)
  if (filters.search) params.append('search', filters.search)

  return fetchJson<BeliefsResponse>(`/api/beliefs?${params.toString()}`)
}

export function createBelief(payload: Partial<BeliefItem>): Promise<{ ok: boolean; id: number }> {
  return fetchJson<{ ok: boolean; id: number }>('/api/beliefs', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateBelief(id: number, payload: Partial<BeliefItem>): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/beliefs/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function deleteBelief(id: number): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/beliefs/${id}`, {
    method: 'DELETE',
  })
}

export function approveBelief(id: number): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/beliefs/${id}/approve`, {
    method: 'POST',
  })
}

export function archiveBelief(id: number): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/beliefs/${id}/archive`, {
    method: 'POST',
  })
}

export function getBeliefEvidence(id: number, before = 5, after = 5): Promise<EvidencePayload> {
  return fetchJson<EvidencePayload>(`/api/beliefs/${id}/evidence?before=${before}&after=${after}`)
}

export function batchArchiveBeliefsLegacy(): Promise<{ ok: boolean; archived: number }> {
  return fetchJson<{ ok: boolean; archived: number }>('/api/beliefs/batch-archive', {
    method: 'POST',
  })
}

export function batchArchiveSelectedBeliefs(ids: number[]): Promise<{ ok: boolean; archived: number }> {
  return fetchJson<{ ok: boolean; archived: number }>('/api/beliefs/batch-archive-selected', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })
}

export function batchApproveBeliefs(ids: number[]): Promise<{ ok: boolean; approved: number }> {
  return fetchJson<{ ok: boolean; approved: number }>('/api/beliefs/batch-approve', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })
}

export function batchDeleteBeliefs(ids: number[]): Promise<{ ok: boolean; deleted: number }> {
  return fetchJson<{ ok: boolean; deleted: number }>('/api/beliefs/batch-delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })
}
