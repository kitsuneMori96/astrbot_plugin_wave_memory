import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { setViewport } from '@/test/setup'
import { IndexesPage } from '../../diagnostics/IndexesPage'
import { PeoplePage } from '../../people/PeoplePage'
import { BookLorePage } from '../BookLorePage'
import { FewShotPage } from '../FewShotPage'

const mocks = vi.hoisted(() => ({
  getBookLoreSummary: vi.fn(),
  getBookLoreItems: vi.fn(),
  getApprovedFewShot: vi.fn(),
  getScopedFacts: vi.fn(),
  getPeople: vi.fn(),
  getLegacyRelationships: vi.fn(),
  getIndexDiagnostics: vi.fn(),
  getScopeOptions: vi.fn(),
}))

vi.mock('@/api/knowledge', () => ({
  getBookLoreSummary: mocks.getBookLoreSummary,
  getBookLoreItems: mocks.getBookLoreItems,
  getApprovedFewShot: mocks.getApprovedFewShot,
  getScopedFacts: mocks.getScopedFacts,
}))
vi.mock('@/api/people', () => ({ getPeople: mocks.getPeople, getLegacyRelationships: mocks.getLegacyRelationships }))
vi.mock('@/api/diagnostics', () => ({ getIndexDiagnostics: mocks.getIndexDiagnostics }))
vi.mock('@/api/options', () => ({
  getScopeOptions: mocks.getScopeOptions,
  scopeOptionsFor: (_payload: unknown, kinds: string[]) => kinds.includes('bot')
    ? [{ value: 'bot-real', label: '真实 Bot', kind: 'bot', description: 'Bot ID bot-real' }]
    : [{ value: 'qq:group:42', label: '群 42', kind: 'session', description: 'bot-real · group · runtime' }],
}))

const page = { total: 1, total_status: 'exact', reason_code: null, limit: 25, offset: 0, page: 1, page_count: 1, has_more: false }

beforeEach(() => {
  setViewport(1280)
  mocks.getScopeOptions.mockResolvedValue({ bots: [], sessions: [], channels: [], generated_at: 1, source: { health: 'healthy', reason_code: null } })
  mocks.getLegacyRelationships.mockResolvedValue({ items: [], page: { ...page, total: 0 }, legacy: true, readonly: true, scope: null, scope_status: 'legacy_group_key', reason_code: 'bot_and_canonical_session_unavailable' })
})

describe('知识、人物与诊断页面关键约束', () => {
  it('BookLore 使用服务端摘要返回的真实 catalog scope', async () => {
    mocks.getBookLoreSummary.mockResolvedValue({
      scope: { catalog_id: 'configured-catalog', corpus_id: 'canon-corpus', version: 'v7' },
      counts: { entities: 0, communities: 0, relations: 0, notes: 0 },
      schema: { fingerprint: 'x', tables: {}, missing_tables: [] },
      read_only: true,
    })
    mocks.getBookLoreItems.mockResolvedValue({ items: [], page: { ...page, total: 0 }, scope: { catalog_id: 'configured-catalog', corpus_id: 'canon-corpus', version: 'v7' }, resource: 'entities', read_only: true })

    render(<MemoryRouter><BookLorePage /></MemoryRouter>)

    expect(await screen.findByText(/configured-catalog/)).toBeVisible()
    await waitFor(() => expect(mocks.getBookLoreItems).toHaveBeenCalledWith('entities', expect.objectContaining({ catalog_id: 'configured-catalog', corpus_id: 'canon-corpus', version: 'v7' })))
  })

  it('FewShot 未选择真实 Bot 时不请求跨 Bot 列表', async () => {
    render(<MemoryRouter><FewShotPage /></MemoryRouter>)

    expect(await screen.findByText(/禁止跨群或跨 Bot 汇总/)).toBeVisible()
    expect(mocks.getApprovedFewShot).not.toHaveBeenCalled()
  })

  it('People 对缺失 Affinity 明示不可用且不回填 50', async () => {
    mocks.getPeople.mockResolvedValue({
      items: [{
        user_id: 'user-1', group_id: '42', bot_id: 'bot-real', nickname: '小羽', display_name: '小羽', aliases: ['羽毛'], interaction_count: 8,
        scope: { user_id: 'user-1', group_id: '42', bot_id: 'bot-real' }, scope_key: 'user-1|42|bot-real', metadata: {}, registry_metadata: {}, person_registry: {},
        affinity: null, affinity_status: 'unavailable', affinity_reason_code: 'scoped_affinity_projection_unavailable',
      }],
      page,
    })

    render(<MemoryRouter initialEntries={['/people?bot_id=bot-real&session_id=qq%3Agroup%3A42']}><PeoplePage /></MemoryRouter>)

    expect(await screen.findByText('小羽')).toBeVisible()
    expect(screen.getAllByText('不可用').length).toBeGreaterThan(0)
    expect(screen.queryByText('50')).not.toBeInTheDocument()
  })

  it('Indexes 解释 drift 并只提供 Maintenance 安全入口', async () => {
    mocks.getIndexDiagnostics.mockResolvedValue({
      health: 'drift', source: 'wave_memory_readonly_diagnostics', checked_at: '2026-01-01T00:00:00Z',
      evidence: { read_only: true, probe_count: 1, health_counts: { drift: 1 } },
      checks: [{ name: 'fts', health: 'drift', source: 'sqlite:C:\\private\\memory.db', checked_at: '2026-01-01T00:00:00Z', evidence: { missing_rows: 2, path: 'C:\\private\\memory.db' } }],
    })

    render(<MemoryRouter><IndexesPage /></MemoryRouter>)

    expect(await screen.findByText(/关键词\/语义召回漏项/)).toBeVisible()
    expect(screen.getByRole('link', { name: '前往安全 Maintenance' })).toHaveAttribute('href', '/maintenance?source=diagnostics&panel=indexes')
    expect(screen.queryByRole('button', { name: /rebuild|重建/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/C:\\private/)).not.toBeInTheDocument()
  })
})
