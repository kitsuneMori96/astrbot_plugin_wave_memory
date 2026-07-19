import { fetchJson } from './client'
import type { EvidenceRef, PageResponse, PageSize } from '@/components/shared/types'

export interface KnowledgeListQuery {
  limit?: PageSize
  offset?: number
  search?: string
  sort?: string
  filter?: string
  bot_id?: string
  session_id?: string
  visibility?: 'group'
  status?: string
  health?: string
  catalog_id?: string
  corpus_id?: string
  version?: string
}

export interface CatalogScopeDto {
  catalog_id: string
  corpus_id: string
  version: string
}

export type BookLoreResource = 'entities' | 'communities' | 'relations' | 'notes'

export interface BookLoreItem {
  id: number | string
  original: unknown | null
  localized: unknown | null
  translation: unknown | null
  resolution: unknown | null
  quarantine: boolean | number | null
  [key: string]: unknown
}

export interface BookLorePage extends PageResponse<BookLoreItem> {
  scope: CatalogScopeDto
  resource: BookLoreResource
  read_only: true
}

export interface ReviewedBookLoreProjection {
  id: number
  community_id: string
  title: string
  summary: string
  content: string
  rank: number
  status: 'approved'
  revision: number
  source_scope: CatalogScopeDto
  target_scope: {
    bot_id: string
    visibility: 'group'
    session: { id: string; platform_id: string; kind: 'group'; conversation_id: string }
  }
  evidence: EvidenceRef[]
  created_at?: number | null
  updated_at?: number | null
  approved_at?: number | null
}

export interface ReviewedBookLorePage extends PageResponse<ReviewedBookLoreProjection> {
  scope: ReviewedBookLoreProjection['target_scope']
  source: 'reviewed_book_lore_projections'
}

export interface BookLoreSummary {
  scope: CatalogScopeDto
  counts: Record<BookLoreResource, number>
  schema: {
    fingerprint: string
    tables: Record<string, Array<Record<string, unknown>>>
    missing_tables: string[]
  }
  read_only: true
}

export interface ScopedFact {
  id: number
  bot_id: string
  session_id: string
  visibility: 'group'
  subject: string
  predicate: string
  object: string
  confidence: number
  status: string
  source_memory_id?: number | null
  provenance?: Record<string, unknown>
  evidence: EvidenceRef[]
  evidence_status: 'available' | 'unavailable'
  [key: string]: unknown
}

export interface ScopedFactsPage extends PageResponse<ScopedFact> {
  scope: {
    bot_id: string
    session_id: string
    visibility: 'group'
  }
}


export interface ApprovedFewShot {
  id: number
  content: string
  score?: number
  traits?: string[]
  status: 'approved'
  bot_id?: string
  health: 'healthy'
  created_at?: number | null
  approved_at?: number | null
}

function queryString(query: KnowledgeListQuery): string {
  const params = new URLSearchParams()
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  })
  const serialized = params.toString()
  return serialized ? `?${serialized}` : ''
}

export function getBookLoreSummary(query: Pick<KnowledgeListQuery, 'catalog_id' | 'corpus_id' | 'version'> = {}): Promise<BookLoreSummary> {
  return fetchJson<BookLoreSummary>(`/api/knowledge/book-lore/summary${queryString(query)}`)
}

export function getBookLoreItems(resource: BookLoreResource, query: KnowledgeListQuery = {}): Promise<BookLorePage> {
  return fetchJson<BookLorePage>(`/api/knowledge/book-lore/${resource}${queryString(query)}`)
}

export function getReviewedBookLore(query: KnowledgeListQuery): Promise<ReviewedBookLorePage> {
  return fetchJson<ReviewedBookLorePage>(`/api/knowledge/book-lore/projections${queryString(query)}`)
}

export function getScopedFacts(query: KnowledgeListQuery): Promise<ScopedFactsPage> {
  return fetchJson<ScopedFactsPage>(`/api/knowledge/facts${queryString(query)}`)
}

export type ApprovedFewShotQuery = KnowledgeListQuery & { bot_id: string; session_id: string; visibility: 'group' }

export function getApprovedFewShot(query: ApprovedFewShotQuery): Promise<PageResponse<ApprovedFewShot>> {
  return fetchJson<PageResponse<ApprovedFewShot>>(`/api/knowledge/few-shot${queryString({ ...query, status: 'approved', health: 'healthy' })}`)
}

export function getApprovedFewShotDetail(id: number, query: ApprovedFewShotQuery): Promise<ApprovedFewShot> {
  return fetchJson<ApprovedFewShot>(`/api/knowledge/few-shot/${id}${queryString({ ...query, status: 'approved', health: 'healthy' })}`)
}
