import test from 'node:test'
import assert from 'node:assert/strict'

import {
  filterHolymanCandidates,
  filterHolymanEvidence,
  filterHolymanPhrases,
  getHolymanCategories,
  getSelectedCandidateWords,
} from '../src/pages/jargon/holymanFilters.ts'

const phrases = [
  {
    word: 'v我50',
    meaning: '疯狂星期四索要 50 元',
    category: 'kfc',
    category_label: '疯狂星期四',
    is_activated: true,
  },
  {
    word: '叠甲',
    meaning: '提前声明防御立场',
    category: 'defense',
    category_label: '表达策略',
    is_activated: false,
  },
  {
    word: '不是哥们',
    meaning: '表达质疑或反讽',
    custom_meaning: '质疑开场',
    category: 'reaction',
    category_label: '反应词',
    is_activated: false,
  },
]

const candidates = [
  { id: 1, word: '外层词', reason: 'quote phrase', source: '神言.txt', status: 'pending_review' },
  { id: 2, word: '已通过词', reason: 'manual', source: 'db', status: 'approved' },
  { id: 3, word: '拒绝词', reason: 'ngram fragment', source: '神言.txt', status: 'rejected' },
]

test('filterHolymanPhrases searches word meaning custom meaning and category labels', () => {
  assert.deepEqual(filterHolymanPhrases(phrases, { search: '星期四', status: 'all', category: 'all' }).map((item) => item.word), ['v我50'])
  assert.deepEqual(filterHolymanPhrases(phrases, { search: '质疑', status: 'all', category: 'all' }).map((item) => item.word), ['不是哥们'])
  assert.deepEqual(filterHolymanPhrases(phrases, { search: '表达策略', status: 'all', category: 'all' }).map((item) => item.word), ['叠甲'])
})

test('filterHolymanPhrases applies activation status and category filters together', () => {
  assert.deepEqual(filterHolymanPhrases(phrases, { search: '', status: 'active', category: 'all' }).map((item) => item.word), ['v我50'])
  assert.deepEqual(filterHolymanPhrases(phrases, { search: '', status: 'inactive', category: 'defense' }).map((item) => item.word), ['叠甲'])
  assert.deepEqual(filterHolymanPhrases(phrases, { search: '反讽', status: 'inactive', category: 'reaction' }).map((item) => item.word), ['不是哥们'])
})

test('getHolymanCategories prefers provided categories and falls back to phrase aggregation', () => {
  assert.deepEqual(getHolymanCategories(phrases, [{ id: 'kfc', label: '疯狂星期四', count: 1 }]), [{ id: 'kfc', label: '疯狂星期四', count: 1 }])
  assert.deepEqual(getHolymanCategories(phrases, []), [
    { id: 'defense', label: '表达策略', count: 1 },
    { id: 'kfc', label: '疯狂星期四', count: 1 },
    { id: 'reaction', label: '反应词', count: 1 },
  ])
})

test('filterHolymanCandidates searches word reason source and status then filters status aliases', () => {
  assert.deepEqual(filterHolymanCandidates(candidates, { search: '神言', status: 'all' }).map((item) => item.word), ['外层词', '拒绝词'])
  assert.deepEqual(filterHolymanCandidates(candidates, { search: '', status: 'pending' }).map((item) => item.word), ['外层词'])
  assert.deepEqual(filterHolymanCandidates(candidates, { search: '', status: 'approved' }).map((item) => item.word), ['已通过词'])
  assert.deepEqual(filterHolymanCandidates(candidates, { search: 'ngram', status: 'rejected' }).map((item) => item.word), ['拒绝词'])
})

test('getSelectedCandidateWords maps selected ids to candidate words including string ids', () => {
  assert.deepEqual(getSelectedCandidateWords(candidates, [1, '3']), ['外层词', '拒绝词'])
})

test('filterHolymanEvidence searches title summary source tags text category and linked terms', () => {
  const concepts = [
    { title: '真诚是弱点', summary: '用反串包裹表达', source: 'values.md', tags: ['values', 'persona'] },
    { title: '复制粘贴文化', summary: '长文本模因', source: 'internet.md', tags: ['copypasta'] },
  ]
  const examples = [
    { text: '你说得对，但是', category: 'copypasta', source: 'quotes.md', linked_terms: ['原神传教'] },
  ]

  assert.deepEqual(filterHolymanEvidence(concepts, '反串').map((item) => item.title), ['真诚是弱点'])
  assert.deepEqual(filterHolymanEvidence(concepts, 'copypasta').map((item) => item.title), ['复制粘贴文化'])
  assert.deepEqual(filterHolymanEvidence(examples, '原神').map((item) => item.text), ['你说得对，但是'])
}
)
