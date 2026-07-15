import { useEffect, useState } from 'react'
import { AlertTriangleIcon } from 'lucide-react'

import { getBlackboxFacts, type BlackboxFactItem, type BlackboxListPayload } from '@/api/blackbox'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { BlackboxCapabilityPage } from './BlackboxCapabilityPage'

const governance = [
  { label: '影响范围', value: '稳定事实关系、facts 注入通道和人物/实体关系解释。' },
  { label: '生效时机', value: '只读诊断立即展示；编辑 confidence、归档、删除和合并后续实现。' },
  { label: '是否持久化', value: '本页不写入；删除、合并重复 facts 需二次确认。' },
  { label: '是否需要重启', value: '只读查看不需要重启；通道参数由 /channels 热配置控制。' },
  { label: '回滚方式', value: '后续写操作需保留证据记忆、旧 confidence 和 rollback_hint。' },
]

function formatValue(value: unknown): string {
  if (value === undefined || value === null || value === '') {
    return '0'
  }
  return String(value)
}

function formatConfidence(value: unknown): string {
  const score = Number(value ?? 0)
  return Number.isFinite(score) ? score.toFixed(2) : '0.00'
}

function textField(item: BlackboxFactItem, key: keyof BlackboxFactItem, fallback = '-'): string {
  const value = item[key]
  return value === undefined || value === null || value === '' ? fallback : String(value)
}

export function BlackboxFactsPage() {
  const [factsPayload, setFactsPayload] = useState<BlackboxListPayload<BlackboxFactItem> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    async function load() {
      setLoading(true)
      setError('')
      try {
        const payload = await getBlackboxFacts({ limit: 50, offset: 0, sort: '-confidence' })
        if (alive) {
          setFactsPayload(payload)
        }
      } catch (err) {
        if (alive) {
          setError(err instanceof Error ? err.message : 'Facts 数据读取失败')
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

  const facts = factsPayload?.items ?? []
  const aliasCount = facts.filter((fact) => String(fact.fact_type ?? '').includes('PERSON_ALIAS')).length
  const lowConfidenceCount = facts.filter((fact) => Number(fact.confidence ?? 1) < 0.5).length

  return (
    <div className="flex flex-col gap-6">
      <BlackboxCapabilityPage
        title="Facts / 关系管理"
        description="稳定事实关系与人物/实体关系，不是自由文本记忆。"
        badges={['只读诊断', '治理配置', '删除、合并重复 facts 需二次确认']}
        metrics={[
          { label: 'facts 总数', value: loading ? '加载中' : formatValue(factsPayload?.total), description: '所有稳定关系事实数量。' },
          { label: 'PERSON_ALIAS', value: loading ? '加载中' : formatValue(aliasCount), description: '昵称/别名分流结果与候选数量。' },
          { label: '低置信关系', value: loading ? '加载中' : formatValue(lowConfidenceCount), description: 'confidence 低于治理阈值的关系数量。' },
        ]}
        sections={[
          {
            title: 'facts 列表字段',
            description: '列表必须能解释关系三元组、类型和证据来源。',
            items: ['subject', 'predicate', 'object', 'fact_type', 'confidence', 'source_memory_id'],
          },
          {
            title: '详情与证据',
            description: '详情页后续展示证据记忆、更新时间和关联 tag。',
            items: ['证据记忆', '更新时间', '关联 tag', '来源 source'],
          },
          {
            title: 'facts channel 测试',
            description: '后续输入当前消息，查看 facts channel 会注入什么。',
            items: ['测试消息', '命中 facts', '过滤原因', '注入预览'],
          },
        ]}
        governance={governance}
        states={['加载中', '读取失败', '暂无数据']}
      />

      {loading ? (
        <Card>
          <CardHeader>
            <CardTitle>Facts 只读数据加载中</CardTitle>
            <CardDescription>正在读取 /api/blackbox/facts。</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-2/3" />
          </CardContent>
        </Card>
      ) : error ? (
        <Alert variant="destructive">
          <AlertTriangleIcon />
          <AlertTitle>Facts 数据读取失败</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle>Facts 关系列表</CardTitle>
                <CardDescription>只读展示 facts 三元组；编辑、归档、删除、合并后续做二次确认写操作。</CardDescription>
              </div>
              <Badge variant="outline">total: {formatValue(factsPayload?.total)}</Badge>
            </div>
          </CardHeader>
          <CardContent>
            {facts.length === 0 ? (
              <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">暂无 facts 关系</div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>subject</TableHead>
                    <TableHead>predicate</TableHead>
                    <TableHead>object</TableHead>
                    <TableHead>fact_type</TableHead>
                    <TableHead>confidence</TableHead>
                    <TableHead>source_memory_id</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {facts.map((fact, index) => (
                    <TableRow key={String(fact.id ?? `${fact.subject ?? 'fact'}-${index}`)}>
                      <TableCell>{textField(fact, 'subject')}</TableCell>
                      <TableCell>{textField(fact, 'predicate')}</TableCell>
                      <TableCell>{textField(fact, 'object')}</TableCell>
                      <TableCell>{textField(fact, 'fact_type')}</TableCell>
                      <TableCell>{formatConfidence(fact.confidence)}</TableCell>
                      <TableCell className="font-mono text-xs">{textField(fact, 'source_memory_id')}</TableCell>
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
