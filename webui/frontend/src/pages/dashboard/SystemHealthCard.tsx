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

function serviceReasonLabel(reason: unknown): string {
  return String(reason ?? '')
    .replace(/tag 向量/g, '标签向量')
    .replace(/tag/g, '标签')
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
          <div className="flex flex-col gap-3">
            {services.map((service, index) => {
              const ok = service.status === 'ok'
              const Icon = ok ? CheckCircle2Icon : AlertCircleIcon
              return (
                <div key={`${service.name}-${index}`} className="flex items-start justify-between gap-3 rounded-lg border p-3">
                  <div className="flex min-w-0 items-start gap-3">
                    <Icon className={ok ? 'mt-0.5 size-4 text-muted-foreground shrink-0' : 'mt-0.5 size-4 text-destructive shrink-0'} />
                    <div className="min-w-0">
                      <p className="text-sm font-medium truncate">{serviceNameLabel(service.name)}</p>
                      {service.reason ? <p className="text-xs text-muted-foreground break-words">{serviceReasonLabel(service.reason)}</p> : null}
                    </div>
                  </div>
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
