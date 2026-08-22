import { NavLink, useLocation } from 'react-router-dom'
import { ExternalLinkIcon, MoonStarIcon, WavesIcon } from 'lucide-react'

import { appRoutes } from '@/app/routes'
import { Badge } from '@/components/ui/badge'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarSeparator,
} from '@/components/ui/sidebar'

const routeGroups = [
  { label: '日常运营', paths: ['/dashboard', '/memories', '/prompts', '/soul'] },
  { label: '数据管理', paths: ['/import', '/bindings', '/beliefs', '/jargon', '/blackbox'] },
  { label: '开发调试', paths: ['/injection', '/channels', '/learning-objects', '/agent-feedback'] },
  { label: '系统维护', paths: ['/settings', '/compatibility'] },
]

const toolLinks = [
  { href: '/explore', label: '3D 星图' },
  { href: '/maintain', label: '维护工具' },
]

export function WaveSidebar() {
  const location = useLocation()

  return (
    <Sidebar collapsible="icon" variant="inset">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild size="lg" tooltip="Wave Memory">
              <NavLink to="/dashboard">
                <WavesIcon />
                <span className="flex flex-col gap-0.5">
                  <span className="font-semibold">Wave Memory</span>
                  <span className="text-xs text-muted-foreground">WebUI 控制台</span>
                </span>
              </NavLink>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        {routeGroups.map((group) => {
          const routes = group.paths
            .map((path) => appRoutes.find((route) => route.path === path))
            .filter((route): route is NonNullable<typeof route> => Boolean(route))

          if (!routes.length) {
            return null
          }

          return (
            <SidebarGroup key={group.label}>
              <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {routes.map((item) => {
                    const Icon = item.icon
                    return (
                      <SidebarMenuItem key={item.path}>
                        <SidebarMenuButton
                          asChild
                          isActive={location.pathname === item.path || location.pathname.startsWith(`${item.path}/`)}
                          tooltip={item.title}
                        >
                          <NavLink to={item.path}>
                            <Icon />
                            <span>{item.title}</span>
                          </NavLink>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    )
                  })}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          )
        })}
        <SidebarSeparator />
        <SidebarGroup>
          <SidebarGroupLabel>扩展工具</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {toolLinks.map((link) => (
                <SidebarMenuItem key={link.href}>
                  <SidebarMenuButton asChild tooltip={link.label}>
                    <a href={link.href}>
                      <ExternalLinkIcon />
                      <span>{link.label}</span>
                    </a>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <div className="flex items-center gap-2 px-2 py-1 text-xs text-muted-foreground">
          <MoonStarIcon className="size-4" />
          <span>shadcn · Nova 风格</span>
          <Badge variant="secondary">v1</Badge>
        </div>
      </SidebarFooter>
    </Sidebar>
  )
}
