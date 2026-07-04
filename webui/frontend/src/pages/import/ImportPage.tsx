import { useEffect, useState } from 'react'
import { AlertCircleIcon, CheckCircle2Icon, Loader2, PlayIcon, RefreshCwIcon, Save, StopCircleIcon } from 'lucide-react'
import { toast } from 'sonner'

import { getChannelConfig, type ChannelConfigData } from '@/api/channels'
import { getImportSources, type ImportSourceItem } from '@/api/import'
import { saveFullConfig } from '@/api/config'
import { getSystemStatus, type SystemPayload } from '@/api/system'
import { runPostStream, type StreamProgress } from '@/api/memories'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'

export function ImportPage() {
  const [sys, setSys] = useState<SystemPayload | null>(null)
  const [config, setConfig] = useState<ChannelConfigData | null>(null)
  const [sources, setSources] = useState<ImportSourceItem[]>([])
  
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // 导入与 Tag 批分析的 SSE 控制状态
  const [importing, setImporting] = useState(false)
  const [importLimit, setImportLimit] = useState(2000)
  const [streamProgress, setStreamProgress] = useState<StreamProgress | null>(null)
  const [streamLog, setStreamLog] = useState<string[]>([])
  const [streamType, setStreamType] = useState<'import' | 'extract' | null>(null)

  // LLM 批量参数
  const [llmBatchSize, setLlmBatchSize] = useState(50)

  async function loadData() {
    setLoading(true)
    setError('')
    try {
      const [sysPayload, configPayload, sourcesPayload] = await Promise.all([
        getSystemStatus(),
        getChannelConfig(),
        getImportSources(),
      ])
      setSys(sysPayload)
      setConfig(configPayload.current ?? null)
      setSources(sourcesPayload.sources ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : '智能导入数据加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadData()
  }, [])

  // 保存基础提供商配置
  async function handleSaveConfig() {
    if (!config) return
    setSaving(true)
    try {
      await saveFullConfig({
        embedding_provider_id: config.embedding_provider_id,
        embedding_dimension: config.embedding_dimension,
        tag_llm_provider_id: config.tag_llm_provider_id,
      })
      toast.success('模型提供商参数保存成功，重启后生效')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '配置保存失败')
    } finally {
      setSaving(false)
    }
  }

  // 触发老记忆插件一键排重导入 (SSE)
  function handleStartImport(sourceItem: ImportSourceItem) {
    if (importing) return
    setImporting(true)
    setStreamType('import')
    setStreamProgress(null)
    setStreamLog([`[INIT] 正在扫描并连接记忆数据源：${sourceItem.name}...`])
    
    // 我们将把 limit 作为伪 id 拼写进队列
    void runPostStream(`/api/memories/import/run?source_id=${sourceItem.id}&limit=${importLimit}`, [], (state) => {
      setStreamProgress(state)
      if (state.processed !== undefined) {
        setStreamLog((prev) => [
          ...prev,
          `[IMPORT] 正在写入: ${Math.round(state.progress * 100)}% | 已导入: ${state.processed}/${state.total}`,
        ].slice(-60))
      }
      if (state.done) {
        setStreamLog((prev) => [...prev, '[SUCCESS] 数据源同步去重入库 100% 成功！'])
        toast.success('数据导入并去重成功')
        setImporting(false)
        void loadData()
      }
      if (state.error) {
        setStreamLog((prev) => [...prev, `[ERROR] 同步中断: ${state.error}`])
        toast.error(`同步失败: ${state.error}`)
        setImporting(false)
      }
    }).catch((err) => {
      const msg = err instanceof Error ? err.message : '连接异常'
      setStreamLog((prev) => [...prev, `[CRITICAL] 写入流异常: ${msg}`])
      toast.error(`同步异常: ${msg}`)
      setImporting(false)
    })
  }

  // 触发无标签记忆的 LLM 异步批量分析提取 (SSE)
  function handleStartLLMExtract() {
    if (importing) return
    setImporting(true)
    setStreamType('extract')
    setStreamProgress(null)
    setStreamLog(['[INIT] 正在启动无标签记忆的 LLM 智能分类与 Tag 批分析提取引擎...'])

    void runPostStream(`/api/memories/import/llm-extract?batch_size=${llmBatchSize}`, [], (state) => {
      setStreamProgress(state)
      if (state.processed !== undefined) {
        setStreamLog((prev) => [
          ...prev,
          `[EXTRACT] 提取进度: ${Math.round(state.progress * 100)}% | 已分析: ${state.processed}/${state.total} | 失败: ${state.errors ?? 0}`,
        ].slice(-60))
      }
      if (state.done) {
        setStreamLog((prev) => [...prev, '[SUCCESS] 无标签记忆已全量清洗并写入结构化 Tag！'])
        toast.success('LLM 批量提取标签完成')
        setImporting(false)
        void loadData()
      }
      if (state.error) {
        setStreamLog((prev) => [...prev, `[ERROR] 提取异常: ${state.error}`])
        toast.error(`提取异常: ${state.error}`)
        setImporting(false)
      }
    }).catch((err) => {
      const msg = err instanceof Error ? err.message : '连接异常'
      setStreamLog((prev) => [...prev, `[CRITICAL] 提取失败: ${msg}`])
      toast.error(`提取异常: ${msg}`)
      setImporting(false)
    })
  }

  // 中止 LLM 分析
  async function handleStopExtract() {
    try {
      await fetch('/api/memories/import/llm-extract/stop', { method: 'POST' })
      setStreamLog((prev) => [...prev, '[HALT] 用户手动中止了 LLM 分析引擎。当前已提取的标签保留，再次点击时会自动从断点续传。'])
      toast.warning('提取引擎已手动中止')
      setImporting(false)
    } catch (e) {
      toast.error('中止失败')
    }
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

  const unlabelledCount = Number((sys?.memories?.total ?? 0) - (sys?.memories?.with_tags ?? 0))

  return (
    <div className="flex flex-col gap-6">
      {/* ─── 1. 模型提供商配置 ─── */}
      <Card>
        <CardHeader className="py-4 shrink-0">
          <CardTitle>智能导入与标签提取</CardTitle>
          <CardDescription>
            自动发现外部记忆插件（如 SelfLearning）数据并排重入库；使用 LLM 对无标签的长期记忆进行批量结构化标签提取。
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-0 flex flex-col gap-4">
          <CardTitle className="text-sm font-semibold">🤖 核心模型提供商</CardTitle>
          {config ? (
            <FieldGroup className="grid gap-4 sm:grid-cols-3">
              <Field>
                <FieldLabel>Embedding Provider</FieldLabel>
                <div className="flex h-10 items-center rounded-md border px-3 text-sm bg-muted/20">
                  {String(config.embedding_provider_id || '未配置')}
                </div>
              </Field>
              <Field>
                <FieldLabel>向量输出维度</FieldLabel>
                <Input
                  type="number"
                  value={Number(config.embedding_dimension ?? 1024)}
                  onChange={(e) => setConfig({ ...config, embedding_dimension: Number(e.target.value) || 1024 })}
                />
              </Field>
              <Field>
                <FieldLabel>Tag 提取分析 LLM</FieldLabel>
                <Input
                  placeholder="如 openai_chat_completion_xxx"
                  value={String(config.tag_llm_provider_id ?? '')}
                  onChange={(e) => setConfig({ ...config, tag_llm_provider_id: e.target.value })}
                />
              </Field>
            </FieldGroup>
          ) : null}
          <div className="flex justify-end border-t pt-3">
            <Button disabled={saving} onClick={() => void handleSaveConfig()}>
              {saving ? <Loader2 className="animate-spin" data-icon="inline-start" /> : <Save data-icon="inline-start" />}
              保存配置
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* ─── 2. 外部插件排重一键导入 ─── */}
        <Card className="flex flex-col">
          <CardHeader className="py-4 flex flex-row items-center justify-between gap-3 shrink-0 border-b">
            <div className="flex flex-col gap-1">
              <CardTitle className="text-sm font-semibold">📦 外部插件一键排重导入</CardTitle>
              <CardDescription>自动发现其他带有对话语料的普通记忆生态，导入过程在后台异步向量化。</CardDescription>
            </div>
            <Button variant="outline" className="size-8 p-0" onClick={() => void loadData()} title="刷新">
              <RefreshCwIcon className="size-4" />
            </Button>
          </CardHeader>
          <CardContent className="pt-6 flex-1 flex flex-col justify-between gap-4">
            <div className="flex flex-col gap-3.5">
              <Field className="max-w-xs">
                <FieldLabel>单批次导入条数上限</FieldLabel>
                <Input
                  type="number"
                  min="100"
                  max="50000"
                  value={importLimit}
                  onChange={(e) => setImportLimit(Number(e.target.value) || 2000)}
                />
              </Field>

              <div className="flex flex-col gap-3">
                {sources.length === 0 ? (
                  <p className="text-xs text-muted-foreground p-6 text-center">系统当前未扫描到任何其他可用的记忆插件源数据。</p>
                ) : (
                  sources.map((src) => (
                    <div key={src.id} className="rounded-lg border p-3.5 bg-muted/10 flex flex-col gap-3">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex flex-col gap-1 min-w-0">
                          <span className="text-xs font-semibold text-foreground flex items-center gap-1.5 truncate">
                            {src.name}
                            <Badge variant={src.has_adapter ? 'secondary' : 'outline'} className="text-[9px]">
                              {src.has_adapter ? '专属适配' : '自动扫描'}
                            </Badge>
                          </span>
                          <span className="text-[10px] text-muted-foreground truncate">{src.description}</span>
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          <span className="font-mono text-xs text-primary">{src.count?.toLocaleString()} 条</span>
                          <Button disabled={importing} size="xs" onClick={() => handleStartImport(src)}>
                            导入
                          </Button>
                        </div>
                      </div>
                      <div className="w-full h-1.5 rounded-full overflow-hidden bg-muted">
                        <div className="h-full rounded-full bg-primary" style={{ width: `${src.imported_pct ?? 0}%` }} />
                      </div>
                      <div className="flex justify-between text-[10px] text-muted-foreground">
                        <span>已导入 ~{src.imported_pct ?? 0}%</span>
                        {src.remaining === 0 ? (
                          <span className="text-emerald-500 font-semibold">✓ 均已安全入库</span>
                        ) : (
                          <span>剩余 {src.remaining?.toLocaleString()} 条未对齐</span>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </CardContent>
        </Card>

        {/* ─── 3. LLM 批量 Tag 分析 ─── */}
        <Card className="flex flex-col">
          <CardHeader className="py-4 shrink-0 border-b">
            <CardTitle className="text-sm font-semibold">🧠 LLM 异步标签（Tag）提取</CardTitle>
            <CardDescription>调用上面配置的 Tag LLM 批次提取标签，对零碎记忆自动建立高维主题索引。</CardDescription>
          </CardHeader>
          <CardContent className="pt-6 flex-1 flex flex-col justify-between gap-4">
            <div className="flex flex-col gap-3.5">
              <div className="grid gap-3 sm:grid-cols-2 rounded-lg border p-3.5 bg-muted/20 text-xs">
                <div><span className="text-muted-foreground font-medium">总记忆数：</span>{sys?.memories?.total?.toLocaleString() ?? '-'}</div>
                <div><span className="text-muted-foreground font-medium">已结构化标签记录：</span>{sys?.memories?.with_tags?.toLocaleString() ?? '-'}</div>
                <div className="sm:col-span-2 flex items-center justify-between border-t pt-2.5 mt-1.5">
                  <span className="text-muted-foreground font-semibold">待分析提取的空白记忆：</span>
                  <span className={unlabelledCount > 0 ? 'font-bold font-mono text-amber-500 text-sm' : 'font-bold font-mono text-emerald-500'}>
                    {unlabelledCount?.toLocaleString()} 条
                  </span>
                </div>
              </div>

              <Field className="max-w-xs">
                <FieldLabel>单页批提取打包大小</FieldLabel>
                <Input
                  type="number"
                  min="1"
                  max="500"
                  value={llmBatchSize}
                  onChange={(e) => setLlmBatchSize(Number(e.target.value) || 50)}
                />
                <FieldDescription>大 Batch 节省 API Token 耗时，小 Batch 提取质量和稳定性更高。</FieldDescription>
              </Field>
            </div>

            <div className="flex gap-2">
              <Button disabled={importing || unlabelledCount === 0} className="flex-1 py-5 justify-center" onClick={handleStartLLMExtract}>
                <PlayIcon data-icon="inline-start" />
                🧠 开启异步提取标签分析
              </Button>
              {importing && streamType === 'extract' ? (
                <Button variant="destructive" className="py-5" onClick={() => void handleStopExtract()}>
                  <StopCircleIcon data-icon="inline-start" />
                  中止分析
                </Button>
              ) : null}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ─── 4. SSE 控制流实时控制台 ─── */}
      {importing || streamProgress ? (
        <Card className="animate-in fade-in duration-200">
          <CardHeader className="py-4 shrink-0 border-b">
            <CardTitle className="text-xs font-semibold">
              {streamType === 'import' ? '📦 正在从外部记忆插件一键排重导入...' : '🧠 正在连接 LLM 自动提取并分析记忆标签...'}
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-6 flex flex-col gap-4">
            {streamProgress ? (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-primary font-semibold">
                    进度：{Math.round(streamProgress.progress * 100)}%
                  </span>
                  <span>
                    已写入: {streamProgress.processed}/{streamProgress.total} | 失败：{streamProgress.errors ?? 0}
                  </span>
                </div>
                <div className="w-full h-2 rounded-full overflow-hidden bg-muted">
                  <div className="h-full rounded-full bg-primary transition-all duration-300" style={{ width: `${streamProgress.progress * 100}%` }} />
                </div>
              </div>
            ) : (
              <div className="flex items-center gap-2 text-xs text-muted-foreground py-2 font-mono">
                <Loader2 className="animate-spin text-primary size-4 shrink-0" />
                正在与流式后台通道握手握手，拉取计算日志...
              </div>
            )}

            <div className="flex flex-col gap-1.5 mt-2">
              <span className="text-xs font-semibold text-foreground">流式执行日志控制台：</span>
              <ScrollArea className="h-44 rounded-lg border bg-muted/60 p-3 font-mono text-[10px] text-muted-foreground leading-relaxed">
                {streamLog.length === 0 ? (
                  <div className="text-muted-foreground">等待数据块推送...</div>
                ) : (
                  streamLog.map((line, idx) => (
                    <div key={idx} className={line.includes('[CRITICAL]') || line.includes('[ERROR]') ? 'text-destructive' : line.includes('[SUCCESS]') ? 'text-emerald-500' : ''}>
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
                <AlertDescription>当前异步进程已 100% 成功执行完毕。</AlertDescription>
              </Alert>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
export default ImportPage
