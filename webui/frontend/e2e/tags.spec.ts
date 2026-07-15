import { expect, test } from './fixtures'

test('Tag 总览在桌面展示真实运行状态和只读目录', async ({ authenticatedPage: page }) => {
  await page.goto('#/tags')

  await expect(page.getByRole('heading', { name: 'Tag 浪潮总览' })).toBeVisible()
  await expect(page.getByText('语义 RAG')).toBeVisible()
  await expect(page.getByText('42 个向量 · generation 7')).toBeVisible()
  await expect(page.getByRole('cell', { name: '共同记忆' })).toBeVisible()
  await expect(page.getByRole('button', { name: /删除|重命名|改类型/ })).toHaveCount(0)
})

test('Tag 总览在窄屏使用卡片而不是裁切表格', async ({ authenticatedPage: page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('#/tags')

  await expect(page.locator('[data-responsive-table="cards"]')).toBeVisible()
  await expect(page.locator('[data-responsive-table="table"]')).toHaveCount(0)
  await expect(page.getByText('共同记忆')).toBeVisible()
  await expect(page.getByText('置信度', { exact: true })).toBeVisible()
})
