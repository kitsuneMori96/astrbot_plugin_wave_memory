import { type FormEvent, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeftIcon, Loader2Icon, RefreshCwIcon, SearchIcon, EyeIcon } from 'lucide-react'
import { toast } from 'sonner'

import { getBlackboxFacts, updateBlackboxFact, deleteBlackboxFact, type BlackboxFactItem, type BlackboxListPayload } from '@/api/blackbox'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'

function formatValue(value: unknown): string {
  return value !== undefined && value !== null && value !== '' ? String(value) : '0'
}

function formatConfidence(value: unknown): string {
  const score = Number(value ?? 0)
  return Number.isFinite(score) ? score.toFixed(2) : '0.00'
}

function textField(item: BlackboxFactItem, key: keyof BlackboxFactItem, fallback = '-'): string {
  const value = item[key]
  return value !== undefined && value !== null && value !== '' ? String(value) : fallback
}

export function BlackboxFactsPage() {
  const [factsPayload, setFactsPayload] = useState<BlackboxListPayload<BlackboxFactItem> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [searchText, setSearchText] = useState('')
  const [appliedSearch, setAppliedSearch] = useState('')
  const [factFilter, setFactFilter] = useState('')
  const [offset, setOffset] = useState(0)
  const [refreshNonce, setRefreshNonce] = useState(0)
  const [selectedFact, setSelectedFact] = useState<BlackboxFactItem | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const limit = 50

  useEffect(() => {
    let alive = true
    async function load() {
      setLoading(true)
      setError('')
      try {
        const payload = await getBlackboxFacts({ limit, offset, search: appliedSearch, sort: '-confidence', filter: factFilter })
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
  }, [appliedSearch, factFilter, offset, refreshNonce])

  function handleSearchSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setOffset(0)
    setAppliedSearch(searchText.trim())
  }

  function handleRefresh() {
    setRefreshNonce((value) => value + 1)
  }

  async function handleSaveConfidence(id: number | string, confidence: number) {
    try {
      const res = await updateBlackboxFact(id, { confidence })
      if (res.ok) {
        toast.success('置信度保存成功')
        setDetailOpen(false)
        handleRefresh()
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存置信度失败')
    }
  }

  async function handleDeleteFact(id: number | string) {
    if (!confirm('确定要物理删除这一条 Facts 关系吗？')) return
    try {
      const res = await deleteBlackboxFact(id)
      if (res.ok) {
        toast.success('Facts 关系删除成功')
        setDetailOpen(false)
        handleRefresh()
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '删除 Facts 失败')
    }
  }

  function handleOpenDetail(item: BlackboxFactItem) {
    setSelectedFact(item)
    setDetailOpen(true)
  }

  const facts = factsPayload?.items ?? []
  const aliasCount = facts.filter((fact) => String(fact.fact_type ?? '').includes('PERSON_ALIAS')).length
  const lowConfidenceCount = facts.filter((fact) => Number(fact.confidence ?? 1) < 0.5).length
  const total = factsPayload?.total ?? 0
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
          <h1 className="text-xl font-bold tracking-tight">Facts 事实关系网络</h1>
          <p className="text-xs text-muted-foreground">存储与维护 Bot 互动中形成的三元组关系网络。可由 BDI 自我认知系统唤醒并匹配。</p>
        </div>

        {/* 真实的统计汇总 */}
        <div className="flex flex-wrap gap-2 text-xs">
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-center min-w-[70px]">
            <div className="text-muted-foreground scale-95 origin-center mb-0.5">关系总数</div>
            <div className="font-semibold">{loading ? '...' : formatValue(factsPayload?.total)}</div>
          </div>
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-center min-w-[70px]">
            <div className="text-muted-foreground scale-95 origin-center mb-0.5">当前页别名</div>
            <div className="font-semibold text-pink-500">{loading ? '...' : formatValue(aliasCount)}</div>
          </div>
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-center min-w-[70px]">
            <div className="text-muted-foreground scale-95 origin-center mb-0.5">当前页低置信</div>
            <div className="font-semibold text-amber-500">{loading ? '...' : formatValue(lowConfidenceCount)}</div>
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
                <Input className="pl-8 h-8" value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder="搜索 Facts 关系(subject / predicate / object)..." />
              </div>
              <select className="h-8 rounded-lg border bg-background px-2 text-xs" value={factFilter} onChange={(e) => { setFactFilter(e.target.value); setOffset(0) }}>
                <option value="">全部事实类型</option>
                <option value="PERSON_ALIAS">PERSON_ALIAS (别名)</option>
                <option value="relation">relation (稳定关系)</option>
                <option value="preference">preference (偏好)</option>
              </select>
              <Button disabled={loading} type="submit" size="sm" className="h-8">搜索 Facts</Button>
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

          {/* 纯粹的关系大表 */}
          {error ? (
            <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-6 text-sm text-destructive">{error}</div>
          ) : loading ? (
            <TableSkeleton />
          ) : facts.length === 0 ? (
            <EmptyState />
          ) : (
            <div className="rounded-lg border overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/15">
                    <TableHead className="w-16">id</TableHead>
                    <TableHead>主体 (Subject)</TableHead>
                    <TableHead>谓词关系 (Predicate)</TableHead>
                    <TableHead>客体对象 (Object)</TableHead>
                    <TableHead className="w-40">事实类型</TableHead>
                    <TableHead className="w-24">置信度</TableHead>
                    <TableHead className="w-24">证据源 ID</TableHead>
                    <TableHead className="w-16 text-right">查看/编辑</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {facts.map((item, idx) => (
                    <TableRow key={String(item.id ?? `${item.subject}-${idx}`)} className="cursor-pointer hover:bg-muted/5" onClick={() => handleOpenDetail(item)}>
                      <TableCell className="font-mono text-xs">{textField(item, 'id')}</TableCell>
                      <TableCell className="font-medium">{textField(item, 'subject')}</TableCell>
                      <TableCell><Badge variant="outline" className="text-[10px] font-normal">{textField(item, 'predicate')}</Badge></TableCell>
                      <TableCell className="font-medium">{textField(item, 'object')}</TableCell>
                      <TableCell>
                        <Badge variant="secondary" className="text-[9px] font-normal px-2">
                          {textField(item, 'fact_type')}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{formatConfidence(item.confidence)}</TableCell>
                      <TableCell className="font-mono text-xs">#{textField(item, 'source_memory_id')}</TableCell>
                      <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                        <Button variant="ghost" size="icon-xs" className="hover:text-primary hover:bg-primary/10" onClick={() => handleOpenDetail(item)}>
                          <EyeIcon className="size-3.5" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 真实的 Facts 详情 Sheet 抽屉（不占 1/3 屏幕，仅在点击时弹出） */}
      {selectedFact ? (
        <Sheet open={detailOpen} onOpenChange={setDetailOpen}>
          <SheetContent className="w-[380px] sm:w-[480px]">
            <SheetHeader className="pb-4 border-b">
              <SheetTitle>Facts 关系详情及控制中心</SheetTitle>
              <SheetDescription>查看证据句来源，或物理修改、碎掉这一条关系事实。</SheetDescription>
            </SheetHeader>
            <div className="flex flex-col gap-5 py-5 text-xs">
              <div className="grid gap-3.5 rounded-lg border bg-muted/10 p-3.5">
                <div><span className="text-muted-foreground block mb-0.5">主体 (Subject)</span> <span className="font-medium text-sm">{textField(selectedFact, 'subject')}</span></div>
                <div><span className="text-muted-foreground block mb-0.5">关系 (Predicate)</span> <Badge variant="outline" className="font-normal text-xs">{textField(selectedFact, 'predicate')}</Badge></div>
                <div><span className="text-muted-foreground block mb-0.5">客体 (Object)</span> <span className="font-medium text-sm">{textField(selectedFact, 'object')}</span></div>
                <div><span className="text-muted-foreground block mb-0.5">事实类型 (Type)</span> <Badge variant="secondary" className="font-normal">{textField(selectedFact, 'fact_type')}</Badge></div>
              </div>

              {/* 证据溯源 */}
              <div className="rounded-lg border bg-muted/10 p-3.5 flex flex-col gap-2">
                <div className="font-semibold text-[10px] text-slate-500 uppercase tracking-wider">证据溯源</div>
                <div className="flex items-center justify-between gap-2">
                  <span>源记忆 ID: <span className="font-mono">#{textField(selectedFact, 'source_memory_id')}</span></span>
                  {selectedFact.source_memory_id ? (
                    <Button asChild variant="outline" size="xs">
                      <Link to={`/memories?open=${selectedFact.source_memory_id}`}>查看证据记忆</Link>
                    </Button>
                  ) : null}
                </div>
              </div>

              {/* 置信度微调 & 删除 */}
              {selectedFact.id ? (
                <div className="rounded-lg border border-primary/10 bg-primary/[0.015] p-3.5 flex flex-col gap-4">
                  <div className="font-semibold text-[10px] text-primary uppercase tracking-wider">关系治理动作</div>
                  <div className="flex flex-col gap-1.5">
                    <div className="flex items-center justify-between text-muted-foreground">
                      <span>置信度 (Confidence):</span>
                      <span className="font-mono font-semibold">{formatConfidence(selectedFact.confidence)}</span>
                    </div>
                    <input
                      type="range"
                      min="0.0"
                      max="1.0"
                      step="0.05"
                      className="w-full accent-primary h-1.5 bg-muted rounded-lg appearance-none"
                      value={selectedFact.confidence ?? 0.8}
                      onChange={(e) => setSelectedFact({ ...selectedFact, confidence: parseFloat(e.target.value) })}
                    />
                  </div>
                  <div className="flex gap-2">
                    <Button size="xs" className="flex-1" onClick={() => void handleSaveConfidence(selectedFact.id!, selectedFact.confidence ?? 0.8)}>
                      保存修改
                    </Button>
                    <Button size="xs" variant="destructive" className="flex-1" onClick={() => void handleDeleteFact(selectedFact.id!)}>
                      物理删除事实
                    </Button>
                  </div>
                </div>
              ) : null}
            </div>
          </SheetContent>
        </Sheet>
      ) : null}
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
      没有过滤匹配到对应 Facts 事实三元组。
    </div>
  )
}
