import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertCircleIcon, CheckCircle2Icon, FileEditIcon, Loader2Icon, RefreshCwIcon, SaveIcon, SearchIcon, TagIcon, Trash2Icon, Undo2Icon } from 'lucide-react'
import { toast } from 'sonner'

import {
  addMemoryTag,
  batchDeleteMemories,
  deleteMemory,
  deleteMemoryTag,
  getMemoryDetail,
  getSimilarMemories,
  listLegacyMemories,
  listMemories,
  listSenders,
  memoryBatchStreamUrl,
  reEmbedMemory,
  runPostStream,
  updateMemory,
  type LegacyMemoriesResponse,
  type MemoryDetail,
  type MemoryItem,
  type MemoryRefInput,
  type MemoryScope,
  type SenderItem,
  type SimilarMemoryItem,
  type StreamProgress,
} from '@/api/memories'
import { getScopeOptions, scopeOptionsFor } from '@/api/options'
import { type TagExecutionOptions, type TagWritePolicy } from '@/api/tags'
import { TagExtractionConfigPanel } from '@/components/tag/TagExtractionConfigPanel'
import { PaginationControls, QueryState, ScopeSelect, type ObjectRefState } from '@/components/shared'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { useCanonicalScopeDefault, usePaginationSearchParams } from '@/hooks/use-pagination-search-params'

const SOURCES = ['live', 'chat', 'noise', 'core', 'identity_quarantine', 'evolution', 'bzz_experience', 'experience', 'lore', 'book_lore', 'oni_lore', 'bot_reply', 'fewshot']

const DEEP_LINK_LABELS: Record<Exclude<ObjectRefState, 'ready'>, string> = {
  'not-found': '对象不存在、引用无效或 revision 已变化；不会使用裸 ID 回退定位。',
  'scope-mismatch': '对象引用与当前 Bot / 会话 Scope 不匹配。',
  'version-stale': '对象版本已更新，请从最新列表重新打开。',
}

interface ConfirmAction {
  title: string
  description: string
  label: string
  destructive?: boolean
  run: () => Promise<void> | void
}

function deepLinkFailureState(reason: unknown): ObjectRefState {
  const payload = reason instanceof Error && 'payload' in reason ? (reason as Error & { payload?: unknown }).payload : undefined
  const code = typeof payload === 'object' && payload !== null && 'error' in payload ? (payload as { error?: { code?: unknown } }).error?.code : undefined
  if (code === 'scope_mismatch') return 'scope-mismatch'
  if (code === 'version_stale') return 'version-stale'
  return 'not-found'
}

function formatTime(seconds: unknown): string {
  const value = Number(seconds)
  return Number.isFinite(value) && value > 0 ? new Date(value * 1000).toLocaleString('zh-CN') : '未记录'
}

function tagBadgeClass(type = 'keyword'): string {
  const colors: Record<string, string> = {
    person: 'border-pink-500/20 bg-pink-500/10 text-pink-500',
    topic: 'border-blue-500/20 bg-blue-500/10 text-blue-500',
    entity: 'border-red-500/20 bg-red-500/10 text-red-500',
    event: 'border-emerald-500/20 bg-emerald-500/10 text-emerald-500',
    emotion: 'border-amber-500/20 bg-amber-500/10 text-amber-500',
  }
  return `border font-normal text-[10px] ${colors[type] ?? 'border-border/50 bg-muted text-muted-foreground'}`
}

