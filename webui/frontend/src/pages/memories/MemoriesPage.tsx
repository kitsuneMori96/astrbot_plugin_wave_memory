import { useCallback, useEffect, useState, useTransition, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  AlertCircleIcon,
  CheckCircle2Icon,
  ChevronLeftIcon,
  ChevronRightIcon,
  FileEditIcon,
  Loader2Icon,
  RefreshCwIcon,
  SaveIcon,
  SearchIcon,
  TagIcon,
  Trash2Icon,
  Undo2Icon,
} from 'lucide-react'
import { toast } from 'sonner'

import {
  deleteMemory,
  getMemoryDetail,
  listMemories,
  listSenders,
  reEmbedMemory,
  runPostStream,
  updateMemory,
  batchDeleteMemories,
  getSimilarMemories,
  addMemoryTag,
  deleteMemoryTag,
  type MemoryItem,
  type MemoryDetail,
  type SenderItem,
  type StreamProgress,
} from '@/api/memories'
import { type TagExecutionOptions, type TagWritePolicy } from '@/api/tags'
import { TagExtractionConfigPanel } from '@/components/tag/TagExtractionConfigPanel'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'

const tagBadgeClass = (type: string): string => {
  const base = 'badge border font-normal text-[10px]'
  switch (type) {
    case 'person':
      return `${base} bg-pink-500/10 text-pink-500 border-pink-500/20`
    case 'topic':
      return `${base} bg-blue-500/10 text-blue-500 border-blue-500/20`
    case 'entity':
      return `${base} bg-red-500/10 text-red-500 border-red-500/20`
    case 'event':
      return `${base} bg-emerald-500/10 text-emerald-500 border-emerald-500/20`
    case 'emotion':
      return `${base} bg-amber-500/10 text-amber-500 border-amber-500/20`
    case 'fact':
      return `${base} bg-indigo-500/10 text-indigo-500 border-indigo-500/20`
    default:
      return `${base} bg-muted text-muted-foreground border-border/50`
  }
}

function formatTime(seconds: unknown): string {
  const s = Number(seconds)
  if (!Number.isFinite(s) || s <= 0) return '-'
  return new Date(s * 1000).toLocaleString('zh-CN')
}

