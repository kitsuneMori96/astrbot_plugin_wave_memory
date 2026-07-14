import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCwIcon, SearchIcon } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'

import { getInjectionTrace, listInjectionTraces, type InjectionTraceSummary, type TraceDetailPayload, type TraceFilters } from '@/api/injection'
import { getScopeOptions, scopeOptionsFor } from '@/api/options'
import { getAgentFeedback, type AgentFeedbackPayload } from '@/api/review'
import { PaginationControls, QueryState, ScopeSelect } from '@/components/shared'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useCanonicalScopeDefault, usePaginationSearchParams } from '@/hooks/use-pagination-search-params'
import { TraceDetailSheet } from '@/pages/injection/TraceDetailSheet'

function formatTime(value: unknown): string {
  const seconds = Number(value)
  return Number.isFinite(seconds) && seconds > 0 ? new Date(seconds * 1000).toLocaleString('zh-CN') : '未记录'
}

function textValue(value: unknown, fallback = '未记录'): string {
  return value === undefined || value === null || value === '' ? fallback : String(value)
}

function traceBot(trace: InjectionTraceSummary): string {
  if (trace.bot_profile_id) return String(trace.bot_profile_id)
  if (trace.bot_id) return String(trace.bot_id)
  return '未记录（legacy trace 未关联 Bot）'
}

function traceSession(trace: InjectionTraceSummary): { primary: string; secondary?: string } {
  if (trace.session) {
    return {
      primary: trace.session.label || trace.session.id,
      secondary: `${trace.session.kind} · ${trace.session.id}`,
    }
  }
  if (trace.session_id) return { primary: String(trace.session_id), secondary: '仅记录 session_id' }
  if (trace.group_id) return { primary: '未关联结构化 session', secondary: `legacy group_id: ${String(trace.group_id)}` }
  return { primary: '未记录（legacy trace）', secondary: '无 session/session_id/group_id' }
}

function tracePreview(trace: InjectionTraceSummary): string {
  return textValue(trace.preview ?? trace.final_text_preview ?? trace.message_preview)
}

function channelRecords(trace: InjectionTraceSummary): Array<Record<string, unknown>> {
  const raw = trace.channels ?? trace.channel_results ?? trace.channel_summaries ?? []
  return Array.isArray(raw)
    ? raw.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    : []
}

function channelCount(trace: InjectionTraceSummary, kind: 'hit' | 'skipped' | 'error'): string {
  const explicitKeys = kind === 'hit'
    ? ['hit_channel_count', 'hit_channels_count', 'matched_channel_count']
    : kind === 'skipped'
      ? ['skipped_channel_count', 'skipped_channels_count', 'filtered_channel_count']
      : ['error_channel_count', 'error_channels_count']
  const explicit = explicitKeys.map((key) => trace[key]).find((value) => value !== undefined && value !== null)
  if (explicit !== undefined) return String(explicit)

  const count = channelRecords(trace).filter((channel) => {
    const status = String(channel.status ?? '')
    if (kind === 'hit') return status === 'ok' || Number(channel.hit_count ?? channel.hits_count ?? 0) > 0
    if (kind === 'skipped') return ['empty', 'disabled', 'skipped', 'filtered'].includes(status)
    return status.includes('error') || status.includes('timeout') || Boolean(channel.error)
  }).length
  if (count > 0) return String(count)
  return kind === 'error' && trace.has_error ? '1' : '0'
}

function primaryTokenChannel(trace: InjectionTraceSummary): string {
  const explicit = trace.primary_token_channel ?? trace.max_token_channel ?? trace.top_token_channel
  if (explicit !== undefined && explicit !== null && explicit !== '') return String(explicit)
  const top = channelRecords(trace).reduce<Record<string, unknown> | null>((current, channel) => {
    const currentTokens = Number(current?.tokens ?? current?.token_count ?? 0)
    const nextTokens = Number(channel.tokens ?? channel.token_count ?? 0)
    return nextTokens > currentTokens ? channel : current
  }, null)
  return top ? textValue(top.channel ?? top.name ?? top.key) : '未记录'
}

function traceLatency(trace: InjectionTraceSummary): string {
  const value = Number(trace.latency_ms ?? trace.total_latency_ms ?? trace.total_ms)
  return Number.isFinite(value) && value >= 0 ? `${Math.round(value)} ms` : '未记录'
}

function statusLabel(status: unknown): string {
  const value = String(status ?? '')
  if (value === 'ok') return '正常'
  if (value === 'error') return '错误'
  if (value === 'timeout') return '超时'
  if (value === 'skipped') return '已跳过'
  return value || '未知'
}

