import { fetchJson } from './client'

export interface ProviderPayload {
  id: string
  name: string
  model: string
  [key: string]: unknown
}

export interface ConfigItem {
  key: string
  type: string
  description: string
  hint: string
  default: unknown
  value: unknown
  special: string
}

export interface ConfigGroup {
  key: string
  kind: 'object' | 'scalar'
  type?: string
  description: string
  hint: string
  default?: unknown
  value?: unknown
  special?: string
  items?: ConfigItem[]
}

export interface ConfigSchemaPayload {
  groups: ConfigGroup[]
}

export interface HotParam {
  key: string
  type: 'float' | 'int' | 'string'
  min: number
  max: number
  default: number
  current: number
  description: string
}

export interface HotConfigPayload {
  params: HotParam[]
  config: Record<string, unknown>
}

export interface FullConfigSavePayload {
  [key: string]: unknown
}

export interface FullConfigResponse {
  ok: boolean
  changed: string[]
  message: string
  error?: string
}

export interface HotConfigSaveResponse {
  ok: boolean
  updated: string[]
  errors: string[]
}

export function getConfigSchema(): Promise<ConfigSchemaPayload> {
  return fetchJson<ConfigSchemaPayload>('/api/config/schema')
}

export function getHotConfig(): Promise<HotConfigPayload> {
  return fetchJson<HotConfigPayload>('/api/config/hot')
}

export function saveHotConfig(payload: Record<string, unknown>): Promise<HotConfigSaveResponse> {
  return fetchJson<HotConfigSaveResponse>('/api/config/hot', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function saveFullConfig(payload: FullConfigSavePayload): Promise<FullConfigResponse> {
  return fetchJson<FullConfigResponse>('/api/config/full', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function listProviders(): Promise<{ providers: ProviderPayload[] }> {
  return fetchJson<{ providers: ProviderPayload[] }>('/api/providers')
}
