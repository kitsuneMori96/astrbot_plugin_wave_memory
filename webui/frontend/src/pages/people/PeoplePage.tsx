import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { AlertCircleIcon, EyeIcon, RefreshCwIcon, SearchIcon } from 'lucide-react'

import { getScopeOptions, scopeOptionsFor } from '@/api/options'
import { getLegacyRelationships, getPeople, type LegacyAuditPage, type LegacyRelationshipEvent, type PersonItem } from '@/api/people'
import { PaginationControls, QueryState, ResponsiveDetail, ScopeSelect, usePaginationSearchParams, type PageResponse } from '@/components/shared'
import { useCanonicalScopeDefault } from '@/hooks/use-pagination-search-params'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

function aliasLabels(aliases: unknown[]): string[] {
  return aliases.map((alias) => typeof alias === 'string' ? alias : '').filter(Boolean)
}

function interactionCount(item: PersonItem): number | null {
  if (typeof item.interaction_count === 'number' && Number.isFinite(item.interaction_count)) return item.interaction_count
  const registryCount = item.person_registry?.message_count
  return typeof registryCount === 'number' && Number.isFinite(registryCount) ? registryCount : null
}

function PersonDetail({ item }: { item: PersonItem }) {
  const aliases = aliasLabels(item.aliases)
  const metadataCount = Object.keys(item.metadata ?? {}).length + Object.keys(item.registry_metadata ?? {}).length
  return <div className="flex flex-col gap-4">
    <dl className="grid gap-3 sm:grid-cols-2">
      <div><dt className="text-sm font-medium text-muted-foreground">用户 ID</dt><dd className="break-all font-mono">{item.user_id}</dd></div>
      <div><dt className="text-sm font-medium text-muted-foreground">显示名称</dt><dd>{item.display_name}</dd></div>
      <div><dt className="text-sm font-medium text-muted-foreground">群 / Group ID</dt><dd className="break-all font-mono">{item.group_id}</dd></div>
      <div><dt className="text-sm font-medium text-muted-foreground">BotProfile.db_id</dt><dd className="break-all font-mono">{item.bot_id}</dd></div>
      <div><dt className="text-sm font-medium text-muted-foreground">互动数</dt><dd>{interactionCount(item) ?? '未记录'}</dd></div>
      <div><dt className="text-sm font-medium text-muted-foreground">Affinity</dt><dd><Badge variant="outline">不可用</Badge></dd></div>
    </dl>
    <div><h3 className="mb-2 text-sm font-medium text-muted-foreground">登记别名</h3><div className="flex flex-wrap gap-1">{aliases.length ? aliases.map((alias) => <Badge key={alias} variant="outline">{alias}</Badge>) : <span className="text-muted-foreground">未登记别名</span>}</div></div>
    <div className="rounded-md border bg-muted/10 p-3 text-sm"><p className="font-medium">为什么没有 Affinity 数值？</p><p className="mt-1 text-muted-foreground">{item.affinity_reason_code || '当前没有经过复合作用域验证的 affinity projection。'} 页面不会用旧 affection 字段或固定 50 代替真实值。</p></div>
    <div className="rounded-md border p-3 text-sm text-muted-foreground">服务端另有 {metadataCount} 项画像元数据。为避免在主界面直出内部结构，此处仅展示经过定义的身份、作用域、别名和互动字段。</div>
  </div>
}

