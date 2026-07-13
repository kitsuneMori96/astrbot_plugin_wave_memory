import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangleIcon, ArrowLeftIcon, Loader2Icon, RefreshCwIcon } from 'lucide-react'
import { toast } from 'sonner'

import { getBlackboxIndexesCheck, getBlackboxIndexesSummary, rebuildIndexes, type BlackboxIndexesSummary } from '@/api/blackbox'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

function formatValue(value: unknown): string {
  return value !== undefined && value !== null && value !== '' ? String(value) : '0'
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
  const [refreshNonce, setRefreshNonce] = useState(0)
  const [rebuilding, setRebuilding] = useState(false)

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
  }, [refreshNonce])

  function handleRefresh() {
    setRefreshNonce((value) => value + 1)
  }

  async function handleRebuild() {
    if (!confirm('【高风险】确定要一键物理重构记忆/Tag特征向量 HNSW 索引吗？\n后台将清空当前运行期特征，重读长期记忆并重新加载，这在高并发下可能花费数秒。')) return
    setRebuilding(true)
    try {
      const res = await rebuildIndexes()
      if (res.ok) {
        toast.success(res.message || 'HNSW 向量索引重新对齐暖更新完成')
        handleRefresh()
      } else {
        throw new Error(res.message || '重建失败')
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '重建索引失败')
    } finally {
      setRebuilding(false)
    }
  }

  const fts5Health = summary?.health?.fts5_index
  const rows = [
    { name: 'memory vector index (长期记忆特征向量)', count: summary?.counts?.memories, missing: summary?.counts?.memories_missing_vector, health: healthValue(summary, 'memory_vector_index') },
    { name: 'FTS5 index (SQLite 全文检索树)', count: summary?.counts?.memory_tags, missing: '-', health: fts5Health ?? healthValue(summary, 'fts5_index') },
    { name: 'EPA basis (情绪好感度先验标签)', count: summary?.counts?.tags, missing: summary?.counts?.tags_missing_vector, health: healthValue(summary, 'epa_basis') },
    { name: 'BookLore HNSW index (世界观向量关联图)', count: summary?.counts?.book_entities, missing: '-', health: healthValue(summary, 'book_lore_hnsw_index') },
  ]

  return (
    <div className="flex flex-col gap-5">
      {/* 极简精致 Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Button asChild variant="ghost" size="sm" className="w-fit px-0 text-muted-foreground hover:bg-transparent h-6">
            <Link to="/blackbox">
              <ArrowLeftIcon className="size-3.5 mr-1" />
              返回黑盒矩阵
            </Link>
          </Button>
          <h1 className="text-xl font-bold tracking-tight">特征索引与 FTS5 全文检索</h1>
          <p className="text-xs text-muted-foreground">检查底层 SQLite 数据库与 HNSW 内存特征向量的对齐健康度，防止语义关联发生特征漂移。</p>
        </div>
      </div>

      {loading ? (
        <Card className="border-border/60">
          <CardHeader>
            <CardTitle>Indexes 数据加载中</CardTitle>
            <CardDescription>正在读取 /api/blackbox/indexes/summary...</CardDescription>
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
        <div className="flex flex-col gap-5">
          {/* 100% 真实的诊断矩阵 */}
          <Card className="border-border/60">
            <CardHeader className="pb-3 border-b">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <CardTitle className="text-sm font-semibold">索引与 HNSW 对齐健康矩阵</CardTitle>
                  <CardDescription className="text-xs">{check?.message || '只读诊断完毕，无异常特征丢失。'}</CardDescription>
                </div>
                <Badge variant={check?.ok ? 'secondary' : 'outline'} className="text-[10px] font-normal px-2.5">
                  {check?.ok ? '诊断就绪' : 'readonly'}
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="pt-4">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/15">
                    <TableHead>托管索引对象 (Object)</TableHead>
                    <TableHead className="w-36">DB 总记录数</TableHead>
                    <TableHead className="w-40 text-destructive">缺失特征向量数</TableHead>
                    <TableHead className="w-36">健康可用性</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.name}>
                      <TableCell className="font-medium text-xs">{row.name}</TableCell>
                      <TableCell className="font-mono text-xs">{formatValue(row.count)}</TableCell>
                      <TableCell className="font-mono text-xs text-destructive">{formatValue(row.missing)}</TableCell>
                      <TableCell>
                        <Badge variant={row.health === 'present' || row.health === 'inspectable' ? 'default' : 'secondary'} className="text-[9px] font-normal">
                          {row.health}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          {/* 100% 真实的重建命令操盘面板 */}
          <Card className="border-border/60">
            <CardHeader>
              <CardTitle className="text-sm font-semibold">索引自愈与物理重塑</CardTitle>
              <CardDescription className="text-xs">一键重新解密数据库特征、暖重建向量树；缺失特征记忆可以直接跳转到管理器特征计算模块自愈。</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-wrap items-center gap-2">
              <Button disabled={loading} size="sm" type="button" variant="outline" onClick={handleRefresh} className="h-8 text-xs">
                重新检查
              </Button>
              <Button asChild size="sm" variant="outline" className="h-8 text-xs">
                <Link to="/memories?has_vector=false">查看缺向量记忆</Link>
              </Button>
              <Button disabled={loading || rebuilding} size="sm" type="button" onClick={handleRebuild} className="h-8 text-xs">
                {rebuilding ? <Loader2Icon className="animate-spin size-3 mr-1" /> : <RefreshCwIcon className="size-3 mr-1" />}
                {rebuilding ? '正在重建中...' : '一键重建向量索引'}
              </Button>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
