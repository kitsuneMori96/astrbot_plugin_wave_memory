import { type ReactNode, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRightIcon, AlertTriangleIcon, DatabaseIcon, TagsIcon, UsersIcon, WavesIcon } from 'lucide-react'

import { getInjectionMetrics, getRecentErrors, getSystemStatus, type ErrorPayload, type InjectionMetricsPayload, type SystemPayload } from '@/api/system'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { InjectionTrendCard } from '@/pages/dashboard/InjectionTrendCard'
import { SystemHealthCard } from '@/pages/dashboard/SystemHealthCard'

interface DashboardState {
  system?: SystemPayload
  metrics?: InjectionMetricsPayload
  errors?: ErrorPayload
}

function formatNumber(value: unknown): string {
  const number = Number(value)
  if (!Number.isFinite(number)) {
    return '-'
  }
  return new Intl.NumberFormat('zh-CN').format(number)
}

function formatStorage(value: unknown): string {
  const mb = Number(value)
  if (!Number.isFinite(mb) || mb <= 0) {
    return '-'
  }
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(1)} MB`
}

function metricSummary(metrics: InjectionMetricsPayload | undefined, key: string, field: 'sum' | 'avg'): number {
  const value = metrics?.summary?.[key]
  if (typeof value === 'object' && value !== null && field in value) {
    const number = Number((value as Record<string, unknown>)[field])
    return Number.isFinite(number) ? number : 0
  }
  return 0
}

function DashboardSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {['memories', 'vectors', 'tags', 'storage'].map((item) => (
          <Card key={item}>
            <CardHeader>
              <Skeleton className="h-4 w-24" />
            </CardHeader>
            <CardContent>
              <Skeleton className="h-8 w-32" />
            </CardContent>
          </Card>
        ))}
      </div>
      <Skeleton className="h-72 w-full" />
    </div>
  )
}

function KpiCard({ title, value, description, icon: Icon }: { title: string; value: string; description: string; icon: typeof WavesIcon }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <CardDescription>{title}</CardDescription>
          <CardTitle className="text-2xl">{value}</CardTitle>
        </div>
        <Icon className="size-5 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">{description}</p>
      </CardContent>
    </Card>
  )
}

const capabilityActions = [
  { title: '高级检索', route: '/explore', description: '神经云图与高级检索实验台' },
  { title: 'BookLore', route: '/blackbox/book-lore', description: '书设知识索引与 BookLore-only 查询' },
  { title: 'FewShot', route: '/blackbox/fewshot', description: '风格范例库与待审候选' },
  { title: 'Facts', route: '/blackbox/facts', description: '稳定事实关系与注入测试' },
  { title: '人物画像/好感', route: '/blackbox/people', description: 'UserProfile、Affinity 与关系事件' },
  { title: 'FTS5', route: '/blackbox/indexes', description: '全文检索与索引健康' },
  { title: '注入 Trace', route: '/injection', description: '最近注入链路验证' },
  { title: '通道配置', route: '/channels', description: '热参数调优与校验预览' },
]

const actionItems = [
  { title: '无标签记忆数量', route: '/import', description: '导入与 Tag 提取后复核覆盖率' },
  { title: 'Tag 低覆盖', route: '/maintain', description: '进入维护工作台查看 Tag 审计' },
  { title: 'BookLore 索引缺失', route: '/blackbox/book-lore', description: '检查 HNSW 文件、id map 与 count 匹配' },
  { title: 'FewShot 待审候选', route: '/blackbox/fewshot', description: '查看 pending / approved / rejected' },
  { title: '注入通道错误', route: '/injection', description: '从 trace 详情定位错误通道' },
  { title: '配置校验失败', route: '/channels', description: '返回通道热配置校验并调参' },
]

function isStaticRoute(route: string): boolean {
  return route === '/explore' || route === '/maintain'
}

function ActionButton({ route, children }: { route: string; children: ReactNode }) {
  return (
    <Button asChild variant="outline" size="sm">
      {isStaticRoute(route) ? (
        <a href={route}>
          {children}
          <ArrowRightIcon data-icon="inline-end" />
        </a>
      ) : (
        <Link to={route}>
          {children}
          <ArrowRightIcon data-icon="inline-end" />
        </Link>
      )}
    </Button>
  )
}

function statusVariant(value: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (value === 'ok') return 'secondary'
  if (value === 'degraded') return 'outline'
  if (value === 'off') return 'destructive'
  return 'outline'
}

function serviceStatus(system: SystemPayload | undefined, capability: string): string {
  const services = system?.services_health ?? []
  const lower = capability.toLowerCase()
  const matched = services.find((service) => String(service.name ?? '').toLowerCase().includes(lower))
  if (!matched) return 'unknown'
  const value = String(matched.status ?? 'unknown')
  if (value === 'ok') return 'ok'
  if (value === 'disabled' || value === 'off') return 'off'
  return 'degraded'
}

function moduleLabel(key: unknown): string {
  const value = String(key ?? 'unknown')
  const labels: Record<string, string> = {
    total_tokens: '总 token',
    memories_tokens: '主记忆',
    soul_tokens: '灵魂合计',
    belief_tokens: '信念',
    relation_memories_tokens: '关系记忆',
    jargon_tokens: '黑话',
    persona_tokens: '人格画像',
    facts_tokens: '事实',
    concern_tokens: '关切',
    mood_tokens: '情绪',
    book_lore_tokens: '书设知识',
    timeline_tokens: '时间线',
    fts5_tokens: '全文检索',
    fewshot_tokens: '风格范例',
  }
  if (value === 'unknown') return '未知模块'
  return labels[value] ?? value
}

function moduleRoute(key: unknown): string | undefined {
  const value = String(key ?? '')
  const routes: Record<string, string> = {
    book_lore_tokens: '/blackbox/book-lore',
    fewshot_tokens: '/blackbox/fewshot',
    jargon_tokens: '/jargon',
    belief_tokens: '/beliefs',
    facts_tokens: '/blackbox/facts',
  }
  return routes[value]
}

function ModuleRanking({ metrics }: { metrics?: InjectionMetricsPayload }) {
  const ranking = (metrics?.ranking ?? []).slice(0, 8)
  const max = Math.max(...ranking.map((item) => Number(item.sum ?? item.total_tokens ?? 0)), 1)

  return (
    <Card className="h-full flex flex-col">
      <CardHeader>
        <CardTitle>模块消耗排行</CardTitle>
        <CardDescription>按 token 总量聚合</CardDescription>
      </CardHeader>
      <CardContent className="flex-1">
        {ranking.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无排行数据。</p>
        ) : (
          <div className="flex flex-col gap-4">
            {ranking.map((item) => {
              const value = Number(item.sum ?? item.total_tokens ?? 0)
              const ratio = Math.max(4, Math.round((value / max) * 100))
              const key = item.key ?? item.name ?? 'unknown'
              const route = moduleRoute(key)
              const label = moduleLabel(key)
              return (
                <div key={key} className="flex flex-col gap-2">
                  <div className="flex min-w-0 items-center justify-between gap-3 text-sm">
                    {route ? (
                      <Link to={route} className="min-w-0 flex-1 truncate font-medium text-foreground hover:underline">
                        {label}
                      </Link>
                    ) : (
                      <span className="min-w-0 flex-1 truncate font-medium text-foreground">{label}</span>
                    )}
                    <Badge variant="secondary" className="shrink-0 font-mono text-xs">{formatNumber(value)}</Badge>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-muted">
                    <div className="h-full rounded-full bg-primary transition-all duration-500" style={{ width: `${ratio}%` }} />
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function errorLevelLabel(level: unknown): string {
  const value = String(level ?? 'error')
  if (value === 'warning' || value === 'warn') return '警告'
  if (value === 'error') return '错误'
  if (value === 'info') return '信息'
  return value
}

function CapabilityStatusMatrix({ system }: { system?: SystemPayload }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>能力状态矩阵</CardTitle>
        <CardDescription>{'发现 -> 管理 -> 验证 -> 调参'}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {capabilityActions.map((capability) => {
            const status = serviceStatus(system, capability.title)
            return (
              <div key={capability.title} className="flex flex-col gap-3 rounded-lg border p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{capability.title}</p>
                    <p className="text-xs text-muted-foreground">{capability.description}</p>
                  </div>
                  <Badge variant={statusVariant(status)}>{status}</Badge>
                </div>
                <ActionButton route={capability.route}>管理入口</ActionButton>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}

function NeedsAttentionCards({ system, errors }: { system?: SystemPayload; errors?: ErrorPayload }) {
  const tagCoverage = Number(system?.coverage?.tag_pct ?? 0)
  const hasErrors = (errors?.errors ?? []).length > 0
  const enrichedActions = actionItems.map((item) => {
    if (item.title === 'Tag 低覆盖') {
      return { ...item, status: tagCoverage > 0 && tagCoverage < 80 ? 'degraded' : 'unknown' }
    }
    if (item.title === '注入通道错误') {
      return { ...item, status: hasErrors ? 'degraded' : 'unknown' }
    }
    return { ...item, status: 'unknown' }
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle>需要处理</CardTitle>
        <CardDescription>把总览发现的问题直接带到管理页处理。</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {enrichedActions.map((item) => (
            <div key={item.title} className="flex flex-col gap-3 rounded-lg border p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{item.title}</p>
                  <p className="text-xs text-muted-foreground">{item.description}</p>
                </div>
                <Badge variant={statusVariant(item.status)}>{item.status}</Badge>
              </div>
              <ActionButton route={item.route}>处理入口</ActionButton>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

function RecentErrors({ errors }: { errors?: ErrorPayload }) {
  const items = errors?.errors ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle>最近错误</CardTitle>
        <CardDescription>运行时警告/错误摘要</CardDescription>
      </CardHeader>
      <CardContent className="px-6 pb-6">
        {items.length === 0 ? (
          <Alert>
            <AlertTriangleIcon />
            <AlertTitle>暂无错误</AlertTitle>
            <AlertDescription>最近没有记录到运行时错误。</AlertDescription>
          </Alert>
        ) : (
          <div className="flex flex-col gap-4">
            {items.slice(0, 5).map((item, index) => (
              <Alert key={`${index}-${String(item.message ?? item.error ?? '')}`} variant="destructive" className="flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <AlertTriangleIcon className="size-4" />
                  <AlertTitle className="font-semibold">{errorLevelLabel(item.level ?? item.type)}</AlertTitle>
                </div>
                <AlertDescription className="w-full">
                  <pre className="mt-2 max-h-48 w-full overflow-auto rounded-md bg-destructive-foreground/10 p-3 font-mono text-xs text-destructive whitespace-pre-wrap break-all leading-relaxed">
                    {String(item.message ?? item.error ?? JSON.stringify(item))}
                  </pre>
                </AlertDescription>
              </Alert>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function DashboardPage() {
  const [data, setData] = useState<DashboardState>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    async function load() {
      setLoading(true)
      setError('')
      try {
        const [system, metrics, errors] = await Promise.all([getSystemStatus(), getInjectionMetrics('7d'), getRecentErrors()])
        if (alive) {
          setData({ system, metrics, errors })
        }
      } catch (err) {
        if (alive) {
          setError(err instanceof Error ? err.message : '总览仪表盘加载失败')
        }
      } finally {
        if (alive) {
          setLoading(false)
        }
      }
    }
    void load()
    return () => {
      alive = false
    }
  }, [])

  const storageWarning = Number(data.system?.db_size_mb ?? 0) > 2048
  const kpis = useMemo(
    () => [
      {
        title: '记忆总量',
        value: formatNumber(data.system?.memories?.total),
        description: `向量覆盖 ${data.system?.coverage?.vector_pct ?? 0}%`,
        icon: WavesIcon,
      },
      {
        title: '标签覆盖率',
        value: `${data.system?.coverage?.tag_pct ?? 0}%`,
        description: `已打标 ${formatNumber(data.system?.memories?.with_tags)}/${formatNumber(data.system?.memories?.total)} 条 · 标签词库 ${formatNumber(data.system?.tags?.total)} 个`,
        icon: TagsIcon,
      },
      {
        title: '社交认知',
        value: formatNumber(data.system?.lifecycle?.user_profiles),
        description: `活跃用户 ${formatNumber(data.system?.lifecycle?.active_users)}`,
        icon: UsersIcon,
      },
      {
        title: '数据库体积',
        value: formatStorage(data.system?.db_size_mb),
        description: `共现 ${formatNumber(data.system?.cooccurrence?.nodes)} 节点 / ${formatNumber(data.system?.cooccurrence?.edges)} 边`,
        icon: DatabaseIcon,
      },
    ],
    [data.system]
  )

  if (loading) {
    return <DashboardSkeleton />
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertTriangleIcon />
        <AlertTitle>总览仪表盘加载失败</AlertTitle>
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {kpis.map((item) => (
          <KpiCard key={item.title} {...item} />
        ))}
      </div>

      {storageWarning ? (
        <Alert variant="destructive">
          <AlertTriangleIcon />
          <AlertTitle>数据库体积超过 2GB</AlertTitle>
          <AlertDescription>建议在维护窗口执行清理与备份，避免 SQLite 体积继续膨胀。</AlertDescription>
        </Alert>
      ) : null}

      <CapabilityStatusMatrix system={data.system} />
      <NeedsAttentionCards system={data.system} errors={data.errors} />

      <div className="grid gap-4 lg:grid-cols-3">
        <InjectionTrendCard metrics={data.metrics} />
        <ModuleRanking metrics={data.metrics} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <SystemHealthCard services={data.system?.services_health} />
        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardTitle>注入摘要</CardTitle>
              <CardDescription>近 7 天 token 聚合</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-2">
              <KpiCard title="总 token" value={formatNumber(metricSummary(data.metrics, 'total_tokens', 'sum'))} description="累计值" icon={WavesIcon} />
              <KpiCard title="平均 token" value={formatNumber(metricSummary(data.metrics, 'total_tokens', 'avg'))} description="平均值" icon={WavesIcon} />
            </CardContent>
          </Card>
          <RecentErrors errors={data.errors} />
        </div>
      </div>
    </div>
  )
}
