import { type FormEvent, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeftIcon, Loader2Icon, Trash2Icon, RefreshCwIcon, SearchIcon, CheckCircle2Icon, XCircleIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  getBlackboxFewShotExamples,
  getBlackboxFewShotSummary,
  updateBlackboxFewShot,
  deleteBlackboxFewShot,
  type BlackboxFewShotExample,
  type BlackboxFewShotSummary,
  type BlackboxListPayload,
} from '@/api/blackbox'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'

function formatValue(value: unknown): string {
  return value !== undefined && value !== null && value !== '' ? String(value) : '0'
}

function formatScore(value: unknown): string {
  const score = Number(value ?? 0)
  return Number.isFinite(score) ? score.toFixed(2) : '0.00'
}

function textField(item: BlackboxFewShotExample, key: keyof BlackboxFewShotExample, fallback = '-'): string {
  const value = item[key]
  return value !== undefined && value !== null && value !== '' ? String(value) : fallback
}

export function BlackboxFewShotPage() {
  const [summary, setSummary] = useState<BlackboxFewShotSummary | null>(null)
  const [examplesPayload, setExamplesPayload] = useState<BlackboxListPayload<BlackboxFewShotExample> | null>(null)
  
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [searchText, setSearchText] = useState('')
  const [appliedSearch, setAppliedSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('pending')
  const [offset, setOffset] = useState(0)
  const [refreshNonce, setRefreshNonce] = useState(0)
  const limit = 20

  useEffect(() => {
    let alive = true
    async function load() {
      setLoading(true)
      setError('')
      try {
        const [summaryPayload, examplePayload] = await Promise.all([
          getBlackboxFewShotSummary(),
          getBlackboxFewShotExamples({ limit, offset, search: appliedSearch, sort: '-score', filter: statusFilter }),
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
  }, [appliedSearch, offset, refreshNonce, statusFilter])

  function handleSearchSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setOffset(0)
    setAppliedSearch(searchText.trim())
  }

  function handleRefresh() {
    setRefreshNonce((value) => value + 1)
  }

  async function handleUpdateStatus(id: number | string, newStatus: string) {
    try {
      const res = await updateBlackboxFewShot(id, { status: newStatus })
      if (res.ok) {
        toast.success(`该范例已移动到 ${newStatus}`)
        handleRefresh()
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '更新状态失败')
    }
  }

  async function handleDeleteFewShot(id: number | string) {
    if (!confirm('确定要物理删除这一条 FewShot 风格范例吗？')) return
    try {
      const res = await deleteBlackboxFewShot(id)
      if (res.ok) {
        toast.success('删除成功')
        handleRefresh()
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const examples = examplesPayload?.items ?? []
  const total = examplesPayload?.total ?? 0
  const hasNext = offset + limit < total

  return (
    <div className="flex flex-col gap-5">
      {/* 极简精致 Header 控制条 */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Button asChild variant="ghost" size="sm" className="w-fit px-0 text-muted-foreground hover:bg-transparent h-6">
            <Link to="/blackbox">
              <ArrowLeftIcon className="size-3.5 mr-1" />
              返回黑盒矩阵
            </Link>
          </Button>
          <h1 className="text-xl font-bold tracking-tight">FewShot 风格范例候选库</h1>
          <p className="text-xs text-muted-foreground">缓存并审核通过 AI 机制自动捕获或手动塞入的风格范例，调谐大模型的回答温度和风格相似性。</p>
        </div>

        {/* 真实的统计汇总面板 */}
        <div className="flex flex-wrap gap-2 text-xs">
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-center min-w-[70px]">
            <div className="text-muted-foreground scale-95 origin-center mb-0.5">待审核</div>
            <div className="font-semibold text-amber-500">{loading ? '...' : formatValue(summary?.counts?.pending)}</div>
          </div>
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-center min-w-[70px]">
            <div className="text-muted-foreground scale-95 origin-center mb-0.5">已批准</div>
            <div className="font-semibold text-emerald-500">{loading ? '...' : formatValue(summary?.counts?.approved)}</div>
          </div>
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-center min-w-[70px]">
            <div className="text-muted-foreground scale-95 origin-center mb-0.5">已拒绝</div>
            <div className="font-semibold text-destructive">{loading ? '...' : formatValue(summary?.counts?.rejected)}</div>
          </div>
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-center min-w-[70px]">
            <div className="text-muted-foreground scale-95 origin-center mb-0.5">平均分</div>
            <div className="font-semibold">{loading ? '...' : formatScore(summary?.average_score)}</div>
          </div>
        </div>
      </div>

      {/* 100% 真实的搜索与操控平台 */}
      <Card className="border-border/60">
        <CardContent className="p-4 flex flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <form className="flex items-center gap-2 flex-1 min-w-[280px]" onSubmit={handleSearchSubmit}>
              <div className="relative flex-1 max-w-md">
                <SearchIcon className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
                <Input className="pl-8 h-8" value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder="搜索 FewShot 内容、风格 traits、bot_id..." />
              </div>
              <Button disabled={loading} type="submit" size="sm" className="h-8">搜索 FewShot</Button>
              <Button disabled={loading} type="button" variant="outline" size="sm" className="h-8" onClick={handleRefresh}>
                {loading ? <Loader2Icon className="animate-spin size-3.5" /> : <RefreshCwIcon className="size-3.5" />}
              </Button>
            </form>
            <div className="flex gap-1.5 text-xs text-muted-foreground">
              <Button disabled={loading || offset <= 0} type="button" variant="outline" size="xs" onClick={() => setOffset(Math.max(0, offset - limit))}>上一页</Button>
              <Button disabled={loading || !hasNext} type="button" variant="outline" size="xs" onClick={() => setOffset(offset + limit)}>下一页</Button>
              <span className="self-center ml-1.5">当前第 {offset / limit + 1} 页 / 过滤小计: {total} 条</span>
            </div>
          </div>

          <Separator />

          {/* 纯粹的 Tabs 大表，无死文案 */}
          {error ? (
            <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-6 text-sm text-destructive">{error}</div>
          ) : (
            <Tabs value={statusFilter} onValueChange={(val) => { setStatusFilter(val); setOffset(0) }} className="w-full">
              <TabsList className="h-8 p-0.5 bg-muted/40 mb-3 border">
                <TabsTrigger value="pending" className="text-xs h-7 text-amber-500">pending (待审核候选)</TabsTrigger>
                <TabsTrigger value="approved" className="text-xs h-7 text-emerald-500">approved (已批准激活)</TabsTrigger>
                <TabsTrigger value="rejected" className="text-xs h-7 text-muted-foreground">rejected (已拒绝屏蔽)</TabsTrigger>
              </TabsList>

              <div className="mt-0">
                {loading ? <TableSkeleton /> : examples.length === 0 ? <EmptyState /> : (
                  <div className="rounded-lg border overflow-hidden">
                    <Table>
                      <TableHeader>
                        <TableRow className="bg-muted/15">
                          <TableHead className="w-16">id</TableHead>
                          <TableHead>范例对话语料与风格句子 (Content)</TableHead>
                          <TableHead className="w-20">特征得分</TableHead>
                          <TableHead className="w-36">匹配 traits</TableHead>
                          <TableHead className="w-24">分流 Bot</TableHead>
                          <TableHead className="w-40 text-right">人工审核/管理</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {examples.map((item, idx) => (
                          <TableRow key={textField(item, 'id') || `fs-${idx}`}>
                            <TableCell className="font-mono text-xs">{textField(item, 'id')}</TableCell>
                            <TableCell className="max-w-xl text-muted-foreground truncate font-normal leading-relaxed">{textField(item, 'content')}</TableCell>
                            <TableCell className="font-mono text-xs">{formatScore(item.score)}</TableCell>
                            <TableCell><Badge variant="outline" className="text-[9px] font-normal">{textField(item, 'traits')}</Badge></TableCell>
                            <TableCell className="font-mono text-xs">{textField(item, 'bot_id')}</TableCell>
                            <TableCell className="text-right">
                              <div className="flex justify-end gap-1">
                                {item.id ? (
                                  <>
                                    {statusFilter !== 'approved' ? (
                                      <Button variant="ghost" size="xs" className="h-7 text-emerald-500 hover:bg-emerald-500/10" onClick={() => void handleUpdateStatus(item.id!, 'approved')}>
                                        <CheckCircle2Icon className="size-3 mr-0.5" />批准
                                      </Button>
                                    ) : null}
                                    {statusFilter !== 'rejected' ? (
                                      <Button variant="ghost" size="xs" className="h-7 text-destructive hover:bg-destructive/10" onClick={() => void handleUpdateStatus(item.id!, 'rejected')}>
                                        <XCircleIcon className="size-3 mr-0.5" />拒绝
                                      </Button>
                                    ) : null}
                                    <Button variant="ghost" size="xs" className="h-7 text-destructive hover:bg-destructive/10" onClick={() => void handleDeleteFewShot(item.id!)}>
                                      <Trash2Icon className="size-3 mr-0.5" />删除
                                    </Button>
                                  </>
                                ) : '-'}
                              </div>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </div>
            </Tabs>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function TableSkeleton() {
  return (
    <div className="flex flex-col gap-2 pt-2">
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-full" />
    </div>
  )
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed p-10 text-center text-xs text-muted-foreground">
      没有过滤匹配到对应状态的 FewShot 风格范例。
    </div>
  )
}
