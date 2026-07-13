import { Link } from 'react-router-dom'
import { AlertTriangleIcon, ArrowRightIcon } from 'lucide-react'

import type { TraceDetailPayload } from '@/api/injection'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'

function JsonBlock({ value }: { value: unknown }) {
  const jsonStr = typeof value === 'string' ? value : JSON.stringify(value ?? {}, null, 2)
  const display = jsonStr.length > 50000 ? `${jsonStr.slice(0, 50000)}\n\n// [警告：载荷过大，已为渲染性能截断]` : jsonStr

  return (
    <pre className="max-h-64 overflow-auto rounded-lg border bg-muted/50 p-3 font-mono text-xs leading-relaxed text-muted-foreground whitespace-pre overflow-x-auto">
      {display}
    </pre>
  )
}

function channelStatusLabel(status: unknown): string {
  const value = String(status ?? 'unknown')
  if (value === 'ok') return '正常'
  if (value === 'error') return '错误'
  if (value === 'timeout') return '超时'
  if (value === 'empty') return '空'
  if (value === 'disabled') return '已关闭'
  if (value === 'unknown') return '未知'
  return value
}

function asRecords(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => item !== null && typeof item === 'object' && !Array.isArray(item)) : []
}

function fieldText(item: Record<string, unknown>, key: string, fallback = '-'): string {
  const value = item[key]
  return value === undefined || value === null || value === '' ? fallback : String(value)
}

function boolFlagLabel(value: unknown): string {
  return value === true ? 'true' : value === false ? 'false' : '-'
}

function channelName(channel: Record<string, unknown>): string {
  return String(channel.channel ?? channel.name ?? channel.key ?? 'unknown')
}

// v4.5 对象跳转契约：
// memory item -> /memories?id=...
// belief -> /beliefs?id=...
// jargon -> /jargon?id=...
// fewshot -> /blackbox/fewshot?id=...
// book_lore -> /blackbox/book-lore?id=...
// facts -> /blackbox/facts?id=...
function managementRouteForChannel(channel: string, item?: Record<string, unknown>): string | null {
  const id = String(item?.id ?? item?.memory_id ?? item?.belief_id ?? item?.fact_id ?? item?.example_id ?? item?.entity_id ?? '')
  const query = id ? `?id=${encodeURIComponent(id)}` : ''

  if (channel === 'memory' || channel === 'timeline' || channel === 'persona' || channel === 'affinity') return `/memories${query}`
  if (channel === 'belief') return `/beliefs${query}`
  if (channel === 'jargon') return `/jargon${query}`
  if (channel === 'fewshot') return `/blackbox/fewshot${query}`
  if (channel === 'book_lore') return `/blackbox/book-lore${query}`
  if (channel === 'facts') return `/blackbox/facts${query}`
  if (channel === 'fts5') return '/blackbox/indexes'
  return null
}

function jargonHitItems(detail: TraceDetailPayload): Array<Record<string, unknown>> {
  return asRecords(detail.channels).flatMap((channel) => {
    const name = channelName(channel)
    if (name !== 'jargon') {
      return []
    }
    return asRecords(channel.hit_items)
  })
}

function filteredItems(detail: TraceDetailPayload): Array<Record<string, unknown>> {
  return asRecords(detail.channels).flatMap((channel) => {
    const name = channelName(channel)
    return asRecords(channel.filtered_items).map((item) => ({ ...item, channel_name: name }))
  })
}

function hitItemsByChannel(detail: TraceDetailPayload): Array<{ channel: string; item: Record<string, unknown>; route: string | null }> {
  const channelHits = asRecords(detail.channels).flatMap((channel) => {
    const name = channelName(channel)
    return asRecords(channel.hit_items).map((item) => ({ channel: name, item, route: managementRouteForChannel(name, item) }))
  })

  if (channelHits.length) {
    return channelHits
  }

  return asRecords(detail.hits).map((item) => {
    const name = String(item.channel ?? item.source_channel ?? item.type ?? 'memory')
    return { channel: name, item, route: managementRouteForChannel(name, item) }
  })
}

