import { Link } from 'react-router-dom'
import { ArrowLeftIcon, ShieldCheckIcon } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'

interface MetricItem {
  label: string
  value: string
  description: string
}

interface InfoSection {
  title: string
  description: string
  items: string[]
}

interface GovernanceRow {
  label: string
  value: string
}

interface BlackboxCapabilityPageProps {
  title: string
  description: string
  badges: string[]
  metrics: MetricItem[]
  sections: InfoSection[]
  governance: GovernanceRow[]
}

export function BlackboxCapabilityPage({ title, description, badges, metrics, sections, governance }: BlackboxCapabilityPageProps) {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-2">
          <Button asChild variant="ghost" size="sm" className="w-fit px-0 text-muted-foreground hover:bg-transparent">
            <Link to="/blackbox">
              <ArrowLeftIcon data-icon="inline-start" />
              返回黑盒矩阵
            </Link>
          </Button>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
            {badges.map((badge) => (
              <Badge key={badge} variant={badge.includes('危险') || badge.includes('高风险') ? 'destructive' : 'secondary'}>
                {badge}
              </Badge>
            ))}
          </div>
          <p className="max-w-3xl text-sm text-muted-foreground">{description}</p>
        </div>
      </div>

      <Alert>
        <ShieldCheckIcon />
        <AlertTitle>只读优先，写操作分期</AlertTitle>
        <AlertDescription>
          当前页面先建立管理信息架构、风险说明和后续 API 契约占位；禁用、删除、合并、重建等操作只做说明，不在本切片触发写入。
        </AlertDescription>
      </Alert>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {metrics.map((metric) => (
          <Card key={metric.label}>
            <CardHeader className="gap-1">
              <CardDescription>{metric.label}</CardDescription>
              <CardTitle>{metric.value}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">{metric.description}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <div className="flex flex-col gap-4">
          {sections.map((section) => (
            <Card key={section.title}>
              <CardHeader>
                <CardTitle>{section.title}</CardTitle>
                <CardDescription>{section.description}</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="grid gap-2 sm:grid-cols-2">
                  {section.items.map((item) => (
                    <div key={item} className="rounded-lg border bg-muted/30 p-3 text-sm text-muted-foreground">
                      {item}
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardTitle>配置与风险标识</CardTitle>
              <CardDescription>所有黑盒页面统一展示来源、影响和回滚语义。</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {governance.map((row) => (
                <div key={row.label} className="flex flex-col gap-1 rounded-lg border p-3">
                  <span className="text-xs font-medium text-muted-foreground">{row.label}</span>
                  <span className="text-sm">{row.value}</span>
                </div>
              ))}
            </CardContent>
          </Card>

        </div>
      </div>

      <Separator />
    </div>
  )
}
