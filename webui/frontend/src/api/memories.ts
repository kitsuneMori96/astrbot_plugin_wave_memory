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
  selected?: number
  tagged?: number
  imported?: number
  skipped?: number
  errors?: number
  remaining?: number
  partial?: boolean
  done?: boolean
  error?: string
  message?: string
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

/**
 * 专为 POST + text/event-stream 持续流解包而封装的高性能 XHR/Fetch Chunk Reader。
 */
export interface StreamOptions {
  signal?: AbortSignal
  payload?: Record<string, unknown>
}

export async function runPostStream(
  path: string,
  ids: number[],
  onProgress: (state: StreamProgress) => void,
  options: StreamOptions = {}
): Promise<StreamProgress | null> {
  const token = getStoredToken()
  const payload = options.payload ?? {}
  const response = await fetch(toApiPath(path), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ ids, ...payload }),
    signal: options.signal,
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
  let lastPayload: StreamProgress | null = null

  const handleLine = (line: string) => {
    const cleanLine = line.trim()
    if (!cleanLine || !cleanLine.startsWith('data:')) return

    const jsonStr = cleanLine.substring(5).trim()
    let payload: StreamProgress
    try {
      payload = JSON.parse(jsonStr) as StreamProgress
    } catch {
      return
    }

    lastPayload = payload
    onProgress(payload)
    if (payload.error) {
      throw new Error(payload.error)
    }
  }

  while (true) {
    let chunk: ReadableStreamReadResult<Uint8Array>
    try {
      chunk = await reader.read()
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err)
      throw new Error(`流式连接中断：${detail || '网络连接被提前关闭'}`)
    }

    const { value, done } = chunk
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      handleLine(line)
    }
  }

  if (buffer.trim()) {
    handleLine(buffer)
  }

  return lastPayload
}

export interface SimilarMemoryItem {
  id: number
  content: string
  source: string
  similarity: number
}

export function getSimilarMemories(id: number): Promise<{ items: SimilarMemoryItem[]; reason?: string }> {
  return fetchJson<{ items: SimilarMemoryItem[]; reason?: string }>(`/api/memories/${id}/similar`)
}

export function addMemoryTag(id: number, tagName: string): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/memories/${id}/tags`, {
    method: 'POST',
    body: JSON.stringify({ tag_name: tagName }),
  })
}

export function deleteMemoryTag(id: number, tagName: string): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/memories/${id}/tags/${encodeURIComponent(tagName)}`, {
    method: 'DELETE',
  })
}
