import { Link } from 'react-router-dom'
import { ArrowRightIcon, BookOpenIcon, BrainCircuitIcon, GitBranchIcon, SearchCheckIcon, UsersIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'

interface CapabilityCard {
  title: string
  route: string
  description: string
  badges: string[]
  icon: typeof BookOpenIcon
}

const capabilities: CapabilityCard[] = [
  {
    title: 'BookLore 书设知识',
    route: '/blackbox/book-lore',
    description: '查看世界观与书设知识库，提供实体、关系、社区的只读诊断。',
    badges: ['只读诊断', '世界观'],
    icon: BookOpenIcon,
  },
  {
    title: 'FewShot 风格范例',
    route: '/blackbox/fewshot',
    description: '查看风格范例候选库，提供范例列表、状态筛选、批准或拒绝。',
    badges: ['治理配置', '风格特征'],
    icon: BrainCircuitIcon,
  },
  {
    title: 'Facts 事实关系',
    route: '/blackbox/facts',
    description: '管理稳定事实关系网络，支持三元组编辑、物理删除和证据深度溯源。',
    badges: ['事实网络', '中风险'],
    icon: GitBranchIcon,
  },
  {
    title: '人物与好感度',
    route: '/blackbox/people',
    description: '查看用户画像登记表、UserProfile 好感评分和互动事件时间线。',
    badges: ['只读诊断', '用户画像'],
    icon: UsersIcon,
  },
  {
    title: '索引与 FTS5 健康',
    route: '/blackbox/indexes',
    description: '检查向量索引、FTS5、EPA basis 健康状态，支持一键物理重建。',
    badges: ['索引检查', '危险操作'],
    icon: SearchCheckIcon,
  },
]

function CapabilityCardView({ capability }: { capability: CapabilityCard }) {
  const Icon = capability.icon
  return (
    <Card className="flex h-full flex-col hover:border-primary/30 hover:shadow-md transition-all duration-300">
      <CardHeader className="flex flex-row items-start justify-between gap-4 pb-2">
        <div className="flex flex-col gap-1">
          <CardTitle className="text-base font-semibold">{capability.title}</CardTitle>
          <CardDescription className="text-xs leading-relaxed text-muted-foreground">
            {capability.description}
          </CardDescription>
        </div>
        <Icon className="size-5 text-muted-foreground shrink-0 mt-0.5" />
      </CardHeader>
      <CardContent className="flex flex-1 flex-col justify-end pt-2">
        <div className="flex flex-wrap gap-1.5">
          {capability.badges.map((badge) => (
            <Badge key={badge} variant={badge.includes('危险') || badge.includes('风险') ? 'destructive' : 'secondary'} className="text-[10px] font-normal px-2 py-0.5">
              {badge}
            </Badge>
          ))}
        </div>
      </CardContent>
      <CardFooter className="pt-3 border-t bg-muted/10">
        <Button asChild variant="ghost" size="sm" className="w-full justify-between h-8 text-xs font-medium hover:bg-primary/5 hover:text-primary px-3">
          <Link to={capability.route} className="flex items-center justify-between w-full">
            <span>进入管理</span>
            <ArrowRightIcon className="size-3.5" data-icon="inline-end" />
          </Link>
        </Button>
      </CardFooter>
    </Card>
  )
}

export function BlackboxHubPage() {
  return (
    <div className="flex flex-col gap-6">
      <Card className="border-border/60">
        <CardHeader className="pb-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex flex-col gap-1.5">
              <CardTitle className="text-lg font-semibold">黑盒管理矩阵</CardTitle>
              <CardDescription className="text-sm">
                集中诊断、审计与管理 WaveMemory 底层异构知识图谱、特征索引和心智范例。
              </CardDescription>
            </div>
            <Badge variant="outline" className="text-xs">v4.5.4 Rework</Badge>
          </div>
        </CardHeader>
        <CardContent>
          <Separator className="mb-6" />
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {capabilities.map((capability) => (
              <CapabilityCardView key={capability.title} capability={capability} />
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
