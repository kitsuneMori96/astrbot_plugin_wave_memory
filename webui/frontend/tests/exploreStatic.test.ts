import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const staticDir = resolve(import.meta.dirname, '../../static')
const exploreHtml = readFileSync(resolve(staticDir, 'explore.html'), 'utf8')
const kgScript = readFileSync(resolve(staticDir, 'kg.js'), 'utf8')

test('Explore embed mode hides duplicate links, offsets the topbar and keeps the standalone Scope gate', () => {
  assert.match(exploreHtml, /get\('embed'\) === '1'/)
  assert.match(exploreHtml, /body\.embed-mode #explore-return-link/)
  assert.match(exploreHtml, /body\.embed-mode \.topbar/)
  assert.match(exploreHtml, /id="scope-gate"/)
  assert.match(exploreHtml, /href="\/static\/app\/#\/knowledge\/facts"/)
  assert.match(exploreHtml, /href="\/static\/app\/#\/maintenance"/)
})

test('Explore provides a Fact/Tag relation editor and a separate delete confirmation layer', () => {
  assert.match(exploreHtml, /id="knowledge-editor-dialog"/)
  assert.match(exploreHtml, /id="knowledge-delete-confirm"/)
  assert.match(exploreHtml, /id="fact-editor-subject"/)
  assert.match(exploreHtml, /id="fact-editor-predicate"/)
  assert.match(exploreHtml, /id="fact-editor-object"/)
  assert.match(exploreHtml, /id="relation-editor-source" readonly/)
  assert.match(exploreHtml, /id="relation-editor-target" readonly/)
  assert.doesNotMatch(kgScript, /window\.confirm/)
})

test('Knowledge mutations use only command POST bodies with ObjectRef, revision, patch and idempotency', () => {
  assert.match(kgScript, /const resource = kind === 'fact' \? 'facts' : 'tag-relations'/)
  assert.match(kgScript, /`\/api\/kg\/commands\/\$\{resource\}\/\$\{action\}`/)
  assert.match(kgScript, /runKnowledgeCommand\('update', currentKnowledgePatch\(\)\)/)
  assert.match(kgScript, /runKnowledgeCommand\('delete', \{\}\)/)
  assert.match(kgScript, /knowledgeCommandIdempotencyKey\(action, item, patch\)/)
  assert.match(kgScript, /object_ref: item\.object_ref/)
  assert.match(kgScript, /revision: item\.revision/)
  assert.match(kgScript, /patch,/)
  assert.match(kgScript, /idempotency_key: idempotencyKey/)
  assert.match(kgScript, /if \(response\.status === 409\)/)
  assert.doesNotMatch(kgScript, /editable:false|read_only:true/)
  assert.doesNotMatch(kgScript, /fetch\([^\n]*(?:add-fact|\/api\/kg\/(?:facts|tag-relations)\/\$\{)/)
})
