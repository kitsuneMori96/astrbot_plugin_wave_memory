import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { EyeIcon, RefreshCwIcon, SearchIcon } from 'lucide-react'

import {
  getBookLoreItems,
  getBookLoreSummary,
  getReviewedBookLore,
  type BookLoreItem,
  type BookLorePage,
  type BookLoreResource,
  type BookLoreSummary,
  type ReviewedBookLorePage,
  type ReviewedBookLoreProjection,
} from '@/api/knowledge'
import { getScopeOptions, scopeOptionsFor } from '@/api/options'
import { EvidenceList, PaginationControls, QueryState, ResponsiveDetail, ScopeSelect } from '@/components/shared'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useCanonicalScopeDefault, usePaginationSearchParams } from '@/hooks/use-pagination-search-params'

const RESOURCES: Array<{ value: BookLoreResource; label: string; help: string }> = [
  { value: 'entities', label: '实体', help: '人物、地点与设定对象' },
  { value: 'communities', label: '社区', help: '聚合后的世界观主题' },
  { value: 'relations', label: '关系', help: '实体之间的语义关系' },
  { value: 'notes', label: '笔记', help: '导入来源中的原始知识片段' },
]

function displayText(value: unknown, fallback = '未记录'): string {
  if (value === undefined || value === null || value === '') return fallback
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'bigint') return String(value)
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.length ? value.map((item) => displayText(item, '')).filter(Boolean).join('、') : fallback
  if (typeof value === 'object') return `结构化内容（${Object.keys(value).length} 项）`
  return String(value)
}

function itemTitle(item: BookLoreItem): string {
  return displayText(item.title ?? item.name ?? item.source_title ?? item.id)
}

function itemSummary(item: BookLoreItem): string {
  return displayText(item.summary ?? item.description ?? item.content ?? item.original, '无摘要')
}

function governance(item: BookLoreItem) {
  const quarantined = item.quarantine === true || item.quarantine === 1
  return (
    <div className="flex flex-wrap gap-1">
      <Badge variant="outline">{displayText(item.resolution, '解析状态未知')}</Badge>
      <Badge variant={quarantined ? 'destructive' : 'secondary'}>
        {item.quarantine === null || item.quarantine === undefined ? '隔离状态未知' : quarantined ? '已隔离' : '可用'}
      </Badge>
    </div>
  )
}

function ReadableValue({ value }: { value: unknown }): ReactNode {
  if (value === undefined || value === null || value === '') return <span className="text-muted-foreground">未记录</span>
  if (Array.isArray(value)) {
    return value.length ? <div className="flex flex-wrap gap-1">{value.map((item, index) => <Badge key={index} variant="outline">{displayText(item)}</Badge>)}</div> : <span className="text-muted-foreground">未记录</span>
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value).filter(([key]) => !/(path|file|directory)/i.test(key)).slice(0, 20)
    return entries.length ? (
      <dl className="grid gap-2 rounded-md border p-3 sm:grid-cols-2">
        {entries.map(([key, nested]) => <div key={key}><dt className="text-xs text-muted-foreground">{key}</dt><dd className="break-words">{displayText(nested)}</dd></div>)}
      </dl>
    ) : <span className="text-muted-foreground">无可展示字段</span>
  }
  return <span className="whitespace-pre-wrap break-words">{displayText(value)}</span>
}

function ItemDetail({ item, resource }: { item: BookLoreItem; resource: BookLoreResource }) {
  const relationFields = resource === 'relations'
    ? [['起始实体', item.source], ['关系', item.relation], ['目标实体', item.target]] as const
    : []
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">{governance(item)}<Badge variant="outline">{resource}</Badge></div>
      <dl className="grid gap-3 sm:grid-cols-2">
        <div><dt className="text-sm font-medium text-muted-foreground">稳定 ID</dt><dd className="break-all font-mono">{String(item.id)}</dd></div>
        <div><dt className="text-sm font-medium text-muted-foreground">标题</dt><dd>{itemTitle(item)}</dd></div>
        {relationFields.map(([label, value]) => <div key={label}><dt className="text-sm font-medium text-muted-foreground">{label}</dt><dd><ReadableValue value={value} /></dd></div>)}
      </dl>
      <div><h3 className="mb-1 text-sm font-medium text-muted-foreground">原文 / 主要内容</h3><div className="rounded-md border p-3"><ReadableValue value={item.original ?? item.content ?? item.summary ?? item.description} /></div></div>
      <div><h3 className="mb-1 text-sm font-medium text-muted-foreground">本地化内容</h3><div className="rounded-md border p-3"><ReadableValue value={item.localized} /></div></div>
      <div><h3 className="mb-1 text-sm font-medium text-muted-foreground">翻译内容</h3><div className="rounded-md border p-3"><ReadableValue value={item.translation} /></div></div>
    </div>
  )
}

