import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { BookOpenIcon, CheckIcon, DatabaseIcon, EyeIcon, Globe2Icon, MessageSquareQuoteIcon, SearchIcon, ShieldCheckIcon, XIcon, LockIcon } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'

import { fetchJson } from '@/api/client'
import { getCatalogAudit, listJargons, listLegacyJargons, reviewJargon, type CatalogAssetRecord, type CatalogAuditPayload, type JargonItem, type JargonResponse, type LegacyJargonResponse } from '@/api/jargon'
import { getScopeOptions, scopeOptionsFor } from '@/api/options'
import { EvidenceList, ObjectDeepLink, PaginationControls, QueryState, ResponsiveDetail, ScopeSelect, type ObjectRefState } from '@/components/shared'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useCanonicalScopeDefault, usePaginationSearchParams } from '@/hooks/use-pagination-search-params'

const STATUS_LABELS: Record<JargonItem['status'], string> = {
  pending: '待审核',
  confirmed: '已确认',
  rejected: '已拒绝',
}

const EVIDENCE_TYPE_LABELS: Record<string, string> = {
  memory: '记忆证据',
  episode: '情节证据',
  relationship_event: '关系事件',
  message: '消息记录',
}

function deepLinkFailureState(reason: unknown): ObjectRefState {
  const payload = reason instanceof Error && 'payload' in reason ? (reason as Error & { payload?: unknown }).payload : undefined
  const code = typeof payload === 'object' && payload !== null && 'error' in payload
    ? (payload as { error?: { code?: unknown } }).error?.code
    : undefined
  if (code === 'scope_mismatch') return 'scope-mismatch'
  if (code === 'version_stale') return 'version-stale'
  return 'not-found'
}

const DEEP_LINK_LABELS: Record<Exclude<ObjectRefState, 'ready'>, string> = {
  'not-found': '对象不存在或引用无效；不会使用裸 ID 回退定位。',
  'scope-mismatch': '对象引用与当前 Bot / 会话范围不匹配。',
  'version-stale': '对象版本已更新，请从最新列表重新打开。',
}

function statusClass(status: JargonItem['status']) {
  if (status === 'confirmed') return 'border-emerald-500/20 bg-emerald-500/10 text-emerald-600'
  if (status === 'pending') return 'border-amber-500/20 bg-amber-500/10 text-amber-600'
  return 'border-red-500/20 bg-red-500/10 text-red-600'
}

