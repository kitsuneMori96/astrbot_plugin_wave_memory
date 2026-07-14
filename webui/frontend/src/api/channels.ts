import { fetchJson } from './client'

export interface RuntimePayload { mode?: string; label?: string; description?: string; [key: string]: unknown }
export interface FieldValueDto { default: unknown; saved: unknown; effective: unknown; apply_mode: 'hot' | 'restart' | 'next_run' | 'unknown'; effective_since?: number | string | null }
export interface ChannelSettings {
  enabled?: boolean
  priority?: number
  top_k?: number | null
  max_items?: number | null
  token_budget?: number
  timeout_ms?: number
  min_score?: number | null
  modes?: string[]
  status?: string
  last_latency_ms?: number
  last_hit_count?: number
  field_states?: Record<string, FieldValueDto>
  [key: string]: unknown
}
export interface ChannelConfigData { mode?: string; recent_dedup_minutes?: number; trace_enabled?: boolean; channels?: Record<string, ChannelSettings>; [key: string]: unknown }
export interface ChannelDescriptor {
  id: string
  purpose: string
  dependencies: string[]
  risk: 'low' | 'medium' | 'high' | 'critical' | string
  management_route: string | null
  verification_filters: Record<string, string>
  available: boolean
}
export interface ChannelDiffItem { path?: string; before?: unknown; after?: unknown; [key: string]: unknown }
export interface ChannelConfigPayload {
  runtime?: RuntimePayload
  current?: ChannelConfigData
  defaults?: ChannelConfigData
  overrides?: Record<string, unknown>
  diff?: ChannelDiffItem[]
  descriptors?: ChannelDescriptor[]
  revision?: string
  effective_since?: number | null
  verification_url?: string
  editable_fields?: string[]
  limits?: Record<string, number>
}
export interface ChannelValidationPayload {
  ok?: boolean
  errors?: string[]
  current?: ChannelConfigData
  candidate?: ChannelConfigData | null
  effective?: ChannelConfigData | null
  diff?: ChannelDiffItem[]
  message?: string
  operation?: { id?: string; kind?: string; status?: string }
  revision?: string | number | null
  effective_since?: number | null
  verification_url?: string
  preflight_token?: string
  error_code?: string
}
export type ChannelPatch = Partial<ChannelConfigData>

export function getChannelConfig(): Promise<ChannelConfigPayload> { return fetchJson('/api/config/channels') }
export function validateChannelConfig(patch: ChannelPatch): Promise<ChannelValidationPayload> { return fetchJson('/api/config/channels/validate', { method: 'POST', body: JSON.stringify(patch) }) }
export function applyChannelConfig(patch: ChannelPatch, preflightToken: string): Promise<ChannelValidationPayload> { return fetchJson('/api/config/channels', { method: 'POST', body: JSON.stringify({ ...patch, preflight_token: preflightToken, confirmation: 'apply' }) }) }
export function resetChannelConfigDefaults(): Promise<ChannelValidationPayload> { return fetchJson('/api/config/channels/defaults', { method: 'POST' }) }
export function safeValidation(value?: ChannelValidationPayload | null): Required<Pick<ChannelValidationPayload, 'ok' | 'errors' | 'diff'>> & ChannelValidationPayload {
  return { ...value, ok: value?.ok === true, errors: value?.errors ?? [], diff: value?.diff ?? [] }
}
