import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { TagsPage } from './TagsPage'

const getTags = vi.fn()
const getTagQuality = vi.fn()
let isMobile = false

vi.mock('@/api/tags', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/tags')>(),
  getTags: (...args: unknown[]) => getTags(...args),
  getTagQuality: (...args: unknown[]) => getTagQuality(...args),
}))

vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => isMobile }))

describe('TagsPage', () => {
  beforeEach(() => {
    isMobile = false
    getTags.mockReset().mockResolvedValue({
      items: [{ id: 1, name: '共同记忆', type: 'topic', frequency: 12, confidence: 0.86 }],
      total: 1,
      available_types: ['person', 'topic'],
      legacy: true,
      readonly: true,
      capabilities: { mutation: { available: false, reason_code: 'legacy_mutation_disabled' } },
    })
    getTagQuality.mockReset().mockResolvedValue({
      total_tags: 1,
      total_memories: 10,
      tagged_memories: 8,
      untagged_memories: 2,
      extractable_untagged_memories: 1,
      skipped_short_untagged_memories: 1,
      orphan_memory_tag_refs: 0,
      coverage: 0.8,
      runtime: {
        capabilities: {
          extract: { available: true, reason_code: null },
          mutation: { available: false, reason_code: 'legacy_mutation_disabled' },
        },
        index: { available: true, health: 'ready', reason_code: null, count: 42, generation: 7, db_watermark: 123 },
        rag: { mode: 'semantic', semantic_available: true, fallback_reason: null, provider_configured: true, reference_refresh_interval: 200 },
      },
    })
  })

  it('展示真实质量指标与只读 Tag 目录，不暴露 legacy 写操作', async () => {
    render(<TagsPage />)

    expect(await screen.findByText('共同记忆')).toBeVisible()
    expect(screen.getByText('80%')).toBeVisible()
    expect(screen.getAllByText('只读').length).toBeGreaterThan(0)
    expect(screen.getByText('语义 RAG')).toBeVisible()
    expect(screen.getByText('42 个向量 · generation 7')).toBeVisible()
    expect(screen.getByRole('option', { name: 'person' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /删除|重命名|改类型/ })).not.toBeInTheDocument()
    expect(getTags).toHaveBeenCalledWith(expect.objectContaining({ limit: 25, offset: 0, sort: 'frequency' }))
  })

  it('窄屏使用完整语义卡片而不是裁切桌面表格', async () => {
    isMobile = true
    const { container } = render(<TagsPage />)

    expect(await screen.findByText('共同记忆')).toBeVisible()
    expect(container.querySelector('[data-responsive-table="cards"]')).toBeInTheDocument()
    expect(container.querySelector('[data-responsive-table="table"]')).not.toBeInTheDocument()
    expect(screen.getByText('置信度')).toBeVisible()
  })
})
