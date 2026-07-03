export interface ApiError extends Error {
  status?: number
  detail?: string
  payload?: unknown
}

export interface ApiRequestInit extends RequestInit {
  noAuth?: boolean
}

const TOKEN_KEY = 'wavememory.webui.token'

let authFailureHandler: (() => void) | undefined

export function getStoredToken(): string | null {
  if (typeof window === 'undefined') {
    return null
  }
  return window.localStorage.getItem(TOKEN_KEY)
}

export function setStoredToken(token: string | null): void {
  if (typeof window === 'undefined') {
    return
  }
  if (token) {
    window.localStorage.setItem(TOKEN_KEY, token)
  } else {
    window.localStorage.removeItem(TOKEN_KEY)
  }
}

export function clearStoredToken(): void {
  setStoredToken(null)
}

export function setAuthFailureHandler(handler: (() => void) | undefined): void {
  authFailureHandler = handler
}

function toApiPath(path: string): string {
  if (/^https?:\/\//i.test(path)) {
    return path
  }
  return path.startsWith('/') ? path : `/${path}`
}

async function parseResponse(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('application/json')) {
    return response.json()
  }
  const text = await response.text()
  if (!text) {
    return null
  }
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function createApiError(response: Response, payload: unknown): ApiError {
  const detail =
    typeof payload === 'object' && payload !== null && 'detail' in payload
      ? String((payload as { detail?: unknown }).detail)
      : typeof payload === 'object' && payload !== null && 'error' in payload
        ? String((payload as { error?: unknown }).error)
        : response.statusText
  const error = new Error(detail || `HTTP ${response.status}`) as ApiError
  error.status = response.status
  error.detail = detail
  error.payload = payload
  return error
}

export async function fetchJson<T>(path: string, init: ApiRequestInit = {}): Promise<T> {
  const { noAuth, headers, body, ...fetchInit } = init
  const requestHeaders = new Headers(headers)
  const token = getStoredToken()

  if (body && !(body instanceof FormData) && !requestHeaders.has('Content-Type')) {
    requestHeaders.set('Content-Type', 'application/json')
  }
  if (!noAuth && token && token !== 'no-auth' && !requestHeaders.has('Authorization')) {
    requestHeaders.set('Authorization', `Bearer ${token}`)
  }

  const response = await fetch(toApiPath(path), {
    ...fetchInit,
    body,
    headers: requestHeaders,
  })
  const payload = await parseResponse(response)

  if (!response.ok) {
    if (response.status === 401) {
      clearStoredToken()
      authFailureHandler?.()
    }
    throw createApiError(response, payload)
  }

  return payload as T
}
