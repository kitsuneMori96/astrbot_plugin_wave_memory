import { fetchJson } from './client'

export type BeliefType = 'self_identity' | 'person_judgment' | 'world_view' | 'preference'
export type LegacyBeliefType = 'self' | 'other' | 'world' | 'value'

const LEGACY_TYPE_MAP: Record<LegacyBeliefType, BeliefType> = {
  self: 'self_identity',
  other: 'person_judgment',
  world: 'world_view',
  value: 'preference',
}

export interface BeliefTag {
  id: number
  name: string
  type?: string
}

export interface BeliefItem {
  id: number
  content: string
  type: BeliefType
  status: 'pending' | 'active' | 'archived' | 'pending_legacy'
  source?: string
  confidence?: number
  strength?: number
  timestamp?: number
  created_at?: number
  last_reinforced?: number
  updated_at?: number
  bot_id?: string
  sources?: string[]
}

export interface BeliefsFilters {
  page?: number
  size?: number
  type?: 'self_identity' | 'person_judgment' | 'world_view' | 'preference' | ''
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
  relationship_events?: any[]  // 新增对齐旧版
  episodes?: any[]             // 新增对齐旧版自省独白
  memories?: any[]             // 新增对齐旧版
}

function normalizeBeliefType(type: unknown): BeliefType {
  const raw = String(type || '').trim()
  if (raw === 'self_identity' || raw === 'person_judgment' || raw === 'world_view' || raw === 'preference') {
    return raw
  }
  return LEGACY_TYPE_MAP[raw as LegacyBeliefType] ?? 'world_view'
}

function normalizeBeliefItem(item: any): BeliefItem {
  return {
    ...item,
    type: normalizeBeliefType(item?.type),
    confidence: item?.confidence ?? item?.strength,
    strength: item?.strength ?? item?.confidence,
    timestamp: item?.timestamp ?? item?.created_at,
    last_reinforced: item?.last_reinforced ?? item?.updated_at,
  }
}

function normalizeBeliefPayload(payload: Partial<BeliefItem>): Record<string, unknown> {
  const next: Record<string, unknown> = { ...payload }
  if (payload.type) next.type = normalizeBeliefType(payload.type)
  if (payload.confidence != null && next.strength == null) next.strength = payload.confidence
  delete next.confidence
  delete next.timestamp
  delete next.last_reinforced
  delete next.created_at
  delete next.updated_at
  return next
}

function pageOffset(filters: BeliefsFilters): { limit: number; offset: number } {
  const limit = Math.max(1, Number(filters.size || 50))
  const page = Math.max(1, Number(filters.page || 1))
  return { limit, offset: (page - 1) * limit }
}

export async function listBeliefs(filters: BeliefsFilters): Promise<BeliefsResponse> {
  const params = new URLSearchParams()
  const { limit, offset } = pageOffset(filters)
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  if (filters.type) params.set('type', filters.type)
  if (filters.status) params.set('status', filters.status)
  if (filters.bot_id) params.set('bot_id', filters.bot_id)
  if (filters.search) params.set('search', filters.search)

  const res = await fetchJson<BeliefsResponse>(`/api/beliefs?${params.toString()}`)
  return { ...res, items: (res.items ?? []).map(normalizeBeliefItem) }
}

export function createBelief(payload: Partial<BeliefItem>): Promise<{ ok: boolean; id?: number; belief_id?: number }> {
  return fetchJson<{ ok: boolean; id?: number; belief_id?: number }>('/api/beliefs', {
    method: 'POST',
    body: JSON.stringify(normalizeBeliefPayload(payload)),
  })
}

export function updateBelief(id: number, payload: Partial<BeliefItem>): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/beliefs/${id}`, {
    method: 'PUT',
    body: JSON.stringify(normalizeBeliefPayload(payload)),
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

export async function getBeliefEvidence(id: number, before = 5, after = 5): Promise<EvidencePayload> {
  const res = await fetchJson<EvidencePayload>(`/api/beliefs/${id}/evidence?before=${before}&after=${after}`)
  return { ...res, belief: res.belief ? normalizeBeliefItem(res.belief) : res.belief }
}

export async function batchArchiveBeliefsLegacy(): Promise<{ ok: boolean; archived: number }> {
  const res = await fetchJson<{ ok: boolean; archived?: number; archived_count?: number }>('/api/beliefs/batch-archive', {
    method: 'POST',
  })
  return { ok: res.ok, archived: res.archived ?? res.archived_count ?? 0 }
}

export async function batchArchiveSelectedBeliefs(ids: number[]): Promise<{ ok: boolean; archived: number }> {
  const res = await fetchJson<{ ok: boolean; archived?: number; archived_count?: number }>('/api/beliefs/batch-archive-selected', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })
  return { ok: res.ok, archived: res.archived ?? res.archived_count ?? 0 }
}

export async function batchApproveBeliefs(ids: number[]): Promise<{ ok: boolean; approved: number }> {
  const res = await fetchJson<{ ok: boolean; approved?: number; approved_count?: number }>('/api/beliefs/batch-approve', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })
  return { ok: res.ok, approved: res.approved ?? res.approved_count ?? 0 }
}

export async function batchDeleteBeliefs(ids: number[]): Promise<{ ok: boolean; deleted: number }> {
  const res = await fetchJson<{ ok: boolean; deleted?: number; deleted_count?: number }>('/api/beliefs/batch-delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })
  return { ok: res.ok, deleted: res.deleted ?? res.deleted_count ?? 0 }
}
