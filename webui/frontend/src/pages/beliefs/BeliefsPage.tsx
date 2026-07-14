import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { ArchiveIcon, BrainCircuitIcon, CheckIcon, EyeIcon, SearchIcon, ShieldCheckIcon } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'

import { approveBelief, archiveBelief, listBeliefs, listLegacyBeliefs, type BeliefItem, type BeliefType, type LegacyBeliefsResponse, type ScopedSelection } from '@/api/beliefs'
import { fetchJson } from '@/api/client'
import { getScopeOptions, scopeOptionsFor } from '@/api/options'
import { EvidenceList, ObjectDeepLink, PaginationControls, QualityDecisionBadge, QueryState, ResponsiveDetail, ScopeSelect, type ObjectRefState } from '@/components/shared'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useCanonicalScopeDefault, usePaginationSearchParams } from '@/hooks/use-pagination-search-params'

const TYPE_LABELS: Record<BeliefType, string> = {
  self_identity: '自我身份',
  person_judgment: '人物判断',
  world_view: '世界观',
  preference: '偏好',
}

const STATUS_LABELS: Record<BeliefItem['status'], string> = {
  pending: '待审核',
  active: '已生效',
  archived: '已归档',
  quarantined: '已隔离',
}

const COMPONENT_LABELS: Record<string, string> = {
  evidence: '证据质量',
  frequency: '出现频率',
  recency: '近期程度',
  consistency: '一致性',
  source: '来源可信度',
  confidence: '综合置信度',
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

function statusClass(status: BeliefItem['status']) {
  if (status === 'active') return 'border-emerald-500/20 bg-emerald-500/10 text-emerald-600'
  if (status === 'pending') return 'border-amber-500/20 bg-amber-500/10 text-amber-600'
  if (status === 'quarantined') return 'border-red-500/20 bg-red-500/10 text-red-600'
  return 'border-border bg-muted text-muted-foreground'
}

function typeClass(type: BeliefType) {
  if (type === 'self_identity') return 'border-purple-500/20 bg-purple-500/10 text-purple-600'
  if (type === 'person_judgment') return 'border-amber-500/20 bg-amber-500/10 text-amber-600'
  if (type === 'world_view') return 'border-blue-500/20 bg-blue-500/10 text-blue-600'
  return 'border-pink-500/20 bg-pink-500/10 text-pink-600'
}

function confidenceText(value: number | null) {
  if (value === null || !Number.isFinite(value)) return '未评估'
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`
}

function ConfidenceComponents({ item }: { item: BeliefItem }) {
  const components = Object.entries(item.confidence_components ?? {})
  if (!components.length) return <p className="text-sm text-muted-foreground">服务端未返回置信分量。</p>
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {components.map(([key, raw]) => {
        const value = Math.max(0, Math.min(1, Number(raw) || 0))
        return (
          <div key={key} className="rounded-lg border bg-muted/20 p-3">
            <div className="mb-2 flex items-center justify-between gap-3 text-sm">
              <span>{COMPONENT_LABELS[key] ?? '评估分量'}</span>
              <span className="font-medium tabular-nums">{Math.round(value * 100)}%</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-primary" style={{ width: `${value * 100}%` }} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

function EvidenceCards({ item }: { item: BeliefItem }) {
  if (!item.evidence.length) {
    return <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">当前没有可用证据，系统不会将它直接晋升为有效信念。</div>
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {item.evidence.slice(0, 4).map((evidence) => (
        <div key={`${evidence.type}:${evidence.id}`} className="rounded-lg border bg-card p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium">{EVIDENCE_TYPE_LABELS[evidence.type] ?? '关联证据'}</span>
            <Badge variant={evidence.availability === 'available' ? 'secondary' : 'outline'}>
              {evidence.availability === 'available' ? '可用' : evidence.availability === 'quarantined' ? '已隔离' : '待核验'}
            </Badge>
          </div>
          <p className="mt-2 line-clamp-2 text-sm text-muted-foreground">{evidence.summary || '已保留可追溯引用，可在技术详情中核对完整来源。'}</p>
        </div>
      ))}
    </div>
  )
}

function BeliefDetails({ item, mutating, onTransition }: { item: BeliefItem; mutating: boolean; onTransition: (action: 'approve' | 'archive') => void }) {
  return (
    <div className="flex flex-col gap-6">
      <section className="space-y-2">
        <div className="flex flex-wrap gap-2"><Badge className={typeClass(item.type)}>{TYPE_LABELS[item.type]}</Badge><Badge className={statusClass(item.status)}>{STATUS_LABELS[item.status]}</Badge></div>
        <p className="text-base leading-7 text-foreground">{item.content}</p>
        {item.anchor_sentence ? <blockquote className="rounded-r-lg border-l-2 border-primary bg-primary/5 px-4 py-3 text-muted-foreground">“{item.anchor_sentence}”</blockquote> : null}
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3"><h3 className="font-medium">状态分量</h3><Badge variant="outline">综合 {confidenceText(item.confidence)}</Badge></div>
        <ConfidenceComponents item={item} />
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3"><h3 className="font-medium">证据卡</h3><span className="text-sm text-muted-foreground">{item.evidence.length} 条</span></div>
        <EvidenceCards item={item} />
      </section>

      {item.quarantine_reason ? <Alert variant="destructive"><AlertTitle>当前处于隔离状态</AlertTitle><AlertDescription>{item.quarantine_reason}</AlertDescription></Alert> : null}

      <div className="flex flex-wrap gap-2 border-t pt-4">
        <Button disabled={mutating || !item.actions.approve.available} onClick={() => onTransition('approve')}><CheckIcon data-icon="inline-start" aria-hidden="true" />通过并激活</Button>
        <Button variant="outline" disabled={mutating || !item.actions.archive.available} onClick={() => onTransition('archive')}><ArchiveIcon data-icon="inline-start" aria-hidden="true" />归档</Button>
        {!item.actions.approve.available ? <span className="self-center text-sm text-muted-foreground">无法通过：{item.actions.approve.reason_code ?? '当前状态不允许'}</span> : null}
      </div>

      <details className="rounded-lg border bg-muted/20 p-3">
        <summary className="cursor-pointer font-medium">技术字段与完整证据引用</summary>
        <div className="mt-4 flex flex-col gap-4">
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <div><dt className="text-muted-foreground">信念键</dt><dd className="break-all font-mono">{item.belief_key}</dd></div>
            <div><dt className="text-muted-foreground">修订版本</dt><dd className="font-mono">{item.revision}</dd></div>
            <div><dt className="text-muted-foreground">置信策略</dt><dd className="break-all font-mono">{item.confidence_policy_version ?? '未记录'}</dd></div>
            <div><dt className="text-muted-foreground">作用域</dt><dd className="break-all font-mono">{item.bot_id} · {item.session_id} · {item.visibility}</dd></div>
          </dl>
          <EvidenceList evidence={item.evidence} />
          {item.object_ref ? <ObjectDeepLink to="/beliefs" objectRef={item.object_ref}>复制可复现对象深链</ObjectDeepLink> : null}
          <details className="rounded-md border bg-background p-3"><summary className="cursor-pointer text-sm">查看置信分量 JSON</summary><pre className="mt-3 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(item.confidence_components ?? {}, null, 2)}</pre></details>
        </div>
      </details>
    </div>
  )
}

export function BeliefsPage() {
  const pagination = usePaginationSearchParams()
  const [searchParams] = useSearchParams()
  const [payload, setPayload] = useState<Awaited<ReturnType<typeof listBeliefs>> | null>(null)
  const [queryStatus, setQueryStatus] = useState<'loading' | 'success' | 'empty' | 'error'>('loading')
  const [error, setError] = useState<unknown>()
  const [mutating, setMutating] = useState<number | null>(null)
  const [deepLinkedItem, setDeepLinkedItem] = useState<BeliefItem | null>(null)
  const [deepLinkStatus, setDeepLinkStatus] = useState<'loading' | ObjectRefState | null>(null)
  const [legacyPayload, setLegacyPayload] = useState<LegacyBeliefsResponse | null>(null)
  const [legacyOffset, setLegacyOffset] = useState(0)
  const [legacyLoading, setLegacyLoading] = useState(true)
  const botId = searchParams.get('bot_id') ?? ''
  const sessionId = searchParams.get('session_id') ?? ''
  const visibility = searchParams.get('visibility') ?? 'group'
  const objectRef = searchParams.get('ref') ?? ''
  const objectId = searchParams.get('object_id') ?? ''
  const type = (searchParams.get('type') ?? '') as BeliefType | ''
  const status = searchParams.get('status') ?? ''
  const search = searchParams.get('search') ?? ''
  useCanonicalScopeDefault({ botId, sessionId, setFilters: pagination.setFilters })
  const [searchDraft, setSearchDraft] = useState(search)

  useEffect(() => setSearchDraft(search), [search])

  const scope = useMemo<ScopedSelection | null>(() => botId && sessionId && visibility === 'group' ? { bot_id: botId, session_id: sessionId, visibility: 'group' } : null, [botId, sessionId, visibility])
  const loadBots = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['bot']), [])
  const loadSessions = useCallback(async () => {
    const options = scopeOptionsFor(await getScopeOptions(), ['session'])
    return botId ? options.filter((option) => option.description?.startsWith(`${botId} ·`)) : options
  }, [botId])

  const load = useCallback(async () => {
    if (!scope) {
      setPayload(null)
      setQueryStatus('empty')
      return
    }
    setQueryStatus('loading')
    setError(undefined)
    try {
      const next = await listBeliefs({ ...scope, limit: pagination.limit, offset: pagination.offset, type: type || undefined, status: status || undefined, search: search || undefined })
      setPayload(next)
      setQueryStatus(next.items.length ? 'success' : 'empty')
    } catch (reason) {
      setPayload(null)
      setError(reason)
      setQueryStatus('error')
    }
  }, [pagination.limit, pagination.offset, scope, search, status, type])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    let cancelled = false
    setLegacyLoading(true)
    listLegacyBeliefs({ type: type || undefined, status: status || undefined, search: search || undefined, limit: 25, offset: legacyOffset })
      .then((result) => { if (!cancelled) setLegacyPayload(result) })
      .catch(() => { if (!cancelled) setLegacyPayload(null) })
      .finally(() => { if (!cancelled) setLegacyLoading(false) })
    return () => { cancelled = true }
  }, [legacyOffset, search, status, type])
  useEffect(() => {
    if (!objectRef) { setDeepLinkedItem(null); setDeepLinkStatus(null); return }
    if (!scope) { setDeepLinkedItem(null); setDeepLinkStatus('scope-mismatch'); return }
    if (objectId && !/^\d+$/.test(objectId)) { setDeepLinkedItem(null); setDeepLinkStatus('not-found'); return }
    const query = new URLSearchParams({ ref: objectRef, ...scope })
    const endpoint = objectId ? `/api/beliefs/${objectId}?${query.toString()}` : `/api/beliefs/resolve?${query.toString()}`
    let cancelled = false
    setDeepLinkStatus('loading')
    fetchJson<{ item: BeliefItem }>(endpoint).then((result) => {
      if (cancelled) return
      setDeepLinkedItem(result.item)
      setDeepLinkStatus('ready')
    }).catch((reason) => {
      if (cancelled) return
      setDeepLinkedItem(null)
      setDeepLinkStatus(deepLinkFailureState(reason))
    })
    return () => { cancelled = true }
  }, [objectId, objectRef, scope])

  async function transition(item: BeliefItem, action: 'approve' | 'archive') {
    if (!scope || !item.actions[action].available) return
    setMutating(item.id)
    try {
      const result = action === 'approve' ? await approveBelief(item, scope) : await archiveBelief(item, scope)
      if (!result.ok || result.operation.status !== 'succeeded') throw new Error('服务端未确认生命周期变更成功')
      toast.success(action === 'approve' ? '信念已通过证据门并激活' : '信念已归档')
      await load()
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '信念状态变更失败')
    } finally {
      setMutating(null)
    }
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault()
    pagination.setFilters({ search: searchDraft.trim() || null })
  }

  const pageItems = payload?.items ?? []
  const activeCount = pageItems.filter((item) => item.status === 'active').length
  const pendingCount = pageItems.filter((item) => item.status === 'pending').length
  const evidenceCount = pageItems.reduce((sum, item) => sum + item.evidence.length, 0)
  const totalText = payload?.page.total_status === 'exact' && payload.page.total !== null ? payload.page.total : '—'

  return (
    <div data-slot="beliefs-page" className="flex flex-col gap-6">
      <Card className="overflow-hidden border-primary/10 bg-gradient-to-br from-primary/5 via-card to-card">
        <CardHeader className="gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex gap-3"><div className="rounded-xl bg-primary/10 p-3 text-primary"><BrainCircuitIcon className="size-6" /></div><div><CardTitle className="text-xl">信念证据与生命周期</CardTitle><CardDescription className="mt-1 max-w-3xl">以证据为中心查看心智信念；审核状态与生效、归档、隔离生命周期严格分离。</CardDescription></div></div>
          <Badge variant="outline" className="w-fit"><ShieldCheckIcon className="size-3.5" />仅允许受控状态迁移</Badge>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-lg border bg-background/80 p-3"><p className="text-xs text-muted-foreground">匹配总数</p><p className="mt-1 text-2xl font-semibold tabular-nums">{totalText}</p></div>
          <div className="rounded-lg border bg-background/80 p-3"><p className="text-xs text-muted-foreground">本页已生效</p><p className="mt-1 text-2xl font-semibold tabular-nums text-emerald-600">{activeCount}</p></div>
          <div className="rounded-lg border bg-background/80 p-3"><p className="text-xs text-muted-foreground">本页待审核</p><p className="mt-1 text-2xl font-semibold tabular-nums text-amber-600">{pendingCount}</p></div>
          <div className="rounded-lg border bg-background/80 p-3"><p className="text-xs text-muted-foreground">本页证据引用</p><p className="mt-1 text-2xl font-semibold tabular-nums text-primary">{evidenceCount}</p></div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">范围与筛选</CardTitle><CardDescription>先选择真实 Bot 和群会话；搜索仅在提交时更新地址，不会逐字写入浏览历史。</CardDescription></CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <ScopeSelect value={botId || undefined} loadOptions={loadBots} label="Bot" onValueChange={(value) => pagination.setFilters({ bot_id: value, session_id: null })} />
          <ScopeSelect value={sessionId || undefined} loadOptions={loadSessions} label="群 / 会话" disabled={!botId} onValueChange={(value) => pagination.setFilters({ session_id: value })} />
          <Field><FieldLabel>信念类型</FieldLabel><Select value={type || 'all'} onValueChange={(value) => pagination.setFilters({ type: value === 'all' ? null : value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="all">全部类型</SelectItem>{Object.entries(TYPE_LABELS).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>
          <Field><FieldLabel>生命周期</FieldLabel><Select value={status || 'all'} onValueChange={(value) => pagination.setFilters({ status: value === 'all' ? null : value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="all">全部状态</SelectItem><SelectItem value="pending">待审核</SelectItem><SelectItem value="active">已生效</SelectItem><SelectItem value="archived">已归档</SelectItem><SelectItem value="quarantined">已隔离</SelectItem></SelectGroup></SelectContent></Select></Field>
          <form className="flex items-end gap-2 md:col-span-2 xl:col-span-4" onSubmit={submitSearch}><Field className="flex-1"><FieldLabel htmlFor="belief-search">内容搜索</FieldLabel><Input id="belief-search" value={searchDraft} placeholder="搜索信念内容或锚定句" onChange={(event) => setSearchDraft(event.target.value)} /></Field><Button type="submit"><SearchIcon data-icon="inline-start" />搜索</Button>{search ? <Button type="button" variant="ghost" onClick={() => { setSearchDraft(''); pagination.setFilters({ search: null }) }}>清除</Button> : null}</form>
        </CardContent>
      </Card>

      {deepLinkStatus ? <Alert data-slot="belief-deep-link-state" variant={deepLinkStatus === 'ready' || deepLinkStatus === 'loading' ? 'default' : 'destructive'}><AlertTitle>{deepLinkStatus === 'loading' ? '正在校验对象深链' : deepLinkStatus === 'ready' ? '深链信念已定位' : '无法打开深链信念'}</AlertTitle><AlertDescription>{deepLinkStatus === 'ready' && deepLinkedItem ? <span><strong>{TYPE_LABELS[deepLinkedItem.type]}</strong>：{deepLinkedItem.content}</span> : deepLinkStatus === 'loading' ? '正在验证对象引用、当前范围与版本。' : DEEP_LINK_LABELS[deepLinkStatus as Exclude<ObjectRefState, 'ready'>]}</AlertDescription></Alert> : null}

      <Card>
        <CardHeader><CardTitle className="text-base">信念清单</CardTitle><CardDescription>主表仅展示可读业务字段；证据引用、版本和 JSON 收纳在详情中。</CardDescription></CardHeader>
        <CardContent className="flex flex-col gap-4">
          <QueryState status={queryStatus} error={error} onRetry={() => void load()} title={!scope ? '请选择真实 Bot 与会话' : undefined} description={!scope ? '作用域未选择时不会查询，也不会补入默认 Bot。' : undefined}>
            <div className="overflow-hidden rounded-lg border">
              <Table>
                <TableHeader><TableRow><TableHead>信念</TableHead><TableHead className="w-32">类型</TableHead><TableHead className="w-28">生命周期</TableHead><TableHead className="w-28">综合置信度</TableHead><TableHead className="w-24">证据</TableHead><TableHead className="w-28 text-right">操作</TableHead></TableRow></TableHeader>
                <TableBody>{pageItems.map((item) => (
                  <TableRow key={item.id} data-slot="belief-card">
                    <TableCell><p className="max-w-2xl font-medium leading-6">{item.content}</p>{item.anchor_sentence ? <p className="mt-1 max-w-2xl truncate text-xs text-muted-foreground">锚定句：{item.anchor_sentence}</p> : null}</TableCell>
                    <TableCell><Badge className={typeClass(item.type)}>{TYPE_LABELS[item.type]}</Badge></TableCell>
                    <TableCell><Badge className={statusClass(item.status)}>{STATUS_LABELS[item.status]}</Badge></TableCell>
                    <TableCell><div className="flex items-center gap-2"><span className="font-medium tabular-nums">{confidenceText(item.confidence)}</span><QualityDecisionBadge decision={item.evidence_health === 'available' ? 'allow' : 'quarantine'} /></div></TableCell>
                    <TableCell><span className="tabular-nums">{item.evidence.length} 条</span></TableCell>
                    <TableCell className="text-right"><ResponsiveDetail title={TYPE_LABELS[item.type]} description="证据、状态分量与受控生命周期操作" className="sm:max-w-4xl" trigger={<Button type="button" variant="outline" size="sm"><EyeIcon data-icon="inline-start" />查看</Button>}><BeliefDetails item={item} mutating={mutating === item.id} onTransition={(action) => void transition(item, action)} /></ResponsiveDetail></TableCell>
                  </TableRow>
                ))}</TableBody>
              </Table>
            </div>
          </QueryState>
          {payload ? <PaginationControls page={payload.page} onOffsetChange={pagination.setOffset} onLimitChange={pagination.setLimit} /> : null}
        </CardContent>
      </Card>

      <Card className="border-amber-500/10 bg-amber-500/[0.02]">
        <CardHeader className="py-4">
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="border-amber-500/20 text-amber-600 bg-amber-500/5">只读审计</Badge>
            <CardTitle className="text-base">Legacy 历史信念</CardTitle>
          </div>
          <CardDescription>旧 belief_system 记录没有真实 BotProfile.db_id 与 canonical session 证据；这里不允许审核、激活或归档，也不会将旧 group_id 当作正式会话。</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 pt-0">
          <Alert className="mb-4 border-amber-500/15 bg-amber-500/[0.02] text-amber-700 dark:text-amber-500"><ShieldCheckIcon className="size-4 text-amber-600" /><AlertTitle>严格隔离的历史记录</AlertTitle><AlertDescription className="text-xs">{legacyPayload ? `共 ${legacyPayload.page.total.toLocaleString('zh-CN')} 条待审计记录。` : legacyLoading ? '正在读取审计清单。' : 'Legacy 审计接口暂不可用。'} 只有唯一作用域证据成立时才可由后端投影。</AlertDescription></Alert>
          {legacyPayload?.items.length ? <><div className="overflow-auto rounded-lg border bg-background"><Table><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>内容</TableHead><TableHead>类型</TableHead><TableHead>旧 bot_id</TableHead><TableHead>状态</TableHead><TableHead>置信度</TableHead></TableRow></TableHeader><TableBody>{legacyPayload.items.map((item) => <TableRow key={item.id}><TableCell className="font-mono text-xs">#{item.id}</TableCell><TableCell className="max-w-xl"><p className="line-clamp-2">{item.content}</p></TableCell><TableCell>{TYPE_LABELS[item.type] ?? item.type}</TableCell><TableCell className="font-mono text-xs text-muted-foreground">{item.bot_id || '未记录'}</TableCell><TableCell>{item.status || '未知'}</TableCell><TableCell>{confidenceText(item.confidence)}</TableCell></TableRow>)}</TableBody></Table></div><div className="flex items-center justify-between text-sm text-muted-foreground"><span>第 {legacyOffset + 1}-{Math.min(legacyOffset + 25, legacyPayload.page.total)} 条</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={legacyLoading || legacyOffset === 0} onClick={() => setLegacyOffset(Math.max(0, legacyOffset - 25))}>上一页</Button><Button size="sm" variant="outline" disabled={legacyLoading || legacyOffset + 25 >= legacyPayload.page.total} onClick={() => setLegacyOffset(legacyOffset + 25)}>下一页</Button></div></div></> : !legacyLoading ? <p className="text-xs text-muted-foreground py-4 text-center">没有未归属的 Legacy 信念。</p> : null}
        </CardContent>
      </Card>
    </div>
  )
}
