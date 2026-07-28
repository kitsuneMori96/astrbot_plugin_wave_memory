import { fetchJson } from './client'

export interface BindingItem {
  id: number
  local_id: string
  platform: string
  master_id: string
  bot_id: string
  created_at: number
}

export interface BindingListPayload {
  items: BindingItem[]
  total: number
  limit: number
  offset: number
}

export interface BindingFormData {
  local_id: string
  platform: string
  master_id: string
  bot_id?: string
}

export function getBindings(params: { search?: string; limit?: number; offset?: number }): Promise<BindingListPayload> {
  const q: Record<string, string> = {}
  if (params.search) q.search = params.search
  if (params.limit !== undefined) q.limit = String(params.limit)
  if (params.offset !== undefined) q.offset = String(params.offset)
  const qs = new URLSearchParams(q).toString()
  return fetchJson(`/api/bindings${qs ? '?' + qs : ''}`)
}

export function createBinding(data: BindingFormData): Promise<BindingItem> {
  return fetchJson('/api/bindings', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export function deleteBinding(id: number): Promise<{ ok: boolean }> {
  return fetchJson(`/api/bindings/${id}`, { method: 'DELETE' })
}
