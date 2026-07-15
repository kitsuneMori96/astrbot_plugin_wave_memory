import { useEffect, useState } from 'react'
import { AlertTriangleIcon } from 'lucide-react'

import {
  getBlackboxBookLoreCommunities,
  getBlackboxBookLoreEntities,
  getBlackboxBookLoreNotes,
  getBlackboxBookLoreRelations,
  getBlackboxBookLoreSummary,
  type BlackboxBookLoreCommunity,
  type BlackboxBookLoreEntity,
  type BlackboxBookLoreNote,
  type BlackboxBookLoreRelation,
  type BlackboxBookLoreSummary,
  type BlackboxListPayload,
} from '@/api/blackbox'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { BlackboxCapabilityPage } from './BlackboxCapabilityPage'

const governance = [
  { label: '影响范围', value: 'BookLore 书设知识召回、book_lore 注入通道和世界观查询。' },
  { label: '生效时机', value: '只读诊断立即展示；索引重建、导入/刷新后续单独实现。' },
  { label: '是否持久化', value: '本页不写入；后续写操作才会改变 BookLore DB 或索引文件。' },
  { label: '是否需要重启', value: '只读查看不需要重启；静态导入配置仍以 AstrBot 配置为准。' },
  { label: '回滚方式', value: '重建索引需二次确认，并保留旧索引备份/重建说明。' },
]

function formatCount(value: unknown): string {
  if (value === undefined || value === null || value === '') {
    return '0'
  }
  return String(value)
}

function textField(item: Record<string, unknown>, key: string, fallback = '-'): string {
  const value = item[key]
  return value === undefined || value === null || value === '' ? fallback : String(value)
}

