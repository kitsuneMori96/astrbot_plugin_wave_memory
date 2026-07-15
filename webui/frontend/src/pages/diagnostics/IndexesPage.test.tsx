import { MemoryRouter } from 'react-router-dom'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { IndexesPage } from './IndexesPage'

const api = vi.hoisted(() => ({ diagnostics: vi.fn() }))
vi.mock('@/api/diagnostics', () => ({ getIndexDiagnostics: api.diagnostics }))
vi.mock('@/components/shared', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/shared')>()
  return { ...actual, ResponsiveDetail: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }
})

beforeEach(() => {
  api.diagnostics.mockResolvedValue({
    health: 'healthy',
    source: 'wave_memory_readonly_diagnostics',
    checked_at: 'not-a-date',
    evidence: { read_only: true, probe_count: 1, health_counts: { healthy: 1 } },
    checks: [{ name: 'fts', health: 'healthy', source: 'sqlite:fts', checked_at: 'not-a-date', evidence: { count: 2 } }],
  })
})

describe('IndexesPage 时间降级', () => {
  it('非法 checked_at 显示时间不可用，过滤摘要仍使用真实数据', async () => {
    render(<MemoryRouter><IndexesPage /></MemoryRouter>)

    expect(await screen.findByText('全文检索索引')).toBeVisible()
    expect(screen.getAllByText('1', { selector: '.text-lg' }).length).toBe(2)
    expect(await screen.findByText(/检查时间：时间不可用/)).toBeVisible()
    expect(screen.getByRole('textbox', { name: '搜索诊断项' })).toBeVisible()
    expect(screen.getByRole('link', { name: '进入 Maintenance 修复任务' })).toHaveAttribute('href', '/maintenance?source=diagnostics&panel=indexes')
    expect(screen.queryByText('检查与安全处理')).not.toBeInTheDocument()
    expect(screen.queryByText(/Invalid Date/)).not.toBeInTheDocument()
  })

  it('诊断失败时保持未知状态，不显示无漂移结论或伪零摘要', async () => {
    api.diagnostics.mockRejectedValue(new Error('probe offline'))

    render(<MemoryRouter><IndexesPage /></MemoryRouter>)

    expect(await screen.findByText('索引健康状态未知')).toBeVisible()
    expect(screen.getByText('probe offline')).toBeVisible()
    expect(screen.queryByText('未发现阻断性索引漂移')).not.toBeInTheDocument()
    expect(screen.getAllByText('—', { selector: '.text-lg' })).toHaveLength(4)
  })
})
