import { fetchJson } from './client'

export interface AgentFeedbackPayload {
  feedback_records?: Array<Record<string, unknown>>
  config_suggestions?: Array<Record<string, unknown>>
  review_candidates?: Array<Record<string, unknown>>
  history?: Record<string, Array<Record<string, unknown>>>
  summary?: Record<string, number>
  safety_note?: string
  error?: string
}

export interface AgentActionResponse {
  ok?: boolean
  message?: string
  error?: string
  [key: string]: unknown
}

export type AgentAction = 'approve' | 'reject' | 'ignore'

export function getAgentFeedback(): Promise<AgentFeedbackPayload> {
  return fetchJson<AgentFeedbackPayload>('/api/agent-feedback')
}

export function reviewConfigSuggestion(id: number, action: AgentAction): Promise<AgentActionResponse> {
  return fetchJson<AgentActionResponse>(`/api/agent-feedback/config-suggestions/${id}/${action}`, { method: 'POST' })
}
