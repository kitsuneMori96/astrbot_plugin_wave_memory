import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { test as base, expect, type Page } from '@playwright/test'

interface ApiFixture {
  databasePath: string
  get(pathname: string): unknown
}

interface FrontendFixtures {
  apiFixture: ApiFixture
  authenticatedPage: Page
}

export const test = base.extend<FrontendFixtures>({
  apiFixture: async ({ browserName: _browserName }, provide) => {
    const directory = await mkdtemp(path.join(tmpdir(), 'wavememory-frontend-'))
    const databasePath = path.join(directory, 'api-fixture.sqlite3')
    const database = new DatabaseSync(databasePath)
    database.exec('CREATE TABLE api_responses (pathname TEXT PRIMARY KEY, payload_json TEXT NOT NULL)')
    const insert = database.prepare('INSERT INTO api_responses(pathname, payload_json) VALUES (?, ?)')
    const responses: Record<string, unknown> = {
      '/api/auth/check': { requires_auth: true },
      '/api/system': { memories: { total: 0 }, coverage: { vector_pct: 0, tag_pct: 0 }, tags: { structured: 0 }, todos: { untagged_count: 0, pending_fewshot: 0, has_errors: false }, services_health: [] },
      '/api/errors': { errors: [], total: 0 },
      '/api/metrics/injection': { count: 0, series: [], ranking: [], range: '7d' },
      '/api/channels/config': { current: { channels: {} } },
    }
    Object.entries(responses).forEach(([pathname, payload]) => insert.run(pathname, JSON.stringify(payload)))

    await provide({
      databasePath,
      get(pathname) {
        const row = database.prepare('SELECT payload_json FROM api_responses WHERE pathname = ?').get(pathname) as { payload_json?: string } | undefined
        return row?.payload_json ? JSON.parse(row.payload_json) : {}
      },
    })

    database.close()
    await rm(directory, { recursive: true, force: true })
  },

  authenticatedPage: async ({ page, apiFixture }, provide) => {
    await page.addInitScript(() => localStorage.setItem('wavememory.webui.token', 'e2e-auth-token'))
    await page.route(/^https?:\/\/[^/]+\/api\//, async (route) => {
      const pathname = new URL(route.request().url()).pathname
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(apiFixture.get(pathname)) })
    })
    await provide(page)
  },
})

export { expect }
