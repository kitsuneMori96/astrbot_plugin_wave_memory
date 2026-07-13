import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRightIcon, AlertTriangleIcon, DatabaseIcon, TagsIcon, UsersIcon, WavesIcon, CheckCircle2Icon, BookOpenIcon } from 'lucide-react'

import { getChannelConfig, type ChannelConfigPayload } from '@/api/channels'
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
  channels?: ChannelConfigPayload
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
    <Card className="group relative overflow-hidden border-border/60 bg-gradient-to-b from-card to-card/95 shadow-sm hover:border-primary/25 hover:shadow-[0_0_24px_rgba(124,58,237,0.02)] transition-all duration-300">
      <CardHeader className="flex flex-row items-center justify-between gap-3 pb-2">
        <div className="flex flex-col gap-1 min-w-0">
          <CardDescription className="text-xs text-muted-foreground/80 font-medium truncate">{title}</CardDescription>
          <CardTitle className="text-2xl font-semibold font-mono tracking-tight text-foreground truncate">{value}</CardTitle>
        </div>
        <div className="p-2 rounded-xl bg-primary/10 border border-primary/15 text-primary shrink-0 transition-transform duration-300 group-hover:scale-110 group-hover:shadow-[0_0_12px_rgba(124,58,237,0.1)]">
          <Icon className="size-4.5" />
        </div>
      </CardHeader>
      <CardContent>
        <p className="text-xs text-muted-foreground leading-normal">{description}</p>
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
    lore_tokens: '世界知识',
    exp_memories_tokens: '时间线/经历',
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
    lore_tokens: '/blackbox/book-lore',
    book_lore_tokens: '/blackbox/book-lore',
    exp_memories_tokens: '/channels',
    timeline_tokens: '/channels',
    relation_memories_tokens: '/blackbox/people',
    fewshot_tokens: '/blackbox/fewshot',
    jargon_tokens: '/jargon',
    belief_tokens: '/beliefs',
    facts_tokens: '/blackbox/facts',
  }
  return routes[value]
}

