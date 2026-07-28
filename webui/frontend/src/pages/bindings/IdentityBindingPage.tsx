import { useCallback, useEffect, useState } from 'react'
import { PlusIcon, SearchIcon, Trash2Icon } from 'lucide-react'
import { toast } from 'sonner'

import { getBindings, createBinding, deleteBinding, type BindingItem } from '@/api/bindings'
import { getBlackboxPeople, type BlackboxPersonItem } from '@/api/blackbox'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AlertTriangleIcon } from 'lucide-react'

const PAGE_SIZE = 50

function platformBadge(platform: string) {
  switch (platform) {
    case 'qq': return <Badge className="bg-blue-500 hover:bg-blue-600">QQ</Badge>
    case 'wechat': return <Badge className="bg-green-500 hover:bg-green-600">微信</Badge>
    case 'telegram': return <Badge className="bg-sky-500 hover:bg-sky-600">Telegram</Badge>
    default: return <Badge variant="outline">{platform}</Badge>
  }
}

export function IdentityBindingSection() {
  const [bindings, setBindings] = useState<BindingItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [createOpen, setCreateOpen] = useState(false)
  const [formPlatform, setFormPlatform] = useState('qq')
  const [formLocal, setFormLocal] = useState('')
  const [formMaster, setFormMaster] = useState('')
  const [saving, setSaving] = useState(false)
  const [peopleList, setPeopleList] = useState<BlackboxPersonItem[]>([])
  const [peopleLoading, setPeopleLoading] = useState(false)
  const [localSearch, setLocalSearch] = useState('')
  const [masterSearch, setMasterSearch] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const payload = await getBindings({ search: search || undefined, limit: PAGE_SIZE, offset })
      setBindings(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (err) {
      setError(err instanceof Error ? err.message : '绑定数据读取失败')
    } finally {
      setLoading(false)
    }
  }, [search, offset])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!createOpen) return
    setPeopleLoading(true)
    getBlackboxPeople({ limit: 500 })
      .then(p => setPeopleList(p.items ?? []))
      .catch(() => toast.error('加载人物列表失败'))
      .finally(() => setPeopleLoading(false))
  }, [createOpen])

  const personLabel = (p: BlackboxPersonItem) => {
    const id = p.qq_id || p.user_id || ''
    const name = p.display_name || p.nickname || id
    const group = p.group_id ? ` @${p.group_id}` : ''
    return `${name} (${id}${group})`
  }

  const personId = (p: BlackboxPersonItem) => p.qq_id || p.user_id || ''

  const filteredPeople = (searchText: string, excludeId: string) =>
    peopleList.filter(p => {
      if (!searchText) return personId(p) !== excludeId
      const q = searchText.toLowerCase()
      const id = personId(p).toLowerCase()
      const label = personLabel(p).toLowerCase()
      return id !== excludeId && (id.includes(q) || label.includes(q))
    })

  const handleCreate = async () => {
    const local = formLocal.trim()
    const master = formMaster.trim()
    if (!local || !master) {
      toast.error('请选择子账号和主账号')
      return
    }
    if (local === master) {
      toast.error('子账号和主账号不能相同')
      return
    }
    setSaving(true)
    try {
      await createBinding({ local_id: local, platform: formPlatform, master_id: master })
      toast.success('绑定成功')
      setCreateOpen(false)
      resetForm()
      setOffset(0)
      void load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '绑定失败')
    } finally {
      setSaving(false)
    }
  }

  const resetForm = () => {
    setFormPlatform('qq')
    setFormLocal('')
    setFormMaster('')
    setLocalSearch('')
    setMasterSearch('')
  }

  const handleDelete = async (id: number) => {
    try {
      await deleteBinding(id)
      toast.success('已删除绑定')
      void load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 flex-1 min-w-[200px]">
              <div className="relative flex-1 max-w-sm">
                <SearchIcon className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="搜索 local_id 或 master_id"
                  className="pl-8"
                  value={search}
                  onChange={e => { setSearch(e.target.value); setOffset(0) }}
                />
              </div>
              <div className="text-sm text-muted-foreground whitespace-nowrap">
                共 {total} 条
              </div>
            </div>
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              <PlusIcon className="mr-1 h-4 w-4" />
              新增绑定
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-2/3" />
            </div>
          ) : error ? (
            <Alert variant="destructive">
              <AlertTriangleIcon />
              <AlertTitle>数据读取失败</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : bindings.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground text-sm">
              {search ? '没有匹配的绑定记录' : '暂无绑定，点击"新增绑定"添加'}
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>平台</TableHead>
                    <TableHead>平台用户 ID</TableHead>
                    <TableHead>绑定到 (master_id)</TableHead>
                    <TableHead>Bot</TableHead>
                    <TableHead className="w-20">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {bindings.map(b => (
                    <TableRow key={b.id}>
                      <TableCell>{platformBadge(b.platform)}</TableCell>
                      <TableCell className="font-mono text-xs">{b.local_id}</TableCell>
                      <TableCell className="font-mono text-xs">{b.master_id}</TableCell>
                      <TableCell className="text-xs">{b.bot_id}</TableCell>
                      <TableCell>
                        <Button variant="ghost" size="icon" onClick={() => handleDelete(b.id)} title="删除">
                          <Trash2Icon className="h-4 w-4 text-red-400" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              {totalPages > 1 && (
                <div className="flex items-center justify-between mt-4">
                  <div className="text-sm text-muted-foreground">
                    第 {currentPage} / {totalPages} 页
                  </div>
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" disabled={currentPage <= 1} onClick={() => setOffset(o => o - PAGE_SIZE)}>
                      上一页
                    </Button>
                    <Button variant="outline" size="sm" disabled={currentPage >= totalPages} onClick={() => setOffset(o => o + PAGE_SIZE)}>
                      下一页
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <Dialog open={createOpen} onOpenChange={v => { setCreateOpen(v); if (!v) resetForm() }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>新增身份绑定</DialogTitle>
            <DialogDescription>
              将某个平台用户的 ID 绑定到主 ID，绑定的用户将共享记忆和好感。
              例如将微信用户 wxid_xxx 绑定到 QQ 号 123456789。
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4">
            <div className="grid gap-2">
              <Label>平台</Label>
              <Select value={formPlatform} onValueChange={setFormPlatform}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="qq">QQ</SelectItem>
                  <SelectItem value="wechat">微信</SelectItem>
                  <SelectItem value="telegram">Telegram</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label>选择子账号 (local_id)</Label>
              {peopleLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Skeleton className="h-4 w-4 rounded-full" />
                  加载人物列表…
                </div>
              ) : (
                <>
                  <Input
                    placeholder="搜索用户…"
                    value={localSearch}
                    onChange={e => setLocalSearch(e.target.value)}
                  />
                  <div className="max-h-44 overflow-y-auto rounded-md border">
                    {filteredPeople(localSearch, formMaster).length === 0 ? (
                      <div className="p-3 text-sm text-muted-foreground text-center">无匹配用户</div>
                    ) : (
                      filteredPeople(localSearch, formMaster).slice(0, 60).map(p => (
                        <div
                          key={personId(p)}
                          className={`cursor-pointer px-3 py-1.5 text-sm hover:bg-accent ${
                            personId(p) === formLocal ? 'bg-accent font-medium' : ''
                          }`}
                          onClick={() => {
                            setFormLocal(personId(p))
                            if (personId(p) === formMaster) setFormMaster('')
                          }}
                        >
                          <span className="truncate block">{personLabel(p)}</span>
                        </div>
                      ))
                    )}
                  </div>
                </>
              )}
            </div>
            <div className="grid gap-2">
              <Label>选择主账号 (master_id)</Label>
              {peopleLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Skeleton className="h-4 w-4 rounded-full" />
                  加载人物列表…
                </div>
              ) : (
                <>
                  <Input
                    placeholder="搜索用户…"
                    value={masterSearch}
                    onChange={e => setMasterSearch(e.target.value)}
                  />
                  <div className="max-h-44 overflow-y-auto rounded-md border">
                    {filteredPeople(masterSearch, formLocal).length === 0 ? (
                      <div className="p-3 text-sm text-muted-foreground text-center">无匹配用户</div>
                    ) : (
                      filteredPeople(masterSearch, formLocal).slice(0, 60).map(p => (
                        <div
                          key={personId(p)}
                          className={`cursor-pointer px-3 py-1.5 text-sm hover:bg-accent ${
                            personId(p) === formMaster ? 'bg-accent font-medium' : ''
                          }`}
                          onClick={() => setFormMaster(personId(p))}
                        >
                          <span className="truncate block">{personLabel(p)}</span>
                        </div>
                      ))
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
          <div className="flex justify-end gap-2 mt-2">
            <Button variant="outline" onClick={() => setCreateOpen(false)}>取消</Button>
            <Button onClick={handleCreate} disabled={saving}>
              {saving ? '保存中…' : '确认绑定'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
