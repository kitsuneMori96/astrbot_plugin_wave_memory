import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { AlertCircleIcon, EyeIcon, RefreshCwIcon, SearchIcon } from 'lucide-react'

import { getLegacyFacts, getScopedFacts, type LegacyFactsPage, type ScopedFact, type ScopedFactsPage } from '@/api/knowledge'
import { getScopeOptions, scopeOptionsFor } from '@/api/options'
import { EvidenceList, PaginationControls, QueryState, ResponsiveDetail, ScopeSelect, usePaginationSearchParams } from '@/components/shared'
import { useCanonicalScopeDefault } from '@/hooks/use-pagination-search-params'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

function confidence(value: number | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '未知'
}

function FactDetail({ fact }: { fact: ScopedFact }) {
  return <div className="flex flex-col gap-4">
    <dl className="grid gap-3 sm:grid-cols-2">
      <div><dt className="text-sm font-medium text-muted-foreground">主体</dt><dd className="break-words">{fact.subject}</dd></div>
      <div><dt className="text-sm font-medium text-muted-foreground">谓词</dt><dd><Badge variant="outline">{fact.predicate}</Badge></dd></div>
      <div><dt className="text-sm font-medium text-muted-foreground">客体</dt><dd className="break-words">{fact.object}</dd></div>
      <div><dt className="text-sm font-medium text-muted-foreground">置信度</dt><dd>{confidence(fact.confidence)}</dd></div>
      <div><dt className="text-sm font-medium text-muted-foreground">状态</dt><dd><Badge variant="secondary">{fact.status || '未知'}</Badge></dd></div>
      <div><dt className="text-sm font-medium text-muted-foreground">来源记忆</dt><dd className="font-mono">{fact.source_memory_id ? `#${fact.source_memory_id}` : '未关联'}</dd></div>
    </dl>
    <div className="rounded-md border bg-muted/10 p-3 text-xs text-muted-foreground">作用域：Bot {fact.bot_id} · 会话 {fact.session_id} · group。页面不会跨作用域补齐关系。</div>
    <div><h3 className="mb-2 text-sm font-medium">同作用域证据</h3><EvidenceList evidence={fact.evidence} objectPath="/memories" emptyDescription="没有可验证的同作用域记忆证据；这不代表事实为假，但无法从此页面完成溯源。" /></div>
  </div>
}