export function MemoriesPage() {
  const pagination = usePaginationSearchParams()
  const [params, setParams] = useSearchParams()
  const botId = params.get('bot_id') ?? ''
  const sessionId = params.get('session_id') ?? ''
  const visibility = params.get('visibility') ?? 'group'
  const objectRef = params.get('ref') ?? ''
  const objectId = params.get('object_id') ?? ''
  const search = params.get('search') ?? ''
  const source = params.get('source') ?? ''
  const sender = params.get('sender') ?? ''
  const hasTags = params.get('has_tags') ?? ''
  const hasVector = params.get('has_vector') ?? ''
  useCanonicalScopeDefault({ botId, sessionId, setFilters: pagination.setFilters })
  const scope = useMemo<MemoryScope | null>(() => botId && sessionId && visibility === 'group' ? { bot_id: botId, session_id: sessionId, visibility: 'group' } : null, [botId, sessionId, visibility])

  const [searchDraft, setSearchDraft] = useState(search)
  const [payload, setPayload] = useState<Awaited<ReturnType<typeof listMemories>> | null>(null)
  const [status, setStatus] = useState<'loading' | 'success' | 'empty' | 'unknown' | 'error'>('empty')
  const [error, setError] = useState<unknown>()
  const [senders, setSenders] = useState<SenderItem[]>([])
  const [selectedRefs, setSelectedRefs] = useState<string[]>([])
  const [legacyPayload, setLegacyPayload] = useState<LegacyMemoriesResponse | null>(null)
  const [legacyOffset, setLegacyOffset] = useState(0)
  const [legacyLoading, setLegacyLoading] = useState(true)

  const [detailOpen, setDetailOpen] = useState(false)
  const [detail, setDetail] = useState<MemoryDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [deepLinkStatus, setDeepLinkStatus] = useState<'loading' | ObjectRefState | null>(null)
  const [content, setContent] = useState('')
  const [importance, setImportance] = useState(0)
  const [saving, setSaving] = useState(false)
  const [similarLoading, setSimilarLoading] = useState(false)
  const [similarItems, setSimilarItems] = useState<SimilarMemoryItem[]>([])
  const [newTagName, setNewTagName] = useState('')
  const resolvedRef = useRef('')

  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null)
  const [confirmRunning, setConfirmRunning] = useState(false)
  const [configOpen, setConfigOpen] = useState(false)
  const [streamOpen, setStreamOpen] = useState(false)
  const [streamTitle, setStreamTitle] = useState('')
  const [streamProgress, setStreamProgress] = useState<StreamProgress | null>(null)
  const [streamLog, setStreamLog] = useState<string[]>([])
  const [streamRunning, setStreamRunning] = useState(false)
  const [tagBatchSize, setTagBatchSize] = useState(20)
  const [tagWritePolicy, setTagWritePolicy] = useState<TagWritePolicy>('missing_only')

  useEffect(() => setSearchDraft(search), [search])

  const loadBots = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['bot']), [])
  const loadSessions = useCallback(async () => {
    const options = scopeOptionsFor(await getScopeOptions(), ['session'])
    return botId ? options.filter((option) => option.description?.startsWith(`${botId} ·`)) : options
  }, [botId])

  const load = useCallback(async () => {
    if (!scope) {
      setPayload(null)
      setSelectedRefs([])
      setStatus('empty')
      return
    }
    setStatus('loading')
    setError(undefined)
    try {
      const next = await listMemories({ ...scope, limit: pagination.limit, offset: pagination.offset, search: search || undefined, source: source || undefined, sender: sender || undefined, has_tags: hasTags || undefined, has_vector: hasVector || undefined })
      setPayload(next)
      setSelectedRefs([])
      setStatus(next.items.length ? 'success' : next.page.total_status === 'unavailable' ? 'unknown' : 'empty')
    } catch (reason) {
      setPayload(null)
      setError(reason)
      setStatus('error')
    }
  }, [hasTags, hasVector, pagination.limit, pagination.offset, scope, search, sender, source])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    let cancelled = false
    setLegacyLoading(true)
    listLegacyMemories({ limit: 25, offset: legacyOffset, search: search || undefined, source: source || undefined })
      .then((result) => { if (!cancelled) setLegacyPayload(result) })
      .catch(() => { if (!cancelled) setLegacyPayload(null) })
      .finally(() => { if (!cancelled) setLegacyLoading(false) })
    return () => { cancelled = true }
  }, [legacyOffset, search, source])
  useEffect(() => {
    if (!scope) { setSenders([]); return }
    let cancelled = false
    listSenders(scope).then((result) => { if (!cancelled) setSenders(result.senders) }).catch(() => { if (!cancelled) setSenders([]) })
    return () => { cancelled = true }
  }, [scope])

  const hydrateDetail = useCallback(async (detailUrl: string) => {
    setDetailOpen(true)
    setDetail(null)
    setDetailLoading(true)
    setSimilarLoading(true)
    setSimilarItems([])
    try {
      const result = await getMemoryDetail(detailUrl)
      resolvedRef.current = result.item.ref
      setDetail(result.item)
      setContent(result.item.content)
      setImportance(Number(result.item.importance ?? 0))
      setDeepLinkStatus('ready')
      try {
        const similar = await getSimilarMemories(result.item)
        setSimilarItems(similar.items ?? [])
      } catch {
        setSimilarItems([])
      }
      return result.item
    } finally {
      setDetailLoading(false)
      setSimilarLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!objectRef) { setDeepLinkStatus(null); return }
    if (resolvedRef.current === objectRef) return
    if (!scope) { setDetail(null); setDeepLinkStatus('scope-mismatch'); return }
    if (objectId && !/^\d+$/.test(objectId)) { setDetail(null); setDeepLinkStatus('not-found'); return }
    const query = new URLSearchParams({ ref: objectRef, ...scope })
    const endpoint = objectId ? `/api/memories/${objectId}?${query.toString()}` : `/api/memories/resolve?${query.toString()}`
    let cancelled = false
    setDeepLinkStatus('loading')
    hydrateDetail(endpoint).catch((reason) => {
      if (cancelled) return
      setDetail(null)
      setDetailOpen(false)
      setDeepLinkStatus(deepLinkFailureState(reason))
    })
    return () => { cancelled = true }
  }, [hydrateDetail, objectId, objectRef, scope])

  async function open(item: MemoryItem) {
    try {
      const result = await hydrateDetail(item.detail_url)
      setParams((current) => {
        const next = new URLSearchParams(current)
        next.set('ref', result.ref)
        next.set('object_id', String(result.id))
        next.set('bot_id', result.bot_id)
        next.set('session_id', result.session_id)
        next.set('visibility', result.visibility)
        return next
      })
    } catch (reason) {
      setDeepLinkStatus(deepLinkFailureState(reason))
      toast.error(reason instanceof Error ? reason.message : '记忆详情加载失败')
    }
  }

  function closeDetail() {
    setDetailOpen(false)
    setDetail(null)
    setSimilarItems([])
    resolvedRef.current = ''
    setParams((current) => {
      const next = new URLSearchParams(current)
      next.delete('ref')
      next.delete('object_id')
      return next
    })
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault()
    pagination.setFilters({ search: searchDraft || null })
  }

  function resetFilters() {
    setSearchDraft('')
    pagination.setFilters({ search: null, source: null, sender: null, has_tags: null, has_vector: null })
  }

  function selectedItems(): MemoryItem[] {
    const selected = new Set(selectedRefs)
    return payload?.items.filter((item) => selected.has(item.ref)) ?? []
  }

  function toggleAll(checked: boolean) {
    setSelectedRefs(checked ? (payload?.items.map((item) => item.ref) ?? []) : [])
  }

  function toggleRow(ref: string, checked: boolean) {
    setSelectedRefs((current) => checked ? [...current, ref] : current.filter((value) => value !== ref))
  }

  async function saveDetail() {
    if (!detail) return
    setSaving(true)
    try {
      const result = await updateMemory(detail.mutation_url, content, importance)
      if (!result.ok || result.operation.status !== 'succeeded' || !result.item) throw new Error('服务端未确认更新成功')
      const next = result.item
      resolvedRef.current = next.ref
      setDetail(next)
      setContent(next.content)
      setParams((current) => {
        const paramsNext = new URLSearchParams(current)
        paramsNext.set('ref', next.ref)
        paramsNext.set('object_id', String(next.id))
        return paramsNext
      })
      toast.success('记忆已按当前 Scope 更新')
      await load()
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '更新失败')
    } finally {
      setSaving(false)
    }
  }

  async function removeDetail() {
    if (!detail) return
    setSaving(true)
    try {
      const result = await deleteMemory(detail.mutation_url)
      if (!result.ok || result.operation.status !== 'succeeded') throw new Error('服务端未确认删除成功')
      toast.success('记忆已删除')
      closeDetail()
      await load()
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '删除失败')
    } finally {
      setSaving(false)
    }
  }

  async function reEmbedDetail() {
    if (!detail) return
    setSaving(true)
    try {
      const result = await reEmbedMemory(detail)
      if (!result.ok) throw new Error(result.error ?? '服务端未确认向量化成功')
      setDetail({ ...detail, has_vector: true })
      toast.success('重新向量化已完成')
      await load()
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '重新向量化失败')
    } finally {
      setSaving(false)
    }
  }

  async function addTag(event: FormEvent) {
    event.preventDefault()
    if (!detail) return
    const name = newTagName.trim()
    if (!name) return
    if ((detail.tags ?? []).some((tag) => tag.name.toLowerCase() === name.toLowerCase())) { toast.warning('该标签已关联'); return }
    try {
      await addMemoryTag(detail, name)
      setDetail({ ...detail, tags: [...(detail.tags ?? []), { name, type: 'custom' }] })
      setNewTagName('')
      toast.success(`已关联标签“${name}”`)
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '标签关联失败')
    }
  }

  async function removeTag(name: string) {
    if (!detail) return
    try {
      await deleteMemoryTag(detail, name)
      setDetail({ ...detail, tags: (detail.tags ?? []).filter((tag) => tag.name !== name) })
      toast.success(`已移除标签“${name}”`)
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '标签移除失败')
    }
  }

  async function batchDelete() {
    if (!scope) return
    const refs: MemoryRefInput[] = selectedItems().map((item) => ({ id: item.id, ref: item.ref }))
    const result = await batchDeleteMemories(scope, refs)
    if (!result.ok) throw new Error('服务端未确认批量删除成功')
    toast.success(`已删除 ${result.deleted} 条记忆`)
    await load()
  }

  function startStream(action: 're-embed' | 'extract-tags') {
    if (!scope || streamRunning) return
    const refs: MemoryRefInput[] = selectedItems().map((item) => ({ id: item.id, ref: item.ref }))
    if (!refs.length) return
    setStreamTitle(action === 're-embed' ? '批量重新向量化' : '批量提取 Tag')
    setStreamProgress(null)
    setStreamLog([`[INIT] 已提交 ${refs.length} 个当前 Scope 的 ObjectRef。`])
    setStreamOpen(true)
    setStreamRunning(true)
    void runPostStream(memoryBatchStreamUrl(action, scope), refs, (state) => {
      setStreamProgress(state)
      setStreamLog((current) => [...current, `[PROGRESS] ${Math.round((state.progress ?? 0) * 100)}% · ${state.processed ?? 0}/${state.total} · 失败 ${state.errors ?? 0}`].slice(-50))
      if (state.done) {
        setStreamLog((current) => [...current, '[SUCCESS] 批量任务完成。'])
        setSelectedRefs([])
        void load()
      }
    }, action === 'extract-tags' ? { payload: { extract_tags: true, tag_batch_size: tagBatchSize, tag_write_policy: tagWritePolicy } } : undefined).catch((reason) => {
      const message = reason instanceof Error ? reason.message : '批量任务失败'
      setStreamLog((current) => [...current, `[ERROR] ${message}`])
      toast.error(message)
    }).finally(() => setStreamRunning(false))
  }

  async function executeConfirm() {
    if (!confirmAction) return
    setConfirmRunning(true)
    try {
      await confirmAction.run()
      setConfirmAction(null)
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '操作失败')
    } finally {
      setConfirmRunning(false)
    }
  }

  const items = payload?.items ?? []
  const allSelected = items.length > 0 && selectedRefs.length === items.length
  const totalDescription = payload?.page.total_status === 'exact' && payload.page.total !== null
    ? `当前 Scope 共 ${payload.page.total.toLocaleString()} 条`
    : items.length ? `当前页 ${items.length} 条；服务端未提供总数` : '等待选择 Scope'

  return (
    <div data-slot="memories-page" className="flex flex-col gap-5">
      <Card>
        <CardHeader className="py-4">
          <CardTitle>记忆管理器</CardTitle>
          <CardDescription>保留紧凑检索与管理能力；所有读取和变更均绑定真实 Bot、canonical session 与服务端签发 ObjectRef，不会从裸 ID 补默认 Scope。</CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <form className="flex flex-col gap-3" onSubmit={submitSearch}>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-7">
              <ScopeSelect value={botId || undefined} loadOptions={loadBots} label="Bot" onValueChange={(value) => pagination.setFilters({ bot_id: value, session_id: null })} />
              <ScopeSelect value={sessionId || undefined} loadOptions={loadSessions} label="群 / 会话" disabled={!botId} onValueChange={(value) => pagination.setFilters({ session_id: value })} />
              <Field><FieldLabel htmlFor="memory-search">关键词</FieldLabel><Input id="memory-search" placeholder="搜索记忆内容" value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} /></Field>
              <Field><FieldLabel>来源</FieldLabel><Select value={source || 'all'} onValueChange={(value) => pagination.setFilters({ source: value === 'all' ? null : value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部来源</SelectItem>{SOURCES.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select></Field>
              <Field><FieldLabel>发送者</FieldLabel><Select value={sender || 'all'} onValueChange={(value) => pagination.setFilters({ sender: value === 'all' ? null : value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部发送者</SelectItem>{senders.map((item) => <SelectItem key={item.name} value={item.name}>{item.name} ({item.count})</SelectItem>)}</SelectContent></Select></Field>
              <Field><FieldLabel>Tag</FieldLabel><Select value={hasTags || 'all'} onValueChange={(value) => pagination.setFilters({ has_tags: value === 'all' ? null : value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部</SelectItem><SelectItem value="true">有标签</SelectItem><SelectItem value="false">无标签</SelectItem></SelectContent></Select></Field>
              <Field><FieldLabel>向量</FieldLabel><Select value={hasVector || 'all'} onValueChange={(value) => pagination.setFilters({ has_vector: value === 'all' ? null : value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部</SelectItem><SelectItem value="true">有向量</SelectItem><SelectItem value="false">无向量</SelectItem></SelectContent></Select></Field>
            </div>
            <div className="flex flex-wrap gap-2"><Button type="submit" disabled={!scope || status === 'loading'}><SearchIcon data-icon="inline-start" />搜索</Button><Button type="button" variant="outline" onClick={resetFilters}><Undo2Icon data-icon="inline-start" />重置筛选</Button></div>
          </form>
        </CardContent>
      </Card>

      {deepLinkStatus && deepLinkStatus !== 'ready' ? <Alert variant={deepLinkStatus === 'loading' ? 'default' : 'destructive'}><AlertTitle>{deepLinkStatus === 'loading' ? '正在校验对象深链' : '无法打开深链记忆'}</AlertTitle><AlertDescription>{deepLinkStatus === 'loading' ? '正在验证 ObjectRef、当前 Scope 与 canonical revision。' : DEEP_LINK_LABELS[deepLinkStatus]}</AlertDescription></Alert> : null}

      {selectedRefs.length ? <div className="flex flex-wrap items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 p-3"><Badge variant="secondary">已选 {selectedRefs.length} 条</Badge><Button size="sm" variant="destructive" onClick={() => setConfirmAction({ title: '永久删除所选记忆？', description: `将删除 ${selectedRefs.length} 条当前 Scope 记忆及其标签关联，操作不可撤销。`, label: '确认批量删除', destructive: true, run: batchDelete })}><Trash2Icon data-icon="inline-start" />批量删除</Button><Button size="sm" variant="outline" disabled={streamRunning} onClick={() => setConfirmAction({ title: '批量重新向量化？', description: `将重算 ${selectedRefs.length} 条记忆的向量并触发索引修复。`, label: '确认执行', run: () => startStream('re-embed') })}><RefreshCwIcon data-icon="inline-start" />批量 re-embed</Button><Button size="sm" variant="outline" disabled={streamRunning} onClick={() => setConfirmAction({ title: '批量提取 Tag？', description: `将按当前策略处理 ${selectedRefs.length} 条记忆；append/replace 可能改变已有标签。`, label: '确认执行', run: () => startStream('extract-tags') })}><TagIcon data-icon="inline-start" />批量提取 Tag</Button><Button size="sm" variant="outline" onClick={() => setConfigOpen(true)}>提取配置</Button><Button size="sm" variant="ghost" className="ml-auto" onClick={() => setSelectedRefs([])}>取消选择</Button></div> : null}

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-4 py-4"><div><CardTitle>记忆条目</CardTitle><CardDescription>{totalDescription}</CardDescription></div></CardHeader>
        <CardContent className="pt-0">
          <QueryState status={status} error={error} onRetry={() => void load()} title={!scope ? '请选择真实 Bot 与会话' : undefined} description={!scope ? '记忆管理不接受默认 Bot、私聊或伪群作用域。' : payload?.page.reason_code ?? undefined}>
            <div className="overflow-auto rounded-lg border">
              <Table>
                <TableHeader><TableRow><TableHead className="w-10"><input aria-label="选择当前页全部记忆" type="checkbox" checked={allSelected} onChange={(event) => toggleAll(event.target.checked)} /></TableHead><TableHead className="w-16">ID</TableHead><TableHead>内容</TableHead><TableHead>发送者</TableHead><TableHead>来源</TableHead><TableHead>Tags</TableHead><TableHead className="text-center">向量</TableHead><TableHead>时间</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader>
                <TableBody>{items.map((item) => <TableRow key={item.ref} className={selectedRefs.includes(item.ref) ? 'bg-primary/5' : undefined}><TableCell><input aria-label={`选择记忆 ${item.id}`} type="checkbox" checked={selectedRefs.includes(item.ref)} onChange={(event) => toggleRow(item.ref, event.target.checked)} /></TableCell><TableCell className="font-mono text-xs text-muted-foreground">#{item.id}</TableCell><TableCell className="max-w-md cursor-pointer truncate hover:text-primary" onClick={() => void open(item)}>{item.content}</TableCell><TableCell className="max-w-32 truncate text-muted-foreground">{item.sender_name ?? item.sender_id ?? '未记录'}</TableCell><TableCell><Badge variant="secondary" className="font-mono text-[10px]">{item.source ?? '未记录'}</Badge></TableCell><TableCell><div className="flex flex-wrap gap-1">{item.tags?.length ? item.tags.slice(0, 2).map((tag, index) => <Badge key={`${tag.name}-${index}`} className={tagBadgeClass(tag.type)}>{tag.name}</Badge>) : <span className="text-xs text-muted-foreground">—</span>}</div></TableCell><TableCell className={item.has_vector ? 'text-center font-bold text-emerald-500' : 'text-center font-bold text-destructive'}>{item.has_vector ? '●' : '○'}</TableCell><TableCell className="whitespace-nowrap font-mono text-xs text-muted-foreground">{formatTime(item.timestamp)}</TableCell><TableCell className="text-right"><Button variant="ghost" size="icon-sm" title="打开详情" onClick={() => void open(item)}><FileEditIcon /></Button></TableCell></TableRow>)}</TableBody>
              </Table>
            </div>
          </QueryState>
          {payload ? <PaginationControls className="mt-4" page={payload.page} disabled={status === 'loading'} onOffsetChange={pagination.setOffset} onLimitChange={pagination.setLimit} /> : null}
        </CardContent>
      </Card>

      <Card className="border-amber-500/10 bg-amber-500/[0.02]">
        <CardHeader className="py-4">
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="border-amber-500/20 text-amber-600 bg-amber-500/5">只读审计</Badge>
            <CardTitle className="text-base">Legacy 历史记忆</CardTitle>
          </div>
          <CardDescription>这里保留尚未投影到 canonical RuntimeScope 的历史记忆。它们没有足够证据绑定当前 Bot / 群，因此不会混入上方正式列表，不参与实时召回。</CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <Alert className="mb-4 border-amber-500/15 bg-amber-500/[0.02] text-amber-700 dark:text-amber-500"><AlertCircleIcon className="size-4 text-amber-600" /><AlertTitle>未归属数据，不是当前群记忆</AlertTitle><AlertDescription className="text-xs">{legacyPayload ? `共 ${(legacyPayload.page.total ?? 0).toLocaleString('zh-CN')} 条历史记录待审计。` : legacyLoading ? '正在读取历史审计清单。' : 'Legacy 审计接口暂不可用。'} 仅当 metadata_json.runtime_scope 等证据能唯一确定作用域时，后端才会将记录投影到正式区。</AlertDescription></Alert>
          {legacyPayload?.items.length ? <>
            <div className="overflow-auto rounded-lg border bg-background">
              <Table><TableHeader><TableRow><TableHead className="w-20">Legacy ID</TableHead><TableHead>内容</TableHead><TableHead>发送者</TableHead><TableHead>来源</TableHead><TableHead>时间</TableHead></TableRow></TableHeader><TableBody>{legacyPayload.items.map((item) => <TableRow key={item.id}><TableCell className="font-mono text-xs text-muted-foreground">#{item.id}</TableCell><TableCell className="max-w-xl"><p className="line-clamp-2">{item.content}</p></TableCell><TableCell className="text-muted-foreground text-xs">{item.sender_name ?? item.sender_id ?? '未记录'}</TableCell><TableCell><Badge variant="outline" className="font-mono text-[10px]">{item.source ?? '未记录'}</Badge></TableCell><TableCell className="whitespace-nowrap font-mono text-xs text-muted-foreground">{formatTime(item.timestamp)}</TableCell></TableRow>)}</TableBody></Table>
            </div>
            <div className="mt-3 flex items-center justify-between text-sm text-muted-foreground"><span>第 {legacyOffset + 1}-{Math.min(legacyOffset + 25, (legacyPayload.page.total ?? 0))} 条</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={legacyLoading || legacyOffset === 0} onClick={() => setLegacyOffset(Math.max(0, legacyOffset - 25))}>上一页</Button><Button size="sm" variant="outline" disabled={legacyLoading || legacyOffset + 25 >= (legacyPayload.page.total ?? 0)} onClick={() => setLegacyOffset(legacyOffset + 25)}>下一页</Button></div></div>
          </> : !legacyLoading ? <p className="text-xs text-muted-foreground py-4 text-center">没有未归属的 Legacy 记忆。</p> : null}
        </CardContent>
      </Card>

      <Sheet open={detailOpen} onOpenChange={(openValue) => { if (!openValue) closeDetail() }}>
        <SheetContent className="w-full gap-0 sm:max-w-2xl">
          <SheetHeader className="border-b pr-12"><SheetTitle>记忆明细</SheetTitle><SheetDescription>{detail ? `${detail.bot_id} · ${detail.session_id} · revision ${detail.version}` : '正在按 ObjectRef 读取'}</SheetDescription></SheetHeader>
          <ScrollArea className="flex-1"><div className="flex flex-col gap-5 p-4">
            {detailLoading ? <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2Icon className="animate-spin" />正在加载详情与相似记忆</div> : detail ? <>
              <Field><FieldLabel htmlFor="memory-content">内容</FieldLabel><Textarea id="memory-content" className="min-h-36" value={content} onChange={(event) => setContent(event.target.value)} /><FieldDescription>保存会推进 revision，并回读新的 ObjectRef。</FieldDescription></Field>
              <div className="grid gap-3 rounded-lg border bg-muted/20 p-3 text-xs sm:grid-cols-2"><div><span className="text-muted-foreground">发送者：</span>{detail.sender_name ?? detail.sender_id}</div><div><span className="text-muted-foreground">来源：</span>{detail.source ?? '未记录'}</div><div><span className="text-muted-foreground">重要度：</span><Input type="number" min="0" max="1" step="0.01" className="ml-2 inline-flex h-8 w-24" value={importance} onChange={(event) => setImportance(Number(event.target.value))} /></div><div><span className="text-muted-foreground">向量：</span><Badge variant={detail.has_vector ? 'secondary' : 'destructive'}>{detail.has_vector ? '已入库' : '缺失'}</Badge></div><div><span className="text-muted-foreground">创建时间：</span>{formatTime(detail.timestamp)}</div><div><span className="text-muted-foreground">访问次数：</span>{detail.access_count ?? '未记录'}</div></div>
              <Field><FieldLabel>关联标签 (Tags)</FieldLabel><div className="flex flex-col gap-3 rounded-lg border bg-muted/10 p-3">{detail.tags === undefined ? <Alert><AlertCircleIcon /><AlertTitle>已有标签清单 unavailable</AlertTitle><AlertDescription>当前 scoped 详情契约未返回已有标签；这里不会伪装为空。仍可安全新增标签，本次页面已确认的标签会显示在下方。</AlertDescription></Alert> : null}<div className="flex min-h-7 flex-wrap gap-1.5">{(detail.tags ?? []).map((tag, index) => <Badge key={`${tag.name}-${index}`} className={`${tagBadgeClass(tag.type)} flex items-center gap-1 pr-1`}>{tag.name}<button type="button" className="rounded px-1 hover:bg-foreground/10" aria-label={`移除标签 ${tag.name}`} onClick={() => setConfirmAction({ title: `移除标签“${tag.name}”？`, description: '仅移除当前 ObjectRef 与该标签的关联，不删除标签字典项。', label: '确认移除', destructive: true, run: () => removeTag(tag.name) })}>×</button></Badge>)}</div><form className="flex gap-2" onSubmit={addTag}><Input className="h-8 max-w-xs text-xs" placeholder="输入自定义标签" value={newTagName} onChange={(event) => setNewTagName(event.target.value)} /><Button type="submit" size="sm">关联</Button></form></div></Field>
              <Card className="border-primary/20 bg-primary/5"><CardHeader className="py-3"><CardTitle className="text-sm">相似记忆</CardTitle><CardDescription>由当前 ObjectRef 对应向量查询；结果未签发新 ObjectRef，因此仅只读展示，不使用裸 ID 穿透。</CardDescription></CardHeader><CardContent className="flex flex-col gap-2 pt-0">{similarLoading ? <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2Icon className="animate-spin" />计算中</div> : similarItems.length ? similarItems.map((item) => <div key={item.id} className="rounded-lg border bg-background/50 p-2.5"><div className="flex justify-between gap-2 text-[10px] font-mono text-muted-foreground"><span>#{item.id} · {item.source || '未记录'}</span><span className="font-semibold text-primary">相似度 {item.similarity}%</span></div><p className="mt-1 line-clamp-2 text-xs leading-relaxed">{item.content}</p></div>) : <p className="py-3 text-center text-xs text-muted-foreground">未找到相似记录，或当前记忆没有可用向量。</p>}</CardContent></Card>
              <div className="flex flex-wrap gap-2 border-t pt-4"><Button disabled={saving} onClick={() => void saveDetail()}>{saving ? <Loader2Icon className="animate-spin" /> : <SaveIcon />}保存并回读 revision</Button><Button disabled={saving} variant="outline" onClick={() => setConfirmAction({ title: '重新向量化当前记忆？', description: '将重算向量并触发索引修复，不修改正文 revision。', label: '确认 re-embed', run: reEmbedDetail })}><RefreshCwIcon />re-embed</Button><Button disabled={saving} variant="destructive" className="ml-auto" onClick={() => setConfirmAction({ title: `永久删除记忆 #${detail.id}？`, description: '将按当前 ObjectRef 删除记忆，操作不可撤销。', label: '确认删除', destructive: true, run: removeDetail })}><Trash2Icon />删除当前 ObjectRef</Button></div>
            </> : <Alert variant="destructive"><AlertTitle>详情 unavailable</AlertTitle><AlertDescription>无法读取当前 ObjectRef。</AlertDescription></Alert>}
          </div></ScrollArea>
        </SheetContent>
      </Sheet>

      <Dialog open={Boolean(confirmAction)} onOpenChange={(openValue) => { if (!openValue && !confirmRunning) setConfirmAction(null) }}><DialogContent><DialogHeader><DialogTitle>{confirmAction?.title}</DialogTitle><DialogDescription>{confirmAction?.description}</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" disabled={confirmRunning} onClick={() => setConfirmAction(null)}>取消</Button><Button variant={confirmAction?.destructive ? 'destructive' : 'default'} disabled={confirmRunning} onClick={() => void executeConfirm()}>{confirmRunning ? <Loader2Icon className="animate-spin" /> : null}{confirmAction?.label}</Button></DialogFooter></DialogContent></Dialog>

      <Dialog open={configOpen} onOpenChange={setConfigOpen}><DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle>Tag 提取参数</DialogTitle><DialogDescription>配置仅用于当前勾选的 scoped ObjectRef。</DialogDescription></DialogHeader><TagExtractionConfigPanel title="LLM 运行配置" description="missing_only 最安全；append/replace 会在执行前再次确认。" disabled={streamRunning} onOptionsChange={(options: Required<TagExecutionOptions>) => { setTagBatchSize(options.tag_batch_size); setTagWritePolicy(options.tag_write_policy) }} /><DialogFooter><Button onClick={() => setConfigOpen(false)}>保存配置</Button></DialogFooter></DialogContent></Dialog>

      <Dialog open={streamOpen} onOpenChange={setStreamOpen}><DialogContent className="sm:max-w-lg"><DialogHeader><DialogTitle>{streamTitle}</DialogTitle><DialogDescription>批量任务只提交当前 Scope 的服务端签发 ObjectRef。</DialogDescription></DialogHeader><div className="flex flex-col gap-4">{streamProgress ? <div><div className="mb-1 flex justify-between text-xs"><span>{Math.round(streamProgress.progress * 100)}%</span><span>{streamProgress.processed ?? 0}/{streamProgress.total} · 失败 {streamProgress.errors ?? 0}</span></div><div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary transition-all" style={{ width: `${streamProgress.progress * 100}%` }} /></div></div> : <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2Icon className="animate-spin" />正在连接批量任务</div>}<ScrollArea className="h-40 rounded-lg border bg-muted/40 p-3 font-mono text-[10px]">{streamLog.map((line, index) => <div key={`${index}-${line}`} className={line.includes('[ERROR]') ? 'text-destructive' : line.includes('[SUCCESS]') ? 'text-emerald-500' : undefined}>{line}</div>)}</ScrollArea>{streamProgress?.done ? <Alert className="border-emerald-500/20 bg-emerald-500/10"><CheckCircle2Icon /><AlertTitle>处理完成</AlertTitle></Alert> : null}</div></DialogContent></Dialog>
    </div>
  )
}

export default MemoriesPage
