import { useEffect, useState } from 'react'
import {
  FileEdit,
  Loader2,
  Search,
  Trash2,
  Undo2,
  EyeIcon,
} from 'lucide-react'
import { toast } from 'sonner'

import {
  approveBelief,
  archiveBelief,
  batchApproveBeliefs,
  batchArchiveBeliefsLegacy,
  batchArchiveSelectedBeliefs,
  batchDeleteBeliefs,
  createBelief,
  deleteBelief,
  getBeliefEvidence,
  listBeliefs,
  updateBelief,
  type BeliefItem,
  type EvidencePayload,
} from '@/api/beliefs'
import { getStoredToken } from '@/api/client'
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

// 辅助：获取信念类型的标签颜色
function beliefTypeBadge(type: string): string {
  switch (type) {
    case 'self':
      return 'bg-purple-500/10 text-purple-500 border-purple-500/20 hover:bg-purple-500/10'
    case 'world':
      return 'bg-blue-500/10 text-blue-500 border-blue-500/20 hover:bg-blue-500/10'
    case 'value':
      return 'bg-pink-500/10 text-pink-500 border-pink-500/20 hover:bg-pink-500/10'
    default:
      return 'bg-amber-500/10 text-amber-500 border-amber-500/20 hover:bg-amber-500/10'
  }
}

// 辅助：获取状态标签
function beliefStatusBadge(status: string): string {
  switch (status) {
    case 'active':
      return 'bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/10 border-emerald-500/20'
    case 'pending':
      return 'bg-amber-500/10 text-amber-500 hover:bg-amber-500/10 border-amber-500/20'
    case 'archived':
      return 'bg-muted text-muted-foreground border-border/50'
    default:
      return 'bg-indigo-500/10 text-indigo-500 hover:bg-indigo-500/10 border-indigo-500/20'
  }
}

