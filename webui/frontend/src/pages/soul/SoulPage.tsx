import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ActivityIcon, AlertCircleIcon, Clock3Icon, HeartHandshakeIcon, LockIcon, TargetIcon, CompassIcon } from 'lucide-react'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts'

import { isRequestCancelled } from '@/api/client'
import { getScopeOptions, scopeOptionsFor } from '@/api/options'
import { getRelationships, type RelationshipItem } from '@/api/people'
import { getLegacySoulSnapshot, getSoulState, type LegacyMoodItem, type LegacySoulSnapshot, type SoulScopeSelection, type SoulStatePayload } from '@/api/soul'
import { RelationshipCalibrationPanel } from '@/components/relationship/RelationshipCalibrationPanel'
import { EvidenceList, ObjectDeepLink, PaginationControls, QueryState, ScopeSelect } from '@/components/shared'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from '@/components/ui/chart'
import { Separator } from '@/components/ui/separator'
import { useCanonicalScopeDefault, usePaginationSearchParams } from '@/hooks/use-pagination-search-params'

const moodChartConfig = {
  intensity: { label: '情绪强度', color: 'var(--chart-1)' },
} satisfies ChartConfig

const componentChartConfig = {
  value: { label: '分量值', color: 'var(--chart-2)' },
} satisfies ChartConfig

function formatTime(seconds: unknown): string {
  const value = Number(seconds)
  return Number.isFinite(value) && value > 0 ? new Date(value * 1000).toLocaleString('zh-CN') : '未记录'
}

function reasonText(reason: string | null | undefined): string {
  if (!reason) return '服务端未提供原因'
  const labels: Record<string, string> = {
    soul_scoped_repository_unavailable: '正式 SoulScope repository 尚未就绪',
    scoped_soul_mutation_unavailable: '正式 scoped mutation 尚未提供',
    soul_runtime_refresh_unavailable: 'Soul runtime refresh 尚未提供',
  }
  return labels[reason] ?? reason
}

function SectionUnavailable({ reason }: { reason?: string | null }) {
  return (
    <div className="rounded-xl border border-primary/10 bg-gradient-to-b from-card to-primary/5 p-6 text-center shadow-sm relative overflow-hidden group">
      <div className="absolute -right-6 -bottom-6 size-24 rounded-full bg-primary/5 blur-xl group-hover:scale-125 transition-transform duration-500" />
      <CompassIcon className="mx-auto mb-3.5 size-6 text-primary/60 animate-pulse" />
      <h4 className="text-xs font-semibold text-foreground/80 tracking-wide">等待心智唤醒</h4>
      <p className="mt-1.5 text-[11px] text-muted-foreground/90 max-w-[200px] mx-auto leading-normal">{reasonText(reason)}</p>
    </div>
  )
}

