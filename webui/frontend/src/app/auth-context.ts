import { createContext, useContext } from 'react'

export type AuthState =
  | { status: 'checking' }
  | { status: 'anonymous'; requiresAuth: true }
  | { status: 'ready'; token: string | null; requiresAuth: boolean }

export interface AuthContextValue {
  state: AuthState
  login: (password: string) => Promise<void>
  logout: () => void
  refresh: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}