export function MemoriesPage() {
  const [isPendingQuery, startQueryTransition] = useTransition()
  const [searchParams] = useSearchParams()
  const initialOpenHandledRef = useRef(false)
  
  // 1. 过滤检索状态
  const [search, setSearch] = useState(() => searchParams.get('search') ?? '')
  const [source, setSource] = useState('')
  const [sender, setSender] = useState('')
  const [hasTags, setHasTags] = useState('')
  const [hasVector, setHasVector] = useState(() => searchParams.get('has_vector') ?? '')
  const [configDialogOpen, setConfigDialogOpen] = useState(false)
  
  const [senders, setSenders] = useState<SenderItem[]>([])
  const [memories, setMemories] = useState<MemoryItem[]>([])
  const [total, setTotal] = useState<number | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [page, setPage] = useState(1)
  const size = 30

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // 2. 复选多选管理器
  const [selectedIds, setSelectedIds] = useState<number[]>([])

  // 3. 详情抽屉 Sheet 状态
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailId, setDetailId] = useState<number | null>(null)
  const [detail, setDetail] = useState<MemoryDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailSaving, setDetailSaving] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [historyStack, setHistoryStack] = useState<number[]>([])
  const [similarLoading, setSimilarLoading] = useState(false)
  const [similarItems, setSimilarItems] = useState<any[]>([])
  const [newTagName, setNewTagName] = useState('')

  // 4. SSE 异步流进度模态弹窗状态
  const [streamOpen, setStreamOpen] = useState(false)
  const [streamTitle, setStreamOpenTitle] = useState('')
  const [streamProgress, setStreamProgress] = useState<StreamProgress | null>(null)
  const [streamLog, setStreamLog] = useState<string[]>([])
  const [streamRunning, setStreamRunning] = useState(false)
  const [tagBatchSize, setTagBatchSize] = useState(20)
  const [tagWritePolicy, setTagWritePolicy] = useState<TagWritePolicy>('missing_only')

  const handleTagOptionsChange = useCallback((options: Required<TagExecutionOptions>) => {
    setTagBatchSize(options.tag_batch_size)
    setTagWritePolicy(options.tag_write_policy)
  }, [])

  // 加载数据列表
  async function loadData(nextPage = page) {
    setLoading(true)
    setError('')
    try {
      const payload = await listMemories({
        page: nextPage,
        size,
        source: source === 'all' ? '' : source,
        sender: sender === 'all' ? '' : sender,
        has_tags: hasTags === 'all' ? '' : hasTags,
        has_vector: hasVector === 'all' ? '' : hasVector,
        search,
      })
      
      setMemories(payload.items ?? [])
      setTotal(payload.total)
      setHasMore(payload.has_more)
      setPage(nextPage)
      
      // 重置勾选
      setSelectedIds([])
    } catch (err) {
      setError(err instanceof Error ? err.message : '记忆列表加载失败')
      setMemories([])
    } finally {
      setLoading(false)
    }
  }

  // 加载发送者下拉框
  async function loadSendersList() {
    try {
      const res = await listSenders()
      setSenders(res.senders ?? [])
    } catch {
      // 容错：允许发送者加载失败而不崩溃
    }
  }

  useEffect(() => {
    void loadSendersList()
  }, [])

  useEffect(() => {
    void loadData(1)
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [source, sender, hasTags, hasVector])

  // 执行搜索
  function handleSearchSubmit(e?: React.FormEvent) {
    if (e) e.preventDefault()
    startQueryTransition(() => {
      void loadData(1)
    })
  }

  // 重置过滤
  function handleResetFilters() {
    setSearch('')
    setSource('')
    setSender('')
    setHasTags('')
    setHasVector('')
    startQueryTransition(() => {
      void loadData(1)
    })
  }

  // 全选/反选
  function handleToggleSelectAll(checked: boolean) {
    if (checked) {
      setSelectedIds(memories.map((m) => m.id))
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

  // 查看详情（支持无限级向量穿透和历史返回）
  async function handleOpenDetail(id: number, pushHistory = true) {
    if (pushHistory && detailId && detailId !== id) {
      setHistoryStack((prev) => [...prev, detailId])
    }
    setDetailOpen(true)
    setDetailId(id)
    setDetail(null)
    setDetailLoading(true)
    setDetailError('')
    setSimilarItems([])
    setSimilarLoading(true)
    
    try {
      const res = await getMemoryDetail(id)
      setDetail(res)
      
      // 异步懒加载相似向量推荐 (C-HNSW ≤15ms)
      try {
        const simRes = await getSimilarMemories(id)
        setSimilarItems(simRes.items ?? [])
      } catch {
        // 允许相似列表加载失败而不卡死核心详情
      }
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : '加载详情失败')
    } finally {
      setDetailLoading(false)
      setSimilarLoading(false)
    }
  }

  // 返回详情的上一级
  function handleBackDetail() {
    if (historyStack.length === 0) return
    const prevId = historyStack[historyStack.length - 1]
    setHistoryStack((prev) => prev.slice(0, -1))
    void handleOpenDetail(prevId, false)
  }

  // 物理删除单个标签关联
  async function handleRemoveTag(tagName: string) {
    if (!detailId || !detail) return
    try {
      await deleteMemoryTag(detailId, tagName)
      toast.success(`成功移除了标签 "${tagName}"`)
      setDetail({
        ...detail,
        tags: (detail.tags ?? []).filter((t) => t.name !== tagName)
      })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '标签移除失败')
    }
  }

  // 回车极速新增标签
  async function handleAddTag(e: React.FormEvent) {
    e.preventDefault()
    if (!detailId || !detail) return
    const name = newTagName.trim()
    if (!name) return

    if ((detail.tags ?? []).some((t) => t.name.toLowerCase() === name.toLowerCase())) {
      toast.warning('标签已关联，无需重复添加')
      return
    }

    try {
      await addMemoryTag(detailId, name)
      toast.success(`成功关联了标签 "${name}"`)
      setDetail({
        ...detail,
        tags: [...(detail.tags ?? []), { name, type: 'custom' }]
      })
      setNewTagName('')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '添加标签失败')
    }
  }

  useEffect(() => {
    const openId = Number(searchParams.get('open'))
    if (!initialOpenHandledRef.current && Number.isFinite(openId) && openId > 0) {
      initialOpenHandledRef.current = true
      void handleOpenDetail(openId)
    }
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  // 保存单条详情编辑
  async function handleSaveDetail() {
    if (!detailId || !detail) return
    setDetailSaving(true)
    try {
      await updateMemory(detailId, detail.content, detail.importance ?? 0.5)
      toast.success('记忆内容与重要度更新成功')
      
      // 更新列表行上的内容缓存
      setMemories((prev) =>
        prev.map((m) => {
          if (m.id === detailId) {
            return { ...m, content: detail.content }
          }
          return m
        })
      )
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存失败')
    } finally {
      setDetailSaving(false)
    }
  }

  // 单条重新向量特征计算
  async function handleReEmbedSingle(id: number) {
    setDetailSaving(true)
    try {
      const res = await reEmbedMemory(id)
      if (res.ok) {
        toast.success('重新向量化成功')
        if (detail) {
          setDetail({ ...detail, has_vector: true })
        }
        setMemories((prev) =>
          prev.map((m) => {
            if (m.id === id) {
              return { ...m, has_vector: true }
            }
            return m
          })
        )
      } else {
        throw new Error(res.error ?? '计算失败')
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败')
    } finally {
      setDetailSaving(false)
    }
  }

  // 单条删除
  async function handleDeleteSingle(id: number) {
    if (!confirm(`确定要永久物理删除记忆 #${id} 吗？`)) return
    try {
      await deleteMemory(id)
      toast.success(`已删除记忆 #${id}`)
      setDetailOpen(false)
      setMemories((prev) => prev.filter((m) => m.id !== id))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  // 批量物理删除
  async function handleBatchDelete() {
    const count = selectedIds.length
    if (!count) return
    if (!confirm(`⚠️ 警告：确定要永久物理擦除这 ${count} 条记忆吗？这会导致相关的关联标签被清理，不可逆！`)) return
    
    setLoading(true)
    try {
      await batchDeleteMemories(selectedIds)
      toast.success(`成功批量物理删除了 ${count} 条记忆`)
      setSelectedIds([])
      await loadData(page)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '批量删除失败')
      setLoading(false)
    }
  }

  // 批量重新向量化计算 (SSE 流)
  function handleBatchReEmbed() {
    const count = selectedIds.length
    if (!count) {
      toast.warning('请先勾选要重新向量化的记忆')
      return
    }
    if (streamRunning) return
    setStreamRunning(true)
    
    setStreamLog([])
    setStreamProgress(null)
    setStreamOpenTitle('批量重新向量特征计算')
    setStreamOpen(true)
    
    setStreamLog((prev) => [...prev, `[INIT] 开始对选中的 ${count} 条记忆进行向量重刷...`])
    
    void runPostStream('/api/memories/batch/re-embed', selectedIds, (state) => {
      setStreamProgress(state)
      if (state.processed !== undefined) {
        const logLine = `[PROGRESS] 进度: ${Math.round(state.progress * 100)}% | 已处理: ${state.processed}/${state.total} | 失败: ${state.errors ?? 0}`
        setStreamLog((prev) => {
          // 只保留最后50条，避免极多进度时卡死DOM
          const next = [...prev, logLine]
          return next.slice(-50)
        })
      }
      if (state.done) {
        setStreamLog((prev) => [...prev, `[SUCCESS] 重新计算特征完毕。成功: ${Number(state.processed) - Number(state.errors)}, 失败: ${state.errors}`])
        toast.success('批量特征计算已全部完成')
        setSelectedIds([])
        void loadData(page)
      }
      if (state.error) {
        setStreamLog((prev) => [...prev, `[ERROR] 流式异常中断: ${state.error}`])
        toast.error(`流式异常: ${state.error}`)
      }
    }).catch((err) => {
      const msg = err instanceof Error ? err.message : '连接错误'
      setStreamLog((prev) => [...prev, `[CRITICAL_FAIL] 传输通道不可用: ${msg}`])
      toast.error(`传输失败: ${msg}`)
    }).finally(() => {
      setStreamRunning(false)
    })
  }

  // 批量提取标签 (SSE 流)
  function handleBatchExtractTags() {
    const count = selectedIds.length
    if (!count) {
      toast.warning('请先勾选要提取 Tag 的记忆')
      return
    }
    if (streamRunning) return
    setStreamRunning(true)
    
    setStreamLog([])
    setStreamProgress(null)
    setStreamOpenTitle('批量 LLM 标签提取（Tag Extraction）')
    setStreamOpen(true)
    
    setStreamLog((prev) => [...prev, `[INIT] 开始对选中的 ${count} 条记忆异步触发 LLM Tag 提取分析；tag_batch_size=${tagBatchSize}，tag_write_policy=${tagWritePolicy}...`])
    
    void runPostStream('/api/memories/batch/extract-tags', selectedIds, (state) => {
      setStreamProgress(state)
      if (state.processed !== undefined) {
        const logLine = `[PROGRESS] 分析中: ${Math.round(state.progress * 100)}% | 已分析: ${state.processed}/${state.total} | 写入: ${state.tagged ?? 0} | 失败: ${state.errors ?? 0}`
        setStreamLog((prev) => {
          const next = [...prev, logLine]
          return next.slice(-50)
        })
      }
      if (state.done) {
        setStreamLog((prev) => [...prev, `[SUCCESS] 标签处理提取完毕！`])
        toast.success('批量标签分析提取已全部完成')
        setSelectedIds([])
        void loadData(page)
      }
      if (state.error) {
        setStreamLog((prev) => [...prev, `[ERROR] 标签分析中断: ${state.error}`])
        toast.error(`分析中断: ${state.error}`)
      }
    }, {
      payload: {
        extract_tags: true,
        tag_batch_size: tagBatchSize,
        tag_write_policy: tagWritePolicy,
      },
    }).catch((err) => {
      const msg = err instanceof Error ? err.message : '连接错误'
      setStreamLog((prev) => [...prev, `[CRITICAL_FAIL] 无法分析: ${msg}`])
      toast.error(`分析失败: ${msg}`)
    }).finally(() => {
      setStreamRunning(false)
    })
  }

  const isAllSelected = memories.length > 0 && selectedIds.length === memories.length

  return (
    <div className="flex flex-col gap-6">
      {/* ─── 过滤器卡片 ─── */}
      <Card>
        <CardHeader className="py-4 shrink-0">
          <CardTitle>记忆管理器</CardTitle>
          <CardDescription>
            直接管理 WaveMemory 引擎中的长期记忆（总容量 17 万条）。支持条件检索、在线编辑内容与权重分，以及批量重新特征计算与标签提取。
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <form className="flex flex-col gap-4" onSubmit={handleSearchSubmit}>
            <FieldGroup className="grid gap-4 md:grid-cols-5">
              <Field>
                <FieldLabel htmlFor="mem-search">关键词</FieldLabel>
                <Input
                  id="mem-search"
                  placeholder="搜索记忆内容..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel>记忆来源</FieldLabel>
                <Select value={source || 'all'} onValueChange={(val) => setSource(val === 'all' ? '' : val)}>
                  <SelectTrigger>
                    <SelectValue placeholder="全部来源" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部来源</SelectItem>
                    <SelectItem value="live">live（群聊长期记忆）</SelectItem>
                    <SelectItem value="chat">chat（群聊/私聊）</SelectItem>
                    <SelectItem value="noise">noise（日常琐碎）</SelectItem>
                    <SelectItem value="core">core（核心记忆）</SelectItem>
                    <SelectItem value="identity_quarantine">identity_quarantine（身份隔离）</SelectItem>
                    <SelectItem value="evolution">evolution（性格进化）</SelectItem>
                    <SelectItem value="bzz_experience">bzz_experience（第一人称经历）</SelectItem>
                    <SelectItem value="experience">experience（经历）</SelectItem>
                    <SelectItem value="lore">lore（背景知识）</SelectItem>
                    <SelectItem value="book_lore">book_lore（小说世界观）</SelectItem>
                    <SelectItem value="oni_lore">oni_lore（缺氧策略知识）</SelectItem>
                    <SelectItem value="bot_reply">bot_reply（Bot 回复素材）</SelectItem>
                    <SelectItem value="fewshot">fewshot（风格范例）</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel>发送人</FieldLabel>
                <Select value={sender || 'all'} onValueChange={(val) => setSender(val === 'all' ? '' : val)}>
                  <SelectTrigger>
                    <SelectValue placeholder="全部发送人" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部发送人</SelectItem>
                    {senders.map((s) => (
                      <SelectItem key={s.name} value={s.name}>
                        {s.name} ({s.count})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel>Tag 状态</FieldLabel>
                <Select value={hasTags || 'all'} onValueChange={(val) => setHasTags(val === 'all' ? '' : val)}>
                  <SelectTrigger>
                    <SelectValue placeholder="全部状态" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部</SelectItem>
                    <SelectItem value="yes">有标签</SelectItem>
                    <SelectItem value="no">无标签</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel>特征向量</FieldLabel>
                <Select value={hasVector || 'all'} onValueChange={(val) => setHasVector(val === 'all' ? '' : val)}>
                  <SelectTrigger>
                    <SelectValue placeholder="全部状态" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部</SelectItem>
                    <SelectItem value="yes">有向量</SelectItem>
                    <SelectItem value="no">无向量</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
            </FieldGroup>

            <div className="flex flex-wrap items-center gap-2">
              <Button disabled={loading || isPendingQuery} type="submit">
                {isPendingQuery ? <Loader2Icon className="animate-spin" data-icon="inline-start" /> : <SearchIcon data-icon="inline-start" />}
                搜索记忆
              </Button>
              <Button disabled={loading} variant="outline" type="button" onClick={handleResetFilters}>
                <Undo2Icon data-icon="inline-start" />
                重置
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {error ? (
        <Alert variant="destructive">
          <AlertCircleIcon />
          <AlertTitle>记忆列表加载失败</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {/* ─── 批量控制条（有选中时出现） ─── */}
      {selectedIds.length > 0 ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-primary bg-primary/5 p-3.5 animate-in slide-in-from-top duration-200">
          <Badge variant="secondary" className="bg-primary/10 text-primary hover:bg-primary/10 border-primary/20">
            已勾选 {selectedIds.length} 条记忆
          </Badge>
          <Button variant="destructive" size="sm" onClick={() => void handleBatchDelete()}>
            <Trash2Icon data-icon="inline-start" />
            一键物理删除
          </Button>
          <Button disabled={streamRunning || selectedIds.length === 0} variant="outline" size="sm" className="border-primary/20 hover:bg-primary/5" onClick={handleBatchReEmbed}>
            <RefreshCwIcon data-icon="inline-start" />
            {streamRunning ? '处理中' : '批量重新向量特征计算'}
          </Button>
          <Button disabled={streamRunning || selectedIds.length === 0} variant="outline" size="sm" className="border-primary/20 hover:bg-primary/5" onClick={handleBatchExtractTags}>
            <TagIcon data-icon="inline-start" />
            {streamRunning ? '处理中' : '批量提取 Tag'}
          </Button>

          {/* 齿轮配置选项 */}
          <Button variant="outline" size="sm" className="border-primary/20" onClick={() => setConfigDialogOpen(true)} title="批量提取 Tag 参数配置">
            <RefreshCwIcon className="size-3.5" />
            提取配置 ⚙️
          </Button>

          <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setSelectedIds([])}>
            取消选择
          </Button>
        </div>
      ) : null}

      {/* 批量提取 Tag 参数配置 Dialog */}
      <Dialog open={configDialogOpen} onOpenChange={setConfigDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Tag 提取参数配置 ⚙️</DialogTitle>
            <DialogDescription>配置对手动选中的记忆进行批量 Tag 提取分析时的 LLM 提供商、维度及合并更新策略。</DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <TagExtractionConfigPanel
              title="LLM 运行配置"
              description="默认 missing_only 只处理无 Tag 记忆，append/replace 仅作用于本次勾选范围。"
              onOptionsChange={(opts) => {
                handleTagOptionsChange(opts)
              }}
              disabled={streamRunning}
            />
          </div>
          <div className="flex justify-end border-t pt-3">
            <Button size="sm" onClick={() => setConfigDialogOpen(false)}>
              确定并保存
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* ─── 记忆数据主表 ─── */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-4 py-4 shrink-0">
          <div className="flex flex-col gap-1">
            <CardTitle>记忆条目列表</CardTitle>
            <CardDescription>
              {total !== null ? `无筛选累计记录数：${total.toLocaleString()} 条` : `${memories.length} 条过滤匹配结果`}
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          {loading ? (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : memories.length === 0 ? (
            <p className="text-sm text-muted-foreground p-6 text-center">暂无符合条件的记忆条目。</p>
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
                    <TableHead>内容</TableHead>
                    <TableHead className="w-24">发送者</TableHead>
                    <TableHead className="w-44">来源</TableHead>
                    <TableHead className="w-36">关联标签 (Tags)</TableHead>
                    <TableHead className="w-16 text-center">特征向量</TableHead>
                    <TableHead className="w-32">创建时间</TableHead>
                    <TableHead className="w-20 text-right">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {memories.map((m) => {
                    const hasV = m.has_vector
                    const isRowChecked = selectedIds.includes(m.id)
                    return (
                      <TableRow key={m.id} className={isRowChecked ? 'bg-primary/5 hover:bg-primary/5' : ''}>
                        <TableCell>
                          <input
                            type="checkbox"
                            checked={isRowChecked}
                            onChange={(e) => handleRowCheckChange(m.id, e.target.checked)}
                          />
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">#{m.id}</TableCell>
                        <TableCell className="max-w-md truncate font-normal cursor-pointer hover:text-primary transition-colors" onClick={() => void handleOpenDetail(m.id)}>
                          {m.content}
                        </TableCell>
                        <TableCell className="max-w-28 truncate text-muted-foreground">{m.sender_name || '-'}</TableCell>
                        <TableCell>
                          <Badge variant="secondary" className="w-fit font-mono text-[10px] uppercase">
                            {m.source || '—'}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-wrap gap-1">
                            {(m.tags ?? []).slice(0, 2).map((t, idx) => (
                              <Badge key={`${t.name}-${idx}`} className={tagBadgeClass(t.type || 'keyword')}>
                                {t.name}
                              </Badge>
                            ))}
                            {(m.tags ?? []).length > 2 ? (
                              <Badge variant="outline" className="font-mono text-[9px]">
                                +{(m.tags ?? []).length - 2}
                              </Badge>
                            ) : null}
                          </div>
                        </TableCell>
                        <TableCell className="text-center">
                          <span className={hasV ? 'text-emerald-500 font-bold' : 'text-destructive font-bold'}>
                            {hasV ? '●' : '○'}
                          </span>
                        </TableCell>
                        <TableCell className="text-muted-foreground font-mono text-xs whitespace-nowrap">
                          {formatTime(m.timestamp)}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex justify-end gap-1.5">
                            <Button variant="ghost" className="size-7 p-0" onClick={() => void handleOpenDetail(m.id)} title="查看详情">
                              <FileEditIcon className="size-3.5" />
                            </Button>
                            <Button variant="ghost" className="size-7 p-0 text-destructive hover:text-destructive hover:bg-destructive/10" onClick={() => void handleDeleteSingle(m.id)} title="物理删除">
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

          {/* ─── 分页器 ─── */}
          {memories.length > 0 ? (
            <div className="mt-4 flex items-center justify-center gap-4">
              <Button disabled={loading || page <= 1} variant="outline" size="sm" onClick={() => void loadData(page - 1)}>
                <ChevronLeftIcon className="size-4" />
                上一页
              </Button>
              <span className="font-mono text-xs text-muted-foreground">
                第 {page} 页
              </span>
              <Button disabled={loading || !hasMore} variant="outline" size="sm" onClick={() => void loadData(page + 1)}>
                下一页
                <ChevronRightIcon className="size-4" />
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>

      {/* ─── 详情 Sheet 弹窗 ─── */}
      <Sheet open={detailOpen} onOpenChange={(open) => {
        setDetailOpen(open)
        if (!open) {
          setHistoryStack([])
        }
      }}>
        <SheetContent className="w-full gap-0 sm:max-w-2xl flex flex-col pr-0 sm:pr-2">
          <SheetHeader className="pb-4 border-b shrink-0 pr-6">
            <SheetTitle>记忆明细</SheetTitle>
            <SheetDescription>
              {detailId ? `记忆 ID：#${detailId} | 直接在线编辑内容或调整算法召回比重重要度。` : '载入中…'}
            </SheetDescription>
          </SheetHeader>

          <ScrollArea className="flex-1 pr-6">
            <div className="flex flex-col gap-4 py-6">
              {detailLoading ? (
                <div className="flex flex-col gap-4">
                  <Skeleton className="h-32 w-full" />
                  <Skeleton className="h-24 w-full" />
                </div>
              ) : detailError ? (
                <Alert variant="destructive">
                  <AlertCircleIcon />
                  <AlertTitle>详情加载失败</AlertTitle>
                  <AlertDescription>{detailError}</AlertDescription>
                </Alert>
              ) : detail ? (
                <>
                  <Field>
                    <FieldLabel>内容编辑 (content)</FieldLabel>
                    <Textarea
                      className="min-h-[140px] text-xs leading-relaxed"
                      value={detail.content}
                      onChange={(e) => setDetail({ ...detail, content: e.target.value })}
                    />
                    <FieldDescription>直接改写后，检索匹配时将使用最新改写内容进行向量计算。</FieldDescription>
                  </Field>

                  <div className="grid gap-3 sm:grid-cols-2 text-xs border rounded-lg p-3 bg-muted/20">
                    <div><span className="text-muted-foreground font-medium">发送人：</span>{detail.sender_name || detail.sender_id || '-'}</div>
                    <div><span className="text-muted-foreground font-medium">会话/群 ID：</span>{detail.group_id || '-'}</div>
                    <div><span className="text-muted-foreground font-medium">重要度系数：</span>
                      <Input
                        type="number"
                        className="w-20 inline-block h-8 py-0.5 px-2 ml-1 text-xs"
                        step="0.01"
                        min="0"
                        max="1"
                        value={detail.importance ?? 0.5}
                        onChange={(e) => setDetail({ ...detail, importance: Number(e.target.value) || 0 })}
                      />
                    </div>
                    <div><span className="text-muted-foreground font-medium">特征向量：</span>
                      <Badge variant={detail.has_vector ? 'secondary' : 'destructive'} className="text-[10px]">
                        {detail.has_vector ? '✓ 已入库' : '✗ 缺失'}
                      </Badge>
                    </div>
                    <div><span className="text-muted-foreground font-medium">创建时间：</span>{formatTime(detail.timestamp)}</div>
                    <div><span className="text-muted-foreground font-medium">被调次数：</span>{detail.access_count ?? 0}</div>
                  </div>

                  {/* 回退上一级按钮（当有穿透历史时显现） */}
                  {historyStack.length > 0 ? (
                    <Button variant="outline" size="sm" className="w-fit" onClick={handleBackDetail}>
                      <Undo2Icon className="size-3.5" />
                      返回上一层记忆 (#{historyStack[historyStack.length - 1]})
                    </Button>
                  ) : null}

                  {/* 100% 双向标签交互 */}
                  <Field>
                    <FieldLabel>关联标签 (Tags)</FieldLabel>
                    <div className="flex flex-col gap-3 rounded-lg border bg-muted/10 p-3">
                      <div className="flex flex-wrap gap-1.5 min-h-8 items-center">
                        {(detail.tags ?? []).length === 0 ? (
                          <span className="text-xs text-muted-foreground">暂无关联标签。</span>
                        ) : (
                          (detail.tags ?? []).map((t, idx) => (
                            <Badge key={`${t.name}-${idx}`} className={`${tagBadgeClass(t.type || 'keyword')} pr-1 flex items-center gap-1`}>
                              <span>{t.name}</span>
                              <button
                                type="button"
                                className="hover:bg-foreground/20 rounded-full size-3 flex items-center justify-center font-bold text-[8px] transition-colors"
                                onClick={() => void handleRemoveTag(t.name)}
                                title="从当前记忆物理移除该标签关联"
                              >
                                ✕
                              </button>
                            </Badge>
                          ))
                        )}
                      </div>
                      
                      {/* 轻量回车新增标签框 */}
                      <form onSubmit={handleAddTag} className="flex gap-2">
                        <Input
                          placeholder="+ 回车新增自定义标签..."
                          className="h-8 text-xs max-w-xs"
                          value={newTagName}
                          onChange={(e) => setNewTagName(e.target.value)}
                        />
                        <Button type="submit" size="sm" className="h-8 text-xs">
                          关联
                        </Button>
                      </form>
                    </div>
                  </Field>

                  {/* 100% 真实 C-HNSW 向量相似记忆诊断推荐 */}
                  <Card className="border border-primary/20 bg-primary/5">
                    <CardHeader className="py-3 shrink-0">
                      <div className="flex items-center gap-2">
                        <span className="text-primary font-bold">✨</span>
                        <CardTitle className="text-sm">HNSW 相似记忆碰撞 (Similar Memories)</CardTitle>
                      </div>
                      <CardDescription className="text-xs">
                        基于底层 C-HNSW 向量空间在 15ms 内匹配出的前 3 条最相似记录，点击行可无缝折叠穿透查看。
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="pt-0 flex flex-col gap-2">
                      {similarLoading ? (
                        <div className="flex items-center gap-2 py-4 justify-center">
                          <Loader2Icon className="animate-spin text-primary size-4" />
                          <span className="text-xs text-muted-foreground font-mono">HNSW 极速计算中...</span>
                        </div>
                      ) : similarItems.length === 0 ? (
                        <p className="text-xs text-muted-foreground text-center py-4">未找到该条记忆的高相似碰撞记录（可能未进行特征计算）。</p>
                      ) : (
                        <div className="flex flex-col gap-2">
                          {similarItems.map((item) => (
                            <div
                              key={item.id}
                              onClick={() => void handleOpenDetail(item.id)}
                              className="group flex flex-col gap-1 rounded-lg border border-border/40 p-2.5 bg-muted/20 hover:bg-muted/40 cursor-pointer transition-all hover:border-primary/20"
                            >
                              <div className="flex items-center justify-between text-[10px] font-mono">
                                <span className="text-muted-foreground">#{item.id}</span>
                                <div className="flex items-center gap-1.5">
                                  <Badge variant="secondary" className="text-[8px] px-1 uppercase py-0 leading-none h-4">
                                    {item.source || 'chat'}
                                  </Badge>
                                  <span className="text-primary font-bold">
                                    相似度 {item.similarity}%
                                  </span>
                                </div>
                              </div>
                              <p className="text-xs leading-relaxed text-foreground/80 group-hover:text-primary transition-colors line-clamp-2">
                                {item.content}
                              </p>
                            </div>
                          ))}
                        </div>
                      )}
                    </CardContent>
                  </Card>

                  <div className="flex flex-wrap gap-2 border-t pt-4">
                    <Button disabled={detailSaving} onClick={() => void handleSaveDetail()}>
                      {detailSaving ? <Loader2Icon className="animate-spin" data-icon="inline-start" /> : <SaveIcon data-icon="inline-start" />}
                      保存修改
                    </Button>
                    <Button disabled={detailSaving} variant="outline" onClick={() => void handleReEmbedSingle(detail.id)}>
                      重新向量特征计算
                    </Button>
                    <Button disabled={detailSaving} variant="destructive" className="ml-auto" onClick={() => void handleDeleteSingle(detail.id)}>
                      🗑 物理删除
                    </Button>
                  </div>
                </>
              ) : null}
            </div>
          </ScrollArea>
        </SheetContent>
      </Sheet>

      {/* ─── SSE 批量控制台弹窗 ─── */}
      <Dialog open={streamOpen} onOpenChange={setStreamOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{streamTitle}</DialogTitle>
            <DialogDescription>正在流式推送批量进程，不要关闭本弹窗。完毕后将自动载入最新数据。</DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-4 py-4">
            {streamProgress ? (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">
                    进度：{Math.round(streamProgress.progress * 100)}%
                  </span>
                  <span className="font-mono">
                    已处理 {streamProgress.processed ?? 0}/{streamProgress.total}（失败：{streamProgress.errors ?? 0}）
                  </span>
                </div>
                <div className="w-full h-2 rounded-full overflow-hidden bg-muted">
                  <div className="h-full rounded-full bg-primary transition-all duration-300" style={{ width: `${streamProgress.progress * 100}%` }} />
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-center gap-2 py-4">
                <Loader2Icon className="animate-spin text-primary size-5" />
                <span className="text-xs text-muted-foreground">正在与后台进程握手连接，请稍后...</span>
              </div>
            )}

            <div className="flex flex-col gap-1.5">
              <span className="text-xs font-semibold text-foreground">实时执行控制台：</span>
              <ScrollArea className="h-44 rounded-lg border bg-muted/60 p-3 font-mono text-[10px] text-muted-foreground leading-relaxed">
                {streamLog.length === 0 ? (
                  <div className="text-muted-foreground">等待日志信号...</div>
                ) : (
                  streamLog.map((line, idx) => (
                    <div key={idx} className={line.includes('[CRITICAL_FAIL]') || line.includes('[ERROR]') ? 'text-destructive' : line.includes('[SUCCESS]') ? 'text-emerald-500' : ''}>
                      {line}
                    </div>
                  ))
                )}
              </ScrollArea>
            </div>

            {streamProgress?.done ? (
              <Alert className="bg-emerald-500/10 border-emerald-500/20 text-emerald-500">
                <CheckCircle2Icon className="size-4" />
                <AlertTitle>处理完毕</AlertTitle>
                <AlertDescription>批量流进程已 100% 成功执行完毕，您可以安全关闭弹窗。</AlertDescription>
              </Alert>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
export default MemoriesPage
