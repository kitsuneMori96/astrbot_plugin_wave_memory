import { useEffect, useMemo, useState } from 'react'
import { AlertTriangleIcon, DatabaseIcon, TagsIcon, UsersIcon, WavesIcon } from 'lucide-react'

import { getInjectionMetrics, getRecentErrors, getSystemStatus, type ErrorPayload, type InjectionMetricsPayload, type SystemPayload } from '@/api/system'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
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
              return (
                <div key={key} className="flex flex-col gap-2">
                  <div className="flex items-center justify-between gap-3 text-sm min-w-0">
                    <span className="truncate font-medium text-foreground min-w-0 flex-1">{moduleLabel(key)}</span>
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
        description: `结构化标签 ${formatNumber(data.system?.tags?.structured)}`,
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

      <div className="grid gap-4 lg:grid-cols-3">
        <InjectionTrendCard metrics={data.metrics} />
        <ModuleRanking metrics={data.metrics} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <SystemHealthCard services={data.system?.services_health} />
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
      </div>

      <RecentErrors errors={data.errors} />
    </div>
  )
}
