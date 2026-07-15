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
  type ChannelPatch,
  type ChannelSettings,
  type ChannelValidationPayload,
} from '@/api/channels'
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
  const [validation, setValidation] = useState<ChannelValidationPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // 检测配置是否修改过
  const isDirty = draft && original && JSON.stringify(serializePatch(draft)) !== JSON.stringify(serializePatch(original))

  async function load() {
    setLoading(true)
    setError('')
    try {
      const payload = await getChannelConfig()
      const currentData = payload.current ?? { channels: {}, recent_dedup_minutes: 30, trace_enabled: true }
      setDraft(currentData)
      setOriginal(JSON.parse(JSON.stringify(currentData)))
      setRuntime(payload.runtime ?? {})
      setValidation(null)
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
      const result = safeValidation(await applyChannelConfig(serializePatch(draft)))
      setValidation(result)
      if (result.ok) {
        setDraft(result.candidate ?? draft)
        setOriginal(JSON.parse(JSON.stringify(result.candidate ?? draft)))
        toast.success(result.message ?? '通道配置已热应用')
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
      if (result.ok) {
        setDraft(result.candidate ?? draft)
        setOriginal(JSON.parse(JSON.stringify(result.candidate ?? draft)))
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
              <div id="runtime-mode" className="flex h-10 items-center rounded-md border px-3 text-sm">
                {String(runtime.mode ?? draft.mode ?? '-')}
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
            <Button disabled={saving || (validation !== null && !validationShape.ok)} onClick={() => void apply()}>
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
              <Link to="/injection">
                去注入观测台验证最近 trace
                <ArrowRightIcon data-icon="inline-end" />
              </Link>
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>字段说明</CardTitle>
          <CardDescription>这些字段都是 WaveMemory 热参数；影响注入通道启用、优先级、预算和过滤阈值。</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {fieldHelp.map((item) => (
              <div key={item} className="rounded-lg border bg-muted/30 p-3 text-sm text-muted-foreground">
                {item}
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <ChannelConfigTable draft={draft} onDraftChange={setDraft} />
      <ChannelDiffCard diff={validationShape.diff} validation={validation} />
    </div>
  )
}
