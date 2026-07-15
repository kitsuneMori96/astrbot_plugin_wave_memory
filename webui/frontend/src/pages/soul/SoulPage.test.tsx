import { MemoryRouter } from 'react-router-dom'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SoulPage } from './SoulPage'

const api = vi.hoisted(() => ({ formal: vi.fn(), legacy: vi.fn(), scopes: vi.fn() }))
vi.mock('@/api/soul', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/soul')>()
  return { ...actual, getSoulState: api.formal, getLegacySoulSnapshot: api.legacy }
})
vi.mock('@/api/options', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/options')>()
  return { ...actual, getScopeOptions: api.scopes }
})
vi.mock('@/components/shared', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/shared')>()
  return { ...actual, EvidenceList: () => <div>证据列表</div>, ObjectDeepLink: ({ children }: { children: React.ReactNode }) => <span>{children}</span> }
})
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return { ...actual, Area: () => null, AreaChart: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>, Bar: () => null, BarChart: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>, CartesianGrid: () => null, XAxis: () => null, YAxis: () => null }
})

const page = { items: [], page: { total: 0, total_status: 'exact', reason_code: null, limit: 25, offset: 0, page: 1, page_count: 0, has_more: false } }

beforeEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })) })
  api.scopes.mockResolvedValue({
    bots: [{ db_id: 'bot-a', name: 'Bot A', status: 'active' }],
    sessions: [{ id: 'session-a', bot_id: 'bot-a', platform_id: 'qq', kind: 'group', conversation_id: '1', label: '群 1' }],
    channels: [], generated_at: 1, source: { health: 'healthy', reason_code: null },
  })
  api.formal.mockResolvedValue({
    source: { health: 'healthy', reason_code: null },
    mood: { value: '平静', state: 'known', components: null, policy_version: 'v1', revision: 1, evidence: [] },
    concerns: page,
    timeline: page,
    relationship: { affinity: 0.5, state: 'known', revision: 1, evidence: [], people_ref: null },
    capabilities: { mutate: { available: false, reason_code: 'readonly' }, runtime_refresh: { available: false, reason_code: 'unavailable' } },
    runtime_refresh: { status: 'unavailable', operation: null, reason_code: 'unavailable' },
  })
  api.legacy.mockRejectedValue(new Error('legacy offline'))
})

describe('SoulPage 正式与 Legacy 独立加载', () => {
  it('Legacy 失败不拖垮 formal scoped 数据，并在展开审计后提供独立重试', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={['/soul?bot_id=bot-a&session_id=session-a&visibility=group']}><SoulPage /></MemoryRouter>)

    expect(await screen.findByText('平静')).toBeVisible()
    const legacySummary = screen.getByText('Legacy 只读审计（非当前 Scope）')
    expect(legacySummary.closest('details')).not.toHaveAttribute('open')
    await user.click(legacySummary)
    expect(await screen.findByText('Legacy 只读投影不可用')).toBeVisible()
    expect(screen.getByText('legacy offline')).toBeVisible()
    expect(screen.getByText(/同一组 limit\/offset/)).toBeVisible()
    expect(screen.getByRole('button', { name: '重试' })).toBeVisible()
  })
})
