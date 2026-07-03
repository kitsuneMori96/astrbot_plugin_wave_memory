import { useEffect, useState } from 'react'
import { AlertTriangleIcon } from 'lucide-react'

import { getLearningObjectsReview, type LearningObjectReviewPayload } from '@/api/review'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

function SummaryCard({ title, value }: { title: string; value: unknown }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{title}</CardDescription>
        <CardTitle className="text-2xl">{String(value ?? 0)}</CardTitle>
      </CardHeader>
    </Card>
  )
}

export function LearningObjectsPage() {
  const [data, setData] = useState<LearningObjectReviewPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const payload = await getLearningObjectsReview()
        if (alive) {
          setData(payload)
        }
      } catch (err) {
        if (alive) {
          setError(err instanceof Error ? err.message : '学习对象审查加载失败')
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
    return (
      <Alert variant="destructive">
        <AlertTriangleIcon />
        <AlertTitle>加载失败</AlertTitle>
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    )
  }

  const summary = data?.summary ?? {}
  const objects = data?.objects ?? []
  const pending = data?.pending_candidates ?? []
  const risky = data?.risky_candidates ?? []
  const duplicates = data?.duplicate_entries ?? []

  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-4 md:grid-cols-4">
        <SummaryCard title="对象数" value={summary.objects} />
        <SummaryCard title="高风险对象" value={summary.high_risk_objects} />
        <SummaryCard title="待审候选" value={summary.pending_candidates} />
        <SummaryCard title="风险候选" value={summary.risky_candidates} />
      </div>

      {duplicates.length > 0 ? (
        <Alert variant="destructive">
          <AlertTriangleIcon />
          <AlertTitle>发现重复登记提示</AlertTitle>
          <AlertDescription>{duplicates.length} 个学习对象存在重复相关审计发现。</AlertDescription>
        </Alert>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>学习对象登记表</CardTitle>
          <CardDescription>写入路径、存储、风险和可注入通道</CardDescription>
        </CardHeader>
        <CardContent>
          {objects.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无学习对象。</p>
          ) : (
            <div className="overflow-auto rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Key</TableHead>
                    <TableHead>风险</TableHead>
                    <TableHead>模式</TableHead>
                    <TableHead>写入路径</TableHead>
                    <TableHead>存储</TableHead>
                    <TableHead>注入通道</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {objects.map((item) => (
                    <TableRow key={item.key ?? item.name}>
                      <TableCell className="font-medium">{item.key ?? item.name}</TableCell>
                      <TableCell><Badge variant={item.risk === 'high' ? 'destructive' : 'secondary'}>{item.risk ?? 'unknown'}</Badge></TableCell>
                      <TableCell>{item.mode_enabled ? 'enabled' : item.mode_disabled_reason ?? 'disabled'}</TableCell>
                      <TableCell>{String(item.write_path ?? '-')}</TableCell>
                      <TableCell>{String(item.storage ?? '-')}</TableCell>
                      <TableCell>{String(item.injection_channel ?? '-')}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>待审候选</CardTitle>
            <CardDescription>{pending.length} 条</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {pending.length === 0 ? <p className="text-sm text-muted-foreground">暂无候选。</p> : pending.slice(0, 8).map((item) => <pre key={String(item.id ?? item.content)} className="overflow-auto rounded-lg bg-muted p-3 text-xs">{JSON.stringify(item, null, 2)}</pre>)}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>风险候选</CardTitle>
            <CardDescription>{risky.length} 条</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {risky.length === 0 ? <p className="text-sm text-muted-foreground">暂无风险候选。</p> : risky.slice(0, 8).map((item) => <pre key={String(item.id ?? item.content)} className="overflow-auto rounded-lg bg-muted p-3 text-xs">{JSON.stringify(item, null, 2)}</pre>)}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
