import type { ComponentType } from 'react'
import {
  ActivityIcon,
  BookHeartIcon,
  BookOpenIcon,
  BrainCircuitIcon,
  DatabaseIcon,
  DownloadIcon,
  GaugeIcon,
  GitBranchIcon,
  GitCompareArrowsIcon,
  HeartIcon,
  SearchCheckIcon,
  Settings2Icon,
  SlidersIcon,
  SmileIcon,
  UsersIcon,
} from 'lucide-react'

import { ChannelConfigPage } from '@/pages/channels/ChannelConfigPage'
import { CompatibilityPage } from '@/pages/review/CompatibilityPage'
import { DashboardPage } from '@/pages/dashboard/DashboardPage'
import { InjectionPage } from '@/pages/injection/InjectionPage'
import { SettingsPage } from '@/pages/settings/SettingsPage'
import { MemoriesPage } from '@/pages/memories/MemoriesPage'
import { ImportPage } from '@/pages/import/ImportPage'
import { BeliefsPage } from '@/pages/beliefs/BeliefsPage'
import { JargonPage } from '@/pages/jargon/JargonPage'
import { SoulPage } from '@/pages/soul/SoulPage'
import { BlackboxHubPage } from '@/pages/blackbox/BlackboxHubPage'
import { BlackboxBookLorePage } from '@/pages/blackbox/BlackboxBookLorePage'
import { BlackboxFewShotPage } from '@/pages/blackbox/BlackboxFewShotPage'
import { BlackboxFactsPage } from '@/pages/blackbox/BlackboxFactsPage'
import { BlackboxPeoplePage } from '@/pages/blackbox/BlackboxPeoplePage'
import { BlackboxIndexesPage } from '@/pages/blackbox/BlackboxIndexesPage'
import { MaintainPage } from '@/pages/maintain/MaintainPage'
import { LearningCenterPage } from '@/pages/learning/LearningCenterPage'

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
    path: '/maintain',
    title: '标签与维护中心',
    description: '历史记忆批量标签分析分类、合并治理与系统算法自检',
    icon: SlidersIcon,
    element: MaintainPage,
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
    path: '/learning-center',
    title: '通用学习中心',
    description: '来源、候选、经历内化与晋升历史的统一审核入口',
    icon: BrainCircuitIcon,
    element: LearningCenterPage,
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
    path: '/blackbox',
    title: '黑盒管理',
    description: 'BookLore、FewShot、Facts、人物与索引能力的只读诊断与联动入口',
    icon: DatabaseIcon,
    element: BlackboxHubPage,
  },
  {
    path: '/blackbox/book-lore',
    title: 'BookLore 管理',
    description: '世界观/书设知识库、索引健康、搜索分页与未开放写操作标识',
    icon: BookOpenIcon,
    element: BlackboxBookLorePage,
  },
  {
    path: '/blackbox/fewshot',
    title: 'FewShot 管理',
    description: '风格范例库只读搜索、状态筛选与未开放写操作标识',
    icon: BrainCircuitIcon,
    element: BlackboxFewShotPage,
  },
  {
    path: '/blackbox/facts',
    title: 'Facts / 关系管理',
    description: '稳定事实关系、PERSON_ALIAS、详情查看与证据记忆跳转',
    icon: GitBranchIcon,
    element: BlackboxFactsPage,
  },
  {
    path: '/blackbox/people',
    title: '人物与好感管理',
    description: '人物画像、UserProfile、Affinity、搜索分页与详情展开',
    icon: UsersIcon,
    element: BlackboxPeoplePage,
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