export function BlackboxBookLorePage() {
  const [summary, setSummary] = useState<BlackboxBookLoreSummary | null>(null)
  const [entitiesPayload, setEntitiesPayload] = useState<BlackboxListPayload<BlackboxBookLoreEntity> | null>(null)
  const [communitiesPayload, setCommunitiesPayload] = useState<BlackboxListPayload<BlackboxBookLoreCommunity> | null>(null)
  const [relationsPayload, setRelationsPayload] = useState<BlackboxListPayload<BlackboxBookLoreRelation> | null>(null)
  const [notesPayload, setNotesPayload] = useState<BlackboxListPayload<BlackboxBookLoreNote> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    async function load() {
      setLoading(true)
      setError('')
      try {
        const [summaryPayload, entityPayload, communityPayload, relationPayload, notePayload] = await Promise.all([
          getBlackboxBookLoreSummary(),
          getBlackboxBookLoreEntities({ limit: 20, offset: 0, sort: 'name' }),
          getBlackboxBookLoreCommunities({ limit: 20, offset: 0, sort: 'title' }),
          getBlackboxBookLoreRelations({ limit: 20, offset: 0, sort: 'source' }),
          getBlackboxBookLoreNotes({ limit: 20, offset: 0, sort: 'title' }),
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
  }, [])

  const entities = entitiesPayload?.items ?? []
  const communities = communitiesPayload?.items ?? []
  const relations = relationsPayload?.items ?? []
  const notes = notesPayload?.items ?? []

  return (
    <div className="flex flex-col gap-6">
      <BlackboxCapabilityPage
        title="BookLore 管理"
        description="世界观/书设知识库，不是群聊记忆，不是人格指令。"
        badges={['只读诊断', '治理配置', '重建索引需二次确认']}
        metrics={[
          { label: '实体数', value: loading ? '加载中' : formatCount(summary?.counts?.entities), description: 'book_entities 总数与最近更新时间。' },
          { label: '关系数', value: loading ? '加载中' : formatCount(summary?.counts?.relations), description: 'book_relations 总数与跨实体连接情况。' },
          { label: '社区数', value: loading ? '加载中' : formatCount(summary?.counts?.communities), description: 'book_communities 可注入世界观摘要数量。' },
          { label: 'notes 数', value: loading ? '加载中' : formatCount(summary?.counts?.notes), description: 'book_notes 原始笔记和来源章节数量。' },
        ]}
        sections={[
          {
            title: '索引健康',
            description: '检查 BookLore 向量索引和 DB 计数是否匹配。',
            items: ['HNSW 文件存在性', 'id map 存在性', 'DB count vs index count', 'source_book 覆盖率'],
          },
          {
            title: 'BookLore-only 查询',
            description: '后续输入 query，只查 BookLore，显示命中和分数。',
            items: ['关键词搜索', '向量测试召回', '命中 community 分数', 'relations 详情预览'],
          },
          {
            title: '后续操作边界',
            description: '危险写操作只列契约，不在本只读切片执行。',
            items: ['重建索引需二次确认', '禁用条目需确认', '删除条目需确认', '导入/刷新需任务记录'],
          },
        ]}
        governance={governance}
        states={['加载中', '读取失败', '暂无数据']}
      />

      {loading ? (
        <Card>
          <CardHeader>
            <CardTitle>BookLore 只读数据加载中</CardTitle>
            <CardDescription>正在读取 /api/blackbox/book-lore/summary 与 entities / communities / relations / notes。</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-2/3" />
          </CardContent>
        </Card>
      ) : error ? (
        <Alert variant="destructive">
          <AlertTriangleIcon />
          <AlertTitle>BookLore 数据读取失败</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : (
        <>
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle>BookLore 数据列表</CardTitle>
                <CardDescription>只读展示 book_entities；下方同步展示 communities / relations / notes。</CardDescription>
              </div>
              <Badge variant="outline">total: {formatCount(entitiesPayload?.total)}</Badge>
            </div>
          </CardHeader>
          <CardContent>
            {entities.length === 0 ? (
              <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">暂无 BookLore entities</div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>id</TableHead>
                    <TableHead>name/title</TableHead>
                    <TableHead>summary</TableHead>
                    <TableHead>source_book</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {entities.map((entity, index) => (
                    <TableRow key={String(entity.id ?? `${entity.name ?? entity.title ?? 'entity'}-${index}`)}>
                      <TableCell className="font-mono text-xs">{textField(entity, 'id')}</TableCell>
                      <TableCell>{textField(entity, 'name', textField(entity, 'title'))}</TableCell>
                      <TableCell className="max-w-xl truncate text-muted-foreground">{textField(entity, 'summary', textField(entity, 'description'))}</TableCell>
                      <TableCell>{textField(entity, 'source_book')}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle>BookLore communities</CardTitle>
                <CardDescription>只读展示 book_communities 世界观摘要。</CardDescription>
              </div>
              <Badge variant="outline">total: {formatCount(communitiesPayload?.total)}</Badge>
            </div>
          </CardHeader>
          <CardContent>
            {communities.length === 0 ? (
              <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">暂无 BookLore communities</div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>id</TableHead>
                    <TableHead>title</TableHead>
                    <TableHead>summary</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {communities.map((community, index) => (
                    <TableRow key={String(community.id ?? `${community.title ?? 'community'}-${index}`)}>
                      <TableCell className="font-mono text-xs">{textField(community, 'id')}</TableCell>
                      <TableCell>{textField(community, 'title')}</TableCell>
                      <TableCell className="max-w-xl truncate text-muted-foreground">{textField(community, 'summary')}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle>BookLore relations</CardTitle>
                <CardDescription>只读展示 book_relations 跨实体关系。</CardDescription>
              </div>
              <Badge variant="outline">total: {formatCount(relationsPayload?.total)}</Badge>
            </div>
          </CardHeader>
          <CardContent>
            {relations.length === 0 ? (
              <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">暂无 BookLore relations</div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>id</TableHead>
                    <TableHead>source</TableHead>
                    <TableHead>relation</TableHead>
                    <TableHead>target</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {relations.map((relation, index) => (
                    <TableRow key={String(relation.id ?? `${relation.source ?? 'relation'}-${index}`)}>
                      <TableCell className="font-mono text-xs">{textField(relation, 'id')}</TableCell>
                      <TableCell>{textField(relation, 'source')}</TableCell>
                      <TableCell>{textField(relation, 'relation')}</TableCell>
                      <TableCell>{textField(relation, 'target')}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle>BookLore notes</CardTitle>
                <CardDescription>只读展示 book_notes 原始笔记与内容片段。</CardDescription>
              </div>
              <Badge variant="outline">total: {formatCount(notesPayload?.total)}</Badge>
            </div>
          </CardHeader>
          <CardContent>
            {notes.length === 0 ? (
              <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">暂无 BookLore notes</div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>id</TableHead>
                    <TableHead>title</TableHead>
                    <TableHead>content</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {notes.map((note, index) => (
                    <TableRow key={String(note.id ?? `${note.title ?? 'note'}-${index}`)}>
                      <TableCell className="font-mono text-xs">{textField(note, 'id')}</TableCell>
                      <TableCell>{textField(note, 'title')}</TableCell>
                      <TableCell className="max-w-xl truncate text-muted-foreground">{textField(note, 'content')}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
        </>
      )}
    </div>
  )
}