function SummaryTile({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return <div className="min-w-[7rem] rounded-lg border bg-muted/20 px-3 py-2 text-center"><div className="text-xs text-muted-foreground">{label}</div><div className={`text-lg font-semibold ${tone ?? ''}`}>{value}</div></div>
}

export function FactsPage() {
  const pagination = usePaginationSearchParams()
  const botId = pagination.searchParams.get('bot_id') ?? ''
  const sessionId = pagination.searchParams.get('session_id') ?? ''
  const search = pagination.searchParams.get('search') ?? ''
  const statusFilter = pagination.searchParams.get('status') ?? ''
  useCanonicalScopeDefault({ botId, sessionId, setFilters: pagination.setFilters })
  const [searchDraft, setSearchDraft] = useState(search)
  const [data, setData] = useState<ScopedFactsPage | null>(null)
  const [error, setError] = useState<unknown>()
  const [loading, setLoading] = useState(false)
  const [reload, setReload] = useState(0)
  const [legacyData, setLegacyData] = useState<LegacyFactsPage | null>(null)
  const [legacyOffset, setLegacyOffset] = useState(0)
  const [legacyLoading, setLegacyLoading] = useState(true)

  const loadBots = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['bot']), [])
  const loadSessions = useCallback(async () => {
    const options = scopeOptionsFor(await getScopeOptions(), ['session'])
    return botId ? options.filter((option) => option.description?.startsWith(`${botId} ·`)) : []
  }, [botId])

  useEffect(() => { setSearchDraft(search) }, [search])
  useEffect(() => {
    if (!botId || !sessionId) { setData(null); setLoading(false); setError(undefined); return }
    let active = true
    setLoading(true)
    setError(undefined)
    getScopedFacts({
      bot_id: botId,
      session_id: sessionId,
      visibility: 'group',
      search: search || undefined,
      status: statusFilter || undefined,
      limit: pagination.limit,
      offset: pagination.offset,
    })
      .then((value) => { if (active) setData(value) })
      .catch((reason: unknown) => { if (active) { setData(null); setError(reason) } })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [botId, pagination.limit, pagination.offset, reload, search, sessionId, statusFilter])
  useEffect(() => {
    let active = true
    setLegacyLoading(true)
    getLegacyFacts({ search: search || undefined, limit: 25, offset: legacyOffset })
      .then((value) => { if (active) setLegacyData(value) })
      .catch(() => { if (active) setLegacyData(null) })
      .finally(() => { if (active) setLegacyLoading(false) })
    return () => { active = false }
  }, [legacyOffset, search])

  const submitSearch = (event: FormEvent) => { event.preventDefault(); pagination.setFilters({ search: searchDraft.trim() }) }
  const evidenceCount = data?.items.filter((fact) => fact.evidence_status === 'available').length ?? 0
  const lowConfidence = data?.items.filter((fact) => typeof fact.confidence === 'number' && fact.confidence < 0.5).length ?? 0
  const total = data?.page.total_status === 'exact' ? data.page.total ?? 0 : '—'
  const status = !botId || !sessionId ? 'unknown' : loading ? 'loading' : error ? 'error' : !data?.items.length ? 'empty' : 'success'

  return <div className="flex flex-col gap-5" data-page="facts">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <header className="max-w-2xl"><h1 className="text-xl font-bold tracking-tight">Facts 事实关系</h1><p className="text-xs text-muted-foreground">查看精确 Bot 与群会话内的 subject / predicate / object；证据只接受同一 group 作用域中健康、未隔离的记忆。</p></header>
      <div className="flex flex-wrap gap-2"><SummaryTile label="筛选总数" value={total} /><SummaryTile label="本页有证据" value={evidenceCount} tone="text-emerald-600" /><SummaryTile label="本页低置信" value={lowConfidence} tone="text-amber-600" /></div>
    </div>

    <Card className="border-border/60"><CardHeader><CardTitle className="text-sm">作用域与筛选</CardTitle><CardDescription>BotProfile.db_id 不是 QQ 号；必须先选择真实 Bot，再选择属于它的 canonical 群会话。</CardDescription></CardHeader><CardContent className="grid gap-4 md:grid-cols-2">
      <ScopeSelect value={botId || undefined} loadOptions={loadBots} label="Bot" placeholder="选择真实 Bot" required onValueChange={(value) => pagination.setFilters({ bot_id: value, session_id: null })} />
      <ScopeSelect value={sessionId || undefined} loadOptions={loadSessions} label="群 / 会话" placeholder="选择该 Bot 的真实群会话" disabled={!botId} required onValueChange={(value) => pagination.setFilters({ session_id: value })} />
      <form className="flex gap-2 md:col-span-2" onSubmit={submitSearch}><div className="relative min-w-0 flex-1"><SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" /><Input aria-label="搜索 Facts" className="h-8 pl-8" value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder="搜索主体、谓词或客体" /></div><select aria-label="事实状态" className="h-8 rounded-md border bg-background px-2 text-xs" value={statusFilter} onChange={(event) => pagination.setFilters({ status: event.target.value })}><option value="">全部状态</option><option value="active">active</option><option value="confirmed">confirmed</option><option value="pending">pending</option><option value="rejected">rejected</option></select><Button type="submit" size="sm" disabled={loading}>搜索</Button><Button type="button" size="sm" variant="outline" disabled={loading || !botId || !sessionId} onClick={() => setReload((value) => value + 1)}><RefreshCwIcon aria-hidden="true" /><span className="sr-only">刷新</span></Button></form>
    </CardContent></Card>

    <Card className="border-border/60"><CardContent className="flex flex-col gap-4 p-4">
      <QueryState status={status} error={error} title="Facts 读取失败" description={!botId || !sessionId ? '请从服务端真实选项中选择 Bot 与 canonical 群会话；页面不会读取 legacy facts 或跨 Bot 汇总。' : undefined} onRetry={() => setReload((value) => value + 1)}>
        <div className="overflow-hidden rounded-lg border"><Table><TableHeader><TableRow className="bg-muted/20"><TableHead className="w-20">ID</TableHead><TableHead>主体</TableHead><TableHead>谓词</TableHead><TableHead>客体</TableHead><TableHead className="w-24">置信度</TableHead><TableHead className="w-28">证据</TableHead><TableHead className="w-14"><span className="sr-only">详情</span></TableHead></TableRow></TableHeader><TableBody>{data?.items.map((fact) => <TableRow key={fact.id}><TableCell className="font-mono text-xs">{fact.id}</TableCell><TableCell className="font-medium">{fact.subject}</TableCell><TableCell><Badge variant="outline">{fact.predicate}</Badge></TableCell><TableCell className="max-w-sm truncate">{fact.object}</TableCell><TableCell className="font-mono text-xs">{confidence(fact.confidence)}</TableCell><TableCell><Badge variant={fact.evidence_status === 'available' ? 'default' : 'outline'}>{fact.evidence_status === 'available' ? '可溯源' : '不可用'}</Badge></TableCell><TableCell className="text-right"><ResponsiveDetail title={`Fact #${fact.id}`} description="只读关系、精确作用域与证据" trigger={<Button type="button" variant="ghost" size="icon-sm" aria-label={`查看 Fact ${fact.id} 详情`}><EyeIcon aria-hidden="true" /></Button>}><FactDetail fact={fact} /></ResponsiveDetail></TableCell></TableRow>)}</TableBody></Table></div>
      </QueryState>
      {data?.page ? <PaginationControls page={data.page} onOffsetChange={pagination.setOffset} onLimitChange={pagination.setLimit} disabled={loading} label="Facts 分页" /> : null}
    </CardContent></Card>

    <Card className="border-amber-500/10 bg-amber-500/[0.02]">
      <CardHeader className="py-4">
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="border-amber-500/20 text-amber-600 bg-amber-500/5">只读审计</Badge>
          <CardTitle className="text-base">Legacy Facts 历史事实</CardTitle>
        </div>
        <CardDescription>旧 facts 表没有 Bot / canonical session 列。这里仅供核查，不会把 group_id 猜成 qq:group:*，也不会混入上方正式事实。</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 pt-0">
        <Alert className="mb-4 border-amber-500/15 bg-amber-500/[0.02] text-amber-700 dark:text-amber-500"><AlertCircleIcon className="size-4 text-amber-600" /><AlertTitle>作用域未证实</AlertTitle><AlertDescription className="text-xs">{legacyData ? `共 ${(legacyData.page.total ?? 0).toLocaleString('zh-CN')} 条 Legacy facts。` : legacyLoading ? '正在读取审计清单。' : 'Legacy 审计接口暂不可用。'} 必须由唯一证据链完成投影后，记录才会进入正式区。</AlertDescription></Alert>
        {legacyData?.items.length ? <><div className="overflow-auto rounded-lg border bg-background"><Table><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>主体</TableHead><TableHead>谓词</TableHead><TableHead>客体</TableHead><TableHead>旧 group_id</TableHead><TableHead>类型</TableHead></TableRow></TableHeader><TableBody>{legacyData.items.map((fact) => <TableRow key={fact.id}><TableCell className="font-mono text-xs">#{fact.id}</TableCell><TableCell>{fact.subject}</TableCell><TableCell><Badge variant="outline">{fact.predicate}</Badge></TableCell><TableCell className="max-w-sm truncate">{fact.object}</TableCell><TableCell className="font-mono text-xs text-muted-foreground">{fact.group_id || '未记录'}</TableCell><TableCell>{fact.fact_type || '未记录'}</TableCell></TableRow>)}</TableBody></Table></div><div className="flex items-center justify-between text-sm text-muted-foreground"><span>第 {legacyOffset + 1}-{Math.min(legacyOffset + 25, (legacyData.page.total ?? 0))} 条</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={legacyLoading || legacyOffset === 0} onClick={() => setLegacyOffset(Math.max(0, legacyOffset - 25))}>上一页</Button><Button size="sm" variant="outline" disabled={legacyLoading || legacyOffset + 25 >= (legacyData.page.total ?? 0)} onClick={() => setLegacyOffset(legacyOffset + 25)}>下一页</Button></div></div></> : !legacyLoading ? <p className="text-xs text-muted-foreground py-4 text-center">没有未归属的 Legacy facts。</p> : null}
      </CardContent></Card>

    <Card className="border-dashed"><CardHeader><CardTitle className="text-sm">状态说明</CardTitle><CardDescription>“不可用证据”表示当前事实没有通过同作用域健康检查的来源记忆，不应理解为已验证事实。切换 Bot 或群会话会清空分页，避免把相同裸 ID 当作同一对象。</CardDescription></CardHeader></Card>
  </div>
}
