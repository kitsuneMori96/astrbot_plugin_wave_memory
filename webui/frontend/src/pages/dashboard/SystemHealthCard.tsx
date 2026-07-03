import { AlertCircleIcon, CheckCircle2Icon } from 'lucide-react'

import type { ServiceHealth } from '@/api/system'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export function SystemHealthCard({ services = [] }: { services?: ServiceHealth[] }) {
  const hasIssues = services.some((service) => service.status !== 'ok')

  return (
    <Card>
      <CardHeader>
        <CardTitle>系统健康</CardTitle>
        <CardDescription>核心服务就绪度</CardDescription>
      </CardHeader>
      <CardContent>
        {services.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无服务健康数据。</p>
        ) : (
          <div className="flex flex-col gap-3">
            {services.map((service) => {
              const ok = service.status === 'ok'
              const Icon = ok ? CheckCircle2Icon : AlertCircleIcon
              return (
                <div key={service.name} className="flex items-start justify-between gap-3 rounded-lg border p-3">
                  <div className="flex min-w-0 items-start gap-3">
                    <Icon className={ok ? 'mt-0.5 size-4 text-muted-foreground' : 'mt-0.5 size-4 text-destructive'} />
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{service.name}</p>
                      {service.reason ? <p className="text-xs text-muted-foreground">{service.reason}</p> : null}
                    </div>
                  </div>
                  <Badge variant={ok ? 'secondary' : 'destructive'}>{service.status}</Badge>
                </div>
              )
            })}
            <Badge variant={hasIssues ? 'destructive' : 'secondary'}>{hasIssues ? 'degraded' : 'healthy'}</Badge>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
