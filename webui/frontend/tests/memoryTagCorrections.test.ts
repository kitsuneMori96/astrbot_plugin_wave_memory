import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const apiSource = readFileSync(new URL('../src/api/memories.ts', import.meta.url), 'utf8')
const pageSource = readFileSync(new URL('../src/pages/memories/MemoriesPage.tsx', import.meta.url), 'utf8')

test('记忆 Tag 校准 API 只使用 scoped correction 与 correction ObjectRef 撤销', () => {
  assert.match(apiSource, /\/tags\/correction'/)
  assert.match(apiSource, /\/tags\/correction\/undo'/)
  assert.match(apiSource, /correction_ref: correctionRef/)
  assert.doesNotMatch(apiSource, /tag_name: tagName/)
  assert.doesNotMatch(apiSource, /\/tags\/\$\{encodeURIComponent/)
})

test('记忆详情区分 automatic effective manual 并强制填写理由', () => {
  assert.match(pageSource, /自动基线/)
  assert.match(pageSource, /当前 effective/)
  assert.match(pageSource, /人工校准生效中/)
  assert.match(pageSource, /请先填写校准理由/)
  assert.match(pageSource, /请先填写撤销理由/)
  assert.match(pageSource, /tagState\.manual\.ref/)
  assert.match(pageSource, /name="memory-tag-reason"/)
  assert.match(pageSource, /aria-label=\{`人工排除 Tag/)
})

test('Tag mutation 回读新 Memory ObjectRef 并更新 URL 深链', () => {
  assert.match(pageSource, /resolvedRef\.current = result\.item\.memory\.ref/)
  assert.match(pageSource, /next\.set\('ref', result\.item!\.memory\.ref\)/)
  assert.match(pageSource, /setDetail\(\{ \.\.\.detail, \.\.\.result\.item\.memory \}\)/)
})
