import { expect, test } from './fixtures'

test('认证与临时 SQLite API fixture 可启动规范 Dashboard', async ({ authenticatedPage, apiFixture }) => {
  await authenticatedPage.goto('./#/dashboard')

  await expect(authenticatedPage.getByText('系统状态正常', { exact: true })).toBeVisible()
  await expect(authenticatedPage.getByText('当前未检测到无标签记忆、待审风格样本或运行时错误。', { exact: true })).toBeVisible()
  expect(apiFixture.databasePath).toContain('api-fixture.sqlite3')
})

test('恢复后的正式页面均可从应用壳进入且不会崩溃', async ({ authenticatedPage }) => {
  const pageErrors: string[] = []
  authenticatedPage.on('pageerror', (error) => pageErrors.push(error.message))
  const routes = [
    ['/memories', '记忆'],
    ['/import', '导入'],
    ['/maintenance', '维护任务'],
    ['/observatory', '注入观测台'],
    ['/channels', '通道配置'],
    ['/learning', '学习过程'],
    ['/beliefs', '信念'],
    ['/jargon', '本地表达'],
    ['/soul', 'Soul 状态'],
    ['/knowledge/book-lore', 'BookLore'],
    ['/knowledge/style-examples', '风格样例'],
    ['/knowledge/facts', '事实'],
    ['/people', '人物与关系'],
    ['/diagnostics/indexes', '索引诊断'],
    ['/compatibility', '生态兼容'],
    ['/settings', '系统配置'],
  ] as const

  for (const [path, title] of routes) {
    await authenticatedPage.goto(`./#${path}`)
    await authenticatedPage.waitForTimeout(50)
    expect(pageErrors, `${path} 不应触发页面运行时异常`).toEqual([])
    await expect(authenticatedPage.getByRole('main').getByText(title, { exact: true }).first()).toBeVisible()
    await expect(authenticatedPage.getByText('页面不存在', { exact: true })).toHaveCount(0)
  }
})
