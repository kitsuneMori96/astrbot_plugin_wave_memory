import { AlertCircleIcon, CheckCircle2Icon } from 'lucide-react'

import type { ServiceHealth } from '@/api/system'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

function serviceNameLabel(name: unknown): string {
  const value = String(name ?? '-')
  const labels: Record<string, string> = {
    Embedding: '向量嵌入',
    MetaThinking: '元思考',
    'Tag 索引': '标签索引',
    'Tag 提取': '标签提取',
    'EPA 基底': 'EPA 情感基底',
  }
  return labels[value] ?? value
}

function serviceStatusLabel(status: unknown): string {
  const value = String(status ?? 'unknown')
  if (value === 'ok') return '正常'
  if (value === 'error') return '错误'
  if (value === 'timeout') return '超时'
  if (value === 'degraded') return '降级'
  if (value === 'unknown') return '未知'
  return value
}

export function SystemHealthCard({ services = [] }: { services?: ServiceHealth[] }) {
  const hasIssues = services.some((service) => service.status !== 'ok')
  const isEmpty = services.length === 0

  return (
    <Card className="h-full flex flex-col">
      <CardHeader>
        <CardTitle>系统健康</CardTitle>
        <CardDescription>核心服务就绪度</CardDescription>
      </CardHeader>
      <CardContent className="flex-1 flex flex-col justify-between">
        {isEmpty ? (
          <p className="text-sm text-muted-foreground p-6">暂无服务健康数据。</p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {services.map((service, index) => {
              const ok = service.status === 'ok'
              const Icon = ok ? CheckCircle2Icon : AlertCircleIcon
              return (
                <div key={`${service.name}-${index}`} className="flex items-center gap-2 rounded-lg border px-3 py-2">
                  <Icon className={ok ? 'size-3.5 text-muted-foreground shrink-0' : 'size-3.5 text-destructive shrink-0'} />
                  <span className="min-w-0 flex-1 truncate text-sm">{serviceNameLabel(service.name)}</span>
                  <Badge variant={ok ? 'secondary' : 'destructive'} className="shrink-0 font-mono text-xs">{serviceStatusLabel(service.status)}</Badge>
                </div>
              )
            })}
          </div>
        )}
        <div className="mt-4 pt-4 border-t flex justify-end">
          <Badge variant={isEmpty ? 'outline' : hasIssues ? 'destructive' : 'secondary'} className="uppercase font-semibold">
            {isEmpty ? '未知' : hasIssues ? '异常' : '健康'}
          </Badge>
        </div>
      </CardContent>
    </Card>
  )
}
