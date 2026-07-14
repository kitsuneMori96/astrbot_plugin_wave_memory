import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { buildObjectDeepLink } from '@/lib/object-deep-link'
import { ObjectDeepLink } from '../ObjectDeepLink'
import { QueryState } from '../QueryState'
import { ScopeSelect } from '../ScopeSelect'

const options = [
  { value: 'bot:yushu', label: '羽书', kind: 'bot' as const },
  { value: 'session:group:42', label: '群 42', kind: 'session' as const },
]

describe('ScopeSelect 与 ObjectDeepLink', () => {
  it('只呈现异步返回的真实 scoped options', async () => {
    const onChange = vi.fn()
    const loadOptions = vi.fn(async () => options)
    const user = userEvent.setup()
    render(<ScopeSelect loadOptions={loadOptions} onValueChange={onChange} />)

    const trigger = await screen.findByRole('combobox', { name: '作用域' })
    await user.click(trigger)
    await user.click(screen.getByRole('option', { name: '羽书' }))

    expect(onChange).toHaveBeenCalledWith('bot:yushu', options[0])
    expect(screen.queryByRole('option', { name: '白真真' })).not.toBeInTheDocument()
  })

  it('真实 options 为空时禁用选择且明确显示 empty', async () => {
    render(<ScopeSelect loadOptions={async () => []} onValueChange={() => undefined} />)

    expect(await screen.findByText('当前真实为空，请先检查 Bot 与会话来源配置。')).toBeVisible()
    expect(screen.getByRole('combobox', { name: '作用域' })).toBeDisabled()
  })

  it('深链序列化 ref、locator 与服务端 scope query，不接受默认 scope', () => {
    const href = buildObjectDeepLink('/beliefs?view=active', {
      ref: 'signed:scope/version+token', kind: 'belief', locator: 7, scope_key: 'bot:yushu',
      scope_query: { bot_id: 'bot-yushu', session_id: 'qq:group:42', visibility: 'group' }, version: 3,
    }, 'evidence', 'trace-7')
    expect(href).toContain('ref=signed%3Ascope%2Fversion%2Btoken')
    expect(href).toContain('object_id=7')
    expect(href).toContain('bot_id=bot-yushu')
    expect(href).toContain('session_id=qq%3Agroup%3A42')
    expect(href).toContain('visibility=group')
    expect(href).toContain('trace_id=trace-7')
    expect(new URLSearchParams(href.split('?')[1]).has('id')).toBe(false)

    render(
      <MemoryRouter>
        <ObjectDeepLink to="/beliefs" objectRef={{ ref: 'opaque-ref' }}>打开信念</ObjectDeepLink>
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: '打开信念' })).toHaveAttribute('href', '/beliefs?ref=opaque-ref')
  })

  it('scope mismatch 显示可恢复 not-found 而不是生成链接', () => {
    render(
      <MemoryRouter>
        <ObjectDeepLink to="/beliefs" objectRef={{ ref: 'opaque-ref' }} state="scope-mismatch" />
      </MemoryRouter>,
    )
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('不会使用裸 ID 或默认 Bot定位'.replace('Bot定位', 'Bot 定位'))
  })
})

describe('QueryState', () => {
  it('区分真实 empty、error 与 unknown', async () => {
    const { rerender } = render(<QueryState status="empty" />)
    expect(screen.getByText('当前真实为空')).toBeVisible()

    rerender(<QueryState status="error" error={new Error('API unavailable')} onRetry={() => undefined} />)
    expect(screen.getByRole('alert')).toHaveTextContent('API unavailable')
    expect(screen.getByRole('button', { name: '重试' })).toBeVisible()

    rerender(<QueryState status="unknown" />)
    await waitFor(() => expect(screen.getByText('状态未知')).toBeVisible())
    expect(screen.getByText(/不等同于空数据或成功/)).toBeVisible()
  })
})
