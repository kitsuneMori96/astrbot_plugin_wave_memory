import { fetchJson } from './client'

export interface LlmTraceFilters {
  from_ts?: number | string
  to_ts?: number | string
  group_id?: string
  sender_id?: string
  limit?: number
}

export interface LlmTraceSummary {
  trace_id?: string
  timestamp?: number
  group_id?: string
  sender_id?: string
  message_preview?: string
  model?: string
  provider_id?: string
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  latency_ms?: number
  status?: string
  error?: string
  response_preview?: string
  [key: string]: unknown
}

export interface LlmTraceDetail extends LlmTraceSummary {
  system_prompt?: string
  contexts?: Array<Record<string, unknown>>
  extra_parts?: string[]
  tools?: Array<Record<string, unknown>>
}

export interface LlmTraceListPayload {
  traces?: LlmTraceSummary[]
  count?: number
  limit?: number
  error?: string
}

function toSearchParams(filters: LlmTraceFilters): string {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value))
    }
  })
  return params.toString()
}

export function listLlmTraces(filters: LlmTraceFilters = {}): Promise<LlmTraceListPayload> {
  const query = toSearchParams(filters)
  return fetchJson<LlmTraceListPayload>(`/api/llm-debug/traces${query ? `?${query}` : ''}`)
}

export function getLlmTrace(traceId: string): Promise<LlmTraceDetail> {
  return fetchJson<LlmTraceDetail>(`/api/llm-debug/traces/${encodeURIComponent(traceId)}`)
}

export function clearLlmTraces(): Promise<{ deleted?: number; error?: string }> {
  return fetchJson<{ deleted?: number; error?: string }>('/api/llm-debug/traces', {
    method: 'DELETE',
  })
}
