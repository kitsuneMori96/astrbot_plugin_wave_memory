import { type FormEvent, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeftIcon, Loader2Icon, RefreshCwIcon, SearchIcon, EyeIcon } from 'lucide-react'

import { getBlackboxPeople, type BlackboxListPayload, type BlackboxPersonItem } from '@/api/blackbox'
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

function textField(item: BlackboxPersonItem, key: keyof BlackboxPersonItem, fallback = '-'): string {
  const value = item[key]
  return value !== undefined && value !== null && value !== '' ? String(value) : fallback
}

export function BlackboxPeoplePage() {
  const [peoplePayload, setPeoplePayload] = useState<BlackboxListPayload<BlackboxPersonItem> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [searchText, setSearchText] = useState('')
  const [appliedSearch, setAppliedSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [refreshNonce, setRefreshNonce] = useState(0)
  const [selectedPerson, setSelectedPerson] = useState<BlackboxPersonItem | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const limit = 50

  useEffect(() => {
    let alive = true
    async function load() {
      setLoading(true)
      setError('')
      try {
        const payload = await getBlackboxPeople({ limit, offset, search: appliedSearch, sort: 'qq_id' })
        if (alive) {
          setPeoplePayload(payload)
        }
      } catch (err) {
        if (alive) {
          setError(err instanceof Error ? err.message : 'People 数据读取失败')
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
  }, [appliedSearch, offset, refreshNonce])

  function handleSearchSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setOffset(0)
    setAppliedSearch(searchText.trim())
  }

  function handleRefresh() {
    setRefreshNonce((value) => value + 1)
  }

  function handleOpenDetail(person: BlackboxPersonItem) {
    setSelectedPerson(person)
    setDetailOpen(true)
  }

  const people = peoplePayload?.items ?? []
  const withBotId = people.filter((person) => person.bot_id).length
  const aliasRows = people.filter((person) => person.aliases).length
  const total = peoplePayload?.total ?? 0
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
          <h1 className="text-xl font-bold tracking-tight">人物与好感度画像</h1>
          <p className="text-xs text-muted-foreground">诊断与管理 UserProfile 用户画像等级、好感信号累加度（Affinity）和别名，Bot 借此识别聊天对象。</p>
        </div>

        {/* 真实的统计汇总 */}
        <div className="flex flex-wrap gap-2 text-xs">
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-center min-w-[70px]">
            <div className="text-muted-foreground scale-95 origin-center mb-0.5">活跃人物</div>
            <div className="font-semibold">{loading ? '...' : formatValue(peoplePayload?.total)}</div>
          </div>
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-center min-w-[70px]">
            <div className="text-muted-foreground scale-95 origin-center mb-0.5">专属 bot_id</div>
            <div className="font-semibold text-blue-500">{loading ? '...' : formatValue(withBotId)}</div>
          </div>
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-center min-w-[70px]">
            <div className="text-muted-foreground scale-95 origin-center mb-0.5">登记别名</div>
            <div className="font-semibold text-pink-500">{loading ? '...' : formatValue(aliasRows)}</div>
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
                <Input className="pl-8 h-8" value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder="搜索用户 QQ/user_id、昵称、登记别名..." />
              </div>
              <Button disabled={loading} type="submit" size="sm" className="h-8">搜索人物</Button>
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

          {/* 纯粹的人物大表 */}
          {error ? (
            <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-6 text-sm text-destructive">{error}</div>
          ) : loading ? (
            <TableSkeleton />
          ) : people.length === 0 ? (
            <EmptyState />
          ) : (
            <div className="rounded-lg border overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/15">
                    <TableHead>QQ 号/User_ID</TableHead>
                    <TableHead>活跃群昵称</TableHead>
                    <TableHead>登记别名/绰号</TableHead>
                    <TableHead className="w-36">归属群/Group_ID</TableHead>
                    <TableHead className="w-36">对应 bot_id</TableHead>
                    <TableHead className="w-24 text-center">互动累加</TableHead>
                    <TableHead className="w-24 text-center">好感度</TableHead>
                    <TableHead className="w-16 text-right">详情</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {people.map((item, idx) => (
                    <TableRow key={String(item.qq_id ?? item.user_id ?? `person-${idx}`)} className="cursor-pointer hover:bg-muted/5" onClick={() => handleOpenDetail(item)}>
                      <TableCell className="font-mono text-xs">{textField(item, 'qq_id', textField(item, 'user_id'))}</TableCell>
                      <TableCell className="font-medium">{textField(item, 'display_name', textField(item, 'nickname'))}</TableCell>
                      <TableCell className="max-w-xs truncate text-muted-foreground">{textField(item, 'aliases')}</TableCell>
                      <TableCell className="font-mono text-xs">{textField(item, 'group_id')}</TableCell>
                      <TableCell><Badge variant="secondary" className="text-[10px] font-normal">{textField(item, 'bot_id')}</Badge></TableCell>
                      <TableCell className="text-center font-mono text-xs">{formatValue(item.interaction_count ?? item.message_count)}</TableCell>
                      <TableCell className="text-center font-semibold text-emerald-500">{formatValue(item.affection ?? 50)}</TableCell>
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

      {/* 人物画像 Sheet 详情抽屉 */}
      {selectedPerson ? (
        <Sheet open={detailOpen} onOpenChange={setDetailOpen}>
          <SheetContent className="w-[380px] sm:w-[480px]">
            <SheetHeader className="pb-4 border-b">
              <SheetTitle>人物画像详情</SheetTitle>
              <SheetDescription>查看 user_profiles + person_registry 关联细节，合并/绰号改动后续设计。</SheetDescription>
            </SheetHeader>
            <div className="flex flex-col gap-4 py-5 text-xs">
              <div className="grid gap-3 rounded-lg border bg-muted/10 p-3.5">
                <div><span className="text-muted-foreground block mb-0.5">QQ 号/User_ID</span> <span className="font-mono font-medium text-sm">{textField(selectedPerson, 'qq_id', textField(selectedPerson, 'user_id'))}</span></div>
                <div><span className="text-muted-foreground block mb-0.5">活跃群昵称</span> <span className="font-medium text-sm">{textField(selectedPerson, 'display_name', textField(selectedPerson, 'nickname'))}</span></div>
                <div><span className="text-muted-foreground block mb-0.5">群/Group_ID</span> <span className="font-mono font-medium">{textField(selectedPerson, 'group_id')}</span></div>
                <div><span className="text-muted-foreground block mb-0.5">专属 bot_id (BotProfile.db_id，非 QQ号)</span> <Badge variant="secondary" className="font-mono">{textField(selectedPerson, 'bot_id')}</Badge></div>
              </div>

              <div className="grid grid-cols-2 gap-2.5 rounded-lg border bg-muted/10 p-3.5">
                <div><span className="text-muted-foreground block mb-0.5">总消息/互动数</span> <span className="font-semibold text-sm">{formatValue(selectedPerson.interaction_count ?? selectedPerson.message_count)}</span></div>
                <div><span className="text-muted-foreground block mb-0.5">好感度 (Affinity)</span> <span className="font-semibold text-sm text-emerald-500">{formatValue(selectedPerson.affection ?? 50)}</span></div>
                <div className="col-span-2"><span className="text-muted-foreground block mb-0.5">登记别名</span> <Badge variant="outline" className="font-normal">{textField(selectedPerson, 'aliases')}</Badge></div>
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-muted-foreground uppercase text-[10px] tracking-wider font-semibold">自愈性 Meta-Metadata JSON</span>
                <pre className="max-h-48 overflow-auto rounded-lg border bg-muted/30 p-3 text-xs leading-relaxed font-mono">
                  {JSON.stringify(selectedPerson.metadata ?? {}, null, 2)}
                </pre>
              </div>
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
      没有过滤匹配到对应人物画像数据。
    </div>
  )
}
