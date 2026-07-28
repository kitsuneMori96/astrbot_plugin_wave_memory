import { useEffect, useState } from 'react'
import { AlertTriangleIcon } from 'lucide-react'

import { getBlackboxFacts, type BlackboxFactItem, type BlackboxListPayload } from '@/api/blackbox'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { BlackboxCapabilityPage } from './BlackboxCapabilityPage'

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
  const typeCounts: Record<string, number> = {}
  for (const f of facts) {
    const t = String(f.fact_type ?? 'unknown')
    typeCounts[t] = (typeCounts[t] ?? 0) + 1
  }

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
        sections={!loading ? [
          {
            title: '类型分布',
            description: '各类 fact_type 的条目数量。',
            items: Object.entries(typeCounts).length > 0
              ? Object.entries(typeCounts).map(([t, c]) => `${t}: ${c}`)
              : ['暂无 facts 数据'],
          },
          {
            title: '置信度与来源',
            description: 'facts 的置信度分布与证据关联。',
            items: [
              `低置信度 (<0.5): ${lowConfidenceCount}`,
              `有证据来源: ${facts.filter(f => f.source_memory_id != null && f.source_memory_id !== '').length}`,
              `总数: ${formatValue(factsPayload?.total)}`,
            ],
          },
        ] : [
          { title: '加载中', description: '正在读取 Facts…', items: ['请稍候'] },
          { title: '加载中', description: '正在读取 Facts…', items: ['请稍候'] },
        ]}
        governance={[
          { label: '影响范围', value: 'facts 稳定关系事实，不是自由文本记忆。' },
          { label: '读取模式', value: '只读诊断' },
          { label: '生效时机', value: '只读诊断即时展示；编辑/归档后续接入。' },
          { label: '是否需要重启', value: '只读查看不需要重启。' },
          { label: '回滚方式', value: '删除/合并操作需二次确认。' },
        ]}
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
