import { useEffect, useState } from 'react'
import { AlertTriangleIcon } from 'lucide-react'

import { getBlackboxFewShotExamples, getBlackboxFewShotSummary, type BlackboxFewShotExample, type BlackboxFewShotSummary, type BlackboxListPayload } from '@/api/blackbox'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { BlackboxCapabilityPage } from './BlackboxCapabilityPage'

const governance = [
  { label: '影响范围', value: 'FewShot 风格范例召回、fewshot 注入通道和候选治理。' },
  { label: '生效时机', value: '只读诊断立即展示；批准、拒绝、提取和漂移检测后续接 API。' },
  { label: '是否持久化', value: '本页不写入；批准/拒绝属于后续写操作。' },
  { label: '是否需要重启', value: '只读查看不需要重启；通道启用仍由热配置控制。' },
  { label: '回滚方式', value: '后续写操作必须记录 rollback_hint 和候选来源。' },
]

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

export function BlackboxFewShotPage() {
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
        title="FewShot 管理"
        description="风格范例库，不是事实记忆，不代表真实发生过。"
        badges={['只读诊断', '治理配置', '通道配置']}
        metrics={[
          { label: 'pending / approved / rejected', value: loading ? '加载中' : `${formatValue(summary?.counts?.pending)} / ${formatValue(summary?.counts?.approved)} / ${formatValue(summary?.counts?.rejected)}`, description: '候选、已批准和已拒绝范例数量。' },
          { label: '平均 score', value: loading ? '加载中' : formatScore(summary?.average_score), description: '已批准风格范例的平均质量分。' },
          { label: '最近提取时间', value: '只读未记录', description: 'FewShotService.extract_candidates 最近任务时间后续接入。' },
        ]}
        sections={[
          {
            title: '候选列表',
            description: '展示 content、score、traits、status、bot_id、created_at、approved_at。',
            items: ['status 筛选', 'bot_id 筛选', 'trait 筛选', 'score range 筛选'],
          },
          {
            title: '漂移检测',
            description: '后续输入近期回复，运行 check_drift()，只展示结果不自动改库。',
            items: ['近期回复输入', '风格漂移摘要', '风险 warning', '建议处理动作'],
          },
          {
            title: '测试匹配',
            description: '模拟某 bot_id 会注入哪些 few-shot。',
            items: ['bot_id 输入', 'max_items 预览', '命中示例', '过滤原因'],
          },
        ]}
        governance={governance}
        states={['加载中', '读取失败', '暂无数据']}
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
