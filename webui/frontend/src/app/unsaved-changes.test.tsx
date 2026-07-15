import { useState } from 'react'
import { HashRouter, Link, useLocation } from 'react-router-dom'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { UnsavedChangesProvider, useUnsavedChangesGuard } from '@/app/unsaved-changes'

function GuardRegistration({ message = '测试草稿尚未保存。' }: { message?: string }) {
  useUnsavedChangesGuard(true, message)
  return null
}

function LocationProbe() {
  const location = useLocation()
  return <output aria-label="当前位置">{`${location.pathname}${location.search}${location.hash}`}</output>
}

function Harness() {
  const [guardMounted, setGuardMounted] = useState(true)
  return (
    <>
      {guardMounted ? <GuardRegistration /> : null}
      <button type="button" onClick={() => setGuardMounted(false)}>卸载 guard</button>
      <Link to="/next?tab=1#detail">站内下一页</Link>
      <a href="#/modified" onClick={(event) => event.preventDefault()}>modified 站内链接</a>
      <a href="https://example.org" target="_blank" rel="noreferrer">外部新窗口</a>
      <LocationProbe />
    </>
  )
}

function MultipleGuardsHarness() {
  const [firstMounted, setFirstMounted] = useState(true)
  return (
    <>
      {firstMounted ? <GuardRegistration message="第一处草稿未保存。" /> : null}
      <GuardRegistration message="第二处草稿未保存。" />
      <button type="button" onClick={() => setFirstMounted(false)}>卸载第一个 guard</button>
      <Link to="/next">多 guard 导航</Link>
      <LocationProbe />
    </>
  )
}

function renderHarness(element = <Harness />) {
  window.history.replaceState(null, '', '/#/current?keep=1')
  return render(<HashRouter><UnsavedChangesProvider>{element}</UnsavedChangesProvider></HashRouter>)
}

describe('共享未保存离开保护', () => {
  it('站内导航取消时保持当前地址，确认后才离开', async () => {
    const user = userEvent.setup()
    renderHarness()

    const location = screen.getByLabelText('当前位置')
    await user.click(screen.getByRole('link', { name: '站内下一页' }))
    expect(location).toHaveTextContent('/current?keep=1')
    expect(screen.getByRole('dialog')).toHaveTextContent('测试草稿尚未保存')

    await user.click(screen.getByRole('button', { name: '继续编辑' }))
    expect(location).toHaveTextContent('/current?keep=1')

    await user.click(screen.getByRole('link', { name: '站内下一页' }))
    await user.click(screen.getByRole('button', { name: '放弃修改并离开' }))
    expect(screen.getByRole('status', { name: '当前位置' })).toHaveTextContent('/next?tab=1#detail')
  })

  it('直接 hash 变化会恢复原地址，确认后完成目标导航', async () => {
    const user = userEvent.setup()
    renderHarness()
    const location = screen.getByLabelText('当前位置')

    act(() => { window.location.hash = '#/direct?from=hash' })
    expect(await screen.findByRole('dialog')).toHaveTextContent('测试草稿尚未保存')
    expect(window.location.hash).toBe('#/current?keep=1')
    expect(location).toHaveTextContent('/current?keep=1')

    await user.click(screen.getByRole('button', { name: '继续编辑' }))
    expect(window.location.hash).toBe('#/current?keep=1')

    act(() => { window.location.hash = '#/direct?from=hash' })
    await user.click(await screen.findByRole('button', { name: '放弃修改并离开' }))
    await waitFor(() => expect(location).toHaveTextContent('/direct?from=hash'))
  })

  it('浏览器 back 触发 popstate 时取消保持原地址，确认后到达历史目标', async () => {
    const user = userEvent.setup()
    window.history.replaceState(null, '', '/#/history-target')
    window.history.pushState(null, '', '/#/current?keep=1')
    const firstView = render(<HashRouter><UnsavedChangesProvider><Harness /></UnsavedChangesProvider></HashRouter>)
    const firstLocation = screen.getByLabelText('当前位置')

    act(() => { window.history.back() })
    expect(await screen.findByRole('dialog')).toHaveTextContent('测试草稿尚未保存')
    expect(firstLocation).toHaveTextContent('/current?keep=1')
    await user.click(screen.getByRole('button', { name: '继续编辑' }))
    expect(firstLocation).toHaveTextContent('/current?keep=1')
    firstView.unmount()

    window.history.replaceState(null, '', '/#/history-target')
    window.history.pushState(null, '', '/#/current?keep=1')
    render(<HashRouter><UnsavedChangesProvider><Harness /></UnsavedChangesProvider></HashRouter>)
    const confirmedLocation = screen.getByLabelText('当前位置')
    act(() => { window.history.back() })
    await user.click(await screen.findByRole('button', { name: '放弃修改并离开' }))
    await waitFor(() => expect(confirmedLocation).toHaveTextContent('/history-target'))
  })

  it('beforeunload 生效，guard 组件卸载后清理注册并允许导航', async () => {
    const user = userEvent.setup()
    renderHarness()
    const guardedUnload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(guardedUnload)
    expect(guardedUnload.defaultPrevented).toBe(true)

    await user.click(screen.getByRole('button', { name: '卸载 guard' }))
    const cleanUnload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(cleanUnload)
    expect(cleanUnload.defaultPrevented).toBe(false)
    await user.click(screen.getByRole('link', { name: '站内下一页' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('status', { name: '当前位置' })).toHaveTextContent('/next?tab=1#detail')
  })

  it('卸载一个 guard 不会清掉另一个 guard', async () => {
    const user = userEvent.setup()
    renderHarness(<MultipleGuardsHarness />)

    await user.click(screen.getByRole('button', { name: '卸载第一个 guard' }))
    await user.click(screen.getByRole('link', { name: '多 guard 导航' }))

    expect(screen.getByRole('dialog')).toHaveTextContent('第二处草稿未保存')
    expect(screen.getByLabelText('当前位置')).toHaveTextContent('/current?keep=1')
  })

  it('Ctrl/Meta modified click 与外部新窗口链接不会被误拦截', async () => {
    const user = userEvent.setup()
    renderHarness()
    const internal = screen.getByRole('link', { name: 'modified 站内链接' })

    fireEvent.click(internal, { ctrlKey: true })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    fireEvent.click(internal, { metaKey: true })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    await user.click(screen.getByRole('link', { name: '外部新窗口' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
