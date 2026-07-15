import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { AlertCircleIcon, ChevronDownIcon, EyeIcon, RefreshCwIcon, SearchIcon } from 'lucide-react'

import { getScopeOptions, scopeOptionsFor } from '@/api/options'
import { getLegacyRelationships, getPeople, type LegacyAuditPage, type LegacyRelationshipEvent, type PersonItem } from '@/api/people'
import { PaginationControls, QueryState, ResponsiveTable, ScopeSelect, usePaginationSearchParams, type PageResponse } from '@/components/shared'
import { useCanonicalScopeDefault } from '@/hooks/use-pagination-search-params'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

function aliasLabels(aliases: unknown[]): string[] {
  return aliases.map((alias) => typeof alias === 'string' ? alias : '').filter(Boolean)
}

function interactionCount(item: PersonItem): number | null {
  if (typeof item.interaction_count === 'number' && Number.isFinite(item.interaction_count)) return item.interaction_count
  const registryCount = item.person_registry?.message_count
  return typeof registryCount === 'number' && Number.isFinite(registryCount) ? registryCount : null
}

function SummaryTile({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return <div className="min-w-[4.75rem] rounded-lg border bg-muted/20 px-3 py-1.5 text-center text-xs"><div className="text-[10px] text-muted-foreground">{label}</div><div className={`font-semibold ${tone ?? ''}`}>{value}</div></div>
}

function PersonDetail({ item }: { item: PersonItem }) {
  const aliases = aliasLabels(item.aliases)
  const metadataCount = Object.keys(item.metadata ?? {}).length + Object.keys(item.registry_metadata ?? {}).length

  return <div className="flex flex-col gap-5 text-sm">
    <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">正式人物画像</Badge><Badge variant="secondary">group Scope</Badge><Badge variant="outline">Affinity 不可用</Badge></div>

    <div className="grid gap-3 rounded-lg border bg-muted/10 p-3.5">
      <div><span className="mb-0.5 block text-xs text-muted-foreground">显示名称</span><span className="break-words font-medium">{item.display_name}</span></div>
      <div><span className="mb-0.5 block text-xs text-muted-foreground">用户 ID</span><span className="break-all font-mono text-xs">{item.user_id}</span></div>
      <div><span className="mb-0.5 block text-xs text-muted-foreground">Canonical Scope</span><span className="break-all font-mono text-xs">{item.bot_id} · {item.group_id} · group</span></div>
    </div>

    <div className="grid grid-cols-2 gap-3 rounded-lg border p-3.5">
      <div><span className="mb-0.5 block text-xs text-muted-foreground">互动数</span><span className="font-mono font-medium">{interactionCount(item) ?? '未记录'}</span></div>
      <div><span className="mb-0.5 block text-xs text-muted-foreground">Affinity</span><Badge variant="outline">不可用</Badge></div>
      <div className="col-span-2"><span className="mb-1.5 block text-xs text-muted-foreground">登记别名</span><div className="flex flex-wrap gap-1">{aliases.length ? aliases.map((alias) => <Badge key={alias} variant="outline" className="font-normal">{alias}</Badge>) : <span className="text-muted-foreground">未登记别名</span>}</div></div>
    </div>

    <Alert>
      <AlertCircleIcon />
      <AlertTitle>为什么没有 Affinity 数值？</AlertTitle>
      <AlertDescription>{item.affinity_reason_code || '当前没有经过复合作用域验证的 affinity projection。'} 页面不会使用旧 affection、跨群全局值或固定回填值伪装当前关系。</AlertDescription>
    </Alert>

    <details className="rounded-lg border bg-muted/10 p-3 text-xs text-muted-foreground">
      <summary className="cursor-pointer font-medium text-foreground">技术字段与安全边界</summary>
      <div className="mt-3 grid gap-2">
        <p>复合键：<span className="break-all font-mono">user_id + group_id + bot_id</span>。列表键为当前响应的 scope_key，不用于裸 ID mutation。</p>
        <p>服务端另返回 {metadataCount} 项画像元数据；主界面不直出内部 JSON，也不会将不同 Bot 或群中的同名用户合并。</p>
      </div>
    </details>
  </div>
}

export function PeoplePage() {
  const pagination = usePaginationSearchParams()
  const botId = pagination.searchParams.get('bot_id') ?? ''
  const sessionId = pagination.searchParams.get('session_id') ?? ''
  const search = pagination.searchParams.get('search') ?? ''
  useCanonicalScopeDefault({ botId, sessionId, setFilters: pagination.setFilters })

  const [searchDraft, setSearchDraft] = useState(search)
  const [data, setData] = useState<PageResponse<PersonItem> | null>(null)
  const [error, setError] = useState<unknown>()
  const [loading, setLoading] = useState(false)
  const [reload, setReload] = useState(0)
  const [selectedPerson, setSelectedPerson] = useState<PersonItem | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [legacyRelations, setLegacyRelations] = useState<LegacyAuditPage<LegacyRelationshipEvent> | null>(null)
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
    getPeople({ bot_id: botId, session_id: sessionId, visibility: 'group', search: search || undefined, limit: pagination.limit, offset: pagination.offset })
      .then((value) => { if (active) setData(value) })
      .catch((reason: unknown) => { if (active) { setData(null); setError(reason) } })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [botId, pagination.limit, pagination.offset, reload, search, sessionId])
  useEffect(() => {
    let active = true
    setLegacyLoading(true)
    getLegacyRelationships({ search: search || undefined, limit: 25, offset: legacyOffset })
      .then((value) => { if (active) setLegacyRelations(value) })
      .catch(() => { if (active) setLegacyRelations(null) })
      .finally(() => { if (active) setLegacyLoading(false) })
    return () => { active = false }
  }, [legacyOffset, search])

  const submitSearch = (event: FormEvent) => {
    event.preventDefault()
    setLegacyOffset(0)
    pagination.setFilters({ search: searchDraft.trim() || null })
  }
  const clearSearch = () => {
    setLegacyOffset(0)
    setSearchDraft('')
    pagination.setFilters({ search: null })
  }
  const openDetail = (item: PersonItem) => {
    setSelectedPerson(item)
    setDetailOpen(true)
  }

  const people = data?.items ?? []
  const aliasCount = data ? people.reduce((sum, item) => sum + aliasLabels(item.aliases).length, 0) : '—'
  const interactionTotal = data && people.every((item) => interactionCount(item) !== null) ? people.reduce((sum, item) => sum + interactionCount(item)!, 0) : '未提供'
  const total = data?.page.total_status === 'exact' ? data.page.total ?? '—' : '—'
  const legacyTotal = legacyRelations?.page.total ?? null
  const legacyRangeEnd = legacyRelations ? legacyTotal === null ? legacyOffset + legacyRelations.items.length : Math.min(legacyOffset + 25, legacyTotal) : legacyOffset
  const status = !botId || !sessionId ? 'unknown' : loading ? 'loading' : error ? 'error' : !people.length ? 'empty' : 'success'

  return <div className="flex flex-col gap-4" data-page="people">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <header className="max-w-2xl">
        <h1 className="text-xl font-bold tracking-tight">人物与关系画像</h1>
        <p className="text-xs text-muted-foreground">按 user_id + group_id + bot_id 复合作用域查看身份、别名与互动；同名用户不会跨 Bot 或群合并。</p>
      </header>
      <div className="flex flex-wrap gap-2">
        <SummaryTile label="筛选人物" value={loading ? '…' : total} />
        <SummaryTile label="本页别名" value={loading ? '…' : aliasCount} tone="text-pink-600" />
        <SummaryTile label="本页互动" value={loading ? '…' : interactionTotal} tone="text-blue-600" />
        <SummaryTile label="Affinity" value="不可用" tone="text-muted-foreground" />
      </div>
    </div>

    <Card className="overflow-hidden border-border/60">
      <CardContent className="p-0">
        <div className="flex flex-col gap-3 bg-muted/[0.035] p-3">
          <div className="flex flex-wrap items-end gap-2" data-slot="people-scope-context">
            <Badge variant="outline" className="mb-0.5 h-7">Canonical Scope</Badge>
            <ScopeSelect className="min-w-48 flex-1 xl:max-w-64" value={botId || undefined} loadOptions={loadBots} label="Bot" placeholder="选择真实 Bot" required onValueChange={(value) => pagination.setFilters({ bot_id: value, session_id: null })} />
            <ScopeSelect className="min-w-56 flex-[1.4] xl:max-w-80" value={sessionId || undefined} loadOptions={loadSessions} label="群 / 会话" placeholder="选择该 Bot 的 canonical 群会话" disabled={!botId} required onValueChange={(value) => pagination.setFilters({ session_id: value })} />
            <span className="pb-1 text-[10px] text-muted-foreground">BotProfile.db_id ≠ QQ 号 · visibility: group</span>
          </div>

          <form className="flex flex-wrap items-center gap-2" onSubmit={submitSearch}>
            <div className="relative min-w-60 flex-1 xl:max-w-xl">
              <SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input aria-label="搜索人物" className="h-8 pl-8 text-xs" value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder="搜索用户 ID、昵称或登记别名" disabled={!botId || !sessionId} />
            </div>
            <Button type="submit" size="sm" className="h-8" disabled={loading || !botId || !sessionId}>搜索</Button>
            <Button type="button" size="sm" className="h-8" variant="ghost" onClick={clearSearch}>清除</Button>
            <Button type="button" size="icon-sm" className="h-8 w-8" variant="outline" disabled={loading || !botId || !sessionId} onClick={() => setReload((value) => value + 1)} aria-label="刷新人物画像">
              <RefreshCwIcon className={loading ? 'animate-spin' : undefined} aria-hidden="true" />
            </Button>
            <span className="ml-auto text-xs text-muted-foreground">{data?.page ? `当前第 ${Math.floor(pagination.offset / pagination.limit) + 1} 页` : '请选择完整 Scope'}</span>
          </form>
        </div>

        <Separator />

        <div className="flex flex-col gap-3 p-3">
          <QueryState status={status} error={error} title="人物画像读取失败" description={!botId || !sessionId ? '请先选择真实 Bot 与 canonical 群会话；页面不会读取跨作用域人物。' : '当前 Scope 与搜索条件下没有正式人物画像。'} onRetry={() => setReload((value) => value + 1)}>
            <ResponsiveTable
              label="人物画像清单"
              table={<Table>
                <TableHeader><TableRow className="h-8 bg-muted/15"><TableHead className="py-1 text-[11px]">用户 ID</TableHead><TableHead className="py-1 text-[11px]">显示名称</TableHead><TableHead className="py-1 text-[11px]">登记别名</TableHead><TableHead className="w-36 py-1 text-[11px]">群</TableHead><TableHead className="w-32 py-1 text-[11px]">Bot</TableHead><TableHead className="w-20 py-1 text-center text-[11px]">互动数</TableHead><TableHead className="w-24 py-1 text-[11px]">Affinity</TableHead><TableHead className="w-12 py-1"><span className="sr-only">详情</span></TableHead></TableRow></TableHeader>
                <TableBody>{people.map((item) => {
                  const aliases = aliasLabels(item.aliases)
                  return <TableRow key={item.scope_key} className="h-9 cursor-pointer hover:bg-muted/10" onClick={() => openDetail(item)}>
                    <TableCell className="max-w-44 truncate py-1 font-mono text-[11px]">{item.user_id}</TableCell>
                    <TableCell className="max-w-44 truncate py-1 text-xs font-medium">{item.display_name}</TableCell>
                    <TableCell className="max-w-48 truncate py-1 text-xs text-muted-foreground">{aliases.length ? aliases.join('、') : '未登记'}</TableCell>
                    <TableCell className="max-w-36 truncate py-1 font-mono text-[11px]">{item.group_id}</TableCell>
                    <TableCell className="max-w-32 truncate py-1"><Badge variant="secondary" className="max-w-full truncate px-1.5 font-mono text-[10px] font-normal">{item.bot_id}</Badge></TableCell>
                    <TableCell className="py-1 text-center font-mono text-[11px]">{interactionCount(item) ?? '—'}</TableCell>
                    <TableCell className="py-1"><Badge variant="outline" className="text-[10px]">不可用</Badge></TableCell>
                    <TableCell className="py-1 text-right" onClick={(event) => event.stopPropagation()}><Button type="button" variant="ghost" size="icon-xs" aria-label={`查看 ${item.display_name} 详情`} onClick={() => openDetail(item)}><EyeIcon aria-hidden="true" /></Button></TableCell>
                  </TableRow>
                })}</TableBody>
              </Table>}
              cards={people.map((item) => {
                const aliases = aliasLabels(item.aliases)
                return <article key={item.scope_key} className="flex flex-col gap-3 rounded-lg border bg-card p-4">
                  <div className="flex items-start justify-between gap-2"><div><p className="font-medium">{item.display_name}</p><p className="break-all font-mono text-xs text-muted-foreground">{item.user_id}</p></div><Badge variant="outline">Affinity 不可用</Badge></div>
                  <div className="flex flex-wrap gap-1">{aliases.length ? aliases.map((alias) => <Badge key={alias} variant="outline">{alias}</Badge>) : <span className="text-xs text-muted-foreground">未登记别名</span>}</div>
                  <dl className="grid gap-2 text-xs sm:grid-cols-2"><div><dt className="text-muted-foreground">群</dt><dd className="break-all font-mono">{item.group_id}</dd></div><div><dt className="text-muted-foreground">Bot</dt><dd className="break-all font-mono">{item.bot_id}</dd></div><div><dt className="text-muted-foreground">互动数</dt><dd>{interactionCount(item) ?? '—'}</dd></div></dl>
                  <Button type="button" className="w-fit" variant="outline" size="sm" onClick={() => openDetail(item)}>查看详情</Button>
                </article>
              })}
            />
          </QueryState>
          {data?.page ? <PaginationControls page={data.page} onOffsetChange={pagination.setOffset} onLimitChange={pagination.setLimit} disabled={loading} label="人物分页" /> : null}
        </div>
      </CardContent>
    </Card>

    <details className="group overflow-hidden rounded-lg border border-amber-500/15 bg-amber-500/[0.02]">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 marker:content-none">
        <div className="flex min-w-0 items-center gap-2"><Badge variant="outline" className="border-amber-500/20 bg-amber-500/5 text-amber-600">只读审计</Badge><span className="font-medium">Legacy 关系事件</span><span className="hidden truncate text-xs text-muted-foreground sm:inline">归属未证实，不参与正式画像或 Affinity</span></div>
        <ChevronDownIcon className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" />
      </summary>
      <div className="border-t p-4">
        <Alert className="mb-4 border-amber-500/15 bg-amber-500/[0.02] text-amber-700 dark:text-amber-500"><AlertCircleIcon className="size-4 text-amber-600" /><AlertTitle>关系归属未证实</AlertTitle><AlertDescription className="text-xs">{legacyRelations ? legacyTotal === null ? 'Legacy 关系事件总数未提供。' : `共 ${legacyTotal.toLocaleString('zh-CN')} 条 Legacy 关系事件。` : legacyLoading ? '正在读取审计清单。' : 'Legacy 审计接口暂不可用。'} 旧 relationship_events 没有 BotProfile.db_id 与 canonical session 列；旧 user_id / group_id 不能替代完整 RuntimeScope，也不会参与 Affinity 计算。</AlertDescription></Alert>
        {legacyRelations?.items.length ? <>
          <div className="overflow-auto rounded-lg border bg-background"><Table><TableHeader><TableRow className="h-8"><TableHead className="py-1 text-[11px]">ID</TableHead><TableHead className="py-1 text-[11px]">用户 ID</TableHead><TableHead className="py-1 text-[11px]">旧群 / 会话</TableHead><TableHead className="py-1 text-[11px]">事件类型</TableHead><TableHead className="py-1 text-[11px]">维度 / 变化</TableHead><TableHead className="py-1 text-[11px]">原因</TableHead></TableRow></TableHeader><TableBody>{legacyRelations.items.map((item) => <TableRow key={item.id} className="h-9"><TableCell className="py-1 font-mono text-[11px]">#{item.id}</TableCell><TableCell className="py-1 font-mono text-[11px]">{item.user_id || '未记录'}</TableCell><TableCell className="py-1 font-mono text-[11px] text-muted-foreground">{item.group_id || '未记录'}</TableCell><TableCell className="py-1 text-xs">{item.event_type || '未知'}</TableCell><TableCell className="py-1 text-xs">{item.dimension || '未记录'} / {item.delta ?? '—'}</TableCell><TableCell className="max-w-xl py-1 text-xs"><p className="line-clamp-2">{item.reason || '未记录'}</p></TableCell></TableRow>)}</TableBody></Table></div>
          <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground"><span>{legacyTotal === null ? `已读取第 ${legacyOffset + 1}-${legacyRangeEnd} 条；总数未提供` : `第 ${legacyOffset + 1}-${legacyRangeEnd} 条`}</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={legacyLoading || legacyOffset === 0} onClick={() => setLegacyOffset(Math.max(0, legacyOffset - 25))}>上一页</Button><Button size="sm" variant="outline" disabled={legacyLoading || !legacyRelations.page.has_more} onClick={() => setLegacyOffset(legacyOffset + 25)}>下一页</Button></div></div>
        </> : !legacyLoading ? <p className="py-4 text-center text-xs text-muted-foreground">没有未归属的 Legacy 关系事件。</p> : null}
      </div>
    </details>

    <Sheet open={detailOpen} onOpenChange={setDetailOpen}>
      <SheetContent className="w-[min(94vw,34rem)] sm:max-w-xl">
        <SheetHeader className="border-b pr-12"><SheetTitle>人物画像详情</SheetTitle><SheetDescription>只读查看复合作用域内的身份、别名、互动与 Affinity 可用性。</SheetDescription></SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">{selectedPerson ? <PersonDetail item={selectedPerson} /> : null}</div>
      </SheetContent>
    </Sheet>
  </div>
}
