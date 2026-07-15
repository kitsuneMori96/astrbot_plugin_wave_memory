import { fetchJson } from './client'

export interface RuntimePayload {
  mode?: string
  label?: string
  description?: string
  [key: string]: unknown
}

export interface ChannelSettings {
  enabled?: boolean
  priority?: number
  top_k?: number
  max_items?: number
  token_budget?: number
  timeout_ms?: number
  min_score?: number
  modes?: string[]
  status?: string
  last_latency_ms?: number
  last_hit_count?: number
  [key: string]: unknown
}

export interface ChannelConfigData {
  mode?: string
  recent_dedup_minutes?: number
  trace_enabled?: boolean
  channels?: Record<string, ChannelSettings>
  [key: string]: unknown
}

export interface ChannelDiffItem {
  path?: string
  before?: unknown
  after?: unknown
  [key: string]: unknown
}

export interface ChannelConfigPayload {
  runtime?: RuntimePayload
  current?: ChannelConfigData
  defaults?: ChannelConfigData
  overrides?: Record<string, unknown>
  diff?: ChannelDiffItem[]
  editable_fields?: string[]
  limits?: Record<string, number>
}

export interface ChannelValidationPayload {
  ok?: boolean
  errors?: string[]
  current?: ChannelConfigData
  candidate?: ChannelConfigData | null
  diff?: ChannelDiffItem[]
  message?: string
}

export type ChannelPatch = Partial<ChannelConfigData>

export function getChannelConfig(): Promise<ChannelConfigPayload> {
  return fetchJson<ChannelConfigPayload>('/api/config/channels')
}

export function validateChannelConfig(patch: ChannelPatch): Promise<ChannelValidationPayload> {
  return fetchJson<ChannelValidationPayload>('/api/config/channels/validate', {
    method: 'POST',
    body: JSON.stringify(patch),
  })
}

export function applyChannelConfig(patch: ChannelPatch): Promise<ChannelValidationPayload> {
  return fetchJson<ChannelValidationPayload>('/api/config/channels', {
    method: 'POST',
    body: JSON.stringify(patch),
  })
}

export function resetChannelConfigDefaults(): Promise<ChannelValidationPayload> {
  return fetchJson<ChannelValidationPayload>('/api/config/channels/defaults', { method: 'POST' })
}

export function safeValidation(value?: ChannelValidationPayload | null): Required<Pick<ChannelValidationPayload, 'ok' | 'errors' | 'diff'>> & ChannelValidationPayload {
  return {
    ...value,
    ok: Boolean(value?.ok),
    errors: value?.errors ?? [],
    diff: value?.diff ?? [],
  }
}
