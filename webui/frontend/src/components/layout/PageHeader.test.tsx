import { MemoryRouter, useLocation } from 'react-router-dom'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AuthContext, type AuthContextValue } from '@/app/auth-context'
import { UnsavedChangesProvider, useUnsavedChangesGuard } from '@/app/unsaved-changes'
import { PageHeader } from '@/components/layout/PageHeader'

vi.mock('@/components/ui/sidebar', () => ({
  SidebarTrigger: () => <button type="button">切换侧栏</button>,
}))

function LocationProbe() {
  const location = useLocation()
  return <output aria-label="当前位置">{`${location.pathname}${location.search}${location.hash}`}</output>
}

function DirtyRegistration() {
  useUnsavedChangesGuard(true, '设置页草稿尚未保存。')
  return null
}

function renderHeader(value: AuthContextValue, dirty = false) {
  return render(
    <MemoryRouter initialEntries={['/memories?bot_id=bot%3Ayushu&offset=20#detail']}>
      <UnsavedChangesProvider>
        <AuthContext.Provider value={value}>
          {dirty ? <DirtyRegistration /> : null}
          <PageHeader />
          <LocationProbe />
        </AuthContext.Provider>
      </UnsavedChangesProvider>
    </MemoryRouter>,
  )
}

function authValue(logout = vi.fn()): AuthContextValue {
  return {
    state: { status: 'ready', token: 'token', requiresAuth: true },
    login: vi.fn(),
    logout,
    refresh: vi.fn(),
  }
}

describe('PageHeader 退出登录', () => {
  it('无 dirty 时立即退出，并保留当前 search/hash', async () => {
    const logout = vi.fn()
    const user = userEvent.setup()
    renderHeader(authValue(logout))

    await user.click(screen.getByRole('button', { name: '退出登录' }))

    expect(logout).toHaveBeenCalledOnce()
    expect(screen.getByRole('status', { name: '当前位置' })).toHaveTextContent('/memories?bot_id=bot%3Ayushu&offset=20#detail')
  })

  it('有 dirty 时先确认，取消不退出，确认后才 logout', async () => {
    const logout = vi.fn()
    const user = userEvent.setup()
    renderHeader(authValue(logout), true)

    await user.click(screen.getByRole('button', { name: '退出登录' }))
    expect(logout).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toHaveTextContent('退出登录后这些草稿将丢失')

    await user.click(screen.getByRole('button', { name: '继续编辑' }))
    expect(logout).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '退出登录' }))
    await user.click(screen.getByRole('button', { name: '放弃修改并退出登录' }))
    expect(logout).toHaveBeenCalledOnce()
  })

  it('无认证模式不显示退出按钮', () => {
    renderHeader({
      state: { status: 'ready', token: null, requiresAuth: false },
      login: vi.fn(),
      logout: vi.fn(),
      refresh: vi.fn(),
    })

    expect(screen.queryByRole('button', { name: '退出登录' })).not.toBeInTheDocument()
  })
})
