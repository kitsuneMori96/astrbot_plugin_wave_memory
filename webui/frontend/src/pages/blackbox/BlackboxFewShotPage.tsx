import { useEffect, useState } from 'react'
import { AlertTriangleIcon } from 'lucide-react'

import { getBlackboxFewShotExamples, getBlackboxFewShotSummary, type BlackboxFewShotExample, type BlackboxFewShotSummary, type BlackboxListPayload } from '@/api/blackbox'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { BlackboxCapabilityPage } from './BlackboxCapabilityPage'

function governance(readonly: boolean) {
  return [
    { label: '影响范围', value: 'FewShot 风格范例召回、fewshot 注入通道和候选治理。' },
    { label: '读取模式', value: readonly ? '只读诊断' : '可写' },
    { label: '生效时机', value: '只读诊断即时展示；批准/拒绝后续接入。' },
    { label: '是否需要重启', value: '只读查看不需要重启。' },
    { label: '回滚方式', value: '写操作必须记录 rollback_hint 和候选来源。' },
  ]
}

function formatValue(value: unknown): string {
  if (value === undefined || value === null || value === '') {
    return '0'
  }
  return String(value)
}

function formatScore(value: unknown): string {
  const score = Number(value ?? 0)
  return Number.isFinite(score) ? score.toFixed(2) : '0.00'
}

function textField(item: BlackboxFewShotExample, key: keyof BlackboxFewShotExample, fallback = '-'): string {
  const value = item[key]
  return value === undefined || value === null || value === '' ? fallback : String(value)
}

export function BlackboxFewShotPage({ embedded = false }: { embedded?: boolean } = {}) {
  const [summary, setSummary] = useState<BlackboxFewShotSummary | null>(null)
  const [examplesPayload, setExamplesPayload] = useState<BlackboxListPayload<BlackboxFewShotExample> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    async function load() {
      setLoading(true)
      setError('')
      try {
        const [summaryPayload, examplePayload] = await Promise.all([
          getBlackboxFewShotSummary(),
          getBlackboxFewShotExamples({ limit: 20, offset: 0, sort: '-score' }),
        ])
        if (alive) {
          setSummary(summaryPayload)
          setExamplesPayload(examplePayload)
        }
      } catch (err) {
        if (alive) {
          setError(err instanceof Error ? err.message : 'FewShot 数据读取失败')
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

  const examples = examplesPayload?.items ?? []

  return (
    <div className="flex flex-col gap-6">
      <BlackboxCapabilityPage
        showBackLink={!embedded}
        title="FewShot 管理"
        description="风格范例库，不是事实记忆，不代表真实发生过。"
        badges={['只读诊断', '治理配置', '通道配置']}
        metrics={[
          { label: 'pending / approved / rejected', value: loading ? '加载中' : `${formatValue(summary?.counts?.pending)} / ${formatValue(summary?.counts?.approved)} / ${formatValue(summary?.counts?.rejected)}`, description: '候选、已批准和已拒绝范例数量。' },
          { label: '平均 score', value: loading ? '加载中' : formatScore(summary?.average_score), description: '已批准风格范例的平均质量分。' },
          { label: '最近提取时间', value: '只读未记录', description: 'FewShotService.extract_candidates 最近任务时间后续接入。' },
        ]}
        sections={summary ? [
          {
            title: '状态分布',
            description: '各审核状态的范例数量。',
            items: [
              `Pending: ${formatValue(summary.counts?.pending)}`,
              `Approved: ${formatValue(summary.counts?.approved)}`,
              `Rejected: ${formatValue(summary.counts?.rejected)}`,
              `总计: ${formatValue(summary.counts?.total)}`,
            ],
          },
          {
            title: '质量概览',
            description: '已批准范例的平均质量分。',
            items: [
              `平均分: ${formatScore(summary.average_score)}`,
              `漂移检测: ${summary.drift_detection ?? '-'}`,
              `安全: ${summary.safety ?? '-'}`,
            ],
          },
        ] : [
          { title: '加载中', description: '正在读取 FewShot 摘要…', items: ['请稍候'] },
          { title: '加载中', description: '正在读取 FewShot 摘要…', items: ['请稍候'] },
        ]}
        governance={governance(summary?.readonly ?? true)}
      />

      {loading ? (
        <Card>
          <CardHeader>
            <CardTitle>FewShot 只读数据加载中</CardTitle>
            <CardDescription>正在读取 /api/blackbox/fewshot/summary 与 examples。</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-2/3" />
          </CardContent>
        </Card>
      ) : error ? (
        <Alert variant="destructive">
          <AlertTriangleIcon />
          <AlertTitle>FewShot 数据读取失败</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle>FewShot 范例列表</CardTitle>
                <CardDescription>只读展示 few_shot_examples；批准、拒绝、删除、编辑后续做二次确认写操作。</CardDescription>
              </div>
              <Badge variant="outline">total: {formatValue(examplesPayload?.total)}</Badge>
            </div>
          </CardHeader>
          <CardContent>
            {examples.length === 0 ? (
              <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">暂无 FewShot examples</div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>id</TableHead>
                    <TableHead>content</TableHead>
                    <TableHead>score</TableHead>
                    <TableHead>traits</TableHead>
                    <TableHead>status</TableHead>
                    <TableHead>bot_id</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {examples.map((example, index) => (
                    <TableRow key={String(example.id ?? `${example.bot_id ?? 'fewshot'}-${index}`)}>
                      <TableCell className="font-mono text-xs">{textField(example, 'id')}</TableCell>
                      <TableCell className="max-w-xl truncate text-muted-foreground">{textField(example, 'content')}</TableCell>
                      <TableCell>{formatScore(example.score)}</TableCell>
                      <TableCell>{textField(example, 'traits')}</TableCell>
                      <TableCell>{textField(example, 'status')}</TableCell>
                      <TableCell>{textField(example, 'bot_id')}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
