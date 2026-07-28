import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangleIcon, ArrowDownIcon, ArrowUpIcon, EyeIcon, Link2Icon, SearchIcon } from 'lucide-react'

import { getBlackboxPeople, type BlackboxListPayload, type BlackboxPersonItem } from '@/api/blackbox'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Separator } from '@/components/ui/separator'
import { BlackboxCapabilityPage } from './BlackboxCapabilityPage'
import { IdentityBindingSection } from '@/pages/bindings'

function governance(readonly: boolean) {
  return [
    { label: '影响范围', value: 'person_registry、user_profiles、affinity dimensions、relationship events。' },
    { label: '读取模式', value: readonly ? '只读诊断' : '可写' },
    { label: '生效时机', value: '只读诊断即时展示；合并人物等高危操作后续接入。' },
    { label: '是否需要重启', value: '只读查看不需要重启。' },
    { label: '回滚方式', value: '合并操作必须先 merge-preview，记录旧映射。' },
  ]
}

const PAGE_SIZE = 50

function attitudeBadge(level: string | undefined) {
  switch (level) {
    case 'intimate': return <Badge className="bg-pink-500 hover:bg-pink-600">亲密</Badge>
    case 'friendly': return <Badge className="bg-green-500 hover:bg-green-600">友好</Badge>
    case 'neutral':  return <Badge variant="outline">中立</Badge>
    case 'cold':     return <Badge className="bg-blue-400 hover:bg-blue-500">冷淡</Badge>
    case 'hostile':  return <Badge variant="destructive">敌对</Badge>
    default:         return <Badge variant="outline">{level || '-'}</Badge>
  }
}

function affectionColor(aff: number | undefined) {
  if (aff === undefined || aff === null) return 'text-muted-foreground'
  if (aff >= 60) return 'text-pink-500 font-bold'
  if (aff >= 30) return 'text-green-500 font-semibold'
  if (aff >= 0)  return ''
  if (aff >= -30) return 'text-blue-400'
  return 'text-red-500 font-semibold'
}

function formatTime(seconds: unknown): string {
  const s = Number(seconds)
  if (!Number.isFinite(s) || s <= 0) return '-'
  return new Date(s * 1000).toLocaleString('zh-CN')
}

function textField(item: BlackboxPersonItem, key: keyof BlackboxPersonItem, fallback = '-'): string {
  const value = item[key]
  return value === undefined || value === null || value === '' ? fallback : String(value)
}

function safeMeta(item: BlackboxPersonItem): Record<string, unknown> {
  const m = item.metadata
  if (!m) return {}
  if (typeof m === 'string') {
    try { return JSON.parse(m) } catch { return {} }
  }
  if (typeof m === 'object') return m as Record<string, unknown>
  return {}
}

type SortKey = 'qq_id' | 'affection' | 'interaction_count' | 'last_seen'

