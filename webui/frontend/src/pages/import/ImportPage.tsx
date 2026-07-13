import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertCircleIcon,
  ArrowRightIcon,
  CheckCircle2Icon,
  Loader2Icon,
  RefreshCwIcon,
} from 'lucide-react'
import { toast } from 'sonner'

import { getImportSources, type ImportSourceItem } from '@/api/import'
import { getFullConfig, type WaveConfigPayload } from '@/api/config'
import { runPostStream, type StreamProgress } from '@/api/memories'
import { type TagExecutionOptions, type TagWritePolicy } from '@/api/tags'
import { TagExtractionConfigPanel } from '@/components/tag/TagExtractionConfigPanel'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'

const importWizardSteps = ['配置检查', '数据源发现', '导入预览', '执行导入', 'Tag 提取', '结果复核']

const dryRunPreviewFields = ['数据源', '总条数', '已导入估计', '重复估计', '将写入 source 类型', '是否会 re-embed', '是否同步 Tag']

export function ImportPage() {
  const [config, setConfig] = useState<WaveConfigPayload | null>(null)
  const [sources, setSources] = useState<ImportSourceItem[]>([])

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // 导入的 SSE 控制状态
  const [importing, setImporting] = useState(false)
  const [importLimit, setImportLimit] = useState(2000)
  const [extractTagsOnImport, setExtractTagsOnImport] = useState(true)
  const [tagBatchSize, setTagBatchSize] = useState(20)
  const [tagWritePolicy, setTagWritePolicy] = useState<TagWritePolicy>('missing_only')
  const [streamProgress, setStreamProgress] = useState<StreamProgress | null>(null)
  const [streamLog, setStreamLog] = useState<string[]>([])

  async function loadData() {
    setLoading(true)
    setError('')
    try {
      const [configPayload, sourcesPayload] = await Promise.all([
        getFullConfig(),
        getImportSources(),
      ])
      setConfig(configPayload ?? null)
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

  const handleTagOptionsChange = useCallback((options: Required<TagExecutionOptions>) => {
    setExtractTagsOnImport(options.extract_tags)
    setTagBatchSize(options.tag_batch_size)
    setTagWritePolicy(options.tag_write_policy)
  }, [])

  // 触发老记忆插件一键排重导入 (SSE)
  function handleStartImport(sourceItem: ImportSourceItem) {
    if (importing) return
    setImporting(true)
    setStreamProgress(null)
    setStreamLog([`[INIT] 正在扫描并连接记忆数据源：${sourceItem.name}...`])

    void runPostStream(`/api/import/from-source?source_id=${sourceItem.id}&limit=${importLimit}&extract_tags=${extractTagsOnImport ? '1' : '0'}&tag_batch_size=${tagBatchSize}&tag_write_policy=${tagWritePolicy}`, [], (state) => {
      setStreamProgress(state)
      const written = state.processed ?? state.imported
      if (written !== undefined) {
        setStreamLog((prev) => [
          ...prev,
          `[IMPORT] 正在写入: ${Math.round(state.progress * 100)}% | 已导入: ${written}/${state.total ?? importLimit} | 同步打标: ${state.tagged ?? 0}`,
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
    }).then(() => {
      setImporting(false)
      void loadData()
    }).catch((err) => {
      const msg = err instanceof Error ? err.message : '连接异常'
      setStreamLog((prev) => [...prev, `[CRITICAL] 写入流异常: ${msg}`])
      toast.error(`同步异常: ${msg}`)
      setImporting(false)
    })
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

  const totalSourceCount = sources.reduce((sum, src) => sum + Number(src.count ?? 0), 0)
  const importedEstimate = sources.reduce((sum, src) => sum + Math.round(Number(src.count ?? 0) * Number(src.imported_pct ?? 0) / 100), 0)
  const remainingEstimate = sources.reduce((sum, src) => sum + Number(src.remaining ?? 0), 0)
  const duplicateEstimate = Math.max(totalSourceCount - importedEstimate - remainingEstimate, 0)
  const dryRunPreviewRows = [
    ['数据源', sources.length ? `${sources.length} 个可扫描来源` : '未发现可扫描来源'],
    ['总条数', totalSourceCount.toLocaleString()],
    ['已导入估计', importedEstimate.toLocaleString()],
    ['重复估计', duplicateEstimate.toLocaleString()],
    ['将写入 source 类型', 'external_plugin_import'],
    ['是否会 re-embed', Number(config?.embedding_dimension ?? 0) > 0 ? `会，按 ${Number(config?.embedding_dimension)} 维向量写入` : '等待配置检查'],
    ['是否同步 Tag', extractTagsOnImport ? `会，按每批 ${tagBatchSize} 条调用同一 Tag LLM` : '否，仅导入并留给维护中心补提取'],
  ]

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>智能导入与标签提取</CardTitle>
          <CardDescription>
            自动发现外部记忆插件（如 SelfLearning）数据并排重入库；导入时可同步提取 Tag，也可留给标签与维护中心补跑。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="grid gap-3 md:grid-cols-6">
            {importWizardSteps.map((step, index) => (
              <div key={step} className="rounded-lg border bg-muted/20 p-3">
                <div className="flex items-center justify-between gap-2">
                  <Badge variant={index < 2 ? 'secondary' : 'outline'}>{index + 1}</Badge>
                  <span className="text-xs text-muted-foreground">{index < 2 ? '已接入' : '契约'}</span>
                </div>
                <p className="mt-3 text-sm font-medium">{step}</p>
              </div>
            ))}
          </div>
          <Alert>
            <AlertCircleIcon />
            <AlertTitle>provider 配置块属于静态配置，需要重启</AlertTitle>
            <AlertDescription>
              Embedding Provider、向量维度、Tag 提取分析 LLM 会影响导入和 Tag 提取；智能导入和标签与维护中心共用同一个 Tag 提取分析 LLM，保存后通过 AstrBot 重启让 provider 配置块生效。
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>

      {/* ─── 1. 模型提供商配置 ─── */}
      <TagExtractionConfigPanel
        title="智能导入 Tag 提取配置"
        description="外部导入只对本次新增 memories 同步提取 Tag；Provider、向量维度与维护中心共用同一套静态配置。"
        config={config}
        onConfigChange={setConfig}
        onOptionsChange={handleTagOptionsChange}
        disabled={importing}
        showExtractToggle
      />

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">dry-run 预览</CardTitle>
          <CardDescription>先把导入前的关键数字显式摆出来，避免直接执行后才发现来源、重复或 re-embed 策略不对。</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="grid gap-3 md:grid-cols-3">
            {dryRunPreviewRows.map(([label, value]) => (
              <div key={label} className="rounded-lg border bg-muted/20 p-4">
                <p className="text-xs text-muted-foreground">{label}</p>
                <p className="mt-2 text-sm font-semibold">{value}</p>
              </div>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            {dryRunPreviewFields.map((field) => (
              <Badge key={field} variant="outline">{field}</Badge>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* ─── 2. 外部插件排重一键导入 ─── */}
      <Card>
        <CardHeader className="border-b">
          <CardTitle className="text-sm font-semibold">外部插件一键排重导入</CardTitle>
          <CardDescription>自动发现其他带有对话语料的普通记忆生态，导入过程在后台异步向量化。</CardDescription>
          <CardAction>
            <Button variant="outline" size="icon" onClick={() => void loadData()} title="刷新">
              <RefreshCwIcon />
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 pt-6">
          <div className="grid gap-4 md:grid-cols-3">
            <Field>
              <FieldLabel>单批次导入条数上限</FieldLabel>
              <Input
                type="number"
                min="100"
                max="50000"
                value={importLimit}
                disabled={importing}
                onChange={(e) => setImportLimit(Number(e.target.value) || 2000)}
              />
            </Field>
            <Field>
              <FieldLabel>Tag 提取执行策略</FieldLabel>
              <div className="flex h-10 items-center rounded-md border bg-muted/20 px-3 text-sm">
                tag_batch_size={tagBatchSize} · tag_write_policy={tagWritePolicy}
              </div>
            </Field>
            <Field className="justify-end rounded-lg border bg-muted/10 px-3 py-2">
              <label className="flex items-center gap-2 text-sm font-medium">
                <input
                  type="checkbox"
                  checked={extractTagsOnImport}
                  disabled={importing}
                  onChange={(e) => setExtractTagsOnImport(e.target.checked)}
                />
                <span>同步提取 Tag</span>
              </label>
              <p className="text-xs text-muted-foreground">
                和标签与维护中心共用同一个 Tag 提取分析 LLM；关闭后导入的数据可在维护中心补跑。
              </p>
            </Field>
          </div>

          <div className="flex flex-col gap-3">
            {sources.length === 0 ? (
              <p className="p-6 text-center text-xs text-muted-foreground">系统当前未扫描到任何其他可用的记忆插件源数据。</p>
            ) : (
              sources.map((src) => (
                <div key={src.id} className="flex flex-col gap-3 rounded-lg border bg-muted/10 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex min-w-0 flex-col gap-1">
                      <span className="flex items-center gap-2 truncate text-xs font-semibold text-foreground">
                        {src.name}
                        <Badge variant={src.has_adapter ? 'secondary' : 'outline'}>
                          {src.has_adapter ? '专属适配' : '自动扫描'}
                        </Badge>
                      </span>
                      <span className="truncate text-xs text-muted-foreground">{src.description}</span>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <span className="font-mono text-xs text-primary">{src.count?.toLocaleString()} 条</span>
                      <Button disabled={importing} size="xs" onClick={() => handleStartImport(src)}>
                        导入
                      </Button>
                    </div>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                    <div className="h-full rounded-full bg-primary" style={{ width: `${src.imported_pct ?? 0}%` }} />
                  </div>
                  <div className="flex justify-between gap-3 text-xs text-muted-foreground">
                    <span>已导入 ~{src.imported_pct ?? 0}%</span>
                    {src.remaining === 0 ? (
                      <Badge variant="secondary">均已安全入库</Badge>
                    ) : (
                      <span>剩余 {src.remaining?.toLocaleString()} 条未对齐</span>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </CardContent>
      </Card>

      {/* ─── 3. SSE 控制流实时控制台 ─── */}
      {importing || streamProgress ? (
        <Card className="animate-in fade-in duration-200">
          <CardHeader className="border-b">
            <CardTitle className="text-xs font-semibold">
              正在从外部记忆插件一键排重导入...
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4 pt-6">
            {streamProgress ? (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-3 font-mono text-xs">
                  <span className="font-semibold text-primary">
                    进度：{Math.min(100, Math.max(0, Math.round((streamProgress.progress ?? 0) * 100)))}%
                  </span>
                  <span>
                    已写入: {streamProgress.processed ?? streamProgress.imported ?? 0}/{streamProgress.total ?? importLimit} | 打标：{streamProgress.tagged ?? 0} | 失败：{streamProgress.errors ?? 0}
                  </span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                  <div className="h-full rounded-full bg-primary transition-all duration-300" style={{ width: `${Math.min(100, Math.max(0, Math.round((streamProgress.progress ?? 0) * 100)))}%` }} />
                </div>
              </div>
            ) : (
              <div className="flex items-center gap-2 py-2 font-mono text-xs text-muted-foreground">
                <Loader2Icon className="shrink-0 animate-spin text-primary" />
                正在与流式后台通道握手，拉取计算日志...
              </div>
            )}


            <div className="flex flex-col gap-2">
              <span className="text-xs font-semibold text-foreground">流式执行日志控制台：</span>
              <ScrollArea className="h-44 rounded-lg border bg-muted/60 p-3 font-mono text-xs leading-relaxed text-muted-foreground">
                {streamLog.length === 0 ? (
                  <div className="text-muted-foreground">等待数据块推送...</div>
                ) : (
                  streamLog.map((line, idx) => (
                    <div key={idx} className={line.includes('[CRITICAL]') || line.includes('[ERROR]') ? 'text-destructive' : line.includes('[SUCCESS]') ? 'text-primary' : ''}>
                      {line}
                    </div>
                  ))
                )}
              </ScrollArea>
            </div>

            {streamProgress?.done ? (
              <Alert>
                <CheckCircle2Icon />
                <AlertTitle>处理完毕</AlertTitle>
                <AlertDescription>当前异步进程已 100% 成功执行完毕。</AlertDescription>
              </Alert>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">结果复核入口</CardTitle>
          <CardDescription>导入完成后从三个入口复核数据、Tag 审计和覆盖率变化。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          <Button asChild variant="outline" size="lg">
            <Link to="/memories">
              去记忆管理器看新导入数据
              <ArrowRightIcon data-icon="inline-end" />
            </Link>
          </Button>
          <Button asChild variant="outline" size="lg">
            <Link to="/maintain">
              去标签与维护中心看 Tag 审计
              <ArrowRightIcon data-icon="inline-end" />
            </Link>
          </Button>
          <Button asChild variant="outline" size="lg">
            <Link to="/dashboard">
              去总览看覆盖率变化
              <ArrowRightIcon data-icon="inline-end" />
            </Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
export default ImportPage
