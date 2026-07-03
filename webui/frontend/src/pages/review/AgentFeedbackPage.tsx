import { useEffect, useState } from 'react'
import { AlertTriangleIcon } from 'lucide-react'
import { toast } from 'sonner'

import { getAgentFeedback, reviewCandidate, reviewConfigSuggestion, type AgentAction, type AgentFeedbackPayload } from '@/api/review'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

function recordId(record: Record<string, unknown>): number | null {
  const value = Number(record.id ?? record.suggestion_id ?? record.candidate_id)
  return Number.isFinite(value) ? value : null
}

function titleOf(record: Record<string, unknown>): string {
  return String(record.title ?? record.candidate_type ?? record.feedback ?? record.content ?? record.reason ?? record.id ?? '-')
}

export function AgentFeedbackPage() {
  const [data, setData] = useState<AgentFeedbackPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function load() {
    setLoading(true)
    setError('')
    try {
      const payload = await getAgentFeedback()
      if (payload.error) {
        throw new Error(payload.error)
      }
      setData(payload)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Agent 反馈加载失败')
    } finally {
      setLoading(false)
    }
  }

  async function act(kind: 'suggestion' | 'candidate', id: number, action: AgentAction) {
    setBusy(true)
    try {
      const result = kind === 'suggestion' ? await reviewConfigSuggestion(id, action) : await reviewCandidate(id, action)
      if (!result.ok) {
        throw new Error(result.error ?? '操作失败')
      }
      toast.success(result.message ?? '人工状态已记录')
      await load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    void load()
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

  const suggestions = data?.config_suggestions ?? []
  const candidates = data?.review_candidates ?? []
  const feedback = data?.feedback_records ?? []

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Agent Feedback</CardTitle>
          <CardDescription>记忆反馈、配置建议、审查候选与人工操作。</CardDescription>
        </CardHeader>
      </Card>
      <Alert>
        <AlertTriangleIcon />
        <AlertTitle>人工审查边界</AlertTitle>
        <AlertDescription>{data?.safety_note ?? '危险建议不会自动生效；这里只记录人工状态。'}</AlertDescription>
      </Alert>

      <div className="grid gap-4 md:grid-cols-3">
        <Card><CardHeader><CardDescription>反馈记录</CardDescription><CardTitle className="text-2xl">{feedback.length}</CardTitle></CardHeader></Card>
        <Card><CardHeader><CardDescription>配置建议</CardDescription><CardTitle className="text-2xl">{suggestions.length}</CardTitle></CardHeader></Card>
        <Card><CardHeader><CardDescription>审查候选</CardDescription><CardTitle className="text-2xl">{candidates.length}</CardTitle></CardHeader></Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>反馈记录</CardTitle>
          <CardDescription>最近 trace/memory 反馈</CardDescription>
        </CardHeader>
        <CardContent>
          {feedback.length === 0 ? <p className="text-sm text-muted-foreground">暂无反馈。</p> : (
            <div className="overflow-auto rounded-lg border">
              <Table>
                <TableHeader><TableRow><TableHead>ID</TableHead><TableHead>Feedback</TableHead><TableHead>Trace</TableHead><TableHead>内容</TableHead></TableRow></TableHeader>
                <TableBody>{feedback.slice(0, 20).map((item) => <TableRow key={String(item.id ?? JSON.stringify(item))}><TableCell>{String(item.id ?? '-')}</TableCell><TableCell><Badge variant="secondary">{String(item.feedback ?? '-')}</Badge></TableCell><TableCell>{String(item.trace_id ?? '-')}</TableCell><TableCell className="max-w-lg truncate">{titleOf(item)}</TableCell></TableRow>)}</TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>配置建议</CardTitle><CardDescription>批准只记录状态，不自动改配置</CardDescription></CardHeader>
          <CardContent className="flex flex-col gap-3">
            {suggestions.length === 0 ? <p className="text-sm text-muted-foreground">暂无待审配置建议。</p> : suggestions.map((item) => {
              const id = recordId(item)
              return (
                <div key={String(item.id ?? JSON.stringify(item))} className="flex flex-col gap-3 rounded-lg border p-3">
                  <div className="flex items-start justify-between gap-3"><span className="font-medium">{titleOf(item)}</span><Badge>{String(item.review_status ?? 'pending')}</Badge></div>
                  <pre className="overflow-auto rounded-md bg-muted p-2 text-xs">{JSON.stringify(item, null, 2)}</pre>
                  <div className="flex flex-wrap gap-2">
                    {(['approve', 'reject', 'ignore'] as const).map((action) => <Button key={action} disabled={busy || id === null} size="sm" variant={action === 'approve' ? 'default' : 'outline'} onClick={() => id !== null && void act('suggestion', id, action)}>{action}</Button>)}
                  </div>
                </div>
              )
            })}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>审查候选</CardTitle><CardDescription>候选不会自动提升到高风险对象</CardDescription></CardHeader>
          <CardContent className="flex flex-col gap-3">
            {candidates.length === 0 ? <p className="text-sm text-muted-foreground">暂无待审候选。</p> : candidates.map((item) => {
              const id = Number(item.id)
              return (
                <div key={String(item.id ?? item.content)} className="flex flex-col gap-3 rounded-lg border p-3">
                  <div className="flex items-start justify-between gap-3"><span className="font-medium">{String(item.candidate_type ?? item.object_key ?? 'candidate')}</span><Badge>{String(item.review_status ?? 'pending')}</Badge></div>
                  <pre className="overflow-auto rounded-md bg-muted p-2 text-xs">{JSON.stringify(item, null, 2)}</pre>
                  <div className="flex flex-wrap gap-2">
                    {(['approve', 'reject', 'ignore'] as const).map((action) => <Button key={action} disabled={busy || !Number.isFinite(id)} size="sm" variant={action === 'approve' ? 'default' : 'outline'} onClick={() => Number.isFinite(id) && void act('candidate', id, action)}>{action}</Button>)}
                  </div>
                </div>
              )
            })}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
