import { useEffect, useState } from 'react'
import { AlertTriangleIcon, RefreshCwIcon, SearchIcon } from 'lucide-react'

import { getInjectionTrace, listInjectionTraces, type InjectionTraceSummary, type TraceDetailPayload, type TraceFilters } from '@/api/injection'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { TraceDetailSheet } from '@/pages/injection/TraceDetailSheet'

interface FilterState {
  group_id: string
  sender_id: string
  bot_id: string
  channel: string
  status: string
  has_error: string
  scope: string
  limit: string
}

const defaultFilters: FilterState = {
  group_id: '',
  sender_id: '',
  bot_id: '',
  channel: '',
  status: '',
  has_error: '',
  scope: '',
  limit: '100',
}

function toFilters(filters: FilterState): TraceFilters {
  return {
    group_id: filters.group_id,
    sender_id: filters.sender_id,
    bot_id: filters.bot_id,
    channel: filters.channel,
    status: filters.status,
    has_error: filters.has_error,
    scope: filters.scope,
    limit: Number(filters.limit) || 100,
  }
}

function formatTime(value: unknown): string {
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return '-'
  }
  return new Date(seconds * 1000).toLocaleString('zh-CN')
}

function traceId(trace: InjectionTraceSummary): string {
  return String(trace.trace_id ?? trace.id ?? '')
}

function tracePreview(trace: InjectionTraceSummary): string {
  return String(trace.preview ?? trace.final_text_preview ?? trace.message_preview ?? '-')
}

function traceTokens(trace: InjectionTraceSummary): string {
  return String(trace.total_tokens ?? trace.tokens ?? '-')
}

function traceLatency(trace: InjectionTraceSummary): string {
  const value = Number(trace.latency_ms ?? trace.total_ms ?? 0)
  return Number.isFinite(value) && value > 0 ? `${Math.round(value)} ms` : '-'
}

export function InjectionPage() {
  const [filters, setFilters] = useState<FilterState>(defaultFilters)
  const [traces, setTraces] = useState<InjectionTraceSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [detailOpen, setDetailOpen] = useState(false)
  const [detail, setDetail] = useState<TraceDetailPayload | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')

  async function load(nextFilters = filters) {
    setLoading(true)
    setError('')
    try {
      const payload = await listInjectionTraces(toFilters(nextFilters))
      if (payload.error) {
        throw new Error(payload.error)
      }
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
      const payload = await getInjectionTrace(id)
      if (payload.error) {
        throw new Error(payload.error)
      }
      setDetail(payload)
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : 'trace 详情加载失败')
    } finally {
      setDetailLoading(false)
    }
  }

  useEffect(() => {
    void load(defaultFilters)
  }, [])

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Injection Observatory</CardTitle>
          <CardDescription>筛选注入 trace，查看通道瀑布、命中项、过滤项和最终注入文本。</CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="flex flex-col gap-4"
            onSubmit={(event) => {
              event.preventDefault()
              void load()
            }}
          >
            <FieldGroup className="grid gap-4 md:grid-cols-4">
              <Field>
                <FieldLabel htmlFor="trace-group">群 / 会话</FieldLabel>
                <Input id="trace-group" value={filters.group_id} onChange={(event) => setFilters({ ...filters, group_id: event.target.value })} />
              </Field>
              <Field>
                <FieldLabel htmlFor="trace-sender">发送者</FieldLabel>
                <Input id="trace-sender" value={filters.sender_id} onChange={(event) => setFilters({ ...filters, sender_id: event.target.value })} />
              </Field>
              <Field>
                <FieldLabel htmlFor="trace-bot">Bot</FieldLabel>
                <Input id="trace-bot" value={filters.bot_id} onChange={(event) => setFilters({ ...filters, bot_id: event.target.value })} />
              </Field>
              <Field>
                <FieldLabel htmlFor="trace-channel">通道</FieldLabel>
                <Input id="trace-channel" value={filters.channel} onChange={(event) => setFilters({ ...filters, channel: event.target.value })} />
              </Field>
              <Field>
                <FieldLabel>状态</FieldLabel>
                <Select value={filters.status || 'all'} onValueChange={(value) => setFilters({ ...filters, status: value === 'all' ? '' : value })}>
                  <SelectTrigger>
                    <SelectValue placeholder="全部" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectItem value="all">全部</SelectItem>
                      <SelectItem value="ok">ok</SelectItem>
                      <SelectItem value="error">error</SelectItem>
                      <SelectItem value="timeout">timeout</SelectItem>
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel>错误</FieldLabel>
                <Select value={filters.has_error || 'all'} onValueChange={(value) => setFilters({ ...filters, has_error: value === 'all' ? '' : value })}>
                  <SelectTrigger>
                    <SelectValue placeholder="全部" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectItem value="all">全部</SelectItem>
                      <SelectItem value="true">有错误</SelectItem>
                      <SelectItem value="false">无错误</SelectItem>
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel htmlFor="trace-scope">范围</FieldLabel>
                <Input id="trace-scope" value={filters.scope} onChange={(event) => setFilters({ ...filters, scope: event.target.value })} />
              </Field>
              <Field>
                <FieldLabel htmlFor="trace-limit">Limit</FieldLabel>
                <Input id="trace-limit" inputMode="numeric" value={filters.limit} onChange={(event) => setFilters({ ...filters, limit: event.target.value })} />
              </Field>
            </FieldGroup>
            <div className="flex flex-wrap gap-2">
              <Button disabled={loading} type="submit">
                <SearchIcon data-icon="inline-start" />
                查询
              </Button>
              <Button disabled={loading} type="button" variant="outline" onClick={() => void load()}>
                <RefreshCwIcon data-icon="inline-start" />
                刷新
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {error ? (
        <Alert variant="destructive">
          <AlertTriangleIcon />
          <AlertTitle>Trace 列表加载失败</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Trace 列表</CardTitle>
          <CardDescription>{loading ? '加载中…' : `${traces.length} 条结果`}</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : traces.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无 trace。</p>
          ) : (
            <div className="overflow-auto rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Trace ID</TableHead>
                    <TableHead>时间</TableHead>
                    <TableHead>模式</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>Session</TableHead>
                    <TableHead>预览</TableHead>
                    <TableHead>Token</TableHead>
                    <TableHead>耗时</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {traces.map((trace) => {
                    const id = traceId(trace)
                    const status = String(trace.status ?? (trace.has_error ? 'error' : 'ok'))
                    return (
                      <TableRow key={id || JSON.stringify(trace)} className="cursor-pointer" onClick={() => id && void openDetail(id)}>
                        <TableCell className="font-mono text-xs">{id || '-'}</TableCell>
                        <TableCell>{formatTime(trace.timestamp ?? trace.created_at)}</TableCell>
                        <TableCell>{String(trace.mode ?? '-')}</TableCell>
                        <TableCell>
                          <Badge variant={status === 'ok' ? 'secondary' : 'destructive'}>{status}</Badge>
                        </TableCell>
                        <TableCell>{String(trace.session_id ?? trace.group_id ?? '-')}</TableCell>
                        <TableCell className="max-w-md truncate">{tracePreview(trace)}</TableCell>
                        <TableCell>{traceTokens(trace)}</TableCell>
                        <TableCell>{traceLatency(trace)}</TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <TraceDetailSheet open={detailOpen} onOpenChange={setDetailOpen} detail={detail} loading={detailLoading} error={detailError} />
    </div>
  )
}
