import { useState } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { usePaginationSearchParams } from '@/hooks/use-pagination-search-params'
import { PaginationControls } from '../PaginationControls'
import type { PageMetadata } from '../types'

function PaginationHarness() {
  const [offset, setOffset] = useState(0)
  const [limit, setLimit] = useState<25 | 50 | 100>(25)
  const page: PageMetadata = {
    total: 75,
    total_status: 'exact',
    reason_code: null,
    limit,
    offset,
    page: Math.floor(offset / limit) + 1,
    page_count: Math.ceil(75 / limit),
    has_more: offset + limit < 75,
  }
  return <PaginationControls page={page} onOffsetChange={setOffset} onLimitChange={setLimit} />
}

function HookHarness() {
  const pagination = usePaginationSearchParams()
  const location = useLocation()
  return (
    <>
      <output aria-label="当前查询">{location.search}</output>
      <button onClick={() => pagination.setFilters({ status: 'ok' })}>修改筛选</button>
    </>
  )
}

describe('PaginationControls', () => {
  it('翻页后恢复触发按钮焦点并通过 aria-live 宣告精确页数', async () => {
    const user = userEvent.setup()
    render(<PaginationHarness />)

    const next = screen.getByRole('button', { name: '下一页' })
    await user.click(next)

    expect(screen.getByText('第 2 页，共 3 页，共 75 条')).toHaveAttribute('aria-live', 'polite')
    expect(next).toHaveFocus()
  })

  it('筛选变化统一清除 URL offset 并保留合法页大小', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/?offset=50&limit=50&status=error']}>
        <HookHarness />
      </MemoryRouter>,
    )

    await user.click(screen.getByRole('button', { name: '修改筛选' }))
    const query = screen.getByLabelText('当前查询').textContent ?? ''
    expect(query).not.toContain('offset=')
    expect(query).toContain('limit=50')
    expect(query).toContain('status=ok')
  })
})