function LegacyMoodChart({ moods }: { moods: LegacyMoodItem[] }) {
  const data = moods.slice(-20).map((item) => ({ ...item, label: formatTime(item.timestamp) }))
  if (data.length < 2) return <div className="rounded-xl border bg-muted/10 p-6 text-center text-xs text-muted-foreground">Legacy 情绪样本不足，暂不绘制轨迹。</div>
  return (
    <div className="flex flex-col gap-3">
      <ChartContainer config={moodChartConfig} className="h-[220px] w-full">
        <AreaChart data={data} margin={{ left: 4, right: 12, top: 8 }}>
          <defs><linearGradient id="legacyMoodFill" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="var(--chart-1)" stopOpacity={0.2} /><stop offset="95%" stopColor="var(--chart-1)" stopOpacity={0.01} /></linearGradient></defs>
          <CartesianGrid vertical={false} strokeDasharray="3 3" opacity={0.2} />
          <XAxis dataKey="timestamp" tickFormatter={(value) => formatTime(value).slice(5, 16)} tickLine={false} axisLine={false} minTickGap={28} className="text-[10px]" />
          <YAxis domain={[0, 1]} tickLine={false} axisLine={false} width={32} className="text-[10px]" />
          <ChartTooltip content={<ChartTooltipContent />} />
          <Area dataKey="intensity" type="monotone" stroke="var(--chart-1)" strokeWidth={2} fill="url(#legacyMoodFill)" />
        </AreaChart>
      </ChartContainer>
      <div className="flex flex-wrap gap-2">{data.slice(-10).map((item) => <div key={item.id} className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs ${item.is_active ? 'border-primary/30 bg-primary/5' : 'bg-muted/10'}`}><Badge variant="outline">{item.type || 'unknown'}</Badge><span className="font-mono text-muted-foreground">{Math.round(Number(item.intensity || 0) * 100)}%</span>{item.is_active ? <Badge variant="secondary">active</Badge> : null}</div>)}</div>
    </div>
  )
}

export function SoulPage() {
  const pagination = usePaginationSearchParams()
  const [searchParams] = useSearchParams()
  const [payload, setPayload] = useState<SoulStatePayload | null>(null)
  const [legacy, setLegacy] = useState<LegacySoulSnapshot | null>(null)
  const [relationshipOptions, setRelationshipOptions] = useState<RelationshipItem[]>([])
  const [relationshipOptionsLoading, setRelationshipOptionsLoading] = useState(false)
  const subjectId = searchParams.get('subject_principal_id') ?? ''
  const [status, setStatus] = useState<'loading' | 'success' | 'empty' | 'unknown' | 'error'>('empty')
  const [error, setError] = useState<unknown>()
  const [legacyStatus, setLegacyStatus] = useState<'loading' | 'success' | 'empty' | 'error'>('empty')
  const [legacyError, setLegacyError] = useState<unknown>()
  const formalRequestRef = useRef<AbortController | null>(null)
  const legacyRequestRef = useRef<AbortController | null>(null)
  const botId = searchParams.get('bot_id') ?? ''
  const sessionId = searchParams.get('session_id') ?? ''
  useCanonicalScopeDefault({ botId, sessionId, setFilters: pagination.setFilters })
  const scope = useMemo<SoulScopeSelection | null>(() => botId && sessionId ? { bot_id: botId, session_id: sessionId, visibility: 'group' } : null, [botId, sessionId])
  const relationshipItem = useMemo<RelationshipItem | null>(() => {
    const relationship = payload?.relationship
    const ref = relationship?.people_ref
    if (!relationship || !ref || !relationship.values || relationship.revision === null) return null
    const locator = String(ref.locator ?? subjectId)
    const selected = relationshipOptions.find((item) => item.subject_principal_id === locator)
    if (!selected) return null
    return {
      ...selected,
      affinity: relationship.affinity,
      state: relationship.state,
      revision: Number(relationship.revision),
      values: relationship.values,
      evidence: relationship.evidence,
      object_ref: ref,
      calibration: relationship.calibration ?? { available: false, reason_code: 'relationship_unknown' },
    }
  }, [payload, relationshipOptions, subjectId])

  const loadBots = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['bot']), [])
  const loadSessions = useCallback(async () => {
    const options = scopeOptionsFor(await getScopeOptions(), ['session'])
    return botId ? options.filter((option) => option.description?.startsWith(`${botId} ·`)) : options
  }, [botId])

  useEffect(() => {
    if (!scope) {
      setRelationshipOptions([])
      setRelationshipOptionsLoading(false)
      return
    }
    const controller = new AbortController()
    let active = true
    setRelationshipOptionsLoading(true)
    getRelationships({ ...scope, limit: 100, offset: 0 }, controller.signal)
      .then((value) => { if (active && !controller.signal.aborted) setRelationshipOptions(value.items) })
      .catch(() => { if (active && !controller.signal.aborted) setRelationshipOptions([]) })
      .finally(() => { if (active && !controller.signal.aborted) setRelationshipOptionsLoading(false) })
    return () => { active = false; controller.abort() }
  }, [scope])

  const loadFormal = useCallback(async () => {
    formalRequestRef.current?.abort()
    if (!scope) {
      setPayload(null)
      setStatus('empty')
      return
    }
    const controller = new AbortController()
    formalRequestRef.current = controller
    setStatus('loading')
    setError(undefined)
    try {
      const formal = await getSoulState({ ...scope, ...(subjectId ? { subject_principal_id: subjectId } : {}) }, pagination.limit, pagination.offset, controller.signal)
      if (formalRequestRef.current !== controller || controller.signal.aborted) return
      setPayload(formal)
      setStatus('success')
    } catch (reason) {
      if (formalRequestRef.current !== controller || controller.signal.aborted || isRequestCancelled(reason)) return
      setPayload(null)
      setError(reason)
      setStatus('error')
    }
  }, [pagination.limit, pagination.offset, scope, subjectId])

  const loadLegacy = useCallback(async () => {
    legacyRequestRef.current?.abort()
    if (!scope) {
      setLegacy(null)
      setLegacyStatus('empty')
      return
    }
    const controller = new AbortController()
    legacyRequestRef.current = controller
    setLegacyStatus('loading')
    setLegacyError(undefined)
    try {
      const snapshot = await getLegacySoulSnapshot(scope, controller.signal)
      if (legacyRequestRef.current !== controller || controller.signal.aborted) return
      setLegacy(snapshot)
      setLegacyStatus('success')
    } catch (reason) {
      if (legacyRequestRef.current !== controller || controller.signal.aborted || isRequestCancelled(reason)) return
      setLegacy(null)
      setLegacyError(reason)
      setLegacyStatus('error')
    }
  }, [scope])

  useEffect(() => {
    void loadFormal()
    return () => formalRequestRef.current?.abort()
  }, [loadFormal])
  useEffect(() => {
    void loadLegacy()
    return () => legacyRequestRef.current?.abort()
  }, [loadLegacy])

  const componentData = useMemo(() => Object.entries(payload?.mood.components ?? {}).map(([name, value]) => ({ name, value })), [payload?.mood.components])
  const formalUnavailable = payload?.source.health === 'unavailable' || payload?.source.health === 'error'

  return (
    <div data-slot="soul-page" className="flex flex-col gap-5">
      <Card>
        <CardHeader className="py-4">
          <CardTitle>Soul 作用域状态</CardTitle>
          <CardDescription>Mood、Concern、Timeline 与关系仅按真实 Bot + canonical group session 读取；不接受默认 Bot、私聊或伪群作用域。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 pt-0 md:grid-cols-3"><ScopeSelect value={botId || undefined} loadOptions={loadBots} label="Bot" onValueChange={(value) => pagination.setFilters({ bot_id: value, session_id: null, subject_principal_id: null })} /><ScopeSelect value={sessionId || undefined} loadOptions={loadSessions} label="群 / 会话" disabled={!botId} onValueChange={(value) => pagination.setFilters({ session_id: value, subject_principal_id: null })} /><label className="flex min-w-0 flex-col gap-1.5 text-sm font-medium"><span>群友 principal</span><select className="h-8 min-w-0 rounded-md border bg-background px-2 font-mono text-xs font-normal" value={subjectId} onChange={(event) => pagination.setFilters({ subject_principal_id: event.target.value || null })} disabled={!botId || !sessionId || relationshipOptionsLoading || relationshipOptions.length === 0}><option value="">{relationshipOptionsLoading ? '正在读取当前群友…' : '选择当前群友'}</option>{relationshipOptions.map((item) => <option key={item.subject_principal_id} value={item.subject_principal_id}>{item.person.display_name} · {item.person.user_id}</option>)}{subjectId && !relationshipOptions.some((item) => item.subject_principal_id === subjectId) ? <option value={subjectId}>{subjectId}（当前深链）</option> : null}</select><span className="text-xs font-normal text-muted-foreground">选项来自当前 Bot + canonical 群会话；subject principal 不跨 Scope 复用</span></label></CardContent>
      </Card>

      <QueryState status={status} error={error} onRetry={() => void loadFormal()} title={!scope ? '请选择真实 Bot 与群会话' : undefined} description={!scope ? 'Soul 不接受默认 Bot、私聊或伪群作用域。' : undefined}>
        {payload ? <div className="flex flex-col gap-5">
          {formalUnavailable ? <Alert><AlertCircleIcon /><AlertTitle>正式 scoped Soul 数据 unavailable</AlertTitle><AlertDescription>{reasonText(payload.source.reason_code)}。下方正式区域会保持 unavailable/unknown；不会用 Legacy 表内容伪装成当前 Scope。</AlertDescription></Alert> : null}

          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="overflow-hidden border-primary/15 bg-gradient-to-br from-card to-primary/5 lg:col-span-2">
              <CardHeader className="border-b bg-muted/10 py-4"><div className="flex items-center gap-2"><ActivityIcon className="size-4 text-primary" /><CardTitle className="text-sm">当前 Mood 图 / 分量</CardTitle></div><CardDescription>正式 SoulScope · revision {payload.mood.revision ?? '未记录'} · policy {payload.mood.policy_version ?? '未记录'}</CardDescription></CardHeader>
              <CardContent className="pt-5">
                {formalUnavailable ? <SectionUnavailable reason={payload.source.reason_code} /> : <div className="grid gap-5 md:grid-cols-[180px_1fr]"><div className="flex min-h-40 flex-col items-center justify-center rounded-xl border bg-muted/10 text-center"><Badge variant={payload.mood.state === 'known' ? 'secondary' : 'outline'}>{payload.mood.state}</Badge><p className="mt-3 text-xl font-semibold">{payload.mood.value ?? '未知 / 未记录'}</p><p className="mt-1 text-xs text-muted-foreground">当前可信心境</p></div><div>{componentData.length ? <ChartContainer config={componentChartConfig} className="h-[190px] w-full"><BarChart data={componentData} layout="vertical" margin={{ left: 8, right: 16 }}><CartesianGrid horizontal={false} opacity={0.2} /><XAxis type="number" tickLine={false} axisLine={false} /><YAxis dataKey="name" type="category" tickLine={false} axisLine={false} width={88} className="text-[10px]" /><ChartTooltip content={<ChartTooltipContent />} /><Bar dataKey="value" fill="var(--chart-2)" radius={4} /></BarChart></ChartContainer> : <div className="flex h-[190px] items-center justify-center rounded-xl border text-sm text-muted-foreground">无可信分量</div>}</div></div>}
                {!formalUnavailable ? <div className="mt-4"><EvidenceList evidence={payload.mood.evidence} /></div> : null}
              </CardContent>
            </Card>

            <Card className="overflow-hidden border-pink-500/15 bg-gradient-to-br from-card to-pink-500/5">
              <CardHeader className="border-b bg-muted/10 py-4"><div className="flex items-center gap-2"><HeartHandshakeIcon className="size-4 text-pink-500" /><CardTitle className="text-sm">关系投影</CardTitle></div><CardDescription>正式 SoulScope · revision {payload.relationship.revision ?? '未记录'}</CardDescription></CardHeader>
              <CardContent className="flex flex-col gap-4 pt-5">
                {formalUnavailable ? <SectionUnavailable reason={payload.source.reason_code} /> : <><div className="rounded-xl border bg-background/50 p-4 text-center"><Badge variant={payload.relationship.state === 'known' ? 'secondary' : 'outline'}>{payload.relationship.state}</Badge><p className="mt-3 text-3xl font-bold font-mono">{payload.relationship.affinity ?? '—'}</p><p className="text-xs text-muted-foreground">Affinity</p>{typeof payload.relationship.affinity === 'number' ? <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-pink-500" style={{ width: `${Math.max(0, Math.min(100, payload.relationship.affinity <= 1 ? payload.relationship.affinity * 100 : payload.relationship.affinity))}%` }} /></div> : null}</div><EvidenceList evidence={payload.relationship.evidence} />{payload.relationship.people_ref ? <ObjectDeepLink to="/people" objectRef={payload.relationship.people_ref}>打开当前人物关系</ObjectDeepLink> : null}{relationshipItem ? <RelationshipCalibrationPanel item={relationshipItem} query={{ bot_id: botId, session_id: sessionId, visibility: 'group', user_id: relationshipItem.person.user_id, subject_principal_id: relationshipItem.subject_principal_id }} onChanged={() => void loadFormal()} /> : <Alert><AlertTitle>当前关系未知</AlertTitle><AlertDescription>请输入当前群友的 canonical subject principal；没有正式关系行时不会创建默认 0，也不会提供校准写入口。</AlertDescription></Alert>}</>}
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader className="border-b bg-muted/10 py-4"><div className="flex items-center gap-2"><TargetIcon className="size-4 text-primary" /><CardTitle className="text-sm">Concern · 当前关切</CardTitle></div><CardDescription>正式 SoulScope 共享窗口记录</CardDescription></CardHeader>
              <CardContent className="flex flex-col gap-3 pt-5">{formalUnavailable || payload.concerns.page.total_status === 'unavailable' ? <SectionUnavailable reason={payload.concerns.page.reason_code ?? payload.source.reason_code} /> : payload.concerns.items.length ? payload.concerns.items.map((item) => <div key={item.id} className="rounded-lg border bg-muted/10 p-3"><div className="flex items-start justify-between gap-3"><p className="text-sm font-semibold">{item.summary || '未命名关切'}</p><Badge variant="outline">revision {item.revision ?? '—'}</Badge></div><p className="mt-1 text-[10px] text-muted-foreground">policy {item.policy_version ?? '未记录'}</p><div className="mt-3"><EvidenceList evidence={item.evidence} /></div></div>) : <p className="p-6 text-center text-sm text-muted-foreground">当前 Scope 没有关切记录。</p>}</CardContent>
            </Card>

            <Card>
              <CardHeader className="border-b bg-muted/10 py-4"><div className="flex items-center gap-2"><Clock3Icon className="size-4 text-primary" /><CardTitle className="text-sm">Timeline · 时间线</CardTitle></div><CardDescription>正式 SoulScope 共享窗口事件锚点</CardDescription></CardHeader>
              <CardContent className="pt-5">{formalUnavailable || payload.timeline.page.total_status === 'unavailable' ? <SectionUnavailable reason={payload.timeline.page.reason_code ?? payload.source.reason_code} /> : payload.timeline.items.length ? <div className="ml-2 flex flex-col gap-5 border-l-2 border-muted pl-5">{payload.timeline.items.map((item) => <div key={item.id} className="relative"><span className="absolute -left-[27px] top-1 size-3 rounded-full border-2 border-background bg-primary" /><div className="rounded-lg border bg-muted/10 p-3"><p className="text-sm font-semibold">{item.summary || '未命名事件'}</p><p className="mt-1 text-[10px] text-muted-foreground">revision {item.revision ?? '未记录'} · policy {item.policy_version ?? '未记录'}</p><div className="mt-3"><EvidenceList evidence={item.evidence} /></div></div></div>)}</div> : <p className="p-6 text-center text-sm text-muted-foreground">当前 Scope 没有时间线记录。</p>}</CardContent>
            </Card>
          </div>

          <div className="flex flex-col gap-2"><p className="text-xs text-muted-foreground">正式 Soul API 对 Concern 与 Timeline 使用同一组 limit/offset；下方分页会同时移动两个列表的共享窗口。</p><PaginationControls page={payload.concerns.page} onOffsetChange={pagination.setOffset} onLimitChange={pagination.setLimit} /></div>

          <Separator />

          <details className="overflow-hidden rounded-xl border border-amber-500/25 bg-amber-500/[0.03]">
            <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-6 py-4 marker:hidden"><LockIcon className="size-4 text-amber-500" /><span className="text-sm font-semibold">Legacy 只读审计（非当前 Scope）</span><Badge variant="outline" className="border-amber-500/30 text-amber-600">readonly</Badge><Badge variant="outline">legacy-not-session-scoped</Badge><span className="text-xs text-muted-foreground">默认收起</span></summary>
            <div className="border-t border-amber-500/15 px-6 py-5">
              <p className="mb-5 text-sm text-muted-foreground">以下内容来自可安全读取的旧表，最多只能证明 Bot 级筛选，不能证明属于当前 session。它与上方正式 SoulScope 数据严格分区，不提供任何写按钮。</p>
              <QueryState status={legacyStatus} error={legacyError} title="Legacy 只读投影不可用" onRetry={() => void loadLegacy()}>
              {legacy ? <>
                <div>
                  <div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-semibold">Legacy Mood trajectory</h3><Badge variant={legacy.moods.status === 'available' ? 'secondary' : 'outline'}>{legacy.moods.status}</Badge></div>
                  {legacy.moods.status === 'available' ? <LegacyMoodChart moods={legacy.moods.items} /> : <SectionUnavailable reason={legacy.moods.reason} />}
                </div>
                <Separator />
                <div className="grid gap-5 lg:grid-cols-2">
                  <div><div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-semibold">Legacy Concern</h3><Badge variant={legacy.concerns.status === 'available' ? 'secondary' : 'outline'}>{legacy.concerns.status}</Badge></div>{legacy.concerns.status === 'unavailable' ? <SectionUnavailable reason={legacy.concerns.reason} /> : legacy.concerns.items.length ? <div className="flex flex-col gap-3">{legacy.concerns.items.map((item) => <div key={item.id} className="rounded-lg border bg-background/50 p-3"><div className="flex items-center justify-between gap-3"><p className="truncate text-xs font-semibold">{item.topic}</p><Badge variant="outline" className="font-mono">{Math.round(Number(item.intensity || 0) * 100)}%</Badge></div><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-amber-500" style={{ width: `${Math.max(0, Math.min(100, Number(item.intensity || 0) * 100))}%` }} /></div><p className="mt-2 text-[10px] text-muted-foreground">Bot {item.bot_id || botId} · 最近触发 {formatTime(item.last_triggered)}</p></div>)}</div> : <p className="rounded-xl border p-6 text-center text-xs text-muted-foreground">Legacy 表中没有该 Bot 的 Concern。</p>}</div>
                  <div><div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-semibold">Legacy Timeline</h3><Badge variant={legacy.timeline.status === 'available' ? 'secondary' : 'outline'}>{legacy.timeline.status}</Badge></div>{legacy.timeline.status === 'unavailable' ? <SectionUnavailable reason={legacy.timeline.reason} /> : legacy.timeline.items.length ? <div className="ml-2 flex flex-col gap-5 border-l-2 border-amber-500/20 pl-5">{legacy.timeline.items.map((item) => <div key={item.id} className="relative"><span className="absolute -left-[27px] top-1 size-3 rounded-full border-2 border-background bg-amber-500" /><p className="text-xs font-semibold leading-relaxed">{item.event_summary}</p><p className="mt-1 text-[10px] text-muted-foreground">{formatTime(item.timestamp)} · 情绪印记 {Math.round(Number(item.emotional_weight || 0) * 100)}% · Bot {item.bot_id || botId}</p></div>)}</div> : <p className="rounded-xl border p-6 text-center text-xs text-muted-foreground">Legacy 表中没有该 Bot 的 Timeline。</p>}</div>
                </div>
              </> : null}
              </QueryState>
            </div>
          </details>

          <Alert><AlertTitle>运行时一致性边界</AlertTitle><AlertDescription>正式 mutation：{payload.capabilities.mutate.available ? 'available' : `unavailable（${reasonText(payload.capabilities.mutate.reason_code)}）`}；runtime refresh：{payload.runtime_refresh.status}。页面不会展示不可用的写按钮，也不会把 Legacy 读数据声明为当前 Scope。</AlertDescription></Alert>
        </div> : null}
      </QueryState>
    </div>
  )
}

export default SoulPage
