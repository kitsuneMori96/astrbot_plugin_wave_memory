import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertCircleIcon, Loader2Icon, SaveIcon, Undo2Icon } from 'lucide-react'
import { toast } from 'sonner'

import {
  getConfigSchema,
  getHotConfig,
  listProviders,
  saveFullConfig,
  saveHotConfig,
  type ConfigApplyMode,
  type ConfigGroup,
  type ConfigItem,
  type HotParam,
  type ProviderPayload,
} from '@/api/config'
import { FieldValueState, QueryState, type ApplyMode } from '@/components/shared'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

function cloneGroups(groups: ConfigGroup[]): ConfigGroup[] {
  return typeof structuredClone === 'function'
    ? structuredClone(groups)
    : groups.map((group) => ({ ...group, items: group.items?.map((item) => ({ ...item })) }))
}

function cloneHotParams(params: HotParam[]): HotParam[] {
  return params.map((param) => ({ ...param }))
}

function applyMode(mode?: ConfigApplyMode): ApplyMode {
  return mode === 'next_run' ? 'next-run' : (mode ?? 'unknown')
}

function pathState(item: ConfigItem | ConfigGroup) {
  return {
    defaultValue: item.default,
    savedValue: item.saved,
    effectiveValue: item.effective,
    applyMode: applyMode(item.apply_mode),
    effectiveSince: item.effective_since,
  }
}

function changedPayload(current: ConfigGroup[], original: ConfigGroup[]): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  for (const group of current) {
    const previous = original.find((item) => item.key === group.key)
    if (group.kind === 'object') {
      const changed: Record<string, unknown> = {}
      for (const item of group.items ?? []) {
        const oldItem = previous?.items?.find((candidate) => candidate.key === item.key)
        if (!oldItem || !Object.is(item.value, oldItem.value)) changed[item.key] = item.value
      }
      if (Object.keys(changed).length) payload[group.key] = changed
    } else if (!previous || !Object.is(group.value, previous.value)) {
      payload[group.key] = group.value
    }
  }
  return payload
}

