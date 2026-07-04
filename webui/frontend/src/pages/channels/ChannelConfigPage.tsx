import { useEffect, useState } from 'react'
import { Loader2Icon, RotateCcwIcon, SaveIcon, ShieldCheckIcon, WandSparklesIcon } from 'lucide-react'
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
          <CardTitle>Channel Config</CardTitle>
          <CardDescription>热更新注入通道参数；安全通道在 UI 中不可关闭。</CardDescription>
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
              <FieldLabel>Trace 记录</FieldLabel>
              <div className="flex h-10 items-center justify-between gap-3 rounded-md border px-3">
                <span className="text-sm text-muted-foreground">trace_enabled</span>
                <Switch checked={Boolean(draft.trace_enabled)} onCheckedChange={(checked) => setDraft({ ...draft, trace_enabled: checked })} />
              </div>
            </Field>
          </FieldGroup>
          <Alert>
            <ShieldCheckIcon />
            <AlertTitle>安全边界</AlertTitle>
            <AlertDescription>safety channel 始终保持启用，防止近期上下文重复和身份污染过滤被绕过。</AlertDescription>
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
            {validation ? <Badge variant={validationShape.ok ? 'secondary' : 'destructive'}>{validationShape.ok ? '校验通过 (valid)' : '校验失败 (invalid)'}</Badge> : null}
            {isDirty ? (
              <Badge variant="secondary" className="animate-pulse bg-amber-500/10 text-amber-500 hover:bg-amber-500/10 border-amber-500/20">
                <WandSparklesIcon className="size-3 mr-1" />
                配置已修改（未保存）
              </Badge>
            ) : null}
          </div>
        </CardContent>
      </Card>

      <ChannelConfigTable draft={draft} onDraftChange={setDraft} />
      <ChannelDiffCard diff={validationShape.diff} validation={validation} />
    </div>
  )
}
