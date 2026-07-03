import { fetchJson } from './client'

export const livingMemoryAliasNames = ['recall_long_term_memory', 'memorize_long_term_memory'] as const

export interface CompatibilityPayload {
  runtime?: Record<string, unknown>
  facade?: {
    enabled?: boolean
    status?: string
    interface?: string[]
  }
  tool_aliases?: Record<string, { enabled?: boolean; target?: string }>
  detected_plugins?: Array<Record<string, unknown>>
  duplicate_warnings?: Array<{ plugin_id?: string; name?: string; message?: string }>
  recommended_settings?: string[]
}

export function getCompatibilityStatus(): Promise<CompatibilityPayload> {
  return fetchJson<CompatibilityPayload>('/api/compat/status')
}
