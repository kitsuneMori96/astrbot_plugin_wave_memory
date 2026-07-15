import { describe, expect, it, vi } from 'vitest'

import { ApiRequestCancelledError, fetchJson } from './client'

describe('fetchJson 取消语义', () => {
  it('调用方 signal 已取消时，不发起 fetch 并同步传播取消状态', async () => {
    const controller = new AbortController()
    controller.abort()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchJson('/api/test', { signal: controller.signal })).rejects.toBeInstanceOf(ApiRequestCancelledError)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
