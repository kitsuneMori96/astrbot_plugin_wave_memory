import { Link } from 'react-router-dom'
import { ArrowRightIcon, BookOpenIcon, BrainCircuitIcon, DatabaseIcon, GitBranchIcon, SearchCheckIcon, UsersIcon } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'

interface CapabilityCard {
  title: string
  route: string
  description: string
  status: string
  badges: string[]
  nextStep: string
  implemented: boolean
  icon: typeof BookOpenIcon
}

const capabilities: CapabilityCard[] = [
  {
    title: 'BookLore',
    route: '/blackbox/book-lore',
    description: '世界观/书设知识库，不是群聊记忆，不是人格指令',
    status: '只读入口 · 等待独立管理页',
    badges: ['只读诊断', '治理配置'],
    nextStep: '已补只读页面：实体、关系、社区、索引健康与 BookLore-only 查询契约。',
    implemented: true,
    icon: BookOpenIcon,
  },
  {
    title: 'FewShot',
    route: '/blackbox/fewshot',
    description: '风格范例库，不是事实记忆，不代表真实发生过',
    status: '只读入口 · 候选/审核页待补',
    badges: ['治理配置', '通道配置'],
    nextStep: '已补只读页面：pending/approved/rejected、漂移检测与测试匹配契约。',
    implemented: true,
    icon: BrainCircuitIcon,
  },
  {
    title: 'Facts',
    route: '/blackbox/facts',
    description: '事实关系管理入口：稳定关系、证据、来源与注入影响。',
    status: '只读入口 · 关系 CRUD 待设计',
    badges: ['只读诊断', '中风险'],
    nextStep: '已补只读页面：事实列表字段、PERSON_ALIAS、facts channel 测试契约。',
    implemented: true,
    icon: GitBranchIcon,
  },
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
            <Badge variant="outline">BookLore | FewShot | Facts | 人物 | 索引 | 学习对象</Badge>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Alert>
            <BrainCircuitIcon />
            <AlertTitle>{'发现 -> 管理 -> 验证 -> 调参'}</AlertTitle>
            <AlertDescription>
              这是 v4.5.0 的黑盒能力总入口。当前卡片以只读入口为主；删除、合并、重建、禁用等危险操作需二次确认并在后续子页面单独实现。
            </AlertDescription>
          </Alert>
          <Separator />
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {capabilities.map((capability) => (
              <CapabilityCardView key={capability.title} capability={capability} />
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
