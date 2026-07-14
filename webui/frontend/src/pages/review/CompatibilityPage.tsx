import { useCallback, useEffect, useState } from 'react'
import { AlertTriangleIcon, CheckCircle2Icon, CircleHelpIcon } from 'lucide-react'

import {
  getCompatibilityStatus,
  type CapabilityStatus,
  type CompatibilityPayload,
  type CompatibilityStatus,
} from '@/api/compatibility'
import { QueryState } from '@/components/shared'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

const STATUS_LABELS: Record<CompatibilityStatus, string> = {
  detected: '已探测',
  not_detected: '未探测到',
  not_configured: '未配置探测',
  probe_failed: '探测失败',
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

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const nextData = await getCompatibilityStatus()
      if (!nextData?.probe || !nextData.facade || !Array.isArray(nextData.duplicate_warnings) || !nextData.tool_aliases || !Array.isArray(nextData.recommended_settings) || !nextData.documentation) {
        throw new Error('兼容状态响应缺少探测证据或能力字段。')
      }
      setData(nextData)
    } catch (nextError) {
      setError(nextError)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const queryStatus = loading ? 'loading' : error ? 'error' : data ? 'success' : 'unknown'
  return (
    <QueryState status={queryStatus} error={error} onRetry={() => void load()} title="兼容状态加载失败" loadingRows={5}>
      {data ? (
        <div className="flex flex-col gap-6">
          {data.probe.status === 'probe_failed' ? (
            <Alert variant="destructive"><AlertTriangleIcon /><AlertTitle>插件探测失败，结论不可判定</AlertTitle><AlertDescription>{data.probe.error ?? '请检查探测来源后重试。'}</AlertDescription></Alert>
          ) : data.probe.status === 'not_configured' ? (
            <Alert><CircleHelpIcon /><AlertTitle>插件探测未配置</AlertTitle><AlertDescription>这不等同于“没有重复插件”；当前没有可执行的 registry 探测源。</AlertDescription></Alert>
          ) : data.duplicate_warnings.length ? (
            <Alert variant="destructive"><AlertTriangleIcon /><AlertTitle>重复记忆插件风险</AlertTitle><AlertDescription>{data.duplicate_warnings.map((item) => item.message ?? item.name).join('；')}</AlertDescription></Alert>
          ) : (
            <Alert><CheckCircle2Icon /><AlertTitle>本次探测未发现已知重复插件</AlertTitle><AlertDescription>该结论仅对显示的来源和检查时间有效。</AlertDescription></Alert>
          )}

          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <CardTitle>插件生态探测</CardTitle>
                <Badge variant={data.probe.status === 'detected' ? 'default' : 'outline'}>{STATUS_LABELS[data.probe.status]}</Badge>
              </div>
              <CardDescription>明确区分已探测、未探测到、未配置和探测失败。</CardDescription>
            </CardHeader>
            <CardContent><FactMeta fact={data.probe} /></CardContent>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <CapabilityCard title="LivingMemory Facade" capability={data.facade} />
            {Object.entries(data.tool_aliases).map(([name, capability]) => <CapabilityCard key={name} title={name} capability={capability} />)}
          </div>

          <Card>
            <CardHeader><CardTitle>当前运行模式</CardTitle><CardDescription>来源为实际插件配置解析结果。</CardDescription></CardHeader>
            <CardContent className="space-y-2 text-sm">
              <Badge variant="secondary">{String(data.runtime?.mode ?? '未知')}</Badge>
              <p className="text-muted-foreground">来源：{String(data.runtime?.source ?? '未知')}；检查时间：{String(data.runtime?.checked_at ?? '未知')}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>建议</CardTitle><CardDescription>只根据当前 capability 与冲突证据生成。</CardDescription></CardHeader>
            <CardContent><ul className="list-disc space-y-2 pl-5 text-sm">{data.recommended_settings.map((item) => <li key={item}>{item}</li>)}</ul></CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>静态接口文档</CardTitle><CardDescription>{data.documentation.notice}</CardDescription></CardHeader>
            <CardContent className="space-y-4 text-sm">
              <div><h3 className="font-medium">Facade 接口</h3><ul className="mt-2 list-disc space-y-1 pl-5 font-mono">{data.documentation.facade_interfaces.map((item) => <li key={item}>{item}</li>)}</ul></div>
              <div><h3 className="font-medium">工具别名说明</h3><dl className="mt-2 space-y-2">{Object.entries(data.documentation.tool_aliases).map(([name, description]) => <div key={name}><dt className="font-mono font-medium">{name}</dt><dd className="text-muted-foreground">{description}</dd></div>)}</dl></div>
            </CardContent>
          </Card>
        </div>
      ) : null}
    </QueryState>
  )
}