function NeedsAttentionCards({ system }: { system?: SystemPayload }) {
  const todos = system?.todos
  const untagged = todos?.untagged_count ?? 0
  const pendingFewShot = todos?.pending_fewshot ?? 0
  const hasErrors = todos?.has_errors ?? false

  const activeTodos = [
    ...(untagged > 0 ? [{
      title: '记忆标签待复核',
      description: `系统检测到有 ${untagged} 条记忆尚未提取任何结构化标签，需进行批量分析处理。`,
      route: '/maintain',
      badge: '标签待审',
      statusClass: 'border-l-4 border-l-violet-500/80',
    }] : []),
    ...(pendingFewShot > 0 ? [{
      title: '风格特征范例待审核',
      description: `风格候选库目前积压了 ${pendingFewShot} 条待审核的 Few-Shot 范例。`,
      route: '/blackbox/fewshot',
      badge: '风格待审',
      statusClass: 'border-l-4 border-l-amber-500/80',
    }] : []),
    ...(hasErrors ? [{
      title: '注入链路错误记录',
      description: '监测到运行时注入链路中包含未处理的严重错误日志，需排查。',
      route: '/injection',
      badge: '故障告警',
      statusClass: 'border-l-4 border-l-destructive/80',
    }] : []),
  ]

  if (activeTodos.length === 0) {
    return (
      <Card className="border-border/60 bg-gradient-to-b from-card to-card/95 shadow-sm overflow-hidden">
        <CardContent className="py-4 px-5 flex items-center gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-500 animate-pulse">
            <CheckCircle2Icon className="size-4" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-semibold text-foreground flex items-center gap-1.5">
              <span>系统状态正常</span>
              <Badge variant="outline" className="text-[9px] font-normal border-emerald-500/20 text-emerald-500 bg-emerald-500/5 px-2 py-0">就绪</Badge>
            </p>
            <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
              当前未检测到无标签记忆、待审风格样本或运行时错误。
            </p>
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="border-border/60 bg-gradient-to-b from-card to-card/95 shadow-sm">
      <CardHeader className="pb-3 flex flex-row items-center justify-between gap-4">
        <div>
          <CardTitle className="text-base font-semibold tracking-tight">系统待办</CardTitle>
          <CardDescription className="text-xs mt-0.5">
            需要介入处理的系统任务和状态审计。
          </CardDescription>
        </div>
        <Badge variant="destructive" className="font-mono text-[10px] py-0.5 px-2 bg-destructive/10 text-destructive border border-destructive/15">
          {activeTodos.length} 项待办
        </Badge>
      </CardHeader>
      <CardContent className="pb-5">
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {activeTodos.map((item) => (
            <div key={item.title} className={`group flex flex-col justify-between gap-4 rounded-xl border border-border/80 bg-muted/5 hover:bg-muted/10 p-4 transition-all duration-300 hover:border-primary/15 ${item.statusClass}`}>
              <div className="flex flex-col gap-1.5">
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm font-semibold tracking-tight text-foreground/90">{item.title}</p>
                  <Badge variant="outline" className="text-[10px] font-normal px-2 py-0.5 shrink-0 bg-background/80">
                    {item.badge}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  {item.description}
                </p>
              </div>
              <Button asChild variant="outline" size="sm" className="w-full h-8 text-xs font-medium justify-between hover:bg-primary/5 hover:text-primary px-3 transition-all duration-300">
                <Link to={item.route} className="flex items-center justify-between w-full">
                  <span>去处理</span>
                  <ArrowRightIcon className="size-3.5 transition-transform duration-300 group-hover:translate-x-0.5" data-icon="inline-end" />
                </Link>
              </Button>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

/* 仅展示可由注入指标与通道配置直接证明的数据 */
interface InjectionBreakdownCardProps {
  metrics?: InjectionMetricsPayload
  channels?: ChannelConfigPayload
}

function InjectionBreakdownCard({ metrics, channels }: InjectionBreakdownCardProps) {
  const ranking = (metrics?.ranking ?? []).slice(0, 5)
  const max = Math.max(...ranking.map((item) => Number(item.sum ?? item.total_tokens ?? 0)), 1)
  const experienceTokens = Number((metrics?.ranking ?? []).find((item) => item.key === 'exp_memories_tokens')?.sum ?? 0)
  const timelineConfig = channels?.current?.channels?.timeline
  const enabled = timelineConfig?.enabled === true

  return (
    <Card className="border-border/60 bg-gradient-to-b from-card to-card/95 shadow-sm">
      <CardHeader className="pb-3">
        <CardTitle className="tracking-tight text-base font-semibold">注入模块消耗</CardTitle>
        <CardDescription className="text-xs">当前所选时间范围内，各注入模块的实际 Token 累计值</CardDescription>
      </CardHeader>

      <CardContent className="flex-1 pb-4">
        {ranking.length === 0 ? (
          <p className="text-xs text-muted-foreground">暂无排行数据。</p>
        ) : (
          <div className="flex flex-col gap-3.5">
            {ranking.map((item) => {
              const value = Number(item.sum ?? item.total_tokens ?? 0)
              const ratio = Math.max(4, Math.round((value / max) * 100))
              const key = item.key ?? item.name ?? 'unknown'
              const route = moduleRoute(key)
              const label = moduleLabel(key)
              return (
                <div key={key} className="flex flex-col gap-1">
                  <div className="flex min-w-0 items-center justify-between gap-3 text-[11px]">
                    {route ? (
                      <Link to={route} className="min-w-0 flex-1 truncate font-semibold text-foreground/80 hover:text-primary transition-colors hover:underline">
                        {label}
                      </Link>
                    ) : (
                      <span className="min-w-0 flex-1 truncate font-semibold text-foreground/70">{label}</span>
                    )}
                    <span className="shrink-0 font-mono font-medium text-muted-foreground/90">{formatNumber(value)} tkn</span>
                  </div>
                  <div className="h-1 overflow-hidden rounded-full bg-muted/40">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-primary via-violet-500 to-indigo-500 shadow-[0_0_6px_rgba(124,58,237,0.2)] transition-all duration-500"
                      style={{ width: `${ratio}%` }}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </CardContent>

      <div className="px-6">
        <div className="border-t border-border/40" />
      </div>

      <CardHeader className="pt-4 pb-2.5 flex flex-row items-center justify-between gap-4">
        <div className="flex-1 min-w-0">
          <CardTitle className="tracking-tight text-sm font-semibold flex items-center gap-1.5">
            <BookOpenIcon className="size-4 text-primary" />
            <span>经历注入通道</span>
          </CardTitle>
          <CardDescription className="text-[10.5px] mt-0.5 leading-relaxed text-muted-foreground/80">
            配置经历通道的单次注入上限与 Token 预算；不代表 facts 表总量。
          </CardDescription>
        </div>
        <Badge variant="outline" className="px-2 py-0.5 text-[9px] font-mono shrink-0">
          {metrics?.range ?? '当前窗口'} 消耗 {formatNumber(experienceTokens)} token
        </Badge>
      </CardHeader>

      <CardContent className="pt-0 pb-5 flex flex-col gap-3.5">
        <div className="grid gap-2 grid-cols-2">
          <div className="rounded-lg border bg-muted/5 p-2 flex flex-col justify-between">
            <span className="text-[9.5px] text-muted-foreground/80 font-medium">通道唤醒</span>
            <span className="mt-0.5 text-[11.5px] font-semibold flex items-center gap-1.5">
              <span className={`size-1.5 rounded-full ${enabled ? 'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.3)]' : 'bg-muted-foreground/30'}`} />
              <span>{enabled ? '自动注入' : '已关闭'}</span>
            </span>
          </div>
          <div className="rounded-lg border bg-muted/5 p-2 flex flex-col justify-between">
            <span className="text-[9.5px] text-muted-foreground/80 font-medium">配置状态</span>
            <span className="mt-0.5 font-mono text-[11px] text-foreground/80">{timelineConfig?.status ? String(timelineConfig.status) : '未返回'}</span>
          </div>
          <div className="rounded-lg border bg-muted/5 p-2 flex flex-col justify-between">
            <span className="text-[9.5px] text-muted-foreground/80 font-medium">注入上限</span>
            <span className="mt-0.5 font-mono text-[11px] font-semibold">{formatNumber(timelineConfig?.max_items ?? 5)} 条</span>
          </div>
          <div className="rounded-lg border bg-muted/5 p-2 flex flex-col justify-between">
            <span className="text-[9.5px] text-muted-foreground/80 font-medium">Token 预算</span>
            <span className="mt-0.5 font-mono text-[11px] font-semibold">{formatNumber(timelineConfig?.token_budget ?? 220)}</span>
          </div>
        </div>

        <Button asChild variant="outline" size="sm" className="group w-full justify-between h-7.5 text-[11px] font-medium hover:bg-primary/5 hover:text-primary transition-all duration-300">
          <Link to="/channels">
            <span>前往通道热配置修改</span>
            <ArrowRightIcon className="size-3.5 transition-transform duration-300 group-hover:translate-x-0.5" data-icon="inline-end" />
          </Link>
        </Button>
      </CardContent>
    </Card>
  )
}

function RecentErrors({ errors }: { errors?: ErrorPayload }) {
  const items = errors?.errors ?? []
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})

  const toggleExpand = (index: number) => {
    setExpanded(prev => ({ ...prev, [index]: !prev[index] }))
  }

  if (items.length === 0) return null // 平时100%零占用，不占大盘任何空白！

  return (
    <Card className="border-border/60 bg-gradient-to-b from-card to-card/95 shadow-sm border-l-4 border-l-destructive/80">
      <CardHeader className="pb-2.5">
        <CardTitle className="tracking-tight text-base font-semibold flex items-center gap-2 text-destructive">
          <AlertTriangleIcon className="size-4.5" />
          <span>检测到系统异常故障</span>
        </CardTitle>
        <CardDescription className="text-xs">注入与认知管道运行时警告、异常与断点记录，建议排查</CardDescription>
      </CardHeader>
      <CardContent className="px-6 pb-5 flex flex-col gap-3">
        {items.slice(0, 2).map((item, index) => {
          const errMsg = String(item.message ?? item.error ?? JSON.stringify(item))
          const isLong = errMsg.length > 80
          const isExpanded = !!expanded[index]
          const displayMsg = !isExpanded && isLong ? `${errMsg.substring(0, 80)}...` : errMsg

          return (
            <div
              key={`${index}-${errMsg}`}
              className="flex flex-col gap-2 rounded-xl border border-destructive/15 bg-destructive-foreground/[0.01] p-3.5 transition-all duration-300"
            >
              <div className="flex items-center justify-between gap-3 w-full">
                <span className="font-semibold text-xs text-destructive/90">{errorLevelLabel(item.level ?? item.type)}</span>
                {isLong && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => toggleExpand(index)}
                    className="h-5.5 px-2 text-[10px] font-semibold text-destructive/80 hover:text-destructive hover:bg-destructive/10 shrink-0"
                  >
                    {isExpanded ? '收起详情' : '展开故障堆栈'}
                  </Button>
                )}
              </div>
              <pre className="mt-1 max-h-48 w-full overflow-auto rounded-md bg-destructive-foreground/[0.03] p-2 font-mono text-[10px] text-destructive/80 whitespace-pre-wrap break-all leading-relaxed transition-all duration-300">
                {displayMsg}
              </pre>
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}

export function DashboardPage() {
  const [data, setData] = useState<DashboardState>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [metricsRange, setMetricsRange] = useState('7d')
  const [metricsLoading, setMetricsLoading] = useState(false)

  // 1. 初始化系统状态、错误日志、通道配置（只执行一次）
  useEffect(() => {
    let alive = true
    async function load() {
      setLoading(true)
      setError('')
      try {
        const [system, errors, channels] = await Promise.all([
          getSystemStatus(),
          getRecentErrors(),
          getChannelConfig().catch(() => undefined),
        ])
        if (alive) {
          setData(prev => ({ ...prev, system, errors, channels }))
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

  // 2. 响应式拉取不同 Range 的注入指标（支持点击无缝热加载）
  useEffect(() => {
    let alive = true
    async function loadMetrics() {
      setMetricsLoading(true)
      try {
        const metrics = await getInjectionMetrics(metricsRange)
        if (alive) {
          setData(prev => ({ ...prev, metrics }))
        }
      } catch (e) {
        console.error('Failed to load metrics:', e)
      } finally {
        if (alive) {
          setMetricsLoading(false)
        }
      }
    }
    void loadMetrics()
    return () => {
      alive = false
    }
  }, [metricsRange])

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
        title: '用户画像记录',
        value: formatNumber(data.system?.lifecycle?.user_profiles),
        description: `有互动记录 ${formatNumber(data.system?.lifecycle?.active_users)} 条`,
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
      {/* 顶部四大 KPI */}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {kpis.map((item) => (
          <KpiCard key={item.title} {...item} />
        ))}
      </div>

      {storageWarning && (
        <Alert variant="destructive">
          <AlertTriangleIcon />
          <AlertTitle>数据库体积超过 2GB</AlertTitle>
          <AlertDescription>建议在维护窗口执行清理与备份，避免 SQLite 体积继续膨胀。</AlertDescription>
        </Alert>
      )}

      {/* 系统待办：高保真状态立线 */}
      <NeedsAttentionCards system={data.system} />

      <div className="grid items-start gap-6 lg:grid-cols-3">
        <InjectionTrendCard
          metrics={data.metrics}
          range={metricsRange}
          onRangeChange={setMetricsRange}
          loading={metricsLoading}
        />
        <InjectionBreakdownCard metrics={data.metrics} channels={data.channels} />
      </div>

      <SystemHealthCard services={data.system?.services_health} summary={data.system?.services_summary} />
      <RecentErrors errors={data.errors} />
    </div>
  )
}
