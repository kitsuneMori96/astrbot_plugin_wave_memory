import { fetchJson } from './client'

export const livingMemoryAliasNames = ['recall_long_term_memory', 'memorize_long_term_memory'] as const

export type CompatibilityStatus = 'detected' | 'not_detected' | 'not_configured' | 'probe_failed'

export interface CompatibilityEvidence {
  kind?: string
  summary?: string
  source?: string
  plugin_id?: string
  name?: string
  active?: boolean
  actual?: boolean
  configured?: unknown
  effective?: unknown
}

export interface CompatibilityFact {
  status: CompatibilityStatus
  source: string
  checked_at: string
  error: string | null
  evidence: CompatibilityEvidence[]
}

export interface CapabilityStatus extends CompatibilityFact {
  id: string
  enabled: boolean
  configured?: unknown
}

export interface DetectedPlugin {
  id?: string
  name?: string
  active?: boolean
  source?: string
}

export interface CompatibilityPayload extends CompatibilityFact {
  runtime?: Record<string, unknown> & Partial<CompatibilityFact>
  probe: CompatibilityFact & { plugins?: DetectedPlugin[] }
  facade: CapabilityStatus
  tool_aliases: Record<string, CapabilityStatus>
  capabilities?: CapabilityStatus[]
  detected_plugins: DetectedPlugin[]
  duplicate_warnings: Array<{ plugin_id?: string; name?: string; message?: string }>
  recommended_settings: string[]
  documentation: {
    kind: 'static'
    facade_interfaces: string[]
    tool_aliases: Record<string, string>
    notice: string
  }
}

export function getCompatibilityStatus(): Promise<CompatibilityPayload> {
  return fetchJson<CompatibilityPayload>('/api/compat/status')
}
