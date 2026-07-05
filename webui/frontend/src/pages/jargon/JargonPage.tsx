import { useEffect, useState } from 'react'
import {
  AlertCircleIcon,
  CheckCircle2Icon,
  Loader2Icon,
  RefreshCwIcon,
  SearchIcon,
  Undo2Icon,
  LayersIcon,
  EyeIcon,
  Trash2Icon,
  GlobeIcon,
  Edit2Icon,
} from 'lucide-react'
import { toast } from 'sonner'

import {
  addHolymanBlocklist,
  batchDeleteJargons,
  batchReviewHolymanCandidates,
  batchReviewJargons,
  checkHolymanUpdate,
  createJargon,
  deleteJargon,
  getHolymanStatus,
  getJargonEvidence,
  listJargons,
  previewHolymanSync,
  reviewJargon,
  syncHolymanAssets,
  toggleHolymanPhrase,
  toggleJargonGlobal,
  updateJargon,
  type HolymanSyncPreviewPayload,
  type HolymanUpdateCheckPayload,
  type JargonItem,
} from '@/api/jargon'
import { type StreamProgress } from '@/api/memories'

import { getStoredToken } from '@/api/client'
import {
  filterHolymanCandidates,
  filterHolymanEvidence,
  filterHolymanPhrases,
  getHolymanCategories,
  getSelectedCandidateWords,
} from './holymanFilters'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

function formatTime(seconds: unknown): string {
  const s = Number(seconds)
  if (!Number.isFinite(s) || s <= 0) return '-'
  return new Date(s * 1000).toLocaleString('zh-CN')
}

function formatUpdateCheckTime(value: unknown): string {
  const text = String(value || '').trim()
  if (!text) return '—'
  const date = new Date(text)
  if (Number.isNaN(date.getTime())) return text
  return date.toLocaleString('zh-CN')
}

function asArray(value: unknown): any[] {
  return Array.isArray(value) ? value : []
}

function holymanPhraseWord(item: any): string {
  return String(item?.word ?? '').trim()
}

function holymanCandidateKey(item: any): string {
  return String(item?.id ?? item?.word ?? '').trim()
}

function isHolymanPendingStatus(statusValue: unknown): boolean {
  const value = String(statusValue || 'pending').toLowerCase()
  return value === 'pending' || value === 'pending_review'
}

function holymanCorpusText(item: any): string {
  return String(item?.text ?? item?.content ?? item?.raw ?? item ?? '').trim()
}

function filterHolymanCorpus(items: any[], search: string): any[] {
  const q = search.trim().toLowerCase()
  if (!q) return items
  return items.filter((item) => {
    const text = holymanCorpusText(item).toLowerCase()
    const source = String(item?.source ?? '').toLowerCase()
    const terms = Array.isArray(item?.linked_terms) ? item.linked_terms.join(' ').toLowerCase() : ''
    return text.includes(q) || source.includes(q) || terms.includes(q)
  })
}

function syncCountLabel(key: string): string {
  return ({ phrases: '精选口癖', concepts: '文化概念', examples: '声音样本', corpus: '原始语料', candidates: '待审候选', blocked: '屏蔽项' } as Record<string, string>)[key] || key
}

function syncDeltaText(value: number): string {
  if (value > 0) return `+${value}`
  return String(value)
}

