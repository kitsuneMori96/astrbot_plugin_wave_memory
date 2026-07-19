import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { setViewport } from '@/test/setup'
import { MemoriesPage } from './MemoriesPage'

const api = vi.hoisted(() => ({
  list: vi.fn(),
  senders: vi.fn(),
  scopes: vi.fn(),
}))

vi.mock('@/api/memories', () => ({
  listMemories: api.list,
  listSenders: api.senders,
  getMemoryDetail: vi.fn(),
  getSimilarMemories: vi.fn(),
  updateMemory: vi.fn(),
  deleteMemory: vi.fn(),
  getMemoryTagState: vi.fn(),
  correctMemoryTags: vi.fn(),
  undoMemoryTagCorrection: vi.fn(),
  batchDeleteMemories: vi.fn(),
  memoryBatchStreamUrl: vi.fn(() => '/api/memories/batch/re-embed'),
  reEmbedMemory: vi.fn(),
  runPostStream: vi.fn(),
}))
vi.mock('@/api/options', () => ({
  getScopeOptions: api.scopes,
  scopeOptionsFor: (_payload: unknown, kinds: string[]) => kinds.includes('bot')
    ? [{ value: 'bot-real', label: '真实 Bot', kind: 'bot', description: 'Bot ID bot-real' }]
    : [{ value: 'qq:group:42', label: '群 42', kind: 'session', description: 'bot-real · group · runtime' }],
}))
vi.mock('@/components/tag/TagExtractionConfigPanel', () => ({ TagExtractionConfigPanel: () => <div>标签提取配置</div> }))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), info: vi.fn(), success: vi.fn(), message: vi.fn() } }))

const page = { total: 10, total_status: 'exact', reason_code: null, limit: 25, offset: 0, page: 1, page_count: 1, has_more: false }

beforeEach(() => {
  api.scopes.mockResolvedValue({ bots: [], sessions: [], channels: [], generated_at: 1, source: { health: 'healthy', reason_code: null } })
  api.list.mockResolvedValue({ items: [], page })
  api.senders.mockResolvedValue({ senders: [], source: { status: 'available', reason_code: null } })
})

describe('MemoriesPage 筛选草稿', () => {
  it('来源选择不会逐项请求，提交时带入筛选并重置 offset', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={['/memories?bot_id=bot-real&session_id=qq%3Agroup%3A42&visibility=group&offset=25']}><MemoriesPage /></MemoryRouter>)

    await waitFor(() => expect(api.list).toHaveBeenCalledTimes(1))
    const sourceSelect = screen.getAllByRole('combobox')[2]
    await user.click(sourceSelect)
    await user.click(screen.getByRole('option', { name: 'live' }))
    expect(api.list).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: '搜索' }))
    await waitFor(() => expect(api.list).toHaveBeenCalledTimes(2))
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ source: 'live', offset: 0 }))
  })

  it('桌面保留记忆表格，窄屏显示完整卡片与详情入口', async () => {
    api.list.mockResolvedValue({ items: [{ ref: 'memory:1', id: 1, content: '移动端完整记忆正文', sender_name: 'Alice', source: 'live', tags: [{ name: '重要', type: 'topic' }], has_vector: true, timestamp: 1 }], page })
    const desktop = render(<MemoryRouter initialEntries={['/memories?bot_id=bot-real&session_id=qq%3Agroup%3A42&visibility=group']}><MemoriesPage /></MemoryRouter>)
    await waitFor(() => expect(desktop.container.querySelector('[data-responsive-table="table"] table')).toBeInTheDocument())
    desktop.unmount()

    setViewport(390)
    const mobile = render(<MemoryRouter initialEntries={['/memories?bot_id=bot-real&session_id=qq%3Agroup%3A42&visibility=group']}><MemoriesPage /></MemoryRouter>)
    expect(await screen.findByText('移动端完整记忆正文')).toBeVisible()
    await waitFor(() => expect(mobile.container.querySelector('[data-responsive-table="cards"]')).toBeInTheDocument())
    expect(mobile.container.querySelector('[data-responsive-table="cards"] table')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '打开详情' })).toBeVisible()
    expect(mobile.container.querySelector('[data-responsive-table="cards"]')).toHaveTextContent('有向量')
  })
})
