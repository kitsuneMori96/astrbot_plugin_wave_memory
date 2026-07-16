import { fetchJson, isRequestCancelled } from './client'
import type { EvidenceRef, ObjectRefDescriptor, PageResponse } from '@/components/shared/types'

export interface SoulScopeSelection {
  bot_id: string
  session_id: string
  visibility: 'group'
  subject_principal_id?: string
}

export interface SoulRecord {
  id: string | number
  summary?: string
  topic?: string
  event_summary?: string
  event_type?: string
  emotional_weight?: number | null
  timestamp?: number | null
  last_triggered?: number | null
  revision?: number | string | null
  policy_version?: string | null
  evidence: EvidenceRef[]
  object_ref?: ObjectRefDescriptor | null
}

export interface RelationshipHistorySnapshot {
  dimension: string
  automatic_value: number | null
  manual_adjustment: number | null
  manual_override: number | null
  effective_value: number | null
  relationship_revision?: number | null
  updated_at?: number | null
}

export interface RelationshipHistoryItem {
  id: string
  event_id: string | number
  kind: 'automatic' | 'manual' | string
  event_type: string | null
  action: string | null
  dimension: string
  delta: number | null
  reason: string | null
  source_episode_id: number | string | null
  source_memory_id: number | string | null
  revision: number | null
  timestamp: number | null
  operation_id: string | null
  actor: string | null
  value_layer: string | null
  before: RelationshipHistorySnapshot | null
  after: RelationshipHistorySnapshot | null
  evidence: EvidenceRef[]
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

export function calibrateSoulRelationship(scope: SoulScopeSelection, payload: RelationshipCalibrationPayload): Promise<{ ok: boolean; operation: { kind: string; status: string; id?: string }; revision: number; item?: Record<string, unknown> }> {
  return fetchJson(`/api/people/relationships/commands/calibrate?${scopeQuery(scope)}`, { method: 'POST', body: JSON.stringify(payload) })
}

export interface SoulStatePayload {
  scope: SoulScopeSelection & { kind: 'SoulScope'; platform_id: string; conversation_id: string }
  source: { health: 'healthy' | 'empty' | 'unavailable' | 'error'; reason_code: string | null }
  mood: {
    value: string | null
    state: 'known' | 'unknown'
    components: Record<string, number> | null
    policy_version: string | null
    revision: number | string | null
    evidence: EvidenceRef[]
  }
  concerns: PageResponse<SoulRecord>
  timeline: PageResponse<SoulRecord> & { revision?: number | string | null }
  relationship_history: PageResponse<RelationshipHistoryItem> & { revision?: number | string | null }
  soul_context: {
    status: 'available' | 'unknown' | 'unavailable' | string
    reason_code: string | null
    timezone: string | null
    circadian: Record<string, unknown> | string | null
    energy: number | string | null
    sleepiness: number | string | null
  }
  relationship: {
    affinity: number | null
    state: 'known' | 'unknown' | string
    revision: number | string | null
    evidence: EvidenceRef[]
    people_ref: (ObjectRefDescriptor & { kind: 'relationship' }) | null
    dimensions?: Record<string, number> | null
    values?: Record<string, { dimension: string; automatic_value: number; manual_adjustment: number | null; manual_override: number | null; effective_value: number; relationship_revision: number; evidence: unknown[] }> | null
    calibration?: { available: boolean; reason_code: string | null }
  }
  capabilities: {
    mutate: { available: boolean; reason_code: string | null }
    runtime_refresh: { available: boolean; reason_code: string | null }
  }
  runtime_refresh: { status: string; operation: unknown; reason_code: string | null }
}

export interface LegacyConcernItem {
  id: number
  topic: string
  intensity: number
  bot_id?: string
  created_at?: number
  last_triggered?: number
}

export interface LegacyTimelineItem {
  id: number
  event_summary: string
  emotional_weight: number
  timestamp: number
  bot_id?: string
}

export interface LegacyMoodItem {
  id: number
  type: string
  intensity: number
  description: string
  timestamp: number
  is_active: boolean
  valence?: number
  arousal?: number
  bot_id?: string
}

export interface LegacySoulCollection<T> {
  status: 'available' | 'unavailable'
  items: T[]
  reason?: string
}

export interface LegacySoulSnapshot {
  readonly: true
  scope_status: 'legacy-not-session-scoped'
  concerns: LegacySoulCollection<LegacyConcernItem>
  timeline: LegacySoulCollection<LegacyTimelineItem>
  moods: LegacySoulCollection<LegacyMoodItem>
}

function scopeQuery(scope: SoulScopeSelection, extra: Record<string, string> = {}): string {
  return new URLSearchParams({ bot_id: scope.bot_id, session_id: scope.session_id, visibility: scope.visibility, ...(scope.subject_principal_id ? { subject_principal_id: scope.subject_principal_id } : {}), ...extra }).toString()
}

export function getSoulState(
  scope: SoulScopeSelection,
  limit: 25 | 50 | 100,
  offset: number,
  signal?: AbortSignal,
  timeRange: { from_ts?: number; to_ts?: number } = {},
): Promise<SoulStatePayload> {
  const extra = {
    limit: String(limit),
    offset: String(offset),
    ...(timeRange.from_ts !== undefined ? { from_ts: String(timeRange.from_ts) } : {}),
    ...(timeRange.to_ts !== undefined ? { to_ts: String(timeRange.to_ts) } : {}),
  }
  return fetchJson<SoulStatePayload>(`/api/soul/state?${scopeQuery(scope, extra)}`, { signal })
}

async function legacyCollection<T>(request: Promise<{ items: T[] }>): Promise<LegacySoulCollection<T>> {
  try {
    const payload = await request
    return { status: 'available', items: payload.items ?? [] }
  } catch (reason) {
    if (isRequestCancelled(reason)) throw reason
    return { status: 'unavailable', items: [], reason: reason instanceof Error ? reason.message : 'legacy_read_unavailable' }
  }
}

export async function getLegacySoulSnapshot(scope: SoulScopeSelection, signal?: AbortSignal): Promise<LegacySoulSnapshot> {
  const concerns = legacyCollection(fetchJson<{ items: LegacyConcernItem[] }>(`/api/concerns?${scopeQuery(scope, { limit: '50' })}`, { signal }))
  const timeline = legacyCollection(fetchJson<{ items: LegacyTimelineItem[] }>(`/api/time-anchors?${scopeQuery(scope, { limit: '50' })}`, { signal }))
  const moods = legacyCollection(
    fetchJson<{ items: Array<LegacyMoodItem & { desc?: string; ts?: number }> }>(`/api/mood/trajectory?${scopeQuery(scope, { limit: '100' })}`, { signal }).then((payload) => ({
      items: payload.items.map((item) => ({ ...item, description: item.description ?? item.desc ?? '', timestamp: item.timestamp ?? item.ts ?? 0 })),
    })),
  )
  const [concernResult, timelineResult, moodResult] = await Promise.all([concerns, timeline, moods])
  return { readonly: true, scope_status: 'legacy-not-session-scoped', concerns: concernResult, timeline: timelineResult, moods: moodResult }
}
