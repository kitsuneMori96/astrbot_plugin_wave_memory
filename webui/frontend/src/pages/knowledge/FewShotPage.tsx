import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { EyeIcon, RefreshCwIcon, SearchIcon } from 'lucide-react'

import { getApprovedFewShot, type ApprovedFewShot } from '@/api/knowledge'
import { getScopeOptions, scopeOptionsFor } from '@/api/options'
import { PaginationControls, QueryState, ResponsiveDetail, ScopeSelect, usePaginationSearchParams, type PageResponse } from '@/components/shared'
import { useCanonicalScopeDefault } from '@/hooks/use-pagination-search-params'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

function score(value: number | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '未评分'
}

function formatTime(value: number | null | undefined): string {
  if (value === undefined || value === null) return '未记录'
  const millis = value < 10_000_000_000 ? value * 1000 : value
  const date = new Date(millis)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN')
}

function FewShotDetail({ item }: { item: ApprovedFewShot }) {
  return <div className="flex flex-col gap-4">
    <div className="flex flex-wrap gap-2"><Badge>approved</Badge><Badge variant="secondary">healthy</Badge><Badge variant="outline">Bot {item.bot_id}</Badge></div>
    <div><h3 className="mb-1 text-sm font-medium text-muted-foreground">正式范例内容</h3><p className="whitespace-pre-wrap rounded-md border p-3 leading-relaxed">{item.content}</p></div>
    <dl className="grid gap-3 sm:grid-cols-2">
      <div><dt className="text-sm font-medium text-muted-foreground">稳定 ID</dt><dd className="font-mono">{item.id}</dd></div>
      <div><dt className="text-sm font-medium text-muted-foreground">评分</dt><dd>{score(item.score)}</dd></div>
      <div><dt className="text-sm font-medium text-muted-foreground">创建时间</dt><dd>{formatTime(item.created_at)}</dd></div>
      <div><dt className="text-sm font-medium text-muted-foreground">批准时间</dt><dd>{formatTime(item.approved_at)}</dd></div>
    </dl>
    <div><h3 className="mb-2 text-sm font-medium text-muted-foreground">风格特征</h3><div className="flex flex-wrap gap-1">{item.traits?.length ? item.traits.map((trait) => <Badge key={trait} variant="outline">{trait}</Badge>) : <span className="text-muted-foreground">未记录 traits</span>}</div></div>
  </div>
}

