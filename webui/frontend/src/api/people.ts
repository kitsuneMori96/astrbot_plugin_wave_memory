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
  affinity: number | null
  affinity_status: 'unavailable' | 'available' | string
  affinity_reason_code: string
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
