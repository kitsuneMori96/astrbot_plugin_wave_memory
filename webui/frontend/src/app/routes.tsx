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
  LinkIcon,
  MessageSquareWarningIcon,
  SearchCheckIcon,
  Settings2Icon,
  SlidersIcon,
  SmileIcon,
  UsersIcon,
} from 'lucide-react'

import { IdentityBindingSection as IdentityBindingPage } from '@/pages/bindings'
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
import { PromptsPage } from '@/pages/prompts/PromptsPage'
import { BlackboxHubPage } from '@/pages/blackbox/BlackboxHubPage'
import { BlackboxPeoplePage, BlackboxPersonDetailPage } from '@/pages/blackbox'
import { BlackboxIndexesPage } from '@/pages/blackbox/BlackboxIndexesPage'


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
    path: '/bindings',
    title: '身份绑定',
    description: 'local_id 与 master_id 的身份映射管理',
    icon: LinkIcon,
    element: IdentityBindingPage,
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
    description: '群聊习得本地黑话，在线同步 Holyman 广域抽象黑话分层资产',
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
    path: '/prompts',
    title: '提示词中心',
    description: 'wave 自成人设库、三级绑定、Planner/风格/安全架构模板编辑',
    icon: BookHeartIcon,
    element: PromptsPage,
  },
  {
    path: '/blackbox',
    title: '黑盒管理',
    description: 'BookLore、FewShot、Facts、人物与索引能力的统一管理入口',
    icon: DatabaseIcon,
    element: BlackboxHubPage,
  },
  {
    path: '/blackbox/people',
    title: '人物与好感管理',
    description: '人物画像、UserProfile、Affinity、关系事件与别名入口',
    icon: UsersIcon,
    element: BlackboxPeoplePage,
  },
  {
    path: '/blackbox/people/:id',
    title: '人物详情',
    description: '人物完整画像：维度雷达、好感趋势、关系事件、表达模式',
    icon: UsersIcon,
    element: BlackboxPersonDetailPage,
  },
  {
    path: '/blackbox/indexes',
    title: '索引与 FTS5 管理',
    description: '向量索引、FTS5、EPA basis 与 BookLore HNSW 健康入口',
    icon: SearchCheckIcon,
    element: BlackboxIndexesPage,
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
    title: '运行时调参',
    description: '运行时滑块热更新调参；静态 Schema 配置请前往 AstrBot 6185',
    icon: SlidersIcon,
    element: SettingsPage,
  },
]

export const defaultRoute = '/dashboard'

export function getRouteByPath(pathname: string): AppRoute {
  return appRoutes.find((route) => route.path === pathname) ?? appRoutes[0]
}
