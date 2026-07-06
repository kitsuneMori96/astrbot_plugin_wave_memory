import { useEffect, useState } from 'react'
import { AlertTriangleIcon } from 'lucide-react'

import { getBlackboxIndexesCheck, getBlackboxIndexesSummary, type BlackboxIndexesSummary } from '@/api/blackbox'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { BlackboxCapabilityPage } from './BlackboxCapabilityPage'

const governance = [
  { label: '影响范围', value: 'memory vector index、tag vector index、cooccurrence graph、EPA basis、FTS5 index、BookLore HNSW index。' },
  { label: '生效时机', value: '只读诊断默认开启；重建和 re-embed 任务后续实现。' },
  { label: '是否持久化', value: '本页不写入；重建需二次确认。' },
  { label: '是否需要重启', value: '只读检查不需要重启；索引重建是否需重载由后续任务说明。' },
  { label: '回滚方式', value: '后续重建必须先 preview，保留旧索引路径和恢复说明。' },
]

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
        sections={[
          {
            title: '索引状态矩阵',
            description: '集中展示 DB 行数、索引 count 和缺失向量摘要。',
            items: ['DB 行数 vs index count', '缺失向量列表摘要', 'FTS5 可用性检查', 'BookLore HNSW index'],
          },
          {
            title: '重建任务入口',
            description: '重建需二次确认，本切片只声明任务边界。',
            items: ['rebuild preview', 'reembed preview', '单条 re-embed', '批量 re-embed'],
          },
          {
            title: '受管索引对象',
            description: '后续按对象展示检查结果和恢复建议。',
            items: ['tag vector index', 'cooccurrence graph', 'EPA basis', 'FTS5 index'],
          },
        ]}
        governance={governance}
        states={['加载中', '读取失败', '暂无数据']}
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
