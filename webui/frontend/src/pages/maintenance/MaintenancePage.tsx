import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  BanIcon,
  ChevronDownIcon,
  HistoryIcon,
  RefreshCwIcon,
  SearchCheckIcon,
  SlidersIcon,
} from 'lucide-react'
import { toast } from 'sonner'

import {
  cancelMaintenanceJob,
  getMaintenanceCheckpoint,
  getMaintenanceJob,
  getMaintenanceLogs,
  listMaintenanceJobs,
  type MaintenanceCheckpoint,
  type MaintenanceJob,
  type MaintenanceLog,
} from '@/api/maintenance'
import { PaginationControls, QueryState } from '@/components/shared'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { usePaginationSearchParams } from '@/hooks/use-pagination-search-params'
import { MaintainPage } from '@/pages/maintain/MaintainPage'

const activeStatuses = new Set(['pending', 'queued', 'running'])

function jobKind(job: MaintenanceJob): string {
  const candidates = [
    job.kind,
    job.operation?.kind,
    typeof job.cursor?.kind === 'string' ? job.cursor.kind : undefined,
    typeof job.progress?.kind === 'string' ? job.progress.kind : undefined,
    typeof job.result?.kind === 'string' ? job.result.kind : undefined,
  ].filter(Boolean).join(' ')
  if (candidates.includes('tag_backfill') || job.result?.bounded === true || 'after_id' in (job.cursor ?? {})) return '标签补提取'
  if (candidates.includes('tag_audit') || 'strategy' in (job.cursor ?? {}) || 'total_suggestions' in (job.progress ?? {})) return '标签质量审计'
  if (candidates.includes('import') || 'mode' in (job.progress ?? {}) || 'source_id' in (job.result ?? {})) return '外部记忆导入'
  if (candidates.includes('memory_index')) return '记忆索引维护'
  if (candidates.includes('tag_index')) return '标签索引维护'
  if (candidates.includes('cooccurrence')) return '共现索引维护'
  return '后台维护任务'
}

function statusLabel(status?: string): string {
  return ({
    pending: '等待调度',
    queued: '排队中',
    running: '执行中',
    succeeded: '已成功',
    failed: '已失败',
    cancelled: '已取消',
  } as Record<string, string>)[status ?? ''] ?? '状态未知'
}

function statusVariant(status?: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'succeeded') return 'default'
  if (status === 'failed') return 'destructive'
  if (status === 'pending' || status === 'queued' || status === 'running') return 'secondary'
  return 'outline'
}

function formatTime(value?: number | null): string {
  return value && Number.isFinite(value) ? new Date(value * 1000).toLocaleString('zh-CN') : '未记录'
}

function firstNumber(records: Array<Record<string, unknown> | null | undefined>, keys: string[]): number | null {
  for (const record of records) {
    if (!record) continue
    for (const key of keys) {
      const value = Number(record[key])
      if (Number.isFinite(value)) return value
    }
  }
  return null
}

function progressOf(job: MaintenanceJob) {
  const records = [job.progress, job.cursor, job.result]
  const current = firstNumber(records, ['processed', 'processed_count', 'imported', 'current']) ?? 0
  const total = firstNumber(records, ['total', 'total_count', 'total_scanned', 'selected']) ?? 0
  const explicit = firstNumber(records, ['progress', 'ratio', 'percent'])
  const ratio = explicit === null ? (total > 0 ? current / total : job.status === 'succeeded' ? 1 : 0) : explicit > 1 ? explicit / 100 : explicit
  return { current, total, ratio: Math.min(1, Math.max(0, ratio)) }
}

function dataSummary(value: unknown): string {
  if (!value || typeof value !== 'object') return ''
  const data = value as Record<string, unknown>
  const parts: string[] = []
  if (typeof data.status === 'string') parts.push(`状态 ${statusLabel(data.status)}`)
  if (typeof data.message === 'string') parts.push(data.message)
  const progress = data.progress
  if (progress && typeof progress === 'object') {
    const current = firstNumber([progress as Record<string, unknown>], ['processed', 'imported', 'current'])
    const total = firstNumber([progress as Record<string, unknown>], ['total', 'selected'])
    if (current !== null) parts.push(`已处理 ${current}${total !== null ? ` / ${total}` : ''}`)
  }
  if (typeof data.code === 'string') parts.push(`错误码 ${data.code}`)
  return parts.join(' · ')
}

function logLabel(event: string): string {
  return ({ scheduled: '任务已调度', checkpoint: '进度 checkpoint', result: '任务结果', failure: '任务失败' } as Record<string, string>)[event] ?? event
}

