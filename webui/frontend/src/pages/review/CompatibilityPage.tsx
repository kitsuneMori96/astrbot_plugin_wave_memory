import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangleIcon, CheckCircle2Icon, CircleHelpIcon, RefreshCwIcon } from 'lucide-react'

import {
  getCompatibilityStatus,
  type CapabilityStatus,
  type CompatibilityPayload,
  type CompatibilityStatus,
} from '@/api/compatibility'
import { isRequestCancelled } from '@/api/client'
import { QueryState } from '@/components/shared'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

const STATUS_LABELS: Record<CompatibilityStatus, string> = {
  detected: '已探测',
  not_detected: '未探测到',
  not_configured: '未配置探测',
  probe_failed: '探测失败',
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function safeCapability(value: unknown): CapabilityStatus | null {
  const record = asRecord(value)
  const status = record?.status
  if (!record || typeof status !== 'string' || !(status in STATUS_LABELS)) return null
  return {
    id: typeof record.id === 'string' ? record.id : 'unknown',
    enabled: record.enabled === true,
    status: status as CompatibilityStatus,
    source: typeof record.source === 'string' ? record.source : '未知',
    checked_at: typeof record.checked_at === 'string' ? record.checked_at : '',
    error: typeof record.error === 'string' ? record.error : null,
    evidence: Array.isArray(record.evidence) ? record.evidence.filter((item) => asRecord(item)) as CapabilityStatus['evidence'] : [],
    configured: record.configured,
  }
}

function MissingSection({ title }: { title: string }) {
  return <Card><CardHeader><CardTitle>{title}</CardTitle><CardDescription>服务端本次未返回该分区，其他兼容性结论仍可继续核验。</CardDescription></CardHeader><CardContent className="text-sm text-muted-foreground">当前分区不可用。</CardContent></Card>
}

function FactMeta({ fact }: { fact: Pick<CapabilityStatus, 'source' | 'checked_at' | 'error' | 'evidence'> }) {
  return (
    <div className="flex flex-col gap-2 text-sm text-muted-foreground">
      <p>来源：<span className="font-mono text-foreground">{fact.source || '未知'}</span></p>
      <p>检查时间：<span className="text-foreground">{fact.checked_at || '未知'}</span></p>
      {fact.error ? <p className="text-destructive">错误：{fact.error}</p> : null}
      {fact.evidence.length ? (
        <ul className="list-disc space-y-1 pl-5">
          {fact.evidence.map((item, index) => (
            <li key={`${item.kind ?? 'evidence'}-${index}`}>{item.summary ?? `${item.name ?? item.plugin_id ?? item.kind}：${item.active === false ? '未启用' : '已记录'}`}</li>
          ))}
        </ul>
      ) : <p>服务端没有返回可核验的证据。</p>}
    </div>
  )
}

function CapabilityCard({ title, capability }: { title: string; capability: CapabilityStatus }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>{title}</CardTitle>
          <Badge variant={capability.status === 'detected' ? 'default' : capability.status === 'probe_failed' ? 'destructive' : 'outline'}>
            {STATUS_LABELS[capability.status]}
          </Badge>
        </div>
        <CardDescription>以下结论来自运行时注册状态，不从静态接口名称推断。</CardDescription>
      </CardHeader>
      <CardContent><FactMeta fact={capability} /></CardContent>
    </Card>
  )
}