function warningsAndErrors(detail: TraceDetailPayload): Record<string, unknown> {
  const channelErrors = asRecords(detail.channels)
    .filter((channel) => channel.error || String(channel.status ?? '').includes('error') || String(channel.status ?? '').includes('timeout'))
    .map((channel) => ({ channel: channelName(channel), status: channel.status, error: channel.error ?? channel.message }))

  return {
    errors: detail.errors ?? detail.error ?? [],
    warnings: detail.warnings ?? [],
    channel_errors: channelErrors,
  }
}

function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-semibold">{title}</CardTitle>
        {description ? <CardDescription className="text-xs">{description}</CardDescription> : null}
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
  const finalText = String(detail?.final_text ?? detail?.final_injection_text ?? '')
  const jargonItems = detail ? jargonHitItems(detail) : []
  const filteredItemList = detail ? filteredItems(detail) : []
  const manageableHits = detail ? hitItemsByChannel(detail) : []

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full flex-col gap-0 pr-0 sm:max-w-3xl sm:pr-2">
        <SheetHeader className="shrink-0 border-b pb-4 pr-6">
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
                <Alert>
                  <AlertTriangleIcon />
                  <AlertTitle>观测和验证，不承载对象本体管理</AlertTitle>
                  <AlertDescription>本页用于定位注入链路；需要修改对象时，请跳转到对应管理页处理后再回到观测台验证。</AlertDescription>
                </Alert>

                <Section title="请求上下文">
                  <JsonBlock value={detail.request ?? detail.context ?? {}} />
                </Section>
                <Section title="预算">
                  <JsonBlock value={detail.budget ?? {}} />
                </Section>
                <Section title="通道瀑布" description="通道状态、耗时、token 与错误信息">
                  <div className="flex flex-col gap-3">
                    {asRecords(detail.channels).length === 0 ? (
                      <p className="py-2 text-xs text-muted-foreground">暂无通道明细。</p>
                    ) : (
                      asRecords(detail.channels).map((channel, index) => {
                        const name = channelName(channel)
                        const route = managementRouteForChannel(name)
                        return (
                          <div key={`${index}-${name}`} className="flex flex-col gap-3 rounded-lg border bg-card p-3">
                            <div className="flex items-center justify-between gap-3">
                              <span className="text-sm font-semibold text-foreground">{name}</span>
                              <Badge variant={String(channel.status ?? '').includes('error') || String(channel.status ?? '').includes('timeout') ? 'destructive' : 'secondary'}>
                                {channelStatusLabel(channel.status)}
                              </Badge>
                            </div>
                            {route ? (
                              <Button asChild variant="outline" size="sm" className="w-fit">
                                <Link to={route}>
                                  去管理页
                                  <ArrowRightIcon data-icon="inline-end" />
                                </Link>
                              </Button>
                            ) : null}
                            <JsonBlock value={channel} />
                          </div>
                        )
                      })
                    )}
                  </div>
                </Section>
                <Section title="黑话来源" description="jargon channel 命中的词条来源、层级和 reference-only 边界">
                  {jargonItems.length === 0 ? (
                    <p className="py-2 text-xs text-muted-foreground">暂无 jargon 命中。</p>
                  ) : (
                    <div className="flex flex-col gap-2">
                      {jargonItems.map((item, index) => (
                        <div key={`${fieldText(item, 'word')}-${index}`} className="flex flex-col gap-2 rounded-lg border bg-card p-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-medium text-foreground">{fieldText(item, 'word')}</span>
                            <Badge variant="secondary">{fieldText(item, 'source')}</Badge>
                            <Badge variant="outline">source_layer: {fieldText(item, 'source_layer')}</Badge>
                            <Badge variant="outline">reference_only: {boolFlagLabel(item.reference_only)}</Badge>
                            <Badge variant="outline">runtime_match: {boolFlagLabel(item.runtime_match)}</Badge>
                            <Badge variant="outline">matched_by: {fieldText(item, 'matched_by')}</Badge>
                          </div>
                          <p className="text-xs text-muted-foreground">{fieldText(item, 'meaning', fieldText(item, 'preview'))}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </Section>
                <Section title="命中项" description="可跳转对象会显示管理入口；不可跳转对象保留原始 JSON 便于排查">
                  {manageableHits.length === 0 ? (
                    <JsonBlock value={detail.hits ?? []} />
                  ) : (
                    <div className="flex flex-col gap-2">
                      {manageableHits.map(({ channel, item, route }, index) => (
                        <div key={`${channel}-${index}`} className="flex flex-col gap-2 rounded-lg border bg-card p-3">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <Badge variant="secondary">{channel}</Badge>
                            {route ? (
                              <Button asChild variant="outline" size="sm">
                                <Link to={route}>
                                  打开对象管理
                                  <ArrowRightIcon data-icon="inline-end" />
                                </Link>
                              </Button>
                            ) : null}
                          </div>
                          <JsonBlock value={item} />
                        </div>
                      ))}
                    </div>
                  )}
                </Section>
                <Section title="过滤项" description="跳过原因：filtered_items 中记录的过滤通道、原因和预览">
                  {filteredItemList.length === 0 ? (
                    <p className="py-2 text-xs text-muted-foreground">暂无过滤项。</p>
                  ) : (
                    <div className="flex flex-col gap-2">
                      {filteredItemList.map((item, index) => (
                        <div key={`${fieldText(item, 'channel_name')}-${fieldText(item, 'reason')}-${index}`} className="flex flex-col gap-2 rounded-lg border bg-card p-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge variant="secondary">{fieldText(item, 'channel_name')}</Badge>
                            <Badge variant="outline">filter_channel: {fieldText(item, 'filter_channel', fieldText(item, 'channel_name'))}</Badge>
                            <Badge variant="outline">reason: {fieldText(item, 'reason')}</Badge>
                          </div>
                          <p className="text-xs text-muted-foreground">{fieldText(item, 'preview')}</p>
                          <JsonBlock value={item} />
                        </div>
                      ))}
                    </div>
                  )}
                </Section>
                <Section title="最终注入文本">
                  {finalText ? (
                    <pre className="max-h-96 overflow-auto rounded-lg border bg-muted/50 p-3 font-mono text-xs leading-relaxed text-foreground whitespace-pre-wrap break-all">
                      {finalText}
                    </pre>
                  ) : (
                    <p className="text-sm text-muted-foreground">暂无最终文本。</p>
                  )}
                </Section>
                <Section title="错误/警告">
                  <JsonBlock value={warningsAndErrors(detail)} />
                </Section>
                <Section title="反馈与修正入口" description="反馈记录保留在注入观测台；候选提交、审核和晋升历史统一进入学习中心">
                  <div className="flex flex-wrap gap-2">
                    <Button asChild variant="outline" size="sm">
                      <Link to="/learning-center">
                        查看学习中心候选
                        <ArrowRightIcon data-icon="inline-end" />
                      </Link>
                    </Button>
                    <Button asChild variant="outline" size="sm">
                      <Link to="/channels">
                        调整通道配置
                        <ArrowRightIcon data-icon="inline-end" />
                      </Link>
                    </Button>
                    <Button asChild variant="outline" size="sm">
                      <Link to="/dashboard">
                        返回总览复核
                        <ArrowRightIcon data-icon="inline-end" />
                      </Link>
                    </Button>
                  </div>
                  <JsonBlock value={detail.feedback ?? []} />
                </Section>
              </>
            ) : (
              <p className="py-6 text-center text-sm text-muted-foreground">请选择一条 trace。</p>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
