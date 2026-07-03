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

const legacyLinks = [
  { href: '/legacy', label: 'Old WebUI' },
  { href: '/explore', label: 'Explore' },
  { href: '/maintain', label: 'Maintain' },
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
                  <span className="text-xs text-muted-foreground">WebUI Console</span>
                </span>
              </NavLink>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Overview</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {appRoutes.slice(0, 1).map((item) => {
                const Icon = item.icon
                return (
                  <SidebarMenuItem key={item.path}>
                    <SidebarMenuButton asChild isActive={location.pathname === item.path} tooltip={item.title}>
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
        <SidebarGroup>
          <SidebarGroupLabel>Injection</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {appRoutes.slice(1, 3).map((item) => {
                const Icon = item.icon
                return (
                  <SidebarMenuItem key={item.path}>
                    <SidebarMenuButton asChild isActive={location.pathname === item.path} tooltip={item.title}>
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
        <SidebarGroup>
          <SidebarGroupLabel>Review</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {appRoutes.slice(3).map((item) => {
                const Icon = item.icon
                return (
                  <SidebarMenuItem key={item.path}>
                    <SidebarMenuButton asChild isActive={location.pathname === item.path} tooltip={item.title}>
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
        <SidebarSeparator />
        <SidebarGroup>
          <SidebarGroupLabel>Legacy</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {legacyLinks.map((link) => (
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
          <span>shadcn/radix-nova</span>
          <Badge variant="secondary">v1</Badge>
        </div>
      </SidebarFooter>
    </Sidebar>
  )
}
