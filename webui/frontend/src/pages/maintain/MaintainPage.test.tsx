import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MaintainPage } from '@/pages/maintain/MaintainPage'

const api = vi.hoisted(() => ({
  system: vi.fn(),
  quality: vi.fn(),
  suggestions: vi.fn(),
  resolveBatch: vi.fn(),
}))

vi.mock('@/api/system', () => ({ getSystemStatus: api.system }))
vi.mock('@/api/tags', () => ({
  getTagQuality: api.quality,
  getAuditSuggestions: api.suggestions,
  resolveAuditBatch: api.resolveBatch,
  resolveAuditSuggestion: vi.fn(),
}))
vi.mock('@/api/maintenance', () => ({
  cancelMaintenanceJob: vi.fn(),
  getMaintenanceLogs: vi.fn(),
  startTagAudit: vi.fn(),
  startTagBackfill: vi.fn(),
  waitForMaintenanceJob: vi.fn(),
}))
vi.mock('@/components/tag/TagExtractionConfigPanel', () => ({ TagExtractionConfigPanel: () => <div>标签提取配置</div> }))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() } }))

beforeEach(() => {
  api.system.mockResolvedValue({ memories: { total: 10, with_tags: 5 } })
  api.quality.mockResolvedValue({ total_tags: 3, total_memories: 10, tagged_memories: 5, untagged_memories: 5, coverage: 0.5 })
  api.suggestions.mockResolvedValue({
    suggestions: [{ id: 's-1', action: 'delete', reason: '低质量标签', status: 'pending', created_at: 1, source_tag_name: '噪声' }],
    counts: { pending: 1 },
  })
  api.resolveBatch.mockResolvedValue({ processed: 1, results: [] })
})

describe('MaintainPage 全部拒绝确认', () => {
  it('取消不调用批量 API，确认后才提交', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><MaintainPage /></MemoryRouter>)

    await user.click(await screen.findByRole('tab', { name: '质量审计与建议审核' }))
    await user.click(await screen.findByRole('button', { name: '全部拒绝' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('服务端批量写入 API')

    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(api.resolveBatch).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '全部拒绝' }))
    await user.click(screen.getByRole('button', { name: '确认全部拒绝' }))
    await waitFor(() => expect(api.resolveBatch).toHaveBeenCalledWith(['s-1'], 'reject'))
  })
})
