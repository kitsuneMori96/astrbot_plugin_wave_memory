import { fetchJson } from './client'
import type { ObjectRefDescriptor, PageResponse, PageSize } from '@/components/shared/types'

export interface PeopleQuery {
  limit?: PageSize
  offset?: number
  search?: string
  bot_id: string
  session_id: string
  visibility: 'group'
  user_id?: string
  subject_principal_id?: string
}

export interface PersonScope {
  user_id: string
  group_id: string
  bot_id: string
}

export interface PersonItem {
  id?: number
  user_id: string
  group_id: string
  bot_id: string
  nickname?: string | null
  display_name: string
  aliases: unknown[]
  interaction_count?: number
  scope: PersonScope
  scope_key: string
  metadata: Record<string, unknown>
  registry_metadata: Record<string, unknown>
  person_registry: Record<string, unknown>
  affinity: null
  affinity_status: 'unavailable'
  affinity_reason_code: string
}

export interface LegacyPersonItem extends Omit<PersonItem, 'scope' | 'scope_key' | 'registry_metadata' | 'person_registry'> {
  legacy: true
  readonly: true
  scope: null
  scope_status: 'legacy_group_key'
  scope_reason: string
}

export interface LegacyRelationshipEvent {
  id: number
  bot_id: string
  group_id: string
  user_id: string
  event_type: string
  dimension: string
  delta: number
  reason: string
  source_episode_id?: number | null
  source_memory_id?: number | null
  created_at: number
  legacy: true
  readonly: true
  scope: null
  scope_status: 'legacy_group_key'
  scope_reason: string
}

export interface RelationshipValue {
  dimension: string
  automatic_value: number
  manual_adjustment: number | null
  manual_override: number | null
  effective_value: number
  relationship_revision: number
  evidence: unknown[]
}

export interface RelationshipItem {
  subject_principal_id: string
  person: PersonItem
  affinity: number | null
  state: 'known' | 'unknown' | string
  revision: number | null
  values: Record<string, RelationshipValue> | null
  evidence: unknown[]
  object_ref: (ObjectRefDescriptor & { kind: 'relationship' }) | null
  calibration: { available: boolean; reason_code: string | null }
}

export interface RelationshipQuery extends PeopleQuery {
  user_id?: string
}

export interface RelationshipCalibrationPayload {
  object_ref: string | { ref: string }
  revision: number
  action: 'adjust' | 'override' | 'clear_override' | 'restore_auto'
  dimension: string
  delta?: number
  value?: number
  reason: string
  evidence: unknown[]
}

export interface RelationshipCalibrationResponse {
  ok: boolean
  operation: { kind: string; status: string; id?: string }
  revision: number
  item?: Record<string, unknown>
}

export function getRelationships(query: RelationshipQuery, signal?: AbortSignal): Promise<PageResponse<RelationshipItem>> {
  return fetchJson<PageResponse<RelationshipItem>>(`/api/people/relationships${queryString(query)}`, { signal })
}

export function calibrateRelationship(query: PeopleQuery, payload: RelationshipCalibrationPayload): Promise<RelationshipCalibrationResponse> {
  return fetchJson<RelationshipCalibrationResponse>(`/api/people/relationships/commands/calibrate${queryString(query)}`, { method: 'POST', body: JSON.stringify(payload) })
}

export interface LegacyAuditPage<T> extends PageResponse<T> {
  legacy: true
  readonly: true
  scope: null
  scope_status: 'legacy_group_key'
  reason_code: string
}

function queryString(query: object): string {
  const params = new URLSearchParams()
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  })
  const serialized = params.toString()
  return serialized ? `?${serialized}` : ''
}

export function getPeople(query: PeopleQuery): Promise<PageResponse<PersonItem>> {
  return fetchJson<PageResponse<PersonItem>>(`/api/people${queryString(query)}`)
}

export function getLegacyPeople(query: { bot_id?: string; group_id?: string; search?: string; limit?: PageSize; offset?: number }): Promise<LegacyAuditPage<LegacyPersonItem>> {
  return fetchJson<LegacyAuditPage<LegacyPersonItem>>(`/api/people/legacy/audit${queryString(query)}`)
}

export function getLegacyRelationships(query: { bot_id?: string; group_id?: string; session_id?: string; user_id?: string; search?: string; limit?: PageSize; offset?: number }): Promise<LegacyAuditPage<LegacyRelationshipEvent>> {
  return fetchJson<LegacyAuditPage<LegacyRelationshipEvent>>(`/api/people/legacy/relationships${queryString(query)}`)
}
