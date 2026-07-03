import { fetchJson } from './client'

export interface TraceFilters {
  from_ts?: number | string
  to_ts?: number | string
  from?: number | string
  to?: number | string
  group_id?: string
  sender_id?: string
  bot_id?: string
  channel?: string
  status?: string
  has_error?: boolean | string
  scope?: string
  chat_type?: string
  limit?: number
}

export interface InjectionTraceSummary {
  trace_id?: string
  timestamp?: number
  created_at?: number
  scope?: string
  chat_type?: string
  group_id?: string
  sender_id?: string
  bot_id?: string
  mode?: string
  status?: string
  session_id?: string
  preview?: string
  final_text_preview?: string
  total_tokens?: number
  latency_ms?: number
  has_error?: boolean
  [key: string]: unknown
}

export interface TraceListPayload {
  traces?: InjectionTraceSummary[]
  count?: number
  limit?: number
  error?: string
}

export interface TraceDetailPayload {
  trace_id?: string
  request?: Record<string, unknown>
  budget?: Record<string, unknown>
  channels?: Array<Record<string, unknown>>
  hits?: Array<Record<string, unknown>>
  filtered?: Array<Record<string, unknown>>
  final_text?: string
  final_injection_text?: string
  feedback?: Array<Record<string, unknown>>
  error?: string
  [key: string]: unknown
}

function toSearchParams(filters: TraceFilters): string {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value))
    }
  })
  return params.toString()
}

export function listInjectionTraces(filters: TraceFilters = {}): Promise<TraceListPayload> {
  const query = toSearchParams(filters)
  return fetchJson<TraceListPayload>(`/api/injection/traces${query ? `?${query}` : ''}`)
}

export function getInjectionTrace(traceId: string): Promise<TraceDetailPayload> {
  return fetchJson<TraceDetailPayload>(`/api/injection/traces/${encodeURIComponent(traceId)}`)
}
