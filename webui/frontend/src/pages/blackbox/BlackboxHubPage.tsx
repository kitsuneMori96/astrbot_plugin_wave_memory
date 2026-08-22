import { Link } from 'react-router-dom'
import { ArrowRightIcon, BrainCircuitIcon, DatabaseIcon, SearchCheckIcon, UsersIcon } from 'lucide-react'

import { BlackboxBookLorePage } from './BlackboxBookLorePage'
import { BlackboxFactsPage } from './BlackboxFactsPage'
import { BlackboxFewShotPage } from './BlackboxFewShotPage'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

interface CapabilityCard {
  title: string
  route: string
  description: string
  status: string
  badges: string[]
  nextStep: string
  implemented: boolean
  icon: typeof DatabaseIcon
}

const capabilities: CapabilityCard[] = [



  {
    title: '人物与好感',
    route: '/blackbox/people',
    description: '人物画像、UserProfile、Affinity 的统一入口。',
    status: '只读入口 · 画像/好感管理待补',
    badges: ['只读诊断', '治理配置'],
    nextStep: '已补只读页面：人物画像、别名、好感信号、群/机器人维度契约。',
    implemented: true,
    icon: UsersIcon,
  },
  {
    title: '索引与 FTS5',
    route: '/blackbox/indexes',
    description: '向量索引、FTS5、EPA basis 健康入口。',
    status: '只读入口 · 重建属于危险操作',
    badges: ['只读诊断', '危险操作需二次确认'],
    nextStep: '已补只读页面：HNSW/id map/FTS5/EPA basis 健康检查与重建确认契约。',
    implemented: true,
    icon: SearchCheckIcon,
  },
  {
    title: '学习对象',
    route: '/learning-objects',
    description: '学习对象登记、候选、重复项与风险摘要入口。',
    status: '已有页面 · 纳入黑盒管理矩阵',
    badges: ['治理配置', '只读诊断'],
    nextStep: '保留现有学习对象页，并作为黑盒矩阵的已接入能力。',
    implemented: true,
    icon: DatabaseIcon,
  },
]

function CapabilityCardView({ capability }: { capability: CapabilityCard }) {
  const Icon = capability.icon
  return (
    <Card className="flex h-full flex-col">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="flex flex-col gap-2">
          <CardTitle>{capability.title}</CardTitle>
          <CardDescription>{capability.description}</CardDescription>
        </div>
        <Icon className="size-5 text-muted-foreground" />
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-4">
        <div className="flex flex-wrap gap-2">
          {capability.badges.map((badge) => (
            <Badge key={badge} variant={badge.includes('危险') ? 'destructive' : 'secondary'}>
              {badge}
            </Badge>
          ))}
        </div>
        <div className="rounded-lg border bg-muted/30 p-3 text-sm text-muted-foreground">{capability.status}</div>
        <p className="text-sm text-muted-foreground">{capability.nextStep}</p>
      </CardContent>
      <CardFooter className="flex items-center justify-between gap-3">
        <span className="font-mono text-xs text-muted-foreground">{capability.route}</span>
        <Button asChild={capability.implemented} disabled={!capability.implemented} variant="outline" size="sm">
          {capability.implemented ? (
            <Link to={capability.route}>
              进入管理页
              <ArrowRightIcon data-icon="inline-end" />
            </Link>
          ) : (
            <span>后续补齐</span>
          )}
        </Button>
      </CardFooter>
    </Card>
  )
}

export function BlackboxHubPage() {
  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex flex-col gap-2">
              <CardTitle>黑盒管理前端矩阵</CardTitle>
              <CardDescription>v4.5.0 先建立能力总入口，再按能力逐个补管理页。</CardDescription>
            </div>
            <Badge variant="outline">人物 | 索引 | 学习对象 + BookLore/FewShot/Facts 只读摘要</Badge>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Alert>
            <BrainCircuitIcon />
            <AlertTitle>{'发现 -> 管理 -> 验证 -> 调参'}</AlertTitle>
            <AlertDescription>
              黑盒能力总入口。BookLore / FewShot / Facts 为只读诊断摘要（Tab 切换，切到才加载数据）；
              人物与索引有独立管理页。删除、合并、重建等危险操作需二次确认。
            </AlertDescription>
          </Alert>
          <Tabs defaultValue="matrix">
            <TabsList>
              <TabsTrigger value="matrix">能力总览</TabsTrigger>
              <TabsTrigger value="book-lore">BookLore 摘要</TabsTrigger>
              <TabsTrigger value="fewshot">FewShot 摘要</TabsTrigger>
              <TabsTrigger value="facts">Facts 摘要</TabsTrigger>
            </TabsList>
            <TabsContent value="matrix" className="mt-4">
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {capabilities.map((capability) => (
                  <CapabilityCardView key={capability.title} capability={capability} />
                ))}
              </div>
            </TabsContent>
            <TabsContent value="book-lore" className="mt-4">
              <BlackboxBookLorePage embedded />
            </TabsContent>
            <TabsContent value="fewshot" className="mt-4">
              <BlackboxFewShotPage embedded />
            </TabsContent>
            <TabsContent value="facts" className="mt-4">
              <BlackboxFactsPage embedded />
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  )
}
