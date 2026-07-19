import { useCallback, useEffect, useState, useMemo, type FormEvent } from 'react'
import { AlertCircleIcon, EyeIcon, RefreshCwIcon, SearchIcon, SlidersHorizontalIcon, ArrowUpDownIcon } from 'lucide-react'

import { getScopeOptions, scopeOptionsFor } from '@/api/options'
import { getPeople, getRelationships, type PersonItem, type RelationshipItem } from '@/api/people'
import { RelationshipCalibrationPanel } from '@/components/relationship/RelationshipCalibrationPanel'
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

function PersonDetail({ item, relationship, query, onChanged }: { item: PersonItem; relationship: RelationshipItem | null; query: { bot_id: string; session_id: string; visibility: 'group'; user_id?: string }; onChanged?: () => void }) {
  const aliases = aliasLabels(item.aliases)
  const metadataCount = Object.keys(item.metadata ?? {}).length + Object.keys(item.registry_metadata ?? {}).length
  const actualAffinity = relationship?.affinity !== undefined && relationship?.affinity !== null ? relationship.affinity : null

  return     <div className="flex flex-col gap-5 text-sm">
    <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">正式人物画像</Badge><Badge variant="secondary">group Scope</Badge><Badge variant="outline">关系以正式 projection 为准</Badge></div>


    <div className="grid gap-3 rounded-lg border bg-muted/10 p-3.5">
      <div><span className="mb-0.5 block text-xs text-muted-foreground">显示名称</span><span className="break-words font-medium">{item.display_name}</span></div>
      <div><span className="mb-0.5 block text-xs text-muted-foreground">用户 ID</span><span className="break-all font-mono text-xs">{item.user_id}</span></div>
      <div><span className="mb-0.5 block text-xs text-muted-foreground">Canonical Scope</span><span className="break-all font-mono text-xs">{item.bot_id} · {item.group_id} · group</span></div>
    </div>

    <div className="grid grid-cols-2 gap-3 rounded-lg border p-3.5">
      <div><span className="mb-0.5 block text-xs text-muted-foreground">互动数</span><span className="font-mono font-medium">{interactionCount(item) ?? '未记录'}</span></div>
      <div><span className="mb-0.5 block text-xs text-muted-foreground">Affinity</span>
        {actualAffinity !== null ? (
          <Badge className={`text-xs font-mono font-semibold ${
            actualAffinity >= 15 ? 'bg-rose-500 text-white' :
            actualAffinity >= 5 ? 'bg-pink-500 text-white' :
            actualAffinity > 0 ? 'bg-pink-400/80 text-white' :
            actualAffinity < 0 ? 'bg-blue-500 text-white' : 'bg-muted text-muted-foreground'
          }`}>
            {actualAffinity > 0 ? `+${actualAffinity}` : actualAffinity}
          </Badge>
        ) : (
          <Badge variant="outline">未激活</Badge>
        )}
      </div>
      <div className="col-span-2"><span className="mb-1.5 block text-xs text-muted-foreground">登记别名</span><div className="flex flex-wrap gap-1">{aliases.length ? aliases.map((alias) => <Badge key={alias} variant="outline" className="font-normal">{alias}</Badge>) : <span className="text-muted-foreground">未登记别名</span>}</div></div>
    </div>

    {relationship ? <RelationshipCalibrationPanel item={relationship} query={query} onChanged={onChanged} /> : <Alert><AlertCircleIcon /><AlertTitle>当前关系未知</AlertTitle><AlertDescription>当前 Scope 没有正式 relationship projection；不会使用跨群全局值或默认 0 伪造关系。</AlertDescription></Alert>}

    {actualAffinity === null && (
      <Alert>
        <AlertCircleIcon />
        <AlertTitle>Affinity 当前不可用</AlertTitle>
        <AlertDescription>{item.affinity_reason_code || '当前没有经过复合作用域验证的 affinity projection。'} 页面不会使用跨群全局值或固定回填值伪装当前关系。</AlertDescription>
      </Alert>
    )}

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
  const [relationshipData, setRelationshipData] = useState<PageResponse<RelationshipItem> | null>(null)
  const [error, setError] = useState<unknown>()
  const [loading, setLoading] = useState(false)
  const [reload, setReload] = useState(0)
  const [selectedPerson, setSelectedPerson] = useState<PersonItem | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)

  // 筛选相关状态
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false)
  const [filterState, setFilterState] = useState<'all' | 'known' | 'unknown'>('all')
  const [minAffinity, setMinAffinity] = useState<string>('')
  const [maxAffinity, setMaxAffinity] = useState<string>('')
  const [minInteractions, setMinInteractions] = useState<string>('')
  const [sortBy, setSortBy] = useState<'name' | 'interactions' | 'affinity'>('name')
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc')

  const loadBots = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['bot']), [])
  const loadSessions = useCallback(async () => {
    const options = scopeOptionsFor(await getScopeOptions(), ['session'])
    return botId ? options.filter((option) => option.description?.startsWith(`${botId} ·`)) : []
  }, [botId])

  useEffect(() => { setSearchDraft(search) }, [search])
  useEffect(() => {
    if (!botId || !sessionId) { setData(null); setRelationshipData(null); setLoading(false); setError(undefined); return }
    let active = true
    setLoading(true)
    setError(undefined)
    Promise.all([
      getPeople({ bot_id: botId, session_id: sessionId, visibility: 'group', search: search || undefined, limit: pagination.limit, offset: pagination.offset }),
      getRelationships({ bot_id: botId, session_id: sessionId, visibility: 'group', search: search || undefined, limit: pagination.limit, offset: pagination.offset }).catch(() => null),
    ])
      .then(([people, relationships]) => { if (active) { setData(people); setRelationshipData(relationships) } })
      .catch((reason: unknown) => { if (active) { setData(null); setRelationshipData(null); setError(reason) } })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [botId, pagination.limit, pagination.offset, reload, search, sessionId])
  const submitSearch = (event: FormEvent) => {
    event.preventDefault()
    pagination.setFilters({ search: searchDraft.trim() || null })
  }
  const clearSearch = () => {
    setSearchDraft('')
    pagination.setFilters({ search: null })
    // 重置高级筛选
    setFilterState('all')
    setMinAffinity('')
    setMaxAffinity('')
    setMinInteractions('')
    setSortBy('name')
    setSortOrder('asc')
  }
  const openDetail = (item: PersonItem) => {
    setSelectedPerson(item)
    setDetailOpen(true)
  }

  // 原始人物；空列表也保持稳定引用，避免派生筛选每次渲染失效。
  const rawPeople = useMemo(() => data?.items ?? [], [data])

  // 关联心智关系，应用多维高级过滤和排序
  const people = useMemo(() => {
    let result = rawPeople.map(person => {
      const relation = relationshipData?.items.find(r => r.person.user_id === person.user_id)
      return {
        ...person,
        relation: relation || null,
        affinity: relation?.affinity !== undefined && relation?.affinity !== null ? relation.affinity : null
      }
    })

    // 1. 关系状态过滤
    if (filterState === 'known') {
      result = result.filter(p => p.affinity !== null)
    } else if (filterState === 'unknown') {
      result = result.filter(p => p.affinity === null)
    }

    // 2. Affinity 范围过滤
    if (minAffinity !== '') {
      const minVal = Number(minAffinity)
      if (Number.isFinite(minVal)) {
        result = result.filter(p => p.affinity !== null && p.affinity >= minVal)
      }
    }
    if (maxAffinity !== '') {
      const maxVal = Number(maxAffinity)
      if (Number.isFinite(maxVal)) {
        result = result.filter(p => p.affinity !== null && p.affinity <= maxVal)
      }
    }

    // 3. 互动次数范围过滤
    if (minInteractions !== '') {
      const minInt = Number(minInteractions)
      if (Number.isFinite(minInt)) {
        result = result.filter(p => {
          const count = interactionCount(p)
          return count !== null && count >= minInt
        })
      }
    }

    // 4. 排序逻辑
    result.sort((a, b) => {
      let comparison = 0
      if (sortBy === 'name') {
        comparison = (a.display_name || '').localeCompare(b.display_name || '', 'zh-CN')
      } else if (sortBy === 'interactions') {
        const countA = interactionCount(a) ?? -1
        const countB = interactionCount(b) ?? -1
        comparison = countA - countB
      } else if (sortBy === 'affinity') {
        const affA = a.affinity ?? -9999
        const affB = b.affinity ?? -9999
        comparison = affA - affB
      }

      return sortOrder === 'asc' ? comparison : -comparison
    })

    return result
  }, [rawPeople, relationshipData, filterState, minAffinity, maxAffinity, minInteractions, sortBy, sortOrder])

  const aliasCount = data ? rawPeople.reduce((sum, item) => sum + aliasLabels(item.aliases).length, 0) : '—'
  const interactionTotal = data && rawPeople.every((item) => interactionCount(item) !== null) ? rawPeople.reduce((sum, item) => sum + interactionCount(item)!, 0) : '未提供'
  const total = data?.page.total_status === 'exact' ? people.length : '—'
  const status = !botId || !sessionId ? 'unknown' : loading ? 'loading' : error ? 'error' : !people.length ? 'empty' : 'success'

  // 计算本页平均/整体的 Affinity 指标
  const activeRelationships = relationshipData?.items.filter(r => r.affinity !== null) ?? []
  const averageAffinity = activeRelationships.length > 0
    ? (activeRelationships.reduce((sum, r) => sum + (r.affinity ?? 0), 0) / activeRelationships.length).toFixed(1)
    : '不可用'

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
        <SummaryTile label="本页平均 Affinity" value={loading ? '…' : averageAffinity} tone="text-rose-600" />
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

          <form className="flex flex-col gap-3" onSubmit={submitSearch}>
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative min-w-60 flex-1 xl:max-w-xl">
                <SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input aria-label="搜索人物" className="h-8 pl-8 text-xs" value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder="搜索用户 ID、昵称或登记别名" disabled={!botId || !sessionId} />
              </div>
              <Button type="submit" size="sm" className="h-8 text-xs" disabled={loading || !botId || !sessionId}>搜索</Button>
              <Button type="button" size="sm" className="h-8 text-xs" variant="ghost" onClick={clearSearch}>清除</Button>
              <Button type="button" size="sm" className="h-8 text-xs gap-1.5" variant="outline" onClick={() => setShowAdvancedFilters(!showAdvancedFilters)} disabled={!botId || !sessionId}>
                <SlidersHorizontalIcon className="size-3.5" />
                高级筛选
                {(filterState !== 'all' || minAffinity || maxAffinity || minInteractions || sortBy !== 'name' || sortOrder !== 'asc') && (
                  <Badge variant="secondary" className="px-1 py-0 h-4 min-w-4 text-[10px] bg-rose-500 text-white rounded-full">!</Badge>
                )}
              </Button>
              <Button type="button" size="icon-sm" className="h-8 w-8" variant="outline" disabled={loading || !botId || !sessionId} onClick={() => setReload((value) => value + 1)} aria-label="刷新人物画像">
                <RefreshCwIcon className={loading ? 'animate-spin' : undefined} aria-hidden="true" />
              </Button>
              <span className="ml-auto text-xs text-muted-foreground">{data?.page ? `当前第 ${Math.floor(pagination.offset / pagination.limit) + 1} 页` : '请选择完整 Scope'}</span>
            </div>

            {showAdvancedFilters && (
              <div className="grid gap-3 rounded-lg border bg-muted/20 p-3.5 text-xs animate-in fade-in slide-in-from-top-2 duration-150">
                <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-4">
                  {/* 关系激活状态 */}
                  <div className="flex flex-col gap-1.5">
                    <span className="font-semibold text-muted-foreground">关系激活状态</span>
                    <select
                      className="h-8 rounded-md border bg-background px-2 text-xs"
                      value={filterState}
                      onChange={(e) => setFilterState(e.target.value as typeof filterState)}
                    >
                      <option value="all">全部人物</option>
                      <option value="known">仅显示已激活关系 (known)</option>
                      <option value="unknown">仅未激活关系 (unknown)</option>
                    </select>
                  </div>

                  {/* Affinity 范围 */}
                  <div className="flex flex-col gap-1.5">
                    <span className="font-semibold text-muted-foreground">Affinity 范围</span>
                    <div className="flex items-center gap-1.5">
                      <Input
                        type="number"
                        placeholder="最小"
                        className="h-8 text-xs font-mono"
                        value={minAffinity}
                        onChange={(e) => setMinAffinity(e.target.value)}
                        disabled={filterState === 'unknown'}
                      />
                      <span className="text-muted-foreground">-</span>
                      <Input
                        type="number"
                        placeholder="最大"
                        className="h-8 text-xs font-mono"
                        value={maxAffinity}
                        onChange={(e) => setMaxAffinity(e.target.value)}
                        disabled={filterState === 'unknown'}
                      />
                    </div>
                  </div>

                  {/* 最少互动数 */}
                  <div className="flex flex-col gap-1.5">
                    <span className="font-semibold text-muted-foreground">最少互动次数</span>
                    <Input
                      type="number"
                      placeholder="例如 10"
                      className="h-8 text-xs font-mono"
                      value={minInteractions}
                      onChange={(e) => setMinInteractions(e.target.value)}
                    />
                  </div>

                  {/* 排序属性与方向 */}
                  <div className="flex flex-col gap-1.5">
                    <span className="font-semibold text-muted-foreground">列表排序</span>
                    <div className="flex items-center gap-1.5">
                      <select
                        className="h-8 flex-1 rounded-md border bg-background px-2 text-xs"
                        value={sortBy}
                        onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
                      >
                        <option value="name">显示名称</option>
                        <option value="interactions">互动次数</option>
                        <option value="affinity">Affinity</option>
                      </select>
                      <Button
                        type="button"
                        variant="outline"
                        size="icon-sm"
                        className="h-8 w-8 shrink-0"
                        onClick={() => setSortOrder(o => o === 'asc' ? 'desc' : 'asc')}
                      >
                        <ArrowUpDownIcon className={`size-3.5 transition-transform ${sortOrder === 'desc' ? 'rotate-180' : ''}`} />
                      </Button>
                    </div>
                  </div>
                </div>
              </div>
            )}
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
                  const hasAffinity = item.affinity !== null
                  return <TableRow key={item.scope_key} className="h-9 cursor-pointer hover:bg-muted/10" onClick={() => openDetail(item)}>
                    <TableCell className="max-w-44 truncate py-1 font-mono text-[11px]">{item.user_id}</TableCell>
                    <TableCell className="max-w-44 truncate py-1 text-xs font-medium">{item.display_name}</TableCell>
                    <TableCell className="max-w-48 truncate py-1 text-xs text-muted-foreground">{aliases.length ? aliases.join('、') : '未登记'}</TableCell>
                    <TableCell className="max-w-36 truncate py-1 font-mono text-[11px]">{item.group_id}</TableCell>
                    <TableCell className="max-w-32 truncate py-1"><Badge variant="secondary" className="max-w-full truncate px-1.5 font-mono text-[10px] font-normal">{item.bot_id}</Badge></TableCell>
                    <TableCell className="py-1 text-center font-mono text-[11px]">{interactionCount(item) ?? '—'}</TableCell>
                    <TableCell className="py-1">
                      {hasAffinity ? (
                        <Badge className={`text-[10px] font-mono font-semibold ${
                          item.affinity! >= 15 ? 'bg-rose-500 text-white' :
                          item.affinity! >= 5 ? 'bg-pink-500 text-white' :
                          item.affinity! > 0 ? 'bg-pink-400/80 text-white' :
                          item.affinity! < 0 ? 'bg-blue-500 text-white' : 'bg-muted text-muted-foreground'
                        }`}>
                          {item.affinity! > 0 ? `+${item.affinity}` : item.affinity}
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="text-[10px] text-muted-foreground">未激活</Badge>
                      )}
                    </TableCell>
                    <TableCell className="py-1 text-right" onClick={(event) => event.stopPropagation()}><Button type="button" variant="ghost" size="icon-xs" aria-label={`查看 ${item.display_name} 详情`} onClick={() => openDetail(item)}><EyeIcon aria-hidden="true" /></Button></TableCell>
                  </TableRow>
                })}</TableBody>
              </Table>}
              cards={people.map((item) => {
                const aliases = aliasLabels(item.aliases)
                const hasAffinity = item.affinity !== null
                return <article key={item.scope_key} className="flex flex-col gap-3 rounded-lg border bg-card p-4">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="font-medium">{item.display_name}</p>
                      <p className="break-all font-mono text-xs text-muted-foreground">{item.user_id}</p>
                    </div>
                    {hasAffinity ? (
                      <Badge className={`text-xs font-mono font-semibold ${
                        item.affinity! >= 15 ? 'bg-rose-500 text-white' :
                        item.affinity! >= 5 ? 'bg-pink-500 text-white' :
                        item.affinity! > 0 ? 'bg-pink-400/80 text-white' :
                        item.affinity! < 0 ? 'bg-blue-500 text-white' : 'bg-muted text-muted-foreground'
                      }`}>
                        Affinity: {item.affinity! > 0 ? `+${item.affinity}` : item.affinity}
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="text-xs text-muted-foreground">未激活</Badge>
                    )}
                  </div>
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


    <Sheet open={detailOpen} onOpenChange={setDetailOpen}>
      <SheetContent className="w-[min(94vw,34rem)] sm:max-w-xl">
        <SheetHeader className="border-b pr-12"><SheetTitle>人物画像详情</SheetTitle><SheetDescription>只读查看复合作用域内的身份、别名、互动与 Affinity 可用性。</SheetDescription></SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">{selectedPerson ? <PersonDetail item={selectedPerson} relationship={relationshipData?.items.find((entry) => entry.person.user_id === selectedPerson.user_id) ?? null} query={{ bot_id: botId, session_id: sessionId, visibility: 'group', user_id: selectedPerson.user_id }} onChanged={() => setReload((value) => value + 1)} /> : null}</div>
      </SheetContent>
    </Sheet>
  </div>
}
