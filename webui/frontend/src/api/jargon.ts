import { fetchJson } from './client'
import type { EvidenceRef, ObjectRefDescriptor, PageResponse } from '@/components/shared/types'

export interface JargonScopeSelection {
  bot_id: string
  session_id: string
  visibility: 'group'
}

export interface JargonItem {
  id: number
  word: string
  meaning: string
  frequency: number
  confidence: number | null
  status: 'pending' | 'confirmed' | 'rejected'
  review_status: string
  bot_id: string
  session_id: string
  visibility: 'group'
  source: string
  rule_version: string | null
  promotion: unknown
  anchors: EvidenceRef[]
  object_ref: ObjectRefDescriptor | null
  revision: number
}

export interface JargonFilters extends JargonScopeSelection {
  limit: 25 | 50 | 100
  offset: number
  status?: string
  search?: string
}

export interface JargonCapability {
  available: boolean
  reason_code: string | null
}

export interface JargonResponse extends PageResponse<JargonItem> {
  scope: { kind: string; payload: unknown }
  capabilities: {
    review: JargonCapability
    batch_review: JargonCapability
    edit: JargonCapability
    archive: JargonCapability
    evidence: JargonCapability
    create: JargonCapability
    delete: JargonCapability
    toggle_global: JargonCapability
    select_all_matching: JargonCapability
  }
}

export interface JargonEvidenceMessage {
  id: number
  group_id: string | null
  sender_id: string | null
  sender_name: string | null
  content: string
  timestamp: number
  role: 'before' | 'anchor' | 'after'
}

export interface JargonEvidencePayload {
  ok: boolean
  jargon: Pick<JargonItem, 'id' | 'word' | 'meaning' | 'revision'>
  scope: { kind: string; payload: unknown }
  anchor: JargonEvidenceMessage | null
  messages: JargonEvidenceMessage[]
  fallback_contexts: string[]
  used_fallback: boolean
}

export interface LegacyJargonItem {
  id: number
  word: string
  meaning: string
  frequency: number
  confidence: number | null
  group_id?: string | null
  status: string
  source?: string
  candidate_type?: string
  created_at?: number
}

export interface LegacyJargonResponse {
  items: LegacyJargonItem[]
  total: number
  pending_count: number
  legacy: true
  readonly: true
  page: { number: number; page_size: number; total: number; total_status: 'exact'; has_next: boolean }
}

export type CatalogAssetRecord = Record<string, unknown>

export interface CatalogAuditPayload {
  asset_type: string
  runtime_policy: string
  local_version: string
  remote_version: string
  asset_status: string
  checked_at?: string
  manifest?: Record<string, unknown>
  manifest_summary?: Record<string, unknown>
  quality_report?: Record<string, unknown>
  quality_summary?: Record<string, unknown>
  layers?: Record<string, unknown>
  phrases?: CatalogAssetRecord[]
  concepts?: CatalogAssetRecord[]
  examples?: CatalogAssetRecord[]
  corpus?: CatalogAssetRecord[]
  candidates?: CatalogAssetRecord[]
  categories?: CatalogAssetRecord[]
  blocked?: Record<string, unknown>
  corpus_summary?: Record<string, unknown>
  corpus_counts?: Record<string, unknown>
  local_count?: number
  content_count?: number
  content_hash?: string
  update_available?: boolean
}

function scopeEnvelope(scope: JargonScopeSelection) {
  const [platform_id, kind, conversation_id] = scope.session_id.split(':', 3)
  return { kind: 'RuntimeScope', payload: { bot_id: scope.bot_id, visibility: scope.visibility, session: { id: scope.session_id, platform_id, kind, conversation_id }, subject_principal_id: null } }
}

export function listJargons(filters: JargonFilters): Promise<JargonResponse> {
  const params = new URLSearchParams({ bot_id: filters.bot_id, session_id: filters.session_id, visibility: filters.visibility, limit: String(filters.limit), offset: String(filters.offset) })
  if (filters.status) params.set('status', filters.status)
  if (filters.search) params.set('search', filters.search)
  return fetchJson<JargonResponse>(`/api/jargon?${params.toString()}`)
}

export function getJargonEvidence(item: JargonItem, scope: JargonScopeSelection, before = 15, after = 15): Promise<JargonEvidencePayload> {
  if (!item.object_ref?.ref) throw new Error('该黑话没有服务端签发的 ObjectRef，不能安全读取证据')
  const params = new URLSearchParams({
    bot_id: scope.bot_id,
    session_id: scope.session_id,
    visibility: scope.visibility,
    ref: item.object_ref.ref,
    before: String(Math.max(0, Math.min(50, Math.trunc(before)))),
    after: String(Math.max(0, Math.min(50, Math.trunc(after)))),
  })
  return fetchJson<JargonEvidencePayload>(`/api/jargon/${item.id}/evidence?${params.toString()}`)
}

