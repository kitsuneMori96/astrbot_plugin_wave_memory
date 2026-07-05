export type HolymanPhraseStatusFilter = 'all' | 'active' | 'inactive'

export interface HolymanPhraseFilterOptions {
  search: string
  status: HolymanPhraseStatusFilter
  category: string
}

export interface HolymanCandidateFilterOptions {
  search: string
  status: string
}

export interface HolymanCategoryOption {
  id: string
  label: string
  count: number
}

export interface HolymanPhraseLike {
  word?: unknown
  meaning?: unknown
  custom_meaning?: unknown
  category?: unknown
  category_label?: unknown
  is_activated?: unknown
}

export interface HolymanCandidateLike {
  id?: unknown
  word?: unknown
  reason?: unknown
  source?: unknown
  status?: unknown
}

function text(value: unknown): string {
  if (Array.isArray(value)) return value.map(text).join(' ')
  if (value === null || value === undefined) return ''
  return String(value)
}

function normalized(value: unknown): string {
  return text(value).trim().toLowerCase()
}

function includesSearch(values: unknown[], search: string): boolean {
  const needle = normalized(search)
  if (!needle) return true
  return values.some((value) => normalized(value).includes(needle))
}

function candidateKey(candidate: HolymanCandidateLike): string {
  const raw = candidate.id ?? candidate.word ?? ''
  return text(raw)
}

function normalizeCandidateStatus(status: unknown): string {
  const value = normalized(status || 'pending')
  if (value === 'pending_review') return 'pending'
  if (value === 'confirmed') return 'approved'
  if (value === 'reject') return 'rejected'
  return value || 'pending'
}

export function filterHolymanPhrases<T extends HolymanPhraseLike>(
  items: T[],
  options: HolymanPhraseFilterOptions,
): T[] {
  const category = options.category || 'all'
  return items.filter((item) => {
    if (options.status === 'active' && item.is_activated !== true) return false
    if (options.status === 'inactive' && item.is_activated === true) return false
    if (category !== 'all' && text(item.category || 'unknown') !== category) return false
    return includesSearch([
      item.word,
      item.meaning,
      item.custom_meaning,
      item.category,
      item.category_label,
    ], options.search)
  })
}

export function getHolymanCategories(
  phrases: HolymanPhraseLike[],
  providedCategories: HolymanCategoryOption[] = [],
): HolymanCategoryOption[] {
  if (providedCategories.length > 0) return providedCategories

  const byId = new Map<string, HolymanCategoryOption>()
  for (const phrase of phrases) {
    const id = text(phrase.category || 'unknown') || 'unknown'
    const label = text(phrase.category_label || phrase.category || '未分类') || '未分类'
    const current = byId.get(id)
    if (current) {
      current.count += 1
    } else {
      byId.set(id, { id, label, count: 1 })
    }
  }
  return Array.from(byId.values()).sort((a, b) => a.id.localeCompare(b.id))
}

export function filterHolymanCandidates<T extends HolymanCandidateLike>(
  items: T[],
  options: HolymanCandidateFilterOptions,
): T[] {
  const status = normalized(options.status || 'all')
  return items.filter((item) => {
    const itemStatus = normalizeCandidateStatus(item.status)
    if (status !== 'all' && itemStatus !== status) return false
    return includesSearch([item.word, item.reason, item.source, item.status], options.search)
  })
}

export function getSelectedCandidateWords(
  candidates: HolymanCandidateLike[],
  selectedIds: Array<number | string>,
): string[] {
  const selected = new Set(selectedIds.map(String))
  return candidates
    .filter((candidate) => selected.has(candidateKey(candidate)))
    .map((candidate) => text(candidate.word).trim())
    .filter(Boolean)
}

export function filterHolymanEvidence<T extends Record<string, unknown>>(
  items: T[],
  search: string,
): T[] {
  return items.filter((item) => includesSearch([
    item.title,
    item.summary,
    item.source,
    item.tags,
    item.text,
    item.category,
    item.linked_terms,
    item.key,
    item.word,
    item.content,
    item.meaning,
  ], search))
}
