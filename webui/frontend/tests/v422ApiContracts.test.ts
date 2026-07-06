import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const apiDir = resolve(import.meta.dirname, '../src/api')
const beliefsSource = readFileSync(resolve(apiDir, 'beliefs.ts'), 'utf8')
const jargonSource = readFileSync(resolve(apiDir, 'jargon.ts'), 'utf8')

test('v4.2.2 Beliefs API contract uses limit/offset pagination and semantic belief types', () => {
  assert.match(beliefsSource, /type\?:\s*'self_identity'\s*\|\s*'person_judgment'\s*\|\s*'world_view'\s*\|\s*'preference'/)
  assert.match(beliefsSource, /\.set\('limit'/)
  assert.match(beliefsSource, /\.set\('offset'/)
  assert.doesNotMatch(beliefsSource, /\.set\('page'/)
  assert.doesNotMatch(beliefsSource, /\.set\('size'/)
})

test('v4.2.2 Beliefs batch helpers normalize backend count fields', () => {
  assert.match(beliefsSource, /approved_count/)
  assert.match(beliefsSource, /deleted_count/)
  assert.match(beliefsSource, /archived_count/)
})

test('v4.2.2 Jargon API contract uses backend route names and limit/offset pagination', () => {
  assert.match(jargonSource, /\.set\('limit'/)
  assert.match(jargonSource, /\.set\('offset'/)
  assert.doesNotMatch(jargonSource, /\.set\('page'/)
  assert.doesNotMatch(jargonSource, /\.set\('size'/)
  assert.match(jargonSource, /\/api\/jargon\/\$\{id\}\/context/)
  assert.match(jargonSource, /\/api\/jargon\/\$\{id\}\/toggle_global/)
  assert.match(jargonSource, /\/api\/jargon\/batch-delete/)
  assert.match(jargonSource, /\/api\/jargon\/batch-review/)
  assert.doesNotMatch(jargonSource, /\/api\/jargon\/\$\{id\}\/evidence/)
  assert.doesNotMatch(jargonSource, /\/api\/jargon\/\$\{id\}\/toggle-global/)
  assert.doesNotMatch(jargonSource, /\/api\/jargon\/batch\/delete/)
  assert.doesNotMatch(jargonSource, /\/api\/jargon\/batch\/review/)
})

test('v4.2.2 Jargon batch helpers normalize backend count fields', () => {
  assert.match(jargonSource, /reviewed_count/)
  assert.match(jargonSource, /deleted_count/)
})
