import { HashRouter } from 'react-router-dom'

import { AuthProvider } from '@/app/auth-provider'
import { useAuth } from '@/app/auth-context'
import { AppRoutes } from '@/app/AppShell'
import { LoginPage } from '@/pages/LoginPage'
import { Skeleton } from '@/components/ui/skeleton'
import { TooltipProvider } from '@/components/ui/tooltip'

function AuthGate() {
  const { state } = useAuth()

  if (state.status === 'checking') {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background p-6">
        <div className="flex w-full max-w-md flex-col gap-3">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      </main>
    )
  }

  if (state.status === 'anonymous') {
    return <LoginPage />
  }

  return <AppRoutes />
}

export default function App() {
  return (
    <TooltipProvider>
      <HashRouter>
        <AuthProvider>
          <AuthGate />
        </AuthProvider>
      </HashRouter>
    </TooltipProvider>
  )
}