function SummaryTile({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return <div className="min-w-[7rem] rounded-lg border bg-muted/20 px-3 py-2 text-center"><div className="text-xs text-muted-foreground">{label}</div><div className={`text-lg font-semibold ${tone ?? ''}`}>{value}</div></div>
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

  const submitSearch = (event: FormEvent) => { event.preventDefault(); pagination.setFilters({ search: searchDraft.trim() }) }
  const people = data?.items ?? []
  const aliasCount = people.reduce((sum, item) => sum + aliasLabels(item.aliases).length, 0)
  const interactionTotal = people.reduce((sum, item) => sum + (interactionCount(item) ?? 0), 0)
  const total = data?.page.total_status === 'exact' ? data.page.total ?? 0 : '—'
  const status = !botId || !sessionId ? 'unknown' : loading ? 'loading' : error ? 'error' : !people.length ? 'empty' : 'success'

  return <div className="flex flex-col gap-5" data-page="people">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <header className="max-w-2xl"><h1 className="text-xl font-bold tracking-tight">人物与关系画像</h1><p className="text-xs text-muted-foreground">按 user_id + group_id + bot_id 复合作用域查看人物身份、别名与互动信息；不同 Bot 或群中的同名用户不会合并。</p></header>
      <div className="flex flex-wrap gap-2"><SummaryTile label="筛选人物" value={total} /><SummaryTile label="本页登记别名" value={aliasCount} /><SummaryTile label="本页互动合计" value={interactionTotal} tone="text-blue-600" /><SummaryTile label="Affinity" value="不可用" tone="text-muted-foreground" /></div>
    </div>

    <Card className="border-border/60"><CardHeader><CardTitle className="text-sm">请求作用域与搜索</CardTitle><CardDescription>选项来自当前服务端授权范围。BotProfile.db_id 与 QQ 号是不同标识，不能互换。</CardDescription></CardHeader><CardContent className="grid gap-4 md:grid-cols-2">
      <ScopeSelect value={botId || undefined} loadOptions={loadBots} label="Bot" placeholder="选择真实 Bot" required onValueChange={(value) => pagination.setFilters({ bot_id: value, session_id: null })} />
      <ScopeSelect value={sessionId || undefined} loadOptions={loadSessions} label="群 / 会话" placeholder="选择该 Bot 的真实群会话" disabled={!botId} required onValueChange={(value) => pagination.setFilters({ session_id: value })} />
      <form className="flex gap-2 md:col-span-2" onSubmit={submitSearch}><div className="relative min-w-0 flex-1"><SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" /><Input aria-label="搜索人物" className="h-8 pl-8" value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder="搜索用户 ID、昵称或登记别名" disabled={!botId || !sessionId} /></div><Button type="submit" size="sm" disabled={loading || !botId || !sessionId}>搜索</Button><Button type="button" size="sm" variant="outline" disabled={loading || !botId || !sessionId} onClick={() => setReload((value) => value + 1)}><RefreshCwIcon aria-hidden="true" /><span className="sr-only">刷新</span></Button></form>
    </CardContent></Card>

    <Card className="border-border/60"><CardContent className="flex flex-col gap-4 p-4">
      <QueryState status={status} error={error} title="人物画像读取失败" description={!botId || !sessionId ? '请先选择真实 Bot 与 canonical 群会话；页面不会读取跨作用域人物。' : '当前作用域和搜索条件下没有人物画像。'} onRetry={() => setReload((value) => value + 1)}>
        <div className="overflow-hidden rounded-lg border"><Table><TableHeader><TableRow className="bg-muted/20"><TableHead>用户 ID</TableHead><TableHead>显示名称</TableHead><TableHead>登记别名</TableHead><TableHead>群</TableHead><TableHead>Bot</TableHead><TableHead className="w-24 text-center">互动数</TableHead><TableHead className="w-28">Affinity</TableHead><TableHead className="w-14"><span className="sr-only">详情</span></TableHead></TableRow></TableHeader><TableBody>{people.map((item) => { const aliases = aliasLabels(item.aliases); return <TableRow key={item.scope_key}><TableCell className="font-mono text-xs">{item.user_id}</TableCell><TableCell className="font-medium">{item.display_name}</TableCell><TableCell className="max-w-48 truncate text-muted-foreground">{aliases.length ? aliases.join('、') : '未登记'}</TableCell><TableCell className="max-w-40 truncate font-mono text-xs">{item.group_id}</TableCell><TableCell><Badge variant="secondary" className="font-mono text-xs">{item.bot_id}</Badge></TableCell><TableCell className="text-center font-mono text-xs">{interactionCount(item) ?? '—'}</TableCell><TableCell><Badge variant="outline">不可用</Badge></TableCell><TableCell className="text-right"><ResponsiveDetail title={item.display_name} description="复合作用域内的只读人物画像" trigger={<Button type="button" variant="ghost" size="icon-sm" aria-label={`查看 ${item.display_name} 详情`}><EyeIcon aria-hidden="true" /></Button>}><PersonDetail item={item} /></ResponsiveDetail></TableCell></TableRow> })}</TableBody></Table></div>
      </QueryState>
      {data?.page ? <PaginationControls page={data.page} onOffsetChange={pagination.setOffset} onLimitChange={pagination.setLimit} disabled={loading} label="人物分页" /> : null}
    </CardContent></Card>

    <Card className="border-amber-500/10 bg-amber-500/[0.02]">
      <CardHeader className="py-4">
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="border-amber-500/20 text-amber-600 bg-amber-500/5">只读审计</Badge>
          <CardTitle className="text-base">Legacy 关系事件</CardTitle>
        </div>
        <CardDescription>旧 relationship_events 没有 BotProfile.db_id 与 canonical session 列，因此无法安全归入上方人物关系。这里仅展示历史事件，不参与 Affinity 计算。</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 pt-0">
        <Alert className="mb-4 border-amber-500/15 bg-amber-500/[0.02] text-amber-700 dark:text-amber-500"><AlertCircleIcon className="size-4 text-amber-600" /><AlertTitle>关系归属未证实</AlertTitle><AlertDescription className="text-xs">{legacyRelations ? `共 ${(legacyRelations.page.total ?? 0).toLocaleString('zh-CN')} 条 Legacy 关系事件。` : legacyLoading ? '正在读取审计清单。' : 'Legacy 审计接口暂不可用。'} 旧 user_id / group_id 不能替代完整 RuntimeScope。</AlertDescription></Alert>{legacyRelations?.items.length ? <><div className="overflow-auto rounded-lg border bg-background"><Table><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>用户 ID</TableHead><TableHead>旧群 / 会话</TableHead><TableHead>事件类型</TableHead><TableHead>维度 / 变化</TableHead><TableHead>原因</TableHead></TableRow></TableHeader><TableBody>{legacyRelations.items.map((item) => <TableRow key={item.id}><TableCell className="font-mono text-xs">#{item.id}</TableCell><TableCell className="font-mono text-xs">{item.user_id || '未记录'}</TableCell><TableCell className="font-mono text-xs text-muted-foreground">{item.group_id || '未记录'}</TableCell><TableCell>{item.event_type || '未知'}</TableCell><TableCell>{item.dimension || '未记录'} / {item.delta ?? '—'}</TableCell><TableCell className="max-w-xl"><p className="line-clamp-2">{item.reason || '未记录'}</p></TableCell></TableRow>)}</TableBody></Table></div><div className="flex items-center justify-between text-sm text-muted-foreground"><span>第 {legacyOffset + 1}-{Math.min(legacyOffset + 25, (legacyRelations.page.total ?? 0))} 条</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={legacyLoading || legacyOffset === 0} onClick={() => setLegacyOffset(Math.max(0, legacyOffset - 25))}>上一页</Button><Button size="sm" variant="outline" disabled={legacyLoading || legacyOffset + 25 >= (legacyRelations.page.total ?? 0)} onClick={() => setLegacyOffset(legacyOffset + 25)}>下一页</Button></div></div></> : !legacyLoading ? <p className="text-xs text-muted-foreground py-4 text-center">没有未归属的 Legacy 关系事件。</p> : null}</CardContent></Card>

    <Card className="border-dashed"><CardHeader><CardTitle className="text-sm">Affinity 说明</CardTitle><CardDescription>当前服务端没有可验证的 scoped affinity projection，因此页面明确显示“不可用”。旧 affection、全局值或固定 50 都不能代表当前 Bot 与群会话中的真实关系。</CardDescription></CardHeader></Card>
  </div>
}
