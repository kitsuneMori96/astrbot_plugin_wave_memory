import { fetchJson } from './client'
import type { ObjectRefDescriptor, PageResponse } from '@/components/shared/types'

export type TagWritePolicy = 'missing_only' | 'append' | 'replace'

export interface TagExecutionOptions {
  extract_tags?: boolean
  tag_batch_size?: number
  tag_write_policy?: TagWritePolicy
  skip_short_min_length?: number
}

export const defaultTagExecutionOptions: Required<TagExecutionOptions> = {
  extract_tags: true,
  tag_batch_size: 20,
  tag_write_policy: 'missing_only',
  skip_short_min_length: 10,
}

export const tagWritePolicyLabels: Record<TagWritePolicy, string> = {
  missing_only: '只处理无 Tag 记忆',
  append: '保留已有并追加',
  replace: '清空旧 Tag 后重写',
}

export function clampTagBatchSize(value: unknown, fallback = defaultTagExecutionOptions.tag_batch_size): number {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return fallback
  return Math.max(1, Math.min(50, Math.round(parsed)))
}

export interface TagListItem {
  id: string | number
  name: string
  type: string
  frequency: number
  confidence: number
}

export interface TagListPayload {
  items: TagListItem[]
  total: number
  available_types: string[]
  legacy: boolean
  readonly: boolean
  capabilities: {
    mutation: {
      available: boolean
      reason_code?: string
    }
  }
}

export interface TagListParams {
  limit?: number
  offset?: number
  type?: string
  search?: string
  sort?: 'frequency' | 'recent'
  signal?: AbortSignal
}

export interface TagRuntimePayload {
  capabilities: {
    extract: { available: boolean; reason_code?: string | null }
    mutation: { available: boolean; reason_code?: string | null }
  }
  index: {
    available: boolean
    health: 'ready' | 'legacy' | 'invalid' | 'unavailable'
    reason_code?: string | null
    count: number
    generation?: number | null
    db_watermark?: number | null
  }
  rag: {
    mode: 'semantic' | 'static' | 'unavailable'
    semantic_available: boolean
    fallback_reason?: string | null
    provider_configured: boolean
    reference_refresh_interval?: number | null
  }
}

export interface ScopedTagItem {
  id: number
  name: string
  type: string
  confidence: number
  metadata: Record<string, unknown>
  revision: number
  status: 'active' | 'inactive' | string
  aliases: string[]
  ref: string
  object_ref?: ObjectRefDescriptor
}

export type GovernanceAction = 'merge' | 'retype' | 'alias' | 'deactivate'
export type GovernanceSuggestionStatus = 'pending' | 'approved' | 'rejected' | 'conflict' | 'expired'
export type GovernanceCompensationStatus = 'committed' | 'conflict'
export interface ScopedTagSuggestion {
  suggestion_id: string
  operation_id: string
  action: GovernanceAction
  tag_ids: number[]
  tag_refs?: string[]
  target_tag_id?: number | null
  target_name?: string | null
  target_type?: string | null
  aliases: string[]
  reason: string
  evidence: Record<string, unknown>
  status: GovernanceSuggestionStatus
  revision: number
  created_at: number
  expires_at?: number | null
  resolved_at?: number | null
  resolved_by?: string | null
  resolution_reason?: string | null
  ref: string
  object_ref?: ObjectRefDescriptor
}
export interface GovernanceImpact { memory_count: number; relation_count: number; removed_tags: number; removed_tag_ids?: number[]; related_tag_ids?: number[]; related_tags?: string[]; index_refresh?: string; projection_status?: string }
export interface GovernancePreview { suggestion: ScopedTagSuggestion; preview: { action: GovernanceAction; tag_ids: number[]; target_tag_id?: number; target_name?: string | null; target_type?: string | null; aliases: string[]; before: Record<string, unknown>; after: Record<string, unknown>; impact: GovernanceImpact }; preflight_token: string; expires_at?: number | null }
export interface GovernanceMutationResult { ok: boolean; operation: { kind: string; status: string; id?: string }; revision: number | null; item?: { suggestion_id?: string | null; status: string; impact: GovernanceImpact } }

export interface TagQualityPayload {
  total_tags: number
  total_memories: number
  tagged_memories: number
  untagged_memories?: number
  extractable_untagged_memories?: number
  skipped_short_untagged_memories?: number
  orphan_memory_tag_refs?: number
  coverage: number
  runtime: TagRuntimePayload
}

