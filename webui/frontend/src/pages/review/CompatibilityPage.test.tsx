import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CompatibilityPage } from './CompatibilityPage'

const api = vi.hoisted(() => ({ status: vi.fn() }))
vi.mock('@/api/compatibility', () => ({ getCompatibilityStatus: api.status }))

beforeEach(() => {
  api.status.mockResolvedValue({
    probe: { status: 'not_detected', source: 'registry', checked_at: 'now', error: null, evidence: [] },
    duplicate_warnings: [],
    recommended_settings: [],
    runtime: { mode: 'standalone', source: 'config' },
  })
})

describe('CompatibilityPage 分区降级', () => {
  it('部分字段缺失只影响对应分区，空建议显示真实空态', async () => {
    render(<CompatibilityPage />)

    expect(await screen.findByText('本次探测未发现已知重复插件')).toBeVisible()
    expect(screen.getAllByText('当前分区不可用。').length).toBeGreaterThanOrEqual(3)
    expect(screen.getByText('当前没有建议。')).toBeVisible()
    expect(screen.getByRole('button', { name: '刷新兼容状态' })).toBeVisible()
    expect(screen.queryByText('兼容状态加载失败')).not.toBeInTheDocument()
  })
})
