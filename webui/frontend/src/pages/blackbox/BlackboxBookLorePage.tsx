import { type FormEvent, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeftIcon, Loader2Icon, Trash2Icon, RefreshCwIcon, SearchIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  getBlackboxBookLoreCommunities,
  getBlackboxBookLoreEntities,
  getBlackboxBookLoreNotes,
  getBlackboxBookLoreRelations,
  getBlackboxBookLoreSummary,
  deleteBookLoreItem,
  type BlackboxBookLoreCommunity,
  type BlackboxBookLoreEntity,
  type BlackboxBookLoreNote,
  type BlackboxBookLoreRelation,
  type BlackboxBookLoreSummary,
  type BlackboxListPayload,
} from '@/api/blackbox'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

function formatCount(value: unknown): string {
  return value !== undefined && value !== null && value !== '' ? String(value) : '0'
}

function textField(item: Record<string, unknown>, key: string, fallback = '-'): string {
  const value = item[key]
  return value !== undefined && value !== null && value !== '' ? String(value) : fallback
}

export function BlackboxBookLorePage() {
  const [summary, setSummary] = useState<BlackboxBookLoreSummary | null>(null)
  const [entitiesPayload, setEntitiesPayload] = useState<BlackboxListPayload<BlackboxBookLoreEntity> | null>(null)
  const [communitiesPayload, setCommunitiesPayload] = useState<BlackboxListPayload<BlackboxBookLoreCommunity> | null>(null)
  const [relationsPayload, setRelationsPayload] = useState<BlackboxListPayload<BlackboxBookLoreRelation> | null>(null)
  const [notesPayload, setNotesPayload] = useState<BlackboxListPayload<BlackboxBookLoreNote> | null>(null)
  
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [searchText, setSearchText] = useState('')
  const [appliedSearch, setAppliedSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [refreshNonce, setRefreshNonce] = useState(0)
  const [currentTab, setCurrentTab] = useState('entities')
  const limit = 20

  useEffect(() => {
    let alive = true
    async function load() {
      setLoading(true)
      setError('')
      try {
        const [summaryPayload, entityPayload, communityPayload, relationPayload, notePayload] = await Promise.all([
          getBlackboxBookLoreSummary(),
          getBlackboxBookLoreEntities({ limit, offset, search: appliedSearch, sort: 'name' }),
          getBlackboxBookLoreCommunities({ limit, offset, search: appliedSearch, sort: 'title' }),
          getBlackboxBookLoreRelations({ limit, offset, search: appliedSearch, sort: 'source' }),
          getBlackboxBookLoreNotes({ limit, offset, search: appliedSearch, sort: 'title' }),
        ])
        if (alive) {
          setSummary(summaryPayload)
          setEntitiesPayload(entityPayload)
          setCommunitiesPayload(communityPayload)
          setRelationsPayload(relationPayload)
          setNotesPayload(notePayload)
        }
      } catch (err) {
        if (alive) {
          setError(err instanceof Error ? err.message : 'BookLore 数据读取失败')
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

  async function handleDeleteItem(tableType: 'entities' | 'communities' | 'relations' | 'notes', id: number | string) {
    if (!confirm(`确定要从数据库中物理删除该 BookLore ${tableType} 词条吗？`)) return
    try {
      const res = await deleteBookLoreItem(tableType, id)
      if (res.ok) {
        toast.success(`物理删除成功`)
        handleRefresh()
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const entities = entitiesPayload?.items ?? []
  const communities = communitiesPayload?.items ?? []
  const relations = relationsPayload?.items ?? []
  const notes = notesPayload?.items ?? []

  const activeTotal = 
    currentTab === 'entities' ? entitiesPayload?.total ?? 0 :
    currentTab === 'communities' ? communitiesPayload?.total ?? 0 :
    currentTab === 'relations' ? relationsPayload?.total ?? 0 :
    notesPayload?.total ?? 0

  const hasNext = offset + limit < activeTotal

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
          <h1 className="text-xl font-bold tracking-tight">BookLore 世界观书设知识库</h1>
          <p className="text-xs text-muted-foreground">存储与管理导入的背景、世界设定与书设人物。独立于群聊记忆，不占用人格指令预算。</p>
        </div>

        {/* 真实的统计汇总（取代大卡片） */}
        <div className="flex flex-wrap gap-2 text-xs">
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-center min-w-[70px]">
            <div className="text-muted-foreground scale-95 origin-center mb-0.5">实体数</div>
            <div className="font-semibold">{loading ? '...' : formatCount(summary?.counts?.entities)}</div>
          </div>
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-center min-w-[70px]">
            <div className="text-muted-foreground scale-95 origin-center mb-0.5">社区数</div>
            <div className="font-semibold">{loading ? '...' : formatCount(summary?.counts?.communities)}</div>
          </div>
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-center min-w-[70px]">
            <div className="text-muted-foreground scale-95 origin-center mb-0.5">关系数</div>
            <div className="font-semibold">{loading ? '...' : formatCount(summary?.counts?.relations)}</div>
          </div>
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-center min-w-[70px]">
            <div className="text-muted-foreground scale-95 origin-center mb-0.5">Notes</div>
            <div className="font-semibold">{loading ? '...' : formatCount(summary?.counts?.notes)}</div>
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
                <Input className="pl-8 h-8" value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder="搜索 BookLore 实体、关系、社区或原始 notes..." />
              </div>
              <Button disabled={loading} type="submit" size="sm" className="h-8">搜索 BookLore</Button>
              <Button disabled={loading} type="button" variant="outline" size="sm" className="h-8" onClick={handleRefresh}>
                {loading ? <Loader2Icon className="animate-spin size-3.5" /> : <RefreshCwIcon className="size-3.5" />}
              </Button>
            </form>
            <div className="flex gap-1.5 text-xs text-muted-foreground">
              <Button disabled={loading || offset <= 0} type="button" variant="outline" size="xs" onClick={() => setOffset(Math.max(0, offset - limit))}>上一页</Button>
              <Button disabled={loading || !hasNext} type="button" variant="outline" size="xs" onClick={() => setOffset(offset + limit)}>下一页</Button>
              <span className="self-center ml-1.5">当前第 {offset / limit + 1} 页 / 过滤小计: {activeTotal} 条</span>
            </div>
          </div>

          <Separator />

          {/* 纯粹的数据 Tabs 数据大表（占满页面） */}
          {error ? (
            <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-6 text-sm text-destructive">{error}</div>
          ) : (
            <Tabs value={currentTab} onValueChange={(val) => { setCurrentTab(val); setOffset(0) }} className="w-full">
              <TabsList className="h-8 p-0.5 bg-muted/40 mb-3 border">
                <TabsTrigger value="entities" className="text-xs h-7">book_entities 实体列表</TabsTrigger>
                <TabsTrigger value="communities" className="text-xs h-7">book_communities 社区世界观</TabsTrigger>
                <TabsTrigger value="relations" className="text-xs h-7">book_relations 关系网络</TabsTrigger>
                <TabsTrigger value="notes" className="text-xs h-7">book_notes 原始笔记</TabsTrigger>
              </TabsList>

              <TabsContent value="entities" className="mt-0">
                {loading ? <TableSkeleton /> : entities.length === 0 ? <EmptyState /> : (
                  <div className="rounded-lg border overflow-hidden">
                    <Table>
                      <TableHeader>
                        <TableRow className="bg-muted/15">
                          <TableHead className="w-16">id</TableHead>
                          <TableHead className="w-48">名称/标题</TableHead>
                          <TableHead>世界观/人物书设摘要</TableHead>
                          <TableHead className="w-36">来源书设/章节</TableHead>
                          <TableHead className="w-16 text-right">操作</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {entities.map((item, idx) => (
                          <TableRow key={textField(item, 'id') || `e-${idx}`}>
                            <TableCell className="font-mono text-xs">{textField(item, 'id')}</TableCell>
                            <TableCell className="font-medium">{textField(item, 'name', textField(item, 'title'))}</TableCell>
                            <TableCell className="max-w-xl text-muted-foreground truncate">{textField(item, 'summary', textField(item, 'description'))}</TableCell>
                            <TableCell>{textField(item, 'source_book')}</TableCell>
                            <TableCell className="text-right">
                              <Button variant="ghost" size="icon-xs" className="text-destructive hover:bg-destructive/10" onClick={() => void handleDeleteItem('entities', item.id!)}>
                                <Trash2Icon className="size-3" />
                              </Button>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </TabsContent>

              <TabsContent value="communities" className="mt-0">
                {loading ? <TableSkeleton /> : communities.length === 0 ? <EmptyState /> : (
                  <div className="rounded-lg border overflow-hidden">
                    <Table>
                      <TableHeader>
                        <TableRow className="bg-muted/15">
                          <TableHead className="w-16">id</TableHead>
                          <TableHead className="w-56">社区世界观名</TableHead>
                          <TableHead>高阶社区特征描述</TableHead>
                          <TableHead className="w-16 text-right">操作</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {communities.map((item, idx) => (
                          <TableRow key={textField(item, 'id') || `c-${idx}`}>
                            <TableCell className="font-mono text-xs">{textField(item, 'id')}</TableCell>
                            <TableCell className="font-medium">{textField(item, 'title')}</TableCell>
                            <TableCell className="max-w-xl text-muted-foreground truncate">{textField(item, 'summary')}</TableCell>
                            <TableCell className="text-right">
                              <Button variant="ghost" size="icon-xs" className="text-destructive hover:bg-destructive/10" onClick={() => void handleDeleteItem('communities', item.id!)}>
                                <Trash2Icon className="size-3" />
                              </Button>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </TabsContent>

              <TabsContent value="relations" className="mt-0">
                {loading ? <TableSkeleton /> : relations.length === 0 ? <EmptyState /> : (
                  <div className="rounded-lg border overflow-hidden">
                    <Table>
                      <TableHeader>
                        <TableRow className="bg-muted/15">
                          <TableHead className="w-16">id</TableHead>
                          <TableHead>起始实体 (Source)</TableHead>
                          <TableHead>对应关系 (Relation)</TableHead>
                          <TableHead>目标实体 (Target)</TableHead>
                          <TableHead className="w-16 text-right">操作</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {relations.map((item, idx) => (
                          <TableRow key={textField(item, 'id') || `r-${idx}`}>
                            <TableCell className="font-mono text-xs">{textField(item, 'id')}</TableCell>
                            <TableCell className="font-medium">{textField(item, 'source')}</TableCell>
                            <TableCell><Badge variant="outline" className="text-[10px] font-normal">{textField(item, 'relation')}</Badge></TableCell>
                            <TableCell className="font-medium">{textField(item, 'target')}</TableCell>
                            <TableCell className="text-right">
                              <Button variant="ghost" size="icon-xs" className="text-destructive hover:bg-destructive/10" onClick={() => void handleDeleteItem('relations', item.id!)}>
                                <Trash2Icon className="size-3" />
                              </Button>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </TabsContent>

              <TabsContent value="notes" className="mt-0">
                {loading ? <TableSkeleton /> : notes.length === 0 ? <EmptyState /> : (
                  <div className="rounded-lg border overflow-hidden">
                    <Table>
                      <TableHeader>
                        <TableRow className="bg-muted/15">
                          <TableHead className="w-16">id</TableHead>
                          <TableHead className="w-48">笔记大纲</TableHead>
                          <TableHead>笔记内容片段</TableHead>
                          <TableHead className="w-16 text-right">操作</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {notes.map((item, idx) => (
                          <TableRow key={textField(item, 'id') || `n-${idx}`}>
                            <TableCell className="font-mono text-xs">{textField(item, 'id')}</TableCell>
                            <TableCell className="font-medium">{textField(item, 'title')}</TableCell>
                            <TableCell className="max-w-xl text-muted-foreground truncate">{textField(item, 'content')}</TableCell>
                            <TableCell className="text-right">
                              <Button variant="ghost" size="icon-xs" className="text-destructive hover:bg-destructive/10" onClick={() => void handleDeleteItem('notes', item.id!)}>
                                <Trash2Icon className="size-3" />
                              </Button>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </TabsContent>
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
      没有过滤匹配到对应 BookLore 世界观书设数据。可通过 `Maia` 智能导入。
    </div>
  )
}
