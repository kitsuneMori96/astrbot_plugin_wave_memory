import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { setViewport } from '@/test/setup'
import { LearningCenterPage } from './LearningCenterPage'

const api = vi.hoisted(() => ({
  sources: vi.fn(), jobs: vi.fn(), candidates: vi.fn(), fewshot: vi.fn(), experiences: vi.fn(), promotions: vi.fn(),
  scopes: vi.fn(), detail: vi.fn(), dedicated: vi.fn(), review: vi.fn(), run: vi.fn(), retry: vi.fn(),
}))

vi.mock('@/api/learningCenter', () => ({
  listLearningSources: api.sources,
  listLearningJobs: api.jobs,
  listLearningCandidates: api.candidates,
  getLearningFewShot: api.fewshot,
  getLearningExperiences: api.experiences,
  listLearningPromotions: api.promotions,
  getLearningCandidate: api.detail,
  getDedicatedReviewStatus: api.dedicated,
  reviewLearningCandidate: api.review,
  runLearningJob: api.run,
  retryLearningPromotion: api.retry,
}))
vi.mock('@/api/options', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/options')>()
  return { ...actual, getScopeOptions: api.scopes }
})
vi.mock('sonner', () => ({ toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() } }))

function payload(name: string) {
  return {
    items: [{ id: name === 'bot-a-result' ? 1 : 2, bot_id: name.startsWith('bot-a') ? 'bot-a' : 'bot-b', name, source_type: 'test' }],
    page: { total: 1, total_status: 'exact', reason_code: null, limit: 25, offset: 0, page: 1, page_count: 1, has_more: false },
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

beforeEach(() => {
  api.scopes.mockResolvedValue({
    bots: [{ db_id: 'bot-a', name: 'Bot A', status: 'active' }, { db_id: 'bot-b', name: 'Bot B', status: 'active' }],
    sessions: [], channels: [], generated_at: 1, source: { health: 'healthy', reason_code: null },
  })
  api.jobs.mockResolvedValue(payload('job'))
  api.candidates.mockResolvedValue(payload('candidate'))
  api.fewshot.mockResolvedValue(payload('fewshot'))
  api.experiences.mockResolvedValue({ worldview_internalization: [] })
  api.promotions.mockResolvedValue(payload('promotion'))
})

describe('LearningCenterPage 当前 tab 请求隔离', () => {
  it('非法 tab 规范到 sources，且不会重拉非当前重端点', async () => {
    api.sources.mockResolvedValue(payload('bot-a-result'))
    render(<MemoryRouter initialEntries={['/learning?bot_id=bot-a&tab=illegal']}><LearningCenterPage /></MemoryRouter>)

    expect(await screen.findByText('bot-a-result')).toBeVisible()
    expect(api.sources).toHaveBeenCalledTimes(1)
    expect(api.jobs).not.toHaveBeenCalled()
    expect(api.candidates).not.toHaveBeenCalled()
    expect(api.fewshot).not.toHaveBeenCalled()
    expect(api.experiences).not.toHaveBeenCalled()
    expect(api.promotions).not.toHaveBeenCalled()
  })

  it('切换 Bot 后旧响应不会覆盖当前 sources', async () => {
    const first = deferred<ReturnType<typeof payload>>()
    const second = deferred<ReturnType<typeof payload>>()
    api.sources.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={['/learning?bot_id=bot-a&tab=sources']}><LearningCenterPage /></MemoryRouter>)
    await waitFor(() => expect(api.sources).toHaveBeenCalledTimes(1))

    await user.click(await screen.findByRole('combobox', { name: 'Bot' }))
    await user.click(screen.getByRole('option', { name: /Bot B/ }))
    await waitFor(() => expect(api.sources).toHaveBeenCalledTimes(2))

    second.resolve(payload('bot-b-result'))
    expect(await screen.findByText('bot-b-result')).toBeVisible()
    first.resolve(payload('bot-a-result'))
    await waitFor(() => expect(screen.queryByText('bot-a-result')).not.toBeInTheDocument())
  })

  it('窄屏将来源列表改为可读卡片而不是保留横向宽表', async () => {
    setViewport(390)
    api.sources.mockResolvedValue(payload('mobile-source'))
    const view = render(<MemoryRouter initialEntries={['/learning?bot_id=bot-a&tab=sources']}><LearningCenterPage /></MemoryRouter>)

    expect(await screen.findByText('mobile-source')).toBeVisible()
    await waitFor(() => expect(view.container.querySelector('[data-responsive-table="cards"]')).toBeInTheDocument())
    expect(view.container.querySelector('[data-responsive-table="cards"] table')).not.toBeInTheDocument()
    expect(view.container.querySelector('[data-responsive-table="cards"]')).toHaveTextContent('test')
  })
})
