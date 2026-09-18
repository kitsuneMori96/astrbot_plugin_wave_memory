import { useEffect, useState } from 'react'
import { AlertTriangleIcon, RefreshCwIcon, SearchIcon, TrashIcon } from 'lucide-react'

import { listLlmTraces, getLlmTrace, clearLlmTraces, type LlmTraceSummary, type LlmTraceDetail } from '@/api/llmDebug'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldGroup } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

interface FilterState {
  group_id: string
  sender_id: string
  limit: string
}

const defaultFilters: FilterState = {
  group_id: '',
  sender_id: '',
  limit: '100',
}

function formatTime(value: unknown): string {
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds <= 0) return '-'
  return new Date(seconds * 1000).toLocaleString('zh-CN')
}

function formatTokens(value: unknown): string {
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) return '-'
  return n.toLocaleString()
}

function formatLatency(value: unknown): string {
  const ms = Number(value)
  if (!Number.isFinite(ms) || ms <= 0) return '-'
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`
}

function statusBadge(status: string) {
  if (status === 'ok') return <Badge variant="secondary">正常</Badge>
  if (status === 'error') return <Badge variant="destructive">错误</Badge>
  return <Badge>{status || '-'}</Badge>
}

function estimateTokens(text: string): number {
  return Math.ceil((text || '').length / 4)
}

function Tooltips({ label, tokens }: { label: string; tokens: number }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <span>{label}</span>
      <Badge variant="outline" className="font-mono">{formatTokens(tokens)}</Badge>
    </span>
  )
}

function TokenBar({ parts }: { parts: Array<{ label: string; tokens: number; color: string }> }) {
  const total = parts.reduce((s, p) => s + p.tokens, 0)
  if (total <= 0) return <p className="text-xs text-muted-foreground">无 token 数据</p>
  return (
    <div className="flex flex-col gap-2">
      <div className="flex h-4 w-full overflow-hidden rounded-md">
        {parts.map((p) => {
          const pct = (p.tokens / total) * 100
          return pct > 0 ? (
            <div
              key={p.label}
              className={p.color}
              style={{ width: `${pct}%` }}
              title={`${p.label}: ${formatTokens(p.tokens)} tokens (${pct.toFixed(1)}%)`}
            />
          ) : null
        })}
      </div>
      <div className="flex flex-wrap gap-2">
        {parts.map((p) => (
          <Tooltips key={p.label} label={p.label} tokens={p.tokens} />
        ))}
        <span className="text-xs text-muted-foreground">总计: {formatTokens(total)}</span>
      </div>
    </div>
  )
}

function HighlightedExtraParts({ parts }: { parts: string[] }) {
  const blockColors: Record<string, string> = {
    '<wave_persona>': 'bg-blue-100 border-blue-300 text-blue-800',
    '<sender_profile>': 'bg-green-100 border-green-300 text-green-800',
    '<wave_style>': 'bg-purple-100 border-purple-300 text-purple-800',
    '<wave_memory>': 'bg-amber-100 border-amber-300 text-amber-800',
    '<system_reminder>': 'bg-red-100 border-red-300 text-red-800',
    '<identity_safety>': 'bg-rose-100 border-rose-300 text-rose-800',
    '<self_persona>': 'bg-cyan-100 border-cyan-300 text-cyan-800',
    '<image_caption>': 'bg-slate-100 border-slate-300 text-slate-800',
  }

  function findBlockType(text: string): string | null {
    for (const tag of Object.keys(blockColors)) {
      if (text.includes(tag)) return tag
    }
    return null
  }

  if (!parts || parts.length === 0) {
    return <p className="text-sm text-muted-foreground">无 extra_parts</p>
  }

  return (
    <div className="flex flex-col gap-2">
      {parts.map((part, i) => {
        const blockType = findBlockType(part)
        const colorClass = blockType ? blockColors[blockType] : 'bg-gray-50 border-gray-200'
        return (
          <div key={i} className={`rounded border p-2 text-xs font-mono whitespace-pre-wrap break-all ${colorClass}`}>
            <div className="mb-1 text-[10px] opacity-60">
              {blockType || `extra_part[${i}]`} — {formatTokens(estimateTokens(part))} tokens
            </div>
            {part.length > 800 ? part.slice(0, 800) + '\n... (truncated)' : part}
          </div>
        )
      })}
    </div>
  )
}

function TraceDetailSheet({
  open, onOpenChange, detail, loading, error,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  detail: LlmTraceDetail | null
  loading: boolean
  error: string
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full flex-col gap-0 pr-0 sm:max-w-3xl sm:pr-2">
        <SheetHeader className="shrink-0 border-b pb-4 pr-6">
          <SheetTitle>LLM 请求详情</SheetTitle>
          <SheetDescription>完整请求体快照与 token 分解</SheetDescription>
        </SheetHeader>
        <ScrollArea className="flex-1 pr-6">
          {loading ? (
            <div className="flex flex-col gap-3 p-4">
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-20 w-full" />
            </div>
          ) : error ? (
            <Alert variant="destructive" className="m-4">
              <AlertTriangleIcon />
              <AlertTitle>加载失败</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : detail ? (
            <div className="flex flex-col gap-4 p-4">
              {/* 基本信息 */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">基本信息</CardTitle>
                </CardHeader>
                <CardContent className="text-xs space-y-1">
                  <p><span className="text-muted-foreground">Trace ID:</span> <span className="font-mono">{detail.trace_id}</span></p>
                  <p><span className="text-muted-foreground">时间:</span> {formatTime(detail.timestamp)}</p>
                  <p><span className="text-muted-foreground">群组:</span> {detail.group_id || '(私聊)'}</p>
                  <p><span className="text-muted-foreground">发送者:</span> {detail.sender_id || '-'}</p>
                  <p><span className="text-muted-foreground">模型:</span> {detail.model || '-'}</p>
                  <p><span className="text-muted-foreground">Provider:</span> {detail.provider_id || '-'}</p>
                  <p><span className="text-muted-foreground">状态:</span> {statusBadge(detail.status || '')}</p>
                  <p><span className="text-muted-foreground">延迟:</span> {formatLatency(detail.latency_ms)}</p>
                  <p><span className="text-muted-foreground">消息:</span> {detail.message_preview || '-'}</p>
                </CardContent>
              </Card>

              {/* Token 分解 */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">Token 分解</CardTitle>
                  <CardDescription>各组件占多少 token</CardDescription>
                </CardHeader>
                <CardContent>
                  <TokenBar parts={[
                    { label: 'system_prompt', tokens: estimateTokens(detail.system_prompt || ''), color: 'bg-blue-500' },
                    { label: 'contexts', tokens: Math.ceil(JSON.stringify(detail.contexts || []).length / 4), color: 'bg-green-500' },
                    { label: 'extra_parts', tokens: (detail.extra_parts || []).reduce((s, p) => s + estimateTokens(p), 0), color: 'bg-purple-500' },
                    { label: 'tools', tokens: Math.ceil(JSON.stringify(detail.tools || []).length / 4), color: 'bg-amber-500' },
                  ]} />
                  <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                    <div>prompt_tokens: <Badge variant="outline">{formatTokens(detail.prompt_tokens)}</Badge></div>
                    <div>completion_tokens: <Badge variant="outline">{formatTokens(detail.completion_tokens)}</Badge></div>
                  </div>
                </CardContent>
              </Card>

              {/* System Prompt */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">System Prompt</CardTitle>
                  <CardDescription>{formatTokens(estimateTokens(detail.system_prompt || ''))} tokens</CardDescription>
                </CardHeader>
                <CardContent>
                  <pre className="text-xs whitespace-pre-wrap break-all bg-muted rounded p-3 max-h-60 overflow-auto">
                    {detail.system_prompt || '(空)'}
                  </pre>
                </CardContent>
              </Card>

              {/* Extra Parts（高亮） */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">注入组件 (extra_parts)</CardTitle>
                  <CardDescription>共 {detail.extra_parts?.length || 0} 个组件</CardDescription>
                </CardHeader>
                <CardContent>
                  <HighlightedExtraParts parts={detail.extra_parts || []} />
                </CardContent>
              </Card>

              {/* Contexts */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">对话历史 (contexts)</CardTitle>
                  <CardDescription>{detail.contexts?.length || 0} 条消息</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="max-h-60 overflow-auto">
                    <pre className="text-xs whitespace-pre-wrap break-all bg-muted rounded p-3">
                      {JSON.stringify(detail.contexts || [], null, 2)}
                    </pre>
                  </div>
                </CardContent>
              </Card>

              {/* Tools */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">工具 (tools)</CardTitle>
                  <CardDescription>{detail.tools?.length || 0} 个工具</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="max-h-40 overflow-auto">
                    <pre className="text-xs whitespace-pre-wrap break-all bg-muted rounded p-3">
                      {JSON.stringify(detail.tools || [], null, 2)}
                    </pre>
                  </div>
                </CardContent>
              </Card>

              {/* Response */}
              {detail.response_preview ? (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">LLM 响应预览</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <pre className="text-xs whitespace-pre-wrap break-all bg-muted rounded p-3 max-h-40 overflow-auto">
                      {detail.response_preview}
                    </pre>
                  </CardContent>
                </Card>
              ) : null}
            </div>
          ) : null}
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}

export function LlmDebugPage() {
  const [filters, setFilters] = useState<FilterState>(defaultFilters)
  const [traces, setTraces] = useState<LlmTraceSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [detailOpen, setDetailOpen] = useState(false)
  const [detail, setDetail] = useState<LlmTraceDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')

  async function load(nextFilters = filters) {
    setLoading(true)
    setError('')
    try {
      const payload = await listLlmTraces({
        group_id: nextFilters.group_id || undefined,
        sender_id: nextFilters.sender_id || undefined,
        limit: Number(nextFilters.limit) || 100,
      })
      if (payload.error) throw new Error(payload.error)
      setTraces(payload.traces ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'trace 列表加载失败')
      setTraces([])
    } finally {
      setLoading(false)
    }
  }

  async function openDetail(id: string) {
    setDetailOpen(true)
    setDetail(null)
    setDetailLoading(true)
    setDetailError('')
    try {
      const payload = await getLlmTrace(id)
      if (payload.error) throw new Error(payload.error)
      setDetail(payload)
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : 'trace 详情加载失败')
    } finally {
      setDetailLoading(false)
    }
  }

  async function handleClear() {
    if (!confirm('确认清空所有 LLM trace？')) return
    try {
      await clearLlmTraces()
      setTraces([])
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    void load(defaultFilters)
  }, [])

  return (
    <div className="flex flex-col gap-6">
      {/* Filter card */}
      <Card>
        <CardHeader>
          <CardTitle>LLM Debug · 请求调试台</CardTitle>
          <CardDescription>查看每次 LLM 调用的完整请求体，分析 token 消耗与组件占比。</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-4" onSubmit={(event) => { event.preventDefault(); void load() }}>
            <FieldGroup className="grid gap-4 md:grid-cols-3">
              <Field>
                <label className="text-xs font-medium">群组 ID</label>
                <Input
                  placeholder="筛选群组"
                  value={filters.group_id}
                  onChange={(e) => setFilters({ ...filters, group_id: e.target.value })}
                />
              </Field>
              <Field>
                <label className="text-xs font-medium">发送者 ID</label>
                <Input
                  placeholder="筛选发送者"
                  value={filters.sender_id}
                  onChange={(e) => setFilters({ ...filters, sender_id: e.target.value })}
                />
              </Field>
              <Field>
                <label className="text-xs font-medium">数量上限</label>
                <Input
                  type="number"
                  placeholder="100"
                  value={filters.limit}
                  onChange={(e) => setFilters({ ...filters, limit: e.target.value })}
                />
              </Field>
            </FieldGroup>
            <div className="flex flex-wrap gap-2">
              <Button disabled={loading} type="submit"><SearchIcon className="mr-1 size-4" />查询</Button>
              <Button disabled={loading} type="button" variant="outline" onClick={() => void load()}><RefreshCwIcon className="mr-1 size-4" />刷新</Button>
              <Button disabled={loading} type="button" variant="destructive" onClick={() => void handleClear()}><TrashIcon className="mr-1 size-4" />清空</Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {/* Error alert */}
      {error ? (
        <Alert variant="destructive">
          <AlertTriangleIcon />
          <AlertTitle>LLM trace 列表加载失败</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {/* Results table card */}
      <Card>
        <CardHeader>
          <CardTitle>LLM 请求列表</CardTitle>
          <CardDescription>{loading ? '加载中...' : `${traces.length} 条结果`}</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : traces.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无 trace。发送消息后自动记录。</p>
          ) : (
            <div className="overflow-auto rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>时间</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>群组</TableHead>
                    <TableHead>消息预览</TableHead>
                    <TableHead>Prompt</TableHead>
                    <TableHead>Completion</TableHead>
                    <TableHead>Total</TableHead>
                    <TableHead>延迟</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {traces.map((trace) => {
                    const id = String(trace.trace_id ?? '')
                    return (
                      <TableRow key={id || JSON.stringify(trace)} className="cursor-pointer" onClick={() => id && void openDetail(id)}>
                        <TableCell className="text-xs">{formatTime(trace.timestamp)}</TableCell>
                        <TableCell>{statusBadge(trace.status || '')}</TableCell>
                        <TableCell className="font-mono text-xs">{trace.group_id || '(私聊)'}</TableCell>
                        <TableCell className="max-w-xs truncate text-xs">{trace.message_preview || '-'}</TableCell>
                        <TableCell className="font-mono text-xs">{formatTokens(trace.prompt_tokens)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatTokens(trace.completion_tokens)}</TableCell>
                        <TableCell className="font-mono text-xs">{formatTokens(trace.total_tokens)}</TableCell>
                        <TableCell className="text-xs">{formatLatency(trace.latency_ms)}</TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Detail sheet */}
      <TraceDetailSheet open={detailOpen} onOpenChange={setDetailOpen} detail={detail} loading={detailLoading} error={detailError} />
    </div>
  )
}
