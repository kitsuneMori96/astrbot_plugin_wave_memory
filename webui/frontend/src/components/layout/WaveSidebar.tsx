import { MoonStarIcon, WavesIcon } from 'lucide-react'
import { NavLink, useLocation } from 'react-router-dom'

import { appRoutes, type RouteGroup } from '@/app/routes'
import { Badge } from '@/components/ui/badge'
import { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarMenu, SidebarMenuButton, SidebarMenuItem } from '@/components/ui/sidebar'

const groups: Array<{ id: RouteGroup; label: string }> = [
  { id: 'overview', label: '总览' },
  { id: 'data', label: '数据' },
  { id: 'runtime', label: '运行' },
  { id: 'cognition', label: '认知与学习' },
  { id: 'knowledge', label: '知识与关系' },
  { id: 'system', label: '系统' },
]

export function WaveSidebar() {
  const location = useLocation()
  return <Sidebar collapsible="icon" variant="inset"><SidebarHeader><SidebarMenu><SidebarMenuItem><SidebarMenuButton asChild size="lg" tooltip="Wave Memory"><NavLink to="/dashboard"><WavesIcon aria-hidden="true" /><span className="flex flex-col gap-0.5"><span className="font-semibold">Wave Memory</span><span className="text-sm text-muted-foreground">WebUI 控制台</span></span></NavLink></SidebarMenuButton></SidebarMenuItem></SidebarMenu></SidebarHeader><SidebarContent>
    {groups.map((group) => <SidebarGroup key={group.id}><SidebarGroupLabel>{group.label}</SidebarGroupLabel><SidebarGroupContent><SidebarMenu>{appRoutes.filter((route) => route.group === group.id).map((route) => { const Icon = route.icon; return <SidebarMenuItem key={route.path}><SidebarMenuButton asChild isActive={location.pathname === route.path || location.pathname.startsWith(`${route.path}/`)} tooltip={route.title}><NavLink to={{ pathname: route.path, search: location.search }}><Icon aria-hidden="true" /><span>{route.title}</span></NavLink></SidebarMenuButton></SidebarMenuItem> })}</SidebarMenu></SidebarGroupContent></SidebarGroup>)}
  </SidebarContent><SidebarFooter><div className="flex items-center gap-2 px-2 py-1 text-sm text-muted-foreground"><MoonStarIcon className="size-4" aria-hidden="true" /><span>shadcn · Nova</span><Badge variant="secondary">v1</Badge></div></SidebarFooter></Sidebar>
}
