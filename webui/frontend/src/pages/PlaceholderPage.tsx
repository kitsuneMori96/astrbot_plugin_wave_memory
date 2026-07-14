import { useCallback, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { CompassIcon, SparklesIcon } from 'lucide-react'

import { getScopeOptions, scopeOptionsFor } from '@/api/options'
import { ScopeSelect } from '@/components/shared'
import { useCanonicalScopeDefault, usePaginationSearchParams } from '@/hooks/use-pagination-search-params'

export function ExplorePage() {
  const pagination = usePaginationSearchParams()
  const [params] = useSearchParams()
  const botId = params.get('bot_id') ?? ''
  const sessionId = params.get('session_id') ?? ''
  const iframeRef = useRef<HTMLIFrameElement>(null)

  useCanonicalScopeDefault({ botId, sessionId, setFilters: pagination.setFilters })

  const loadBots = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['bot']), [])
  const loadSessions = useCallback(async () => {
    const options = scopeOptionsFor(await getScopeOptions(), ['session'])
    return botId ? options.filter((option) => option.description?.startsWith(`${botId} ·`)) : []
  }, [botId])

  useEffect(() => {
    if (!botId || !sessionId) return
    const token = localStorage.getItem('wave_token') || ''
    const query = new URLSearchParams({
      bot_id: botId,
      session_id: sessionId,
      visibility: 'group',
    })
    if (token) {
      query.set('token', token)
    }
    if (iframeRef.current) {
      iframeRef.current.src = `/explore?${query.toString()}`
    }
  }, [botId, sessionId])

  return (
    <div data-page="explore-galaxy" className="flex flex-col gap-5 h-[calc(100svh-6.5rem)] min-h-[600px]">
      <Card className="border-border/60">
        <CardHeader className="py-4">
          <CardTitle className="text-base flex items-center gap-2">
            <CompassIcon className="size-4.5 text-primary" />
            3D 神经云图
          </CardTitle>
          <CardDescription>
            查看当前作用域下的 HNSW 同域近邻与 facts、Soul、学习投影等正式知识图层。只读浏览，跨 Scope 结果会被后端拒绝。
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <div className="grid gap-4 md:grid-cols-2">
            <ScopeSelect
              value={botId || undefined}
              loadOptions={loadBots}
              label="Bot"
              placeholder="选择真实 Bot"
              required
              onValueChange={(value) => pagination.setFilters({ bot_id: value, session_id: null })}
            />
            <ScopeSelect
              value={sessionId || undefined}
              loadOptions={loadSessions}
              label="群 / 会话"
              placeholder="选择 canonical 活跃会话"
              disabled={!botId}
              required
              onValueChange={(value) => pagination.setFilters({ session_id: value })}
            />
          </div>
        </CardContent>
      </Card>

      {!botId || !sessionId ? (
        <Card className="flex-1 border-dashed bg-gradient-to-br from-primary/5 via-card to-card flex items-center justify-center p-6">
          <div className="text-center max-w-md">
            <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary">
              <CompassIcon className="size-6 animate-pulse" />
            </div>
            <h3 className="font-semibold text-lg">等待唤醒星云</h3>
            <p className="mt-2 text-sm text-muted-foreground">
              请从上方选择服务端授权的真实 Bot 与 canonical 会话。Scope 确立后，高维图谱引擎会自动装载。
            </p>
          </div>
        </Card>
      ) : (
        <Card className="flex-1 overflow-hidden border-primary/20 bg-black relative shadow-2xl">
          <div className="absolute top-4 left-4 z-10 flex items-center gap-2 rounded-full border border-primary/20 bg-background/80 px-3 py-1.5 backdrop-blur text-xs font-medium shadow-md">
            <SparklesIcon className="size-3.5 text-primary animate-spin" style={{ animationDuration: '3s' }} />
            <span>实时 Scope：{botId} · {sessionId}</span>
          </div>
          <iframe
            ref={iframeRef}
            title="3D Cosmic NeuroGalaxy"
            className="w-full h-full border-none bg-transparent"
            sandbox="allow-scripts allow-same-origin allow-popups"
          />
        </Card>
      )}
    </div>
  )
}

export function PlaceholderPage({ title, description }: { title: string; description: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">该页面的数据视图会在后续任务接入现有 WebUI API。</p>
      </CardContent>
    </Card>
  )
}
