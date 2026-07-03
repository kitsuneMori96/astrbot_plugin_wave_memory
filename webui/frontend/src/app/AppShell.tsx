import { Navigate, Outlet, Route, Routes } from 'react-router-dom'

import { appRoutes, defaultRoute } from '@/app/routes'
import { PageHeader } from '@/components/layout/PageHeader'
import { WaveSidebar } from '@/components/layout/WaveSidebar'
import { ScrollArea } from '@/components/ui/scroll-area'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { Toaster } from '@/components/ui/sonner'

export function AppShell() {
  return (
    <SidebarProvider>
      <WaveSidebar />
      <SidebarInset>
        <PageHeader />
        <ScrollArea className="h-[calc(100svh-3.5rem)]">
          <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 p-4 md:p-6">
            <Outlet />
          </div>
        </ScrollArea>
      </SidebarInset>
      <Toaster richColors />
    </SidebarProvider>
  )
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate replace to={defaultRoute} />} />
        {appRoutes.map((route) => {
          const Element = route.element
          return <Route key={route.path} path={route.path} element={<Element />} />
        })}
      </Route>
      <Route path="*" element={<Navigate replace to={defaultRoute} />} />
    </Routes>
  )
}
