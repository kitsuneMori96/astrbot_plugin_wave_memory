import { fetchJson } from './client'
import type { EvidenceRef, ObjectRefDescriptor, PageResponse } from '@/components/shared/types'

export type BeliefType = 'self_identity' | 'person_judgment' | 'world_view' | 'preference'

export interface ScopedSelection {
  bot_id: string
  session_id: string
  visibility: 'group'
}

export interface BeliefActionAvailability {
  available: boolean
  reason_code: string | null
}

export interface BeliefItem {
  id: number
  belief_key: string
  content: string
  type: BeliefType
  status: 'pending' | 'active' | 'archived' | 'quarantined'
  confidence: number | null
  confidence_components: Record<string, number> | null
  confidence_policy_version: string | null
  anchor_sentence: string | null
  evidence_health: 'available' | 'unavailable' | 'quarantined' | 'unknown'
  quarantine_reason: string | null
  bot_id: string
  session_id: string
  visibility: 'group'
  evidence: EvidenceRef[]
  object_ref: ObjectRefDescriptor | null
  revision: number
  actions: Record<'approve' | 'archive' | 'restore' | 'delete', BeliefActionAvailability>
  updated_at?: number
}

export interface BeliefFilters extends ScopedSelection {
  limit: 25 | 50 | 100
  offset: number
  type?: BeliefType
  status?: string
  search?: string
}

export interface BeliefsResponse extends PageResponse<BeliefItem> {
  scope: { kind: string; payload: unknown }
  capabilities: Record<string, unknown>
}

export interface LegacyBeliefItem {
  id: number
  content: string
  type: BeliefType
  confidence: number | null
  bot_id: string
  source: string
  status: string
  created_at?: number
  updated_at?: number
  legacy: true
  unresolved_legacy: true
  scope: null
}

export interface LegacyBeliefsResponse {
  items: LegacyBeliefItem[]
  total: number
  pending_count: number
  legacy: true
  unresolved_legacy: true
  readonly: true
  scope: null
  page: { number: number; page_size: number; total: number; total_status: 'exact'; has_next: boolean }
}

export interface MutationResult {
  ok: boolean
  operation: { kind: string; status: string; id?: string }
  revision: number | string | null
  item?: { id: number; status: string }
}

function scopeEnvelope(scope: ScopedSelection) {
  const [platform_id, kind, conversation_id] = scope.session_id.split(':', 3)
  return {
    kind: 'RuntimeScope',
    payload: {
      bot_id: scope.bot_id,
      visibility: scope.visibility,
      session: { id: scope.session_id, platform_id, kind, conversation_id },
      subject_principal_id: null,
    },
  }
}

export function listBeliefs(filters: BeliefFilters): Promise<BeliefsResponse> {
  const params = new URLSearchParams({
    bot_id: filters.bot_id,
    session_id: filters.session_id,
    visibility: filters.visibility,
    limit: String(filters.limit),
    offset: String(filters.offset),
  })
  if (filters.type) params.set('type', filters.type)
  if (filters.status) params.set('status', filters.status)
  if (filters.search) params.set('search', filters.search)
  return fetchJson<BeliefsResponse>(`/api/beliefs?${params.toString()}`)
}

export function listLegacyBeliefs(filters: { bot_id?: string; type?: BeliefType; status?: string; search?: string; limit: 25 | 50 | 100; offset: number }): Promise<LegacyBeliefsResponse> {
  const params = new URLSearchParams({ limit: String(filters.limit), offset: String(filters.offset) })
  if (filters.bot_id) params.set('bot_id', filters.bot_id)
  if (filters.type) params.set('type', filters.type)
  if (filters.status) params.set('status', filters.status)
  if (filters.search) params.set('search', filters.search)
  return fetchJson<LegacyBeliefsResponse>(`/api/beliefs/legacy/audit?${params.toString()}`)
}

function transitionBelief(item: BeliefItem | number, action: 'approve' | 'archive', scope: ScopedSelection): Promise<MutationResult> {
  const id = typeof item === 'number' ? item : item.id
  const objectRef = typeof item === 'number' ? null : item.object_ref
  const revision = typeof item === 'number' ? null : item.revision
  // Keep the historical client call shape for source compatibility; the server
  // rejects this branch with object_ref_revision_required instead of mutating.
  if (!objectRef) {
    return fetchJson<MutationResult>(`/api/beliefs/${id}/${action}`, {
      method: 'POST',
      body: JSON.stringify({ scope: scopeEnvelope(scope) }),
    })
  }
  return fetchJson<MutationResult>(`/api/beliefs/${id}/${action}`, {
    method: 'POST',
    body: JSON.stringify({ scope: scopeEnvelope(scope), object_ref: objectRef, revision }),
  })
}

export const approveBelief = (item: BeliefItem | number, scope: ScopedSelection) => transitionBelief(item, 'approve', scope)
export const archiveBelief = (item: BeliefItem | number, scope: ScopedSelection) => transitionBelief(item, 'archive', scope)