function epochToInput(value: string | null): string {
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds <= 0) return ''
  const date = new Date(seconds * 1000)
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 16)
}

function inputToEpoch(value: string): string {
  if (!value) return ''
  const milliseconds = new Date(value).getTime()
  return Number.isFinite(milliseconds) ? String(Math.floor(milliseconds / 1000)) : ''
}

export function InjectionPage() {
  const pagination = usePaginationSearchParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const [payload, setPayload] = useState<Awaited<ReturnType<typeof listInjectionTraces>> | null>(null)
  const [status, setStatus] = useState<'loading' | 'success' | 'empty' | 'error'>('loading')
  const [error, setError] = useState<unknown>()
  const [feedback, setFeedback] = useState<AgentFeedbackPayload | null>(null)
  const [feedbackStatus, setFeedbackStatus] = useState<'loading' | 'success' | 'empty' | 'error'>('loading')
  const [feedbackError, setFeedbackError] = useState<unknown>()
  const [detail, setDetail] = useState<TraceDetailPayload | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')

  const botId = searchParams.get('bot_id') ?? ''
  const sessionId = searchParams.get('session_id') ?? ''
  useCanonicalScopeDefault({ botId, sessionId, setFilters: pagination.setFilters })
  const channel = searchParams.get('channel') ?? ''
  const selectedTraceId = searchParams.get('trace_id') ?? ''

  const filters = useMemo<TraceFilters>(() => ({
    bot_id: botId || undefined,
    session_id: sessionId || undefined,
    group_id: searchParams.get('group_id') || undefined,
    sender_id: searchParams.get('sender_id') || undefined,
    channel: channel || undefined,
    status: searchParams.get('status') || undefined,
    has_error: searchParams.get('has_error') || undefined,
    scope: searchParams.get('scope') || undefined,
    config_revision: searchParams.get('config_revision') || undefined,
    from_ts: searchParams.get('from_ts') || undefined,
    to_ts: searchParams.get('to_ts') || undefined,
    limit: pagination.limit,
    offset: pagination.offset,
  }), [botId, channel, pagination.limit, pagination.offset, searchParams, sessionId])

  const loadBots = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['bot']), [])
  const loadSessions = useCallback(async () => {
    const options = scopeOptionsFor(await getScopeOptions(), ['session'])
    return botId ? options.filter((option) => option.description?.startsWith(`${botId} ·`)) : options
  }, [botId])
  const loadChannels = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['channel']), [])

  const load = useCallback(async () => {
    setStatus('loading')
    setError(undefined)
    try {
      const next = await listInjectionTraces(filters)
      setPayload(next)
      setStatus(next.items.length ? 'success' : 'empty')
    } catch (reason) {
      setPayload(null)
      setError(reason)
      setStatus('error')
    }
  }, [filters])

  const loadFeedback = useCallback(async () => {
    setFeedbackStatus('loading')
    setFeedbackError(undefined)
    try {
      const next = await getAgentFeedback()
      const records = next.feedback_records ?? []
      setFeedback(next)
      setFeedbackStatus(records.length ? 'success' : 'empty')
    } catch (reason) {
      setFeedback(null)
      setFeedbackError(reason)
      setFeedbackStatus('error')
    }
  }, [])

  useEffect(() => { void load() }, [load])
  useEffect(() => { void loadFeedback() }, [loadFeedback])

  useEffect(() => {
    if (!selectedTraceId) {
      setDetail(null)
      setDetailError('')
      return
    }
    let active = true
    setDetail(null)
    setDetailLoading(true)
    setDetailError('')
    getInjectionTrace(selectedTraceId)
      .then((value) => { if (active) setDetail(value) })
      .catch((reason: unknown) => { if (active) setDetailError(reason instanceof Error ? reason.message : 'Trace 详情加载失败') })
      .finally(() => { if (active) setDetailLoading(false) })
    return () => { active = false }
  }, [selectedTraceId])

  function selectTrace(traceId: string | null) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      if (traceId) next.set('trace_id', traceId)
      else next.delete('trace_id')
      return next
    })
  }

  function resetFilters() {
    setSearchParams((current) => {
      const next = new URLSearchParams()
      const traceId = current.get('trace_id')
      if (traceId) next.set('trace_id', traceId)
      return next
    })
  }

  const feedbackRecords = feedback?.feedback_records ?? []

  return (
    <div data-slot="observatory-page" className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Observatory · 注入观测台</CardTitle>
          <CardDescription>筛选 Trace 摘要并按需读取结构化详情。筛选保存在 URL；Bot、会话和通道均来自服务端真实选项。</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <FieldGroup className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <ScopeSelect value={botId || undefined} loadOptions={loadBots} label="Bot" placeholder="选择真实 Bot" onValueChange={(value) => pagination.setFilters({ bot_id: value, session_id: null })} />
            <ScopeSelect value={sessionId || undefined} loadOptions={loadSessions} label="结构化会话" placeholder="选择真实会话" disabled={!botId} onValueChange={(value) => pagination.setFilters({ session_id: value })} />
            <ScopeSelect value={channel || undefined} loadOptions={loadChannels} label="通道" placeholder="选择已注册通道" onValueChange={(value) => pagination.setFilters({ channel: value })} />
            <Field><FieldLabel htmlFor="trace-group">Legacy 群 / 会话 ID</FieldLabel><Input id="trace-group" value={searchParams.get('group_id') ?? ''} onChange={(event) => pagination.setFilters({ group_id: event.target.value })} placeholder="仅按实际 group_id 筛选" /></Field>
            <Field><FieldLabel htmlFor="trace-sender">发送者 ID</FieldLabel><Input id="trace-sender" value={searchParams.get('sender_id') ?? ''} onChange={(event) => pagination.setFilters({ sender_id: event.target.value })} /></Field>
            <Field>
              <FieldLabel>状态</FieldLabel>
              <Select value={searchParams.get('status') || 'all'} onValueChange={(value) => pagination.setFilters({ status: value === 'all' ? null : value })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent><SelectGroup><SelectItem value="all">全部状态</SelectItem><SelectItem value="ok">正常</SelectItem><SelectItem value="error">错误</SelectItem><SelectItem value="timeout">超时</SelectItem><SelectItem value="skipped">已跳过</SelectItem></SelectGroup></SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel>错误</FieldLabel>
              <Select value={searchParams.get('has_error') || 'all'} onValueChange={(value) => pagination.setFilters({ has_error: value === 'all' ? null : value })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent><SelectGroup><SelectItem value="all">全部</SelectItem><SelectItem value="true">有错误</SelectItem><SelectItem value="false">无错误</SelectItem></SelectGroup></SelectContent>
              </Select>
            </Field>
            <Field><FieldLabel htmlFor="trace-scope">Scope / chat type</FieldLabel><Input id="trace-scope" value={searchParams.get('scope') ?? ''} onChange={(event) => pagination.setFilters({ scope: event.target.value })} placeholder="按 trace 实际 scope 筛选" /></Field>
            <Field><FieldLabel htmlFor="trace-from">开始时间</FieldLabel><Input id="trace-from" type="datetime-local" value={epochToInput(searchParams.get('from_ts'))} onChange={(event) => pagination.setFilters({ from_ts: inputToEpoch(event.target.value) })} /></Field>
            <Field><FieldLabel htmlFor="trace-to">结束时间</FieldLabel><Input id="trace-to" type="datetime-local" value={epochToInput(searchParams.get('to_ts'))} onChange={(event) => pagination.setFilters({ to_ts: inputToEpoch(event.target.value) })} /></Field>
            <Field><FieldLabel htmlFor="trace-revision">配置 revision</FieldLabel><Input id="trace-revision" value={searchParams.get('config_revision') ?? ''} onChange={(event) => pagination.setFilters({ config_revision: event.target.value })} placeholder="例如 cfg-…" /></Field>
          </FieldGroup>
          <div className="flex flex-wrap gap-2">
            <Button type="button" disabled={status === 'loading'} onClick={() => void load()}><SearchIcon data-icon="inline-start" aria-hidden="true" />查询</Button>
            <Button type="button" variant="outline" disabled={status === 'loading'} onClick={() => { void load(); void loadFeedback() }}><RefreshCwIcon data-icon="inline-start" aria-hidden="true" />刷新</Button>
            <Button type="button" variant="ghost" onClick={resetFilters}>清除筛选</Button>
          </div>
        </CardContent>
      </Card>

      <Alert>
        <AlertTitle>如何阅读与验证 Trace</AlertTitle>
        <AlertDescription>先核对真实 Bot 与结构化 session，再检查 revision、通道状态、预算、命中、过滤原因、错误和最终文本。legacy trace 缺少 Bot 或 session 时会明确标记缺失，不会用默认 Bot 或 group_id 伪造关联。</AlertDescription>
      </Alert>

      <Card>
        <CardHeader>
          <CardTitle>Trace 摘要</CardTitle>
          <CardDescription>{payload?.page.total_status === 'exact' ? `当前筛选共 ${payload.page.total ?? 0} 条` : `总数不可用：${payload?.page.reason_code ?? '等待查询'}`}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <QueryState status={status} error={error} onRetry={() => void load()}>
            <div className="overflow-auto rounded-lg border">
              <Table>
                <TableHeader><TableRow><TableHead>Trace / 时间</TableHead><TableHead>Bot</TableHead><TableHead>会话</TableHead><TableHead>模式 / 状态</TableHead><TableHead>预览</TableHead><TableHead>命中 / 跳过 / 错误</TableHead><TableHead>主 Token 通道</TableHead><TableHead>Token / 耗时</TableHead><TableHead>Revision</TableHead></TableRow></TableHeader>
                <TableBody>{payload?.items.map((trace) => {
                  const session = traceSession(trace)
                  const traceId = String(trace.trace_id ?? '')
                  const traceStatus = String(trace.status ?? (trace.has_error ? 'error' : 'unknown'))
                  return (
                    <TableRow key={traceId} className="cursor-pointer" onClick={() => traceId && selectTrace(traceId)}>
                      <TableCell><span className="flex min-w-40 flex-col"><Button type="button" variant="link" className="h-auto justify-start p-0 font-mono text-xs" onClick={(event) => { event.stopPropagation(); selectTrace(traceId) }}>{traceId || '未记录 trace_id'}</Button><span className="text-xs text-muted-foreground">{formatTime(trace.timestamp ?? trace.created_at)}</span></span></TableCell>
                      <TableCell className="font-mono text-xs">{traceBot(trace)}</TableCell>
                      <TableCell><span className="flex min-w-44 flex-col"><span>{session.primary}</span>{session.secondary ? <span className="text-xs text-muted-foreground">{session.secondary}</span> : null}</span></TableCell>
                      <TableCell><span className="flex flex-col gap-1"><span>{textValue(trace.mode)}</span><Badge className="w-fit" variant={traceStatus === 'ok' ? 'secondary' : traceStatus === 'unknown' ? 'outline' : 'destructive'}>{statusLabel(traceStatus)}</Badge></span></TableCell>
                      <TableCell className="max-w-md truncate" title={tracePreview(trace)}>{tracePreview(trace)}</TableCell>
                      <TableCell className="font-mono text-xs">{channelCount(trace, 'hit')} / {channelCount(trace, 'skipped')} / {channelCount(trace, 'error')}</TableCell>
                      <TableCell className="font-mono text-xs">{primaryTokenChannel(trace)}</TableCell>
                      <TableCell>{textValue(trace.total_tokens ?? trace.tokens)} / {traceLatency(trace)}</TableCell>
                      <TableCell className="font-mono text-xs">{textValue(trace.config_revision)}</TableCell>
                    </TableRow>
                  )
                })}</TableBody>
              </Table>
            </div>
          </QueryState>
          {payload ? <PaginationControls page={payload.page} onOffsetChange={pagination.setOffset} onLimitChange={pagination.setLimit} disabled={status === 'loading'} /> : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>反馈记录</CardTitle><CardDescription>最近 trace / memory 反馈只读展示；需要查看候选、审核与晋升时进入学习中心。</CardDescription></CardHeader>
        <CardContent>
          <QueryState status={feedbackStatus} error={feedbackError} onRetry={() => void loadFeedback()}>
            <div className="overflow-auto rounded-lg border">
              <Table>
                <TableHeader><TableRow><TableHead>ID</TableHead><TableHead>反馈</TableHead><TableHead>Trace</TableHead><TableHead>记忆</TableHead><TableHead>原因</TableHead><TableHead>时间</TableHead></TableRow></TableHeader>
                <TableBody>{feedbackRecords.slice(0, 50).map((item, index) => <TableRow key={`feedback-${String(item.id ?? index)}-${index}`}><TableCell className="font-mono text-xs">{textValue(item.id)}</TableCell><TableCell><Badge variant="secondary">{textValue(item.feedback)}</Badge></TableCell><TableCell className="font-mono text-xs">{textValue(item.trace_id)}</TableCell><TableCell className="font-mono text-xs">{textValue(item.memory_id)}</TableCell><TableCell className="max-w-lg truncate" title={textValue(item.reason ?? item.content)}>{textValue(item.reason ?? item.content)}</TableCell><TableCell>{formatTime(item.created_at ?? item.timestamp)}</TableCell></TableRow>)}</TableBody>
              </Table>
            </div>
          </QueryState>
        </CardContent>
      </Card>

      <TraceDetailSheet open={Boolean(selectedTraceId)} onOpenChange={(open) => { if (!open) selectTrace(null) }} detail={detail} loading={detailLoading} error={detailError} />
    </div>
  )
}
