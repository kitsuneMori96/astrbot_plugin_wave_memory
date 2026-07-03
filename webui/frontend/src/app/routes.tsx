import type { ComponentType } from 'react'
import {
  ActivityIcon,
  BrainCircuitIcon,
  GaugeIcon,
  GitCompareArrowsIcon,
  MessageSquareWarningIcon,
  Settings2Icon,
} from 'lucide-react'

import { AgentFeedbackPage } from '@/pages/review/AgentFeedbackPage'
import { ChannelConfigPage } from '@/pages/channels/ChannelConfigPage'
import { CompatibilityPage } from '@/pages/review/CompatibilityPage'
import { DashboardPage } from '@/pages/dashboard/DashboardPage'
import { InjectionPage } from '@/pages/injection/InjectionPage'
import { LearningObjectsPage } from '@/pages/review/LearningObjectsPage'

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
    title: 'Dashboard',
    description: '系统健康、覆盖率、注入趋势与最近错误',
    icon: GaugeIcon,
    element: DashboardPage,
  },
  {
    path: '/injection',
    title: 'Injection Observatory',
    description: 'trace 筛选、通道瀑布和最终注入预览',
    icon: ActivityIcon,
    element: InjectionPage,
  },
  {
    path: '/channels',
    title: 'Channel Config',
    description: '注入通道热配置、校验与回滚',
    icon: Settings2Icon,
    element: ChannelConfigPage,
  },
  {
    path: '/learning-objects',
    title: 'Learning Objects',
    description: '学习对象登记、候选与风险摘要',
    icon: BrainCircuitIcon,
    element: LearningObjectsPage,
  },
  {
    path: '/agent-feedback',
    title: 'Agent Feedback',
    description: '反馈记录、配置建议与人工审查',
    icon: MessageSquareWarningIcon,
    element: AgentFeedbackPage,
  },
  {
    path: '/compatibility',
    title: 'Compatibility',
    description: 'LivingMemory facade、工具别名和重复插件风险',
    icon: GitCompareArrowsIcon,
    element: CompatibilityPage,
  },
]

export const defaultRoute = '/dashboard'

export function getRouteByPath(pathname: string): AppRoute {
  return appRoutes.find((route) => route.path === pathname) ?? appRoutes[0]
}