function ReviewedProjectionDetail({ item }: { item: ReviewedBookLoreProjection }) {
  return <div className="flex flex-col gap-4"><div className="flex flex-wrap gap-2"><Badge>approved</Badge><Badge variant="outline">revision {item.revision}</Badge><Badge variant="secondary">rank {item.rank.toFixed(2)}</Badge></div><div><h3 className="mb-1 text-sm font-medium text-muted-foreground">正式投影内容</h3><p className="whitespace-pre-wrap rounded-md border p-3 leading-relaxed">{item.content}</p></div><dl className="grid gap-3 sm:grid-cols-2"><div><dt className="text-sm text-muted-foreground">Community</dt><dd className="font-mono">{item.community_id}</dd></div><div><dt className="text-sm text-muted-foreground">来源 Catalog</dt><dd className="font-mono">{item.source_scope.catalog_id} · {item.source_scope.corpus_id} · {item.source_scope.version}</dd></div><div className="sm:col-span-2"><dt className="text-sm text-muted-foreground">目标 RuntimeScope</dt><dd className="break-all font-mono">{item.target_scope.bot_id} · {item.target_scope.session.id}</dd></div></dl><div><h3 className="mb-2 text-sm font-medium">审核证据</h3><EvidenceList evidence={item.evidence} emptyDescription="正式投影未返回可展示的证据引用。" /></div></div>
}

function SummaryTile({ label, value, help }: { label: string; value: number | undefined; help: string }) {
  return <div className="min-w-[7rem] rounded-lg border bg-muted/20 px-3 py-2 text-center"><div className="text-xs text-muted-foreground">{label}</div><div className="text-lg font-semibold">{value ?? '—'}</div><div className="sr-only">{help}</div></div>
}

