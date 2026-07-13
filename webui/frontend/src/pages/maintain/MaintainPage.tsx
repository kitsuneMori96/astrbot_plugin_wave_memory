import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertCircleIcon,
  CheckCircle2Icon,
  HelpCircleIcon,
  Loader2,
  PlayIcon,
  RefreshCwIcon,
  CheckIcon,
  XIcon,
} from 'lucide-react'
import { toast } from 'sonner'

import { getTagQuality, getAuditSuggestions, resolveAuditSuggestion, resolveAuditBatch, type TagQualityPayload, type AuditSuggestionItem, type TagExecutionOptions, type TagWritePolicy } from '@/api/tags'
import { getSystemStatus, type SystemPayload } from '@/api/system'
import { runPostStream, type StreamProgress } from '@/api/memories'
import { TagExtractionConfigPanel } from '@/components/tag/TagExtractionConfigPanel'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Field, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'

export function MaintainPage() {
  const [sys, setSys] = useState<SystemPayload | null>(null)
  const [quality, setQuality] = useState<TagQualityPayload | null>(null)
  const [suggestions, setSuggestions] = useState<AuditSuggestionItem[]>([])
  const [pendingCount, setPendingCount] = useState(0)

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState('extract')

  // SSE 状态
  const [running, setRunning] = useState(false)
  const [streamProgress, setStreamProgress] = useState<StreamProgress | null>(null)
  const [streamLog, setStreamLog] = useState<string[]>([])
  const [streamType, setStreamType] = useState<'extract' | 'audit' | null>(null)
  const [taskState, setTaskState] = useState<'idle' | 'running' | 'paused' | 'stopped' | 'done' | 'error'>('idle')
  const [cumulativeProcessed, setCumulativeProcessed] = useState(0)
  const [cumulativeTagged, setCumulativeTagged] = useState(0)
  const [cumulativeErrors, setCumulativeErrors] = useState(0)
  const stopRequestedRef = useRef(false)
  const stopModeRef = useRef<'pause' | 'stop' | null>(null)
  const currentAbortRef = useRef<AbortController | null>(null)

  // 批量分析提取参数
  const [tagBatchSize, setTagBatchSize] = useState(20)
  const [tagWritePolicy, setTagWritePolicy] = useState<TagWritePolicy>('missing_only')
  const [skipShortMinLength, setSkipShortMinLength] = useState(10)
  const [maxAutoBatches, setMaxAutoBatches] = useState(200)

  const handleTagOptionsChange = useCallback((options: Required<TagExecutionOptions>) => {
    setTagBatchSize(options.tag_batch_size)
    setTagWritePolicy(options.tag_write_policy)
    setSkipShortMinLength(options.skip_short_min_length)
  }, [])

  // 审计策略参数
  const [auditStrategy, setAuditStrategy] = useState('mixed')
  const [auditCount, setAuditCount] = useState(200)

  async function loadData() {
    setError('')
    try {
      const [sysPayload, qualityPayload, auditPayload] = await Promise.all([
        getSystemStatus(),
        getTagQuality(),
        getAuditSuggestions('pending', '', 100, 0),
      ])
      setSys(sysPayload)
      setQuality(qualityPayload)
      setSuggestions(auditPayload.suggestions ?? [])
      setPendingCount(auditPayload.counts?.pending ?? 0)
    } catch (err) {
      setError(err instanceof Error ? err.message : '标签与维护数据加载失败')
    }
  }

  useEffect(() => {
    async function init() {
      setLoading(true)
      await loadData()
      setLoading(false)
    }
    void init()
  }, [])

  // 触发无标签记忆的 LLM 批量分析提取 (SSE)
  function handleStartLLMExtract() {
    if (running) return
    stopRequestedRef.current = false
    stopModeRef.current = null
    currentAbortRef.current = null
    setRunning(true)
    setTaskState('running')
    setStreamType('extract')
    setStreamProgress(null)
    setCumulativeProcessed(0)
    setCumulativeTagged(0)
    setCumulativeErrors(0)
    setStreamLog(['[INIT] 正在启动无标签记忆的 LLM 智能分类与 Tag 批分析提取引擎...'])

    const runLoop = async () => {
      let rounds = 0
      let previousRemaining: number | null = null
      let totalInitial: number | null = null
      let processedTotal = 0
      let taggedTotal = 0
      let errorsTotal = 0

      while (!stopRequestedRef.current) {
        rounds += 1
        const controller = new AbortController()
        currentAbortRef.current = controller
        const result = await runPostStream(`/api/tags/batch-extract?tag_batch_size=${tagBatchSize}&tag_write_policy=${tagWritePolicy}&skip_short_min_length=${skipShortMinLength}`, [], (state) => {
          if (totalInitial === null && typeof state.total === 'number') {
            totalInitial = state.total
          }
          const displayTotal = totalInitial ?? state.total
          const displayProcessed = processedTotal + (state.processed ?? 0)
          const displayTagged = taggedTotal + (state.tagged ?? 0)
          const displayErrors = errorsTotal + (state.errors ?? 0)
          const aggregateProgress = displayTotal > 0 ? Math.min(displayProcessed / displayTotal, 1) : state.progress

          setStreamProgress({
            ...state,
            progress: aggregateProgress,
            processed: displayProcessed,
            total: displayTotal,
            tagged: displayTagged,
            errors: displayErrors,
          })
          if (state.message) {
            setStreamLog((prev) => [...prev, `[EXTRACT] ${state.message}`].slice(-80))
          } else if (state.processed !== undefined) {
            setStreamLog((prev) => [
              ...prev,
              `[EXTRACT] 本轮: ${state.processed}/${state.total} | 已处理总计: ${displayProcessed}/${displayTotal} | 写入: ${displayTagged} | 失败: ${displayErrors}`,
            ].slice(-80))
          }
        }, { signal: controller.signal })
        currentAbortRef.current = null

        if (!result) break
        processedTotal += result.processed ?? 0
        taggedTotal += result.tagged ?? 0
        errorsTotal += result.errors ?? 0
        setCumulativeProcessed(processedTotal)
        setCumulativeTagged(taggedTotal)
        setCumulativeErrors(errorsTotal)

        const displayTotal = totalInitial ?? result.total
        setStreamProgress({
          ...result,
          processed: processedTotal,
          total: displayTotal,
          tagged: taggedTotal,
          errors: errorsTotal,
          progress: displayTotal > 0 ? Math.min(processedTotal / displayTotal, 1) : result.progress,
        })

        if (stopRequestedRef.current) break
        if (!result.partial || !result.remaining || result.remaining <= 0) break
        if ((result.tagged ?? 0) <= 0 || (previousRemaining !== null && result.remaining >= previousRemaining)) {
          throw new Error(`本轮已处理 ${result.processed ?? 0} 条但没有减少未标注数量，请调小 batch_size 或检查 Tag LLM 输出。`)
        }
        if (rounds >= maxAutoBatches) {
          throw new Error(`已达到最大自动批次数 ${maxAutoBatches}，暂停以避免无限循环。可调大上限后继续。`)
        }

        previousRemaining = result.remaining
        setStreamLog((prev) => [...prev, `[NEXT] 已处理总计 ${processedTotal} 条；剩余 ${result.remaining} 条，自动继续下一批...`].slice(-80))
      }

      if (stopRequestedRef.current) {
        const stopped = stopModeRef.current === 'stop'
        setStreamLog((prev) => [...prev, `${stopped ? '[STOP]' : '[PAUSE]'} 已${stopped ? '停止' : '暂停'}。已处理总计 ${processedTotal} 条，写入 ${taggedTotal} 条。`].slice(-80))
        toast.info(stopped ? '标签提取已停止' : '标签提取已暂停')
      }
    }

    void runLoop().then(() => {
      if (!stopRequestedRef.current) {
        setStreamLog((prev) => [...prev, '[SUCCESS] 批量标签提取任务已结束。'])
        setTaskState('done')
      } else {
        setTaskState(stopModeRef.current === 'stop' ? 'stopped' : 'paused')
      }
      setRunning(false)
      currentAbortRef.current = null
      void loadData()
    }).catch((err) => {
      const isAbort = err instanceof DOMException && err.name === 'AbortError'
      if (stopRequestedRef.current || isAbort) {
        const stopped = stopModeRef.current === 'stop'
        setStreamLog((prev) => [...prev, `${stopped ? '[STOP]' : '[PAUSE]'} 已${stopped ? '停止' : '暂停'}当前标签提取任务。`].slice(-80))
        setTaskState(stopped ? 'stopped' : 'paused')
        toast.info(stopped ? '标签提取已停止' : '标签提取已暂停')
      } else {
        const msg = err instanceof Error ? err.message : '连接异常'
        setStreamLog((prev) => [...prev, `[CRITICAL] 提取失败: ${msg}`])
        setTaskState('error')
        toast.error(`提取异常: ${msg}`)
      }
      setRunning(false)
      currentAbortRef.current = null
      void loadData()
    })
  }

  function handlePauseLLMExtract() {
    stopRequestedRef.current = true
    stopModeRef.current = 'pause'
    currentAbortRef.current?.abort()
    setStreamLog((prev) => [...prev, '[PAUSE] 正在暂停：将停止自动继续下一批，并中止当前连接...'].slice(-80))
  }

  function handleStopLLMExtract() {
    stopRequestedRef.current = true
    stopModeRef.current = 'stop'
    currentAbortRef.current?.abort()
    setStreamLog((prev) => [...prev, '[STOP] 正在停止：将中止当前连接并保留已完成的处理结果...'].slice(-80))
  }

  // 触发自检审计 (SSE)
  function handleStartAudit() {
    if (running) return
    setRunning(true)
    setTaskState('running')
    setStreamType('audit')
    setStreamProgress(null)
    setStreamLog([`[INIT] 正在触发 Tag 质量自检，策略: ${auditStrategy}...`])

    void runPostStream(`/api/tags/audit/trigger?strategy=${auditStrategy}&total_count=${auditCount}`, [], (state: any) => {
      setStreamProgress({
        progress: (state.progress ?? 0) / 100,
        processed: state.processed_count ?? 0,
        total: state.total_scanned ?? auditCount,
      })
      if (state.message) {
        setStreamLog((prev) => [...prev, `[AUDIT] ${state.message}`].slice(-60))
      }
    }).then(() => {
      setStreamLog((prev) => [...prev, '[SUCCESS] 系统自检审计完成！'])
      setTaskState('done')
      setRunning(false)
      void loadData()
    }).catch((err) => {
      const msg = err instanceof Error ? err.message : '连接异常'
      setStreamLog((prev) => [...prev, `[CRITICAL] 审计中断: ${msg}`])
      setTaskState('error')
      toast.error(`自检异常: ${msg}`)
      setRunning(false)
    })
  }

  // 处理单条建议
  async function handleResolve(id: string | number, decision: 'approve' | 'reject') {
    try {
      const res = await resolveAuditSuggestion(id, decision)
      if (res.ok) {
        toast.success(decision === 'approve' ? '建议已批准并落库生效' : '建议已拒绝并丢弃')
        void loadData()
      } else {
        toast.error(res.message ?? '处理失败')
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '请求异常')
    }
  }

  // 批量处理全部建议
  async function handleBatchResolve(decision: 'approve' | 'reject') {
    if (suggestions.length === 0) return
    const ids = suggestions.map((s) => s.id)
    try {
      const res = await resolveAuditBatch(ids, decision)
      toast.success(`批量处理完成，共处理 ${res.processed} 条建议`)
      void loadData()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '批量处理异常')
    }
  }

  function sourceTagLabel(item: AuditSuggestionItem): string {
    if (item.source_tag_name) return item.source_tag_name
    const ids = item.tag_ids ?? []
    const names = ids
      .map((id) => item.tag_names?.[String(id)])
      .filter(Boolean)
      .map((tag) => tag?.type ? `${tag.name} (${tag.type})` : String(tag?.name ?? ''))
      .filter(Boolean)
    return names.length ? names.join(' / ') : '-'
  }

  function targetLabel(item: AuditSuggestionItem): string {
    if (item.action === 'merge') {
      return item.target_name || item.target_tag_name || '-'
    }
    if (item.action === 'retype') {
      return item.target_type || item.new_type || '-'
    }
    if (item.action === 'delete') {
      return '删除源标签'
    }
    return '-'
  }

  if (loading) {
    return (
      <div className="flex flex-col gap-6">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    )
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertCircleIcon />
        <AlertTitle>加载失败</AlertTitle>
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    )
  }

  const untaggedCount = Math.max(0, Number(quality?.untagged_memories ?? ((sys?.memories?.total ?? 0) - (sys?.memories?.with_tags ?? 0))))
  const extractableUntaggedCount = Math.max(0, Number(quality?.extractable_untagged_memories ?? untaggedCount))
  const skippedShortUntaggedCount = Math.max(0, Number(quality?.skipped_short_untagged_memories ?? 0))
  const orphanMemoryTagRefs = Math.max(0, Number(quality?.orphan_memory_tag_refs ?? 0))
  const tagCoveragePct = quality ? (quality.coverage * 100).toFixed(1) : '-'

  return (
    <div className="flex flex-col gap-6">
      {/* 头部 Metrics 概览 */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="text-xs">系统总 Tag 数</CardDescription>
            <CardTitle className="text-2xl font-bold font-mono">
              {quality?.total_tags ? new Intl.NumberFormat('zh-CN').format(quality.total_tags) : '-'}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="text-xs">记忆标签覆盖率</CardDescription>
            <CardTitle className="text-2xl font-bold font-mono">
              {tagCoveragePct}%
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="text-xs">待审核治理建议</CardDescription>
            <CardTitle className="text-2xl font-bold font-mono text-amber-500">
              {pendingCount}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="text-xs">可提取无标签记忆</CardDescription>
            <CardTitle className={`text-2xl font-bold font-mono ${extractableUntaggedCount > 0 ? 'text-destructive' : 'text-primary'}`}>
              {extractableUntaggedCount}
            </CardTitle>
            <CardDescription className="text-[10px] leading-relaxed">
              总无标签 {new Intl.NumberFormat('zh-CN').format(untaggedCount)}；短文本跳过 {new Intl.NumberFormat('zh-CN').format(skippedShortUntaggedCount)}
              {orphanMemoryTagRefs > 0 ? `；孤儿关联 ${new Intl.NumberFormat('zh-CN').format(orphanMemoryTagRefs)}` : ''}
            </CardDescription>
          </CardHeader>
        </Card>
      </div>

      <Card className="border-border/60">
        <CardHeader className="pb-4">
          <CardTitle className="text-lg font-semibold">标签与维护中心</CardTitle>
          <CardDescription className="text-sm">
            管理、清洗和审核已有记忆的结构化标签；支持运行大批量分类提取分析和系统自检审计。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Tabs value={activeTab} onValueChange={setActiveTab} className="flex flex-col gap-5">
            <TabsList className="w-fit">
              <TabsTrigger value="extract">批量标签提取</TabsTrigger>
              <TabsTrigger value="audit">审计建议与治理</TabsTrigger>
            </TabsList>

            {/* Tab 1: 批量标签提取 */}
            <TabsContent value="extract" className="flex flex-col gap-4 mt-0">
              <TagExtractionConfigPanel
                title="维护中心 Tag 提取配置"
                description="全库无标签补跑默认使用 missing_only；append/replace 会被后端拒绝，避免误扫全库改写已有标签。"
                onOptionsChange={handleTagOptionsChange}
                disabled={running}
                showSkipShort
              />
              <div className="rounded-xl border border-border/80 bg-muted/5 p-5">
                <div className="flex flex-col gap-1">
                  <h3 className="text-sm font-semibold tracking-tight">批量分类提取分析</h3>
                  <p className="text-xs text-muted-foreground leading-relaxed">
                    扫描 memories 主表中尚未提取任何结构化标签的长期记忆，使用配置的 LLM 分类提取生成对应的标签。
                  </p>
                </div>

                <div className="grid gap-4 mt-6 md:grid-cols-2 max-w-2xl">
                  <Field className="flex flex-col gap-2">
                    <FieldLabel className="text-xs font-semibold">每批处理记忆数量 (batch_size)</FieldLabel>
                    <Input
                      type="number"
                      min={1}
                      max={50}
                      value={tagBatchSize}
                      onChange={(e) => setTagBatchSize(Math.max(1, Math.min(50, Number(e.target.value))))}
                      disabled={running}
                      className="h-9 text-xs"
                    />
                  </Field>
                  <Field className="flex flex-col gap-2">
                    <FieldLabel className="text-xs font-semibold">最大自动批次数</FieldLabel>
                    <Input
                      type="number"
                      min={1}
                      max={2000}
                      value={maxAutoBatches}
                      onChange={(e) => setMaxAutoBatches(Math.max(1, Math.min(2000, Number(e.target.value))))}
                      disabled={running}
                      className="h-9 text-xs"
                    />
                  </Field>
                </div>

                <div className="mt-4 flex flex-wrap items-center gap-2">
                  {extractableUntaggedCount > 0 ? (
                    <>
                      <Button onClick={handleStartLLMExtract} disabled={running} size="sm" className="w-fit h-9">
                        {running && streamType === 'extract' ? (
                          <>
                            <Loader2 className="size-3.5 animate-spin" />
                            <span>提取中...</span>
                          </>
                        ) : (
                          <>
                            <PlayIcon className="size-3.5" />
                            <span>运行标签批量分析 ({extractableUntaggedCount} 条可提取)</span>
                          </>
                        )}
                      </Button>
                      {running && streamType === 'extract' ? (
                        <>
                          <Button onClick={handlePauseLLMExtract} variant="outline" size="sm" className="h-9">
                            <XIcon className="size-3.5" />
                            <span>暂停</span>
                          </Button>
                          <Button onClick={handleStopLLMExtract} variant="destructive" size="sm" className="h-9">
                            <XIcon className="size-3.5" />
                            <span>停止</span>
                          </Button>
                        </>
                      ) : null}
                      <span className="text-[10px] text-muted-foreground">
                        配置：每批 {tagBatchSize} 条，最多自动运行 {maxAutoBatches} 批；可随时暂停。
                      </span>
                    </>
                  ) : (
                    <div className="flex items-center gap-2 text-xs text-primary font-medium bg-primary/5 rounded-lg px-4 py-3 border border-primary/10">
                      <CheckCircle2Icon className="size-4 shrink-0" />
                      <span>当前暂无可提取的无标签长文本记忆；短文本/空内容会被跳过。</span>
                    </div>
                  )}
                </div>
              </div>
            </TabsContent>

            {/* Tab 2: 审计建议与治理 */}
            <TabsContent value="audit" className="flex flex-col gap-5 mt-0">
              {/* 自检审计控制条 */}
              <div className="rounded-xl border border-border/80 bg-muted/5 p-5">
                <div className="flex flex-col gap-1">
                  <h3 className="text-sm font-semibold tracking-tight">触发系统自检审计</h3>
                  <p className="text-xs text-muted-foreground leading-relaxed">
                    使用标签置信度模型扫描数据库，自动诊断并生成关于标签合并、类型重分类或删除建议。
                  </p>
                </div>

                <div className="flex flex-wrap items-end gap-4 mt-6">
                  <Field className="flex flex-col gap-1.5 min-w-[180px]">
                    <FieldLabel className="text-[10px] font-bold text-muted-foreground uppercase">自检扫描算法</FieldLabel>
                    <select
                      value={auditStrategy}
                      onChange={(e) => setAuditStrategy(e.target.value)}
                      disabled={running}
                      className="h-9 bg-background border border-input rounded-md px-3 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                    >
                      <option value="mixed">Mixed · 混合采样</option>
                      <option value="lowconf">LowConf · 低置信关系</option>
                      <option value="orphan">Orphan · 孤立标签</option>
                      <option value="duplicate">Duplicate · 重复候选</option>
                    </select>
                  </Field>

                  <Field className="flex flex-col gap-1.5 w-24">
                    <FieldLabel className="text-[10px] font-bold text-muted-foreground uppercase">采样数量</FieldLabel>
                    <Input
                      type="number"
                      value={auditCount}
                      min={20}
                      max={2000}
                      step={20}
                      onChange={(e) => setAuditCount(Number(e.target.value))}
                      disabled={running}
                      className="h-9 text-xs"
                    />
                  </Field>

                  <div className="flex gap-2">
                    <Button onClick={handleStartAudit} disabled={running} size="sm" className="h-9">
                      {running && streamType === 'audit' ? (
                        <>
                          <Loader2 className="size-3.5 animate-spin" />
                          <span>自检扫描中...</span>
                        </>
                      ) : (
                        <>
                          <RefreshCwIcon className="size-3.5" />
                          <span>启动系统自检</span>
                        </>
                      )}
                    </Button>

                    {suggestions.length > 0 ? (
                      <>
                        <Button onClick={() => void handleBatchResolve('approve')} variant="secondary" size="sm" className="h-9">
                          <CheckIcon className="size-3.5 text-green-500" />
                          <span>全部批准 ({suggestions.length})</span>
                        </Button>
                        <Button onClick={() => void handleBatchResolve('reject')} variant="ghost" size="sm" className="h-9">
                          <XIcon className="size-3.5 text-destructive" />
                          <span>全部拒绝</span>
                        </Button>
                      </>
                    ) : null}
                  </div>
                </div>
              </div>

              {/* 建议大表 */}
              {suggestions.length > 0 ? (
                <div className="rounded-xl border border-border/60 overflow-hidden">
                  <Table>
                    <TableHeader className="bg-muted/30">
                      <TableRow>
                        <TableHead className="text-xs font-semibold">治理类型</TableHead>
                        <TableHead className="text-xs font-semibold">源标签</TableHead>
                        <TableHead className="text-xs font-semibold">目标对象</TableHead>
                        <TableHead className="text-xs font-semibold">治理建议原因</TableHead>
                        <TableHead className="text-xs font-semibold text-right">动作</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {suggestions.map((item) => (
                        <TableRow key={item.id} className="hover:bg-muted/5">
                          <TableCell className="py-3">
                            <Badge variant={item.action === 'delete' ? 'destructive' : 'secondary'} className="text-[10px] font-normal px-2 py-0.5">
                              {item.action === 'merge' ? '合并' : item.action === 'retype' ? '重分类' : '删除'}
                            </Badge>
                          </TableCell>
                          <TableCell className="py-3 font-medium text-xs font-mono">{sourceTagLabel(item)}</TableCell>
                          <TableCell className="py-3 text-xs font-mono text-muted-foreground">
                            {targetLabel(item)}
                          </TableCell>
                          <TableCell className="py-3 text-xs text-muted-foreground max-w-sm leading-relaxed">{item.reason}</TableCell>
                          <TableCell className="py-3 text-right">
                            <div className="flex justify-end gap-1.5">
                              <Button
                                size="icon"
                                variant="outline"
                                className="h-7 w-7 text-green-500 hover:bg-green-50"
                                onClick={() => void handleResolve(item.id, 'approve')}
                              >
                                <CheckIcon className="size-3.5" />
                              </Button>
                              <Button
                                size="icon"
                                variant="outline"
                                className="h-7 w-7 text-destructive hover:bg-destructive-foreground/10"
                                onClick={() => void handleResolve(item.id, 'reject')}
                              >
                                <XIcon className="size-3.5" />
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              ) : !running ? (
                <div className="text-center py-12 border border-dashed rounded-xl bg-muted/5 flex flex-col items-center justify-center gap-2">
                  <HelpCircleIcon className="size-8 text-muted-foreground/50" />
                  <p className="text-sm font-semibold">无待处理治理建议</p>
                  <p className="text-xs text-muted-foreground max-w-xs leading-relaxed">
                    当前没有待处理的合并、重分类或删除建议。可以点击顶部的“启动系统自检”来扫描最新指标。
                  </p>
                </div>
              ) : null}
            </TabsContent>
          </Tabs>

          {/* 进度/流日志大面板 (公用) */}
          {(running || streamLog.length > 0) && (
            <div className="flex flex-col gap-3 mt-6 border-t pt-5">
              <div className="flex items-center justify-between gap-4 text-xs font-medium">
                <span className="flex items-center gap-1.5 text-primary">
                  {running ? <Loader2 className="size-3.5 animate-spin" /> : <CheckCircle2Icon className="size-3.5" />}
                  <span>{taskState === 'running' ? '任务执行中...' : taskState === 'paused' ? '任务已暂停' : taskState === 'stopped' ? '任务已停止' : taskState === 'error' ? '任务异常' : '任务已结束'}</span>
                </span>
                {streamProgress && (
                  <span className="font-mono text-muted-foreground">
                    进度: {Math.round(streamProgress.progress * 100)}% · 已处理总计 {cumulativeProcessed || streamProgress.processed}/{streamProgress.total}
                    {streamType === 'extract' ? ` · 写入 ${cumulativeTagged} · 失败 ${cumulativeErrors}` : ''}
                  </span>
                )}
              </div>

              {streamProgress && (
                <div className="h-2 w-full bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary rounded-full transition-all duration-300"
                    style={{ width: `${Math.round(streamProgress.progress * 100)}%` }}
                  />
                </div>
              )}

              <ScrollArea className="h-44 w-full rounded-lg border bg-black/5 p-4 mt-1 font-mono text-[10px] text-muted-foreground leading-relaxed">
                <div className="flex flex-col gap-1">
                  {streamLog.map((log, idx) => (
                    <div key={idx} className="whitespace-pre-wrap break-all">
                      {log}
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