export function JargonPage() {
  const [activeTab, setActiveTab] = useState('local')
  
  // 1. 本地群黑话状态
  const [jargons, setJargons] = useState<JargonItem[]>([])
  const [total, setTotal] = useState(0)
  const [pendingCount, setPendingCount] = useState(0)
  
  const [search, setSearch] = useState('')
  const [groupId, setGroupId] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(15) // 单页行数持久化

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  // 多选勾选与跨页全选全部匹配
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [selectAllMatching, setSelectAllMatching] = useState(false)

  // 双击单元格内嵌快捷编辑
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editingValue, setEditingValue] = useState('')

  // 证据上下文气泡
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [evidenceId, setEvidenceId] = useState<number | null>(null)
  const [evidenceData, setEvidenceData] = useState<any | null>(null)
  const [evidenceBefore, setEvidenceBefore] = useState(15)
  const [evidenceAfter, setEvidenceAfter] = useState(15)
  const [evidenceLoading, setEvidenceLoading] = useState(false)

  // 新建/编辑弹窗
  const [editOpen, setEditOpen] = useState(false)
  const [editForm, setEditForm] = useState<Partial<JargonItem>>({
    word: '',
    meaning: '',
    group_id: '',
  })
  const [isEditNew, setIsEditNew] = useState(true)

  // 2. 广域 Holyman 同步与列表分层展示（词条、文化概念、例句、待审、Blocked）
  const [holyman, setHolyman] = useState<any | null>(null)
  const [holymanLoading, setHolymanLoading] = useState(false)
  const [holymanUpdateCheck, setHolymanUpdateCheck] = useState<HolymanUpdateCheckPayload | null>(null)
  const [holymanUpdateChecking, setHolymanUpdateChecking] = useState(false)
  const [streamOpen, setStreamOpen] = useState(false)
  const [streamProgress, setStreamProgress] = useState<StreamProgress | null>(null)
  const [streamLog, setStreamLog] = useState<string[]>([])
  const [syncPreviewOpen, setSyncPreviewOpen] = useState(false)
  const [syncPreviewLoading, setSyncPreviewLoading] = useState(false)
  const [syncPreview, setSyncPreview] = useState<HolymanSyncPreviewPayload | null>(null)
  const [syncConfirming, setSyncConfirming] = useState(false)
  
  // 广域 Mini 子选项卡：对齐 v4.0.0 旧前端的 Holyman 分层
  const [globalSubTab, setGlobalSubTab] = useState<'catchphrases' | 'concepts' | 'examples' | 'corpus' | 'candidates' | 'blocked'>('catchphrases')
  const [holymanSearch, setHolymanSearch] = useState('')
  const [corpusSearch, setCorpusSearch] = useState('')
  const [corpusVisibleCount, setCorpusVisibleCount] = useState(40)
  const [selectedCorpusItem, setSelectedCorpusItem] = useState<any | null>(null)
  const [holymanStatusFilter, setHolymanStatusFilter] = useState<'all' | 'active' | 'inactive'>('all')
  const [holymanCategoryFilter, setHolymanCategoryFilter] = useState('all')
  const [selectedHolymanWords, setSelectedHolymanWords] = useState<string[]>([])
  const [holymanEvidenceSearch, setHolymanEvidenceSearch] = useState('')
  const [candidateSearch, setCandidateSearch] = useState('')
  const [candidateStatusFilter, setCandidateStatusFilter] = useState('all')
  const [selectedHolymanCandidateIds, setSelectedHolymanCandidateIds] = useState<Array<number | string>>([])
  const [newBlockWord, setNewBlockWord] = useState('')
  const [holymanActionLoading, setHolymanActionLoading] = useState(false)

  // 全选控制（当页全选与跨页全选联动）
  function handleToggleSelectAll(checked: boolean) {
    setSelectAllMatching(false)
    if (checked) {
      setSelectedIds(jargons.map((j) => j.id))
    } else {
      setSelectedIds([])
    }
  }

  function handleRowCheckChange(id: number, checked: boolean) {
    setSelectAllMatching(false)
    if (checked) {
      setSelectedIds((prev) => [...prev, id])
    } else {
      setSelectedIds((prev) => prev.filter((item) => item !== id))
    }
  }

  // 跨页全选全部
  function handleSelectAllMatching() {
    setSelectAllMatching(true)
    setSelectedIds(jargons.map((j) => j.id)) // 视觉高亮当前行
    toast.info(`已选中全部符合检索条件的 ${total} 条黑话（跨页全选已激活）`)
  }

  async function loadLocalJargons(nextPage = page) {
    setLoading(true)
    try {
      const res = await listJargons({
        page: nextPage,
        size,
        status: status === 'all' ? '' : status,
        group_id: groupId,
        search,
      })
      setJargons(res.items ?? [])
      setTotal(res.total ?? 0)
      setPendingCount(res.pending_count ?? 0)
      setPage(nextPage)
      setSelectedIds([])
      setSelectAllMatching(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '加载黑话失败')
      setJargons([])
    } finally {
      setLoading(false)
    }
  }

  async function loadHolymanUpdateCheck(force = false, silent = false) {
    setHolymanUpdateChecking(true)
    try {
      const res = await checkHolymanUpdate(force)
      setHolymanUpdateCheck(res)
      if (!silent && force) {
        if (res.warning) {
          toast.warning(res.warning)
        } else if (res.has_update) {
          toast.info(`检测到 Holyman 远端更新：${res.remote_version || 'Unknown'}`)
        } else {
          toast.success('Holyman 远端检查完成，当前无需更新')
        }
      }
      return res
    } catch (err) {
      if (!silent) {
        toast.error(err instanceof Error ? err.message : '检查 Holyman 更新失败')
      }
      return null
    } finally {
      setHolymanUpdateChecking(false)
    }
  }

  async function loadHolyman() {
    setHolymanLoading(true)
    try {
      const res = await getHolymanStatus()
      setHolyman(res)
      if (res?.update_check) {
        setHolymanUpdateCheck(res.update_check)
      }
      setSelectedHolymanWords([])
      setSelectedHolymanCandidateIds([])
    } catch {
      // 容错
    } finally {
      setHolymanLoading(false)
    }
  }

  useEffect(() => {
    if (activeTab === 'local') {
      void loadLocalJargons(1)
    } else {
      void loadHolyman()
    }
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, status, size])

  useEffect(() => {
    if (activeTab !== 'global') return undefined
    void loadHolymanUpdateCheck(false, true)
    const timer = window.setInterval(() => {
      void loadHolymanUpdateCheck(false, true)
    }, 15 * 60 * 1000)
    return () => window.clearInterval(timer)
  }, [activeTab])

  useEffect(() => {
    setSelectedHolymanWords([])
    setSelectedHolymanCandidateIds([])
    setCorpusVisibleCount(40)
  }, [globalSubTab])

  useEffect(() => {
    setCorpusVisibleCount(40)
  }, [corpusSearch])

  // 本地检索提交
  function handleSearchSubmit(e?: React.FormEvent) {
    if (e) e.preventDefault()
    void loadLocalJargons(1)
  }

  function handleResetFilters() {
    setSearch('')
    setGroupId('')
    setStatus('')
    void loadLocalJargons(1)
  }

  // 双击直接内嵌快捷编辑释义
  function handleDoubleDlickEdit(item: JargonItem) {
    setEditingId(item.id)
    setEditingValue(item.meaning)
  }

  async function handleQuickEditSave(id: number) {
    setEditingId(null)
    try {
      await updateJargon(id, { meaning: editingValue })
      toast.success('黑话释义已快捷更新')
      setJargons((prev) =>
        prev.map((j) => (j.id === id ? { ...j, meaning: editingValue } : j))
      )
    } catch {
      toast.error('快捷保存失败')
    }
  }

  // 单行审核确认/否决
  async function handleReviewSingle(id: number, action: 'approve' | 'reject') {
    try {
      await reviewJargon(id, action)
      toast.success(action === 'approve' ? `黑话已通过审核并确认` : `已否决并拉黑该黑话`)
      await loadLocalJargons(page)
    } catch {
      toast.error('审核失败')
    }
  }

  // 单行切换全局/群限制
  async function handleToggleGlobalSingle(id: number) {
    try {
      const res = await toggleJargonGlobal(id)
      toast.success(res.is_global ? '已设为全局可用' : '已限制为群专用')
      await loadLocalJargons(page)
    } catch {
      toast.error('操作失败')
    }
  }

  // 单行物理删除
  async function handleDeleteSingle(id: number) {
    if (!confirm(`确定要永久删除黑话词条 #${id} 吗？`)) return
    try {
      await deleteJargon(id)
      toast.success('删除成功')
      await loadLocalJargons(page)
    } catch {
      toast.error('删除失败')
    }
  }

  // 批量通过/否决/删除
  async function handleBatchReview(action: 'approve' | 'reject') {
    const count = selectedIds.length
    if (!count) return
    setLoading(true)
    try {
      if (selectAllMatching) {
        // 跨页全选，传 all_matching 告诉后端全量操作
        const token = getStoredToken()
        const headers: HeadersInit = token 
          ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } 
          : { 'Content-Type': 'application/json' }
        const res = await fetch('/api/jargon/batch-review', {
          method: 'POST',
          headers,
          body: JSON.stringify({
            all_matching: true,
            action,
            status: status === 'all' ? '' : status,
            group_id: groupId,
            search,
          })
        })
        const data = await res.json() as any
        toast.success(`一键成功批量审核了 ${data.reviewed_count} 条黑话记录`)
      } else {
        await batchReviewJargons(selectedIds, action)
        toast.success(`已批量审核并[${action === 'approve' ? '确认' : '否决'}] ${count} 条黑话`)
      }
      await loadLocalJargons(page)
    } catch {
      toast.error('操作失败')
      setLoading(false)
    }
  }

  async function handleBatchDelete() {
    const count = selectedIds.length
    if (!count) return
    if (!confirm(`确定要批量物理删除这 ${count} 条黑话记录吗？`)) return
    setLoading(true)
    try {
      if (selectAllMatching) {
        const token = getStoredToken()
        const headers: HeadersInit = token 
          ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } 
          : { 'Content-Type': 'application/json' }
        const res = await fetch('/api/jargon/batch-delete', {
          method: 'POST',
          headers,
          body: JSON.stringify({
            all_matching: true,
            status: status === 'all' ? '' : status,
            group_id: groupId,
            search,
          })
        })
        const data = await res.json() as any
        toast.success(`一键批量物理删除了全部 ${data.deleted_count} 条匹配黑话`)
      } else {
        await batchDeleteJargons(selectedIds)
        toast.success(`成功批量删除了 ${count} 条黑话`)
      }
      await loadLocalJargons(page)
    } catch {
      toast.error('操作失败')
      setLoading(false)
    }
  }

  // 本地新建与保存编辑弹窗
  async function handleSaveEdit() {
    setSaving(true)
    try {
      if (isEditNew) {
        await createJargon(editForm)
        toast.success('成功新增黑话词条')
      } else {
        if (editForm.id) {
          await updateJargon(editForm.id, editForm)
          toast.success(`黑话 #${editForm.id} 更新成功`)
        }
      }
      setEditOpen(false)
      await loadLocalJargons(page)
    } catch {
      toast.error('操作失败')
    } finally {
      setSaving(false)
    }
  }

  function handleOpenCreate() {
    setIsEditNew(true)
    setEditForm({ word: '', meaning: '', group_id: '' })
    setEditOpen(true)
  }

  function handleOpenEdit(j: JargonItem) {
    setIsEditNew(false)
    setEditForm(JSON.parse(JSON.stringify(j)))
    setEditOpen(true)
  }

  // 打开关联证据还原弹窗
  async function handleOpenEvidence(id: number, b = evidenceBefore, a = evidenceAfter) {
    setEvidenceOpen(true)
    setEvidenceId(id)
    setEvidenceLoading(true)
    setEvidenceData(null)
    try {
      const res = await getJargonEvidence(id, b, a)
      setEvidenceData(res)
    } catch {
      toast.error('证据加载失败')
    } finally {
      setEvidenceLoading(false)
    }
  }

  // 触发广域 Holyman GitHub 同步预览；确认后才会真实写入资产
  async function handleSyncHolyman() {
    setSyncPreviewOpen(true)
    setSyncPreviewLoading(true)
    setSyncPreview(null)
    try {
      const preview = await previewHolymanSync({ use_proxy: true })
      setSyncPreview(preview)
      if (!preview.ok) {
        toast.error(preview.error || '同步预览失败')
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '同步预览失败')
    } finally {
      setSyncPreviewLoading(false)
    }
  }

  async function handleConfirmSyncHolyman() {
    setSyncConfirming(true)
    setSyncPreviewOpen(false)
    setStreamOpen(true)
    setStreamLog([
      '[CONFIRM] 用户已确认版本与内容差异，开始写入 Holyman 分层资产。',
      `[PREVIEW] 本地 ${syncPreview?.local_version || 'Unknown'} → 远端 ${syncPreview?.remote_version || 'Unknown'}`,
    ])
    setStreamProgress({ progress: 0.08, processed: 0, total: 1 })
    try {
      const res = await syncHolymanAssets({ use_proxy: true })
      if (!res?.ok) {
        throw new Error(res?.error || '同步失败')
      }
      setStreamProgress({ progress: 1, processed: 1, total: 1, done: true })
      setStreamLog((prev) => [
        ...prev,
        `[WRITE] 精选口癖 ${res.content_count ?? res.phrases_count ?? '—'} 条；文化概念 ${res.concepts_count ?? '—'} 组；原始语料 ${res.corpus_count ?? '—'} 条。`,
        '[SUCCESS] 广域 Holyman 语料分层数据库同步覆写完成。',
      ].slice(-60))
      toast.success('Holyman 数据源同步成功')
      await loadHolyman()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '同步失败'
      setStreamProgress({ progress: 1, processed: 0, total: 1, error: msg, done: true })
      setStreamLog((prev) => [...prev, `[ERROR] 同步失败: ${msg}`])
      toast.error(`同步中断: ${msg}`)
    } finally {
      setSyncConfirming(false)
    }
  }

  // 广域语料一键激活/去激活切换
  async function handleToggleHolymanActivation(word: string, meaning: string, currentActivated: boolean) {
    try {
      const res = await toggleHolymanPhrase({ word, meaning, activate: !currentActivated })
      if (res.ok) {
        toast.success(!currentActivated ? `已启用口癖「${word}」为理解提示` : `已停用口癖「${word}」的理解提示`)
        await loadHolyman()
      } else {
        throw new Error(res.error || '激活失败')
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败')
    }
  }

  async function handleBatchToggleHolyman(activate: boolean) {
    const selectedSet = new Set(selectedHolymanWords)
    const selectedItems = filteredHolymanPhrases.filter((item: any) => selectedSet.has(holymanPhraseWord(item)))
    if (!selectedItems.length) return

    setHolymanActionLoading(true)
    try {
      const results = await Promise.allSettled(
        selectedItems.map((item: any) => toggleHolymanPhrase({
          word: holymanPhraseWord(item),
          meaning: String(item?.meaning || ''),
          activate,
        })),
      )
      const failed = results.filter((result) => result.status === 'rejected' || (result.status === 'fulfilled' && !result.value.ok)).length
      const succeeded = selectedItems.length - failed
      if (failed > 0) {
        toast.error(`批量${activate ? '启用' : '停用'}完成：成功 ${succeeded} 条，失败 ${failed} 条`)
      } else {
        toast.success(`已批量${activate ? '启用' : '停用'} ${succeeded} 条精选口癖`)
      }
      await loadHolyman()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '批量操作失败')
    } finally {
      setHolymanActionLoading(false)
    }
  }

  // 广域待审候选词/抓取候选审核通过或否决
  async function handleReviewCandidateSingle(candidate: any, action: 'approve' | 'reject') {
    const key = holymanCandidateKey(candidate)
    if (!key) return
    try {
      const res = await batchReviewHolymanCandidates({ ids: [key], words: [String(candidate?.word || '')].filter(Boolean), action })
      if (res.ok) {
        toast.success(action === 'approve' ? '已通过候选加入黑话库' : '已拒绝该候选词条')
        await loadHolyman()
      } else {
        throw new Error(res.error || '审核失败')
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败')
    }
  }

  async function handleBatchReviewHolymanCandidates(action: 'approve' | 'reject') {
    if (!selectedHolymanCandidateIds.length) return
    if (action === 'reject' && !confirm(`确认拒绝并屏蔽选中的 ${selectedHolymanCandidateIds.length} 个候选？`)) return

    setHolymanActionLoading(true)
    try {
      const words = getSelectedCandidateWords(filteredHolymanCandidates, selectedHolymanCandidateIds)
      const res = await batchReviewHolymanCandidates({ ids: selectedHolymanCandidateIds, words, action })
      if (res.ok) {
        toast.success(`已批量${action === 'approve' ? '通过' : '拒绝'} ${res.reviewed_count || 0} 个候选`)
        await loadHolyman()
      } else {
        throw new Error(res.error || '候选批量审核失败')
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '候选批量审核失败')
    } finally {
      setHolymanActionLoading(false)
    }
  }

  // 广域屏蔽词手动封禁
  async function handleAddBlocklist(word: string) {
    const val = word.trim()
    if (!val) return
    setHolymanActionLoading(true)
    try {
      const res = await addHolymanBlocklist({ word: val, reason: 'manual_block' })
      if (res.ok) {
        toast.success(`已成功将「${val}」手动列入黑话过滤屏蔽名单`)
        setNewBlockWord('')
        await loadHolyman()
      } else {
        throw new Error(res.error || '添加失败')
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '添加失败')
    } finally {
      setHolymanActionLoading(false)
    }
  }

  function handleToggleHolymanPhraseSelection(word: string, checked: boolean) {
    setSelectedHolymanWords((prev) => {
      if (checked) return prev.includes(word) ? prev : [...prev, word]
      return prev.filter((item) => item !== word)
    })
  }

  function handleToggleAllFilteredHolymanPhrases(checked: boolean) {
    setSelectedHolymanWords(checked ? filteredHolymanPhrases.map((item: any) => holymanPhraseWord(item)).filter(Boolean) : [])
  }

  function handleToggleHolymanCandidateSelection(id: number | string, checked: boolean) {
    const key = String(id)
    setSelectedHolymanCandidateIds((prev) => {
      if (checked) return prev.map(String).includes(key) ? prev : [...prev, id]
      return prev.filter((item) => String(item) !== key)
    })
  }

  function handleToggleAllFilteredHolymanCandidates(checked: boolean) {
    setSelectedHolymanCandidateIds(checked ? filteredHolymanCandidates.map((item: any) => holymanCandidateKey(item)).filter(Boolean) : [])
  }

  const isAllSelected = jargons.length > 0 && selectedIds.length === jargons.length
  const totalPages = Math.ceil(total / size) || 1
  const holymanPhrases = asArray(holyman?.phrases ?? holyman?.items ?? holyman?.layers?.catchphrases)
  const holymanConcepts = asArray(holyman?.concepts ?? holyman?.layers?.concepts)
  const holymanExamples = asArray(holyman?.examples ?? holyman?.layers?.quotes_knowledge)
  const holymanCorpus = asArray(holyman?.corpus ?? holyman?.layers?.corpus?.items)
  const holymanCandidates = asArray(holyman?.candidates ?? holyman?.layers?.candidates)
  const holymanBlocked = holyman?.blocked ?? holyman?.layers?.blocked ?? {}
  const holymanCorpusSummary = holyman?.corpus_summary ?? holyman?.layers?.corpus ?? { count: holymanCorpus.length, reference_only: true }
  const effectiveHolymanUpdateCheck = holymanUpdateCheck ?? holyman?.update_check ?? null
  const holymanIsUpdateAvailable = Boolean(effectiveHolymanUpdateCheck?.has_update ?? holyman?.is_update_available ?? holyman?.update_available)
  const holymanLocalVersion = effectiveHolymanUpdateCheck?.local_version ?? holyman?.local_version ?? 'Unknown'
  const holymanRemoteVersion = effectiveHolymanUpdateCheck?.remote_version ?? holyman?.remote_version ?? 'Unknown'
  const holymanAssetStatus = effectiveHolymanUpdateCheck?.asset_status ?? holyman?.asset_status ?? 'ready'
  const holymanCheckedAt = effectiveHolymanUpdateCheck?.checked_at ?? holyman?.checked_at
  const holymanCheckCached = Boolean(effectiveHolymanUpdateCheck?.cached ?? holyman?.update_cached)
  const holymanCheckWarning = effectiveHolymanUpdateCheck?.warning ?? holyman?.warning
  const holymanCategories = getHolymanCategories(holymanPhrases, asArray(holyman?.categories))
  const filteredHolymanPhrases = filterHolymanPhrases(holymanPhrases, {
    search: holymanSearch,
    status: holymanStatusFilter,
    category: holymanCategoryFilter,
  })
  const filteredHolymanConcepts = filterHolymanEvidence(holymanConcepts, holymanEvidenceSearch)
  const filteredHolymanExamples = filterHolymanEvidence(holymanExamples, holymanEvidenceSearch)
  const filteredHolymanCorpus = filterHolymanCorpus(holymanCorpus, corpusSearch)
  const visibleHolymanCorpus = filteredHolymanCorpus.slice(0, corpusVisibleCount)
  const filteredHolymanCandidates = filterHolymanCandidates(holymanCandidates, {
    search: candidateSearch,
    status: candidateStatusFilter,
  })
  const selectedHolymanWordSet = new Set(selectedHolymanWords)
  const allFilteredHolymanPhrasesSelected = filteredHolymanPhrases.length > 0 && filteredHolymanPhrases.every((item: any) => selectedHolymanWordSet.has(holymanPhraseWord(item)))
  const selectedCandidateIdSet = new Set(selectedHolymanCandidateIds.map(String))
  const allFilteredHolymanCandidatesSelected = filteredHolymanCandidates.length > 0 && filteredHolymanCandidates.every((item: any) => selectedCandidateIdSet.has(holymanCandidateKey(item)))

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader className="py-4 shrink-0 border-b bg-muted/10">
          <CardTitle>黑话与口癖审核</CardTitle>
          <CardDescription>
            对聊天中自动捕获、清洗的待审黑话词条进行人工裁决核对，并支持同步 Holyman 广域抽象黑话分层资产。
          </CardDescription>
        </CardHeader>
      </Card>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid w-full grid-cols-2 max-w-md shrink-0">
          <TabsTrigger value="local" onClick={() => setActiveTab('local')}>群聊本地黑话</TabsTrigger>
          <TabsTrigger value="global" onClick={() => { setActiveTab('global'); void loadHolyman(); }}>广域抽象黑话 (Holyman)</TabsTrigger>
        </TabsList>

        {/* ═══ Tab 1: 本地群黑话 ═══ */}
        <TabsContent value="local" className="mt-4 flex flex-col gap-4">
          <Card>
            <CardContent className="pt-6">
              <form className="flex flex-wrap items-center gap-2 mb-4" onSubmit={handleSearchSubmit}>
                <Input
                  className="max-w-xs h-9 text-xs"
                  placeholder="搜索词条/释义..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                <Input
                  className="w-32 h-9 text-xs"
                  placeholder="过滤群号..."
                  value={groupId}
                  onChange={(e) => setGroupId(e.target.value)}
                />
                <Select value={status || 'all'} onValueChange={(val) => setStatus(val === 'all' ? '' : val)}>
                  <SelectTrigger className="w-32 h-9 text-xs">
                    <SelectValue placeholder="全部状态" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部状态</SelectItem>
                    <SelectItem value="pending">待确认</SelectItem>
                    <SelectItem value="confirmed">已确认</SelectItem>
                    <SelectItem value="rejected">已否决</SelectItem>
                  </SelectContent>
                </Select>
                <Button disabled={loading} type="submit" size="sm">
                  <SearchIcon className="size-3.5 mr-1" />
                  搜索
                </Button>
                <Button disabled={loading} variant="outline" size="sm" type="button" onClick={handleResetFilters}>
                  <Undo2Icon className="size-3.5 mr-1" />
                  重置
                </Button>
                <Button disabled={loading} type="button" size="sm" variant="outline" onClick={handleOpenCreate}>
                  ➕ 手动新建
                </Button>
                <div className="ml-auto text-xs text-muted-foreground font-mono">
                  待审：<span className="text-amber-500 font-bold">{pendingCount}</span> 条 · 累计：{total} 条
                </div>
              </form>

              {/* 批量操作控制条 */}
              {selectedIds.length > 0 ? (
                <div className="flex flex-wrap items-center gap-3 rounded-lg border border-primary bg-primary/5 p-3 mb-4 animate-in slide-in-from-top duration-200">
                  <Badge variant="secondary" className="bg-primary/10 text-primary border-primary/20 hover:bg-primary/10">
                    {selectAllMatching ? `已跨页勾选了全部 ${total} 条黑话` : `已勾选当页 ${selectedIds.length} 条黑话`}
                  </Badge>
                  <Button size="xs" onClick={() => void handleBatchReview('approve')}>✓ 批量确认通过</Button>
                  <Button size="xs" variant="outline" className="border-red-500/20 text-destructive hover:bg-destructive/10" onClick={() => void handleBatchReview('reject')}>✕ 批量否决拉黑</Button>
                  <Button size="xs" variant="destructive" onClick={() => void handleBatchDelete()}>🗑 一键物理删除</Button>
                  {!selectAllMatching && total > size ? (
                    <Button size="xs" variant="ghost" className="text-primary hover:bg-primary/10" onClick={handleSelectAllMatching}>
                      🌌 跨页全选全部 {total} 条匹配
                    </Button>
                  ) : null}
                  <Button size="xs" variant="ghost" className="ml-auto" onClick={() => setSelectedIds([])}>取消</Button>
                </div>
              ) : null}

              {loading ? (
                <div className="flex flex-col gap-3">
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                </div>
              ) : jargons.length === 0 ? (
                <p className="text-sm text-muted-foreground p-6 text-center">暂无符合条件的黑话记录。</p>
              ) : (
                <div className="overflow-auto rounded-lg border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-10">
                          <input
                            type="checkbox"
                            checked={isAllSelected}
                            onChange={(e) => handleToggleSelectAll(e.target.checked)}
                          />
                        </TableHead>
                        <TableHead className="w-36">词条</TableHead>
                        <TableHead>释义 (双击快捷修改)</TableHead>
                        <TableHead className="w-24 text-center">学成频次</TableHead>
                        <TableHead className="w-24 text-center">当前状态</TableHead>
                        <TableHead className="w-28 text-center">作用域</TableHead>
                        <TableHead className="w-48 text-right pr-4">操作</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {jargons.map((j) => {
                        const isRowChecked = selectedIds.includes(j.id)
                        const isEditing = editingId === j.id
                        
                        return (
                          <TableRow key={j.id} className={isRowChecked ? 'bg-primary/5 hover:bg-primary/5' : ''}>
                            <TableCell>
                              <input
                                type="checkbox"
                                checked={isRowChecked || selectAllMatching}
                                onChange={(e) => handleRowCheckChange(j.id, e.target.checked)}
                              />
                            </TableCell>
                            <TableCell className="font-semibold text-sm text-foreground">{j.word}</TableCell>
                            <TableCell onDoubleClick={() => handleDoubleDlickEdit(j)}>
                              {isEditing ? (
                                <Input
                                  autoFocus
                                  className="h-8 max-w-sm text-xs py-0.5"
                                  value={editingValue}
                                  onChange={(e) => setEditingValue(e.target.value)}
                                  onBlur={() => void handleQuickEditSave(j.id)}
                                  onKeyDown={(e) => {
                                    if (e.key === 'Enter') void handleQuickEditSave(j.id)
                                    if (e.key === 'Escape') setEditingId(null)
                                  }}
                                />
                              ) : (
                                <span className="text-muted-foreground text-xs cursor-pointer hover:underline block truncate max-w-md" title="双击直接内嵌快捷修改释义">
                                  {j.meaning || '—'}
                                </span>
                              )}
                            </TableCell>
                            <TableCell className="text-center font-mono text-xs">{j.frequency} 次</TableCell>
                            <TableCell className="text-center">
                              <Badge className={j.status === 'confirmed' ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/10 hover:bg-emerald-500/10' : j.status === 'rejected' ? 'bg-destructive/10 text-destructive border border-destructive/10 hover:bg-destructive/10' : 'bg-amber-500/10 text-amber-500 border border-amber-500/10 hover:bg-amber-500/10'} variant="outline">
                                {j.status === 'confirmed' ? '已确认' : j.status === 'rejected' ? '已否决' : '待确认'}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-center">
                              <Badge variant={j.is_global ? 'secondary' : 'outline'} className="text-[10px]">
                                {j.is_global ? '全局可用' : `群 ${j.group_id}`}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-right pr-4 whitespace-nowrap">
                              <div className="flex justify-end gap-1.5">
                                {j.status !== 'confirmed' ? (
                                  <Button size="xs" variant="secondary" className="bg-emerald-500/10 text-emerald-500 border border-emerald-500/10 hover:bg-emerald-500/20" onClick={() => void handleReviewSingle(j.id, 'approve')}>
                                    通过
                                  </Button>
                                ) : null}
                                {j.status !== 'rejected' ? (
                                  <Button size="xs" variant="outline" className="border-red-500/20 text-destructive hover:bg-destructive/10" onClick={() => void handleReviewSingle(j.id, 'reject')}>
                                    否决
                                  </Button>
                                ) : null}
                                <Button variant="ghost" className="h-7 text-xs px-2 flex items-center gap-1" onClick={() => void handleOpenEvidence(j.id)}>
                                  <EyeIcon className="size-3" />
                                  证据
                                </Button>
                                <Button variant="ghost" className="size-7 p-0" onClick={() => handleOpenEdit(j)} title="编辑词条">
                                  <Edit2Icon className="size-3.5 text-muted-foreground" />
                                </Button>
                                <Button variant="ghost" className="size-7 p-0" onClick={() => void handleToggleGlobalSingle(j.id)} title={j.is_global ? '设为群专' : '设为全局可用'}>
                                  <GlobeIcon className="size-3.5 text-muted-foreground" />
                                </Button>
                                <Button variant="ghost" className="size-7 p-0 text-destructive hover:text-destructive hover:bg-destructive/10" onClick={() => void handleDeleteSingle(j.id)}>
                                  <Trash2Icon className="size-3.5" />
                                </Button>
                              </div>
                            </TableCell>
                          </TableRow>
                        )
                      })}
                    </TableBody>
                  </Table>
                </div>
              )}

              {/* 分页控制栏 */}
              {jargons.length > 0 ? (
                <div className="mt-4 flex items-center justify-between gap-4 border-t pt-3">
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <span>每页显示</span>
                    <Select value={String(size)} onValueChange={(val) => setSize(Number(val))}>
                      <SelectTrigger className="w-18 h-7 text-xs py-0.5">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="15">15 行</SelectItem>
                        <SelectItem value="30">30 行</SelectItem>
                        <SelectItem value="50">50 行</SelectItem>
                        <SelectItem value="100">100 行</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex items-center gap-4">
                    <Button disabled={loading || page <= 1} variant="outline" size="sm" onClick={() => void loadLocalJargons(page - 1)}>
                      上一页
                    </Button>
                    <span className="font-mono text-xs text-muted-foreground">
                      第 {page} / {totalPages} 页
                    </span>
                    <Button disabled={loading || page >= totalPages} variant="outline" size="sm" onClick={() => void loadLocalJargons(page + 1)}>
                      下一页
                    </Button>
                  </div>
                </div>
              ) : null}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ═══ Tab 2: Holyman 广域同步 ═══ */}
        <TabsContent value="global" className="mt-4 flex flex-col gap-4">
          <Card>
            <CardHeader className="py-4 flex flex-row items-center justify-between gap-4 border-b shrink-0 bg-muted/20">
              <div className="flex flex-col gap-1">
                <CardTitle className="text-sm font-semibold">🌌 广域抽象黑话 (Holyman)</CardTitle>
                <CardDescription>
                  内置 Holyman 广域黑话分层资产：精选口癖可手动启用为理解提示，其余文化概念、声音样本与原始语料仅作为语境参考，不改变系统身份。
                </CardDescription>
              </div>
              <Button disabled={holymanLoading || syncPreviewLoading || syncConfirming} onClick={() => void handleSyncHolyman()}>
                {syncPreviewLoading ? <Loader2Icon className="animate-spin" data-icon="inline-start" /> : <RefreshCwIcon data-icon="inline-start" />}
                预览并确认同步
              </Button>
            </CardHeader>
            <CardContent className="pt-6">
              {holymanLoading ? (
                <div className="py-12 flex justify-center"><Loader2Icon className="animate-spin text-primary size-6" /></div>
              ) : holyman ? (
                <div className="flex flex-col gap-6 animate-in fade-in duration-200">
                  <Alert className={holymanIsUpdateAvailable ? 'bg-amber-500/10 border-amber-500/20 text-amber-500' : 'bg-emerald-500/10 border-emerald-500/20 text-emerald-500'}>
                    {holymanIsUpdateAvailable ? <AlertCircleIcon /> : <CheckCircle2Icon className="text-emerald-500" />}
                    <AlertTitle>{holymanIsUpdateAvailable ? 'Holyman 知识库有可用更新' : '本地 Holyman 分层资产可用'}</AlertTitle>
                    <AlertDescription>
                      <div className="flex flex-col gap-2 text-xs">
                        <div className="flex flex-wrap items-center gap-2">
                          <span>本地版本：<span className="font-mono">{holymanLocalVersion}</span></span>
                          <span>线上最新：<span className="font-mono">{holymanRemoteVersion}</span></span>
                          <Badge variant={holymanCheckCached ? 'secondary' : 'outline'} className="text-[9px]">
                            {holymanCheckCached ? '缓存结果' : '实时检查'}
                          </Badge>
                          <Badge variant="outline" className="text-[9px]">质量：{holymanAssetStatus}</Badge>
                        </div>
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="text-muted-foreground">
                            上次检查：<span className="font-mono">{formatUpdateCheckTime(holymanCheckedAt)}</span>
                            {effectiveHolymanUpdateCheck?.cache_age_seconds ? ` · 缓存 ${effectiveHolymanUpdateCheck.cache_age_seconds}s` : ''}
                          </span>
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-7 px-2 text-[11px]"
                            disabled={holymanUpdateChecking}
                            onClick={() => void loadHolymanUpdateCheck(true)}
                          >
                            {holymanUpdateChecking ? <Loader2Icon className="animate-spin" data-icon="inline-start" /> : <RefreshCwIcon data-icon="inline-start" />}
                            强制检查
                          </Button>
                        </div>
                        {holymanCheckWarning ? (
                          <div className="text-destructive">{holymanCheckWarning}</div>
                        ) : holymanIsUpdateAvailable ? (
                          <div>建议先点击「预览并确认同步」查看差异，确认后再写入本地分层资产。</div>
                        ) : null}
                      </div>
                    </AlertDescription>
                  </Alert>

                  <div className="grid gap-4 sm:grid-cols-4">
                    <Card className="bg-muted/10 border-border/50"><CardHeader className="py-3"><CardDescription className="text-xs">精选口癖</CardDescription><CardTitle className="text-xl font-mono text-primary">{holymanPhrases.length} 条</CardTitle></CardHeader></Card>
                    <Card className="bg-muted/10 border-border/50"><CardHeader className="py-3"><CardDescription className="text-xs">文化概念</CardDescription><CardTitle className="text-xl font-mono text-primary">{holymanConcepts.length} 组</CardTitle></CardHeader></Card>
                    <Card className="bg-muted/10 border-border/50"><CardHeader className="py-3"><CardDescription className="text-xs">声音样本与知识</CardDescription><CardTitle className="text-xl font-mono text-primary">{holymanExamples.length} 条</CardTitle></CardHeader></Card>
                    <Card className="bg-muted/10 border-border/50"><CardHeader className="py-3"><CardDescription className="text-xs">原始语料</CardDescription><CardTitle className="text-xl font-mono text-primary">{holymanCorpus.length || holymanCorpusSummary?.count || 0} 条</CardTitle></CardHeader></Card>
                  </div>

                  {/* 广域分层 Sub-Tabs 子分类过滤器 */}
                  <Tabs value={globalSubTab} onValueChange={(val: any) => setGlobalSubTab(val)} className="border rounded-xl p-4 bg-muted/5 mt-2">
                    <div className="flex flex-wrap items-center justify-between border-b pb-3 mb-4 gap-3">
                      <div className="flex items-center gap-1.5">
                        <LayersIcon className="size-4 text-primary" />
                        <span className="text-sm font-semibold text-foreground">分层语料透视配置</span>
                      </div>
                      <TabsList className="grid grid-cols-6 h-8 w-full max-w-2xl">
                        <TabsTrigger value="catchphrases" className="text-xs">精选口癖</TabsTrigger>
                        <TabsTrigger value="concepts" className="text-xs">文化概念</TabsTrigger>
                        <TabsTrigger value="examples" className="text-xs">声音样本与知识</TabsTrigger>
                        <TabsTrigger value="corpus" className="text-xs">原始语料</TabsTrigger>
                        <TabsTrigger value="candidates" className="text-xs">待审核候选</TabsTrigger>
                        <TabsTrigger value="blocked" className="text-xs">屏蔽项</TabsTrigger>
                      </TabsList>
                    </div>
                    <div className="text-[11px] text-muted-foreground bg-muted/30 border rounded-md p-2 mb-4">
                      证据层只读展示；文化概念、声音样本与知识、原始语料只作为语境参考，不直接注入 prompt。只有精选口癖和人工通过的候选能进入可控注入候选。
                    </div>

                    {/* Sub-Tab 1: 精选口癖词条 */}
                    <TabsContent value="catchphrases" className="mt-0">
                      <div className="flex flex-col gap-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <Input
                            className="h-8 max-w-xs text-xs"
                            placeholder="搜索词条/释义/分类..."
                            value={holymanSearch}
                            onChange={(event) => setHolymanSearch(event.target.value)}
                          />
                          <Select value={holymanStatusFilter} onValueChange={(value: 'all' | 'active' | 'inactive') => setHolymanStatusFilter(value)}>
                            <SelectTrigger className="h-8 w-32 text-xs">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="all">全部状态</SelectItem>
                              <SelectItem value="active">已启用</SelectItem>
                              <SelectItem value="inactive">未启用</SelectItem>
                            </SelectContent>
                          </Select>
                          <Select value={holymanCategoryFilter} onValueChange={setHolymanCategoryFilter}>
                            <SelectTrigger className="h-8 w-40 text-xs">
                              <SelectValue placeholder="全部分类" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="all">全部分类</SelectItem>
                              {holymanCategories.map((category) => (
                                <SelectItem key={category.id} value={category.id}>{category.label} · {category.count}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <Badge variant="secondary" className="text-[10px]">
                            已筛选 {filteredHolymanPhrases.length} 条 · 已选 {selectedHolymanWords.length} 条
                          </Badge>
                        </div>

                        <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/20 p-2">
                          <label className="flex items-center gap-2 text-xs text-muted-foreground">
                            <input
                              type="checkbox"
                              checked={allFilteredHolymanPhrasesSelected}
                              onChange={(event) => handleToggleAllFilteredHolymanPhrases(event.target.checked)}
                            />
                            全选当前筛选结果
                          </label>
                          <Button size="xs" disabled={holymanActionLoading || selectedHolymanWords.length === 0} onClick={() => void handleBatchToggleHolyman(true)}>
                            批量启用
                          </Button>
                          <Button size="xs" variant="outline" disabled={holymanActionLoading || selectedHolymanWords.length === 0} onClick={() => void handleBatchToggleHolyman(false)}>
                            批量停用
                          </Button>
                          <Button size="xs" variant="ghost" disabled={selectedHolymanWords.length === 0} onClick={() => setSelectedHolymanWords([])}>
                            清空选择
                          </Button>
                        </div>

                        <div className="overflow-auto rounded-lg border max-h-[340px]">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead className="w-10">选择</TableHead>
                                <TableHead className="w-36">词条/口癖</TableHead>
                                <TableHead>黑话释义 (Meaning)</TableHead>
                                <TableHead className="w-28 text-center">二级分类</TableHead>
                                <TableHead className="w-24 text-center">状态</TableHead>
                                <TableHead className="w-32 text-right pr-4">理解提示操作</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {filteredHolymanPhrases.length === 0 ? (
                                <TableRow><TableCell colSpan={6} className="text-center text-xs text-muted-foreground p-6">没有符合筛选条件的精选口癖。</TableCell></TableRow>
                              ) : (
                                filteredHolymanPhrases.map((item: any, idx: number) => {
                                  const word = holymanPhraseWord(item)
                                  return (
                                    <TableRow key={`${word}-${idx}`}>
                                      <TableCell>
                                        <input
                                          type="checkbox"
                                          checked={selectedHolymanWordSet.has(word)}
                                          onChange={(event) => handleToggleHolymanPhraseSelection(word, event.target.checked)}
                                        />
                                      </TableCell>
                                      <TableCell className="font-semibold text-xs text-foreground font-mono">{item.word}</TableCell>
                                      <TableCell className="text-xs text-muted-foreground leading-relaxed">{item.meaning || '—'}</TableCell>
                                      <TableCell className="text-center">
                                        <Badge variant="secondary" className="text-[9px] font-mono">{item.category_label || item.category}</Badge>
                                      </TableCell>
                                      <TableCell className="text-center">
                                        <Badge variant={item.is_activated ? 'secondary' : 'outline'} className="text-[9px]">
                                          {item.is_activated ? '已启用' : '未启用'}
                                        </Badge>
                                      </TableCell>
                                      <TableCell className="text-right pr-4">
                                        <Button
                                          size="xs"
                                          variant={item.is_activated ? 'destructive' : 'secondary'}
                                          className="h-6 text-[10px]"
                                          disabled={holymanActionLoading}
                                          onClick={() => void handleToggleHolymanActivation(item.word, item.meaning, item.is_activated)}
                                        >
                                          {item.is_activated ? '✕ 停用提示' : '✓ 启用提示'}
                                        </Button>
                                      </TableCell>
                                    </TableRow>
                                  )
                                })
                              )}
                            </TableBody>
                          </Table>
                        </div>
                      </div>
                    </TabsContent>

                    {/* Sub-Tab 2: 文化概念 / Concepts */}
                    <TabsContent value="concepts" className="mt-0">
                      <div className="flex flex-col gap-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <Input
                            className="h-8 max-w-sm text-xs"
                            placeholder="搜索文化概念 / 来源 / 标签..."
                            value={holymanEvidenceSearch}
                            onChange={(event) => setHolymanEvidenceSearch(event.target.value)}
                          />
                          <Badge variant="secondary" className="text-[10px]">已筛选 {filteredHolymanConcepts.length} 组</Badge>
                        </div>
                        <div className="overflow-auto rounded-lg border max-h-[340px]">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead className="w-40">文化概念</TableHead>
                                <TableHead>摘要与语境说明</TableHead>
                                <TableHead className="w-40 text-center">来源</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {filteredHolymanConcepts.length === 0 ? (
                                <TableRow><TableCell colSpan={3} className="text-center text-xs text-muted-foreground p-6">暂无符合筛选条件的文化概念数据。</TableCell></TableRow>
                              ) : (
                                filteredHolymanConcepts.map((item: any, idx: number) => (
                                  <TableRow key={idx}>
                                    <TableCell className="font-semibold text-xs text-primary">{item.title || item.key || item.word || 'concept'}</TableCell>
                                    <TableCell className="text-xs text-muted-foreground leading-relaxed">{item.summary || item.content || item.meaning || '—'}</TableCell>
                                    <TableCell className="text-center">
                                      <Badge variant="outline" className="text-[9px] font-mono">{item.source || item.category_label || 'holyman_skills'}</Badge>
                                    </TableCell>
                                  </TableRow>
                                ))
                              )}
                            </TableBody>
                          </Table>
                        </div>
                      </div>
                    </TabsContent>

                    {/* Sub-Tab 3: 声音样本与知识 / Examples */}
                    <TabsContent value="examples" className="mt-0">
                      <div className="flex flex-col gap-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <Input
                            className="h-8 max-w-sm text-xs"
                            placeholder="搜索声音样本 / 来源 / 关联口癖..."
                            value={holymanEvidenceSearch}
                            onChange={(event) => setHolymanEvidenceSearch(event.target.value)}
                          />
                          <Badge variant="secondary" className="text-[10px]">已筛选 {filteredHolymanExamples.length} 条</Badge>
                        </div>
                        <div className="overflow-auto rounded-lg border max-h-[340px]">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead className="w-12 text-center">编号</TableHead>
                                <TableHead>声音样本与知识</TableHead>
                                <TableHead className="w-28 text-center">关联口癖</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {filteredHolymanExamples.length === 0 ? (
                                <TableRow><TableCell colSpan={3} className="text-center text-xs text-muted-foreground p-6">暂无符合筛选条件的声音样本与知识数据。</TableCell></TableRow>
                              ) : (
                                filteredHolymanExamples.map((item: any, idx: number) => (
                                  <TableRow key={idx}>
                                    <TableCell className="text-center font-mono text-xs text-slate-500">#{idx + 1}</TableCell>
                                    <TableCell className="text-xs text-muted-foreground leading-relaxed italic">“{item.text || String(item)}”</TableCell>
                                    <TableCell className="text-center">
                                      {item.linked_terms && item.linked_terms.length > 0 ? (
                                        <div className="flex flex-wrap gap-0.5 justify-center">
                                          {item.linked_terms.map((term: string, sIdx: number) => (
                                            <Badge key={sIdx} variant="secondary" className="text-[8px] font-mono">{term}</Badge>
                                          ))}
                                        </div>
                                      ) : <span className="text-[10px] text-slate-500">—</span>}
                                    </TableCell>
                                  </TableRow>
                                ))
                              )}
                            </TableBody>
                          </Table>
                        </div>
                      </div>
                    </TabsContent>

                    {/* Sub-Tab 4: 原始语料 / Corpus */}
                    <TabsContent value="corpus" className="mt-0">
                      <div className="flex flex-col gap-3">
                        <Alert>
                          <AlertCircleIcon />
                          <AlertTitle>原始语料只读展示</AlertTitle>
                          <AlertDescription>
                            当前共 <span className="font-mono text-primary">{holymanCorpus.length || holymanCorpusSummary?.count || 0}</span> 条；该层 reference_only，不直接注入 prompt，也不参与 confirmed 匹配。
                          </AlertDescription>
                        </Alert>
                        <div className="flex flex-wrap items-center gap-2">
                          <Input
                            className="h-8 max-w-sm text-xs"
                            placeholder="搜索原始语料全文 / 来源 / 关联口癖..."
                            value={corpusSearch}
                            onChange={(event) => setCorpusSearch(event.target.value)}
                          />
                          <Badge variant="secondary" className="text-[10px]">
                            已筛选 {filteredHolymanCorpus.length} 条 · 当前显示 {Math.min(visibleHolymanCorpus.length, filteredHolymanCorpus.length)} 条
                          </Badge>
                        </div>
                        {filteredHolymanCorpus.length === 0 ? (
                          <div className="rounded-lg border bg-muted/20 p-6 text-center text-xs text-muted-foreground">
                            暂无符合筛选条件的原始语料。
                          </div>
                        ) : (
                          <ScrollArea className="h-[420px] rounded-lg border bg-muted/10 p-3">
                            <div className="flex flex-col gap-3 pr-3">
                              {visibleHolymanCorpus.map((item: any, idx: number) => {
                                const text = holymanCorpusText(item)
                                const preview = String(item?.preview || (text.length > 220 ? `${text.slice(0, 220)}...` : text))
                                const line = item?.line ?? item?.index ?? idx + 1
                                return (
                                  <Card key={`${line}-${idx}`} className="bg-background/80 border-border/60">
                                    <CardHeader className="py-3">
                                      <div className="flex flex-wrap items-center justify-between gap-2">
                                        <CardTitle className="text-xs font-mono text-primary">Corpus #{line}</CardTitle>
                                        <div className="flex flex-wrap items-center gap-1.5">
                                          <Badge variant="outline" className="text-[9px] font-mono">{item?.source || '神言.txt'}</Badge>
                                          <Badge variant="secondary" className="text-[9px]">{item?.length || text.length} 字</Badge>
                                          <Badge variant="outline" className="text-[9px]">reference_only</Badge>
                                        </div>
                                      </div>
                                    </CardHeader>
                                    <CardContent className="flex flex-col gap-3 pt-0">
                                      <p className="text-xs leading-relaxed text-muted-foreground whitespace-pre-wrap break-words">
                                        {preview}
                                      </p>
                                      {Array.isArray(item?.linked_terms) && item.linked_terms.length > 0 ? (
                                        <div className="flex flex-wrap gap-1">
                                          {item.linked_terms.slice(0, 8).map((term: string, termIdx: number) => (
                                            <Badge key={`${term}-${termIdx}`} variant="secondary" className="text-[8px] font-mono">{term}</Badge>
                                          ))}
                                        </div>
                                      ) : null}
                                      <div className="flex justify-end">
                                        <Button variant="ghost" size="xs" onClick={() => setSelectedCorpusItem(item)}>
                                          <EyeIcon data-icon="inline-start" />
                                          查看全文
                                        </Button>
                                      </div>
                                    </CardContent>
                                  </Card>
                                )
                              })}
                              {visibleHolymanCorpus.length < filteredHolymanCorpus.length ? (
                                <Button variant="outline" size="sm" onClick={() => setCorpusVisibleCount((count) => count + 40)}>
                                  再显示 40 条
                                </Button>
                              ) : null}
                            </div>
                          </ScrollArea>
                        )}
                      </div>
                    </TabsContent>

                    {/* Sub-Tab 5: 待审核候选 / Candidates */}
                    <TabsContent value="candidates" className="mt-0">
                      <div className="flex flex-col gap-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <Input
                            className="h-8 max-w-xs text-xs"
                            placeholder="搜索候选/原因/来源..."
                            value={candidateSearch}
                            onChange={(event) => setCandidateSearch(event.target.value)}
                          />
                          <Select value={candidateStatusFilter} onValueChange={setCandidateStatusFilter}>
                            <SelectTrigger className="h-8 w-36 text-xs">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="all">全部状态</SelectItem>
                              <SelectItem value="pending">待审核</SelectItem>
                              <SelectItem value="approved">已通过</SelectItem>
                              <SelectItem value="rejected">已拒绝</SelectItem>
                            </SelectContent>
                          </Select>
                          <Badge variant="secondary" className="text-[10px]">
                            已筛选 {filteredHolymanCandidates.length} 条 · 已选 {selectedHolymanCandidateIds.length} 条
                          </Badge>
                        </div>

                        <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/20 p-2">
                          <label className="flex items-center gap-2 text-xs text-muted-foreground">
                            <input
                              type="checkbox"
                              checked={allFilteredHolymanCandidatesSelected}
                              onChange={(event) => handleToggleAllFilteredHolymanCandidates(event.target.checked)}
                            />
                            全选当前筛选结果
                          </label>
                          <Button size="xs" disabled={holymanActionLoading || selectedHolymanCandidateIds.length === 0} onClick={() => void handleBatchReviewHolymanCandidates('approve')}>
                            批量通过
                          </Button>
                          <Button size="xs" variant="outline" disabled={holymanActionLoading || selectedHolymanCandidateIds.length === 0} onClick={() => void handleBatchReviewHolymanCandidates('reject')}>
                            批量拒绝并屏蔽
                          </Button>
                          <Button size="xs" variant="ghost" disabled={selectedHolymanCandidateIds.length === 0} onClick={() => setSelectedHolymanCandidateIds([])}>
                            清空选择
                          </Button>
                        </div>

                        <div className="overflow-auto rounded-lg border max-h-[340px]">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead className="w-10">选择</TableHead>
                                <TableHead className="w-36">候选词条 (Word)</TableHead>
                                <TableHead>语料触发频次</TableHead>
                                <TableHead>捕获原因 (Reason)</TableHead>
                                <TableHead className="w-24 text-center">审核状态</TableHead>
                                <TableHead className="w-28 text-right pr-4">操作</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {filteredHolymanCandidates.length === 0 ? (
                                <TableRow><TableCell colSpan={6} className="text-center text-xs text-muted-foreground p-6">💡 暂无符合筛选条件的待审核候选。</TableCell></TableRow>
                              ) : (
                                filteredHolymanCandidates.map((item: any, idx: number) => {
                                  const key = holymanCandidateKey(item)
                                  return (
                                    <TableRow key={`${key}-${idx}`}>
                                      <TableCell>
                                        <input
                                          type="checkbox"
                                          checked={selectedCandidateIdSet.has(key)}
                                          onChange={(event) => handleToggleHolymanCandidateSelection(key, event.target.checked)}
                                        />
                                      </TableCell>
                                      <TableCell className="font-semibold text-xs text-foreground font-mono">{item.word}</TableCell>
                                      <TableCell className="font-mono text-xs text-primary">{item.count || 1} 次</TableCell>
                                      <TableCell className="text-xs text-muted-foreground truncate max-w-xs" title={item.reason}>{item.reason || '自动捕获'}</TableCell>
                                      <TableCell className="text-center">
                                        <Badge variant="outline" className="text-[9px] bg-amber-500/10 text-amber-500 border-amber-500/20">{isHolymanPendingStatus(item.status) ? '待审核' : item.status}</Badge>
                                      </TableCell>
                                      <TableCell className="text-right pr-4">
                                        <div className="flex justify-end gap-1.5">
                                          <Button size="xs" variant="secondary" className="bg-emerald-500/10 text-emerald-500 border border-emerald-500/10 hover:bg-emerald-500/20" disabled={holymanActionLoading} onClick={() => void handleReviewCandidateSingle(item, 'approve')}>通过</Button>
                                          <Button size="xs" variant="outline" className="border-red-500/20 text-destructive hover:bg-destructive/10" disabled={holymanActionLoading} onClick={() => void handleReviewCandidateSingle(item, 'reject')}>拒绝</Button>
                                        </div>
                                      </TableCell>
                                    </TableRow>
                                  )
                                })
                              )}
                            </TableBody>
                          </Table>
                        </div>
                      </div>
                    </TabsContent>

                    {/* Sub-Tab 6: 屏蔽项 */}
                    <TabsContent value="blocked" className="mt-0">
                      <div className="flex flex-col gap-4">
                        <div className="flex items-center gap-2 max-w-md">
                          <Input
                            placeholder="手动添加要过滤拦截屏蔽的敏感黑话词..."
                            className="h-9 text-xs"
                            value={newBlockWord}
                            onChange={(event) => setNewBlockWord(event.target.value)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter') void handleAddBlocklist(newBlockWord)
                            }}
                          />
                          <Button size="sm" disabled={holymanActionLoading || !newBlockWord.trim()} onClick={() => void handleAddBlocklist(newBlockWord)}>🚫 添加拦截</Button>
                        </div>
                        <div className="overflow-auto rounded-lg border max-h-[300px]">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead className="w-36">已被过滤屏蔽词条</TableHead>
                                <TableHead>封禁理由 (Reason)</TableHead>
                                <TableHead className="w-32">封禁源</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {Object.keys(holymanBlocked).length === 0 ? (
                                <TableRow><TableCell colSpan={3} className="text-center text-xs text-muted-foreground p-6">屏蔽项为空，没有词条被过滤。</TableCell></TableRow>
                              ) : (
                                Object.entries(holymanBlocked).map(([word, reason]: any, idx: number) => (
                                  <TableRow key={idx}>
                                    <TableCell className="font-semibold text-xs text-destructive font-mono">{word}</TableCell>
                                    <TableCell className="text-xs text-muted-foreground">{reason || '手动封禁'}</TableCell>
                                    <TableCell className="text-xs text-slate-500 font-mono">holyman_blocklist</TableCell>
                                  </TableRow>
                                ))
                              )}
                            </TableBody>
                          </Table>
                        </div>
                      </div>
                    </TabsContent>
                  </Tabs>
                </div>
              ) : (
                <div className="py-12 flex justify-center text-xs text-muted-foreground">无法获取广域语料资产库。</div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* ─── 证据气泡还原 Dialog ─── */}
      <Dialog open={evidenceOpen} onOpenChange={setEvidenceOpen}>
        <DialogContent className="sm:max-w-2xl flex flex-col gap-0 h-[80vh]">
          <DialogHeader className="pb-3 border-b shrink-0 pr-6">
            <DialogTitle>黑话获取证据还原</DialogTitle>
            <DialogDescription>还原该条群聊黑话被自动捕获、提取释义时前后的多轮发言上下文。</DialogDescription>
          </DialogHeader>

          {evidenceLoading ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-2">
              <Loader2Icon className="animate-spin text-primary size-5" />
              <span className="text-xs text-muted-foreground font-mono">正在拉取多轮聊天快照，还原对话气泡...</span>
            </div>
          ) : evidenceData ? (
            <div className="flex-1 flex flex-col min-h-0">
              <div className="p-3 bg-muted/40 border-b flex flex-wrap items-center gap-3 text-xs justify-between shrink-0">
                <div className="flex items-center gap-2">
                  <label className="flex items-center gap-1">前：
                    <input type="number" className="input text-xs w-14 h-7" min="0" max="50" value={evidenceBefore} onChange={(e) => setEvidenceBefore(Number(e.target.value) || 0)} />
                  </label>
                  <label className="flex items-center gap-1">后：
                    <input type="number" className="input text-xs w-14 h-7" min="0" max="50" value={evidenceAfter} onChange={(e) => setEvidenceAfter(Number(e.target.value) || 0)} />
                  </label>
                  <Button size="xs" onClick={() => evidenceId && void handleOpenEvidence(evidenceId, evidenceBefore, evidenceAfter)}>刷新</Button>
                </div>
                <Badge variant={evidenceData.used_fallback ? 'destructive' : 'secondary'}>
                  {evidenceData.used_fallback ? '降级静态快照' : '动态事件溯源'}
                </Badge>
              </div>

              {evidenceData.anchor ? (
                <div className="p-3 bg-primary/5 border-b shrink-0">
                  <span className="text-[10px] text-primary block mb-0.5 font-medium">锚定提取记忆词条</span>
                  <p className="text-xs text-foreground font-mono leading-relaxed">{evidenceData.anchor.content}</p>
                </div>
              ) : null}

              <ScrollArea className="flex-1 p-4 bg-muted/10">
                <div className="flex flex-col gap-4">
                  {evidenceData.messages?.length === 0 ? (
                    <p className="text-sm text-muted-foreground py-12 text-center">无法还原上下文，该条黑话属于手动新建或对应的语料已被归档。</p>
                  ) : (
                    evidenceData.messages.map((msg: any, index: number) => {
                      const isAnchor = msg.role === 'anchor'
                      const isBot = msg.sender_id === '2500447291' || msg.sender_id === '1336495069' || String(msg.sender_name).includes('AI') || String(msg.sender_name).includes('Bot')
                      
                      return (
                        <div key={`${msg.id}-${index}`} className={`flex flex-col max-w-[85%] ${isBot ? 'ml-auto items-end' : 'mr-auto items-start'}`}>
                          <span className="text-[9px] text-muted-foreground font-mono mb-1">
                            {msg.sender_name || msg.sender_id} · {formatTime(msg.timestamp)}
                          </span>
                          <div className={`rounded-2xl px-3.5 py-2 text-xs leading-relaxed border ${
                            isAnchor 
                              ? 'bg-amber-500/10 border-amber-500/20 text-foreground dark:text-foreground' 
                              : isBot 
                                ? 'bg-primary text-primary-foreground border-transparent' 
                                : 'bg-background border-border/50 text-foreground'
                          }`}>
                            {msg.content}
                          </div>
                          {isAnchor ? (
                            <span className="text-[9px] text-amber-500 font-bold mt-1 font-mono uppercase">★ 捕获提取点</span>
                          ) : null}
                        </div>
                      )
                    })
                  )}
                </div>
              </ScrollArea>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>

      {/* ─── 新建/编辑黑话 Dialog ─── */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{isEditNew ? '新建黑话词条' : `编辑黑话词条 #${editForm.id}`}</DialogTitle>
          </DialogHeader>

          <form className="flex flex-col gap-4 py-4" onSubmit={(e) => { e.preventDefault(); void handleSaveEdit(); }}>
            <FieldGroup className="grid gap-4">
              <Field>
                <FieldLabel>黑话词条</FieldLabel>
                <Input
                  disabled={!isEditNew}
                  value={editForm.word || ''}
                  onChange={(e) => setEditForm({ ...editForm, word: e.target.value })}
                  placeholder="如 v我50"
                />
              </Field>

              <Field>
                <FieldLabel>黑话释义</FieldLabel>
                <Textarea
                  rows={3}
                  value={editForm.meaning || ''}
                  onChange={(e) => setEditForm({ ...editForm, meaning: e.target.value })}
                  placeholder="输入解释含义..."
                />
              </Field>

              {isEditNew ? (
                <Field>
                  <FieldLabel>绑定群号（留空则全局）</FieldLabel>
                  <Input
                    value={editForm.group_id || ''}
                    onChange={(e) => setEditForm({ ...editForm, group_id: e.target.value })}
                    placeholder="如 123456"
                  />
                </Field>
              ) : null}
            </FieldGroup>

            <div className="flex gap-2 justify-end border-t pt-3 mt-2">
              <Button variant="outline" type="button" onClick={() => setEditOpen(false)}>取消</Button>
              <Button disabled={saving} type="submit">
                {saving ? <Loader2Icon className="animate-spin" data-icon="inline-start" /> : null}
                保存
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {/* ─── 原始语料全文详情 Dialog ─── */}
      <Dialog open={Boolean(selectedCorpusItem)} onOpenChange={(open) => { if (!open) setSelectedCorpusItem(null) }}>
        <DialogContent className="sm:max-w-3xl flex flex-col gap-0 h-[80vh]">
          <DialogHeader className="pb-3 border-b shrink-0 pr-6">
            <DialogTitle>原始语料全文详情</DialogTitle>
            <DialogDescription>Corpus 层为 reference_only，只读展示，不直接注入 prompt，也不参与 confirmed 匹配。</DialogDescription>
          </DialogHeader>
          {selectedCorpusItem ? (
            <div className="flex flex-col gap-3 py-4 min-h-0">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary" className="font-mono">#{selectedCorpusItem.line ?? selectedCorpusItem.index ?? selectedCorpusItem.id}</Badge>
                <Badge variant="outline" className="font-mono">{selectedCorpusItem.source || '神言.txt'}</Badge>
                <Badge variant="outline">reference_only</Badge>
                <Badge variant="outline">safe_for_prompt=false</Badge>
              </div>
              <ScrollArea className="flex-1 rounded-lg border bg-muted/20 p-4">
                <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-muted-foreground">
                  {holymanCorpusText(selectedCorpusItem)}
                </p>
              </ScrollArea>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>

      {/* ─── Holyman 同步预览确认 Dialog ─── */}
      <Dialog open={syncPreviewOpen} onOpenChange={setSyncPreviewOpen}>
        <DialogContent className="sm:max-w-4xl flex flex-col gap-0 max-h-[86vh]">
          <DialogHeader className="pb-3 border-b shrink-0 pr-6">
            <DialogTitle>Holyman 语料同步预览</DialogTitle>
            <DialogDescription>先对比本地与远端版本、内容数量和风险项；确认后才会执行真实写入。</DialogDescription>
          </DialogHeader>

          {syncPreviewLoading ? (
            <div className="flex flex-col items-center justify-center gap-3 py-12 text-sm text-muted-foreground">
              <Loader2Icon className="animate-spin text-primary" />
              正在读取 GitHub 远端内容并生成差异预览...
            </div>
          ) : syncPreview ? (
            <div className="flex flex-col gap-4 py-4 min-h-0">
              <div className="grid gap-3 md:grid-cols-2">
                <Card className="bg-muted/10">
                  <CardHeader className="py-3">
                    <CardDescription>本地版本</CardDescription>
                    <CardTitle className="text-sm font-mono break-all">{syncPreview.local_version || 'Unknown'}</CardTitle>
                    <CardDescription className="font-mono break-all">hash: {syncPreview.local_content_hash || '—'}</CardDescription>
                  </CardHeader>
                </Card>
                <Card className="bg-muted/10">
                  <CardHeader className="py-3">
                    <CardDescription>远端版本</CardDescription>
                    <CardTitle className="text-sm font-mono break-all">{syncPreview.remote_version || 'Unknown'}</CardTitle>
                    <CardDescription className="font-mono break-all">hash: {syncPreview.remote_content_hash || '—'}</CardDescription>
                  </CardHeader>
                </Card>
              </div>

              <Alert className={syncPreview.will_update ? 'bg-amber-500/10 border-amber-500/20 text-amber-500' : 'bg-emerald-500/10 border-emerald-500/20 text-emerald-500'}>
                {syncPreview.will_update ? <AlertCircleIcon /> : <CheckCircle2Icon />}
                <AlertTitle>{syncPreview.will_update ? '检测到内容差异' : '未检测到内容差异'}</AlertTitle>
                <AlertDescription>
                  远端质量状态：{syncPreview.asset_status || 'unknown'}。{syncPreview.safety?.statement || '确认前不会写入本地资产。'}
                </AlertDescription>
              </Alert>

              <ScrollArea className="min-h-0 max-h-[420px] pr-3">
                <div className="flex flex-col gap-4">
                  <div className="rounded-lg border overflow-hidden">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>资产层</TableHead>
                          <TableHead className="text-right">本地</TableHead>
                          <TableHead className="text-right">远端</TableHead>
                          <TableHead className="text-right">变化</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {Object.keys({ ...(syncPreview.local_counts || {}), ...(syncPreview.remote_counts || {}) }).map((key) => {
                          const delta = Number(syncPreview.delta_counts?.[key] || 0)
                          return (
                            <TableRow key={key}>
                              <TableCell className="text-xs font-medium">{syncCountLabel(key)}</TableCell>
                              <TableCell className="text-right font-mono text-xs">{syncPreview.local_counts?.[key] ?? 0}</TableCell>
                              <TableCell className="text-right font-mono text-xs">{syncPreview.remote_counts?.[key] ?? 0}</TableCell>
                              <TableCell className={delta > 0 ? 'text-right font-mono text-xs text-emerald-500' : delta < 0 ? 'text-right font-mono text-xs text-destructive' : 'text-right font-mono text-xs text-muted-foreground'}>{syncDeltaText(delta)}</TableCell>
                            </TableRow>
                          )
                        })}
                      </TableBody>
                    </Table>
                  </div>

                  <div className="grid gap-3 md:grid-cols-3">
                    {[
                      ['新增口癖', syncPreview.samples?.added_phrases || []],
                      ['变更口癖', syncPreview.samples?.changed_phrases || []],
                      ['移除口癖', syncPreview.samples?.removed_phrases || []],
                    ].map(([title, words]: any) => (
                      <Card key={title} className="bg-muted/10">
                        <CardHeader className="py-3">
                          <CardTitle className="text-xs">{title}</CardTitle>
                          <CardDescription>{words.length ? '仅展示前 12 条样本' : '无样本变化'}</CardDescription>
                        </CardHeader>
                        <CardContent className="flex flex-wrap gap-1 pt-0">
                          {words.length ? words.map((word: string) => <Badge key={word} variant="secondary" className="text-[9px] font-mono">{word}</Badge>) : <span className="text-xs text-muted-foreground">—</span>}
                        </CardContent>
                      </Card>
                    ))}
                  </div>

                  <Alert>
                    <AlertCircleIcon />
                    <AlertTitle>安全性声明</AlertTitle>
                    <AlertDescription>
                      同步后的 Holyman 资产仍是 global_jargon_reference；原始语料保持 reference_only、safe_for_prompt=false。只有精选口癖中 runtime_match=true 的条目可被人工启用为理解提示。
                    </AlertDescription>
                  </Alert>
                </div>
              </ScrollArea>
            </div>
          ) : (
            <div className="py-8 text-center text-sm text-muted-foreground">暂无预览数据。</div>
          )}

          <DialogFooter>
            <Button variant="outline" disabled={syncPreviewLoading || syncConfirming} onClick={() => setSyncPreviewOpen(false)}>取消</Button>
            <Button disabled={syncPreviewLoading || syncConfirming || !syncPreview?.ok || syncPreview?.asset_status !== 'ready'} onClick={() => void handleConfirmSyncHolyman()}>
              {syncConfirming ? <Loader2Icon className="animate-spin" data-icon="inline-start" /> : <RefreshCwIcon data-icon="inline-start" />}
              确认同步并写入
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ─── 广域同步结果弹窗 ─── */}
      <Dialog open={streamOpen} onOpenChange={setStreamOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Holyman 语料同步引擎</DialogTitle>
            <DialogDescription>已通过预览确认后拉取、清洗并写入最新的原始资产数据库。</DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-4 py-4">
            {streamProgress ? (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-primary font-semibold">
                    进度：{Math.round(streamProgress.progress * 100)}%
                  </span>
                  <span>
                    已写入: {streamProgress.processed}/{streamProgress.total}
                  </span>
                </div>
                <div className="w-full h-2 rounded-full overflow-hidden bg-muted">
                  <div className="h-full rounded-full bg-primary" style={{ width: `${streamProgress.progress * 100}%` }} />
                </div>
              </div>
            ) : (
              <div className="flex items-center gap-2 text-xs text-muted-foreground py-2 font-mono">
                <Loader2Icon className="animate-spin text-primary size-4 shrink-0" />
                正在与 GitHub 同步线程握手...
              </div>
            )}

            <div className="flex flex-col gap-1.5">
              <ScrollArea className="h-44 rounded-lg border bg-muted/60 p-3 font-mono text-[10px] text-muted-foreground leading-relaxed">
                {streamLog.length === 0 ? (
                  <div>等待流式数据...</div>
                ) : (
                  streamLog.map((line, idx) => (
                    <div key={idx} className={line.includes('[ERROR]') ? 'text-destructive' : line.includes('[SUCCESS]') ? 'text-emerald-500' : ''}>
                      {line}
                    </div>
                  ))
                )}
              </ScrollArea>
            </div>

            {streamProgress?.done && !streamProgress.error ? (
              <Alert className="bg-emerald-500/10 border-emerald-500/20 text-emerald-500">
                <CheckCircle2Icon />
                <AlertTitle>同步成功</AlertTitle>
                <AlertDescription>已完成分层语料的大版本重构并入库。</AlertDescription>
              </Alert>
            ) : null}
            {streamProgress?.error ? (
              <Alert className="bg-destructive/10 border-destructive/20 text-destructive">
                <AlertCircleIcon />
                <AlertTitle>同步失败</AlertTitle>
                <AlertDescription>{streamProgress.error}</AlertDescription>
              </Alert>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
export default JargonPage
