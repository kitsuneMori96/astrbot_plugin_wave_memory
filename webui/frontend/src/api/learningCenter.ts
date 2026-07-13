import { fetchJson } from './client'

export type LearningCandidateType =
  | 'worldview_internalization'
  | 'book_experience_episode'
  | 'interaction_experience'
  | 'few_shot_style'
  | 'fact'
  | 'relationship'
  | 'book_lore'
  | 'jargon_candidate'
  | 'belief_candidate'
  | string

export type LearningReviewStatus = 'pending' | 'approved' | 'rejected' | 'ignored' | 'delegated' | string
export type LearningPromotionStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'retryable_failed'
  | 'terminal_failed'
  | 'waiting_dedicated_review'
  | 'partial'
  | 'mixed'
  | string

export interface LearningListQuery {
  bot_id: string
  limit?: number
  offset?: number
  candidate_type?: string
  review_status?: string
  promotion_status?: string
  target_kind?: string
  source?: string
  since?: number
  until?: number
}

export interface LearningListPayload<T> {
  items: T[]
  total: number
  limit: number
  offset: number
  has_more: boolean
}

export interface LearningSourceItem {
  id: number
  bot_id: string
  source_type?: string
  name?: string
  enabled?: boolean
  config?: Record<string, unknown>
  cursor?: Record<string, unknown> | null
  [key: string]: unknown
}

export interface LearningJobItem {
  id: number
  bot_id: string
  source_id?: number
  candidate_type?: LearningCandidateType
  name?: string
  enabled?: boolean
  schedule?: Record<string, unknown>
  policy?: Record<string, unknown>
  [key: string]: unknown
}

export interface LearningPromotionItem {
  id: number
  candidate_id?: number
  bot_id: string
  candidate_type?: LearningCandidateType
  target_kind?: string
  target_id?: number | string | null
  promotion_status?: LearningPromotionStatus
  error_code?: string | null
  error_message?: string | null
  retryable?: boolean
  metadata?: Record<string, unknown>
  [key: string]: unknown
}

export interface LearningCandidateItem {
  id: number
  bot_id: string
  candidate_type?: LearningCandidateType
  content?: string
  reason?: string
  source_fingerprint?: string
  source_id?: number | null
  job_id?: number | null
  review_status?: LearningReviewStatus
  reviewer?: string | null
  reviewed_at?: number | string | null
  review_note?: string | null
  evidence?: Record<string, unknown> | unknown[] | null
  source?: LearningSourceItem | null
  task?: LearningJobItem | null
  promotion_status?: LearningPromotionStatus | null
  promotions?: LearningPromotionItem[]
  target_ids?: Array<number | string>
  failures?: Array<{ id?: number; code?: string | null; message?: string | null; retryable?: boolean }>
  operations?: Array<Record<string, unknown>>
  [key: string]: unknown
}

export interface ApprovedFewShotExample {
  id?: number | string
  content?: string
  score?: number
  traits?: string[]
  status?: string
  bot_id?: string
  created_at?: number | string
  approved_at?: number | string
  [key: string]: unknown
}

export interface LearningFewShotPayload {
  items?: LearningCandidateItem[]
  candidates?: LearningCandidateItem[]
  approved_examples?: ApprovedFewShotExample[]
  total: number
  limit: number
  offset: number
  has_more: boolean
  [key: string]: unknown
}

export interface LearningExperiencesPayload {
  worldview_internalization?: LearningCandidateItem[]
  book_experience_episodes?: LearningCandidateItem[]
  interaction_experiences?: Array<Record<string, unknown>>
  legacy_history?: {
    evolution?: Array<Record<string, unknown>>
    experience?: Array<Record<string, unknown>>
  }
  pagination?: { limit?: number; offset?: number; has_more?: boolean }
  labels?: Record<string, string>
  [key: string]: unknown
}

export interface DedicatedReviewStatus {
  candidate_id: number
  candidate_type?: LearningCandidateType
  target_id?: number | string | null
  status?: string
  deep_link?: string | null
  error?: string | null
  metadata?: Record<string, unknown>
  promotion?: LearningPromotionItem | null
  [key: string]: unknown
}

