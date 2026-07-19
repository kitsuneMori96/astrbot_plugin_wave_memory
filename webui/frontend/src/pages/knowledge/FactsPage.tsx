import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { EyeIcon, RefreshCwIcon, SearchIcon } from 'lucide-react'

import { getScopedFacts, type ScopedFact, type ScopedFactsPage } from '@/api/knowledge'
import { getScopeOptions, scopeOptionsFor } from '@/api/options'
import { EvidenceList, PaginationControls, QueryState, ResponsiveTable, ScopeSelect, usePaginationSearchParams } from '@/components/shared'
import { useCanonicalScopeDefault } from '@/hooks/use-pagination-search-params'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

function confidence(value: number | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '未知'
}

function SummaryTile({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return <div className="min-w-[4.75rem] rounded-lg border bg-muted/20 px-3 py-1.5 text-center text-xs"><div className="text-[10px] text-muted-foreground">{label}</div><div className={`font-semibold ${tone ?? ''}`}>{value}</div></div>
}

function FactDetail({ fact }: { fact: ScopedFact }) {
  return <div className="flex flex-col gap-5 text-sm">
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant="outline">Fact #{fact.id}</Badge>
      <Badge variant="secondary">{fact.status || '未知状态'}</Badge>
      <Badge variant={fact.evidence_status === 'available' ? 'default' : 'outline'}>{fact.evidence_status === 'available' ? '证据可溯源' : '证据不可用'}</Badge>
    </div>

    <div className="grid gap-3 rounded-lg border bg-muted/10 p-3.5">
      <div><span className="mb-0.5 block text-xs text-muted-foreground">主体 (Subject)</span><span className="break-words font-medium">{fact.subject}</span></div>
      <div><span className="mb-0.5 block text-xs text-muted-foreground">谓词 (Predicate)</span><Badge variant="outline" className="font-normal">{fact.predicate}</Badge></div>
      <div><span className="mb-0.5 block text-xs text-muted-foreground">客体 (Object)</span><span className="break-words font-medium">{fact.object}</span></div>
    </div>

    <dl className="grid grid-cols-2 gap-3 rounded-lg border p-3.5">
      <div><dt className="text-xs text-muted-foreground">置信度</dt><dd className="font-mono font-medium">{confidence(fact.confidence)}</dd></div>
      <div><dt className="text-xs text-muted-foreground">来源记忆</dt><dd className="font-mono">{fact.source_memory_id ? `#${fact.source_memory_id}` : '未关联'}</dd></div>
      <div className="col-span-2"><dt className="text-xs text-muted-foreground">Canonical Scope</dt><dd className="break-all font-mono text-xs">{fact.bot_id} · {fact.session_id} · group</dd></div>
    </dl>

    <div>
      <h3 className="mb-2 font-medium">同作用域证据</h3>
      <EvidenceList evidence={fact.evidence} objectPath="/memories" emptyDescription="没有可验证的同作用域记忆证据；这不代表事实为假，但无法从此页面完成溯源。" />
    </div>

    <details className="rounded-lg border bg-muted/10 p-3 text-xs text-muted-foreground">
      <summary className="cursor-pointer font-medium text-foreground">技术与安全边界</summary>
      <p className="mt-2 leading-relaxed">事实 ID 仅用于当前只读清单展示，不作为跨 Scope mutation 或对象跳转依据。证据入口只跟随服务端返回的 ObjectRef；页面不会跨 Bot、canonical session 或隔离状态补齐关系。</p>
    </details>
  </div>
}

export function FactsPage() {
  const pagination = usePaginationSearchParams()
  const botId = pagination.searchParams.get('bot_id') ?? ''
  const sessionId = pagination.searchParams.get('session_id') ?? ''
  const search = pagination.searchParams.get('search') ?? ''
  const statusFilter = pagination.searchParams.get('status') ?? ''
  useCanonicalScopeDefault({ botId, sessionId, setFilters: pagination.setFilters })

  const [filterDraft, setFilterDraft] = useState({ search, status: statusFilter })
  const [data, setData] = useState<ScopedFactsPage | null>(null)
  const [error, setError] = useState<unknown>()
  const [loading, setLoading] = useState(false)
  const [reload, setReload] = useState(0)
  const [selectedFact, setSelectedFact] = useState<ScopedFact | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)

  const loadBots = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['bot']), [])
  const loadSessions = useCallback(async () => {
    const options = scopeOptionsFor(await getScopeOptions(), ['session'])
    return botId ? options.filter((option) => option.description?.startsWith(`${botId} ·`)) : []
  }, [botId])

  useEffect(() => { setFilterDraft({ search, status: statusFilter }) }, [search, statusFilter])
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
  const submitSearch = (event: FormEvent) => {
    event.preventDefault()
    pagination.setFilters({ search: filterDraft.search.trim() || null, status: filterDraft.status || null })
  }
  const resetFilters = () => {
    setFilterDraft({ search: '', status: '' })
    pagination.setFilters({ search: null, status: null })
  }
  const openDetail = (fact: ScopedFact) => {
    setSelectedFact(fact)
    setDetailOpen(true)
  }

  const facts = data?.items ?? []
  const evidenceCount = data ? facts.filter((fact) => fact.evidence_status === 'available').length : '—'
  const lowConfidence = data ? facts.filter((fact) => typeof fact.confidence === 'number' && fact.confidence < 0.5).length : '—'
  const total = data?.page.total_status === 'exact' ? data.page.total ?? '—' : '—'
  const status = !botId || !sessionId ? 'unknown' : loading ? 'loading' : error ? 'error' : !facts.length ? 'empty' : 'success'

  return <div className="flex flex-col gap-4" data-page="facts">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <header className="max-w-2xl">
        <h1 className="text-xl font-bold tracking-tight">Facts 事实关系</h1>
        <p className="text-xs text-muted-foreground">按真实 Bot 与 canonical 群会话查看三元组；证据仅接受同一 group Scope 中健康、未隔离的记忆。</p>
      </header>
      <div className="flex flex-wrap gap-2">
        <SummaryTile label="筛选总数" value={loading ? '…' : total} />
        <SummaryTile label="本页有证据" value={loading ? '…' : evidenceCount} tone="text-emerald-600" />
        <SummaryTile label="本页低置信" value={loading ? '…' : lowConfidence} tone="text-amber-600" />
      </div>
    </div>

    <Card className="overflow-hidden border-border/60">
      <CardContent className="p-0">
        <div className="flex flex-col gap-3 bg-muted/[0.035] p-3">
          <div className="flex flex-wrap items-end gap-2" data-slot="facts-scope-context">
            <Badge variant="outline" className="mb-0.5 h-7">Canonical Scope</Badge>
            <ScopeSelect className="min-w-48 flex-1 xl:max-w-64" value={botId || undefined} loadOptions={loadBots} label="Bot" placeholder="选择真实 Bot" required onValueChange={(value) => pagination.setFilters({ bot_id: value, session_id: null })} />
            <ScopeSelect className="min-w-56 flex-[1.4] xl:max-w-80" value={sessionId || undefined} loadOptions={loadSessions} label="群 / 会话" placeholder="选择该 Bot 的 canonical 群会话" disabled={!botId} required onValueChange={(value) => pagination.setFilters({ session_id: value })} />
            <span className="pb-1 text-[10px] text-muted-foreground">BotProfile.db_id ≠ QQ 号 · visibility: group</span>
          </div>

          <form className="flex flex-wrap items-center gap-2" onSubmit={submitSearch}>
            <div className="relative min-w-60 flex-1 xl:max-w-xl">
              <SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input aria-label="搜索 Facts" className="h-8 pl-8 text-xs" value={filterDraft.search} onChange={(event) => setFilterDraft((current) => ({ ...current, search: event.target.value }))} placeholder="搜索 subject / predicate / object" disabled={!botId || !sessionId} />
            </div>
            <select aria-label="事实状态" className="h-8 rounded-lg border bg-background px-2 text-xs" value={filterDraft.status} onChange={(event) => setFilterDraft((current) => ({ ...current, status: event.target.value }))} disabled={!botId || !sessionId}>
              <option value="">全部状态</option>
              <option value="active">active</option>
              <option value="confirmed">confirmed</option>
              <option value="pending">pending</option>
              <option value="rejected">rejected</option>
            </select>
            <Button type="submit" size="sm" className="h-8" disabled={loading || !botId || !sessionId}>搜索</Button>
            <Button type="button" size="sm" className="h-8" variant="ghost" onClick={resetFilters}>清除</Button>
            <Button type="button" size="icon-sm" className="h-8 w-8" variant="outline" disabled={loading || !botId || !sessionId} onClick={() => setReload((value) => value + 1)} aria-label="刷新 Facts">
              <RefreshCwIcon className={loading ? 'animate-spin' : undefined} aria-hidden="true" />
            </Button>
            <span className="ml-auto text-xs text-muted-foreground">{data?.page ? `当前第 ${Math.floor(pagination.offset / pagination.limit) + 1} 页` : '请选择完整 Scope'}</span>
          </form>
        </div>

        <Separator />

        <div className="flex flex-col gap-3 p-3">
          <QueryState status={status} error={error} title="Facts 读取失败" description={!botId || !sessionId ? '请从服务端真实选项中选择 Bot 与 canonical 群会话；页面不会跨 Bot 汇总。' : '当前 Scope 与筛选条件下没有正式 Facts。'} onRetry={() => setReload((value) => value + 1)}>
            <ResponsiveTable
              label="Facts 关系清单"
              table={<Table>
                <TableHeader><TableRow className="h-8 bg-muted/15"><TableHead className="w-16 py-1 text-[11px]">ID</TableHead><TableHead className="py-1 text-[11px]">主体</TableHead><TableHead className="py-1 text-[11px]">谓词</TableHead><TableHead className="py-1 text-[11px]">客体</TableHead><TableHead className="w-20 py-1 text-[11px]">置信度</TableHead><TableHead className="w-24 py-1 text-[11px]">证据</TableHead><TableHead className="w-12 py-1"><span className="sr-only">详情</span></TableHead></TableRow></TableHeader>
                <TableBody>{facts.map((fact) => <TableRow key={`${fact.bot_id}:${fact.session_id}:${fact.id}`} className="h-9 cursor-pointer hover:bg-muted/10" onClick={() => openDetail(fact)}>
                  <TableCell className="py-1 font-mono text-[11px]">{fact.id}</TableCell>
                  <TableCell className="max-w-52 truncate py-1 text-xs font-medium">{fact.subject}</TableCell>
                  <TableCell className="py-1"><Badge variant="outline" className="px-1.5 text-[10px] font-normal">{fact.predicate}</Badge></TableCell>
                  <TableCell className="max-w-sm truncate py-1 text-xs">{fact.object}</TableCell>
                  <TableCell className="py-1 font-mono text-[11px]">{confidence(fact.confidence)}</TableCell>
                  <TableCell className="py-1"><Badge variant={fact.evidence_status === 'available' ? 'default' : 'outline'} className="text-[10px]">{fact.evidence_status === 'available' ? '可溯源' : '不可用'}</Badge></TableCell>
                  <TableCell className="py-1 text-right" onClick={(event) => event.stopPropagation()}><Button type="button" variant="ghost" size="icon-xs" aria-label={`查看 Fact ${fact.id} 详情`} onClick={() => openDetail(fact)}><EyeIcon aria-hidden="true" /></Button></TableCell>
                </TableRow>)}</TableBody>
              </Table>}
              cards={facts.map((fact) => <article key={`${fact.bot_id}:${fact.session_id}:${fact.id}`} className="flex flex-col gap-3 rounded-lg border bg-card p-4">
                <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-mono text-xs text-muted-foreground">Fact #{fact.id}</span><Badge variant={fact.evidence_status === 'available' ? 'default' : 'outline'}>{fact.evidence_status === 'available' ? '可溯源' : '不可用'}</Badge></div>
                <dl className="grid gap-2 text-sm"><div><dt className="text-muted-foreground">主体</dt><dd className="break-words font-medium">{fact.subject}</dd></div><div><dt className="text-muted-foreground">谓词</dt><dd><Badge variant="outline">{fact.predicate}</Badge></dd></div><div><dt className="text-muted-foreground">客体</dt><dd className="break-words">{fact.object}</dd></div></dl>
                <div className="flex items-center justify-between gap-3"><span className="font-mono text-xs text-muted-foreground">置信度 {confidence(fact.confidence)}</span><Button type="button" variant="outline" size="sm" onClick={() => openDetail(fact)}>查看详情</Button></div>
              </article>)}
            />
          </QueryState>
          {data?.page ? <PaginationControls page={data.page} onOffsetChange={pagination.setOffset} onLimitChange={pagination.setLimit} disabled={loading} label="Facts 分页" /> : null}
        </div>
      </CardContent>
    </Card>


    <Sheet open={detailOpen} onOpenChange={setDetailOpen}>
      <SheetContent className="w-[min(94vw,34rem)] sm:max-w-xl">
        <SheetHeader className="border-b pr-12"><SheetTitle>Facts 关系详情</SheetTitle><SheetDescription>只读查看正式三元组、canonical Scope 与服务端证据引用。</SheetDescription></SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">{selectedFact ? <FactDetail fact={selectedFact} /> : null}</div>
      </SheetContent>
    </Sheet>
  </div>
}
