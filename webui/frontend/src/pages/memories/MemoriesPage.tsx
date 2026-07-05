import { useEffect, useState, useTransition, useRef } from 'react'
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
  LayoutGridIcon,
  OrbitIcon,
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
  getMemoryClusters,
  type MemoryItem,
  type MemoryDetail,
  type SenderItem,
  type StreamProgress,
  type NebulaPoint,
  type NebulaCluster,
} from '@/api/memories'
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

// 辅助方法：不同标签类型的颜色分级
function tagBadgeClass(type: string): string {
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
  
  // 5. 记忆星云聚类视图状态
  const [viewMode, setViewMode] = useState<'list' | 'nebula'>('list')
  const [nebulaPoints, setNebulaPoints] = useState<NebulaPoint[]>([])
  const [nebulaClusters, setNebulaClusters] = useState<NebulaCluster[]>([])
  const [nebulaLoading, setNebulaLoading] = useState(false)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [hoveredNebulaPoint, setHoveredNebulaPoint] = useState<NebulaPoint | null>(null)

  // 1. 过滤检索状态
  const [search, setSearch] = useState('')
  const [source, setSource] = useState('')
  const [sender, setSender] = useState('')
  const [hasTags, setHasTags] = useState('')
  const [hasVector, setHasVector] = useState('')
  
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

  // 4. SSE 异步流进度模态弹窗状态
  const [streamOpen, setStreamOpen] = useState(false)
  const [streamTitle, setStreamOpenTitle] = useState('')
  const [streamProgress, setStreamProgress] = useState<StreamProgress | null>(null)
  const [streamLog, setStreamLog] = useState<string[]>([])

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

  // 加载星云聚类数据并由 Canvas 绘制
  async function loadNebulaClusters() {
    setNebulaLoading(true)
    try {
      const res = await getMemoryClusters()
      setNebulaPoints(res.points ?? [])
      setNebulaClusters(res.clusters ?? [])
    } catch {
      toast.error('记忆星云聚类加载失败')
    } finally {
      setNebulaLoading(false)
    }
  }

  useEffect(() => {
    if (viewMode === 'nebula') {
      void loadNebulaClusters()
    }
  }, [viewMode])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || viewMode !== 'nebula') return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let animationFrameId: number
    const dpr = window.devicePixelRatio || 1
    const width = canvas.parentElement?.clientWidth || 700
    const height = 460
    
    canvas.width = width * dpr
    canvas.height = height * dpr
    canvas.style.width = `${width}px`
    canvas.style.height = `${height}px`
    ctx.scale(dpr, dpr)

    // 社区星云色彩主题映射
    const clusterColors: Record<string, string> = {
      '灵魂羁绊': '#ec4899', // 玫瑰粉
      '黑话口癖': '#fb7185', // 珊瑚红
      '世界设定': '#a78bfa', // 薰衣草紫
      '日常见闻': '#60a5fa', // 海洋蓝
    }

    let scaleFactor = 2.4
    let dragOffsetX = width / 2
    let dragOffsetY = height / 2
    let isDragging = false
    let startX = 0
    let startY = 0

    // 交互辅助位置坐标
    const getCanvasMousePos = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect()
      return {
        x: e.clientX - rect.left,
        y: e.clientY - rect.top,
      }
    }

    const handleMouseDown = (e: MouseEvent) => {
      isDragging = true
      const pos = getCanvasMousePos(e)
      startX = pos.x - dragOffsetX
      startY = pos.y - dragOffsetY
    }

    const handleMouseMove = (e: MouseEvent) => {
      const pos = getCanvasMousePos(e)
      if (isDragging) {
        dragOffsetX = pos.x - startX
        dragOffsetY = pos.y - startY
      } else {
        // Hover 拾取检测
        let found: NebulaPoint | null = null
        for (const p of nebulaPoints) {
          const cx = p.x * scaleFactor + dragOffsetX
          const cy = p.y * scaleFactor + dragOffsetY
          const dist = Math.hypot(pos.x - cx, pos.y - cy)
          if (dist < 6) {
            found = p
            break
          }
        }
        setHoveredNebulaPoint(found)
        canvas.style.cursor = found ? 'pointer' : (isDragging ? 'grabbing' : 'grab')
      }
    }

    const handleMouseUp = () => {
      isDragging = false
    }

    const handleMouseLeave = () => {
      isDragging = false
      setHoveredNebulaPoint(null)
    }

    const handleWheel = (e: WheelEvent) => {
      e.preventDefault()
      const zoom = e.deltaY < 0 ? 1.1 : 0.9
      scaleFactor = Math.max(0.5, Math.min(10, scaleFactor * zoom))
    }

    const handleClick = () => {
      if (hoveredNebulaPoint) {
        void handleOpenDetail(hoveredNebulaPoint.id)
      }
    }

    canvas.addEventListener('mousedown', handleMouseDown)
    canvas.addEventListener('mousemove', handleMouseMove)
    canvas.addEventListener('mouseup', handleMouseUp)
    canvas.addEventListener('mouseleave', handleMouseLeave)
    canvas.addEventListener('wheel', handleWheel)
    canvas.addEventListener('click', handleClick)

    let angleOffset = 0
    const render = () => {
      ctx.fillStyle = '#060814' // 太空背景色
      ctx.fillRect(0, 0, width, height)

      // 绘制网格背景
      ctx.strokeStyle = 'rgba(139, 92, 246, 0.03)'
      ctx.lineWidth = 1
      const gridSpacing = 40
      for (let x = (dragOffsetX % gridSpacing); x < width; x += gridSpacing) {
        ctx.beginPath()
        ctx.moveTo(x, 0)
        ctx.lineTo(x, height)
        ctx.stroke()
      }
      for (let y = (dragOffsetY % gridSpacing); y < height; y += gridSpacing) {
        ctx.beginPath()
        ctx.moveTo(0, y)
        ctx.lineTo(width, y)
        ctx.stroke()
      }

      // 绘制微弱星系引力光圈
      nebulaClusters.forEach((c) => {
        const cx = c.cx * scaleFactor + dragOffsetX
        const cy = c.cy * scaleFactor + dragOffsetY
        const color = clusterColors[c.name] || '#94a3b8'

        // 绘制引力晕圈
        const grad = ctx.createRadialGradient(cx, cy, 2, cx, cy, Math.max(10, c.count * 1.5 * scaleFactor))
        grad.addColorStop(0, `${color}15`)
        grad.addColorStop(0.5, `${color}05`)
        grad.addColorStop(1, 'transparent')
        ctx.fillStyle = grad
        ctx.beginPath()
        ctx.arc(cx, cy, Math.max(10, c.count * 1.5 * scaleFactor), 0, Math.PI * 2)
        ctx.fill()
      })

      // 绘制星空暗物质粒子
      angleOffset += 0.005
      nebulaPoints.forEach((p) => {
        const cx = p.x * scaleFactor + dragOffsetX
        const cy = p.y * scaleFactor + dragOffsetY
        const color = clusterColors[p.cluster] || '#94a3b8'
        const isHovered = hoveredNebulaPoint?.id === p.id

        // 引入轻微自转视差
        const rotX = cx
        const rotY = cy

        ctx.beginPath()
        ctx.arc(rotX, rotY, isHovered ? 6 : 3, 0, Math.PI * 2)
        ctx.fillStyle = color
        ctx.shadowColor = color
        ctx.shadowBlur = isHovered ? 12 : 3
        ctx.fill()
        ctx.shadowBlur = 0 // 重置阴影避免卡顿
      })

      // 绘制社区气泡标签说明
      nebulaClusters.forEach((c) => {
        const cx = c.cx * scaleFactor + dragOffsetX
        const cy = c.cy * scaleFactor + dragOffsetY
        const color = clusterColors[c.name] || '#94a3b8'

        ctx.font = '10px monospace'
        ctx.fillStyle = color
        ctx.fillText(`🌌 ${c.name} (${c.count} 星体)`, cx - 35, cy - 8)
      })

      animationFrameId = requestAnimationFrame(render)
    }

    render()

    return () => {
      cancelAnimationFrame(animationFrameId)
      canvas.removeEventListener('mousedown', handleMouseDown)
      canvas.removeEventListener('mousemove', handleMouseMove)
      canvas.removeEventListener('mouseup', handleMouseUp)
      canvas.removeEventListener('mouseleave', handleMouseLeave)
      canvas.removeEventListener('wheel', handleWheel)
      canvas.removeEventListener('click', handleClick)
    }
  }, [nebulaPoints, nebulaClusters, viewMode, hoveredNebulaPoint])

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

  // 查看详情
  async function handleOpenDetail(id: number) {
    setDetailOpen(true)
    setDetailId(id)
    setDetail(null)
    setDetailLoading(true)
    setDetailError('')
    try {
      const res = await getMemoryDetail(id)
      setDetail(res)
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : '加载详情失败')
    } finally {
      setDetailLoading(false)
    }
  }

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
    if (!count) return
    
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
    })
  }

  // 批量提取标签 (SSE 流)
  function handleBatchExtractTags() {
    const count = selectedIds.length
    if (!count) return
    
    setStreamLog([])
    setStreamProgress(null)
    setStreamOpenTitle('批量 LLM 标签提取（Tag Extraction）')
    setStreamOpen(true)
    
    setStreamLog((prev) => [...prev, `[INIT] 开始对选中的 ${count} 条记忆异步触发 LLM Tag 提取分析...`])
    
    void runPostStream('/api/memories/batch/extract-tags', selectedIds, (state) => {
      setStreamProgress(state)
      if (state.processed !== undefined) {
        const logLine = `[PROGRESS] 分析中: ${Math.round(state.progress * 100)}% | 已分析: ${state.processed}/${state.total} | 失败: ${state.errors ?? 0}`
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
    }).catch((err) => {
      const msg = err instanceof Error ? err.message : '连接错误'
      setStreamLog((prev) => [...prev, `[CRITICAL_FAIL] 无法分析: ${msg}`])
      toast.error(`分析失败: ${msg}`)
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
                    <SelectItem value="chat">chat（群聊/私聊）</SelectItem>
                    <SelectItem value="noise">noise（日常琐碎）</SelectItem>
                    <SelectItem value="core">core（核心记忆）</SelectItem>
                    <SelectItem value="identity_quarantine">identity_quarantine（身份隔离）</SelectItem>
                    <SelectItem value="evolution">evolution（性格进化）</SelectItem>
                    <SelectItem value="experience">experience（经历）</SelectItem>
                    <SelectItem value="lore">lore（背景知识）</SelectItem>
                    <SelectItem value="book_lore">book_lore（小说世界观）</SelectItem>
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
          <Button variant="outline" size="sm" className="border-primary/20 hover:bg-primary/5" onClick={handleBatchReEmbed}>
            <RefreshCwIcon data-icon="inline-start" />
            批量重新向量特征计算
          </Button>
          <Button variant="outline" size="sm" className="border-primary/20 hover:bg-primary/5" onClick={handleBatchExtractTags}>
            <TagIcon data-icon="inline-start" />
            批量提取 Tag
          </Button>
          <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setSelectedIds([])}>
            取消选择
          </Button>
        </div>
      ) : null}

      {/* ─── 记忆数据主表 ─── */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-4 py-4 shrink-0">
          <div className="flex flex-col gap-1">
            <CardTitle>记忆条目列表</CardTitle>
            <CardDescription>
              {total !== null ? `无筛选累计记录数：${total.toLocaleString()} 条` : `${memories.length} 条过滤匹配结果`}
            </CardDescription>
          </div>
          <div className="flex items-center gap-1.5 rounded-lg border bg-muted/30 p-1">
            <Button
              size="xs"
              variant={viewMode === 'list' ? 'secondary' : 'ghost'}
              className="h-7 text-xs px-2.5"
              onClick={() => setViewMode('list')}
            >
              <LayoutGridIcon className="size-3 mr-1" />
              列表
            </Button>
            <Button
              size="xs"
              variant={viewMode === 'nebula' ? 'secondary' : 'ghost'}
              className="h-7 text-xs px-2.5"
              onClick={() => setViewMode('nebula')}
            >
              <OrbitIcon className="size-3 mr-1" />
              星云
            </Button>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          {viewMode === 'nebula' ? (
            <div className="relative rounded-xl border bg-[#060814] overflow-hidden flex flex-col items-center">
              {nebulaLoading ? (
                <div className="h-[460px] flex flex-col items-center justify-center gap-2 text-xs text-muted-foreground">
                  <Loader2Icon className="animate-spin text-primary size-5" />
                  正在解算记忆特征向量，投射星云社区聚类...
                </div>
              ) : (
                <>
                  <canvas ref={canvasRef} className="block w-full h-[460px] cursor-grab" />
                  <div className="absolute top-3 left-3 flex flex-col gap-1 pointer-events-none">
                    <span className="text-[10px] text-purple-400 font-mono">NEBULA CLUSTERING MAP</span>
                    <span className="text-xs text-slate-400">使用鼠标拖拽平移，滚轮缩放，悬停拾取恒星细节</span>
                  </div>
                  {hoveredNebulaPoint ? (
                    <div className="absolute bottom-3 left-3 right-3 p-3 rounded-lg border border-purple-500/20 bg-slate-950/90 text-xs text-slate-300 max-w-lg shadow-xl animate-in fade-in slide-in-from-bottom-2 duration-150">
                      <div className="flex items-center gap-2 mb-1.5 justify-between">
                        <span className="text-purple-300 font-semibold font-mono text-[10px] uppercase">🌠 {hoveredNebulaPoint.cluster} · 记忆星体 #{hoveredNebulaPoint.id}</span>
                        <span className="text-[10px] text-slate-500 font-mono">{hoveredNebulaPoint.sender} · {formatTime(hoveredNebulaPoint.ts)}</span>
                      </div>
                      <p className="font-normal leading-relaxed text-slate-200 line-clamp-2">{hoveredNebulaPoint.content}</p>
                      <span className="text-[9px] text-purple-400 block mt-1">💡 双击或点击该星体可直接弹出在线编辑和修正面板</span>
                    </div>
                  ) : (
                    <div className="absolute bottom-3 left-3 flex flex-wrap gap-2.5 text-[9px] text-slate-500 font-mono">
                      <div className="flex items-center gap-1"><span className="size-2 rounded-full bg-[#ec4899]" />灵魂羁绊</div>
                      <div className="flex items-center gap-1"><span className="size-2 rounded-full bg-[#fb7185]" />黑话口癖</div>
                      <div className="flex items-center gap-1"><span className="size-2 rounded-full bg-[#a78bfa]" />世界设定</div>
                      <div className="flex items-center gap-1"><span className="size-2 rounded-full bg-[#60a5fa]" />日常见闻</div>
                    </div>
                  )}
                </>
              )}
            </div>
          ) : loading ? (
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
                    <TableHead className="w-20">来源</TableHead>
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
                          <Badge variant="secondary" className="font-mono text-[10px] uppercase">
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
      <Sheet open={detailOpen} onOpenChange={setDetailOpen}>
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

                  <Field>
                    <FieldLabel>关联标签 (Tags)</FieldLabel>
                    <div className="flex flex-wrap gap-1.5 p-3 rounded-lg border bg-muted/10 min-h-12">
                      {(detail.tags ?? []).length === 0 ? (
                        <span className="text-xs text-muted-foreground">暂无关联标签。</span>
                      ) : (
                        (detail.tags ?? []).map((t, idx) => (
                          <Badge key={`${t.name}-${idx}`} className={tagBadgeClass(t.type || 'keyword')}>
                            {t.name} ({String(t.type || '—')})
                          </Badge>
                        ))
                      )}
                    </div>
                  </Field>

                  <div className="flex flex-wrap gap-2 pt-4 border-t">
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