function JobProgress({ job }: { job: MaintenanceJob }) {
  const progress = progressOf(job)
  return <div className="flex flex-col gap-2">
    <div className="flex justify-between gap-3 text-xs text-muted-foreground">
      <span>{progress.total > 0 ? `已处理 ${progress.current.toLocaleString('zh-CN')} / ${progress.total.toLocaleString('zh-CN')}` : '等待进度 checkpoint'}</span>
      <span>{Math.round(progress.ratio * 100)}%</span>
    </div>
    <div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary transition-all" style={{ width: `${Math.round(progress.ratio * 100)}%` }} /></div>
  </div>
}

export function MaintenancePage() {
  const pagination = usePaginationSearchParams()
  const [params, setParams] = useSearchParams()
  const jobId = params.get('job_id') ?? ''
  const activeTab = params.get('tab') === 'jobs' || (jobId && !params.has('tab')) ? 'jobs' : 'workbench'
  const [jobs, setJobs] = useState<Awaited<ReturnType<typeof listMaintenanceJobs>> | null>(null)
  const [status, setStatus] = useState<'loading' | 'success' | 'empty' | 'error'>('loading')
  const [error, setError] = useState<unknown>()
  const [detail, setDetail] = useState<MaintenanceJob | null>(null)
  const [logs, setLogs] = useState<MaintenanceLog[]>([])
  const [checkpoint, setCheckpoint] = useState<MaintenanceCheckpoint | null>(null)

  const setActiveTab = useCallback((tab: string) => {
    setParams((current) => {
      const next = new URLSearchParams(current)
      next.set('tab', tab)
      return next
    })
  }, [setParams])

  const selectJob = useCallback((id: string) => {
    setParams((current) => {
      const next = new URLSearchParams(current)
      next.set('tab', 'jobs')
      next.set('job_id', id)
      return next
    })
  }, [setParams])

  const loadJobs = useCallback(async () => {
    setStatus('loading')
    setError(undefined)
    try {
      const next = await listMaintenanceJobs(pagination.limit, pagination.offset)
      setJobs(next)
      setStatus(next.items.length ? 'success' : 'empty')
    } catch (reason) {
      setError(reason)
      setStatus('error')
    }
  }, [pagination.limit, pagination.offset])

  useEffect(() => { if (activeTab === 'jobs') void loadJobs() }, [activeTab, loadJobs])

  useEffect(() => {
    if (!jobId) {
      setDetail(null)
      setLogs([])
      setCheckpoint(null)
      return
    }
    let active = true
    let timer: number | undefined
    const read = async () => {
      try {
        const [jobPayload, logPage, state] = await Promise.all([
          getMaintenanceJob(jobId),
          getMaintenanceLogs(jobId, 100, 0),
          getMaintenanceCheckpoint(jobId),
        ])
        if (!active) return
        setDetail(jobPayload.item)
        setLogs(logPage.items)
        setCheckpoint(state)
        if (activeStatuses.has(jobPayload.item.status)) timer = window.setTimeout(() => void read(), 2000)
      } catch (reason) {
        if (active) toast.error(reason instanceof Error ? reason.message : '任务详情加载失败')
      }
    }
    void read()
    return () => { active = false; if (timer) window.clearTimeout(timer) }
  }, [jobId])

  async function cancel() {
    if (!detail) return
    try {
      const result = await cancelMaintenanceJob(detail.run_id)
      setDetail(result.item)
      toast.info('取消请求已提交；请以任务最终状态为准。')
      void loadJobs()
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '取消请求失败')
    }
  }

  return <div data-slot="maintenance-page" className="flex flex-col gap-6">
    <Card>
      <CardHeader><CardTitle>标签与维护中心</CardTitle><CardDescription>恢复标签提取、质量审计和建议审核工作台；后台任务历史保持可刷新恢复，queued/202 仅表示已受理。</CardDescription></CardHeader>
      <CardContent className="flex flex-wrap gap-2"><Button asChild variant="outline"><Link to="/diagnostics/indexes"><SearchCheckIcon />只读索引诊断</Link></Button>{activeTab === 'jobs' ? <Button variant="outline" onClick={() => void loadJobs()}><RefreshCwIcon />刷新任务历史</Button> : null}</CardContent>
    </Card>

    <Tabs value={activeTab} onValueChange={setActiveTab} className="flex flex-col gap-5">
      <TabsList className="w-fit"><TabsTrigger value="workbench"><SlidersIcon />维护工作台</TabsTrigger><TabsTrigger value="jobs"><HistoryIcon />Durable 任务历史</TabsTrigger></TabsList>
      <TabsContent value="workbench" className="mt-0"><MaintainPage /></TabsContent>
      <TabsContent value="jobs" className="mt-0 flex flex-col gap-5">
        <QueryState status={status} error={error} onRetry={() => void loadJobs()} title="还没有维护任务" description="标签提取、质量审计和导入任务创建后会出现在这里。">
          <div className="grid gap-3 lg:grid-cols-2">
            {jobs?.items.map((job) => <Card key={job.run_id} className={job.run_id === jobId ? 'border-primary/60' : undefined}>
              <CardHeader><div className="flex flex-wrap items-start justify-between gap-2"><div><CardTitle className="text-base">{jobKind(job)}</CardTitle><CardDescription>{formatTime(job.created_at)}{job.updated_at && job.updated_at !== job.created_at ? ` · 更新于 ${formatTime(job.updated_at)}` : ''}</CardDescription></div><Badge variant={statusVariant(job.status)}>{statusLabel(job.status)}</Badge></div></CardHeader>
              <CardContent className="flex flex-col gap-4"><JobProgress job={job} /><div className="flex flex-wrap items-center justify-between gap-3"><details className="text-xs text-muted-foreground"><summary className="flex cursor-pointer items-center gap-1"><ChevronDownIcon className="size-3" />技术详情</summary><div className="mt-2 font-mono">job_id: {job.run_id}<br />request_id: {job.request_id}</div></details><Button size="sm" variant="outline" onClick={() => selectJob(job.run_id)}>查看进度与日志</Button></div></CardContent>
            </Card>)}
          </div>
        </QueryState>
        {jobs ? <PaginationControls page={jobs.page} onOffsetChange={pagination.setOffset} onLimitChange={pagination.setLimit} /> : null}

        {detail ? <Card>
          <CardHeader><div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle>{jobKind(detail)}详情</CardTitle><CardDescription>创建于 {formatTime(detail.created_at)} · 最近更新 {formatTime(detail.updated_at)}</CardDescription></div><Badge variant={statusVariant(detail.status)}>{statusLabel(detail.status)}</Badge></div></CardHeader>
          <CardContent className="flex flex-col gap-5">
            <JobProgress job={detail} />
            <div className="grid gap-3 md:grid-cols-3">
              <div className="rounded-lg border bg-muted/20 p-3"><p className="text-xs text-muted-foreground">当前阶段</p><p className="mt-1 font-medium">{typeof checkpoint?.checkpoint?.phase === 'string' ? checkpoint.checkpoint.phase : activeStatuses.has(detail.status) ? '等待 checkpoint' : statusLabel(detail.status)}</p></div>
              <div className="rounded-lg border bg-muted/20 p-3"><p className="text-xs text-muted-foreground">错误状态</p><p className="mt-1 font-medium">{detail.error_code ?? '无错误'}</p></div>
              <div className="rounded-lg border bg-muted/20 p-3"><p className="text-xs text-muted-foreground">checkpoint 更新时间</p><p className="mt-1 font-medium">{formatTime(checkpoint?.updated_at)}</p></div>
            </div>
            <div><h3 className="mb-2 text-sm font-semibold">任务日志</h3><div className="max-h-64 overflow-auto rounded-lg border bg-muted/20 p-3 font-mono text-xs">{logs.length ? logs.map((log, index) => <div key={`${log.at}-${log.event}-${index}`} className={log.level === 'error' ? 'text-destructive' : 'text-muted-foreground'}>[{formatTime(log.at)}] {logLabel(log.event)}{dataSummary(log.data) ? ` · ${dataSummary(log.data)}` : ''}</div>) : <p className="text-muted-foreground">当前没有任务日志。</p>}</div></div>
            {detail.error_message ? <AlertBox message={detail.error_message} /> : null}
            <div className="flex flex-wrap items-center justify-between gap-3"><details className="rounded-md border p-3 text-xs text-muted-foreground"><summary className="cursor-pointer font-medium text-foreground">技术详情</summary><div className="mt-2 font-mono">job_id: {detail.run_id}<br />request_id: {detail.request_id}<br />checkpoint source: {checkpoint?.source ?? '未记录'}</div></details><Button variant="destructive" disabled={!activeStatuses.has(detail.status)} onClick={() => void cancel()}><BanIcon />请求取消</Button></div>
          </CardContent>
        </Card> : null}
      </TabsContent>
    </Tabs>
  </div>
}

function AlertBox({ message }: { message: string }) {
  return <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">{message}</div>
}
