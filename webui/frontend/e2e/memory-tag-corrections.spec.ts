import { expect, test } from './fixtures'

const memory = {
  id: 1,
  content: '项目决定采用 Tag 语义主干。',
  sender_id: 'user-alpha',
  sender_name: 'Alice',
  group_id: 'group-alpha',
  bot_id: 'bot-alpha',
  session_id: 'qq:group:group-alpha',
  visibility: 'group',
  source: 'live',
  timestamp: 1_700_000_000,
  importance: 1,
  access_count: 2,
  has_vector: true,
  version: 7,
  ref: 'memory-ref-v7',
  detail_url: '/api/memories/1?ref=memory-ref-v7&bot_id=bot-alpha&session_id=qq%3Agroup%3Agroup-alpha&visibility=group',
  mutation_url: '/api/memories/1?ref=memory-ref-v7&bot_id=bot-alpha&session_id=qq%3Agroup%3Agroup-alpha&visibility=group',
}

const automatic = [{ id: 11, name: '项目决策', tag_type: 'topic', source: 'automatic', position: 1, relevance: 0.91 }]

async function installMemoryRoutes(page: import('@playwright/test').Page) {
  await page.route(/^https?:\/\/[^/]+\/api\/memories(?:\?.*)?$/, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [memory], page: { total: 1, total_status: 'exact', reason_code: null, limit: 25, offset: 0, page: 1, page_count: 1, has_more: false } }) })
  })
  await page.route(/^https?:\/\/[^/]+\/api\/memories\/1(?:\?.*)?$/, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ item: memory, resolution: { state: 'ready' } }) })
  })
  await page.route(/^https?:\/\/[^/]+\/api\/memories\/1\/similar(?:\?.*)?$/, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) })
  })
  await page.route(/^https?:\/\/[^/]+\/api\/memories\/1\/tags(?:\?.*)?$/, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ item: { automatic, effective: automatic, manual: null } }) })
  })
}

test('桌面详情展示 Tag 分层并提交带理由的 scoped 校准', async ({ authenticatedPage: page }) => {
  await installMemoryRoutes(page)
  let requestBody: unknown
  await page.route(/^https?:\/\/[^/]+\/api\/memories\/1\/tags\/correction(?:\?.*)?$/, async (route) => {
    requestBody = route.request().postDataJSON()
    const nextMemory = { ...memory, version: 8, ref: 'memory-ref-v8', mutation_url: memory.mutation_url.replaceAll('v7', 'v8') }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, operation: { kind: 'memory.tags.correct', status: 'succeeded', id: 'operation-1' }, revision: 8, item: { memory: nextMemory, tags: { automatic, effective: [...automatic, { id: 12, name: '架构约束', tag_type: 'custom', source: 'manual' }], manual: { correction_id: 'correction-1', operation: 'add', requested_tags: ['架构约束'], before: ['项目决策'], tags: ['项目决策', '架构约束'], revision: 1, status: 'active', created_at: 100, reason: '补充核心架构语义', ref: 'correction-ref-1' } } } }) })
  })

  await page.goto('#/memories?bot_id=bot-alpha&session_id=qq%3Agroup%3Agroup-alpha&visibility=group')
  await page.getByRole('button', { name: '打开详情' }).click()
  await expect(page.getByText('自动基线')).toBeVisible()
  await expect(page.getByText('当前 effective')).toBeVisible()
  await page.getByLabel('校准理由').fill('补充核心架构语义')
  await page.getByLabel('人工纳入 Tag').fill('架构约束')
  await page.getByRole('button', { name: '人工纳入' }).click()

  await expect(page.getByText('人工校准生效中')).toBeVisible()
  expect(requestBody).toEqual({ operation: 'add', tags: ['架构约束'], reason: '补充核心架构语义' })
  await expect(page).toHaveURL(/ref=memory-ref-v8/)
})

test('390px 窄屏 Tag 校准表单纵向可用且没有水平溢出', async ({ authenticatedPage: page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installMemoryRoutes(page)
  await page.goto('#/memories?bot_id=bot-alpha&session_id=qq%3Agroup%3Agroup-alpha&visibility=group')
  await page.getByRole('button', { name: '打开详情' }).click()

  await expect(page.getByLabel('校准理由')).toBeVisible()
  await expect(page.getByLabel('人工纳入 Tag')).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
  expect(overflow).toBe(false)
})
