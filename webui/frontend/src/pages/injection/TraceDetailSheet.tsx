import { AlertTriangleIcon } from 'lucide-react'

import type { TraceDetailPayload } from '@/api/injection'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'

// 带有语法微着色和大型文本截断保护的高性能 JSON Block
function JsonBlock({ value }: { value: unknown }) {
  const jsonStr = typeof value === 'string' ? value : JSON.stringify(value ?? {}, null, 2)
  
  // 5万字符防卡死硬限制
  if (jsonStr.length > 50000) {
    return (
      <pre className="max-h-64 overflow-auto rounded-lg bg-muted p-3 font-mono text-xs text-muted-foreground whitespace-pre">
        {jsonStr.slice(0, 50000)}
        {"\n\n// [Warning: Payload too large, truncated for render performance]"}
      </pre>
    )
  }

  // 利用正则对常用 JSON 样式做科技感微着色
  // key: 蓝色; string: 绿色; number/boolean: 橙色
  const highlighted = jsonStr
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g, (match) => {
      let cls = 'text-amber-500Dark:text-amber-400' // number, boolean, null 默认橙色
      if (/^"/.test(match)) {
        if (/:$/.test(match)) {
          cls = 'text-blue-500 font-medium' // key 蓝色
        } else {
          cls = 'text-emerald-500 dark:text-emerald-400' // string 绿色
        }
      }
      return `<span class="${cls}">${match}</span>`
    })

  return (
    <pre 
      className="max-h-64 overflow-auto rounded-lg bg-muted/50 dark:bg-muted/30 border border-border/50 p-3 font-mono text-[11px] leading-relaxed text-muted-foreground whitespace-pre overflow-x-auto"
      dangerouslySetInnerHTML={{ __html: highlighted }}
    />
  )
}

function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <Card className="border border-border/50 shadow-sm">
      <CardHeader className="py-4">
        <CardTitle className="text-sm font-semibold text-foreground">{title}</CardTitle>
        {description ? <CardDescription className="text-xs">{description}</CardDescription> : null}
      </CardHeader>
      <CardContent className="pb-4 pt-0">{children}</CardContent>
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
      <SheetContent className="w-full gap-0 sm:max-w-3xl flex flex-col pr-0 sm:pr-2">
        <SheetHeader className="pb-4 border-b shrink-0 pr-6">
          <SheetTitle className="text-lg">Trace 详情</SheetTitle>
          <SheetDescription className="text-xs">{detail?.trace_id ? `trace_id: ${detail.trace_id}` : '请求、预算、通道、命中与反馈'}</SheetDescription>
        </SheetHeader>
        <ScrollArea className="flex-1 pr-6">
          <div className="flex flex-col gap-4 py-6">
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
                  <div className="flex flex-col gap-3">
                    {(detail.channels ?? []).length === 0 ? (
                      <p className="text-xs text-muted-foreground py-2">暂无通道明细。</p>
                    ) : (
                      (detail.channels ?? []).map((channel, index) => (
                        <div key={`${index}-${String(channel.name ?? channel.key ?? '')}`} className="rounded-lg border border-border/50 p-3 bg-card">
                          <div className="mb-2 flex items-center justify-between gap-3">
                            <span className="font-semibold text-sm text-foreground">{String(channel.name ?? channel.key ?? `channel-${index + 1}`)}</span>
                            <Badge variant={String(channel.status ?? '').includes('error') || String(channel.status ?? '').includes('timeout') ? 'destructive' : 'secondary'} className="font-mono text-[10px] uppercase">
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
                  {finalText ? (
                    <pre className="whitespace-pre-wrap rounded-lg bg-muted/50 border border-border/50 p-3 font-mono text-xs text-foreground leading-relaxed break-all max-h-96 overflow-auto">
                      {finalText}
                    </pre>
                  ) : (
                    <p className="text-sm text-muted-foreground">暂无最终文本。</p>
                  )}
                </Section>
                <Section title="反馈">
                  <JsonBlock value={detail.feedback ?? []} />
                </Section>
              </>
            ) : (
              <p className="text-sm text-muted-foreground py-6 text-center">请选择一条 trace。</p>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
