import { fetchJson } from './client'

export interface LearningObjectItem {
  key?: string
  name?: string
  risk?: string
  mode_enabled?: boolean
  mode_disabled_reason?: string
  available_modes?: string[]
  audit_findings?: string[]
  write_path?: string
  storage?: string
  injection_channel?: string
  [key: string]: unknown
}

export interface ReviewCandidate {
  id?: number
  candidate_type?: string
  object_key?: string
  object_risk?: string
  mode_enabled?: boolean
  review_status?: string
  content?: string
  [key: string]: unknown
}

export interface LearningObjectReviewPayload {
  runtime?: Record<string, unknown>
  objects?: LearningObjectItem[]
  pending_candidates?: ReviewCandidate[]
  risky_candidates?: ReviewCandidate[]
  duplicate_entries?: Array<Record<string, unknown>>
  summary?: Record<string, number>
}

export interface AgentFeedbackPayload {
  feedback_records?: Array<Record<string, unknown>>
  config_suggestions?: Array<Record<string, unknown>>
  review_candidates?: ReviewCandidate[]
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

export function getLearningObjectsReview(): Promise<LearningObjectReviewPayload> {
  return fetchJson<LearningObjectReviewPayload>('/api/learning-objects/review')
}

export function getAgentFeedback(): Promise<AgentFeedbackPayload> {
  return fetchJson<AgentFeedbackPayload>('/api/agent-feedback')
}

export function reviewConfigSuggestion(id: number, action: AgentAction): Promise<AgentActionResponse> {
  return fetchJson<AgentActionResponse>(`/api/agent-feedback/config-suggestions/${id}/${action}`, { method: 'POST' })
}

export function reviewCandidate(id: number, action: AgentAction): Promise<AgentActionResponse> {
  return fetchJson<AgentActionResponse>(`/api/agent-feedback/review-candidates/${id}/${action}`, { method: 'POST' })
}
