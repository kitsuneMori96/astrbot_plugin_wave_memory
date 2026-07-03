import { CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts'

import type { InjectionMetricsPayload } from '@/api/system'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from '@/components/ui/chart'

const chartConfig = {
  total_tokens: { label: '总 token', color: 'var(--chart-1)' },
  memories_tokens: { label: '记忆', color: 'var(--chart-2)' },
  soul_tokens: { label: '灵魂', color: 'var(--chart-3)' },
} satisfies ChartConfig

function formatBucket(value: unknown): string {
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return '-'
  }
  return new Date(seconds * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit' })
}

export function InjectionTrendCard({ metrics }: { metrics?: InjectionMetricsPayload }) {
  const series = metrics?.series ?? []

  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>注入消耗趋势</CardTitle>
        <CardDescription>
          {metrics?.range ? `范围：${metrics.range}` : '最近 7 天'} · 样本 {metrics?.count ?? 0} 次
        </CardDescription>
      </CardHeader>
      <CardContent>
        {series.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无注入指标样本。</p>
        ) : (
          <ChartContainer config={chartConfig} className="h-[260px] w-full">
            <LineChart data={series} margin={{ left: 12, right: 12, top: 12 }}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="bucket_ts" tickFormatter={formatBucket} tickLine={false} axisLine={false} minTickGap={24} />
              <YAxis tickLine={false} axisLine={false} width={48} />
              <ChartTooltip content={<ChartTooltipContent />} />
              <Line dataKey="total_tokens" type="monotone" stroke="var(--color-total_tokens)" strokeWidth={2} dot={false} />
              <Line dataKey="memories_tokens" type="monotone" stroke="var(--color-memories_tokens)" strokeWidth={2} dot={false} />
              <Line dataKey="soul_tokens" type="monotone" stroke="var(--color-soul_tokens)" strokeWidth={2} dot={false} />
            </LineChart>
          </ChartContainer>
        )}
      </CardContent>
    </Card>
  )
}
