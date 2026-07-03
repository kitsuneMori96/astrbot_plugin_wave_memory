import { fetchJson } from './client'

export interface AuthCheckResponse {
  requires_auth: boolean
}

export interface LoginResponse {
  token: string
  message?: string
}

export function checkAuth(): Promise<AuthCheckResponse> {
  return fetchJson<AuthCheckResponse>('/api/auth/check', { noAuth: true })
}

export function login(password: string): Promise<LoginResponse> {
  return fetchJson<LoginResponse>('/api/login', {
    method: 'POST',
    noAuth: true,
    body: JSON.stringify({ password }),
  })
}