function confidenceText(value: number | null) {
  if (value === null || !Number.isFinite(value)) return '未评估'
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`
}

function textOf(item: CatalogAssetRecord, keys: string[], fallback = '—') {
  for (const key of keys) {
    const value = item[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (typeof value === 'number') return String(value)
  }
  return fallback
}

function listOf(item: CatalogAssetRecord, key: string) {
  const value = item[key]
  return Array.isArray(value) ? value.map(String).filter(Boolean) : []
}

function AuditMetric({ label, value, description }: { label: string; value: ReactNode; description?: string }) {
  return <div className="rounded-lg border bg-background/80 p-3"><p className="text-xs text-muted-foreground">{label}</p><div className="mt-1 text-lg font-semibold">{value}</div>{description ? <p className="mt-1 text-xs text-muted-foreground">{description}</p> : null}</div>
}

function EvidenceCards({ item }: { item: JargonItem }) {
  if (!item.anchors.length) return <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">没有可解析的锚点证据，因此不能安全通过审核。</div>
  return <div className="grid gap-3 sm:grid-cols-2">{item.anchors.slice(0, 4).map((anchor) => <div key={`${anchor.type}:${anchor.id}`} className="rounded-lg border bg-card p-3"><div className="flex items-center justify-between gap-2"><span className="font-medium">{EVIDENCE_TYPE_LABELS[anchor.type] ?? '上下文证据'}</span><Badge variant={anchor.availability === 'available' ? 'secondary' : 'outline'}>{anchor.availability === 'available' ? '可用' : '待核验'}</Badge></div><p className="mt-2 line-clamp-2 text-sm text-muted-foreground">{anchor.summary || '已保留可追溯引用，可在技术详情中核对完整来源。'}</p></div>)}</div>
}

function JargonDetails({ item, reviewAvailable, busy, onReview }: { item: JargonItem; reviewAvailable: boolean; busy: boolean; onReview: (action: 'approve' | 'reject') => void }) {
  return <div className="flex flex-col gap-6">
    <section className="space-y-3"><div className="flex flex-wrap items-center gap-2"><Badge className={statusClass(item.status)}>{STATUS_LABELS[item.status]}</Badge><Badge variant="outline">出现 {item.frequency} 次</Badge><Badge variant="outline">置信度 {confidenceText(item.confidence)}</Badge></div><div><h3 className="text-xl font-semibold">{item.word}</h3><p className="mt-2 text-base leading-7 text-muted-foreground">{item.meaning || '尚未形成可展示的释义。'}</p></div></section>
    <section className="space-y-3"><div className="flex items-center justify-between"><h3 className="font-medium">证据卡</h3><span className="text-sm text-muted-foreground">{item.anchors.length} 条</span></div><EvidenceCards item={item} /></section>
    <Alert><ShieldCheckIcon /><AlertTitle>审核边界</AlertTitle><AlertDescription>只有待审核且拥有可解析证据的领域候选可以通过；页面不提供自由创建、物理删除或绕过审核写入。</AlertDescription></Alert>
    <div className="flex flex-wrap gap-2 border-t pt-4"><Button disabled={busy || !reviewAvailable || item.status !== 'pending' || item.anchors.length === 0} onClick={() => onReview('approve')}><CheckIcon data-icon="inline-start" />通过审核</Button><Button variant="outline" disabled={busy || !reviewAvailable || item.status !== 'pending'} onClick={() => onReview('reject')}><XIcon data-icon="inline-start" />拒绝候选</Button></div>
    <details className="rounded-lg border bg-muted/20 p-3"><summary className="cursor-pointer font-medium">技术字段与完整证据引用</summary><div className="mt-4 flex flex-col gap-4"><dl className="grid gap-3 text-sm sm:grid-cols-2"><div><dt className="text-muted-foreground">修订版本</dt><dd className="font-mono">{item.revision}</dd></div><div><dt className="text-muted-foreground">审核状态</dt><dd className="break-all font-mono">{item.review_status}</dd></div><div><dt className="text-muted-foreground">来源</dt><dd className="break-all font-mono">{item.source}</dd></div><div><dt className="text-muted-foreground">规则版本</dt><dd className="break-all font-mono">{item.rule_version ?? '未记录'}</dd></div><div className="sm:col-span-2"><dt className="text-muted-foreground">作用域</dt><dd className="break-all font-mono">{item.bot_id} · {item.session_id} · {item.visibility}</dd></div></dl><EvidenceList evidence={item.anchors} emptyDescription="该条目没有可解析 anchor，无法安全晋升。" />{item.object_ref ? <ObjectDeepLink to="/jargon" objectRef={item.object_ref}>复制可复现对象深链</ObjectDeepLink> : null}<details className="rounded-md border bg-background p-3"><summary className="cursor-pointer text-sm">查看晋升记录 JSON</summary><pre className="mt-3 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(item.promotion ?? {}, null, 2)}</pre></details></div></details>
  </div>
}

function CatalogRecordDetail({ title, item }: { title: string; item: CatalogAssetRecord }) {
  const tags = listOf(item, 'linked_terms')
  return <div className="flex flex-col gap-4"><div><h3 className="text-lg font-semibold">{title}</h3><p className="mt-2 whitespace-pre-wrap leading-7 text-muted-foreground">{textOf(item, ['meaning', 'summary', 'content', 'text'], '当前资产没有可展示的正文。')}</p></div>{tags.length ? <div><p className="mb-2 text-sm font-medium">关联词条</p><div className="flex flex-wrap gap-1">{tags.map((tag) => <Badge key={tag} variant="secondary">{tag}</Badge>)}</div></div> : null}<details className="rounded-lg border bg-muted/20 p-3"><summary className="cursor-pointer font-medium">技术字段与原始 JSON</summary><pre className="mt-3 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(item, null, 2)}</pre></details></div>
}

function CatalogTable({ items, titleKeys, summaryKeys, emptyText }: { items: CatalogAssetRecord[]; titleKeys: string[]; summaryKeys: string[]; emptyText: string }) {
  if (!items.length) return <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">{emptyText}</div>
  return <div className="overflow-hidden rounded-lg border"><Table><TableHeader><TableRow><TableHead>名称</TableHead><TableHead>内容摘要</TableHead><TableHead className="w-24 text-right">详情</TableHead></TableRow></TableHeader><TableBody>{items.map((item, index) => { const title = textOf(item, titleKeys, `资产 ${index + 1}`); return <TableRow key={`${title}:${index}`}><TableCell className="font-medium">{title}</TableCell><TableCell className="max-w-2xl"><p className="line-clamp-2 text-sm text-muted-foreground">{textOf(item, summaryKeys)}</p></TableCell><TableCell className="text-right"><ResponsiveDetail title={title} description="Holyman 广域资产只读详情" className="sm:max-w-3xl" trigger={<Button type="button" variant="ghost" size="sm"><EyeIcon data-icon="inline-start" />查看</Button>}><CatalogRecordDetail title={title} item={item} /></ResponsiveDetail></TableCell></TableRow> })}</TableBody></Table></div>
}

function CatalogBrowser({ catalog }: { catalog: CatalogAuditPayload | null }) {
  const [search, setSearch] = useState('')
  const normalized = search.trim().toLocaleLowerCase()
  const filter = useCallback((items: CatalogAssetRecord[] | undefined) => (items ?? []).filter((item) => !normalized || Object.values(item).some((value) => typeof value === 'string' && value.toLocaleLowerCase().includes(normalized))), [normalized])
  const phrases = filter(catalog?.phrases)
  const concepts = filter(catalog?.concepts)
  const examples = filter(catalog?.examples)
  const corpus = filter(catalog?.corpus)
  const candidates = filter(catalog?.candidates)
  const blocked = useMemo<CatalogAssetRecord[]>(() => Object.entries(catalog?.blocked ?? {}).map(([word, reason]) => ({ word, reason })), [catalog?.blocked])
  const sourceCount = Number(catalog?.manifest_summary?.source_count ?? 0)
  const parseStatuses = catalog?.manifest_summary?.parse_statuses
  const parsedCorpus = catalog?.quality_summary?.parsed_corpus_count
  const declaredCorpus = catalog?.quality_summary?.declared_corpus_count
  const errorCount = Number(catalog?.quality_summary?.error_count ?? 0)

  return <div className="flex flex-col gap-5">
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><AuditMetric label="精选口癖" value={`${catalog?.phrases?.length ?? 0} 条`} description="仅显式授权项可参与理解提示" /><AuditMetric label="文化概念" value={`${catalog?.concepts?.length ?? 0} 组`} description="只读语境参考" /><AuditMetric label="声音样本与知识" value={`${catalog?.examples?.length ?? 0} 条`} description="只读语境参考" /><AuditMetric label="原始语料" value={`${catalog?.corpus?.length ?? Number(catalog?.corpus_summary?.count ?? 0)} 条`} description="只读参考，不进入提示" /></div>

    <Card className="bg-muted/10"><CardHeader><CardTitle className="text-base">广域资产审计</CardTitle><CardDescription>版本、解析结果与质量摘要用于核对广域资产完整性；同步命令当前不可用。</CardDescription></CardHeader><CardContent className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><AuditMetric label="本地版本" value={catalog?.local_version || '未知'} /><AuditMetric label="远端版本" value={catalog?.remote_version || '未知'} /><AuditMetric label="清单来源文件" value={sourceCount} description={parseStatuses && typeof parseStatuses === 'object' ? `已记录 ${Object.values(parseStatuses).reduce<number>((sum, value) => sum + Number(value || 0), 0)} 个解析结果` : '暂无解析摘要'} /><AuditMetric label="语料核对" value={`${String(parsedCorpus ?? '—')} / ${String(declaredCorpus ?? '—')}`} description={errorCount ? `${errorCount} 个质量错误` : '未报告质量错误'} /></CardContent></Card>

    <Alert><Globe2Icon /><AlertTitle>广域资产只读浏览</AlertTitle><AlertDescription>资产策略为“仅辅助理解”。原始语料只作为参考，只有资产明确授权的精选项才可能进入受控提示；同步命令当前不可用。</AlertDescription></Alert>

    <Field><FieldLabel htmlFor="catalog-search">浏览器内筛选</FieldLabel><Input id="catalog-search" value={search} placeholder="搜索口癖、概念、声音样本或语料" onChange={(event) => setSearch(event.target.value)} /></Field>

    <Tabs defaultValue="phrases"><TabsList className="grid h-auto w-full grid-cols-2 gap-1 sm:grid-cols-3 xl:grid-cols-6"><TabsTrigger value="phrases">精选口癖</TabsTrigger><TabsTrigger value="concepts">文化概念</TabsTrigger><TabsTrigger value="examples">声音样本</TabsTrigger><TabsTrigger value="corpus">原始语料</TabsTrigger><TabsTrigger value="candidates">资产候选</TabsTrigger><TabsTrigger value="blocked">屏蔽项</TabsTrigger></TabsList>
      <TabsContent value="phrases" className="pt-3"><CatalogTable items={phrases} titleKeys={['word', 'title']} summaryKeys={['meaning', 'summary']} emptyText="没有符合筛选条件的精选口癖。" /></TabsContent>
      <TabsContent value="concepts" className="pt-3"><CatalogTable items={concepts} titleKeys={['title', 'key', 'word']} summaryKeys={['summary', 'content', 'meaning']} emptyText="没有符合筛选条件的文化概念。" /></TabsContent>
      <TabsContent value="examples" className="pt-3"><CatalogTable items={examples} titleKeys={['title', 'word']} summaryKeys={['text', 'content', 'summary']} emptyText="没有符合筛选条件的声音样本与知识。" /></TabsContent>
      <TabsContent value="corpus" className="pt-3"><CatalogTable items={corpus} titleKeys={['title', 'source', 'line']} summaryKeys={['preview', 'text', 'content']} emptyText="没有符合筛选条件的原始语料。" /></TabsContent>
      <TabsContent value="candidates" className="pt-3"><Alert className="mb-3"><ShieldCheckIcon /><AlertTitle>只读候选层</AlertTitle><AlertDescription>这里展示资产包内的候选，不提供审核或写入操作，也不会把昵称、普通词或技术噪声重新包装为本地候选。</AlertDescription></Alert><CatalogTable items={candidates} titleKeys={['word', 'title']} summaryKeys={['meaning', 'reason', 'summary']} emptyText="资产包中没有符合筛选条件的候选。" /></TabsContent>
      <TabsContent value="blocked" className="pt-3"><CatalogTable items={filter(blocked)} titleKeys={['word']} summaryKeys={['reason']} emptyText="没有符合筛选条件的屏蔽项。" /></TabsContent>
    </Tabs>

    <details className="rounded-lg border bg-muted/20 p-3"><summary className="cursor-pointer font-medium">技术字段与资产审计 JSON</summary><div className="mt-4 grid gap-4"><dl className="grid gap-3 text-sm sm:grid-cols-2"><div><dt className="text-muted-foreground">资产类型</dt><dd className="break-all font-mono">{catalog?.asset_type ?? '未知'}</dd></div><div><dt className="text-muted-foreground">运行策略</dt><dd className="break-all font-mono">{catalog?.runtime_policy ?? '未知'}</dd></div><div><dt className="text-muted-foreground">资产状态</dt><dd className="break-all font-mono">{catalog?.asset_status ?? '未知'}</dd></div><div><dt className="text-muted-foreground">检查时间</dt><dd className="break-all font-mono">{catalog?.checked_at ?? '未知'}</dd></div><div><dt className="text-muted-foreground">原始语料策略</dt><dd className="font-mono">reference-only</dd></div><div><dt className="text-muted-foreground">精选匹配标记</dt><dd className="font-mono">runtime-match</dd></div><div><dt className="text-muted-foreground">同步能力代码</dt><dd className="break-all font-mono">catalog_sync_command_unavailable</dd></div></dl><details className="rounded-md border bg-background p-3"><summary className="cursor-pointer text-sm">manifest / quality report</summary><pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify({ manifest: catalog?.manifest, quality_report: catalog?.quality_report, corpus_counts: catalog?.corpus_counts }, null, 2)}</pre></details></div></details>
  </div>
}

export function JargonPage() {
  const pagination = usePaginationSearchParams()
  const [params] = useSearchParams()
  const botId = params.get('bot_id') ?? ''
  const sessionId = params.get('session_id') ?? ''
  const visibility = params.get('visibility') ?? 'group'
  const objectRef = params.get('ref') ?? ''
  const objectId = params.get('object_id') ?? ''
  const statusFilter = params.get('status') ?? ''
  const search = params.get('search') ?? ''
  useCanonicalScopeDefault({ botId, sessionId, setFilters: pagination.setFilters })
  const scope = useMemo(() => ({ bot_id: botId, session_id: sessionId, visibility: 'group' as const }), [botId, sessionId])
  const [payload, setPayload] = useState<JargonResponse | null>(null)
  const [deepLinkedItem, setDeepLinkedItem] = useState<JargonItem | null>(null)
  const [deepLinkStatus, setDeepLinkStatus] = useState<'loading' | ObjectRefState | null>(null)
  const [catalog, setCatalog] = useState<CatalogAuditPayload | null>(null)
  const [catalogStatus, setCatalogStatus] = useState<'loading' | 'success' | 'error'>('loading')
  const [queryStatus, setQueryStatus] = useState<'loading' | 'success' | 'empty' | 'error'>('empty')
  const [error, setError] = useState<unknown>()
  const [mutating, setMutating] = useState<number | null>(null)
  const [searchDraft, setSearchDraft] = useState(search)
  const [legacyPayload, setLegacyPayload] = useState<LegacyJargonResponse | null>(null)
  const [legacyOffset, setLegacyOffset] = useState(0)
  const [legacyLoading, setLegacyLoading] = useState(true)

  useEffect(() => setSearchDraft(search), [search])
  const loadBots = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['bot']), [])
  const loadSessions = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['session']).filter((option) => !botId || option.description?.startsWith(`${botId} ·`)), [botId])

  const load = useCallback(async () => {
    if (!botId || !sessionId) { setPayload(null); setQueryStatus('empty'); return }
    setQueryStatus('loading')
    setError(undefined)
    try {
      const next = await listJargons({ ...scope, limit: pagination.limit, offset: pagination.offset, status: statusFilter || undefined, search: search || undefined })
      setPayload(next)
      setQueryStatus(next.items.length ? 'success' : 'empty')
    } catch (reason) { setPayload(null); setError(reason); setQueryStatus('error') }
  }, [botId, pagination.limit, pagination.offset, scope, search, sessionId, statusFilter])

  const loadCatalog = useCallback(async () => {
    setCatalogStatus('loading')
    try { setCatalog(await getCatalogAudit()); setCatalogStatus('success') }
    catch { setCatalog(null); setCatalogStatus('error') }
  }, [])

  useEffect(() => { void load() }, [load])
  useEffect(() => { void loadCatalog() }, [loadCatalog])
  useEffect(() => {
    let cancelled = false
    setLegacyLoading(true)
    listLegacyJargons({ status: statusFilter || undefined, search: search || undefined, limit: 25, offset: legacyOffset })
      .then((result) => { if (!cancelled) setLegacyPayload(result) })
      .catch(() => { if (!cancelled) setLegacyPayload(null) })
      .finally(() => { if (!cancelled) setLegacyLoading(false) })
    return () => { cancelled = true }
  }, [legacyOffset, search, statusFilter])
  useEffect(() => {
    if (!objectRef) { setDeepLinkedItem(null); setDeepLinkStatus(null); return }
    if (!botId || !sessionId || visibility !== 'group') { setDeepLinkedItem(null); setDeepLinkStatus('scope-mismatch'); return }
    if (objectId && !/^\d+$/.test(objectId)) { setDeepLinkedItem(null); setDeepLinkStatus('not-found'); return }
    const query = new URLSearchParams({ ref: objectRef, ...scope })
    const endpoint = objectId ? `/api/jargon/${objectId}?${query.toString()}` : `/api/jargon/resolve?${query.toString()}`
    let cancelled = false
    setDeepLinkStatus('loading')
    fetchJson<{ item: JargonItem }>(endpoint).then((result) => {
      if (cancelled) return
      setDeepLinkedItem(result.item)
      setDeepLinkStatus('ready')
    }).catch((reason) => {
      if (cancelled) return
      setDeepLinkedItem(null)
      setDeepLinkStatus(deepLinkFailureState(reason))
    })
    return () => { cancelled = true }
  }, [botId, objectId, objectRef, scope, sessionId, visibility])

  async function review(item: JargonItem, action: 'approve' | 'reject') {
    setMutating(item.id)
    try {
      const result = await reviewJargon(item, action, scope)
      if (!result.ok || result.operation.status !== 'succeeded') throw new Error('服务端未确认审核命令成功')
      toast.success(action === 'approve' ? '候选已通过证据审核' : '候选已拒绝')
      await load()
    } catch (reason) { toast.error(reason instanceof Error ? reason.message : '审核失败') }
    finally { setMutating(null) }
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault()
    pagination.setFilters({ search: searchDraft.trim() || null })
  }

  const pageItems = payload?.items ?? []
  const confirmedCount = pageItems.filter((item) => item.status === 'confirmed').length
  const pendingCount = pageItems.filter((item) => item.status === 'pending').length
  const anchorCount = pageItems.reduce((sum, item) => sum + item.anchors.length, 0)
  const totalText = payload?.page.total_status === 'exact' && payload.page.total !== null ? payload.page.total : '—'
  const reviewAvailable = payload?.capabilities.review.available === true

  return <div data-slot="jargon-page" className="flex flex-col gap-6">
    <Card className="overflow-hidden border-primary/10 bg-gradient-to-br from-primary/5 via-card to-card"><CardHeader className="gap-4 sm:flex-row sm:items-start sm:justify-between"><div className="flex gap-3"><div className="rounded-xl bg-primary/10 p-3 text-primary"><MessageSquareQuoteIcon className="size-6" /></div><div><CardTitle className="text-xl">群聊黑话与广域资产</CardTitle><CardDescription className="mt-1 max-w-3xl">本地已确认词条、待审核候选与 Holyman 广域参考资产严格分层；昵称、普通词和技术噪声不进入默认候选。</CardDescription></div></div><div className="flex items-center gap-2">{reviewAvailable ? <Badge variant="outline" className="border-emerald-500/25 bg-emerald-500/5 text-emerald-600"><ShieldCheckIcon className="size-3.5 mr-1" />证据审核中</Badge> : <Badge variant="outline" className="border-amber-500/25 bg-amber-500/5 text-amber-600"><LockIcon className="size-3.5 mr-1" />只读模式</Badge>}</div></CardHeader><CardContent className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><AuditMetric label="匹配总数" value={totalText} /><AuditMetric label="本页已确认" value={confirmedCount} /><AuditMetric label="本页待审核" value={pendingCount} /><AuditMetric label="本页证据锚点" value={anchorCount} /></CardContent></Card>

    {deepLinkStatus ? <Alert data-slot="jargon-deep-link-state" variant={deepLinkStatus === 'ready' || deepLinkStatus === 'loading' ? 'default' : 'destructive'}><AlertTitle>{deepLinkStatus === 'loading' ? '正在校验对象深链' : deepLinkStatus === 'ready' ? '深链词条已定位' : '无法打开深链词条'}</AlertTitle><AlertDescription>{deepLinkStatus === 'ready' && deepLinkedItem ? <span><strong>{deepLinkedItem.word}</strong>：{deepLinkedItem.meaning || '尚未形成释义'}</span> : deepLinkStatus === 'loading' ? '正在验证对象引用、当前范围与版本。' : DEEP_LINK_LABELS[deepLinkStatus as Exclude<ObjectRefState, 'ready'>]}</AlertDescription></Alert> : null}

    <Tabs defaultValue="local"><TabsList><TabsTrigger value="local"><BookOpenIcon />本地词条</TabsTrigger><TabsTrigger value="catalog"><Globe2Icon />Holyman 广域资产</TabsTrigger></TabsList>
      <TabsContent value="local" className="space-y-4">
        <Card><CardHeader><CardTitle className="text-base">范围与筛选</CardTitle><CardDescription>搜索仅在提交时更新地址，不会逐字写入浏览历史。</CardDescription></CardHeader><CardContent className="grid gap-4 md:grid-cols-2"><ScopeSelect value={botId || undefined} loadOptions={loadBots} label="Bot" placeholder="选择真实 Bot" onValueChange={(value) => pagination.setFilters({ bot_id: value, session_id: null })} /><ScopeSelect value={sessionId || undefined} loadOptions={loadSessions} label="群 / 会话" placeholder="选择真实群会话" disabled={!botId} onValueChange={(value) => pagination.setFilters({ session_id: value })} /><Field><FieldLabel>审核状态</FieldLabel><Select value={statusFilter || 'all'} onValueChange={(value) => pagination.setFilters({ status: value === 'all' ? null : value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部领域词条</SelectItem><SelectItem value="confirmed">已确认</SelectItem><SelectItem value="pending">待审核</SelectItem></SelectContent></Select></Field><form className="flex items-end gap-2" onSubmit={submitSearch}><Field className="flex-1"><FieldLabel htmlFor="jargon-search">词条搜索</FieldLabel><Input id="jargon-search" value={searchDraft} placeholder="搜索词条或释义" onChange={(event) => setSearchDraft(event.target.value)} /></Field><Button type="submit"><SearchIcon data-icon="inline-start" />搜索</Button>{search ? <Button type="button" variant="ghost" onClick={() => { setSearchDraft(''); pagination.setFilters({ search: null }) }}>清除</Button> : null}</form></CardContent></Card>

        <Card>
<CardHeader>
<CardTitle className="text-base">本地词条清单</CardTitle>
<CardDescription>主表只显示中文业务字段；来源、规则、对象引用和 JSON 收纳在详情中。</CardDescription>
</CardHeader>
<CardContent className="flex flex-col gap-4">
<QueryState status={queryStatus} error={error} onRetry={() => void load()} title={!botId || !sessionId ? '请选择真实 Bot 与会话' : undefined} description={!botId || !sessionId ? '作用域未选择时不会查询，也不会补入默认 Bot。' : undefined}>
<div className="overflow-hidden rounded-lg border">
<Table>
<TableHeader>
<TableRow>
<TableHead>词条</TableHead>
<TableHead>释义</TableHead>
<TableHead className="w-24">频次</TableHead>
<TableHead className="w-28">审核状态</TableHead>
<TableHead className="w-24">证据</TableHead>
<TableHead className="w-24 text-right">操作</TableHead>
</TableRow>
</TableHeader>
<TableBody>{pageItems.map((item) => <TableRow key={item.id}>
<TableCell className="font-semibold">{item.word}</TableCell>
<TableCell className="max-w-xl">
<p className="line-clamp-2 text-sm text-muted-foreground">{item.meaning || '尚未形成可展示的释义'}</p>
</TableCell>
<TableCell className="tabular-nums">{item.frequency} 次</TableCell>
<TableCell>
<Badge className={statusClass(item.status)}>{STATUS_LABELS[item.status]}</Badge>
</TableCell>
<TableCell>{item.anchors.length} 条</TableCell>
<TableCell className="text-right">
<ResponsiveDetail title={item.word} description="词条释义、证据卡与审核操作" className="sm:max-w-4xl" trigger={<Button type="button" variant="outline" size="sm">
<EyeIcon data-icon="inline-start" />查看</Button>}>
<JargonDetails item={item} reviewAvailable={payload?.capabilities.review.available ?? false} busy={mutating === item.id} onReview={(action) => void review(item, action)} />
</ResponsiveDetail>
</TableCell>
</TableRow>)}</TableBody>
</Table>
</div>
</QueryState>{payload && !payload.capabilities.review.available ? <Alert>
<AlertTitle>审核能力当前不可用</AlertTitle>
<AlertDescription>服务端拒绝原因：{payload.capabilities.review.reason_code ?? '未提供'}</AlertDescription>
</Alert> : null}{payload ? <PaginationControls page={payload.page} onOffsetChange={pagination.setOffset} onLimitChange={pagination.setLimit} /> : null}</CardContent>
</Card>
      </TabsContent>

      <TabsContent value="catalog"><Card><CardHeader><div className="flex items-start justify-between gap-3"><div><CardTitle className="flex items-center gap-2 text-base"><DatabaseIcon className="size-4" />广域资产浏览</CardTitle><CardDescription className="mt-1">只读审计 Holyman 分层参考资产，不赋予本地审核语义，也不提供同步写入。</CardDescription></div><Badge variant={catalog?.asset_status === 'ready' ? 'secondary' : 'outline'}>{catalog?.asset_status === 'ready' ? '资产就绪' : '状态待核验'}</Badge></div></CardHeader><CardContent><QueryState status={catalogStatus} title="广域资产暂不可用" description="未使用演示数据或旧缓存替代。" onRetry={() => void loadCatalog()}>{catalog ? <CatalogBrowser catalog={catalog} /> : null}</QueryState></CardContent></Card></TabsContent>
    </Tabs>

    <Card className="border-amber-500/10 bg-amber-500/[0.02]">
      <CardHeader className="py-4">
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="border-amber-500/20 text-amber-600 bg-amber-500/5">只读审计</Badge>
          <CardTitle className="text-base">Legacy 历史黑话</CardTitle>
        </div>
        <CardDescription>旧 group_jargon 只记录了旧 group_id，没有真实 BotProfile.db_id 与 canonical session。历史词条仅供审计，不参与上方审核流程。</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 pt-0">
        <Alert className="mb-4 border-amber-500/15 bg-amber-500/[0.02] text-amber-700 dark:text-amber-500"><ShieldCheckIcon className="size-4 text-amber-600" /><AlertTitle>未归属历史词条</AlertTitle><AlertDescription className="text-xs">{legacyPayload ? `共 ${legacyPayload.page.total.toLocaleString('zh-CN')} 条待审计记录。` : legacyLoading ? '正在读取审计清单。' : 'Legacy 审计接口暂不可用。'} 页面不会用旧 QQ 群号猜测 canonical Scope。</AlertDescription></Alert>{legacyPayload?.items.length ? <><div className="overflow-auto rounded-lg border bg-background"><Table><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>词条</TableHead><TableHead>释义</TableHead><TableHead>旧 group_id</TableHead><TableHead>频次</TableHead><TableHead>状态</TableHead></TableRow></TableHeader><TableBody>{legacyPayload.items.map((item) => <TableRow key={item.id}><TableCell className="font-mono text-xs">#{item.id}</TableCell><TableCell className="font-semibold">{item.word}</TableCell><TableCell className="max-w-xl"><p className="line-clamp-2">{item.meaning || '未记录'}</p></TableCell><TableCell className="font-mono text-xs text-muted-foreground">{item.group_id || '未记录'}</TableCell><TableCell>{item.frequency}</TableCell><TableCell>{item.status || '未知'}</TableCell></TableRow>)}</TableBody></Table></div><div className="flex items-center justify-between text-sm text-muted-foreground"><span>第 {legacyOffset + 1}-{Math.min(legacyOffset + 25, legacyPayload.page.total)} 条</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={legacyLoading || legacyOffset === 0} onClick={() => setLegacyOffset(Math.max(0, legacyOffset - 25))}>上一页</Button><Button size="sm" variant="outline" disabled={legacyLoading || legacyOffset + 25 >= legacyPayload.page.total} onClick={() => setLegacyOffset(legacyOffset + 25)}>下一页</Button></div></div></> : !legacyLoading ? <p className="text-xs text-muted-foreground py-4 text-center">没有未归属的 Legacy 黑话记录。</p> : null}</CardContent></Card>
  </div>
}

export default JargonPage
