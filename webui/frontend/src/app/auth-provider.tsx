import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import { checkAuth, login as loginRequest } from '@/api/auth'
import { clearStoredToken, getStoredToken, setAuthFailureHandler, setStoredToken } from '@/api/client'
import { getSystemStatus } from '@/api/system'

export type AuthState =
  | { status: 'checking' }
  | { status: 'anonymous'; requiresAuth: true }
  | { status: 'ready'; token: string | null; requiresAuth: boolean }

interface AuthContextValue {
  state: AuthState
  login: (password: string) => Promise<void>
  logout: () => void
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: 'checking' })

  const logout = useCallback(() => {
    clearStoredToken()
    setState({ status: 'anonymous', requiresAuth: true })
  }, [])

  const refresh = useCallback(async () => {
    setState({ status: 'checking' })
    const auth = await checkAuth()
    if (!auth.requires_auth) {
      clearStoredToken()
      setState({ status: 'ready', token: null, requiresAuth: false })
      return
    }

    const token = getStoredToken()
    if (!token) {
      setState({ status: 'anonymous', requiresAuth: true })
      return
    }

    try {
      await getSystemStatus()
      setState({ status: 'ready', token, requiresAuth: true })
    } catch (error) {
      if (typeof error === 'object' && error !== null && 'status' in error && (error as { status?: number }).status === 401) {
        clearStoredToken()
        setState({ status: 'anonymous', requiresAuth: true })
        return
      }
      setState({ status: 'ready', token, requiresAuth: true })
    }
  }, [])

  const login = useCallback(async (password: string) => {
    const response = await loginRequest(password)
    setStoredToken(response.token)
    setState({ status: 'ready', token: response.token, requiresAuth: true })
  }, [])

  useEffect(() => {
    setAuthFailureHandler(logout)
    void refresh()
    return () => setAuthFailureHandler(undefined)
  }, [logout, refresh])

  const value = useMemo<AuthContextValue>(() => ({ state, login, logout, refresh }), [state, login, logout, refresh])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}