export function listLegacyJargons(filters: { group_id?: string; status?: string; search?: string; limit: 25 | 50 | 100; offset: number }): Promise<LegacyJargonResponse> {
  const params = new URLSearchParams({ limit: String(filters.limit), offset: String(filters.offset) })
  if (filters.group_id) params.set('group_id', filters.group_id)
  if (filters.status) params.set('status', filters.status)
  if (filters.search) params.set('search', filters.search)
  return fetchJson<LegacyJargonResponse>(`/api/jargon/legacy/audit?${params.toString()}`)
}

export function reviewJargon(item: JargonItem | number, action: 'approve' | 'reject', scope: JargonScopeSelection) {
  const id = typeof item === 'number' ? item : item.id
  const objectRef = typeof item === 'number' ? null : item.object_ref
  const revision = typeof item === 'number' ? null : item.revision
  // The numeric compatibility branch is still rejected by the server; only a
  // list-issued ObjectRef can reach the formal mutation service.
  const body = objectRef
    ? JSON.stringify({ scope: scopeEnvelope(scope), object_ref: objectRef, revision })
    : JSON.stringify({ scope: scopeEnvelope(scope) })
  return fetchJson<{ ok: boolean; operation: { status: string } }>(`/api/jargon/${id}/review/${action}`, {
    method: 'POST',
    body,
  })
}

export function updateJargonMeaning(item: JargonItem, meaning: string, scope: JargonScopeSelection) {
  if (!item.object_ref) throw new Error('该黑话没有服务端签发的 ObjectRef，不能安全编辑')
  return fetchJson<{ ok: boolean; operation: { status: string }; item: JargonItem }>(`/api/jargon/commands/${item.id}/meaning`, {
    method: 'POST',
    body: JSON.stringify({ scope: scopeEnvelope(scope), object_ref: item.object_ref, revision: item.revision, meaning }),
  })
}

export function archiveJargon(item: JargonItem, scope: JargonScopeSelection) {
  if (!item.object_ref) throw new Error('该黑话没有服务端签发的 ObjectRef，不能安全归档')
  return fetchJson<{ ok: boolean; operation: { status: string }; item: { id: number; status: string } }>(`/api/jargon/commands/${item.id}/archive`, {
    method: 'POST',
    body: JSON.stringify({ scope: scopeEnvelope(scope), object_ref: item.object_ref, revision: item.revision }),
  })
}

export function batchReviewJargons(items: JargonItem[], action: 'approve' | 'reject', scope: JargonScopeSelection) {
  if (!items.length || items.some((item) => !item.object_ref)) throw new Error('批量审核要求每条黑话都有服务端签发的 ObjectRef')
  return fetchJson<{ ok: boolean; operation: { status: string }; reviewed_count: number; items: Array<{ id: number; status: string }> }>('/api/jargon/commands/batch-review', {
    method: 'POST',
    body: JSON.stringify({
      scope: scopeEnvelope(scope),
      action,
      items: items.map((item) => ({ id: item.id, object_ref: item.object_ref, revision: item.revision })),
    }),
  })
}

export interface HolymanUpdateCheckPayload {
  local_version: string
  remote_version: string
  has_update: boolean
  asset_status: string
  checked_at: string
  cached: boolean
  warning?: string
}

export function checkHolymanUpdate(force = false): Promise<HolymanUpdateCheckPayload> {
  return fetchJson<HolymanUpdateCheckPayload>(`/api/jargon/holyman/update/check${force ? '?force=true' : ''}`)
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
  samples?: { added_phrases?: string[]; changed_phrases?: string[]; removed_phrases?: string[] }
  safety?: { statement?: string; corpus_reference_only?: boolean; corpus_safe_for_prompt?: boolean }
  quality_report?: unknown
  error?: string
}

export function previewHolymanSync(useProxy = true): Promise<HolymanSyncPreviewPayload> {
  return fetchJson<HolymanSyncPreviewPayload>('/api/jargon/holyman/sync/preview', {
    method: 'POST',
    body: JSON.stringify({ use_proxy: useProxy }),
  })
}

export function getCatalogAudit(): Promise<CatalogAuditPayload> {
  return fetchJson<CatalogAuditPayload>('/api/jargon/holyman')
}
