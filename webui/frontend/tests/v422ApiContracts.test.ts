import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const apiDir = resolve(import.meta.dirname, '../src/api')
const beliefsSource = readFileSync(resolve(apiDir, 'beliefs.ts'), 'utf8')
const jargonSource = readFileSync(resolve(apiDir, 'jargon.ts'), 'utf8')

test('Scoped Beliefs API uses semantic types, explicit scope and nested pagination', () => {
  assert.match(beliefsSource, /export type BeliefType = 'self_identity' \| 'person_judgment' \| 'world_view' \| 'preference'/)
  assert.match(beliefsSource, /limit: 25 \| 50 \| 100/)
  assert.match(beliefsSource, /offset: number/)
  assert.match(beliefsSource, /PageResponse<BeliefItem>/)
  assert.match(beliefsSource, /scopeEnvelope/)
  assert.doesNotMatch(beliefsSource, /\.set\('page'/)
  assert.doesNotMatch(beliefsSource, /\.set\('size'/)
})

test('Beliefs mutations use the shared operation/revision envelope', () => {
  assert.match(beliefsSource, /operation: \{ kind: string; status: string; id\?: string \}/)
  assert.match(beliefsSource, /revision: number \| string \| null/)
  assert.match(beliefsSource, /body: JSON\.stringify\(\{ scope: scopeEnvelope\(scope\) \}\)/)
})

test('Scoped Jargon API uses explicit scope and canonical review/catalog routes', () => {
  assert.match(jargonSource, /limit: 25 \| 50 \| 100/)
  assert.match(jargonSource, /offset: number/)
  assert.match(jargonSource, /PageResponse<JargonItem>/)
  assert.match(jargonSource, /\/api\/jargon\/\$\{id\}\/review\//)
  assert.match(jargonSource, /\/api\/jargon\/holyman/)
  assert.doesNotMatch(jargonSource, /\/api\/jargon\/batch-delete/)
  assert.doesNotMatch(jargonSource, /\/api\/jargon\/toggle_global/)
})

test('Jargon mutations expose evidence and operation state instead of legacy count fields', () => {
  assert.match(jargonSource, /anchors: EvidenceRef\[\]/)
  assert.match(jargonSource, /object_ref: ObjectRefDescriptor \| null/)
  assert.match(jargonSource, /operation: \{ status: string \}/)
})
