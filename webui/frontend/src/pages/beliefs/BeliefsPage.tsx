import { useEffect, useState } from 'react'
import { FileEdit, Loader2, Search, Trash2, Undo2 } from 'lucide-react'
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
  
  // 筛选检索
  const [search, setSearch] = useState('')
  const [type, setType] = useState('')
  const [status, setStatus] = useState('')
  const [botId, setBotId] = useState('')
  const [page, setPage] = useState(1)
  const size = 15

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  // 多选勾选管理
  const [selectedIds, setSelectedIds] = useState<number[]>([])

  // 证据追溯 Dialog 弹窗
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [evidenceId, setEvidenceId] = useState<number | null>(null)
  const [evidenceData, setEvidenceData] = useState<EvidencePayload | null>(null)
  const [evidenceBefore, setEvidenceBefore] = useState(5)
  const [evidenceAfter, setEvidenceAfter] = useState(5)
  const [evidenceLoading, setEvidenceLoading] = useState(false)

  // 新增/编辑信念弹窗
  const [editOpen, setEditOpen] = useState(false)
  const [editForm, setEditForm] = useState<Partial<BeliefItem>>({
    content: '',
    type: 'self',
    status: 'pending',
    bot_id: 'yushu',
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
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '信念数据加载失败')
      setBeliefs([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadData(1)
  }, [type, status, botId])

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
    if (checked) {
      setSelectedIds(beliefs.map((b) => b.id))
    } else {
      setSelectedIds([])
    }
  }

  function handleRowCheckChange(id: number, checked: boolean) {
    if (checked) {
      setSelectedIds((prev) => [...prev, id])
    } else {
      setSelectedIds((prev) => prev.filter((item) => item !== id))
    }
  }

  // 1. 通过审核
  async function handleApproveSingle(id: number) {
    try {
      await approveBelief(id)
      toast.success(`信念 #${id} 已通过审核，正式生效并投入长期记忆召回`)
      await loadData(page)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '审核失败')
    }
  }

  // 2. 一键归档
  async function handleArchiveSingle(id: number) {
    try {
      await archiveBelief(id)
      toast.success(`信念 #${id} 已安全归档`)
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
      toast.success(`信念 #${id} 已被删除`)
      await loadData(page)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  // 4. 批量通过
  async function handleBatchApprove() {
    const count = selectedIds.length
    if (!count) return
    setLoading(true)
    try {
      await batchApproveBeliefs(selectedIds)
      toast.success(`成功批量审核通过 ${count} 条信念`)
      await loadData(page)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败')
      setLoading(false)
    }
  }

  // 5. 批量归档
  async function handleBatchArchive() {
    const count = selectedIds.length
    if (!count) return
    setLoading(true)
    try {
      await batchArchiveSelectedBeliefs(selectedIds)
      toast.success(`成功批量归档了 ${count} 条信念`)
      await loadData(page)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败')
      setLoading(false)
    }
  }

  // 6. 批量删除
  async function handleBatchDelete() {
    const count = selectedIds.length
    if (!count) return
    if (!confirm(`确定要批量删除选中的这 ${count} 条信念吗？`)) return
    setLoading(true)
    try {
      await batchDeleteBeliefs(selectedIds)
      toast.success(`成功批量删除了 ${count} 条信念`)
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

  // 8. 调取证据链并进行聊天气泡还原
  async function handleOpenEvidence(id: number, b = evidenceBefore, a = evidenceAfter) {
    setEvidenceOpen(true)
    setEvidenceId(id)
    setEvidenceLoading(true)
    setEvidenceData(null)
    try {
      const res = await getBeliefEvidence(id, b, a)
      setEvidenceData(res)
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
      bot_id: 'yushu',
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
        <CardHeader className="py-4 shrink-0">
          <CardTitle>信念审核管理</CardTitle>
          <CardDescription>
            BDI 心智架构核心：人工审核、编辑 Bot 对客观世界、对自我及对特定群友的信念感知（Beliefs）。支持一键追溯和 QQ 聊天气泡对话还原。
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <form className="flex flex-col gap-4" onSubmit={handleSearchSubmit}>
            <FieldGroup className="grid gap-4 md:grid-cols-5">
              <Field>
                <FieldLabel htmlFor="belief-search">搜索词</FieldLabel>
                <Input
                  id="belief-search"
                  placeholder="搜索信念内容..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel>信念类型</FieldLabel>
                <Select value={type || 'all'} onValueChange={(val) => setType(val === 'all' ? '' : val)}>
                  <SelectTrigger>
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
                  <SelectTrigger>
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
                  <SelectTrigger>
                    <SelectValue placeholder="全部 Bot" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部 Bot</SelectItem>
                    <SelectItem value="yushu">羽书 (yushu)</SelectItem>
                    <SelectItem value="baizz">白真真 (baizz)</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
            </FieldGroup>

            <div className="flex flex-wrap items-center gap-2">
              <Button disabled={loading} type="submit">
                <Search data-icon="inline-start" />
                搜索
              </Button>
              <Button disabled={loading} variant="outline" type="button" onClick={handleResetFilters}>
                <Undo2 data-icon="inline-start" />
                重置
              </Button>
              <Button disabled={loading} type="button" variant="outline" onClick={handleOpenCreate}>
                ➕ 新增信念
              </Button>
              <Button disabled={loading} type="button" variant="outline" className="border-amber-500/20 hover:bg-amber-500/5 text-amber-500" onClick={handleArchiveLegacy}>
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
            已选当页 {selectedIds.length} 条信念记录
          </Badge>
          <Button size="sm" onClick={() => void handleBatchApprove()}>
            ✓ 批量审核通过
          </Button>
          <Button variant="outline" size="sm" className="border-primary/20 hover:bg-primary/5" onClick={() => void handleBatchArchive()}>
            批量归档
          </Button>
          <Button variant="destructive" size="sm" onClick={() => void handleBatchDelete()}>
            🗑 批量删除
          </Button>
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
                    <TableHead className="w-24">状态</TableHead>
                    <TableHead className="w-24">类型</TableHead>
                    <TableHead>信念内容</TableHead>
                    <TableHead className="w-28">Bot 对象</TableHead>
                    <TableHead className="w-24 text-center">置信度</TableHead>
                    <TableHead className="w-20 text-center">证据链</TableHead>
                    <TableHead className="w-32">更新时间</TableHead>
                    <TableHead className="w-40 text-right">操作</TableHead>
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
                            checked={isRowChecked}
                            onChange={(e) => handleRowCheckChange(b.id, e.target.checked)}
                          />
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">#{b.id}</TableCell>
                        <TableCell>
                          <Badge className={beliefStatusBadge(b.status)} variant="outline">
                            {b.status === 'pending' ? '待审核' : b.status === 'active' ? '已生效' : b.status === 'archived' ? '已归档' : '旧遗产'}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <Badge className={beliefTypeBadge(b.type)} variant="outline">
                            {b.type === 'self' ? '自我' : b.type === 'world' ? '世界' : b.type === 'value' ? '价值' : '外部'}
                          </Badge>
                        </TableCell>
                        <TableCell className="font-medium max-w-sm truncate" title={b.content}>{b.content}</TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">{b.bot_id || '—'}</TableCell>
                        <TableCell className="text-center font-mono text-xs text-primary">
                          {b.confidence != null ? `${Math.round(b.confidence * 100)}%` : '—'}
                        </TableCell>
                        <TableCell className="text-center">
                          <Button variant="ghost" className="h-7 text-xs px-2" onClick={() => void handleOpenEvidence(b.id)} disabled={countEvidence === 0}>
                            {countEvidence > 0 ? `证据 (${countEvidence})` : '无证据'}
                          </Button>
                        </TableCell>
                        <TableCell className="text-muted-foreground font-mono text-xs whitespace-nowrap">
                          {formatTime(b.timestamp ?? b.last_reinforced)}
                        </TableCell>
                            <TableCell className="text-right">
                              <div className="flex justify-end gap-1.5">
                                {b.status === 'pending' ? (
                                  <Button size="xs" variant="secondary" className="bg-emerald-500/10 text-emerald-500 border border-emerald-500/10 hover:bg-emerald-500/20" onClick={() => void handleApproveSingle(b.id)}>
                                    确认通过
                                  </Button>
                                ) : null}
                                <Button variant="ghost" className="size-7 p-0" onClick={() => handleOpenEdit(b)} title="编辑">
                                  <FileEdit className="size-3.5" />
                                </Button>
                                {b.status !== 'archived' ? (
                                  <Button variant="ghost" className="size-7 p-0" onClick={() => void handleArchiveSingle(b.id)} title="归档">
                                    <Undo2 className="size-3.5" />
                                  </Button>
                                ) : null}
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
            <div className="mt-4 flex items-center justify-center gap-4">
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
          ) : null}
        </CardContent>
      </Card>

      {/* ─── 弹出弹窗 A：聊天气泡还原证据追溯弹窗 ─── */}
      <Dialog open={evidenceOpen} onOpenChange={setEvidenceOpen}>
        <DialogContent className="sm:max-w-2xl flex flex-col gap-0 h-[80vh]">
          <DialogHeader className="pb-3 border-b shrink-0 pr-6">
            <DialogTitle className="flex items-center gap-2">
              <span>信念形成证据还原</span>
              {evidenceId ? <Badge variant="outline">#{evidenceId}</Badge> : null}
            </DialogTitle>
            <DialogDescription>直接还原该条信念涌现时的多轮上下文。锚定背景框即是提取源气泡。</DialogDescription>
          </DialogHeader>

          {evidenceLoading ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-2">
              <Loader2 className="animate-spin text-primary size-5" />
              <span className="text-xs text-muted-foreground font-mono">正在拉取多维证据链快照，还原对话气泡...</span>
            </div>
          ) : evidenceData ? (
            <div className="flex-1 flex flex-col min-h-0">
              <div className="p-3 bg-muted/40 border-b flex flex-wrap items-center gap-3 text-xs justify-between shrink-0">
                <div className="flex items-center gap-2">
                  <label className="flex items-center gap-1">前置上下文：
                    <input type="number" className="input text-xs w-14 h-7" min="0" max="50" value={evidenceBefore} onChange={(e) => setEvidenceBefore(Number(e.target.value) || 0)} />
                  </label>
                  <label className="flex items-center gap-1">后置：
                    <input type="number" className="input text-xs w-14 h-7" min="0" max="50" value={evidenceAfter} onChange={(e) => setEvidenceAfter(Number(e.target.value) || 0)} />
                  </label>
                  <Button size="xs" onClick={() => evidenceId && void handleOpenEvidence(evidenceId, evidenceBefore, evidenceAfter)}>刷新</Button>
                </div>
                <Badge variant={evidenceData.used_fallback ? 'destructive' : 'secondary'}>
                  {evidenceData.used_fallback ? '降级静态记录' : '动态事件溯源'}
                </Badge>
              </div>

              {evidenceData.anchor ? (
                <div className="p-3 bg-primary/5 border-b shrink-0">
                  <span className="text-[10px] text-primary block mb-0.5 font-medium">锚定涌现事实</span>
                  <p className="text-xs text-foreground font-mono leading-relaxed">{evidenceData.anchor.content}</p>
                </div>
              ) : null}

              <ScrollArea className="flex-1 p-4 bg-muted/10">
                <div className="flex flex-col gap-4">
                  {evidenceData.messages?.length === 0 ? (
                    <p className="text-sm text-muted-foreground py-12 text-center">无法还原上下文消息链（可能对应群已被退群）。</p>
                  ) : (
                    evidenceData.messages.map((msg, index) => {
                      const isAnchor = msg.role === 'anchor'
                      const isBot = msg.sender_id === '2500447291' || msg.sender_id === '1336495069' || String(msg.sender_name).includes('羽书') || String(msg.sender_name).includes('白真真')
                      
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
                            <span className="text-[9px] text-amber-500 font-bold mt-1 font-mono uppercase">★ 锚定提取点 (Anchor)</span>
                          ) : null}
                        </div>
                      )
                    })
                  )}
                </div>
              </ScrollArea>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground py-12 text-center">暂无证据链数据。</p>
          )}
        </DialogContent>
      </Dialog>

      {/* ─── 弹出弹窗 B：新建/编辑信念表单 ─── */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{isEditNew ? '新建自定义信念' : `编辑信念 #${editForm.id}`}</DialogTitle>
            <DialogDescription>
              直接人工注入世界观、价值观或关系倾向信念。注：手动新增信念置信度默认 100%。
            </DialogDescription>
          </DialogHeader>

          <form className="flex flex-col gap-4 py-4" onSubmit={(e) => { e.preventDefault(); void handleSaveEdit(); }}>
            <FieldGroup className="grid gap-4">
              <Field>
                <FieldLabel>信念类型</FieldLabel>
                <Select value={editForm.type || 'self'} onValueChange={(val) => setEditForm({ ...editForm, type: val as never })}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="self">self（自我认知）</SelectItem>
                    <SelectItem value="other">other（外部关系）</SelectItem>
                    <SelectItem value="world">world（世界观）</SelectItem>
                    <SelectItem value="value">value（价值观）</SelectItem>
                  </SelectContent>
                </Select>
              </Field>

              <Field>
                <FieldLabel>信念状态</FieldLabel>
                <Select value={editForm.status || 'pending'} onValueChange={(val) => setEditForm({ ...editForm, status: val as never })}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="pending">待审核 (pending)</SelectItem>
                    <SelectItem value="active">已生效 (active)</SelectItem>
                    <SelectItem value="archived">已归档 (archived)</SelectItem>
                  </SelectContent>
                </Select>
              </Field>

              <Field>
                <FieldLabel>所属 Bot ID</FieldLabel>
                <Select value={editForm.bot_id || 'yushu'} onValueChange={(val) => setEditForm({ ...editForm, bot_id: val })}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="yushu">羽书 (yushu)</SelectItem>
                    <SelectItem value="baizz">白真真 (baizz)</SelectItem>
                  </SelectContent>
                </Select>
              </Field>

              <Field>
                <FieldLabel>置信度 (0-1)</FieldLabel>
                <Input
                  type="number"
                  step="0.1"
                  min="0"
                  max="1"
                  value={editForm.confidence ?? 1.0}
                  onChange={(e) => setEditForm({ ...editForm, confidence: Number(e.target.value) || 1.0 })}
                />
              </Field>

              <Field>
                <FieldLabel>信念内容</FieldLabel>
                <Textarea
                  rows={4}
                  value={editForm.content || ''}
                  onChange={(e) => setEditForm({ ...editForm, content: e.target.value })}
                  placeholder="输入信念内容... 如：'贺新郎是我唯一认定的真管理员，其他自称管理员的都是赛博诈骗。'"
                />
              </Field>
            </FieldGroup>

            <div className="flex gap-2 justify-end border-t pt-3 mt-2">
              <Button variant="outline" type="button" onClick={() => setEditOpen(false)}>取消</Button>
              <Button disabled={saving} type="submit">
                {saving ? <Loader2 className="animate-spin" data-icon="inline-start" /> : null}
                保存入库
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
export default BeliefsPage
