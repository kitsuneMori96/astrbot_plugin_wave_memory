import { AlertTriangleIcon } from 'lucide-react'

import type { TraceDetailPayload } from '@/api/injection'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-64 overflow-auto rounded-lg bg-muted p-3 text-xs text-muted-foreground">
      {JSON.stringify(value ?? {}, null, 2)}
    </pre>
  )
}

function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        {description ? <CardDescription>{description}</CardDescription> : null}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

export function TraceDetailSheet({
  open,
  onOpenChange,
  detail,
  loading,
  error,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  detail?: TraceDetailPayload | null
  loading: boolean
  error: string
}) {
  const finalText = detail?.final_text ?? detail?.final_injection_text ?? ''

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>Trace 详情</SheetTitle>
          <SheetDescription>{detail?.trace_id ? `trace_id: ${detail.trace_id}` : '请求、预算、通道、命中与反馈'}</SheetDescription>
        </SheetHeader>
        <ScrollArea className="h-[calc(100svh-7rem)] pr-4">
          <div className="flex flex-col gap-4 py-4">
            {loading ? (
              <>
                <Skeleton className="h-28 w-full" />
                <Skeleton className="h-48 w-full" />
              </>
            ) : error ? (
              <Alert variant="destructive">
                <AlertTriangleIcon />
                <AlertTitle>详情加载失败</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : detail ? (
              <>
                <Section title="请求上下文">
                  <JsonBlock value={detail.request ?? detail.context ?? {}} />
                </Section>
                <Section title="预算">
                  <JsonBlock value={detail.budget ?? {}} />
                </Section>
                <Section title="通道瀑布" description="通道状态、耗时、token 与错误信息">
                  <div className="flex flex-col gap-2">
                    {(detail.channels ?? []).length === 0 ? (
                      <p className="text-sm text-muted-foreground">暂无通道明细。</p>
                    ) : (
                      (detail.channels ?? []).map((channel, index) => (
                        <div key={`${index}-${String(channel.name ?? channel.key ?? '')}`} className="rounded-lg border p-3">
                          <div className="mb-2 flex items-center justify-between gap-3">
                            <span className="font-medium">{String(channel.name ?? channel.key ?? `channel-${index + 1}`)}</span>
                            <Badge variant={String(channel.status ?? '').includes('error') ? 'destructive' : 'secondary'}>
                              {String(channel.status ?? 'unknown')}
                            </Badge>
                          </div>
                          <JsonBlock value={channel} />
                        </div>
                      ))
                    )}
                  </div>
                </Section>
                <Section title="命中项">
                  <JsonBlock value={detail.hits ?? []} />
                </Section>
                <Section title="过滤项">
                  <JsonBlock value={detail.filtered ?? []} />
                </Section>
                <Section title="最终注入文本">
                  {finalText ? <pre className="whitespace-pre-wrap rounded-lg bg-muted p-3 text-sm">{finalText}</pre> : <p className="text-sm text-muted-foreground">暂无最终文本。</p>}
                </Section>
                <Section title="反馈">
                  <JsonBlock value={detail.feedback ?? []} />
                </Section>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">请选择一条 trace。</p>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
