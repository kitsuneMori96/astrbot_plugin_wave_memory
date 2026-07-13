import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const srcDir = resolve(import.meta.dirname, '../src')
const apiSource = readFileSync(resolve(srcDir, 'api/learningCenter.ts'), 'utf8')
const pageSource = readFileSync(resolve(srcDir, 'pages/learning/LearningCenterPage.tsx'), 'utf8')
const routesSource = readFileSync(resolve(srcDir, 'app/routes.tsx'), 'utf8')
const sidebarSource = readFileSync(resolve(srcDir, 'components/layout/WaveSidebar.tsx'), 'utf8')
const traceSource = readFileSync(resolve(srcDir, 'pages/injection/TraceDetailSheet.tsx'), 'utf8')

test('学习中心 API client 覆盖六区域和安全变更端点', () => {
  for (const endpoint of ['/sources', '/jobs', '/candidates', '/promotions', '/few-shot', '/experiences', '/dedicated-review-status']) {
    assert.match(apiSource, new RegExp(`/api/learning-center${endpoint}`))
  }
  assert.match(apiSource, /Idempotency-Key/)
  assert.match(apiSource, /retryLearningPromotion/)
  assert.match(apiSource, /reviewLearningCandidate/)
  assert.match(apiSource, /bot_id/)
  assert.match(apiSource, /candidate_type/)
  assert.match(apiSource, /promotion_status/)
})

test('学习中心是候选唯一前端入口，旧 Agent 反馈页不可导航', () => {
  assert.match(routesSource, /path: '\/learning-center'/)
  assert.match(routesSource, /LearningCenterPage/)
  assert.match(sidebarSource, /'\/learning-center'/)
  assert.doesNotMatch(routesSource, /agent-feedback|AgentFeedbackPage|learning-objects|LearningObjectsPage/)
  assert.doesNotMatch(sidebarSource, /agent-feedback|learning-objects/)
  assert.match(traceSource, /to="\/learning-center"/)
  assert.doesNotMatch(traceSource, /to="\/agent-feedback"/)
})

test('页面保持类型隔离、证据字段和专属审核深链', () => {
  for (const label of ['来源', '任务', '候选', 'FewShot', '经历/内化', '晋升历史']) {
    assert.match(pageSource, new RegExp(label.replace('/', '\\/')))
  }
  assert.match(pageSource, /candidate_type === 'few_shot_style'/)
  assert.match(pageSource, /review_status === 'approved'/)
  assert.match(pageSource, /非书中真实经历/)
  for (const evidenceField of ['chapter_reference', 'original_quote', 'participants', 'knowledge_perspective']) {
    assert.match(pageSource, new RegExp(evidenceField))
  }
  assert.match(pageSource, /deep_link/)
  assert.match(pageSource, /retryable_failed/)
  assert.match(pageSource, /安全重试/)
})

test('页面成功提示只根据 API 返回状态渲染', () => {
  assert.match(pageSource, /promotion_status === 'succeeded'/)
  assert.match(pageSource, /promotion_status === 'running' \|\| item\?\.promotion_status === 'queued'/)
  assert.match(pageSource, /以 API 状态确认结果/)
})
