import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ImportPage } from './ImportPage'

const api = vi.hoisted(() => ({ sources: vi.fn(), job: vi.fn(), logs: vi.fn(), checkpoint: vi.fn(), wait: vi.fn() }))
vi.mock('@/api/import', () => ({
  getImportSources: api.sources,
  preflightImport: vi.fn(),
  startImport: vi.fn(),
  waitForImportJob: api.wait,
}))
vi.mock('@/api/maintenance', () => ({
  getMaintenanceJob: api.job,
  getMaintenanceLogs: api.logs,
  getMaintenanceCheckpoint: api.checkpoint,
}))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() } }))

beforeEach(() => {
  api.sources.mockResolvedValue({ sources: [{ id: 'source-a', name: '来源 A', count: 10, has_adapter: true, description: '可用来源' }] })
  api.job.mockRejectedValue(new Error('job not found'))
  api.logs.mockResolvedValue({ items: [] })
  api.checkpoint.mockResolvedValue({ job_id: 'missing', status: 'failed' })
})

describe('ImportPage 失效 job 深链', () => {
  it('停止永久 spinner，保留来源区并允许清除无效引用', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={['/import?job_id=missing']}><ImportPage /></MemoryRouter>)

    expect(await screen.findByText('1. 数据源与导入策略')).toBeVisible()
    expect(await screen.findByText('无法恢复 URL 中的导入任务')).toBeVisible()
    expect(screen.getByText('job not found')).toBeVisible()
    expect(screen.queryByText('正在通过 URL 中的 job_id 恢复导入任务...')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '清除无效任务引用' }))
    await waitFor(() => expect(screen.queryByText('无法恢复 URL 中的导入任务')).not.toBeInTheDocument())
    expect(screen.getByText('1. 数据源与导入策略')).toBeVisible()
  })

  it('日志失败时保留核心 job 与可用 checkpoint', async () => {
    const user = userEvent.setup()
    api.job.mockResolvedValue({ item: { run_id: 'done', request_id: 'req-done', status: 'succeeded', progress: { processed: 10, total: 10 } } })
    api.logs.mockRejectedValue(new Error('logs offline'))
    api.checkpoint.mockResolvedValue({ job_id: 'done', status: 'succeeded', checkpoint: { phase: 'done' } })

    render(<MemoryRouter initialEntries={['/import?job_id=done']}><ImportPage /></MemoryRouter>)

    expect(await screen.findByText('3. 外部记忆导入任务状态')).toBeVisible()
    expect(await screen.findByText('部分任务附属信息不可用')).toBeVisible()
    expect(screen.getByText(/日志：logs offline/)).toBeVisible()
    await user.click(screen.getByText('技术详情'))
    expect(screen.getByText(/checkpoint: done/)).toBeVisible()
    expect(screen.queryByText('无法恢复 URL 中的导入任务')).not.toBeInTheDocument()
  })
})
