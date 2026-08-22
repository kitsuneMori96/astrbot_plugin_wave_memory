import { useEffect, useState } from 'react'
import { AlertCircleIcon, Loader2Icon, SaveIcon, Undo2Icon } from 'lucide-react'
import { toast } from 'sonner'

import {
  getConfigSchema,
  getHotConfig,
  listProviders,
  saveFullConfig,
  saveHotConfig,
  type ConfigGroup,
  type HotParam,
  type ProviderPayload,
} from '@/api/config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

export function SettingsPage() {
  const [activeTab, setActiveTab] = useState('full')
  
  // 全量配置状态
  const [schemaGroups, setSchemaGroups] = useState<ConfigGroup[]>([])
  const [originalSchemaGroups, setOriginalSchemaGroups] = useState<ConfigGroup[]>([])
  const [providers, setProviders] = useState<ProviderPayload[]>([])
  const [openGroups, setOpenGroups] = useState<string[]>([])
  const [schemaSearch, setSchemaSearch] = useState('')
  
  // 热参配置状态
  const [hotParams, setHotParams] = useState<HotParam[]>([])
  const [originalHotParams, setOriginalHotParams] = useState<HotParam[]>([])

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // 多维度 Dirty 检测
  const isSchemaDirty = JSON.stringify(schemaGroups) !== JSON.stringify(originalSchemaGroups)
  const isHotDirty = JSON.stringify(hotParams) !== JSON.stringify(originalHotParams)
  const isDirty = isSchemaDirty || isHotDirty

  async function loadData() {
    setLoading(true)
    setError('')
    try {
      const [schemaData, hotData, providersData] = await Promise.all([
        getConfigSchema(),
        getHotConfig(),
        listProviders(),
      ])
      
      setSchemaGroups(schemaData.groups ?? [])
      setOriginalSchemaGroups(JSON.parse(JSON.stringify(schemaData.groups ?? [])))
      setHotParams(hotData.params ?? [])
      setOriginalHotParams(JSON.parse(JSON.stringify(hotData.params ?? [])))
      setProviders(providersData.providers ?? [])
      
      // 默认展开第一个分组
      if ((schemaData.groups ?? []).length > 0) {
        setOpenGroups([schemaData.groups[0].key])
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '配置数据加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadData()
  }, [])

  // 离开页面保护
  useEffect(() => {
    if (!isDirty) return
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = '您当前有未保存的配置修改，确定离开吗？'
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [isDirty])

  // 全量配置折叠展开控制
  function toggleGroup(key: string) {
    setOpenGroups((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    )
  }

  // 快捷更新全量标量值
  function updateScalarValue(groupKey: string, nextValue: unknown) {
    setSchemaGroups((prev) =>
      prev.map((g) => {
        if (g.key === groupKey) {
          return { ...g, value: nextValue }
        }
        return g
      })
    )
  }

  // 快捷更新全量对象子项值
  function updateObjectItemValue(groupKey: string, itemKey: string, nextValue: unknown) {
    setSchemaGroups((prev) =>
      prev.map((g) => {
        if (g.key === groupKey) {
          const nextItems = (g.items ?? []).map((it) => {
            if (it.key === itemKey) {
              return { ...it, value: nextValue }
            }
            return it
          })
          return { ...g, items: nextItems }
        }
        return g
      })
    )
  }

  // 更新热更新参数
  function updateHotValue(key: string, nextValue: number) {
    setHotParams((prev) =>
      prev.map((p) => {
        if (p.key === key) {
          return { ...p, current: nextValue }
        }
        return p
      })
    )
  }

  // 保存全量普通配置
  async function handleSaveFull() {
    setSaving(true)
    try {
      const payload: Record<string, unknown> = {}
      schemaGroups.forEach((g) => {
        if (g.kind === 'object') {
          const obj: Record<string, unknown> = {}
          ;(g.items ?? []).forEach((it) => {
            obj[it.key] = it.value
          })
          payload[g.key] = obj
        } else {
          payload[g.key] = g.value
        }
      })

      const res = await saveFullConfig(payload)
      if (res.ok) {
        toast.success(res.message ?? '配置保存成功，部分选项需重启后生效')
        setOriginalSchemaGroups(JSON.parse(JSON.stringify(schemaGroups)))
      } else {
        throw new Error(res.error ?? '保存失败')
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '配置保存失败')
    } finally {
      setSaving(false)
    }
  }

  // 保存实时热参
  async function handleSaveHot() {
    setSaving(true)
    try {
      const payload: Record<string, unknown> = {}
      hotParams.forEach((p) => {
        payload[p.key] = p.current
      })

      const res = await saveHotConfig(payload)
      if (res.ok) {
        toast.success('热更新参数已实时生效，并已持久化保存')
        setOriginalHotParams(JSON.parse(JSON.stringify(hotParams)))
      } else {
        const errMsg = res.errors?.join('；') || '应用热更新配置失败'
        throw new Error(errMsg)
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '热更新保存失败')
    } finally {
      setSaving(false)
    }
  }

  // 恢复单项全量配置到默认值
  function handleResetItemDefault(groupKey: string, itemKey?: string) {
    if (itemKey) {
      const orig = originalSchemaGroups.find((g) => g.key === groupKey)
      const origItem = orig?.items?.find((it) => it.key === itemKey)
      if (origItem) {
        updateObjectItemValue(groupKey, itemKey, origItem.default)
        toast.success(`已重置 ${itemKey} 为默认值`)
      }
    } else {
      const orig = originalSchemaGroups.find((g) => g.key === groupKey)
      if (orig) {
        updateScalarValue(groupKey, orig.default)
        toast.success(`已重置为默认值`)
      }
    }
  }

  // 过滤后的配置分组
  const filteredGroups = schemaGroups.filter((g) => {
    if (!schemaSearch) return true
    const search = schemaSearch.toLowerCase()
    const matchesGroup =
      g.key.toLowerCase().includes(search) ||
      g.description.toLowerCase().includes(search) ||
      g.hint.toLowerCase().includes(search)
    
    if (matchesGroup) return true

    if (g.kind === 'object' && g.items) {
      return g.items.some(
        (it) =>
          it.key.toLowerCase().includes(search) ||
          it.description.toLowerCase().includes(search) ||
          it.hint.toLowerCase().includes(search)
      )
    }
    return false
  })

  if (loading) {
    return (
      <div className="flex flex-col gap-6">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    )
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertCircleIcon />
        <AlertTitle>配置加载失败</AlertTitle>
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-4 py-4 shrink-0">
          <div className="flex flex-col gap-1">
            <CardTitle>运行时调参</CardTitle>
            <CardDescription>
              实时热调参滑块修改立即生效；全量 Schema 属静态配置，日常请前往 AstrBot 6185 配置页管理。
            </CardDescription>
          </div>
          {isDirty ? (
            <Badge variant="secondary" className="animate-pulse bg-amber-500/10 text-amber-500 border-amber-500/20">
              有未保存的修改
            </Badge>
          ) : null}
        </CardHeader>
      </Card>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid w-full grid-cols-2 max-w-md shrink-0">
          <TabsTrigger value="full">静态 Schema（低频）</TabsTrigger>
          <TabsTrigger value="hot">⚡ 实时热调参</TabsTrigger>
        </TabsList>

        {/* ═══ Tab 1: 全量普通配置 ═══ */}
        <TabsContent value="full" className="mt-4 flex flex-col gap-4">
          <Card>
            <CardContent className="pt-6 flex flex-col gap-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <Input
                  className="max-w-xs"
                  placeholder="搜索配置项..."
                  value={schemaSearch}
                  onChange={(e) => setSchemaSearch(e.target.value)}
                />
                <div className="flex items-center gap-2">
                  <Button disabled={saving} variant="outline" onClick={() => void loadData()}>
                    <Undo2Icon data-icon="inline-start" />
                    重置修改
                  </Button>
                  <Button disabled={saving || !isSchemaDirty} onClick={() => void handleSaveFull()}>
                    {saving ? <Loader2Icon className="animate-spin" data-icon="inline-start" /> : <SaveIcon data-icon="inline-start" />}
                    保存全量配置
                  </Button>
                </div>
              </div>

              {filteredGroups.length === 0 ? (
                <p className="text-sm text-muted-foreground p-6 text-center">未匹配到任何配置项。</p>
              ) : (
                <div className="flex flex-col gap-3">
                  {filteredGroups.map((g) => {
                    const isOpen = openGroups.includes(g.key)
                    return (
                      <div key={g.key} className="rounded-lg border border-border/50 bg-card overflow-hidden">
                        <button
                          className="flex w-full items-center justify-between p-4 text-left hover:bg-muted/30 transition-all"
                          onClick={() => toggleGroup(g.key)}
                        >
                          <div className="flex flex-col gap-1 min-w-0">
                            <span className="text-sm font-semibold text-foreground flex items-center gap-2">
                              {g.description}
                              <span className="font-mono text-xs font-normal text-muted-foreground">({g.key})</span>
                            </span>
                            {g.hint ? <span className="text-xs text-muted-foreground truncate">{g.hint}</span> : null}
                          </div>
                          <Badge variant="outline" className="font-mono text-[10px]">
                            {g.kind === 'object' ? `${g.items?.length ?? 0} 项` : '标量'}
                          </Badge>
                        </button>

                        {isOpen ? (
                          <div className="border-t border-border/50 p-4 bg-muted/10">
                            {g.kind === 'object' ? (
                              <div className="grid gap-4 sm:grid-cols-2">
                                {(g.items ?? []).map((it) => (
                                  <div key={it.key} className={it.type === 'bool' ? 'sm:col-span-2' : ''}>
                                    {it.type === 'bool' ? (
                                      <div className="flex items-start justify-between gap-4 rounded-lg border p-3 bg-background">
                                        <div className="flex flex-col gap-1">
                                          <span className="text-xs font-medium text-foreground">{it.description}</span>
                                          {it.hint ? <span className="text-[11px] text-muted-foreground">{it.hint}</span> : null}
                                          <span className="font-mono text-[10px] text-muted-foreground">键：{it.key} | 默认：{String(it.default)}</span>
                                        </div>
                                        <div className="flex items-center gap-2 shrink-0">
                                          <Button variant="ghost" className="size-6 p-0 text-muted-foreground hover:text-foreground" onClick={() => handleResetItemDefault(g.key, it.key)} title="恢复默认">
                                            <Undo2Icon className="size-3" />
                                          </Button>
                                          <Switch checked={Boolean(it.value)} onCheckedChange={(checked) => updateObjectItemValue(g.key, it.key, checked)} />
                                        </div>
                                      </div>
                                    ) : it.special === 'select_provider' ? (
                                      <Field>
                                        <div className="flex items-center justify-between gap-3 mb-1">
                                          <FieldLabel>{it.description}</FieldLabel>
                                          <Button variant="ghost" className="size-6 p-0 text-muted-foreground hover:text-foreground" onClick={() => handleResetItemDefault(g.key, it.key)} title="恢复默认">
                                            <Undo2Icon className="size-3" />
                                          </Button>
                                        </div>
                                        <Select value={String(it.value ?? '')} onValueChange={(val) => updateObjectItemValue(g.key, it.key, val)}>
                                          <SelectTrigger>
                                            <SelectValue placeholder="未配置（留空禁用）" />
                                          </SelectTrigger>
                                          <SelectContent>
                                            <SelectItem value="">禁用 / 留空</SelectItem>
                                            {providers.map((p) => (
                                              <SelectItem key={p.id} value={p.id}>{p.id} ({p.model})</SelectItem>
                                            ))}
                                          </SelectContent>
                                        </Select>
                                        {it.hint ? <FieldDescription>{it.hint}</FieldDescription> : null}
                                        <div className="font-mono text-[9px] text-muted-foreground mt-1">键：{it.key} | 默认：{String(it.default ?? '空')}</div>
                                      </Field>
                                    ) : (
                                      <Field>
                                        <div className="flex items-center justify-between gap-3 mb-1">
                                          <FieldLabel>{it.description}</FieldLabel>
                                          <Button variant="ghost" className="size-6 p-0 text-muted-foreground hover:text-foreground" onClick={() => handleResetItemDefault(g.key, it.key)} title="恢复默认">
                                            <Undo2Icon className="size-3" />
                                          </Button>
                                        </div>
                                        <Input
                                          type={it.type === 'int' ? 'number' : 'text'}
                                          value={it.value === null || it.value === undefined ? '' : String(it.value)}
                                          onChange={(e) => updateObjectItemValue(g.key, it.key, it.type === 'int' ? (Number(e.target.value) || 0) : e.target.value)}
                                        />
                                        {it.hint ? <FieldDescription>{it.hint}</FieldDescription> : null}
                                        <div className="font-mono text-[9px] text-muted-foreground mt-1">键：{it.key} | 默认：{String(it.default ?? '空')}</div>
                                      </Field>
                                    )}
                                  </div>
                                ))}
                              </div>
                            ) : (
                              // 标量项
                              <div className="max-w-md">
                                {g.type === 'bool' ? (
                                  <div className="flex items-start justify-between gap-4 rounded-lg border p-3 bg-background">
                                    <div className="flex flex-col gap-1">
                                      <span className="text-xs font-medium text-foreground">{g.description}</span>
                                      {g.hint ? <span className="text-[11px] text-muted-foreground">{g.hint}</span> : null}
                                      <span className="font-mono text-[10px] text-muted-foreground">键：{g.key} | 默认：{String(g.default)}</span>
                                    </div>
                                    <div className="flex items-center gap-2 shrink-0">
                                      <Button variant="ghost" className="size-6 p-0 text-muted-foreground hover:text-foreground" onClick={() => handleResetItemDefault(g.key)} title="恢复默认">
                                        <Undo2Icon className="size-3" />
                                      </Button>
                                      <Switch checked={Boolean(g.value)} onCheckedChange={(checked) => updateScalarValue(g.key, checked)} />
                                    </div>
                                  </div>
                                ) : g.special === 'select_provider' ? (
                                  <Field>
                                    <div className="flex items-center justify-between gap-3 mb-1">
                                      <FieldLabel>{g.description}</FieldLabel>
                                      <Button variant="ghost" className="size-6 p-0 text-muted-foreground hover:text-foreground" onClick={() => handleResetItemDefault(g.key)} title="恢复默认">
                                        <Undo2Icon className="size-3" />
                                      </Button>
                                    </div>
                                    <Select value={String(g.value ?? '')} onValueChange={(val) => updateScalarValue(g.key, val)}>
                                      <SelectTrigger>
                                        <SelectValue placeholder="未配置（留空禁用）" />
                                      </SelectTrigger>
                                      <SelectContent>
                                        <SelectItem value="">禁用 / 留空</SelectItem>
                                        {providers.map((p) => (
                                          <SelectItem key={p.id} value={p.id}>{p.id} ({p.model})</SelectItem>
                                        ))}
                                      </SelectContent>
                                    </Select>
                                    {g.hint ? <FieldDescription>{g.hint}</FieldDescription> : null}
                                    <div className="font-mono text-[9px] text-muted-foreground mt-1">键：{g.key} | 默认：{String(g.default ?? '空')}</div>
                                  </Field>
                                ) : (
                                  <Field>
                                    <div className="flex items-center justify-between gap-3 mb-1">
                                      <FieldLabel>{g.description}</FieldLabel>
                                      <Button variant="ghost" className="size-6 p-0 text-muted-foreground hover:text-foreground" onClick={() => handleResetItemDefault(g.key)} title="恢复默认">
                                        <Undo2Icon className="size-3" />
                                      </Button>
                                    </div>
                                    <Input
                                      type={g.type === 'int' ? 'number' : 'text'}
                                      value={g.value === null || g.value === undefined ? '' : String(g.value)}
                                      onChange={(e) => updateScalarValue(g.key, g.type === 'int' ? (Number(e.target.value) || 0) : e.target.value)}
                                    />
                                    {g.hint ? <FieldDescription>{g.hint}</FieldDescription> : null}
                                    <div className="font-mono text-[9px] text-muted-foreground mt-1">键：{g.key} | 默认：{String(g.default ?? '空')}</div>
                                  </Field>
                                )}
                              </div>
                            )}
                          </div>
                        ) : null}
                      </div>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ═══ Tab 2: 实时热调参 ═══ */}
        <TabsContent value="hot" className="mt-4 flex flex-col gap-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-4 py-4 shrink-0">
              <div className="flex flex-col gap-1">
                <CardTitle>⚡ 实时更新调参</CardTitle>
                <CardDescription>
                  此处的滑块参数直接在内存中控制引擎算法细节，保存后实时在聊天中生效，无需重启容器！
                </CardDescription>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <Button disabled={saving} variant="outline" onClick={() => void loadData()}>
                  <Undo2Icon data-icon="inline-start" />
                  放弃修改
                </Button>
                <Button disabled={saving || !isHotDirty} onClick={() => void handleSaveHot()}>
                  {saving ? <Loader2Icon className="animate-spin" data-icon="inline-start" /> : <SaveIcon data-icon="inline-start" />}
                  应用热参
                </Button>
              </div>
            </CardHeader>
            <CardContent className="pt-6">
              {hotParams.length === 0 ? (
                <p className="text-sm text-muted-foreground p-6 text-center">暂无可调参选项。</p>
              ) : (
                <div className="grid gap-6 sm:grid-cols-2">
                  {hotParams.map((p) => {
                    const step = p.type === 'int' ? 1 : 0.01
                    return (
                      <div key={p.key} className="rounded-lg border p-4 bg-muted/10 flex flex-col justify-between gap-3">
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex flex-col gap-1">
                            <span className="text-sm font-semibold text-foreground">{p.description}</span>
                            <span className="font-mono text-[10px] text-muted-foreground">{p.key}</span>
                          </div>
                          <Badge variant="secondary" className="font-mono text-xs text-primary shrink-0">
                            {p.current?.toFixed?.(p.type === 'int' ? 0 : 3) ?? p.current}
                          </Badge>
                        </div>

                        <div className="flex flex-col gap-1.5 mt-2">
                          <input
                            type="range"
                            min={p.min}
                            max={p.max}
                            step={step}
                            value={p.current}
                            onChange={(e) => updateHotValue(p.key, Number(e.target.value))}
                            className="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-muted accent-primary hover:accent-primary/80 transition-all"
                          />
                          <div className="flex justify-between text-[10px] text-muted-foreground font-mono">
                            <span>{p.min}</span>
                            <span className="opacity-50">默认值：{p.default}</span>
                            <span>{p.max}</span>
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
