import { expect, test } from './fixtures'

const scope = { bot_id: 'bot-alpha', session_id: 'qq:group:g1', visibility: 'group' as const }
const tags = [
  { id: 1, name: '旧名称', type: 'topic', confidence: 0.9, metadata: {}, revision: 1, status: 'active', aliases: [], ref: 'tag-ref-1' },
  { id: 2, name: '新名称', type: 'topic', confidence: 0.9, metadata: {}, revision: 1, status: 'active', aliases: [], ref: 'tag-ref-2' },
]

test('scoped Tag 工作台先预检再批准，并在窄屏保持可用', async ({ authenticatedPage: page }) => {
  let suggestionCreated = false
  let previewRequested = false
  let resolveRequested = false
  await page.route(/^https?:\/\/[^/]+\/api\/options\/scopes$/, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ bots: [{ db_id: 'bot-alpha', name: 'Alpha' }], sessions: [{ id: 'qq:group:g1', bot_id: 'bot-alpha', kind: 'group', conversation_id: 'g1', label: '群 1' }], channels: [], generated_at: 1, source: { health: 'healthy', reason_code: null } }) })
  })
  await page.route(/^https?:\/\/[^/]+\/api\/tags\/governance\/catalog(?:\?.*)?$/, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: tags, page: { total: 2, total_status: 'exact', reason_code: null, limit: 200, offset: 0, page: 1, page_count: 1, has_more: false }, scope }) })
  })
  await page.route(/^https?:\/\/[^/]+\/api\/tags\/governance\/suggestions(?:\?.*)?$/, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const items = suggestionCreated ? [{ suggestion_id: 'suggestion-1', operation_id: 'operation-1', action: 'merge', tag_ids: [1, 2], tag_refs: ['tag-ref-1', 'tag-ref-2'], target_tag_id: 2, target_name: '规范名称', target_type: 'topic', aliases: [], reason: '同义合并', evidence: {}, status: 'pending', revision: 1, created_at: 1, expires_at: 9999999999, ref: 'suggestion-ref-1' }] : []
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items, page: { total: items.length, total_status: 'exact', reason_code: null, limit: 100, offset: 0, page: 1, page_count: items.length ? 1 : 0, has_more: false }, scope }) })
  })
  await page.route(/^https?:\/\/[^/]+\/api\/tags\/governance\/suggestions(?:\?.*)?$/, async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    suggestionCreated = true
    await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ ok: true, operation: { kind: 'tags.governance.suggestion.create', status: 'succeeded', id: 'operation-1' }, revision: 1, item: { suggestion_id: 'suggestion-1', ref: 'suggestion-ref-1', status: 'pending', impact: { memory_count: 1, relation_count: 1, removed_tags: 1 } } }) })
  })
  await page.route(/^https?:\/\/[^/]+\/api\/tags\/governance\/preview(?:\?.*)?$/, async (route) => {
    previewRequested = true
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, suggestion: { suggestion_id: 'suggestion-1', action: 'merge', revision: 1, ref: 'suggestion-ref-1' }, preview: { action: 'merge', tag_ids: [1, 2], target_tag_id: 2, target_name: '规范名称', target_type: 'topic', aliases: [], before: {}, after: {}, impact: { memory_count: 1, relation_count: 1, removed_tags: 1 } }, preflight_token: 'preflight-1', expires_at: 9999999999 }) })
  })
  await page.route(/^https?:\/\/[^/]+\/api\/tags\/governance\/suggestions\/resolve(?:\?.*)?$/, async (route) => {
    resolveRequested = true
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, operation: { kind: 'tags.governance.resolve', status: 'succeeded', id: 'operation-2' }, revision: 2, item: { suggestion_id: 'suggestion-1', status: 'approved', impact: { memory_count: 1, relation_count: 1, removed_tags: 1 } } }) })
  })

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('#/tags')
  await page.getByLabel('Bot').selectOption('bot-alpha')
  await page.getByLabel('群 / canonical session').selectOption('qq:group:g1')
  await expect(page.getByText('当前 Scope 的 Tag')).toBeVisible()
  const tagCheckboxes = page.locator('input[type="checkbox"]')
  await tagCheckboxes.nth(0).check()
  await tagCheckboxes.nth(1).check()
  await page.getByRole('radio', { name: '选择 新名称 为合并目标' }).check()
  await page.getByLabel('理由 / 证据说明').fill('同义合并')
  await page.getByRole('button', { name: '创建待审建议' }).click()
  await expect(page.getByText('1 条 pending suggestion')).toBeVisible()
  await page.getByRole('button', { name: '预检当前页' }).click()
  await expect(page.getByText('影响记忆：1')).toBeVisible()
  await page.getByLabel('理由 / 证据说明').fill('确认同义合并')
  await page.getByRole('button', { name: '批准并应用' }).click()
  expect(previewRequested).toBe(true)
  expect(resolveRequested).toBe(true)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
  expect(overflow).toBe(false)
})