function SummaryTile({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return <div className="min-w-[7rem] rounded-lg border bg-muted/20 px-3 py-2 text-center"><div className="text-xs text-muted-foreground">{label}</div><div className={`text-lg font-semibold ${tone ?? ''}`}>{value}</div></div>
}

export function FewShotPage() {
  const pagination = usePaginationSearchParams()
  const botId = pagination.searchParams.get('bot_id') ?? ''
  const sessionId = pagination.searchParams.get('session_id') ?? ''
  const search = pagination.searchParams.get('search') ?? ''
  useCanonicalScopeDefault({ botId, sessionId, setFilters: pagination.setFilters })
  const [searchDraft, setSearchDraft] = useState(search)
  const [data, setData] = useState<PageResponse<ApprovedFewShot> | null>(null)
  const [error, setError] = useState<unknown>()
  const [loading, setLoading] = useState(false)
  const [reload, setReload] = useState(0)
  const loadBots = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['bot']), [])
  const loadSessions = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['session']).filter((option) => option.description?.startsWith(`${botId} ·`)), [botId])

  useEffect(() => { setSearchDraft(search) }, [search])
  useEffect(() => {
    if (!botId || !sessionId) { setData(null); setLoading(false); setError(undefined); return }
    let active = true
    setLoading(true)
    setError(undefined)
    getApprovedFewShot({ bot_id: botId, session_id: sessionId, visibility: 'group', search: search || undefined, limit: pagination.limit, offset: pagination.offset })
      .then((value) => { if (active) setData(value) })
      .catch((reason: unknown) => { if (active) { setData(null); setError(reason) } })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [botId, pagination.limit, pagination.offset, reload, search, sessionId])

  const submitSearch = (event: FormEvent) => { event.preventDefault(); pagination.setFilters({ search: searchDraft.trim() }) }
  const pageItems = data?.items ?? []
  const scoredItems = pageItems.filter((item) => typeof item.score === 'number' && Number.isFinite(item.score))
  const averageScore = scoredItems.length ? (scoredItems.reduce((sum, item) => sum + (item.score ?? 0), 0) / scoredItems.length).toFixed(2) : '—'
  const traitCount = new Set(pageItems.flatMap((item) => item.traits ?? [])).size
  const total = data?.page.total_status === 'exact' ? data.page.total ?? 0 : '—'
  const status = !botId || !sessionId ? 'unknown' : loading ? 'loading' : error ? 'error' : !pageItems.length ? 'empty' : 'success'

  return <div className="flex flex-col gap-5" data-page="few-shot">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <header className="max-w-2xl"><h1 className="text-xl font-bold tracking-tight">FewShot 正式风格范例</h1><p className="text-xs text-muted-foreground">仅展示所选 Bot 下 approved 且通过健康检查的正式范例；候选、拒绝项与审核写操作不属于此页面。</p></header>
      <div className="flex flex-wrap gap-2"><SummaryTile label="筛选总数" value={total} /><SummaryTile label="本页平均分" value={averageScore} tone="text-emerald-600" /><SummaryTile label="本页 traits" value={traitCount} /></div>
    </div>

    <Card className="border-border/60"><CardHeader><CardTitle className="text-sm">真实群聊 Scope 与搜索</CardTitle><CardDescription>正式 FewShot 以完整 RuntimeScope 隔离；必须使用服务端证实的 BotProfile.db_id 与 canonical 群会话。</CardDescription></CardHeader><CardContent className="grid gap-4 lg:grid-cols-3">
      <ScopeSelect value={botId || undefined} loadOptions={loadBots} label="Bot" placeholder="选择真实 Bot" required onValueChange={(value) => pagination.setFilters({ bot_id: value, session_id: null })} />
      <ScopeSelect value={sessionId || undefined} loadOptions={loadSessions} label="群 / 会话" placeholder="选择 canonical 群会话" disabled={!botId} required onValueChange={(value) => pagination.setFilters({ session_id: value })} />
      <form className="flex items-end gap-2" onSubmit={submitSearch}><div className="relative min-w-0 flex-1"><SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" /><Input aria-label="搜索 FewShot" className="h-8 pl-8" value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder="搜索范例内容、traits" disabled={!botId || !sessionId} /></div><Button type="submit" size="sm" disabled={loading || !botId || !sessionId}>搜索</Button><Button type="button" size="sm" variant="outline" disabled={loading || !botId || !sessionId} onClick={() => setReload((value) => value + 1)}><RefreshCwIcon aria-hidden="true" /><span className="sr-only">刷新</span></Button></form>
    </CardContent></Card>

    <Card className="border-border/60"><CardContent className="flex flex-col gap-4 p-4">
      <QueryState status={status} error={error} title="FewShot 读取失败" description={!botId || !sessionId ? '请选择真实 Bot 与 canonical 群会话；页面禁止跨群或跨 Bot 汇总。' : '当前 RuntimeScope 与搜索条件下没有 approved / healthy 正式范例。'} onRetry={() => setReload((value) => value + 1)}>
        <div className="overflow-hidden rounded-lg border"><Table><TableHeader><TableRow className="bg-muted/20"><TableHead className="w-20">ID</TableHead><TableHead>范例内容</TableHead><TableHead className="w-24">评分</TableHead><TableHead className="w-64">Traits</TableHead><TableHead className="w-36">状态</TableHead><TableHead className="w-14"><span className="sr-only">详情</span></TableHead></TableRow></TableHeader><TableBody>{pageItems.map((item) => <TableRow key={item.id}><TableCell className="font-mono text-xs">{item.id}</TableCell><TableCell className="max-w-xl truncate leading-relaxed">{item.content}</TableCell><TableCell className="font-mono text-xs">{score(item.score)}</TableCell><TableCell><div className="flex max-w-64 gap-1 overflow-hidden">{item.traits?.length ? item.traits.slice(0, 3).map((trait) => <Badge key={trait} variant="outline" className="max-w-24 truncate">{trait}</Badge>) : <span className="text-xs text-muted-foreground">未记录</span>}</div></TableCell><TableCell><Badge>approved / healthy</Badge></TableCell><TableCell className="text-right"><ResponsiveDetail title={`FewShot #${item.id}`} description={`Bot ${item.bot_id} 的只读正式范例`} trigger={<Button type="button" variant="ghost" size="icon-sm" aria-label={`查看 FewShot ${item.id} 详情`}><EyeIcon aria-hidden="true" /></Button>}><FewShotDetail item={item} /></ResponsiveDetail></TableCell></TableRow>)}</TableBody></Table></div>
      </QueryState>
      {data?.page ? <PaginationControls page={data.page} onOffsetChange={pagination.setOffset} onLimitChange={pagination.setLimit} disabled={loading} label="FewShot 分页" /> : null}
    </CardContent></Card>

    <Card className="border-dashed"><CardHeader><CardTitle className="text-sm">健康与作用域说明</CardTitle><CardDescription>approved 只表示已批准；本页还要求内容通过身份污染与不安全风格检查。总数、分页和搜索始终限定在完整 RuntimeScope，不使用空 bot_id 或旧 group_id 作为汇总入口。</CardDescription></CardHeader></Card>
  </div>
}