export function CompatibilityPage() {
  const [data, setData] = useState<CompatibilityPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const requestRef = useRef<AbortController | null>(null)

  const load = useCallback(async () => {
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller
    setLoading(true)
    setError(null)
    try {
      const nextData = await getCompatibilityStatus(controller.signal)
      if (controller.signal.aborted || requestRef.current !== controller) return
      if (!asRecord(nextData)) throw new Error('兼容状态响应不是可识别的对象。')
      setData(nextData)
    } catch (nextError) {
      if (!controller.signal.aborted && requestRef.current === controller && !isRequestCancelled(nextError)) setError(nextError)
    } finally {
      if (!controller.signal.aborted && requestRef.current === controller) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    return () => requestRef.current?.abort()
  }, [load])

  const queryStatus = loading ? 'loading' : error ? 'error' : data ? 'success' : 'unknown'
  const probe = safeCapability(data?.probe)
  const facade = safeCapability(data?.facade)
  const toolAliasesRecord = asRecord(data?.tool_aliases)
  const toolAliases = toolAliasesRecord ? Object.entries(toolAliasesRecord).map(([name, value]) => [name, safeCapability(value)] as const) : null
  const duplicateWarnings = Array.isArray(data?.duplicate_warnings) ? data.duplicate_warnings : []
  const recommendations = Array.isArray(data?.recommended_settings) ? data.recommended_settings.filter((item): item is string => typeof item === 'string') : null
  const documentation = asRecord(data?.documentation)
  const facadeInterfaces = Array.isArray(documentation?.facade_interfaces) ? documentation.facade_interfaces.filter((item): item is string => typeof item === 'string') : null
  const documentedAliases = asRecord(documentation?.tool_aliases)
  return (
    <div className="flex flex-col gap-4"><div className="flex justify-end"><Button type="button" variant="outline" disabled={loading} onClick={() => void load()}><RefreshCwIcon />刷新兼容状态</Button></div><QueryState status={queryStatus} error={error} onRetry={() => void load()} title="兼容状态加载失败" loadingRows={5}>
      {data ? (
        <div className="flex flex-col gap-6">
          {!probe ? (
            <Alert><CircleHelpIcon /><AlertTitle>插件探测分区未返回</AlertTitle><AlertDescription>无法判断重复插件风险；其他能力分区仍按各自数据展示。</AlertDescription></Alert>
          ) : probe.status === 'probe_failed' ? (
            <Alert variant="destructive"><AlertTriangleIcon /><AlertTitle>插件探测失败，结论不可判定</AlertTitle><AlertDescription>{probe.error ?? '请检查探测来源后重试。'}</AlertDescription></Alert>
          ) : probe.status === 'not_configured' ? (
            <Alert><CircleHelpIcon /><AlertTitle>插件探测未配置</AlertTitle><AlertDescription>这不等同于“没有重复插件”；当前没有可执行的 registry 探测源。</AlertDescription></Alert>
          ) : duplicateWarnings.length ? (
            <Alert variant="destructive"><AlertTriangleIcon /><AlertTitle>重复记忆插件风险</AlertTitle><AlertDescription>{duplicateWarnings.map((item) => item.message ?? item.name).join('；')}</AlertDescription></Alert>
          ) : (
            <Alert><CheckCircle2Icon /><AlertTitle>本次探测未发现已知重复插件</AlertTitle><AlertDescription>该结论仅对显示的来源和检查时间有效。</AlertDescription></Alert>
          )}

          {probe ? <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <CardTitle>插件生态探测</CardTitle>
                <Badge variant={probe.status === 'detected' ? 'default' : 'outline'}>{STATUS_LABELS[probe.status]}</Badge>
              </div>
              <CardDescription>明确区分已探测、未探测到、未配置和探测失败。</CardDescription>
            </CardHeader>
            <CardContent><FactMeta fact={probe} /></CardContent>
          </Card> : <MissingSection title="插件生态探测" />}

          <div className="grid gap-4 lg:grid-cols-2">
            {facade ? <CapabilityCard title="LivingMemory Facade" capability={facade} /> : <MissingSection title="LivingMemory Facade" />}
            {toolAliases === null ? <MissingSection title="工具别名" /> : toolAliases.length ? toolAliases.map(([name, capability]) => capability ? <CapabilityCard key={name} title={name} capability={capability} /> : <MissingSection key={name} title={name} />) : <Card><CardHeader><CardTitle>工具别名</CardTitle></CardHeader><CardContent className="text-sm text-muted-foreground">服务端返回了真实空集合。</CardContent></Card>}
          </div>

          <Card>
            <CardHeader><CardTitle>当前运行模式</CardTitle><CardDescription>来源为实际插件配置解析结果。</CardDescription></CardHeader>
            <CardContent className="space-y-2 text-sm">
              <Badge variant="secondary">{String(data.runtime?.mode ?? '未知')}</Badge>
              <p className="text-muted-foreground">来源：{String(data.runtime?.source ?? '未知')}；检查时间：{String(data.runtime?.checked_at ?? '未知')}</p>
            </CardContent>
          </Card>

          {recommendations === null ? <MissingSection title="建议" /> : <Card>
            <CardHeader><CardTitle>建议</CardTitle><CardDescription>只根据当前 capability 与冲突证据生成。</CardDescription></CardHeader>
            <CardContent>{recommendations.length ? <ul className="list-disc space-y-2 pl-5 text-sm">{recommendations.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="text-sm text-muted-foreground">当前没有建议。</p>}</CardContent>
          </Card>}

          {!documentation || facadeInterfaces === null || !documentedAliases ? <MissingSection title="静态接口文档" /> : <Card>
            <CardHeader><CardTitle>静态接口文档</CardTitle><CardDescription>{typeof documentation.notice === 'string' ? documentation.notice : '服务端未返回说明。'}</CardDescription></CardHeader>
            <CardContent className="space-y-4 text-sm">
              <div><h3 className="font-medium">Facade 接口</h3>{facadeInterfaces.length ? <ul className="mt-2 list-disc space-y-1 pl-5 font-mono">{facadeInterfaces.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="mt-2 text-muted-foreground">当前没有接口条目。</p>}</div>
              <div><h3 className="font-medium">工具别名说明</h3>{Object.keys(documentedAliases).length ? <dl className="mt-2 space-y-2">{Object.entries(documentedAliases).map(([name, description]) => <div key={name}><dt className="font-mono font-medium">{name}</dt><dd className="text-muted-foreground">{String(description)}</dd></div>)}</dl> : <p className="mt-2 text-muted-foreground">当前没有别名说明。</p>}</div>
            </CardContent>
          </Card>}
        </div>
      ) : null}
    </QueryState></div>
  )
}