export function BookLorePage() {
  const pagination = usePaginationSearchParams()
  const requestedTab = pagination.searchParams.get('tab') as BookLoreResource | null
  const resource = RESOURCES.some((item) => item.value === requestedTab) ? requestedTab! : 'entities'
  const search = pagination.searchParams.get('search') ?? ''
  const botId = pagination.searchParams.get('bot_id') ?? ''
  const sessionId = pagination.searchParams.get('session_id') ?? ''
  useCanonicalScopeDefault({ botId, sessionId, setFilters: pagination.setFilters })
  const [searchDraft, setSearchDraft] = useState(search)
  const [summary, setSummary] = useState<BookLoreSummary | null>(null)
  const [payload, setPayload] = useState<BookLorePage | null>(null)
  const [summaryError, setSummaryError] = useState<unknown>()
  const [error, setError] = useState<unknown>()
  const [loading, setLoading] = useState(true)
  const [reload, setReload] = useState(0)
  const [projectionPayload, setProjectionPayload] = useState<ReviewedBookLorePage | null>(null)
  const [projectionError, setProjectionError] = useState<unknown>()
  const [projectionLoading, setProjectionLoading] = useState(false)
  const [projectionOffset, setProjectionOffset] = useState(0)
  const loadBots = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['bot']), [])
  const loadSessions = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['session']).filter((option) => option.description?.startsWith(`${botId} ·`)), [botId])

  useEffect(() => { setSearchDraft(search) }, [search])

  useEffect(() => {
    if (!botId || !sessionId) { setProjectionPayload(null); setProjectionLoading(false); setProjectionError(undefined); return }
    let active = true
    setProjectionLoading(true)
    setProjectionError(undefined)
    getReviewedBookLore({ bot_id: botId, session_id: sessionId, visibility: 'group', search: search || undefined, limit: 25, offset: projectionOffset })
      .then((value) => { if (active) setProjectionPayload(value) })
      .catch((reason: unknown) => { if (active) { setProjectionPayload(null); setProjectionError(reason) } })
      .finally(() => { if (active) setProjectionLoading(false) })
    return () => { active = false }
  }, [botId, projectionOffset, reload, search, sessionId])

  useEffect(() => {
    let active = true
    setSummary(null)
    setSummaryError(undefined)
    getBookLoreSummary()
      .then((value) => {
        if (!value?.scope || !value?.counts) throw new Error('BookLore 摘要缺少 catalog scope 或分类统计。')
        if (active) setSummary(value)
      })
      .catch((reason: unknown) => { if (active) setSummaryError(reason) })
    return () => { active = false }
  }, [reload])

  useEffect(() => {
    if (!summary) { setPayload(null); setLoading(Boolean(!summaryError)); return }
    let active = true
    setLoading(true)
    setError(undefined)
    getBookLoreItems(resource, { ...summary.scope, limit: pagination.limit, offset: pagination.offset, search })
      .then((value) => { if (active) setPayload(value) })
      .catch((reason: unknown) => { if (active) { setPayload(null); setError(reason) } })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [pagination.limit, pagination.offset, resource, search, summary, summaryError])

  const submitSearch = (event: FormEvent) => {
    event.preventDefault()
    setProjectionOffset(0)
    pagination.setFilters({ search: searchDraft.trim() })
  }
  const status = !summary && summaryError ? 'error' : loading ? 'loading' : error ? 'error' : !payload?.items.length ? 'empty' : 'success'

  return (
    <div className="flex flex-col gap-5" data-page="book-lore">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <header className="max-w-2xl">
          <h1 className="text-xl font-bold tracking-tight">BookLore 世界观知识库</h1>
          <p className="text-xs text-muted-foreground">只读查看独立知识源中的实体、社区、关系和笔记；解析与隔离状态来自服务端，不在这里修改数据。</p>
        </header>
        <div className="flex flex-wrap gap-2">
          {RESOURCES.map((item) => <SummaryTile key={item.value} label={item.label} value={summary?.counts[item.value]} help={item.help} />)}
        </div>
      </div>

      <Card className="border-primary/20 bg-primary/5"><CardHeader><CardTitle className="text-sm">当前群可用的 Reviewed BookLore</CardTitle><CardDescription>这里只展示已审核并明确投影到当前 RuntimeScope 的正式世界观知识；下方原始 Catalog 保持独立只读审计，不会自动注入群聊。</CardDescription></CardHeader><CardContent className="flex flex-col gap-4"><div className="grid gap-4 md:grid-cols-2"><ScopeSelect value={botId || undefined} loadOptions={loadBots} label="Bot" placeholder="选择真实 Bot" required onValueChange={(value) => { setProjectionOffset(0); pagination.setFilters({ bot_id: value, session_id: null }) }} /><ScopeSelect value={sessionId || undefined} loadOptions={loadSessions} label="群 / 会话" placeholder="选择 canonical 群会话" disabled={!botId} required onValueChange={(value) => { setProjectionOffset(0); pagination.setFilters({ session_id: value }) }} /></div><QueryState status={!botId || !sessionId ? 'unknown' : projectionLoading ? 'loading' : projectionError ? 'error' : projectionPayload?.items.length ? 'success' : 'empty'} error={projectionError} title="Reviewed BookLore 读取失败" description={!botId || !sessionId ? '请选择服务端证实的 Bot 与 canonical 群会话。' : '当前 RuntimeScope 尚无 approved reviewed projection；不会从原始 Catalog 或其他群补数据。'} onRetry={() => setReload((value) => value + 1)}><div className="overflow-hidden rounded-lg border bg-background"><Table><TableHeader><TableRow><TableHead>标题</TableHead><TableHead>摘要</TableHead><TableHead className="w-24">Rank</TableHead><TableHead className="w-24">证据</TableHead><TableHead className="w-20">详情</TableHead></TableRow></TableHeader><TableBody>{projectionPayload?.items.map((item) => <TableRow key={item.id}><TableCell className="font-medium">{item.title}</TableCell><TableCell className="max-w-xl"><p className="line-clamp-2 text-muted-foreground">{item.summary}</p></TableCell><TableCell className="font-mono text-xs">{item.rank.toFixed(2)}</TableCell><TableCell>{item.evidence.length} 条</TableCell><TableCell><ResponsiveDetail title={item.title} description="审核证据、来源 Catalog 与目标 RuntimeScope" className="sm:max-w-3xl" trigger={<Button type="button" variant="ghost" size="icon-sm" aria-label={`查看 ${item.title} 正式投影`}><EyeIcon /></Button>}><ReviewedProjectionDetail item={item} /></ResponsiveDetail></TableCell></TableRow>)}</TableBody></Table></div></QueryState>{projectionPayload ? <div className="flex items-center justify-between text-sm text-muted-foreground"><span>共 {projectionPayload.page.total ?? 0} 条</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={projectionLoading || projectionOffset === 0} onClick={() => setProjectionOffset(Math.max(0, projectionOffset - 25))}>上一页</Button><Button size="sm" variant="outline" disabled={projectionLoading || projectionOffset + 25 >= (projectionPayload.page.total ?? 0)} onClick={() => setProjectionOffset(projectionOffset + 25)}>下一页</Button></div></div> : null}</CardContent></Card>

      <QueryState status={summaryError ? 'error' : summary ? 'success' : 'loading'} error={summaryError} title="BookLore 摘要读取失败" onRetry={() => setReload((value) => value + 1)}>
        {summary ? <Card className="border-border/60"><CardContent className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm"><div><span className="text-muted-foreground">当前 catalog：</span><span className="font-medium">{summary.scope.catalog_id}</span> · {summary.scope.corpus_id} · {summary.scope.version}</div><Badge variant="outline">服务端配置 · 只读</Badge></CardContent></Card> : null}
      </QueryState>

      <Card className="border-border/60">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">知识目录</CardTitle>
          <CardDescription>搜索仅作用于当前分类；切换分类时保留搜索词，并从第一页开始。</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <form className="flex flex-wrap items-center gap-2" onSubmit={submitSearch}>
            <div className="relative min-w-64 max-w-xl flex-1"><SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" /><Input aria-label="搜索 BookLore" className="h-8 pl-8" value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder="搜索标题、摘要或内容" /></div>
            <Button type="submit" size="sm" disabled={loading}>搜索</Button>
            <Button type="button" size="sm" variant="outline" disabled={loading} onClick={() => setReload((value) => value + 1)}><RefreshCwIcon aria-hidden="true" />重新读取</Button>
          </form>

          <Tabs value={resource} onValueChange={(tab) => pagination.setFilters({ tab })}>
            <TabsList className="h-8 border bg-muted/40 p-0.5">{RESOURCES.map((item) => <TabsTrigger key={item.value} value={item.value} className="h-7 text-xs">{item.label}</TabsTrigger>)}</TabsList>
            {RESOURCES.map((item) => <TabsContent key={item.value} value={item.value} className="mt-3"><QueryState status={status} error={error ?? summaryError} title={`${item.label}读取失败`} description={!summary && summaryError ? '无法确认服务端 catalog scope，因此没有使用硬编码默认值继续查询。' : undefined} onRetry={() => setReload((value) => value + 1)}>
              <div className="overflow-hidden rounded-lg border">
                <Table>
                  <TableHeader><TableRow className="bg-muted/20"><TableHead className="w-20">ID</TableHead><TableHead className="w-56">标题</TableHead><TableHead>摘要</TableHead><TableHead className="w-48">治理状态</TableHead><TableHead className="w-14"><span className="sr-only">详情</span></TableHead></TableRow></TableHeader>
                  <TableBody>{payload?.items.map((entry) => <TableRow key={String(entry.id)}><TableCell className="font-mono text-xs">{entry.id}</TableCell><TableCell className="font-medium">{itemTitle(entry)}</TableCell><TableCell className="max-w-xl truncate text-muted-foreground">{itemSummary(entry)}</TableCell><TableCell>{governance(entry)}</TableCell><TableCell className="text-right"><ResponsiveDetail title={itemTitle(entry)} description={`${item.label}的只读内容与治理状态`} trigger={<Button type="button" variant="ghost" size="icon-sm" aria-label={`查看 ${itemTitle(entry)} 详情`}><EyeIcon aria-hidden="true" /></Button>}><ItemDetail item={entry} resource={item.value} /></ResponsiveDetail></TableCell></TableRow>)}</TableBody>
                </Table>
              </div>
            </QueryState></TabsContent>)}
          </Tabs>
          {payload?.page ? <PaginationControls page={payload.page} onOffsetChange={pagination.setOffset} onLimitChange={pagination.setLimit} disabled={loading} label="BookLore 分页" /> : null}
        </CardContent>
      </Card>

      <Card className="border-dashed"><CardHeader><CardTitle className="text-sm">如何理解这些数据</CardTitle><CardDescription>BookLore 是独立只读知识源。隔离项不会被当作可用知识；“解析状态未知”表示服务端没有提供该字段，不等同于解析成功。需要变更来源或重新导入时，请使用正式导入与学习流程。</CardDescription></CardHeader></Card>
    </div>
  )
}
