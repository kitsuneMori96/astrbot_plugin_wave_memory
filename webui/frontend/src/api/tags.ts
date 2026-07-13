import { fetchJson } from './client'

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

export interface TagQualityPayload {
  total_tags: number
  total_memories: number
  tagged_memories: number
  untagged_memories?: number
  extractable_untagged_memories?: number
  skipped_short_untagged_memories?: number
  orphan_memory_tag_refs?: number
  coverage: number
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

export function getTagQuality(): Promise<TagQualityPayload> {
  return fetchJson<TagQualityPayload>('/api/tags/quality')
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

export function resolveAuditSuggestion(suggestion_id: string | number, decision: 'approve' | 'reject'): Promise<{ ok: boolean; message?: string }> {
  return fetchJson<{ ok: boolean; message?: string }>('/api/tags/audit/resolve', {
    method: 'POST',
    body: JSON.stringify({ suggestion_id, decision }),
  })
}

export function resolveAuditBatch(suggestion_ids: Array<string | number>, decision: 'approve' | 'reject'): Promise<{ processed: number; results: any[] }> {
  return fetchJson<{ processed: number; results: any[] }>('/api/tags/audit/resolve-batch', {
    method: 'POST',
    body: JSON.stringify({ suggestion_ids, decision }),
  })
}
