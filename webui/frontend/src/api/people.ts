import { fetchJson } from './client'
import type { PageResponse, PageSize } from '@/components/shared/types'

export interface PeopleQuery {
  limit?: PageSize
  offset?: number
  search?: string
  bot_id: string
  session_id: string
  visibility: 'group'
  user_id?: string
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
