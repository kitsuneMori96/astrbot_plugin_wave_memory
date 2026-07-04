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
  batchDeleteJargons,
  batchReviewJargons,
  createJargon,
  deleteJargon,
  getHolymanStatus,
  getJargonEvidence,
  listJargons,
  reviewJargon,
  toggleJargonGlobal,
  updateJargon,
  type JargonItem,
  type HolymanStatusPayload,
} from '@/api/jargon'
import { runPostStream, type StreamProgress } from '@/api/memories'
import { getStoredToken } from '@/api/client'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
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

  // 2. 广域 Holyman 同步与列表展示
  const [holyman, setHolyman] = useState<HolymanStatusPayload | null>(null)
  const [holymanLoading, setHolymanLoading] = useState(false)
  const [streamOpen, setStreamOpen] = useState(false)
  const [streamProgress, setStreamProgress] = useState<StreamProgress | null>(null)
  const [streamLog, setStreamLog] = useState<string[]>([])

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

  async function loadHolyman() {
    setHolymanLoading(true)
    try {
      const res = await getHolymanStatus()
      setHolyman(res)
    } catch (e) {
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
  }, [activeTab, status, size])

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
    } catch (err) {
      toast.error('快捷保存失败')
    }
  }

  // 单行审核确认/否决
  async function handleReviewSingle(id: number, action: 'approve' | 'reject') {
    try {
      await reviewJargon(id, action)
      toast.success(action === 'approve' ? `黑话已通过审核并确认` : `已否决并拉黑该黑话`)
      await loadLocalJargons(page)
    } catch (err) {
      toast.error('审核失败')
    }
  }

  // 单行切换全局/群限制
  async function handleToggleGlobalSingle(id: number) {
    try {
      const res = await toggleJargonGlobal(id)
      toast.success(res.is_global ? '已设为全局可用' : '已限制为群专用')
      await loadLocalJargons(page)
    } catch (err) {
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
    } catch (err) {
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
    } catch (err) {
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
    } catch (err) {
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
    } catch (err) {
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
    } catch (err) {
      toast.error('证据加载失败')
    } finally {
      setEvidenceLoading(false)
    }
  }

  // 触发广域 Holyman GitHub 同步 (SSE 流)
  function handleSyncHolyman() {
    setStreamLog([])
    setStreamProgress(null)
    setStreamOpen(true)
    setStreamLog((prev) => [...prev, '[INIT] 正在连接 GitHub CDN 拉取最新 Holyman 灵魂设定与分层词包...'])

    void runPostStream('/api/jargon/holyman/sync', [], (state) => {
      setStreamProgress(state)
      if (state.processed !== undefined) {
        setStreamLog((prev) => [
          ...prev,
          `[SYNC] 同步进度: ${Math.round(state.progress * 100)}% | 已写入: ${state.processed}/${state.total}`,
        ].slice(-60))
      }
      if (state.done) {
        setStreamLog((prev) => [...prev, '[SUCCESS] 广域 Holyman 语料分层数据库同步覆写 100% 完成！'])
        toast.success('Holyman 数据源同步成功')
        void loadHolyman()
      }
      if (state.error) {
        setStreamLog((prev) => [...prev, `[ERROR] 同步失败: ${state.error}`])
        toast.error(`同步中断: ${state.error}`)
      }
    }).catch((err) => {
      const msg = err instanceof Error ? err.message : '连接异常'
      setStreamLog((prev) => [...prev, `[CRITICAL] 网络断开: ${msg}`])
      toast.error(`连接失败: ${msg}`)
    })
  }

  const isAllSelected = jargons.length > 0 && selectedIds.length === jargons.length
  const totalPages = Math.ceil(total / size) || 1

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader className="py-4 shrink-0 border-b bg-muted/10">
          <CardTitle>黑话与口癖审核</CardTitle>
          <CardDescription>
            对聊天中自动捕获、清洗的待审黑话词条进行人工裁决核对，并支持一键通过 API 导入广域人格口癖语料同步。
          </CardDescription>
        </CardHeader>
      </Card>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid w-full grid-cols-2 max-w-md shrink-0">
          <TabsTrigger value="local">群聊本地黑话</TabsTrigger>
          <TabsTrigger value="global">广域人设语料库 (Holyman)</TabsTrigger>
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
                <CardTitle className="text-sm font-semibold">🌌 广域人设自适应口癖库 (Holyman)</CardTitle>
                <CardDescription>
                  内置广域人设资料与经典黑话语料，无需手工采集，一键同步更新后即可使机器人具备广域理解基础。
                </CardDescription>
              </div>
              <Button disabled={holymanLoading} onClick={handleSyncHolyman}>
                {holymanLoading ? <Loader2Icon className="animate-spin" data-icon="inline-start" /> : <RefreshCwIcon data-icon="inline-start" />}
                🔄 在线同步更新 (GitHub)
              </Button>
            </CardHeader>
            <CardContent className="pt-6">
              {holymanLoading ? (
                <div className="py-12 flex justify-center"><Loader2Icon className="animate-spin text-primary size-6" /></div>
              ) : holyman ? (
                <div className="flex flex-col gap-6">
                  {holyman.update_available ? (
                    <Alert className="bg-amber-500/10 border-amber-500/20 text-amber-500">
                      <AlertCircleIcon />
                      <AlertTitle>语料资产有可用更新</AlertTitle>
                      <AlertDescription>
                        本地版本：{holyman.local_version}，最新版本：{holyman.remote_version}。建议立即点击同步覆盖。
                      </AlertDescription>
                    </Alert>
                  ) : (
                    <Alert className="bg-emerald-500/10 border-emerald-500/20 text-emerald-500">
                      <CheckCircle2Icon className="text-emerald-500" />
                      <AlertTitle>本地人设语料库为最新</AlertTitle>
                      <AlertDescription>
                        本地 Holyman 版本为最新的 {holyman.local_version}，无需更新。
                      </AlertDescription>
                    </Alert>
                  )}

                  <div className="grid gap-4 sm:grid-cols-4">
                    <Card className="bg-muted/10 border-border/50"><CardHeader className="py-3"><CardDescription className="text-xs">精选口癖 (quotes)</CardDescription><CardTitle className="text-xl font-mono text-primary">{holyman.items_count} 条</CardTitle></CardHeader></Card>
                    <Card className="bg-muted/10 border-border/50"><CardHeader className="py-3"><CardDescription className="text-xs">人格设定 (concepts)</CardDescription><CardTitle className="text-xl font-mono text-primary">{holyman.concepts_count} 组</CardTitle></CardHeader></Card>
                    <Card className="bg-muted/10 border-border/50"><CardHeader className="py-3"><CardDescription className="text-xs">语录例句 (examples)</CardDescription><CardTitle className="text-xl font-mono text-primary">{holyman.examples_count} 句</CardTitle></CardHeader></Card>
                    <Card className="bg-muted/10 border-border/50"><CardHeader className="py-3"><CardDescription className="text-xs">分层语料 (corpus)</CardDescription><CardTitle className="text-xl font-mono text-primary">{holyman.corpus_count} 段</CardTitle></CardHeader></Card>
                  </div>

                  {/* 广域黑话口癖列表展示 */}
                  <div className="mt-2">
                    <h4 className="text-sm font-semibold text-foreground mb-3 flex items-center gap-1.5">
                      <LayersIcon className="size-4 text-primary" />
                      已加载的广域口癖参考列表
                    </h4>
                    <div className="overflow-auto rounded-lg border max-h-96">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead className="w-36">词条</TableHead>
                            <TableHead>释义 / 心智映射</TableHead>
                            <TableHead className="w-24 text-center">分类</TableHead>
                            <TableHead className="w-24 text-center">动作状态</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {(holyman.phrases ?? []).slice(0, 150).map((item: any, idx: number) => (
                            <TableRow key={idx}>
                              <TableCell className="font-semibold text-xs text-foreground font-mono">{item.word}</TableCell>
                              <TableCell className="text-xs text-muted-foreground">{item.meaning || '—'}</TableCell>
                              <TableCell className="text-center">
                                <Badge variant="secondary" className="text-[9px] whitespace-nowrap">{item.category_label || item.category}</Badge>
                              </TableCell>
                              <TableCell className="text-center">
                                <Badge variant={item.is_activated ? 'default' : 'outline'} className="text-[9px] bg-primary/10 text-primary border-primary/20">
                                  {item.is_activated ? '已激活' : '内置理解'}
                                </Badge>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                    <div className="text-[10px] text-muted-foreground mt-2 text-right">
                      * 仅展示前 150 条内置语料。所有语料均已写入后台理解矩阵，在聊天时自然起效。
                    </div>
                  </div>
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

      {/* ─── 广域 SSE 同步控制弹窗 ─── */}
      <Dialog open={streamOpen} onOpenChange={setStreamOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Holyman 语料同步引擎</DialogTitle>
            <DialogDescription>正在通过 GitHub 流式拉取、清洗并写入最新的原始资产数据库...</DialogDescription>
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

            {streamProgress?.done ? (
              <Alert className="bg-emerald-500/10 border-emerald-500/20 text-emerald-500">
                <CheckCircle2Icon className="size-4" />
                <AlertTitle>同步成功</AlertTitle>
                <AlertDescription>已完成分层语料的大版本重构并入库。</AlertDescription>
              </Alert>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
export default JargonPage