export interface LearningActionResponse {
  ok?: boolean
  item?: {
    candidate?: LearningCandidateItem
    promotions?: LearningPromotionItem[]
    [key: string]: unknown
  } | LearningPromotionItem | Record<string, unknown>
  [key: string]: unknown
}

function toQueryString(query: LearningListQuery): string {
  const params = new URLSearchParams()
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value))
    }
  })
  return `?${params.toString()}`
}

function idempotentInit(idempotencyKey?: string, body?: Record<string, unknown>) {
  const headers = idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined
  return {
    method: 'POST',
    ...(headers ? { headers } : {}),
    ...(body ? { body: JSON.stringify(body) } : {}),
  }
}

export function listLearningSources(query: LearningListQuery): Promise<LearningListPayload<LearningSourceItem>> {
  return fetchJson<LearningListPayload<LearningSourceItem>>(`/api/learning-center/sources${toQueryString(query)}`)
}

export function listLearningJobs(query: LearningListQuery): Promise<LearningListPayload<LearningJobItem>> {
  return fetchJson<LearningListPayload<LearningJobItem>>(`/api/learning-center/jobs${toQueryString(query)}`)
}

export function runLearningJob(jobId: number, botId: string, idempotencyKey?: string): Promise<LearningActionResponse> {
  return fetchJson<LearningActionResponse>(`/api/learning-center/jobs/${jobId}/run?bot_id=${encodeURIComponent(botId)}`, idempotentInit(idempotencyKey))
}

export function listLearningCandidates(query: LearningListQuery): Promise<LearningListPayload<LearningCandidateItem>> {
  return fetchJson<LearningListPayload<LearningCandidateItem>>(`/api/learning-center/candidates${toQueryString(query)}`)
}

export function getLearningCandidate(candidateId: number, botId: string): Promise<{ item: LearningCandidateItem }> {
  return fetchJson<{ item: LearningCandidateItem }>(`/api/learning-center/candidates/${candidateId}?bot_id=${encodeURIComponent(botId)}`)
}

export function reviewLearningCandidate(
  candidateId: number,
  botId: string,
  action: 'approve' | 'reject' | 'ignore',
  options: { reviewer?: string; note?: string; idempotencyKey?: string } = {},
): Promise<LearningActionResponse> {
  return fetchJson<LearningActionResponse>(
    `/api/learning-center/candidates/${candidateId}/review?bot_id=${encodeURIComponent(botId)}`,
    idempotentInit(options.idempotencyKey, { action, reviewer: options.reviewer ?? 'webui', note: options.note }),
  )
}

export function listLearningPromotions(query: LearningListQuery): Promise<LearningListPayload<LearningPromotionItem>> {
  return fetchJson<LearningListPayload<LearningPromotionItem>>(`/api/learning-center/promotions${toQueryString(query)}`)
}

export function retryLearningPromotion(promotionId: number, botId: string, idempotencyKey?: string): Promise<LearningActionResponse> {
  return fetchJson<LearningActionResponse>(
    `/api/learning-center/promotions/${promotionId}/retry?bot_id=${encodeURIComponent(botId)}`,
    idempotentInit(idempotencyKey),
  )
}

export function getLearningFewShot(query: LearningListQuery): Promise<LearningFewShotPayload> {
  return fetchJson<LearningFewShotPayload>(`/api/learning-center/few-shot${toQueryString(query)}`)
}

export function getLearningExperiences(query: LearningListQuery): Promise<LearningExperiencesPayload> {
  return fetchJson<LearningExperiencesPayload>(`/api/learning-center/experiences${toQueryString(query)}`)
}

export function getDedicatedReviewStatus(candidateId: number, botId: string): Promise<{ item: DedicatedReviewStatus }> {
  return fetchJson<{ item: DedicatedReviewStatus }>(
    `/api/learning-center/dedicated-review-status/${candidateId}?bot_id=${encodeURIComponent(botId)}`,
  )
}

// 与现有 WebUI API client 的 getX 命名保持兼容，页面内部使用 listX 表达分页列表语义。
export const getLearningSources = listLearningSources
export const getLearningJobs = listLearningJobs
export const getLearningCandidates = listLearningCandidates
export const getLearningPromotions = listLearningPromotions
