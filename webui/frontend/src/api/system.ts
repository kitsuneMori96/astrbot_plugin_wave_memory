import { fetchJson } from './client'

export interface ServiceHealth {
  name: string
  status: string
  reason?: string
}

export interface SystemPayload {
  memories?: { total?: number; with_vector?: number; with_tags?: number }
  tags?: { total?: number; structured?: number; type_distribution?: Record<string, number> }
  coverage?: { vector_pct?: number; tag_pct?: number }
  cooccurrence?: { nodes?: number; edges?: number }
  db_size_mb?: number
  epa?: { initialized?: boolean; reason?: string }
  services_health?: ServiceHealth[]
  lifecycle?: {
    facts?: number
    persons?: number
    user_profiles?: number
    active_users?: number
    top_users?: Array<{ name?: string; interactions?: number }>
    active_moods?: Array<{ group_id?: string; type?: string; intensity?: number; desc?: string }>
    unspoken_desire?: { topic?: string; motive?: string }
  }
}

export interface ErrorPayload {
  errors?: Array<Record<string, unknown>>
  total?: number
}

export interface InjectionMetricSeriesPoint {
  bucket?: number
  ts?: number
  time?: string
  [key: string]: unknown
}

export interface InjectionMetricRankingItem {
  key?: string
  name?: string
  total_tokens?: number
  avg_tokens?: number
  count?: number
  share_pct?: number
  [key: string]: unknown
}

export interface InjectionMetricsPayload {
  range?: string
  from?: number
  to?: number
  bucket_seconds?: number
  count?: number
  summary?: Record<string, unknown>
  series?: InjectionMetricSeriesPoint[]
  ranking?: InjectionMetricRankingItem[]
  error?: string
}

export function getSystemStatus(): Promise<SystemPayload> {
  return fetchJson<SystemPayload>('/api/system')
}

export function getRecentErrors(): Promise<ErrorPayload> {
  return fetchJson<ErrorPayload>('/api/errors')
}

export function getInjectionMetrics(range = '7d'): Promise<InjectionMetricsPayload> {
  return fetchJson<InjectionMetricsPayload>(`/api/metrics/injection?range=${encodeURIComponent(range)}`)
}
