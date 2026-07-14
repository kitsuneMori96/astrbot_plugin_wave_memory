import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRightIcon, Loader2Icon, RotateCcwIcon, SaveIcon, ShieldCheckIcon, WandSparklesIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  applyChannelConfig,
  getChannelConfig,
  resetChannelConfigDefaults,
  safeValidation,
  validateChannelConfig,
  type ChannelConfigData,
  type ChannelDescriptor,
  type ChannelPatch,
  type ChannelSettings,
  type ChannelValidationPayload,
} from '@/api/channels'
import { getAgentFeedback, reviewConfigSuggestion, type AgentFeedbackPayload } from '@/api/review'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { ChannelConfigTable } from '@/pages/channels/ChannelConfigTable'
import { ChannelDiffCard } from '@/pages/channels/ChannelDiffCard'

const editableFields = ['enabled', 'priority', 'top_k', 'max_items', 'token_budget', 'timeout_ms', 'min_score'] as const
const numericFields = ['priority', 'top_k', 'max_items', 'token_budget', 'timeout_ms', 'min_score'] as const

const fieldHelp = [
  'enabled：是否参与注入',
  'priority：通道执行/拼接优先级',
  'top_k：检索类通道候选数',
  'max_items：非检索类通道最多注入条目',
  'token_budget：单通道预算',
  'timeout_ms：单通道超时',
  'min_score：检索命中最低分',
]

function serializePatch(draft: ChannelConfigData): ChannelPatch {
  const channels: Record<string, Partial<ChannelSettings>> = {}
  Object.entries(draft.channels ?? {}).forEach(([name, channel]) => {
    channels[name] = {}
    editableFields.forEach((field) => {
      if (field in channel) {
        const val = channel[field]
        if (numericFields.includes(field as never)) {
          // 极致类型保护：强制转为精确的 float/int
          channels[name][field] = val === null || val === undefined ? (null as never) : (Number(val) as never)
        } else {
          channels[name][field] = val as never
        }
      }
    })
    if (name === 'safety') {
      channels[name].enabled = true
    }
  })

  return {
    recent_dedup_minutes: Number(draft.recent_dedup_minutes ?? 30),
    trace_enabled: Boolean(draft.trace_enabled),
    channels,
  }
}

