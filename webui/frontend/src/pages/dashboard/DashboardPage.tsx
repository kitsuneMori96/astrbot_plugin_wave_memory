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

function ModuleRanking({ metrics }: { metrics?: InjectionMetricsPayload }) {
  const ranking = (metrics?.ranking ?? []).slice(0, 8)
  const max = Math.max(...ranking.map((item) => Number(item.sum ?? item.total_tokens ?? 0)), 1)

  return (
    <Card>
      <CardHeader>
        <CardTitle>模块消耗排行</CardTitle>
        <CardDescription>按 token 总量聚合</CardDescription>
      </CardHeader>
      <CardContent>
        {ranking.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无排行数据。</p>
        ) : (
          <div className="flex flex-col gap-3">
            {ranking.map((item) => {
              const value = Number(item.sum ?? item.total_tokens ?? 0)
              const ratio = Math.max(4, Math.round((value / max) * 100))
              const key = item.key ?? item.name ?? 'unknown'
              return (
                <div key={key} className="flex flex-col gap-1.5">
                  <div className="flex items-center justify-between gap-3 text-sm">
                    <span className="truncate">{key}</span>
                    <Badge variant="secondary">{formatNumber(value)}</Badge>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-muted">
                    <div className="h-full rounded-full bg-primary" style={{ width: `${ratio}%` }} />
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

function RecentErrors({ errors }: { errors?: ErrorPayload }) {
  const items = errors?.errors ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle>最近错误</CardTitle>
        <CardDescription>运行时 warning/error 摘要</CardDescription>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <Alert>
            <AlertTriangleIcon />
            <AlertTitle>暂无错误</AlertTitle>
            <AlertDescription>最近没有记录到运行时错误。</AlertDescription>
          </Alert>
        ) : (
          <div className="flex flex-col gap-3">
            {items.slice(0, 5).map((item, index) => (
              <Alert key={`${index}-${String(item.message ?? item.error ?? '')}`} variant="destructive">
                <AlertTriangleIcon />
                <AlertTitle>{String(item.level ?? item.type ?? 'error')}</AlertTitle>
                <AlertDescription>{String(item.message ?? item.error ?? JSON.stringify(item))}</AlertDescription>
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
          setError(err instanceof Error ? err.message : 'Dashboard 加载失败')
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
        title: 'Tag 覆盖率',
        value: `${data.system?.coverage?.tag_pct ?? 0}%`,
        description: `结构化 Tag ${formatNumber(data.system?.tags?.structured)}`,
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
        <AlertTitle>Dashboard 加载失败</AlertTitle>
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
            <KpiCard title="总 token" value={formatNumber(metricSummary(data.metrics, 'total_tokens', 'sum'))} description="sum" icon={WavesIcon} />
            <KpiCard title="平均 token" value={formatNumber(metricSummary(data.metrics, 'total_tokens', 'avg'))} description="avg" icon={WavesIcon} />
          </CardContent>
        </Card>
      </div>

      <RecentErrors errors={data.errors} />
    </div>
  )
}
