import { fetchJson, isRequestCancelled } from './client'
import type { EvidenceRef, ObjectRefDescriptor, PageResponse } from '@/components/shared/types'

export interface SoulScopeSelection {
  bot_id: string
  session_id: string
  visibility: 'group'
}

export interface SoulRecord {
  id: string
  summary: string
  revision: number | string | null
  policy_version: string | null
  evidence: EvidenceRef[]
  object_ref: ObjectRefDescriptor | null
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
  timeline: PageResponse<SoulRecord>
  relationship: {
    affinity: number | null
    state: 'known' | 'unknown'
    revision: number | string | null
    evidence: EvidenceRef[]
    people_ref: ObjectRefDescriptor | null
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
  return new URLSearchParams({ bot_id: scope.bot_id, session_id: scope.session_id, visibility: scope.visibility, ...extra }).toString()
}

export function getSoulState(scope: SoulScopeSelection, limit: 25 | 50 | 100, offset: number, signal?: AbortSignal): Promise<SoulStatePayload> {
  return fetchJson<SoulStatePayload>(`/api/soul/state?${scopeQuery(scope, { limit: String(limit), offset: String(offset) })}`, { signal })
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
