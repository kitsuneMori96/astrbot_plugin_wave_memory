import { fetchJson } from './client'

export interface ConcernItem {
  id: number
  topic: string
  intensity: number
  bot_id?: string
  last_triggered?: number
}

export interface TimeAnchorItem {
  id: number
  event_summary: string
  emotional_weight: number
  timestamp: number
  bot_id?: string
}

export interface MoodItem {
  id: number
  type: string
  intensity: number
  description: string
  timestamp: number
  is_active: boolean
  group_id?: string
  bot_id?: string
}

export function listConcerns(botId?: string): Promise<{ items: ConcernItem[] }> {
  return fetchJson<{ items: ConcernItem[] }>(`/api/concerns${botId ? `?bot_id=${botId}` : ''}`)
}

export function createConcern(payload: Partial<ConcernItem>): Promise<{ ok: boolean; id: number }> {
  return fetchJson<{ ok: boolean; id: number }>('/api/concerns', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateConcern(id: number, payload: Partial<ConcernItem>): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/concerns/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function deleteConcern(id: number): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/concerns/${id}`, {
    method: 'DELETE',
  })
}

export function listTimeAnchors(botId?: string): Promise<{ items: TimeAnchorItem[] }> {
  return fetchJson<{ items: TimeAnchorItem[] }>(`/api/time-anchors${botId ? `?bot_id=${botId}` : ''}`)
}

export function createTimeAnchor(payload: Partial<TimeAnchorItem>): Promise<{ ok: boolean; id: number }> {
  return fetchJson<{ ok: boolean; id: number }>('/api/time-anchors', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateTimeAnchor(id: number, payload: Partial<TimeAnchorItem>): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/time-anchors/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function deleteTimeAnchor(id: number): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/time-anchors/${id}`, {
    method: 'DELETE',
  })
}

export function listMoods(groupId?: string): Promise<{ items: MoodItem[] }> {
  return fetchJson<{ items: MoodItem[] }>(`/api/mood/trajectory${groupId ? `?group_id=${groupId}` : ''}`)
}

export function updateMood(id: number, payload: Partial<MoodItem>): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/mood/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function deleteMood(id: number): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/mood/${id}`, {
    method: 'DELETE',
  })
}
