import type { ComponentType } from 'react'
import { ActivityIcon, BookHeartIcon, BookOpenIcon, BrainCircuitIcon, DatabaseIcon, DownloadIcon, GaugeIcon, GitBranchIcon, GitCompareArrowsIcon, HeartIcon, SearchCheckIcon, Settings2Icon, SlidersIcon, SmileIcon, TagsIcon, UsersIcon, CompassIcon } from 'lucide-react'

import { BeliefsPage } from '@/pages/beliefs/BeliefsPage'
import { ChannelConfigPage } from '@/pages/channels/ChannelConfigPage'
import { DashboardPage } from '@/pages/dashboard/DashboardPage'
import { IndexesPage } from '@/pages/diagnostics/IndexesPage'
import { ImportPage } from '@/pages/import/ImportPage'
import { InjectionPage } from '@/pages/injection/InjectionPage'
import { JargonPage } from '@/pages/jargon/JargonPage'
import { BookLorePage } from '@/pages/knowledge/BookLorePage'
import { FactsPage } from '@/pages/knowledge/FactsPage'
import { FewShotPage } from '@/pages/knowledge/FewShotPage'
import { LearningCenterPage } from '@/pages/learning/LearningCenterPage'
import { MaintenancePage } from '@/pages/maintenance/MaintenancePage'
import { MemoriesPage } from '@/pages/memories/MemoriesPage'
import { PeoplePage } from '@/pages/people/PeoplePage'
import { CompatibilityPage } from '@/pages/review/CompatibilityPage'
import { SettingsPage } from '@/pages/settings/SettingsPage'
import { SoulPage } from '@/pages/soul/SoulPage'
import { TagGraphPage } from '@/pages/tags/TagGraphPage'
import { TagsPage } from '@/pages/tags/TagsPage'
import { ExplorePage } from '@/pages/PlaceholderPage'

export type RouteGroup = 'overview' | 'data' | 'runtime' | 'cognition' | 'knowledge' | 'system'
export interface AppRoute { path: string; title: string; description: string; group: RouteGroup; icon: ComponentType<{ className?: string }>; element: ComponentType }

export const appRoutes: AppRoute[] = [
  { path: '/dashboard', title: '总览', description: '真实健康、待办与近期异常', group: 'overview', icon: GaugeIcon, element: DashboardPage },
  { path: '/explore', title: '神经云图', description: '3D 交互式高维记忆与关系星图', group: 'overview', icon: CompassIcon, element: ExplorePage },
  { path: '/memories', title: '记忆', description: 'Scoped PageResponse 与 ObjectRef 记忆资源', group: 'data', icon: DatabaseIcon, element: MemoriesPage },
  { path: '/tags', title: 'Tag 浪潮', description: '只读覆盖率、频率、类型与置信度总览', group: 'data', icon: TagsIcon, element: TagsPage },
  { path: '/tags/graph', title: 'Tag 神经云图', description: 'Scoped 有向共现、来源、脉冲与路径', group: 'data', icon: BrainCircuitIcon, element: TagGraphPage },
  { path: '/import', title: '导入', description: '真实来源预检与 durable import job', group: 'data', icon: DownloadIcon, element: ImportPage },
  { path: '/maintenance', title: '维护任务', description: '可恢复任务、checkpoint、日志与取消语义', group: 'runtime', icon: SlidersIcon, element: MaintenancePage },
  { path: '/observatory', title: '注入观测台', description: '可复现筛选、完整 Trace 与配置 revision', group: 'runtime', icon: ActivityIcon, element: InjectionPage },
  { path: '/channels', title: '通道配置', description: '真实 descriptor、saved/effective 与 Trace 验证', group: 'runtime', icon: Settings2Icon, element: ChannelConfigPage },
  { path: '/learning', title: '学习过程', description: '来源、任务、候选、审核与晋升账本', group: 'cognition', icon: BrainCircuitIcon, element: LearningCenterPage },
  { path: '/beliefs', title: '信念', description: '证据健康与生命周期审核', group: 'cognition', icon: BookHeartIcon, element: BeliefsPage },
  { path: '/jargon', title: '黑话与口癖', description: '群聊习得黑话、证据审核与 Holyman 广域资产审计', group: 'cognition', icon: SmileIcon, element: JargonPage },
  { path: '/soul', title: 'Soul 状态', description: '真实 Bot/会话 Mood、Concern、Timeline 与关系投影', group: 'cognition', icon: HeartIcon, element: SoulPage },
  { path: '/knowledge/book-lore', title: 'BookLore', description: '独立只读语料、解析、本地化与隔离', group: 'knowledge', icon: BookOpenIcon, element: BookLorePage },
  { path: '/knowledge/style-examples', title: '风格样例', description: '仅 approved/healthy 正式 FewShot', group: 'knowledge', icon: BrainCircuitIcon, element: FewShotPage },
  { path: '/knowledge/facts', title: '事实', description: 'Scoped subject/predicate/object 与证据', group: 'knowledge', icon: GitBranchIcon, element: FactsPage },
  { path: '/people', title: '人物与关系', description: '按 Bot/session/user 复合作用域展示', group: 'knowledge', icon: UsersIcon, element: PeoplePage },
  { path: '/diagnostics/indexes', title: '索引诊断', description: '只读来源、count、generation 与健康证据', group: 'system', icon: SearchCheckIcon, element: IndexesPage },
  { path: '/compatibility', title: '生态兼容', description: '真实探测状态、来源、错误与证据', group: 'system', icon: GitCompareArrowsIcon, element: CompatibilityPage },
  { path: '/settings', title: '系统配置', description: 'default/saved/effective 与生效方式', group: 'system', icon: SlidersIcon, element: SettingsPage },
]

export const defaultRoute = '/dashboard'
export function getRouteByPath(pathname: string): AppRoute { return appRoutes.find((route) => route.path === pathname) ?? appRoutes[0] }
