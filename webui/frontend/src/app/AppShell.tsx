import { useEffect, useState } from 'react'
import { Navigate, Outlet, Route, Routes } from 'react-router-dom'

import { appRoutes, defaultRoute } from '@/app/routes'
import { CommandPalette } from '@/components/layout/CommandPalette'
import { PageHeader } from '@/components/layout/PageHeader'
import { WaveSidebar } from '@/components/layout/WaveSidebar'
import { ScrollArea } from '@/components/ui/scroll-area'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { Toaster } from '@/components/ui/sonner'

export function AppShell() {
  const [paletteOpen, setPaletteOpen] = useState(false)

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((v) => !v)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

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
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
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
