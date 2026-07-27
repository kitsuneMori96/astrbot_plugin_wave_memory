import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  AlertTriangleIcon,
  ArrowLeftIcon,
  HeartIcon,
  PencilIcon,
  PlusIcon,
  XIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  CartesianGrid,
  Line,
  LineChart,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import {
  getBlackboxPersonDetail,
  getBlackboxPersonDimensionTrend,
  getBlackboxPersonEvents,
  getBlackboxPersonExpression,
  updatePersonAliases,
  updatePersonImpression,
  updatePersonNotes,
  updatePersonTags,
  type BlackboxPersonDetail,
  type DimensionTrendPoint,
  type RelationshipEventItem,
} from '@/api/blackbox'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'

// ── helpers ──────────────────────────────────────────

function formatTime(seconds: unknown): string {
  const s = Number(seconds)
  if (!Number.isFinite(s) || s <= 0) return '-'
  return new Date(s * 1000).toLocaleString('zh-CN')
}

function attitudeBadge(level: string | undefined) {
  switch (level) {
    case 'intimate': return <Badge className="bg-pink-500 hover:bg-pink-600">亲密</Badge>
    case 'friendly': return <Badge className="bg-green-500 hover:bg-green-600">友好</Badge>
    case 'neutral':  return <Badge variant="outline">中立</Badge>
    case 'cold':     return <Badge className="bg-blue-400 hover:bg-blue-500">冷淡</Badge>
    case 'hostile':  return <Badge variant="destructive">敌对</Badge>
    default:         return <Badge variant="outline">{level || '-'}</Badge>
  }
}

const DIM_LABELS: Record<string, string> = {
  familiarity: '熟悉度',
  trust: '信任',
  fun: '趣味',
  hostility: '敌意',
  depth: '深度',
}

const DIM_COLORS: Record<string, string> = {
  familiarity: '#3b82f6',
  trust: '#22c55e',
  fun: '#f59e0b',
  hostility: '#ef4444',
  depth: '#a855f7',
}

// ── page ─────────────────────────────────────────────

