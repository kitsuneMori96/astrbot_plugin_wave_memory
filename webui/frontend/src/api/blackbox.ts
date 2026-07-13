import { fetchJson } from './client'

export interface BlackboxListQuery {
  limit?: number
  offset?: number
  search?: string
  sort?: string
  filter?: string
}

export interface BlackboxListPayload<T> {
  items?: T[]
  total?: number
  limit?: number
  offset?: number
  search?: string
  sort?: string
  filter?: string
  readonly?: boolean
  route_prefix?: string
  [key: string]: unknown
}

export interface BlackboxBookLoreSummary {
  readonly?: boolean
  route_prefix?: string
  counts?: {
    entities?: number
    relations?: number
    communities?: number
    notes?: number
    [key: string]: unknown
  }
  index_health?: Record<string, unknown>
  safety?: string
  [key: string]: unknown
}

export interface BlackboxBookLoreEntity {
  id?: number | string
  name?: string
  title?: string
  summary?: string
  description?: string
  source_book?: string
  [key: string]: unknown
}

export interface BlackboxBookLoreCommunity {
  id?: number | string
  title?: string
  summary?: string
  [key: string]: unknown
}

export interface BlackboxBookLoreRelation {
  id?: number | string
  source?: string
  target?: string
  relation?: string
  [key: string]: unknown
}

export interface BlackboxBookLoreNote {
  id?: number | string
  title?: string
  content?: string
  [key: string]: unknown
}

export interface BlackboxFewShotSummary {
  readonly?: boolean
  route_prefix?: string
  counts?: {
    pending?: number
    approved?: number
    rejected?: number
    total?: number
    [key: string]: unknown
  }
  average_score?: number
  drift_detection?: string
  safety?: string
  [key: string]: unknown
}

export interface BlackboxFewShotExample {
  id?: number | string
  content?: string
  score?: number
  traits?: string
  status?: string
  bot_id?: string
  created_at?: number | string
  approved_at?: number | string
  [key: string]: unknown
}

export interface BlackboxFactItem {
  id?: number | string
  subject?: string
  predicate?: string
  object?: string
  fact_type?: string
  confidence?: number
  source_memory_id?: number | string
  [key: string]: unknown
}

export interface BlackboxPersonItem {
  qq_id?: string
  user_id?: string
  display_name?: string
  nickname?: string
  aliases?: string
  group_id?: string
  bot_id?: string
  affection?: number
  interaction_count?: number
  message_count?: number
  last_seen?: number | string
  metadata?: unknown
  [key: string]: unknown
}

export interface BlackboxIndexesSummary {
  readonly?: boolean
  route_prefix?: string
  dangerous_operations_require_preview?: boolean
  counts?: {
    memories?: number
    memory_tags?: number
    tags?: number
    memories_missing_vector?: number
    tags_missing_vector?: number
    facts?: number
    book_entities?: number
    [key: string]: unknown
  }
  health?: {
    memory_vector_index?: string
    fts5_index?: string
    epa_basis?: string
    book_lore_hnsw_index?: string
    [key: string]: unknown
  }
  ok?: boolean
  message?: string
  [key: string]: unknown
}

function toQueryString(query: BlackboxListQuery = {}): string {
  const params = new URLSearchParams()
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value))
    }
  })
  const text = params.toString()
  return text ? `?${text}` : ''
}

export function getBlackboxBookLoreSummary(): Promise<BlackboxBookLoreSummary> {
  return fetchJson<BlackboxBookLoreSummary>('/api/blackbox/book-lore/summary')
}

export function getBlackboxBookLoreEntities(query: BlackboxListQuery = {}): Promise<BlackboxListPayload<BlackboxBookLoreEntity>> {
  return fetchJson<BlackboxListPayload<BlackboxBookLoreEntity>>(`/api/blackbox/book-lore/entities${toQueryString(query)}`)
}

export function getBlackboxBookLoreCommunities(query: BlackboxListQuery = {}): Promise<BlackboxListPayload<BlackboxBookLoreCommunity>> {
  return fetchJson<BlackboxListPayload<BlackboxBookLoreCommunity>>(`/api/blackbox/book-lore/communities${toQueryString(query)}`)
}

export function getBlackboxBookLoreRelations(query: BlackboxListQuery = {}): Promise<BlackboxListPayload<BlackboxBookLoreRelation>> {
  return fetchJson<BlackboxListPayload<BlackboxBookLoreRelation>>(`/api/blackbox/book-lore/relations${toQueryString(query)}`)
}

export function getBlackboxBookLoreNotes(query: BlackboxListQuery = {}): Promise<BlackboxListPayload<BlackboxBookLoreNote>> {
  return fetchJson<BlackboxListPayload<BlackboxBookLoreNote>>(`/api/blackbox/book-lore/notes${toQueryString(query)}`)
}

export function getBlackboxFewShotSummary(): Promise<BlackboxFewShotSummary> {
  return fetchJson<BlackboxFewShotSummary>('/api/blackbox/fewshot/summary')
}

export function getBlackboxFewShotExamples(query: BlackboxListQuery = {}): Promise<BlackboxListPayload<BlackboxFewShotExample>> {
  return fetchJson<BlackboxListPayload<BlackboxFewShotExample>>(`/api/blackbox/fewshot/examples${toQueryString(query)}`)
}

export function getBlackboxFacts(query: BlackboxListQuery = {}): Promise<BlackboxListPayload<BlackboxFactItem>> {
  return fetchJson<BlackboxListPayload<BlackboxFactItem>>(`/api/blackbox/facts${toQueryString(query)}`)
}

export function getBlackboxPeople(query: BlackboxListQuery = {}): Promise<BlackboxListPayload<BlackboxPersonItem>> {
  return fetchJson<BlackboxListPayload<BlackboxPersonItem>>(`/api/blackbox/people${toQueryString(query)}`)
}

export function getBlackboxIndexesSummary(): Promise<BlackboxIndexesSummary> {
  return fetchJson<BlackboxIndexesSummary>('/api/blackbox/indexes/summary')
}

export function getBlackboxIndexesCheck(): Promise<BlackboxIndexesSummary> {
  return fetchJson<BlackboxIndexesSummary>('/api/blackbox/indexes/check')
}

export function updateBlackboxFewShot(id: number | string, payload: { status?: string; content?: string }): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/blackbox/fewshot/examples/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function deleteBlackboxFewShot(id: number | string): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/blackbox/fewshot/examples/${id}`, {
    method: 'DELETE',
  })
}

export function updateBlackboxFact(id: number | string, payload: { confidence?: number; predicate?: string; object?: string }): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/blackbox/facts/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function deleteBlackboxFact(id: number | string): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/blackbox/facts/${id}`, {
    method: 'DELETE',
  })
}

export function deleteBookLoreItem(tableType: 'entities' | 'communities' | 'relations' | 'notes', id: number | string): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/blackbox/book-lore/${tableType}/${id}`, {
    method: 'DELETE',
  })
}

export function rebuildIndexes(): Promise<{ ok: boolean; message: string }> {
  return fetchJson<{ ok: boolean; message: string }>('/api/blackbox/indexes/rebuild', {
    method: 'POST',
  })
}

