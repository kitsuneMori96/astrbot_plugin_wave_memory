import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthGate } from '@/App'
import { AuthProvider } from '@/app/auth-provider'

const api = vi.hoisted(() => ({
  checkAuth: vi.fn(),
  getSystemStatus: vi.fn(),
  login: vi.fn(),
}))

vi.mock('@/api/auth', () => ({
  checkAuth: api.checkAuth,
  login: api.login,
}))

vi.mock('@/api/system', () => ({
  getSystemStatus: api.getSystemStatus,
}))

vi.mock('@/app/AppShell', () => ({
  AppRoutes: () => <div>应用已就绪</div>,
}))

vi.mock('@/pages/LoginPage', () => ({
  LoginPage: () => <div>需要登录</div>,
}))

beforeEach(() => {
  const values = new Map<string, string>()
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      clear: () => values.clear(),
      getItem: (key: string) => values.get(key) ?? null,
      key: (index: number) => Array.from(values.keys())[index] ?? null,
      get length() { return values.size },
      removeItem: (key: string) => values.delete(key),
      setItem: (key: string, value: string) => values.set(key, String(value)),
    } satisfies Storage,
  })
  api.checkAuth.mockReset()
  api.getSystemStatus.mockReset()
  api.login.mockReset()
})

describe('AuthProvider 与 AuthGate', () => {
  it('认证检查失败进入 error，重试期间回到 checking，成功后恢复', async () => {
    let resolveRetry: ((value: { requires_auth: boolean }) => void) | undefined
    api.checkAuth
      .mockRejectedValueOnce(new Error('认证服务暂时不可用'))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveRetry = resolve }))
    const user = userEvent.setup()

    render(<AuthProvider><AuthGate /></AuthProvider>)

    expect(await screen.findByRole('alert')).toHaveTextContent('认证服务暂时不可用')
    await user.click(screen.getByRole('button', { name: '重试认证检查' }))
    expect(screen.getByRole('status', { name: '正在检查认证状态' })).toBeVisible()

    resolveRetry?.({ requires_auth: false })
    expect(await screen.findByText('应用已就绪')).toBeVisible()
  })

  it('401 会清理本地 token 并进入 anonymous，而不是 error', async () => {
    window.localStorage.setItem('wavememory.webui.token', 'expired-token')
    api.checkAuth.mockRejectedValue(Object.assign(new Error('unauthorized'), { status: 401 }))

    render(<AuthProvider><AuthGate /></AuthProvider>)

    expect(await screen.findByText('需要登录')).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(window.localStorage.getItem('wavememory.webui.token')).toBeNull()
  })

  it('服务端要求认证且本地无 token 时保持 anonymous 语义', async () => {
    api.checkAuth.mockResolvedValue({ requires_auth: true })

    render(<AuthProvider><AuthGate /></AuthProvider>)

    await waitFor(() => expect(screen.getByText('需要登录')).toBeVisible())
    expect(api.getSystemStatus).not.toHaveBeenCalled()
  })
})