export function BeliefsPage() {
  const [beliefs, setBeliefs] = useState<BeliefItem[]>([])
  const [total, setTotal] = useState(0)
  const [pendingCount, setPendingCount] = useState(0)
  
  // 筛选检索与单页大小
  const [search, setSearch] = useState('')
  const [type, setType] = useState('')
  const [status, setStatus] = useState('')
  const [botId, setBotId] = useState('')
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(15) // 单页行数

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  // 多选勾选与跨页全选全部匹配
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [selectAllMatching, setSelectAllMatching] = useState(false)

  // 证据追溯 Dialog 弹窗
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [evidenceId, setEvidenceId] = useState<number | null>(null)
  const [evidenceData, setEvidenceData] = useState<EvidencePayload | null>(null)
  const [evidenceBefore, setEvidenceBefore] = useState(15) // 上下文数默认拓宽
  const [evidenceAfter, setEvidenceAfter] = useState(15)
  const [evidenceLoading, setEvidenceLoading] = useState(false)
  
  // 证据弹窗子分类 Mini-Tabs
  const [evidenceSubTab, setEvidenceSubTab] = useState<'relationship_event' | 'episode' | 'memory'>('memory')

  // 新增/编辑信念弹窗
  const [editOpen, setEditOpen] = useState(false)
  const [editForm, setEditForm] = useState<Partial<BeliefItem>>({
    content: '',
    type: 'self',
    status: 'pending',
    bot_id: 'bot',
    confidence: 1.0,
  })
  const [isEditNew, setIsEditNew] = useState(true)

  async function loadData(nextPage = page) {
    setLoading(true)
    try {
      const res = await listBeliefs({
        page: nextPage,
        size,
        type: type === 'all' ? '' : type,
        status: status === 'all' ? '' : status,
        bot_id: botId === 'all' ? '' : botId,
        search,
      })
      setBeliefs(res.items ?? [])
      setTotal(res.total ?? 0)
      setPendingCount(res.pending_count ?? 0)
      setPage(nextPage)
      setSelectedIds([])
      setSelectAllMatching(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '信念数据加载失败')
      setBeliefs([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadData(1)
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [type, status, botId, size])

  // 执行搜索
  function handleSearchSubmit(e?: React.FormEvent) {
    if (e) e.preventDefault()
    void loadData(1)
  }

  // 重置过滤
  function handleResetFilters() {
    setSearch('')
    setType('')
    setStatus('')
    setBotId('')
    void loadData(1)
  }

  // 全选/反选当页
  function handleToggleSelectAll(checked: boolean) {
    setSelectAllMatching(false)
    if (checked) {
      setSelectedIds(beliefs.map((b) => b.id))
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

  // 跨页全选全部匹配
  function handleSelectAllMatching() {
    setSelectAllMatching(true)
    setSelectedIds(beliefs.map((b) => b.id))
    toast.info(`已选中全部符合检索条件的 ${total} 条心智信念（跨页全选已激活）`)
  }

  // 1. 通过审核
  async function handleApproveSingle(id: number) {
    try {
      await approveBelief(id)
      toast.success(`信念已确认通过并正式生效`)
      await loadData(page)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '审核失败')
    }
  }

  // 2. 一键归档
  async function handleArchiveSingle(id: number) {
    try {
      await archiveBelief(id)
      toast.success(`信念已安全归档`)
      await loadData(page)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '归档失败')
    }
  }

  // 3. 物理删除
  async function handleDeleteSingle(id: number) {
    if (!confirm(`确定要永久物理擦除信念 #${id} 吗？这会导致对应的关系演进证据被解绑！`)) return
    try {
      await deleteBelief(id)
      toast.success(`信念已被删除`)
      await loadData(page)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  // 4. 批量通过 (支持跨页全选匹配)
  async function handleBatchApprove() {
    const count = selectedIds.length
    if (!count) return
    setLoading(true)
    try {
      if (selectAllMatching) {
        const token = getStoredToken()
        const headers: HeadersInit = token 
          ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } 
          : { 'Content-Type': 'application/json' }
        const res = await fetch('/api/beliefs/batch-approve', {
          method: 'POST',
          headers,
          body: JSON.stringify({
            all_matching: true,
            type: type === 'all' ? '' : type,
            status: status === 'all' ? '' : status,
            bot_id: botId === 'all' ? '' : botId,
            search,
          })
        })
        const data = await res.json() as any
        toast.success(`一键批量审核通过了全部 ${data.approved_count ?? 0} 条心智信念`)
      } else {
        await batchApproveBeliefs(selectedIds)
        toast.success(`成功批量审核通过 ${count} 条信念`)
      }
      await loadData(page)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败')
      setLoading(false)
    }
  }

  // 5. 批量归档 (支持跨页全选匹配)
  async function handleBatchArchive() {
    const count = selectedIds.length
    if (!count) return
    setLoading(true)
    try {
      if (selectAllMatching) {
        const token = getStoredToken()
        const headers: HeadersInit = token 
          ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } 
          : { 'Content-Type': 'application/json' }
        const res = await fetch('/api/beliefs/batch-archive-selected', {
          method: 'POST',
          headers,
          body: JSON.stringify({
            all_matching: true,
            type: type === 'all' ? '' : type,
            status: status === 'all' ? '' : status,
            bot_id: botId === 'all' ? '' : botId,
            search,
          })
        })
        const data = await res.json() as any
        toast.success(`一键批量归档了全部 ${data.archived_count ?? 0} 条匹配信念`)
      } else {
        await batchArchiveSelectedBeliefs(selectedIds)
        toast.success(`成功批量归档了 ${count} 条信念`)
      }
      await loadData(page)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败')
      setLoading(false)
    }
  }

  // 6. 批量删除 (支持跨页全选匹配)
  async function handleBatchDelete() {
    const count = selectedIds.length
    if (!count) return
    if (!confirm(`确定要批量删除选中的这 ${count} 条信念吗？`)) return
    setLoading(true)
    try {
      if (selectAllMatching) {
        const token = getStoredToken()
        const headers: HeadersInit = token 
          ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } 
          : { 'Content-Type': 'application/json' }
        const res = await fetch('/api/beliefs/batch-delete', {
          method: 'POST',
          headers,
          body: JSON.stringify({
            all_matching: true,
            type: type === 'all' ? '' : type,
            status: status === 'all' ? '' : status,
            bot_id: botId === 'all' ? '' : botId,
            search,
          })
        })
        const data = await res.json() as any
        toast.success(`一键批量彻底删除了全部 ${data.deleted_count ?? 0} 条心智信念`)
      } else {
        await batchDeleteBeliefs(selectedIds)
        toast.success(`成功批量删除了 ${count} 条信念`)
      }
      await loadData(page)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败')
      setLoading(false)
    }
  }

  // 7. 一键归档旧遗产 ( pending_legacy )
  async function handleArchiveLegacy() {
    if (!confirm('确定要一键将所有 status=\'pending_legacy\' 的旧遗产信念批量归档吗？（这通常是上个版本导入的数据）')) return
    setLoading(true)
    try {
      const res = await batchArchiveBeliefsLegacy()
      toast.success(`成功将 ${res.archived} 条旧遗产信念一键归档`)
      await loadData(page)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '归档失败')
      setLoading(false)
    }
  }

  // 8. 调取证据链并进行聊天气泡还原与高阶 BDI 分析
  async function handleOpenEvidence(id: number, b = evidenceBefore, a = evidenceAfter) {
    setEvidenceOpen(true)
    setEvidenceId(id)
    setEvidenceLoading(true)
    setEvidenceData(null)
    try {
      const res = await getBeliefEvidence(id, b, a)
      setEvidenceData(res)
      
      // 自适应定位到最优子分类 Tab
      if (res.relationship_events && res.relationship_events.length > 0) {
        setEvidenceSubTab('relationship_event')
      } else if (res.episodes && res.episodes.length > 0) {
        setEvidenceSubTab('episode')
      } else {
        setEvidenceSubTab('memory')
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '加载证据链失败')
    } finally {
      setEvidenceLoading(false)
    }
  }

  // 新增 / 编辑保存
  async function handleSaveEdit() {
    setSaving(true)
    try {
      if (isEditNew) {
        await createBelief(editForm)
        toast.success('自定义信念新建并入库成功')
      } else {
        if (editForm.id) {
          await updateBelief(editForm.id, editForm)
          toast.success(`修改信念 #${editForm.id} 成功`)
        }
      }
      setEditOpen(false)
      await loadData(page)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败')
    } finally {
      setSaving(false)
    }
  }

  function handleOpenCreate() {
    setIsEditNew(true)
    setEditForm({
      content: '',
      type: 'self',
      status: 'pending',
      bot_id: 'bot',
      confidence: 1.0,
    })
    setEditOpen(true)
  }

  function handleOpenEdit(b: BeliefItem) {
    setIsEditNew(false)
    setEditForm(JSON.parse(JSON.stringify(b)))
    setEditOpen(true)
  }

  const isAllSelected = beliefs.length > 0 && selectedIds.length === beliefs.length
  const totalPages = Math.ceil(total / size) || 1

  return (
    <div className="flex flex-col gap-6">
      {/* ─── 过滤器卡片 ─── */}
      <Card>
        <CardHeader className="py-4 shrink-0 border-b bg-muted/10">
          <CardTitle>信念审核管理</CardTitle>
          <CardDescription>
            对自省、长语篇摘要合并过程中涌现出来的 Bot 信念（Beliefs）进行人工裁决。支持追溯关系增减事件、自省内心独白及群聊气泡流。
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-6">
          <form className="flex flex-col gap-4" onSubmit={handleSearchSubmit}>
            <FieldGroup className="grid gap-4 md:grid-cols-4">
              <Field>
                <FieldLabel htmlFor="belief-search">搜索词</FieldLabel>
                <Input
                  id="belief-search"
                  className="h-9 text-xs"
                  placeholder="搜索信念内容..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel>信念类型</FieldLabel>
                <Select value={type || 'all'} onValueChange={(val) => setType(val === 'all' ? '' : val)}>
                  <SelectTrigger className="h-9 text-xs">
                    <SelectValue placeholder="全部类型" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部类型</SelectItem>
                    <SelectItem value="self">self（自我认知）</SelectItem>
                    <SelectItem value="other">other（外部关系）</SelectItem>
                    <SelectItem value="world">world（世界观）</SelectItem>
                    <SelectItem value="value">value（价值观）</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel>信念状态</FieldLabel>
                <Select value={status || 'all'} onValueChange={(val) => setStatus(val === 'all' ? '' : val)}>
                  <SelectTrigger className="h-9 text-xs">
                    <SelectValue placeholder="全部状态" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部状态</SelectItem>
                    <SelectItem value="pending">待审核</SelectItem>
                    <SelectItem value="active">已生效</SelectItem>
                    <SelectItem value="archived">已归档</SelectItem>
                    <SelectItem value="pending_legacy">旧遗产</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel>Bot 对象</FieldLabel>
                <Select value={botId || 'all'} onValueChange={(val) => setBotId(val === 'all' ? '' : val)}>
                  <SelectTrigger className="h-9 text-xs">
                    <SelectValue placeholder="全部 Bot" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部 Bot</SelectItem>
                    <SelectItem value="bot">主 Bot 人格 (bot)</SelectItem>
                    <SelectItem value="assistant">备用 Bot 人格 (assistant)</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
            </FieldGroup>

            <div className="flex flex-wrap items-center gap-2">
              <Button disabled={loading} type="submit" size="sm">
                <Search className="size-3.5 mr-1" />
                搜索
              </Button>
              <Button disabled={loading} variant="outline" size="sm" type="button" onClick={handleResetFilters}>
                <Undo2 className="size-3.5 mr-1" />
                重置
              </Button>
              <Button disabled={loading} type="button" size="sm" variant="outline" onClick={handleOpenCreate}>
                ➕ 新增信念
              </Button>
              <Button disabled={loading} type="button" size="sm" variant="outline" className="border-amber-500/20 hover:bg-amber-500/5 text-amber-500" onClick={handleArchiveLegacy}>
                📦 一键归档旧遗产信念
              </Button>
              <div className="ml-auto text-xs text-muted-foreground font-mono">
                待审：<span className="text-amber-500 font-bold">{pendingCount}</span> 条 · 累计：{total} 条
              </div>
            </div>
          </form>
        </CardContent>
      </Card>

      {/* ─── 批量操作栏 ─── */}
      {selectedIds.length > 0 ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-primary bg-primary/5 p-3.5 animate-in slide-in-from-top duration-200">
          <Badge variant="secondary" className="bg-primary/10 text-primary border-primary/20 hover:bg-primary/10">
            {selectAllMatching ? `已跨页全选了全部 ${total} 条信念` : `已选当页 ${selectedIds.length} 条信念`}
          </Badge>
          <Button size="sm" onClick={() => void handleBatchApprove()}>
            ✓ 批量确认通过
          </Button>
          <Button variant="outline" size="sm" className="border-primary/20 hover:bg-primary/5" onClick={() => void handleBatchArchive()}>
            批量归档
          </Button>
          <Button variant="destructive" size="sm" onClick={() => void handleBatchDelete()}>
            🗑 批量物理删除
          </Button>
          {!selectAllMatching && total > size ? (
            <Button size="xs" variant="ghost" className="text-primary hover:bg-primary/10" onClick={handleSelectAllMatching}>
              🌌 跨页全选全部 {total} 条信念匹配
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setSelectedIds([])}>
            取消选择
          </Button>
        </div>
      ) : null}

      {/* ─── 数据表格 ─── */}
      <Card>
        <CardContent className="pt-6">
          {loading ? (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : beliefs.length === 0 ? (
            <p className="text-sm text-muted-foreground p-6 text-center">暂无符合条件的信念条目。</p>
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
                    <TableHead className="w-16">ID</TableHead>
                    <TableHead className="w-24 text-center">状态</TableHead>
                    <TableHead className="w-24 text-center">类型</TableHead>
                    <TableHead>信念内容</TableHead>
                    <TableHead className="w-28 text-center">Bot 归属</TableHead>
                    <TableHead className="w-24 text-center">置信度</TableHead>
                    <TableHead className="w-24 text-center">关联证据链</TableHead>
                    <TableHead className="w-32">更新时间</TableHead>
                    <TableHead className="w-40 text-right pr-4">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {beliefs.map((b) => {
                    const isRowChecked = selectedIds.includes(b.id)
                    const countEvidence = b.sources?.length ?? 0
                    return (
                      <TableRow key={b.id} className={isRowChecked ? 'bg-primary/5 hover:bg-primary/5' : ''}>
                        <TableCell>
                          <input
                            type="checkbox"
                            checked={isRowChecked || selectAllMatching}
                            onChange={(e) => handleRowCheckChange(b.id, e.target.checked)}
                          />
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">#{b.id}</TableCell>
                        <TableCell className="text-center">
                          <Badge className={beliefStatusBadge(b.status)} variant="outline">
                            {b.status === 'pending' ? '待审核' : b.status === 'active' ? '已生效' : b.status === 'archived' ? '已归档' : '旧遗产'}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-center">
                          <Badge className={beliefTypeBadge(b.type)} variant="outline">
                            {b.type === 'self' ? '自我' : b.type === 'world' ? '世界' : b.type === 'value' ? '价值' : '外部'}
                          </Badge>
                        </TableCell>
                        <TableCell className="font-medium max-w-sm truncate" title={b.content}>{b.content}</TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground text-center">{b.bot_id || '—'}</TableCell>
                        <TableCell className="text-center font-mono text-xs text-primary font-bold">
                          {b.confidence != null ? `${Math.round(b.confidence * 100)}%` : '—'}
                        </TableCell>
                        <TableCell className="text-center">
                          <Button variant="ghost" className="h-7 text-xs px-2 flex items-center gap-1 mx-auto" onClick={() => void handleOpenEvidence(b.id)}>
                            <EyeIcon className="size-3" />
                            {countEvidence > 0 ? `证据 (${countEvidence})` : '无证据'}
                          </Button>
                        </TableCell>
                        <TableCell className="text-muted-foreground font-mono text-xs whitespace-nowrap">
                          {formatTime(b.timestamp ?? b.last_reinforced)}
                        </TableCell>
                        <TableCell className="text-right pr-4">
                          <div className="flex justify-end gap-1.5">
                            {b.status === 'pending' ? (
                              <Button size="xs" variant="secondary" className="bg-emerald-500/10 text-emerald-500 border border-emerald-500/10 hover:bg-emerald-500/20" onClick={() => void handleApproveSingle(b.id)}>
                                确认通过
                              </Button>
                            ) : null}
                            {b.status !== 'archived' ? (
                              <Button size="xs" variant="outline" className="border-amber-500/20 text-amber-500 hover:bg-amber-500/10" onClick={() => void handleArchiveSingle(b.id)}>
                                归档
                              </Button>
                            ) : null}
                            <Button variant="ghost" className="size-7 p-0" onClick={() => handleOpenEdit(b)} title="编辑">
                              <FileEdit className="size-3.5" />
                            </Button>
                            <Button variant="ghost" className="size-7 p-0 text-destructive hover:text-destructive hover:bg-destructive/10" onClick={() => void handleDeleteSingle(b.id)} title="物理删除">
                              <Trash2 className="size-3.5" />
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

          {/* ─── 分页器 ─── */}
          {beliefs.length > 0 ? (
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
                <Button disabled={loading || page <= 1} variant="outline" size="sm" onClick={() => void loadData(page - 1)}>
                  上一页
                </Button>
                <span className="font-mono text-xs text-muted-foreground">
                  第 {page} / {totalPages} 页
                </span>
                <Button disabled={loading || page >= totalPages} variant="outline" size="sm" onClick={() => void loadData(page + 1)}>
                  下一页
                </Button>
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>

      {/* ─── 弹出弹窗 A：聊天气泡还原证据追溯弹窗（统一高阶多图层对齐） ─── */}
      <Dialog open={evidenceOpen} onOpenChange={setEvidenceOpen}>
        <DialogContent className="sm:max-w-2xl flex flex-col gap-0 h-[80vh]">
          <DialogHeader className="pb-3 border-b shrink-0 pr-6">
            <DialogTitle className="flex items-center gap-2">
              <span>信念形成多阶证据链追溯</span>
              {evidenceId ? <Badge variant="outline">#{evidenceId}</Badge> : null}
            </DialogTitle>
            <DialogDescription>
              还原该信念在心智推演过程中涌现的所有事实关系，包含关系数值变迁、反省独白和原始气泡还原。
            </DialogDescription>
          </DialogHeader>

          {evidenceLoading ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-2">
              <Loader2 className="animate-spin text-primary size-5" />
              <span className="text-xs text-muted-foreground font-mono">正在拉取多维证据，还原心智演算链路...</span>
            </div>
          ) : evidenceData ? (
            <div className="flex-1 flex flex-col min-h-0">
              {/* 子分类 Mini-Tabs 导航，完美展示 Relationship Events / Experience Episodes / Chat context */}
              <Tabs value={evidenceSubTab} onValueChange={(val: any) => setEvidenceSubTab(val)} className="flex-1 flex flex-col min-h-0">
                <div className="p-3 bg-muted/40 border-b flex flex-wrap items-center justify-between shrink-0 gap-3">
                  <div className="flex items-center gap-2">
                    <label className="flex items-center gap-1 text-[11px] text-muted-foreground">前：
                      <input type="number" className="input text-xs w-12 h-6" min="0" max="50" value={evidenceBefore} onChange={(e) => setEvidenceBefore(Number(e.target.value) || 0)} />
                    </label>
                    <label className="flex items-center gap-1 text-[11px] text-muted-foreground">后：
                      <input type="number" className="input text-xs w-12 h-6" min="0" max="50" value={evidenceAfter} onChange={(e) => setEvidenceAfter(Number(e.target.value) || 0)} />
                    </label>
                    <Button size="xs" onClick={() => evidenceId && void handleOpenEvidence(evidenceId, evidenceBefore, evidenceAfter)}>刷新</Button>
                  </div>
                  <TabsList className="grid grid-cols-3 h-7 w-full max-w-xs">
                    <TabsTrigger value="relationship_event" className="text-[10px]" disabled={!evidenceData.relationship_events?.length}>关系变化</TabsTrigger>
                    <TabsTrigger value="episode" className="text-[10px]" disabled={!evidenceData.episodes?.length}>自省独白</TabsTrigger>
                    <TabsTrigger value="memory" className="text-[10px]" disabled={!evidenceData.memories?.length}>聊天气泡</TabsTrigger>
                  </TabsList>
                </div>

                {/* TAB 1: 关系数值变化事件 */}
                <TabsContent value="relationship_event" className="flex-1 overflow-auto p-4 bg-muted/5">
                  <div className="space-y-3">
                    {(evidenceData.relationship_events ?? []).map((ev: any) => {
                      const isPositive = Number(ev.delta) >= 0
                      return (
                        <div key={ev.id} className="p-3 rounded-xl border bg-background space-y-2 text-xs shadow-sm animate-in fade-in duration-150">
                          <div className="flex items-center justify-between">
                            <span className="font-semibold text-foreground flex items-center gap-1.5">
                              💥 关系变迁 #{ev.id} · <span className="text-primary">{ev.dimension}</span> 
                              <Badge className={isPositive ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20' : 'bg-destructive/10 text-destructive border-destructive/20'} variant="outline">
                                {isPositive ? '+' : ''}{ev.delta}
                              </Badge>
                            </span>
                            <span className="text-[10px] text-muted-foreground font-mono">{formatTime(ev.created_at)}</span>
                          </div>
                          <div className="grid grid-cols-2 gap-2 text-[10px] text-muted-foreground bg-muted/30 p-2 rounded-md font-mono">
                            <div>对象 QQ ID: {ev.user_id || '—'}</div>
                            <div>群号: {ev.group_id || '全局'}</div>
                          </div>
                          <div className="text-xs text-foreground leading-relaxed pl-1 pt-1"><span className="text-muted-foreground font-medium">触发诱因：</span>{ev.reason}</div>
                        </div>
                      )
                    })}
                  </div>
                </TabsContent>

                {/* TAB 2: 自省内心独白插曲 */}
                <TabsContent value="episode" className="flex-1 overflow-auto p-4 bg-muted/5">
                  <div className="space-y-3">
                    {(evidenceData.episodes ?? []).map((ep: any) => (
                      <div key={ep.id} className="p-4 rounded-xl border bg-background space-y-3 text-xs shadow-sm animate-in fade-in duration-150">
                        <div className="flex items-center justify-between border-b pb-1.5 border-white/5">
                          <span className="font-semibold text-foreground">自省插曲 #{ep.id} <Badge variant="secondary" className="text-[9px] font-normal font-mono uppercase ml-1.5">{ep.episode_type}</Badge></span>
                          <span className="text-[10px] text-muted-foreground font-mono">{formatTime(ep.created_at)}</span>
                        </div>
                        <div className="space-y-2.5 text-xs">
                          {ep.trigger && (
                            <div className="flex gap-2.5">
                              <span className="text-muted-foreground font-semibold shrink-0 w-14 text-right select-none">外部触发:</span>
                              <span className="text-foreground leading-relaxed">{ep.trigger}</span>
                            </div>
                          )}
                          {ep.bot_inner_thought && (
                            <div className="flex gap-2.5 bg-yellow-500/5 border border-yellow-500/10 p-2 rounded-lg">
                              <span className="text-yellow-500 font-semibold shrink-0 w-14 text-right select-none">内心独白:</span>
                              <span className="text-yellow-400/90 leading-relaxed italic font-mono">{ep.bot_inner_thought}</span>
                            </div>
                          )}
                          {ep.bot_reply && (
                            <div className="flex gap-2.5">
                              <span className="text-emerald-500 font-semibold shrink-0 w-14 text-right select-none">回复内容:</span>
                              <span className="text-emerald-400 leading-relaxed font-semibold">“{ep.bot_reply}”</span>
                            </div>
                          )}
                          {ep.outcome && (
                            <div className="flex gap-2.5 border-t border-white/5 pt-1.5 mt-1.5">
                              <span className="text-muted-foreground font-semibold shrink-0 w-14 text-right select-none">反应与后果:</span>
                              <span className="text-foreground leading-relaxed">{ep.outcome}</span>
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </TabsContent>

                {/* TAB 3: 当时真实群聊气泡还原 (Genuine Chat Context) */}
                <TabsContent value="memory" className="flex-1 flex flex-col min-h-0 bg-muted/10">
                  {evidenceData.anchor ? (
                    <div className="p-3 bg-primary/5 border-b shrink-0 flex items-center justify-between">
                      <div className="min-w-0">
                        <span className="text-[10px] text-primary block mb-0.5 font-bold uppercase tracking-wider">🎯 涌现锚定事实</span>
                        <p className="text-xs text-foreground font-mono leading-relaxed truncate">{evidenceData.anchor.content}</p>
                      </div>
                      <Badge variant={evidenceData.used_fallback ? 'destructive' : 'secondary'} className="shrink-0 scale-90">
                        {evidenceData.used_fallback ? '静态降级' : '动态解包'}
                      </Badge>
                    </div>
                  ) : null}

                  <ScrollArea className="flex-1 p-4 bg-muted/5">
                    <div className="flex flex-col gap-4">
                      {evidenceData.memories?.length === 0 ? (
                        <p className="text-sm text-muted-foreground py-12 text-center">无法还原上下文消息链（可能对应群已被退群）。</p>
                      ) : (
                        (evidenceData.memories ?? []).map((msg: any, index: number) => {
                          const isAnchor = evidenceData.anchor && String(msg.id) === String(evidenceData.anchor.id)
                          const isBot = msg.sender_id === '2500447291' || msg.sender_id === '1336495069' || String(msg.sender_name).includes('AI') || String(msg.sender_name).includes('Bot')
                          
                          return (
                            <div key={`${msg.id}-${index}`} className={`flex flex-col max-w-[85%] ${isBot ? 'ml-auto items-end' : 'mr-auto items-start'}`}>
                              <span className="text-[9px] text-muted-foreground font-mono mb-1">
                                {msg.sender_name || msg.sender_id} · {formatTime(msg.timestamp)}
                              </span>
                              <div className={`rounded-2xl px-3.5 py-2 text-xs leading-relaxed border ${
                                isAnchor 
                                  ? 'bg-amber-500/10 border-amber-500/20 text-foreground dark:text-foreground shadow-[0_0_12px_rgba(245,158,11,0.06)]' 
                                  : isBot 
                                    ? 'bg-primary text-primary-foreground border-transparent' 
                                    : 'bg-background border-border/50 text-foreground'
                              }`}>
                                {msg.content}
                              </div>
                              {isAnchor ? (
                                <span className="text-[9px] text-amber-500 font-bold mt-1 font-mono uppercase">★ 锚定提取点 (Anchor)</span>
                              ) : null}
                            </div>
                          )
                        })
                      )}
                    </div>
                  </ScrollArea>
                </TabsContent>
              </Tabs>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground py-12 text-center">暂无证据链数据。</p>
          )}
        </DialogContent>
      </Dialog>

      {/* 新增/编辑信念弹窗 */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{isEditNew ? '新建心智信念' : `编辑信念 #${editForm.id}`}</DialogTitle>
          </DialogHeader>

          <form className="flex flex-col gap-4 py-4" onSubmit={(e) => { e.preventDefault(); void handleSaveEdit(); }}>
            <FieldGroup className="grid gap-4">
              <Field>
                <FieldLabel>信念内容</FieldLabel>
                <Textarea
                  rows={3}
                  value={editForm.content || ''}
                  onChange={(e) => setEditForm({ ...editForm, content: e.target.value })}
                  placeholder="如：用户是非常值得信任和守护的群友..."
                />
              </Field>

              <Field>
                <FieldLabel>信念类型</FieldLabel>
                <Select value={editForm.type || 'self'} onValueChange={(val: any) => setEditForm({ ...editForm, type: val })}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="self">self (自我认知)</SelectItem>
                    <SelectItem value="other">other (外部关系)</SelectItem>
                    <SelectItem value="world">world (世界观)</SelectItem>
                    <SelectItem value="value">value (价值观)</SelectItem>
                  </SelectContent>
                </Select>
              </Field>

              <Field>
                <FieldLabel>置信度分值 (0-1)</FieldLabel>
                <Input
                  type="number"
                  step="0.05"
                  min="0"
                  max="1"
                  value={editForm.confidence ?? 1.0}
                  onChange={(e) => setEditForm({ ...editForm, confidence: Number(e.target.value) || 0 })}
                />
              </Field>

              {isEditNew ? (
                <Field>
                  <FieldLabel>Bot 归属</FieldLabel>
                  <Select value={editForm.bot_id || 'bot'} onValueChange={(val) => setEditForm({ ...editForm, bot_id: val })}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="bot">bot (主Bot)</SelectItem>
                      <SelectItem value="assistant">assistant (备用)</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
              ) : null}
            </FieldGroup>

            <div className="flex gap-2 justify-end border-t pt-3 mt-2">
              <Button variant="outline" type="button" onClick={() => setEditOpen(false)}>取消</Button>
              <Button disabled={saving} type="submit">
                {saving ? <Loader2 className="animate-spin" data-icon="inline-start" /> : null}
                保存
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
export default BeliefsPage
