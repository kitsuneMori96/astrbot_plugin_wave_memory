import { useEffect, useState } from 'react'
import { AlertTriangleIcon } from 'lucide-react'

import { getBlackboxPeople, type BlackboxListPayload, type BlackboxPersonItem } from '@/api/blackbox'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { BlackboxCapabilityPage } from './BlackboxCapabilityPage'

const governance = [
  { label: '影响范围', value: 'person_registry、user_profiles、affinity dimensions、relationship events 和 person aliases。' },
  { label: '生效时机', value: '只读诊断立即展示；合并人物、修正昵称、禁用错误别名后续实现。' },
  { label: '是否持久化', value: '本页不写入；合并人物为高风险。' },
  { label: '是否需要重启', value: '只读查看不需要重启；画像生成链路仍由运行时服务控制。' },
  { label: '回滚方式', value: '后续合并必须先 merge-preview，并记录旧 user_id/group_id/bot_id 映射。' },
]

function formatValue(value: unknown): string {
  if (value === undefined || value === null || value === '') {
    return '0'
  }
  return String(value)
}

function textField(item: BlackboxPersonItem, key: keyof BlackboxPersonItem, fallback = '-'): string {
  const value = item[key]
  return value === undefined || value === null || value === '' ? fallback : String(value)
}

export function BlackboxPeoplePage() {
  const [peoplePayload, setPeoplePayload] = useState<BlackboxListPayload<BlackboxPersonItem> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    async function load() {
      setLoading(true)
      setError('')
      try {
        const payload = await getBlackboxPeople({ limit: 50, offset: 0, sort: 'qq_id' })
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
  }, [])

  const people = peoplePayload?.items ?? []
  const withBotId = people.filter((person) => person.bot_id).length
  const aliasRows = people.filter((person) => person.aliases).length

  return (
    <div className="flex flex-col gap-6">
      <BlackboxCapabilityPage
        title="人物与好感管理"
        description="人物画像、UserProfile、Affinity、关系事件与别名的统一只读入口。"
        badges={['只读诊断', '治理配置', '合并人物为高风险']}
        metrics={[
          { label: 'person_registry', value: loading ? '加载中' : formatValue(peoplePayload?.total), description: '人物登记表、别名和实体归属。' },
          { label: 'user_profiles', value: loading ? '加载中' : formatValue(withBotId), description: 'QQ/user_id、display_name、group_id 与 bot_id。' },
          { label: 'affinity dimensions', value: loading ? '加载中' : formatValue(aliasRows), description: '好感维度、score、interaction_count 与 last_seen。' },
        ]}
        sections={[
          {
            title: '人物列表',
            description: 'bot_id 必须明确显示：BotProfile.db_id，不是 QQ 号。',
            items: ['QQ/user_id', 'display_name/nickname', 'group_id', 'BotProfile.db_id，不是 QQ 号'],
          },
          {
            title: '好感与关系摘要',
            description: '展示 interaction_count、last_seen、affinity score 和 dimensions。',
            items: ['interaction_count', 'last_seen', 'affinity score', 'dimensions'],
          },
          {
            title: '关系事件时间线',
            description: '只读查看 relationship events、别名列表和关联记忆入口。',
            items: ['关系事件时间线', '别名列表', '关联记忆', '合并人物为高风险'],
          },
        ]}
        governance={governance}
        states={['加载中', '读取失败', '暂无数据']}
      />

      {loading ? (
        <Card>
          <CardHeader>
            <CardTitle>People 只读数据加载中</CardTitle>
            <CardDescription>正在读取 /api/blackbox/people。</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-2/3" />
          </CardContent>
        </Card>
      ) : error ? (
        <Alert variant="destructive">
          <AlertTriangleIcon />
          <AlertTitle>People 数据读取失败</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle>人物画像列表</CardTitle>
                <CardDescription>只读展示 person_registry + user_profiles 合并视图；BotProfile.db_id，不是 QQ 号。</CardDescription>
              </div>
              <Badge variant="outline">total: {formatValue(peoplePayload?.total)}</Badge>
            </div>
          </CardHeader>
          <CardContent>
            {people.length === 0 ? (
              <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">暂无 people/profile 数据</div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>qq_id</TableHead>
                    <TableHead>display_name</TableHead>
                    <TableHead>nickname</TableHead>
                    <TableHead>group_id</TableHead>
                    <TableHead>bot_id</TableHead>
                    <TableHead>interaction_count</TableHead>
                    <TableHead>aliases</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {people.map((person, index) => (
                    <TableRow key={String(person.qq_id ?? person.user_id ?? `person-${index}`)}>
                      <TableCell className="font-mono text-xs">{textField(person, 'qq_id', textField(person, 'user_id'))}</TableCell>
                      <TableCell>{textField(person, 'display_name')}</TableCell>
                      <TableCell>{textField(person, 'nickname')}</TableCell>
                      <TableCell className="font-mono text-xs">{textField(person, 'group_id')}</TableCell>
                      <TableCell>{textField(person, 'bot_id')}</TableCell>
                      <TableCell>{formatValue(person.interaction_count ?? person.message_count)}</TableCell>
                      <TableCell className="max-w-xs truncate text-muted-foreground">{textField(person, 'aliases')}</TableCell>
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
