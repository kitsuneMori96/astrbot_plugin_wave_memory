import { useCallback, useEffect, useState } from 'react'
import { Bot, FileCode2, Import, Loader2, Pencil, Plus, RotateCcw, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import {
  createPersona,
  deletePersona,
  importFromAstrbot,
  listBindings,
  listPersonas,
  listTemplates,
  removeBinding,
  resetTemplate,
  saveTemplate,
  setBinding,
  updatePersona,
  type PersonaBinding,
  type PersonaItem,
  type PromptTemplate,
} from '@/api/prompts'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'

function formatTime(seconds: unknown): string {
  const s = Number(seconds)
  if (!Number.isFinite(s) || s <= 0) return '-'
  return new Date(s * 1000).toLocaleString('zh-CN')
}

export function PromptsPage() {
  const [loading, setLoading] = useState(true)
  const [personas, setPersonas] = useState<PersonaItem[]>([])
  const [bindings, setBindings] = useState<PersonaBinding[]>([])
  const [templates, setTemplates] = useState<PromptTemplate[]>([])

  // 人设编辑
  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<PersonaItem | null>(null)
  const [formName, setFormName] = useState('')
  const [formPrompt, setFormPrompt] = useState('')
  const [saving, setSaving] = useState(false)

  // 模板编辑
  const [activeKey, setActiveKey] = useState('')
  const [tplContent, setTplContent] = useState('')
  const [tplSaving, setTplSaving] = useState(false)

  // 绑定表单
  const [botScopeId, setBotScopeId] = useState('')
  const [groupScopeId, setGroupScopeId] = useState('')

  const reload = useCallback(async () => {
    try {
      const [p, b, t] = await Promise.all([listPersonas(), listBindings(), listTemplates()])
      setPersonas(p)
      setBindings(b)
      setTemplates(t)
      if (t.length > 0 && !t.find((x) => x.key === activeKey)) {
        setActiveKey(t[0].key)
        setTplContent(t[0].content)
      }
    } catch (e) {
      toast.error(`加载失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const selectTemplate = (key: string) => {
    setActiveKey(key)
    const tpl = templates.find((x) => x.key === key)
    setTplContent(tpl?.content ?? '')
  }

  const openCreate = () => {
    setEditing(null)
    setFormName('')
    setFormPrompt('')
    setEditorOpen(true)
  }

  const openEdit = (p: PersonaItem) => {
    setEditing(p)
    setFormName(p.name)
    setFormPrompt(p.system_prompt)
    setEditorOpen(true)
  }

  const saveEditor = async () => {
    if (!formName.trim()) {
      toast.error('名称不能为空')
      return
    }
    setSaving(true)
    try {
      if (editing) {
        await updatePersona(editing.id, { name: formName.trim(), system_prompt: formPrompt })
        toast.success('人设已更新')
      } else {
        await createPersona({ name: formName.trim(), system_prompt: formPrompt })
        toast.success('人设已创建')
      }
      setEditorOpen(false)
      await reload()
    } catch (e) {
      toast.error(`保存失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSaving(false)
    }
  }

  const doDelete = async (p: PersonaItem) => {
    if (!window.confirm(`删除人设「${p.name}」？其所有绑定将一并移除。`)) return
    await deletePersona(p.id)
    toast.success('已删除')
    await reload()
  }

  const doImport = async () => {
    try {
      const r = await importFromAstrbot()
      toast.success(`导入 ${r.imported} 个；跳过 ${r.skipped.length} 个`)
      await reload()
    } catch (e) {
      toast.error(`导入失败: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const saveTemplateContent = async () => {
    setTplSaving(true)
    try {
      await saveTemplate(activeKey, tplContent)
      toast.success('模板已保存，运行时即时生效')
      await reload()
    } catch (e) {
      toast.error(`保存失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setTplSaving(false)
    }
  }

  const doResetTemplate = async () => {
    try {
      const r = await resetTemplate(activeKey)
      setTplContent(r.content)
      toast.success('已恢复默认')
      await reload()
    } catch (e) {
      toast.error(`恢复失败: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const bindPersona = async (scope: string, personaIdStr: string, scopeId: string) => {
    const pid = Number(personaIdStr)
    if (!pid) {
      toast.error('请选择人设')
      return
    }
    await setBinding(scope, pid, scopeId)
    toast.success('绑定已保存')
    await reload()
  }

  const activeTpl = templates.find((x) => x.key === activeKey)

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-4 p-4">
      <Tabs defaultValue="personas">
        <TabsList>
          <TabsTrigger value="personas">人设库</TabsTrigger>
          <TabsTrigger value="bindings">绑定关系</TabsTrigger>
          <TabsTrigger value="templates">架构模板</TabsTrigger>
        </TabsList>

        {/* ─── 人设库 ─── */}
        <TabsContent value="personas" className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              wave 自成人设：按 群绑定 &gt; bot 绑定 &gt; 全局默认 解析注入。
            </p>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={doImport}>
                <Import className="mr-1 h-4 w-4" /> 从 AstrBot 导入
              </Button>
              <Button size="sm" onClick={openCreate}>
                <Plus className="mr-1 h-4 w-4" /> 新建人设
              </Button>
            </div>
          </div>
          <Card>
            <CardContent className="pt-6">
              {personas.length === 0 ? (
                <p className="py-8 text-center text-sm text-muted-foreground">暂无人设。可从 AstrBot 导入或新建。</p>
              ) : (
                <div className="space-y-2">
                  {personas.map((p) => (
                    <div key={p.id} className="flex items-center gap-3 rounded-lg border p-3">
                      <Badge variant={p.enabled ? 'default' : 'secondary'}>{p.enabled ? '启用' : '停用'}</Badge>
                      <span className="font-medium">{p.name}</span>
                      <span className="flex-1 truncate text-sm text-muted-foreground">
                        {p.system_prompt.slice(0, 80)}
                      </span>
                      <span className="text-xs text-muted-foreground">{formatTime(p.updated_at)}</span>
                      <Button variant="ghost" size="sm" onClick={() => openEdit(p)}>
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => doDelete(p)}>
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ─── 绑定关系 ─── */}
        <TabsContent value="bindings" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Bot className="h-4 w-4" /> 设置绑定
              </CardTitle>
              <CardDescription>群绑定 &gt; bot 绑定 &gt; 全局默认；scope_id 留空仅对全局有效</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-3">
              <div className="space-y-2">
                <p className="text-sm font-medium">全局默认</p>
                <GlobalBind personas={personas} bindings={bindings} onBind={bindPersona} />
              </div>
              <div className="space-y-2">
                <p className="text-sm font-medium">按 bot（db_id）</p>
                <Input placeholder="bot db_id，如 yushu" value={botScopeId} onChange={(e) => setBotScopeId(e.target.value)} />
                <ScopedBind
                  personas={personas}
                  scope="bot"
                  scopeId={botScopeId}
                  disabled={!botScopeId.trim()}
                  onBind={bindPersona}
                />
              </div>
              <div className="space-y-2">
                <p className="text-sm font-medium">按群（group_id）</p>
                <Input placeholder="群号" value={groupScopeId} onChange={(e) => setGroupScopeId(e.target.value)} />
                <ScopedBind
                  personas={personas}
                  scope="group"
                  scopeId={groupScopeId}
                  disabled={!groupScopeId.trim()}
                  onBind={bindPersona}
                />
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">当前绑定 ({bindings.length})</CardTitle>
            </CardHeader>
            <CardContent>
              {bindings.length === 0 ? (
                <p className="text-sm text-muted-foreground">暂无绑定。</p>
              ) : (
                <div className="space-y-2">
                  {bindings.map((b) => (
                    <div key={b.id} className="flex items-center gap-3 rounded-lg border p-3">
                      <Badge variant="outline">{b.scope}</Badge>
                      <span className="text-sm">{b.scope_id || '(全局)'}</span>
                      <span className="flex-1">→ {b.persona_name ?? `#${b.persona_id}`}</span>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={async () => {
                          await removeBinding(b.scope, b.scope_id)
                          toast.success('已解除')
                          await reload()
                        }}
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ─── 架构模板 ─── */}
        <TabsContent value="templates" className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Planner 判定 / 风格指令 / 延续指令 / 安全边界等架构提示词。变量用 {'{'}name{'}'} 占位，
            未提供的变量渲染为空。
          </p>
          <div className="grid gap-4 md:grid-cols-[280px_1fr]">
            <ScrollArea className="h-[520px] rounded-lg border">
              <div className="space-y-1 p-2">
                {templates.map((t) => (
                  <button
                    key={t.key}
                    className={`w-full rounded-md px-3 py-2 text-left text-sm hover:bg-accent ${
                      t.key === activeKey ? 'bg-accent' : ''
                    }`}
                    onClick={() => selectTemplate(t.key)}
                  >
                    <div className="flex items-center gap-2">
                      <FileCode2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      <span className="truncate font-medium">{t.name}</span>
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <code className="text-xs text-muted-foreground">{t.key}</code>
                      {t.is_custom && (
                        <Badge variant="secondary" className="h-5 px-1.5 text-xs">
                          已自定义
                        </Badge>
                      )}
                    </div>
                  </button>
                ))}
              </div>
            </ScrollArea>
            <Card>
              <CardHeader>
                <CardTitle className="text-base">{activeTpl?.name ?? '选择模板'}</CardTitle>
                <CardDescription>
                  可用变量：{activeTpl?.variables.length ? activeTpl.variables.map((v) => `{${v}}`).join(' ') : '无'}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <Textarea
                  className="min-h-[360px] font-mono text-sm"
                  value={tplContent}
                  onChange={(e) => setTplContent(e.target.value)}
                  disabled={!activeKey}
                />
                <div className="flex justify-end gap-2">
                  <Button variant="outline" onClick={doResetTemplate} disabled={!activeKey}>
                    <RotateCcw className="mr-1 h-4 w-4" /> 恢复默认
                  </Button>
                  <Button onClick={saveTemplateContent} disabled={tplSaving || !activeKey}>
                    {tplSaving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null} 保存
                  </Button>
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>

      {/* 人设编辑 Dialog */}
      <Dialog open={editorOpen} onOpenChange={setEditorOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editing ? '编辑人设' : '新建人设'}</DialogTitle>
            <DialogDescription>system_prompt 将注入到 LLM 的 system 段（wave_persona 块）</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <Input placeholder="人设名称（如 二阶堂真红）" value={formName} onChange={(e) => setFormName(e.target.value)} />
            <Textarea
              placeholder="角色设定 / 说话风格 / 台词锚……"
              className="min-h-[280px]"
              value={formPrompt}
              onChange={(e) => setFormPrompt(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditorOpen(false)}>
              取消
            </Button>
            <Button onClick={saveEditor} disabled={saving}>
              {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null} 保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function GlobalBind({
  personas,
  bindings,
  onBind,
}: {
  personas: PersonaItem[]
  bindings: PersonaBinding[]
  onBind: (scope: string, personaId: string, scopeId: string) => Promise<void>
}) {
  const current = bindings.find((b) => b.scope === 'global')
  return (
    <>
      <Select onValueChange={(v) => void onBind('global', v, '')}>
        <SelectTrigger>
          <SelectValue placeholder={current ? current.persona_name ?? `#${current.persona_id}` : '选择人设'} />
        </SelectTrigger>
        <SelectContent>
          {personas.filter((p) => p.enabled).map((p) => (
            <SelectItem key={p.id} value={String(p.id)}>
              {p.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {current && <p className="text-xs text-muted-foreground">当前: {current.persona_name ?? `#${current.persona_id}`}</p>}
    </>
  )
}

function ScopedBind({
  personas,
  scope,
  scopeId,
  disabled,
  onBind,
}: {
  personas: PersonaItem[]
  scope: string
  scopeId: string
  disabled?: boolean
  onBind: (scope: string, personaId: string, scopeId: string) => Promise<void>
}) {
  return (
    <Select disabled={disabled} onValueChange={(v) => void onBind(scope, v, scopeId)}>
      <SelectTrigger>
        <SelectValue placeholder={disabled ? '先填 scope_id' : '选择人设'} />
      </SelectTrigger>
      <SelectContent>
        {personas.filter((p) => p.enabled).map((p) => (
          <SelectItem key={p.id} value={String(p.id)}>
            {p.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