export function BlackboxPersonDetailPage() {
  const { id: personId } = useParams<{ id: string }>()
  const decodedId = useMemo(() => decodeURIComponent(personId ?? ''), [personId])

  const [detail, setDetail] = useState<BlackboxPersonDetail | null>(null)
  const [events, setEvents] = useState<RelationshipEventItem[]>([])
  const [eventsTotal, setEventsTotal] = useState(0)
  const [trendPoints, setTrendPoints] = useState<DimensionTrendPoint[]>([])
  const [expression, setExpression] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // editing states
  const [editingImpression, setEditingImpression] = useState(false)
  const [impressionDraft, setImpressionDraft] = useState('')
  const [editingTags, setEditingTags] = useState(false)
  const [tagsDraft, setTagsDraft] = useState('')
  const [notesDraft, setNotesDraft] = useState('')
  const [editingNotes, setEditingNotes] = useState(false)
  const [newAlias, setNewAlias] = useState('')
  const [aliases, setAliases] = useState<string[]>([])
  const [trendDays, setTrendDays] = useState(30)

  const load = useCallback(async () => {
    if (!decodedId) return
    setLoading(true)
    setError('')
    try {
      const [d, ev, tr] = await Promise.all([
        getBlackboxPersonDetail(decodedId),
        getBlackboxPersonEvents(decodedId, { limit: 20, offset: 0 }),
        getBlackboxPersonDimensionTrend(decodedId, trendDays),
      ])
      setDetail(d)
      setEvents(ev.items ?? [])
      setEventsTotal(ev.total ?? 0)
      setTrendPoints(tr.points ?? [])
      setImpressionDraft(d.impression ?? '')
      setTagsDraft(
        Object.entries(d.tags ?? {})
          .map(([k, v]) => `${k}:${v}`)
          .join(', '),
      )
      setAliases(d.aliases ?? [])
      // load expression separately
      getBlackboxPersonExpression(decodedId).then(ex => setExpression(ex.expression)).catch(() => {})
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取人物详情失败')
    } finally {
      setLoading(false)
    }
  }, [decodedId, trendDays])

  useEffect(() => { void load() }, [load])

  const handleSaveImpression = async () => {
    if (!detail) return
    const profile = detail.profiles?.[0] as Record<string, string> | undefined
    if (!profile?.group_id) { toast.error('缺少 group_id，无法保存'); return }
    try {
      const res = await updatePersonImpression(decodedId, impressionDraft, profile.group_id, profile.bot_id || 'yushu')
      if (res.ok) {
        toast.success('印象已更新')
        setEditingImpression(false)
        setDetail(prev => prev ? { ...prev, impression: impressionDraft, meta_updated: new Date().toISOString().slice(0, 16) } : prev)
      }
    } catch { toast.error('保存印象失败') }
  }

  const handleSaveTags = async () => {
    if (!detail) return
    const profile = detail.profiles?.[0] as Record<string, string> | undefined
    if (!profile?.group_id) { toast.error('缺少 group_id，无法保存'); return }
    const tags: Record<string, number> = {}
    for (const part of tagsDraft.split(',')) {
      const trimmed = part.trim()
      if (!trimmed) continue
      const [k, ...v] = trimmed.split(':')
      const val = parseInt(v.join(''), 10)
      if (k && !isNaN(val)) tags[k.trim()] = val
    }
    try {
      const res = await updatePersonTags(decodedId, tags, profile.group_id, profile.bot_id || 'yushu')
      if (res.ok) {
        toast.success('标签已更新')
        setEditingTags(false)
        setDetail(prev => prev ? { ...prev, tags, meta_updated: new Date().toISOString().slice(0, 16) } : prev)
      }
    } catch { toast.error('保存标签失败') }
  }

  const handleSaveNotes = async () => {
    if (!detail) return
    const profile = detail.profiles?.[0] as Record<string, string> | undefined
    if (!profile?.group_id) { toast.error('缺少 group_id，无法保存'); return }
    try {
      const res = await updatePersonNotes(decodedId, notesDraft, profile.group_id, profile.bot_id || 'yushu')
      if (res.ok) {
        toast.success('备注已更新')
        setEditingNotes(false)
      }
    } catch { toast.error('保存备注失败') }
  }

  const handleAddAlias = async () => {
    const a = newAlias.trim()
    if (!a) return
    try {
      const res = await updatePersonAliases(decodedId, 'add', a)
      if (res.ok) {
        setAliases(res.aliases ?? [...aliases, a])
        setNewAlias('')
        toast.success('别名已添加')
      }
    } catch { toast.error('添加别名失败') }
  }

  const handleRemoveAlias = async (a: string) => {
    try {
      const res = await updatePersonAliases(decodedId, 'remove', a)
      if (res.ok) {
        setAliases(res.aliases ?? aliases.filter(x => x !== a))
        toast.success('别名已删除')
      }
    } catch { toast.error('删除别名失败') }
  }

  const dimensions = detail?.dimensions ?? {}
  const radarData = [
    { dimension: '熟悉', value: Math.min(100, dimensions.familiarity ?? 0), fullMark: 100 },
    { dimension: '信任', value: Math.min(100, Math.max(0, dimensions.trust ?? 0)), fullMark: 100 },
    { dimension: '趣味', value: Math.min(80, dimensions.fun ?? 0), fullMark: 80 },
    { dimension: '深度', value: Math.min(80, dimensions.depth ?? 0), fullMark: 80 },
    { dimension: '敌意', value: Math.min(100, dimensions.hostility ?? 0), fullMark: 100 },
  ]

  // ── render ──

  if (loading) {
    return (
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-2">
          <Link to="/blackbox/people"><Button variant="ghost" size="icon"><ArrowLeftIcon className="h-4 w-4" /></Button></Link>
          <Skeleton className="h-6 w-48" />
        </div>
        <Card><CardContent className="p-6"><Skeleton className="h-64 w-full" /></CardContent></Card>
      </div>
    )
  }

  if (error || !detail) {
    return (
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-2">
          <Link to="/blackbox/people"><Button variant="ghost" size="icon"><ArrowLeftIcon className="h-4 w-4" /></Button></Link>
          <span className="text-lg font-semibold">人物详情</span>
        </div>
        <Alert variant="destructive">
          <AlertTriangleIcon />
          <AlertTitle>读取失败</AlertTitle>
          <AlertDescription>{error || '人物不存在'}</AlertDescription>
        </Alert>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      {/* ── header ── */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Link to="/blackbox/people"><Button variant="ghost" size="icon"><ArrowLeftIcon className="h-4 w-4" /></Button></Link>
          <div>
            <h1 className="text-xl font-bold">{detail.display_name || detail.nickname || decodedId}</h1>
            <p className="text-xs text-muted-foreground">
              QQ: {decodedId} &middot; {detail.nickname ? `昵称: ${detail.nickname}` : ''}
              &middot; 消息: {detail.message_count ?? 0} &middot; 互动: {detail.interaction_count ?? 0}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1">
            <HeartIcon className={`h-5 w-5 ${detail.affection !== undefined && detail.affection >= 0 ? 'text-red-400' : 'text-gray-400'}`} />
            <span className={`text-2xl font-bold ${(detail.affection ?? 0) >= 60 ? 'text-pink-500' : (detail.affection ?? 0) >= 0 ? '' : 'text-red-500'}`}>
              {detail.affection ?? 0}
            </span>
            <span className="text-sm text-muted-foreground">/ 100</span>
          </div>
          {attitudeBadge(detail.attitude_level)}
          {detail.meta_updated && (
            <span className="text-xs text-muted-foreground">Meta 更新: {detail.meta_updated}</span>
          )}
        </div>
      </div>

      {/* ── row 1: radar + impression / tags ── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="text-base">关系维度雷达</CardTitle></CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={260}>
              <RadarChart data={radarData}>
                <PolarGrid />
                <PolarAngleAxis dataKey="dimension" />
                <PolarRadiusAxis angle={90} domain={[0, 100]} />
                <Radar dataKey="value" stroke="#8884d8" fill="#8884d8" fillOpacity={0.3} />
              </RadarChart>
            </ResponsiveContainer>
            <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
              {Object.entries(dimensions).map(([k, v]) => (
                <span key={k}>
                  <span style={{ color: DIM_COLORS[k] ?? '#888' }}>●</span>{' '}
                  {DIM_LABELS[k] || k}: {v ?? 0}
                </span>
              ))}
            </div>
          </CardContent>
        </Card>

        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-base">印象</CardTitle>
              <Button variant="ghost" size="icon" onClick={() => setEditingImpression(!editingImpression)}>
                <PencilIcon className="h-4 w-4" />
              </Button>
            </CardHeader>
            <CardContent>
              {editingImpression ? (
                <div className="flex flex-col gap-2">
                  <Textarea value={impressionDraft} onChange={e => setImpressionDraft(e.target.value)} rows={3} />
                  <div className="flex gap-2">
                    <Button size="sm" onClick={handleSaveImpression}>保存</Button>
                    <Button size="sm" variant="outline" onClick={() => { setEditingImpression(false); setImpressionDraft(detail.impression ?? '') }}>取消</Button>
                  </div>
                </div>
              ) : (
                <p className="text-sm">{detail.impression || '（无印象记录）'}</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-base">标签</CardTitle>
              <Button variant="ghost" size="icon" onClick={() => setEditingTags(!editingTags)}>
                <PencilIcon className="h-4 w-4" />
              </Button>
            </CardHeader>
            <CardContent>
              {editingTags ? (
                <div className="flex flex-col gap-2">
                  <Input value={tagsDraft} onChange={e => setTagsDraft(e.target.value)} placeholder="格式: 标签名:分数, 标签名:分数" />
                  <p className="text-xs text-muted-foreground">示例: 幽默:8, 爱提问:6, 深夜党:5</p>
                  <div className="flex gap-2">
                    <Button size="sm" onClick={handleSaveTags}>保存</Button>
                    <Button size="sm" variant="outline" onClick={() => { setEditingTags(false); setTagsDraft(Object.entries(detail.tags ?? {}).map(([k, v]) => `${k}:${v}`).join(', ')) }}>取消</Button>
                  </div>
                </div>
              ) : (
                <div className="flex flex-wrap gap-1">
                  {detail.tags && Object.entries(detail.tags).length > 0
                    ? Object.entries(detail.tags).map(([k, v]) => (
                        <Badge key={k} variant="secondary">{k}: {v}</Badge>
                      ))
                    : <span className="text-sm text-muted-foreground">（无标签）</span>}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* ── row 2: trend chart + events ── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-base">好感趋势</CardTitle>
            <Select value={String(trendDays)} onValueChange={v => setTrendDays(Number(v))}>
              <SelectTrigger className="w-24">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="7">7 天</SelectItem>
                <SelectItem value="30">30 天</SelectItem>
                <SelectItem value="90">90 天</SelectItem>
              </SelectContent>
            </Select>
          </CardHeader>
          <CardContent>
            {trendPoints.length === 0 ? (
              <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
                暂无趋势数据（需先有关系事件）
              </div>
            ) : (
              <>
                <ResponsiveContainer width="100%" height={200}>
                  <LineChart data={trendPoints}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                    <YAxis domain={[-100, 100]} tick={{ fontSize: 10 }} />
                    <Tooltip />
                    <Line type="monotone" dataKey="affection" stroke="#ef4444" strokeWidth={2} dot={false} name="好感度" />
                    <Line type="monotone" dataKey="familiarity" stroke="#3b82f6" strokeWidth={1} dot={false} name="熟悉" />
                    <Line type="monotone" dataKey="trust" stroke="#22c55e" strokeWidth={1} dot={false} name="信任" />
                    <Line type="monotone" dataKey="fun" stroke="#f59e0b" strokeWidth={1} dot={false} name="趣味" />
                  </LineChart>
                </ResponsiveContainer>
                <div className="mt-1 flex flex-wrap gap-3 text-xs text-muted-foreground">
                  <span><span className="text-red-500">●</span> 好感度</span>
                  <span><span className="text-blue-500">●</span> 熟悉</span>
                  <span><span className="text-green-500">●</span> 信任</span>
                  <span><span className="text-amber-500">●</span> 趣味</span>
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">关系事件</CardTitle>
            <CardDescription>最近 {events.length} 条（共 {eventsTotal} 条）</CardDescription>
          </CardHeader>
          <CardContent className="max-h-[300px] overflow-y-auto">
            {events.length === 0 ? (
              <div className="flex h-24 items-center justify-center text-sm text-muted-foreground">暂无关系事件</div>
            ) : (
              <div className="flex flex-col gap-2">
                {events.map((ev, i) => (
                  <div key={ev.id ?? i} className="rounded border p-2 text-xs">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline" className="text-[10px]">{ev.event_type}</Badge>
                      <span style={{ color: DIM_COLORS[ev.dimension ?? ''] ?? '#888' }}>
                        {DIM_LABELS[ev.dimension ?? ''] || ev.dimension}{' '}
                        <span className={Number(ev.delta) >= 0 ? 'text-green-500' : 'text-red-500'}>
                          {Number(ev.delta) >= 0 ? '+' : ''}{ev.delta}
                        </span>
                      </span>
                      <span className="text-muted-foreground">{formatTime(ev.created_at)}</span>
                    </div>
                    <p className="mt-1 text-muted-foreground">{ev.reason}</p>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* ── row 3: expression + cross-group profiles ── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="text-base">表达模式</CardTitle></CardHeader>
          <CardContent>
            {expression ? (
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div><span className="text-muted-foreground">平均消息长度:</span> <span className="font-mono">{String(expression.avg_msg_length ?? '-')}</span></div>
                <div><span className="text-muted-foreground">表情使用率:</span> <span className="font-mono">{String(expression.emoji_rate ?? '-')}</span></div>
                <div><span className="text-muted-foreground">提问率:</span> <span className="font-mono">{String(expression.question_rate ?? '-')}</span></div>
                <div><span className="text-muted-foreground">感叹率:</span> <span className="font-mono">{String(expression.exclamation_rate ?? '-')}</span></div>
                <div><span className="text-muted-foreground">词汇丰富度:</span> <span className="font-mono">{String(expression.vocab_richness ?? '-')}</span></div>
                <div><span className="text-muted-foreground">情感偏倚:</span> <span className="font-mono">{String(expression.sentiment_bias ?? '-')}</span></div>
                <div><span className="text-muted-foreground">活跃时段:</span> <span className="font-mono">{Array.isArray(expression.active_hours) ? (expression.active_hours as number[]).join(', ') : '-'}</span></div>
                <div><span className="text-muted-foreground">高频词:</span>
                  <span className="ml-1 font-mono text-xs">
                    {Array.isArray(expression.top_words) ? (expression.top_words as string[]).slice(0, 5).join(', ') : '-'}
                  </span>
                </div>
              </div>
            ) : (
              <div className="flex h-24 items-center justify-center text-sm text-muted-foreground">暂无表达模式数据</div>
            )}
          </CardContent>
        </Card>

        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader><CardTitle className="text-base">跨群画像</CardTitle></CardHeader>
            <CardContent>
              {detail.profiles && detail.profiles.length > 0 ? (
                <div className="flex flex-col gap-2">
                  {detail.profiles.map((p, i) => {
                    const prof = p as Record<string, unknown>
                    const meta = (typeof prof.metadata === 'object' ? prof.metadata : {}) as Record<string, unknown>
                    const dims = (meta.dimensions ?? {}) as Record<string, unknown>
                    return (
                      <div key={i} className="rounded border p-2 text-xs">
                        <div className="flex flex-wrap gap-2">
                          <Badge variant="outline" className="text-[10px]">{String(prof.group_id ?? '-')}</Badge>
                          <span>Bot: {String(prof.bot_id ?? '-')}</span>
                          <span>好感: <span className={Number(prof.affection) >= 60 ? 'text-pink-500 font-bold' : ''}>{String(prof.affection ?? '-')}</span></span>
                          <span>互动: {String(prof.interaction_count ?? 0)}</span>
                        </div>
                        {Object.keys(dims).length > 0 && (
                          <p className="mt-1 text-muted-foreground">
                            {Object.entries(dims).map(([dk, dv]) => `${DIM_LABELS[dk] || dk}: ${dv}`).join(' | ')}
                          </p>
                        )}
                      </div>
                    )
                  })}
                </div>
              ) : (
                <div className="flex h-16 items-center justify-center text-sm text-muted-foreground">无跨群数据</div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-base">人物注册表信息</CardTitle></CardHeader>
            <CardContent className="text-sm">
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div><span className="text-muted-foreground">消息计数:</span> {detail.message_count ?? 0}</div>
                <div><span className="text-muted-foreground">所在群数:</span> {(detail.groups ?? []).length}</div>
                <div><span className="text-muted-foreground">首次出现:</span> {formatTime(detail.first_seen)}</div>
                <div><span className="text-muted-foreground">最后出现:</span> {formatTime(detail.last_seen)}</div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* ── row 4: notes + aliases ── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-base">备注</CardTitle>
            <Button variant="ghost" size="icon" onClick={() => { setEditingNotes(!editingNotes); if (!editingNotes) setNotesDraft('') }}>
              <PencilIcon className="h-4 w-4" />
            </Button>
          </CardHeader>
          <CardContent>
            {editingNotes ? (
              <div className="flex flex-col gap-2">
                <Textarea value={notesDraft} onChange={e => setNotesDraft(e.target.value)} rows={3} placeholder="输入备注..." />
                <div className="flex gap-2">
                  <Button size="sm" onClick={handleSaveNotes}>保存</Button>
                  <Button size="sm" variant="outline" onClick={() => setEditingNotes(false)}>取消</Button>
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                {detail.profiles?.[0] && (detail.profiles[0] as Record<string, unknown>).notes
                  ? String((detail.profiles[0] as Record<string, unknown>).notes)
                  : '（无备注）'}
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">别名管理</CardTitle></CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-1">
              {aliases.length === 0 ? (
                <span className="text-sm text-muted-foreground">（无别名）</span>
              ) : (
                aliases.map(a => (
                  <Badge key={a} variant="secondary" className="flex items-center gap-1">
                    {a}
                    <button onClick={() => handleRemoveAlias(a)} className="text-muted-foreground hover:text-foreground">
                      <XIcon className="h-3 w-3" />
                    </button>
                  </Badge>
                ))
              )}
            </div>
            <div className="mt-2 flex gap-2">
              <Input
                placeholder="添加别名..."
                value={newAlias}
                onChange={e => setNewAlias(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') { void handleAddAlias() } }}
                className="h-8"
              />
              <Button size="sm" variant="outline" onClick={handleAddAlias}><PlusIcon className="h-3 w-3" /></Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}