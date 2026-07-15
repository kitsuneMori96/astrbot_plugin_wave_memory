import { useEffect, useMemo, useRef, useState } from 'react'
import { RefreshCwIcon, SearchIcon, ShieldCheckIcon, TagsIcon } from 'lucide-react'

import { isRequestCancelled } from '@/api/client'
import { getTagQuality, getTags, type TagListPayload, type TagQualityPayload } from '@/api/tags'
import { PaginationControls, QueryState, ResponsiveTable, type PageSize } from '@/components/shared'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

function formatPercent(value: number): string {
  return `${(Math.max(0, Math.min(1, value || 0)) * 100).toFixed(1).replace(/\.0$/, '')}%`
}

function formatConfidence(value: number): string {
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : '未知'
}

function Metric({ label, value, detail }: { label: string; value: string | number; detail: string }) {
  return <div className="min-w-0 rounded-lg border bg-card p-4"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p><p className="mt-1 text-xs text-muted-foreground">{detail}</p></div>
}

export function TagsPage() {
  const [quality, setQuality] = useState<TagQualityPayload | null>(null)
  const [data, setData] = useState<TagListPayload | null>(null)
  const [error, setError] = useState<unknown>()
  const [loading, setLoading] = useState(true)
  const [reload, setReload] = useState(0)
  const [searchDraft, setSearchDraft] = useState('')
  const [search, setSearch] = useState('')
  const [type, setType] = useState('')
  const [sort, setSort] = useState<'frequency' | 'recent'>('frequency')
  const [limit, setLimit] = useState<PageSize>(25)
  const [offset, setOffset] = useState(0)
  const requestRef = useRef<AbortController | null>(null)

  useEffect(() => {
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller
    setLoading(true)
    setError(undefined)
    Promise.all([
      getTags({ limit, offset, type, search, sort, signal: controller.signal }),
      getTagQuality(controller.signal),
    ]).then(([tags, nextQuality]) => {
      if (controller.signal.aborted || requestRef.current !== controller) return
      setData(tags)
      setQuality(nextQuality)
    }).catch((reason: unknown) => {
      if (controller.signal.aborted || requestRef.current !== controller || isRequestCancelled(reason)) return
      setData(null)
      setQuality(null)
      setError(reason)
    }).finally(() => {
      if (!controller.signal.aborted && requestRef.current === controller) setLoading(false)
    })
    return () => controller.abort()
  }, [limit, offset, reload, search, sort, type])

  const tagTypes = useMemo(() => Array.from(new Set((data?.items ?? []).map((item) => item.type).filter(Boolean))).sort(), [data?.items])
  const status = loading ? 'loading' : error ? 'error' : !data?.items.length ? 'empty' : 'success'
  const total = data?.total ?? 0
  const pageCount = total ? Math.ceil(total / limit) : 0

  return <div className="flex flex-col gap-5" data-page="tags">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <header className="max-w-2xl"><div className="flex items-center gap-2"><TagsIcon className="size-5 text-primary" aria-hidden="true" /><h1 className="text-xl font-bold tracking-tight">Tag 浪潮总览</h1></div><p className="mt-1 text-xs text-muted-foreground">查看真实 Tag 覆盖率、频率、类型与置信度。当前 legacy Tag 投影保持只读。</p></header>
      <Button type="button" size="sm" variant="outline" disabled={loading} onClick={() => setReload((value) => value + 1)}><RefreshCwIcon aria-hidden="true" />刷新</Button>
    </div>

    <Alert><ShieldCheckIcon aria-hidden="true" /><AlertTitle>只读安全边界</AlertTitle><AlertDescription>本页不会开放裸 Tag ID 的重命名、改类型或删除。需要写入的能力必须通过 scoped ObjectRef 命令与后端 capability 明确授权。</AlertDescription></Alert>

    <section aria-label="Tag 质量概览" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Metric label="Tag 总数" value={quality?.total_tags ?? '—'} detail="当前 legacy Tag 投影记录" />
      <Metric label="记忆覆盖率" value={quality ? formatPercent(quality.coverage) : '—'} detail={quality ? `${quality.tagged_memories} / ${quality.total_memories} 条真实记忆` : '等待真实质量接口'} />
      <Metric label="可提取未标注" value={quality?.extractable_untagged_memories ?? '—'} detail={`短文本跳过 ${quality?.skipped_short_untagged_memories ?? '—'} 条`} />
      <Metric label="孤立引用" value={quality?.orphan_memory_tag_refs ?? '—'} detail="仅诊断，不在本页直接清理" />
    </section>

    <Card className="border-border/60"><CardHeader className="gap-3 border-b pb-3"><div><CardTitle className="text-sm">Tag 目录</CardTitle><CardDescription>服务端分页的只读结果；搜索和排序会重新读取真实数据。</CardDescription></div><form className="flex flex-wrap items-center gap-2" onSubmit={(event) => { event.preventDefault(); setOffset(0); setSearch(searchDraft.trim()) }}><div className="relative min-w-0 flex-1 basis-56"><SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" /><Input aria-label="搜索 Tag" className="h-8 pl-8" value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder="按 Tag 名称搜索" /></div><select aria-label="Tag 类型" className="h-8 rounded-md border bg-background px-2 text-xs" value={type} onChange={(event) => { setOffset(0); setType(event.target.value) }}><option value="">全部类型</option>{tagTypes.map((value) => <option key={value} value={value}>{value}</option>)}</select><select aria-label="排序方式" className="h-8 rounded-md border bg-background px-2 text-xs" value={sort} onChange={(event) => { setOffset(0); setSort(event.target.value === 'recent' ? 'recent' : 'frequency') }}><option value="frequency">按频率</option><option value="recent">按最近创建</option></select><Button type="submit" size="sm">查询</Button></form></CardHeader><CardContent className="p-4"><QueryState status={status} error={error} title="Tag 数据读取失败" onRetry={() => setReload((value) => value + 1)} description={status === 'empty' ? '当前筛选条件下没有 Tag；未使用演示数据填充。' : undefined}>
      {data?.items.length ? <><ResponsiveTable label="Tag 只读目录" table={<Table><TableHeader><TableRow className="bg-muted/20"><TableHead>Tag</TableHead><TableHead>类型</TableHead><TableHead className="text-right">频率</TableHead><TableHead className="text-right">置信度</TableHead><TableHead>能力</TableHead></TableRow></TableHeader><TableBody>{data.items.map((item) => <TableRow key={String(item.id)}><TableCell className="font-medium">{item.name}</TableCell><TableCell><Badge variant="outline">{item.type || '未分类'}</Badge></TableCell><TableCell className="text-right tabular-nums">{item.frequency}</TableCell><TableCell className="text-right tabular-nums">{formatConfidence(item.confidence)}</TableCell><TableCell><Badge variant="secondary">只读</Badge></TableCell></TableRow>)}</TableBody></Table>} cards={data.items.map((item) => <article key={String(item.id)} className="rounded-lg border bg-card p-4"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="truncate font-medium">{item.name}</p><p className="mt-1 text-xs text-muted-foreground">{item.type || '未分类'}</p></div><Badge variant="secondary">只读</Badge></div><dl className="mt-3 grid grid-cols-2 gap-3 text-sm"><div><dt className="text-xs text-muted-foreground">频率</dt><dd className="font-medium tabular-nums">{item.frequency}</dd></div><div><dt className="text-xs text-muted-foreground">置信度</dt><dd className="font-medium tabular-nums">{formatConfidence(item.confidence)}</dd></div></dl></article>)} />
      <PaginationControls page={{ limit, offset, total, total_status: 'exact', reason_code: null, page: Math.floor(offset / limit) + 1, page_count: pageCount, has_more: offset + limit < total }} disabled={loading} onOffsetChange={setOffset} onLimitChange={(value) => { setOffset(0); setLimit(value) }} label="Tag 分页" /></> : null}
    </QueryState></CardContent></Card>
  </div>
}
