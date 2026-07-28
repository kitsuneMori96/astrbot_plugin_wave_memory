import { useEffect, useState } from 'react'
import { AlertTriangleIcon } from 'lucide-react'

import { getBlackboxIndexesCheck, getBlackboxIndexesSummary, type BlackboxIndexesSummary } from '@/api/blackbox'
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

function healthValue(summary: BlackboxIndexesSummary | null, key: keyof NonNullable<BlackboxIndexesSummary['health']>): string {
  const value = summary?.health?.[key]
  return value === undefined || value === null || value === '' ? 'unknown' : String(value)
}

export function BlackboxIndexesPage() {
  const [summary, setSummary] = useState<BlackboxIndexesSummary | null>(null)
  const [check, setCheck] = useState<BlackboxIndexesSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    async function load() {
      setLoading(true)
      setError('')
      try {
        const [summaryPayload, checkPayload] = await Promise.all([
          getBlackboxIndexesSummary(),
          getBlackboxIndexesCheck(),
        ])
        if (alive) {
          setSummary(summaryPayload)
          setCheck(checkPayload)
        }
      } catch (err) {
        if (alive) {
          setError(err instanceof Error ? err.message : 'Indexes 数据读取失败')
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

  const fts5Health = summary?.health?.fts5_index
  const rows = [
    { name: 'memory vector index', count: summary?.counts?.memories, missing: summary?.counts?.memories_missing_vector, health: healthValue(summary, 'memory_vector_index') },
    { name: 'FTS5 index', count: summary?.counts?.memory_tags, missing: '-', health: fts5Health ?? healthValue(summary, 'fts5_index') },
    { name: 'EPA basis', count: summary?.counts?.tags, missing: summary?.counts?.tags_missing_vector, health: healthValue(summary, 'epa_basis') },
    { name: 'BookLore HNSW index', count: summary?.counts?.book_entities, missing: '-', health: healthValue(summary, 'book_lore_hnsw_index') },
  ]

  return (
    <div className="flex flex-col gap-6">
      <BlackboxCapabilityPage
        title="索引与 FTS5 管理"
        description="向量索引、FTS5、EPA basis、共现图和 BookLore HNSW 的统一健康入口。"
        badges={['只读诊断', '危险操作需二次确认', '只读诊断默认开启']}
        metrics={[
          { label: 'memory vector index', value: loading ? '加载中' : healthValue(summary, 'memory_vector_index'), description: `长期记忆 ${formatValue(summary?.counts?.memories)} 条，缺向量 ${formatValue(summary?.counts?.memories_missing_vector)} 条。` },
          { label: 'FTS5 index', value: loading ? '加载中' : healthValue(summary, 'fts5_index'), description: '全文检索索引可用性与 count 对比。' },
          { label: 'EPA basis', value: loading ? '加载中' : healthValue(summary, 'epa_basis'), description: `Tag ${formatValue(summary?.counts?.tags)} 条，缺向量 ${formatValue(summary?.counts?.tags_missing_vector)} 条。` },
          { label: 'BookLore HNSW index', value: loading ? '加载中' : healthValue(summary, 'book_lore_hnsw_index'), description: `书设实体 ${formatValue(summary?.counts?.book_entities)} 条。` },
        ]}
        sections={summary ? [
          {
            title: '索引状态',
            description: '各索引当前健康状态。',
            items: [
              `Memory vector: ${healthValue(summary, 'memory_vector_index')}`,
              `FTS5: ${healthValue(summary, 'fts5_index')}`,
              `EPA basis: ${healthValue(summary, 'epa_basis')}`,
              `BookLore HNSW: ${healthValue(summary, 'book_lore_hnsw_index')}`,
            ],
          },
          {
            title: '缺失向量',
            description: '缺少特征向量（无法检索）的条目数。',
            items: [
              `记忆缺向量: ${formatValue(summary.counts?.memories_missing_vector)}`,
              `Tag 缺向量: ${formatValue(summary.counts?.tags_missing_vector)}`,
              `记忆总数: ${formatValue(summary.counts?.memories)}`,
              `Tag 总数: ${formatValue(summary.counts?.tags)}`,
            ],
          },
          {
            title: '索引检查',
            description: '综合检查结果。',
            items: check ? [
              `状态: ${check.ok ? '正常' : '异常'}`,
              `消息: ${check.message ?? '-'}`,
            ] : ['检查未完成'],
          },
        ] : [
          { title: '加载中', description: '正在读取索引摘要…', items: ['请稍候'] },
          { title: '加载中', description: '正在读取索引摘要…', items: ['请稍候'] },
          { title: '加载中', description: '正在读取索引摘要…', items: ['请稍候'] },
        ]}
        governance={[
          { label: '影响范围', value: 'memory vector index、tag vector index、FTS5、EPA basis、cooccurrence graph。' },
          { label: '读取模式', value: summary?.readonly ? '只读诊断' : '可写' },
          { label: '生效时机', value: '只读诊断即时展示；重建/re-embed 任务后续实现。' },
          { label: '是否需要重启', value: '只读查看不需要重启；重建是否需重载由后续确定。' },
          { label: '回滚方式', value: '重建必须先 preview，保留旧索引路径。' },
        ]}
      />

      {loading ? (
        <Card>
          <CardHeader>
            <CardTitle>Indexes 只读数据加载中</CardTitle>
            <CardDescription>正在读取 /api/blackbox/indexes/summary 与 /indexes/check。</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-2/3" />
          </CardContent>
        </Card>
      ) : error ? (
        <Alert variant="destructive">
          <AlertTriangleIcon />
          <AlertTitle>Indexes 数据读取失败</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle>索引健康矩阵</CardTitle>
                <CardDescription>{check?.message || '只读诊断完成；重建、修复、清理等写操作必须二次确认。'}</CardDescription>
              </div>
              <Badge variant={check?.ok ? 'secondary' : 'outline'}>{check?.ok ? '只读诊断完成' : 'readonly'}</Badge>
            </div>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>对象</TableHead>
                  <TableHead>DB count</TableHead>
                  <TableHead>missing vector</TableHead>
                  <TableHead>health</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.name}>
                    <TableCell>{row.name}</TableCell>
                    <TableCell>{formatValue(row.count)}</TableCell>
                    <TableCell>{formatValue(row.missing)}</TableCell>
                    <TableCell>{row.health}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