export function BlackboxPeoplePage() {
  const [peoplePayload, setPeoplePayload] = useState<BlackboxListPayload<BlackboxPersonItem> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortKey>('qq_id')
  const [sortDesc, setSortDesc] = useState(false)
  const [offset, setOffset] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const sortParam = sortDesc ? `-${sort}` : sort
      const payload = await getBlackboxPeople({ limit: PAGE_SIZE, offset, search: search || undefined, sort: sortParam })
      setPeoplePayload(payload)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'People 数据读取失败')
    } finally {
      setLoading(false)
    }
  }, [search, sort, sortDesc, offset])

  useEffect(() => { void load() }, [load])

  const handleSort = (key: SortKey) => {
    if (sort === key) {
      setSortDesc(!sortDesc)
    } else {
      setSort(key)
      setSortDesc(false)
    }
  }

  const people = peoplePayload?.items ?? []
  const total = peoplePayload?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1
  const withBotCount = people.filter(p => p.bot_id).length
  const withAffection = people.filter(p => p.affection != null).length
  const highAffection = people.filter(p => (p.affection ?? 0) >= 60).length
  const negativeAffection = people.filter(p => (p.affection ?? 0) < 0).length

  const sortIcon = (key: SortKey) => {
    if (sort !== key) return null
    return sortDesc ? <ArrowDownIcon className="ml-1 inline h-3 w-3" /> : <ArrowUpIcon className="ml-1 inline h-3 w-3" />
  }

  return (
    <div className="flex flex-col gap-6">
      <BlackboxCapabilityPage
        title="人物与好感管理"
        description="人物画像、UserProfile、Affinity、关系事件与别名的统一管理入口。"
        badges={['只读诊断', '治理配置', '合并人物为高风险']}
        metrics={[
          { label: 'person_registry', value: loading ? '加载中' : String(total), description: '人物登记表、别名和实体归属。' },
          { label: 'user_profiles', value: loading ? '加载中' : String(withBotCount), description: 'QQ/user_id、display_name、group_id 与 bot_id。' },
          { label: 'affinity dimensions', value: loading ? '加载中' : String(withAffection), description: '好感维度、score、interaction_count 与 last_seen。' },
        ]}
        sections={!loading ? [
          {
            title: '好感分布',
            description: '当前列表页好感度分布（affection score）。',
            items: [
              `高好感 (≥60): ${highAffection}`,
              `中立 (0~59): ${withAffection - highAffection - negativeAffection}`,
              `负好感 (<0): ${negativeAffection}`,
              `未记录: ${people.length - withAffection}`,
            ],
          },
          {
            title: '交互概览',
            description: '人物交互与 bot 绑定情况。',
            items: [
              `绑定 bot: ${withBotCount}`,
              `有别名: ${people.filter(p => p.aliases).length}`,
              `最近活跃: ${people.filter(p => p.last_seen && Number(p.last_seen) > (Date.now() / 1000 - 86400 * 7)).length}/周`,
            ],
          },
        ] : [
          { title: '加载中', description: '正在读取人物数据…', items: ['请稍候'] },
          { title: '加载中', description: '正在读取人物数据…', items: ['请稍候'] },
        ]}
        governance={governance(!loading && total > 0)}
      />

      {loading ? (
        <Card>
          <CardHeader>
            <CardTitle>People 数据加载中</CardTitle>
            <CardDescription>正在读取 /api/blackbox/people。</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-2/3" />
          </CardContent>
        </Card>
      ) : error ? (
        <Alert variant="destructive">
          <AlertTriangleIcon />
          <AlertTitle>People 数据读取失败</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle>人物画像列表</CardTitle>
                <CardDescription>person_registry + user_profiles 合并视图；点击详情查看完整画像。</CardDescription>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative">
                  <SearchIcon className="absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    className="w-48 pl-8"
                    placeholder="搜索 qq_id/昵称/别名..."
                    value={search}
                    onChange={e => { setSearch(e.target.value); setOffset(0) }}
                  />
                </div>
                <Badge variant="outline">共 {total} 人</Badge>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {people.length === 0 ? (
              <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">暂无 people/profile 数据</div>
            ) : (
              <>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="cursor-pointer select-none" onClick={() => handleSort('qq_id')}>
                        qq_id {sortIcon('qq_id')}
                      </TableHead>
                      <TableHead>display_name</TableHead>
                      <TableHead>nickname</TableHead>
                      <TableHead>group_id</TableHead>
                      <TableHead>bot_id</TableHead>
                      <TableHead className="cursor-pointer select-none" onClick={() => handleSort('affection')}>
                        好感度 {sortIcon('affection')}
                      </TableHead>
                      <TableHead>态度</TableHead>
                      <TableHead className="cursor-pointer select-none" onClick={() => handleSort('interaction_count')}>
                        互动次数 {sortIcon('interaction_count')}
                      </TableHead>
                      <TableHead className="cursor-pointer select-none" onClick={() => handleSort('last_seen')}>
                        最后活跃 {sortIcon('last_seen')}
                      </TableHead>
                      <TableHead>印象</TableHead>
                      <TableHead>别名</TableHead>
                      <TableHead className="text-right">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {people.map((person, index) => {
                      const meta = safeMeta(person)
                      const impression = String(meta.impression || '').slice(0, 16)
                      const attitude = String(meta.attitude_level || '')
                      const personId = person.qq_id || person.user_id || `person-${index}`
                      return (
                        <TableRow key={personId}>
                          <TableCell className="max-w-[100px] truncate font-mono text-xs">{textField(person, 'qq_id', textField(person, 'user_id'))}</TableCell>
                          <TableCell className="max-w-[100px] truncate">{textField(person, 'display_name')}</TableCell>
                          <TableCell className="max-w-[80px] truncate">{textField(person, 'nickname')}</TableCell>
                          <TableCell className="max-w-[100px] truncate font-mono text-xs">{textField(person, 'group_id')}</TableCell>
                          <TableCell className="max-w-[60px] truncate">{textField(person, 'bot_id')}</TableCell>
                          <TableCell className={affectionColor(person.affection)}>{person.affection ?? '-'}</TableCell>
                          <TableCell>{attitudeBadge(attitude)}</TableCell>
                          <TableCell>{person.interaction_count ?? person.message_count ?? '-'}</TableCell>
                          <TableCell className="text-xs text-muted-foreground">{formatTime(person.last_seen)}</TableCell>
                          <TableCell className="max-w-[120px] truncate text-xs text-muted-foreground" title={String(meta.impression || '')}>{impression || '-'}</TableCell>
                          <TableCell className="max-w-[100px] truncate text-xs text-muted-foreground">{textField(person, 'aliases').slice(0, 20)}</TableCell>
                          <TableCell className="text-right">
                            <Link to={`/blackbox/people/${encodeURIComponent(personId)}`}>
                              <Button variant="ghost" size="icon" title="查看详情">
                                <EyeIcon className="h-4 w-4" />
                              </Button>
                            </Link>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
                {total > PAGE_SIZE && (
                  <div className="mt-4 flex items-center justify-between">
                    <div className="text-sm text-muted-foreground">
                      第 {currentPage}/{totalPages} 页，共 {total} 条
                    </div>
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={offset <= 0}
                        onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                      >
                        上一页
                      </Button>
                      <Select
                        value={String(currentPage)}
                        onValueChange={v => setOffset((Number(v) - 1) * PAGE_SIZE)}
                      >
                        <SelectTrigger className="w-20">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {Array.from({ length: totalPages }, (_, i) => (
                            <SelectItem key={i} value={String(i + 1)}>
                              {i + 1}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={offset + PAGE_SIZE >= total}
                        onClick={() => setOffset(offset + PAGE_SIZE)}
                      >
                        下一页
                      </Button>
                    </div>
                  </div>
                )}
              </>
            )}
          </CardContent>
        </Card>
      )}

      <Separator className="my-2" />

      <div className="flex items-center gap-2">
        <Link2Icon className="h-5 w-5 text-muted-foreground" />
        <h2 className="text-lg font-semibold">身份绑定</h2>
      </div>
      <IdentityBindingSection />
    </div>
  )
}