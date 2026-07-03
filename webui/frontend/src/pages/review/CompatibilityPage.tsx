import { useEffect, useState } from 'react'
import { AlertTriangleIcon, CheckCircle2Icon } from 'lucide-react'

import { getCompatibilityStatus, type CompatibilityPayload } from '@/api/compatibility'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

export function CompatibilityPage() {
  const [data, setData] = useState<CompatibilityPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const payload = await getCompatibilityStatus()
        if (alive) {
          setData(payload)
        }
      } catch (err) {
        if (alive) {
          setError(err instanceof Error ? err.message : '兼容状态加载失败')
        }
      } finally {
        if (alive) {
          setLoading(false)
        }
      }
    }
    void load()
    return () => {
      alive = false
    }
  }, [])

  if (loading) {
    return <Skeleton className="h-96 w-full" />
  }
  if (error) {
    return <Alert variant="destructive"><AlertTriangleIcon /><AlertTitle>加载失败</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>
  }

  const warnings = data?.duplicate_warnings ?? []
  const aliases = Object.entries(data?.tool_aliases ?? {})

  return (
    <div className="flex flex-col gap-6">
      {warnings.length > 0 ? (
        <Alert variant="destructive">
          <AlertTriangleIcon />
          <AlertTitle>重复记忆插件风险</AlertTitle>
          <AlertDescription>{warnings.map((item) => item.message ?? item.name).join('；')}</AlertDescription>
        </Alert>
      ) : (
        <Alert>
          <CheckCircle2Icon />
          <AlertTitle>未发现重复插件风险</AlertTitle>
          <AlertDescription>本页面只提示风险，不会自动修改其他插件配置。</AlertDescription>
        </Alert>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader><CardTitle>Runtime</CardTitle><CardDescription>当前运行模式</CardDescription></CardHeader>
          <CardContent><Badge variant="secondary">{String(data?.runtime?.mode ?? '-')}</Badge></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>LivingMemory facade</CardTitle><CardDescription>兼容接口状态</CardDescription></CardHeader>
          <CardContent className="flex flex-col gap-2"><Badge variant={data?.facade?.enabled ? 'secondary' : 'outline'}>{data?.facade?.status ?? 'unknown'}</Badge><pre className="overflow-auto rounded-md bg-muted p-2 text-xs">{JSON.stringify(data?.facade?.interface ?? [], null, 2)}</pre></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>工具别名</CardTitle><CardDescription>LivingMemory 风格别名</CardDescription></CardHeader>
          <CardContent className="flex flex-col gap-2">{aliases.length === 0 ? <p className="text-sm text-muted-foreground">暂无别名信息。</p> : aliases.map(([name, item]) => <div key={name} className="flex items-center justify-between gap-3 rounded-lg border p-2"><span className="font-mono text-xs">{name}</span><Badge variant={item.enabled ? 'secondary' : 'outline'}>{item.enabled ? 'on' : 'off'}</Badge></div>)}</CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle>检测到的插件</CardTitle><CardDescription>可能影响重复注入的记忆插件</CardDescription></CardHeader>
        <CardContent>{(data?.detected_plugins ?? []).length === 0 ? <p className="text-sm text-muted-foreground">未检测到相关插件。</p> : <pre className="overflow-auto rounded-md bg-muted p-3 text-xs">{JSON.stringify(data?.detected_plugins, null, 2)}</pre>}</CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>建议</CardTitle><CardDescription>兼容模式配置建议</CardDescription></CardHeader>
        <CardContent><ul className="flex flex-col gap-2 text-sm text-muted-foreground">{(data?.recommended_settings ?? []).map((item) => <li key={item}>{item}</li>)}</ul></CardContent>
      </Card>
    </div>
  )
}
