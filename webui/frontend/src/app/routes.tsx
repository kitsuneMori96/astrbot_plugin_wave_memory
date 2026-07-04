import type { ComponentType } from 'react'
import {
  ActivityIcon,
  BookHeartIcon,
  BrainCircuitIcon,
  DatabaseIcon,
  DownloadIcon,
  GaugeIcon,
  GitCompareArrowsIcon,
  HeartIcon,
  MessageSquareWarningIcon,
  Settings2Icon,
  SlidersIcon,
  SmileIcon,
} from 'lucide-react'

import { AgentFeedbackPage } from '@/pages/review/AgentFeedbackPage'
import { ChannelConfigPage } from '@/pages/channels/ChannelConfigPage'
import { CompatibilityPage } from '@/pages/review/CompatibilityPage'
import { DashboardPage } from '@/pages/dashboard/DashboardPage'
import { InjectionPage } from '@/pages/injection/InjectionPage'
import { LearningObjectsPage } from '@/pages/review/LearningObjectsPage'
import { SettingsPage } from '@/pages/settings/SettingsPage'
import { MemoriesPage } from '@/pages/memories/MemoriesPage'
import { ImportPage } from '@/pages/import/ImportPage'
import { BeliefsPage } from '@/pages/beliefs/BeliefsPage'
import { JargonPage } from '@/pages/jargon/JargonPage'
import { SoulPage } from '@/pages/soul/SoulPage'

export interface AppRoute {
  path: string
  title: string
  description: string
  icon: ComponentType<{ className?: string }>
  element: ComponentType
}

export const appRoutes: AppRoute[] = [
  {
    path: '/dashboard',
    title: '总览仪表盘',
    description: '系统健康、覆盖率、注入趋势与最近错误',
    icon: GaugeIcon,
    element: DashboardPage,
  },
  {
    path: '/memories',
    title: '记忆管理器',
    description: '搜索、查看、修改长期记忆权重或物理擦除记忆',
    icon: DatabaseIcon,
    element: MemoriesPage,
  },
  {
    path: '/import',
    title: '智能导入',
    description: '自动发现外部老记忆数据并排重，对无标签记忆提取 Tag',
    icon: DownloadIcon,
    element: ImportPage,
  },
  {
    path: '/injection',
    title: '注入观测台',
    description: 'trace 筛选、通道瀑布和最终注入预览',
    icon: ActivityIcon,
    element: InjectionPage,
  },
  {
    path: '/channels',
    title: '通道热配置',
    description: '注入通道热配置、校验与回滚',
    icon: Settings2Icon,
    element: ChannelConfigPage,
  },
  {
    path: '/learning-objects',
    title: '学习对象',
    description: '学习对象登记、候选与风险摘要',
    icon: BrainCircuitIcon,
    element: LearningObjectsPage,
  },
  {
    path: '/agent-feedback',
    title: 'Agent 反馈审查',
    description: '反馈记录、配置建议与人工审查',
    icon: MessageSquareWarningIcon,
    element: AgentFeedbackPage,
  },
  {
    path: '/beliefs',
    title: '信念审核',
    description: '审核/新建 Bot 对世界/自我的心智信念，追溯证据链',
    icon: BookHeartIcon,
    element: BeliefsPage,
  },
  {
    path: '/jargon',
    title: '黑话与口癖',
    description: '群聊习得本地黑话，在线同步官方 Holyman 人设语料库',
    icon: SmileIcon,
    element: JargonPage,
  },
  {
    path: '/soul',
    title: '灵魂与情绪',
    description: '心里话动机展示、生平大事 Timeline、SVG 情绪波动轨迹图',
    icon: HeartIcon,
    element: SoulPage,
  },
  {
    path: '/compatibility',
    title: '生态兼容',
    description: 'LivingMemory 兼容接口、工具别名和重复插件风险',
    icon: GitCompareArrowsIcon,
    element: CompatibilityPage,
  },
  {
    path: '/settings',
    title: '系统配置',
    description: 'Schema 全量配置表单与运行时滑块热更新调参',
    icon: SlidersIcon,
    element: SettingsPage,
  },
]

export const defaultRoute = '/dashboard'

export function getRouteByPath(pathname: string): AppRoute {
  return appRoutes.find((route) => route.path === pathname) ?? appRoutes[0]
}