export function SettingsPage() {
  const [activeTab, setActiveTab] = useState('static')
  const [schemaGroups, setSchemaGroups] = useState<ConfigGroup[]>([])
  const [originalGroups, setOriginalGroups] = useState<ConfigGroup[]>([])
  const [hotParams, setHotParams] = useState<HotParam[]>([])
  const [originalHotParams, setOriginalHotParams] = useState<HotParam[]>([])
  const [providers, setProviders] = useState<ProviderPayload[]>([])
  const [warnings, setWarnings] = useState<Array<{ key: string; message: string }>>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [schema, hot, providerData] = await Promise.all([getConfigSchema(), getHotConfig(), listProviders()])
      setSchemaGroups(schema.groups ?? [])
      setOriginalGroups(cloneGroups(schema.groups ?? []))
      setWarnings(schema.warnings ?? [])
      setHotParams(cloneHotParams(hot.params ?? []))
      setOriginalHotParams(cloneHotParams(hot.params ?? []))
      setProviders(providerData.providers ?? [])
    } catch (nextError) {
      setError(nextError)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void loadData() }, [loadData])

  const fullPayload = useMemo(() => changedPayload(schemaGroups, originalGroups), [schemaGroups, originalGroups])
  const dirtyHot = useMemo(() => hotParams.filter((item) => {
    const old = originalHotParams.find((candidate) => candidate.key === item.key)
    return !old || !Object.is(old.current, item.current)
  }), [hotParams, originalHotParams])
  const isDirty = Object.keys(fullPayload).length > 0 || dirtyHot.length > 0

  useEffect(() => {
    if (!isDirty) return
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [isDirty])

  function updateScalar(groupKey: string, value: unknown) {
    setSchemaGroups((groups) => groups.map((group) => group.key === groupKey ? { ...group, value } : group))
  }

  function updateItem(groupKey: string, itemKey: string, value: unknown) {
    setSchemaGroups((groups) => groups.map((group) => group.key === groupKey
      ? { ...group, items: group.items?.map((item) => item.key === itemKey ? { ...item, value } : item) }
      : group))
  }

  async function saveSchema() {
    setSaving(true)
    try {
      const response = await saveFullConfig(fullPayload)
      if (!response.ok) throw new Error(response.errors?.join('；') || response.error || '配置保存失败')
      toast.success(response.message || '配置已保存；运行时是否生效以回读值为准。')
      const schema = response.schema ?? await getConfigSchema()
      setSchemaGroups(cloneGroups(schema.groups ?? []))
      setOriginalGroups(cloneGroups(schema.groups ?? []))
      setWarnings(schema.warnings ?? [])
    } catch (nextError) {
      toast.error(nextError instanceof Error ? nextError.message : '配置保存失败')
    } finally {
      setSaving(false)
    }
  }

  async function saveHot() {
    setSaving(true)
    try {
      const payload = Object.fromEntries(dirtyHot.map((item) => [item.key, item.current]))
      const response = await saveHotConfig(payload)
      if (!response.ok) throw new Error(response.errors.join('；') || '热参数应用失败')
      toast.success(response.message || '热参数已应用；持久化状态以字段回读为准。')
      if (response.warnings?.length) toast.warning(response.warnings.join('；'))
      const nextParams = response.params ?? (await getHotConfig()).params ?? []
      setHotParams(cloneHotParams(nextParams))
      setOriginalHotParams(cloneHotParams(nextParams))
    } catch (nextError) {
      toast.error(nextError instanceof Error ? nextError.message : '热参数应用失败')
    } finally {
      setSaving(false)
    }
  }

  function editor(group: ConfigGroup, item?: ConfigItem) {
    const value = item ? item.value : group.value
    const type = item?.type ?? group.type
    const special = item?.special ?? group.special
    const setValue = (next: unknown) => item ? updateItem(group.key, item.key, next) : updateScalar(group.key, next)
    if (type === 'bool') return <Switch checked={value === true} onCheckedChange={setValue} aria-label={`修改 ${item?.description ?? group.description}`} />
    if (special === 'select_provider') {
      return (
        <Select value={String(value ?? '')} onValueChange={setValue}>
          <SelectTrigger><SelectValue placeholder="未配置" /></SelectTrigger>
          <SelectContent>
            {providers.map((provider) => <SelectItem key={provider.id} value={provider.id}>{provider.id} ({provider.model})</SelectItem>)}
          </SelectContent>
        </Select>
      )
    }
    const numeric = type === 'int' || type === 'float'
    return <Input type={numeric ? 'number' : 'text'} step={type === 'float' ? 'any' : undefined} value={value == null ? '' : String(value)} onChange={(event) => setValue(numeric && event.target.value !== '' ? Number(event.target.value) : event.target.value)} />
  }

  const visibleGroups = useMemo(() => {
    const term = search.trim().toLowerCase()
    return schemaGroups.map((group) => {
      const groupMatches = !term || `${group.key} ${group.description} ${group.hint}`.toLowerCase().includes(term)
      const mode = activeTab === 'restart' ? 'restart' : activeTab === 'static' ? 'next_run' : null
      if (group.kind === 'object') {
        const items = (group.items ?? []).filter((item) => {
          const textMatches = groupMatches || `${item.key} ${item.description} ${item.hint}`.toLowerCase().includes(term)
          return textMatches && (!mode || item.apply_mode === mode)
        })
        return items.length ? { ...group, items } : null
      }
      return groupMatches && (!mode || group.apply_mode === mode) ? group : null
    }).filter((group): group is ConfigGroup => group !== null)
  }, [activeTab, schemaGroups, search])

  const queryStatus = loading ? 'loading' : error ? 'error' : 'success'
  return (
    <QueryState status={queryStatus} error={error} onRetry={() => void loadData()} title="配置加载失败" loadingRows={6}>
      <div className="flex flex-col gap-6">
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><CardTitle>系统配置</CardTitle><CardDescription className="mt-2">分别展示默认值、已保存值和当前生效值；保存成功不代表运行时已经生效。</CardDescription></div>
              {isDirty ? <Badge variant="secondary">有未保存修改</Badge> : null}
            </div>
          </CardHeader>
        </Card>

        {warnings.length ? (
          <Alert><AlertCircleIcon /><AlertTitle>旧配置兼容诊断</AlertTitle><AlertDescription><ul className="list-disc space-y-1 pl-5">{warnings.map((item) => <li key={item.key}><span className="font-mono">{item.key}</span>：{item.message}</li>)}</ul></AlertDescription></Alert>
        ) : null}

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="grid h-auto w-full grid-cols-2 gap-1 sm:grid-cols-4">
            <TabsTrigger value="static">静态/下次运行</TabsTrigger>
            <TabsTrigger value="hot">实时热参数</TabsTrigger>
            <TabsTrigger value="restart">需重启参数</TabsTrigger>
            <TabsTrigger value="advanced">全部高级设置</TabsTrigger>
          </TabsList>

          <TabsContent value="hot" className="mt-4">
            <Card>
              <CardHeader><CardTitle>实时热参数</CardTitle><CardDescription>只有服务端回读的 effective 才表示当前进程实际使用；无持久化映射时会明确提示。</CardDescription></CardHeader>
              <CardContent className="space-y-4">
                <div className="flex justify-end gap-2"><Button variant="outline" onClick={() => void loadData()} disabled={saving}><Undo2Icon />放弃修改</Button><Button onClick={() => void saveHot()} disabled={saving || !dirtyHot.length}>{saving ? <Loader2Icon className="animate-spin" /> : <SaveIcon />}应用热参数</Button></div>
                {hotParams.map((param) => (
                  <div key={param.key} className="grid gap-4 rounded-lg border p-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,1fr)]">
                    <Field><FieldLabel>{param.description}</FieldLabel><Input type="number" min={param.min} max={param.max} step={param.type === 'int' ? 1 : 0.01} value={param.current} onChange={(event) => setHotParams((items) => items.map((item) => item.key === param.key ? { ...item, current: Number(event.target.value) } : item))} /><FieldDescription>{param.key}；范围 {param.min}–{param.max}。{param.error ?? ''}</FieldDescription></Field>
                    <FieldValueState label={param.key} defaultValue={param.default} savedValue={param.saved} effectiveValue={param.effective} applyMode="hot" effectiveSince={param.effective_since} />
                  </div>
                ))}
              </CardContent>
            </Card>
          </TabsContent>

          {(['static', 'restart', 'advanced'] as const).map((tab) => (
            <TabsContent key={tab} value={tab} className="mt-4 space-y-4">
              <Card>
                <CardContent className="flex flex-wrap items-center justify-between gap-3 pt-6">
                  <Input className="max-w-md" placeholder="搜索配置键、名称或说明" value={search} onChange={(event) => setSearch(event.target.value)} />
                  <div className="flex gap-2"><Button variant="outline" onClick={() => void loadData()} disabled={saving}><Undo2Icon />放弃修改</Button><Button onClick={() => void saveSchema()} disabled={saving || !Object.keys(fullPayload).length}>{saving ? <Loader2Icon className="animate-spin" /> : <SaveIcon />}保存配置</Button></div>
                </CardContent>
              </Card>
              {!visibleGroups.length ? <QueryState status="empty" title="没有匹配的配置项" description="请调整搜索词或切换配置分类。" /> : visibleGroups.map((group) => (
                <Card key={group.key}>
                  <CardHeader><CardTitle>{group.description}</CardTitle><CardDescription>{group.hint || group.key}</CardDescription></CardHeader>
                  <CardContent className="space-y-5">
                    {group.kind === 'object' ? (group.items ?? []).map((item) => (
                      <div key={item.key} className="grid gap-4 rounded-lg border p-4 xl:grid-cols-[minmax(16rem,0.8fr)_minmax(0,1.2fr)]">
                        <Field><div className="flex items-center justify-between gap-3"><FieldLabel>{item.description}</FieldLabel><div className="flex gap-1.5">{item.restart_required ? <Badge variant="secondary">需要重启</Badge> : null}{item.apply_mode === 'hot' ? <button type="button" onClick={() => setActiveTab('hot')} className="inline-flex items-center rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-xs font-semibold text-emerald-600 transition-colors hover:bg-emerald-500/20">⚡ 可实时热应用</button> : null}</div></div>{editor(group, item)}<FieldDescription>{item.hint}<br />键：<span className="font-mono">{group.key}.{item.key}</span>；来源：{item.source}；有效来源：{item.effective_source}。{item.error ? ` 诊断：${item.error}` : ''}</FieldDescription></Field>
                        <FieldValueState label={item.description} {...pathState(item)} />
                      </div>
                    )) : (
                      <div className="grid gap-4 xl:grid-cols-[minmax(16rem,0.8fr)_minmax(0,1.2fr)]">
                        <Field><div className="flex items-center justify-between gap-3"><FieldLabel>{group.description}</FieldLabel><div className="flex gap-1.5">{group.restart_required ? <Badge variant="secondary">需要重启</Badge> : null}{group.apply_mode === 'hot' ? <button type="button" onClick={() => setActiveTab('hot')} className="inline-flex items-center rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-xs font-semibold text-emerald-600 transition-colors hover:bg-emerald-500/20">⚡ 可实时热应用</button> : null}</div></div>{editor(group)}<FieldDescription>键：<span className="font-mono">{group.key}</span>；来源：{group.source}；有效来源：{group.effective_source}。{group.error ? ` 诊断：${group.error}` : ''}</FieldDescription></Field>
                        <FieldValueState label={group.description} {...pathState(group)} />
                      </div>
                    )}
                  </CardContent>
                </Card>
              ))}
            </TabsContent>
          ))}
        </Tabs>
      </div>
    </QueryState>
  )
}
