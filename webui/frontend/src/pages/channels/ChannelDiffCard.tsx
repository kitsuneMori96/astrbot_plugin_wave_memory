import type { ChannelDiffItem, ChannelValidationPayload } from '@/api/channels'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export function ChannelDiffCard({ diff = [], validation }: { diff?: ChannelDiffItem[]; validation?: ChannelValidationPayload | null }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>差异预览</CardTitle>
        <CardDescription>当前配置与候选配置的字段差异</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {validation ? (
          <Alert variant={validation.ok ? 'default' : 'destructive'}>
            <AlertTitle>{validation.ok ? '配置校验通过' : '配置校验失败'}</AlertTitle>
            <AlertDescription>{(validation.errors ?? []).length > 0 ? validation.errors?.join('；') : (validation.message ?? '可以热应用。')}</AlertDescription>
          </Alert>
        ) : null}
        {diff.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无差异。</p>
        ) : (
          <div className="flex flex-col gap-2">
            {diff.map((item) => (
              <div key={item.path} className="rounded-lg border p-3">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <span className="font-mono text-xs">{item.path}</span>
                  <Badge variant="secondary">已变更</Badge>
                </div>
                <div className="grid gap-2 text-xs md:grid-cols-2">
                  <pre className="overflow-auto rounded-md bg-muted p-2">{JSON.stringify(item.before)}</pre>
                  <pre className="overflow-auto rounded-md bg-muted p-2">{JSON.stringify(item.after)}</pre>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