export interface AuditSuggestionItem {
  id: string | number
  action: 'merge' | 'retype' | 'delete'
  reason: string
  status: 'pending' | 'approved' | 'rejected'
  created_at: number
  resolved_at?: number | null
  tag_ids?: Array<string | number>
  tag_names?: Record<string, { name?: string; type?: string }>
  target_name?: string | null
  target_type?: string | null
  // 兼容历史前端字段
  source_tag_id?: string
  source_tag_name?: string
  target_tag_id?: string
  target_tag_name?: string
  new_type?: string
}

export interface AuditSuggestionsPayload {
  suggestions: AuditSuggestionItem[]
  counts: {
    pending?: number
    approved?: number
    rejected?: number
  }
}

export function getTags({ limit = 25, offset = 0, type = '', search = '', sort = 'frequency', signal }: TagListParams = {}): Promise<TagListPayload> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset), sort })
  if (type) params.set('type', type)
  if (search.trim()) params.set('search', search.trim())
  return fetchJson<TagListPayload>(`/api/tags/?${params.toString()}`, { signal })
}

export function getTagQuality(signal?: AbortSignal): Promise<TagQualityPayload> {
  return fetchJson<TagQualityPayload>('/api/tags/quality', { signal })
}

export function getAuditSuggestions(
  status = 'pending',
  action = '',
  limit = 50,
  offset = 0
): Promise<AuditSuggestionsPayload> {
  const params = new URLSearchParams()
  params.append('status', status)
  params.append('limit', String(limit))
  params.append('offset', String(offset))
  if (action) {
    params.append('action', action)
  }
  return fetchJson<AuditSuggestionsPayload>(`/api/tags/audit/suggestions?${params.toString()}`)
}

export interface ScopedGovernanceScope { bot_id: string; session_id: string; visibility: 'group' }

function governanceQuery(scope: ScopedGovernanceScope, extra: Record<string, string | number | undefined> = {}): string {
  const params = new URLSearchParams({ bot_id: scope.bot_id, session_id: scope.session_id, visibility: scope.visibility })
  Object.entries(extra).forEach(([key, value]) => { if (value !== undefined && value !== '') params.set(key, String(value)) })
  return params.toString()
}

export function getScopedTags(scope: ScopedGovernanceScope, search = ''): Promise<PageResponse<ScopedTagItem> & { scope: ScopedGovernanceScope }> {
  return fetchJson(`/api/tags/governance/catalog?${governanceQuery(scope, { search, limit: 200 })}`)
}

export function getScopedTagSuggestions(scope: ScopedGovernanceScope, status: GovernanceSuggestionStatus = 'pending', action = ''): Promise<PageResponse<ScopedTagSuggestion> & { scope: ScopedGovernanceScope }> {
  return fetchJson(`/api/tags/governance/suggestions?${governanceQuery(scope, { status, action, limit: 100 })}`)
}

export function createScopedTagSuggestion(scope: ScopedGovernanceScope, payload: { action: GovernanceAction; tag_refs: string[]; target_tag_ref?: string; target_name?: string; target_type?: string; aliases?: string[]; reason: string; evidence?: Record<string, unknown> }): Promise<GovernanceMutationResult> {
  return fetchJson(`/api/tags/governance/suggestions?${governanceQuery(scope)}`, { method: 'POST', body: JSON.stringify(payload) })
}

export function previewScopedTagSuggestion(scope: ScopedGovernanceScope, suggestionRef: string, revision: number): Promise<GovernancePreview> {
  return fetchJson(`/api/tags/governance/preview?${governanceQuery(scope)}`, { method: 'POST', body: JSON.stringify({ suggestion_ref: suggestionRef, revision }) })
}

export function resolveScopedTagSuggestion(scope: ScopedGovernanceScope, payload: { suggestion_ref: string; revision: number; decision: 'approve' | 'reject'; preflight_token: string; reason: string }): Promise<GovernanceMutationResult> {
  return fetchJson(`/api/tags/governance/suggestions/resolve?${governanceQuery(scope)}`, { method: 'POST', body: JSON.stringify(payload) })
}

export function resolveScopedTagSuggestionBatch(scope: ScopedGovernanceScope, items: Array<{ suggestion_ref: string; revision: number; preflight_token: string }>, decision: 'approve' | 'reject', reason: string): Promise<GovernanceMutationResult> {
  return fetchJson(`/api/tags/governance/suggestions/resolve-batch?${governanceQuery(scope)}`, { method: 'POST', body: JSON.stringify({ items, decision, reason }) })
}

export function compensateScopedTagSuggestion(scope: ScopedGovernanceScope, payload: { suggestion_ref: string; revision: number; reason: string }): Promise<GovernanceMutationResult> {
  return fetchJson(`/api/tags/governance/suggestions/compensate?${governanceQuery(scope)}`, { method: 'POST', body: JSON.stringify(payload) })
}