export function ChannelConfigPage() {
  const [draft, setDraft] = useState<ChannelConfigData | null>(null)
  const [original, setOriginal] = useState<ChannelConfigData | null>(null)
  const [runtime, setRuntime] = useState<Record<string, unknown>>({})
  const [descriptors, setDescriptors] = useState<ChannelDescriptor[]>([])
  const [revision, setRevision] = useState('')
  const [effectiveSince, setEffectiveSince] = useState<number | null>(null)
  const [verificationUrl, setVerificationUrl] = useState('/observatory')
  const [validation, setValidation] = useState<ChannelValidationPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [configSuggestions, setConfigSuggestions] = useState<Array<Record<string, unknown>>>([])
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)

  // 检测配置是否修改过
  const isDirty = draft && original && JSON.stringify(serializePatch(draft)) !== JSON.stringify(serializePatch(original))

  async function load() {
    setLoading(true)
    setError('')
    try {
      const [payload, feedbackPayload] = await Promise.all([
        getChannelConfig(),
        // 配置建议属于配置域；读取失败不能阻断通道配置本身。
        getAgentFeedback().catch((): AgentFeedbackPayload => ({ config_suggestions: [] })),
      ])
      if (!payload.current) throw new Error('服务端未返回当前有效通道配置')
      const currentData = payload.current
      setDraft(currentData)
      setOriginal(JSON.parse(JSON.stringify(currentData)))
      setRuntime(payload.runtime ?? {})
      setDescriptors(payload.descriptors ?? [])
      setRevision(payload.revision ?? '')
      setEffectiveSince(payload.effective_since ?? null)
      setVerificationUrl(payload.verification_url ?? '/observatory')
      setValidation(null)
      setConfigSuggestions(feedbackPayload.config_suggestions ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : '通道配置加载失败')
    } finally {
      setLoading(false)
    }
  }

  async function preview() {
    if (!draft) {
      return
    }
    setSaving(true)
    try {
      const result = safeValidation(await validateChannelConfig(serializePatch(draft)))
      setValidation(result)
      if (result.ok) {
        toast.success('通道配置校验通过')
      } else {
        toast.error(result.errors.join('；') || '通道配置校验失败')
      }
    } catch (err) {
      const result = safeValidation({ ok: false, errors: [err instanceof Error ? err.message : '通道配置校验失败'], diff: [] })
      setValidation(result)
      toast.error(result.errors[0])
    } finally {
      setSaving(false)
    }
  }

  async function apply() {
    if (!draft) {
      return
    }
    setSaving(true)
    try {
      const result = safeValidation(await applyChannelConfig(serializePatch(draft), validation?.preflight_token ?? ''))
      setValidation(result)
      if (result.ok && result.operation?.status === 'succeeded' && result.effective) {
        setDraft(result.effective)
        setOriginal(JSON.parse(JSON.stringify(result.effective)))
        setRevision(String(result.revision ?? ''))
        setEffectiveSince(typeof result.effective_since === 'number' ? result.effective_since : null)
        setVerificationUrl(result.verification_url ?? '/observatory')
        toast.success(result.message ?? '通道配置已应用并完成运行时回读')
      } else {
        toast.error(result.errors.join('；') || '通道配置应用失败')
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : '通道配置应用失败'
      setValidation(safeValidation({ ok: false, errors: [message], diff: [] }))
      toast.error(message)
    } finally {
      setSaving(false)
    }
  }

  async function resetDefaults() {
    setSaving(true)
    try {
      const result = safeValidation(await resetChannelConfigDefaults())
      setValidation(result)
      if (result.ok && result.operation?.status === 'succeeded' && result.effective) {
        setDraft(result.effective)
        setOriginal(JSON.parse(JSON.stringify(result.effective)))
        setRevision(String(result.revision ?? ''))
        setEffectiveSince(typeof result.effective_since === 'number' ? result.effective_since : null)
        setVerificationUrl(result.verification_url ?? '/observatory')
        toast.success(result.message ?? '已恢复默认通道配置')
      } else {
        toast.error(result.errors.join('；') || '恢复默认失败')
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : '恢复默认失败'
      setValidation(safeValidation({ ok: false, errors: [message], diff: [] }))
      toast.error(message)
    } finally {
      setSaving(false)
    }
  }

  async function handleSuggestionReview(id: number, action: 'approve' | 'reject' | 'ignore') {
    setSuggestionsLoading(true)
    try {
      const result = await reviewConfigSuggestion(id, action)
      if (!result.ok) throw new Error(result.error ?? '配置建议处理失败')
      toast.success(result.message ?? '配置建议状态已记录；配置不会自动应用')
      const next = await getAgentFeedback()
      setConfigSuggestions(next.config_suggestions ?? [])
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '配置建议处理失败')
    } finally {
      setSuggestionsLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  // 未保存离开防护
  useEffect(() => {
    if (!isDirty) return
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = '您当前有未保存的通道配置修改，确定离开吗？'
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [isDirty])

  if (loading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    )
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertTitle>通道配置加载失败</AlertTitle>
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    )
  }

  if (!draft) {
    return <p className="text-sm text-muted-foreground">暂无通道配置。</p>
  }

  const validationShape = safeValidation(validation)

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Channel Config · 通道热配置</CardTitle>
          <CardDescription>热更新注入通道参数；safety channel / 安全通道在 UI 中不可关闭。</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <FieldGroup className="grid gap-4 md:grid-cols-3">
            <Field>
              <FieldLabel htmlFor="runtime-mode">运行模式</FieldLabel>
              <div id="runtime-mode" className="flex min-h-10 flex-col justify-center rounded-md border px-3 py-2 text-sm">
                <span>{String(runtime.mode ?? draft.mode ?? 'unknown')}</span>
                <span className="text-muted-foreground">revision: {revision || '未记录'} · 生效时间：{effectiveSince ? new Date(effectiveSince * 1000).toLocaleString('zh-CN') : '未记录'}</span>
              </div>
            </Field>
            <Field>
              <FieldLabel htmlFor="dedup-minutes">近期去重分钟</FieldLabel>
              <Input
                id="dedup-minutes"
                inputMode="numeric"
                value={draft.recent_dedup_minutes ?? 30}
                onChange={(event) => setDraft({ ...draft, recent_dedup_minutes: Number(event.target.value) || 0 })}
              />
            </Field>
            <Field>
              <FieldLabel>Trace 记录开关</FieldLabel>
              <div className="flex h-10 items-center justify-between gap-3 rounded-md border px-3">
                <span className="text-sm text-muted-foreground">{draft.trace_enabled ? '已开启' : '已关闭'}</span>
                <Switch checked={Boolean(draft.trace_enabled)} onCheckedChange={(checked) => setDraft({ ...draft, trace_enabled: checked })} />
              </div>
            </Field>
          </FieldGroup>
          <Alert>
            <ShieldCheckIcon />
            <AlertTitle>安全边界</AlertTitle>
            <AlertDescription>安全通道始终保持启用，防止近期上下文重复和身份污染过滤被绕过。</AlertDescription>
          </Alert>
          <div className="flex flex-wrap items-center gap-3">
            <Button disabled={saving} onClick={() => void preview()}>
              {saving ? <Loader2Icon className="animate-spin" data-icon="inline-start" /> : <WandSparklesIcon data-icon="inline-start" />}
              校验预览
            </Button>
            <Button disabled={saving || !validationShape.ok || !validation?.preflight_token} onClick={() => void apply()}>
              {saving ? <Loader2Icon className="animate-spin" data-icon="inline-start" /> : <SaveIcon data-icon="inline-start" />}
              应用配置
            </Button>
            <Button disabled={saving} variant="outline" onClick={() => void resetDefaults()}>
              <RotateCcwIcon data-icon="inline-start" />
              恢复默认
            </Button>
            {validation ? <Badge variant={validationShape.ok ? 'secondary' : 'destructive'}>{validationShape.ok ? '校验通过' : '校验失败'}</Badge> : null}
            {isDirty ? (
              <Badge variant="secondary" className="animate-pulse">
                <WandSparklesIcon className="mr-1 size-3" />
                配置已修改（未保存）
              </Badge>
            ) : null}
            <Button asChild variant="outline">
              <Link to={verificationUrl}>
                去注入观测台验证最近 trace
                <ArrowRightIcon data-icon="inline-end" />
              </Link>
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>配置建议</CardTitle>
          <CardDescription>配置建议保留在配置域；人工批准只记录状态，不会绕过校验自动应用。</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {configSuggestions.length === 0 ? <p className="text-sm text-muted-foreground">暂无待处理配置建议。</p> : configSuggestions.map((suggestion, index) => {
            const id = Number(suggestion.id)
            const title = String(suggestion.suggestion ?? suggestion.problem ?? suggestion.title ?? `配置建议 ${index + 1}`)
            return (
              <div key={`config-suggestion-${String(suggestion.id ?? index)}`} className="flex flex-col gap-3 rounded-lg border p-3">
                <div className="flex items-start justify-between gap-3"><span className="font-medium">{title}</span><Badge variant="secondary">待处理</Badge></div>
                <p className="text-sm text-muted-foreground">范围：{String(suggestion.scope ?? '未指定')} · 通道：{String(suggestion.channel ?? '未指定')}</p>
                <p className="text-sm leading-relaxed">{String(suggestion.reason ?? suggestion.description ?? '服务端未提供补充说明。')}</p>
                <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">技术详情</summary><pre className="mt-2 max-h-48 overflow-auto rounded-md bg-muted p-2 font-mono">{JSON.stringify(suggestion, null, 2)}</pre></details>
                <div className="flex flex-wrap gap-2">
                  {(['approve', 'reject', 'ignore'] as const).map((action) => (
                    <Button key={action} size="sm" variant={action === 'approve' ? 'default' : action === 'reject' ? 'destructive' : 'secondary'} disabled={suggestionsLoading || !Number.isFinite(id)} onClick={() => void handleSuggestionReview(id, action)}>
                      {action === 'approve' ? '记录批准' : action === 'reject' ? '拒绝' : '忽略'}
                    </Button>
                  ))}
                </div>
              </div>
            )
          })}
        </CardContent>
      </Card>

      <details className="rounded-xl border border-border/70 bg-muted/10 px-4 py-3 text-sm">
        <summary className="cursor-pointer font-medium">参数说明</summary>
        <ul className="mt-3 grid gap-x-8 gap-y-2 text-muted-foreground md:grid-cols-2 xl:grid-cols-3">
          {fieldHelp.map((item) => <li key={item}>{item}</li>)}
        </ul>
      </details>

      <ChannelConfigTable draft={draft} descriptors={descriptors} onDraftChange={setDraft} />
      <ChannelDiffCard diff={validationShape.diff} validation={validation} />
    </div>
  )
}
