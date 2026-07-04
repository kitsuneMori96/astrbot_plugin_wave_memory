import { fetchJson, getStoredToken, toApiPath } from './client'

export interface MemoryTag {
  name: string
  type?: string
  [key: string]: unknown
}

export interface MemoryItem {
  id: number
  content: string
  sender_id: string
  sender_name?: string
  group_id?: string
  source?: string
  timestamp: number
  has_vector: boolean
  tags?: MemoryTag[]
}

export interface SenderItem {
  name: string
  count: number
}

export interface MemoryDetail {
  id: number
  content: string
  sender_id?: string
  sender_name?: string
  group_id?: string
  source?: string
  timestamp?: number
  importance?: number
  access_count?: number
  has_vector?: boolean
  tags?: MemoryTag[]
}

export interface MemoriesFilters {
  page?: number
  size?: number
  source?: string
  sender?: string
  has_tags?: string // 'true'/'false'
  has_vector?: string // 'true'/'false'
  search?: string
}

export interface MemoriesResponse {
  items: MemoryItem[]
  total: number | null
  has_more: boolean
  limit: number
  offset: number
}

export interface StreamProgress {
  progress: number
  processed?: number
  total: number
  errors?: number
  done?: boolean
  error?: string
}

export interface NebulaPoint {
  id: number
  content: string
  sender: string
  ts: number
  x: number
  y: number
  cluster: string
}

export interface NebulaCluster {
  name: string
  cx: number
  cy: number
  count: number
}

export interface MemoriesNebulaResponse {
  clusters: NebulaCluster[]
  points: NebulaPoint[]
}

export function listMemories(filters: MemoriesFilters): Promise<MemoriesResponse> {
  const params = new URLSearchParams()
  if (filters.page) params.append('page', String(filters.page))
  if (filters.size) params.append('size', String(filters.size))
  if (filters.source) params.append('source', filters.source)
  if (filters.sender) params.append('sender', filters.sender)
  if (filters.has_tags) params.append('has_tags', filters.has_tags)
  if (filters.has_vector) params.append('has_vector', filters.has_vector)
  if (filters.search) params.append('search', filters.search)

  return fetchJson<MemoriesResponse>(`/api/memories?${params.toString()}`)
}

export function listSenders(): Promise<{ senders: SenderItem[] }> {
  return fetchJson<{ senders: SenderItem[] }>('/api/memories/senders?limit=300')
}

export function getMemoryDetail(id: number): Promise<MemoryDetail> {
  return fetchJson<MemoryDetail>(`/api/memories/${id}`)
}

export function updateMemory(id: number, content: string, importance: number): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/memories/${id}`, {
    method: 'PUT',
    body: JSON.stringify({ content, importance }),
  })
}

export function deleteMemory(id: number): Promise<{ ok: boolean; deleted: number }> {
  return fetchJson<{ ok: boolean; deleted: number }>(`/api/memories/${id}`, {
    method: 'DELETE',
  })
}

export function reEmbedMemory(id: number): Promise<{ ok: boolean; error?: string }> {
  return fetchJson<{ ok: boolean; error?: string }>(`/api/memories/${id}/re-embed`, {
    method: 'POST',
  })
}

export function batchDeleteMemories(ids: number[]): Promise<{ ok: boolean; deleted: number }> {
  return fetchJson<{ ok: boolean; deleted: number }>('/api/memories/batch/delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })
}

export function getMemoryClusters(): Promise<MemoriesNebulaResponse> {
  return fetchJson<MemoriesNebulaResponse>('/api/memories/clusters')
}

/**
 * 专为 POST + text/event-stream 持续流解包而封装的高性能 XHR/Fetch Chunk Reader。
 */
export async function runPostStream(
  path: string,
  ids: number[],
  onProgress: (state: StreamProgress) => void
): Promise<void> {
  const token = getStoredToken()
  const response = await fetch(toApiPath(path), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ ids }),
  })

  if (!response.ok) {
    throw new Error(`HTTP stream request failed: ${response.status}`)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('ReadableStream is not supported by current browser environment')
  }

  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      const cleanLine = line.trim()
      if (!cleanLine) continue
      if (cleanLine.startsWith('data:')) {
        try {
          const jsonStr = cleanLine.substring(5).trim()
          const payload = JSON.parse(jsonStr) as StreamProgress
          onProgress(payload)
        } catch (e) {
          // Ignore
        }
      }
    }
  }
}
