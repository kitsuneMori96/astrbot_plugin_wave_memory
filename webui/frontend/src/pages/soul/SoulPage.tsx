import { useEffect, useState } from 'react'
import { AlertCircleIcon, RefreshCwIcon } from 'lucide-react'
import { toast } from 'sonner'

import { getSystemStatus, type SystemPayload } from '@/api/system'
import {
  createConcern,
  createTimeAnchor,
  deleteConcern,
  deleteMood,
  deleteTimeAnchor,
  listConcerns,
  listMoods,
  listTimeAnchors,
  updateConcern,
  updateMood,
  updateTimeAnchor,
  type ConcernItem,
  type MoodItem,
  type TimeAnchorItem,
} from '@/api/soul'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'

function formatTime(seconds: unknown): string {
  const s = Number(seconds)
  if (!Number.isFinite(s) || s <= 0) return '-'
  return new Date(s * 1000).toLocaleString('zh-CN')
}

export function SoulPage() {
  const [sys, setSys] = useState<SystemPayload | null>(null)
  const [concerns, setConcerns] = useState<ConcernItem[]>([])
  const [anchors, setAnchors] = useState<TimeAnchorItem[]>([])
  const [moods, setMoods] = useState<MoodItem[]>([])
  
  // 筛选器
  const [botFilter, setBotFilter] = useState('bot')
  const [moodGroupFilter, setMoodGroupFilter] = useState('')

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // 新建/编辑关切焦点弹窗
  const [concernOpen, setConcernOpen] = useState(false)
  const [isConcernNew, setIsConcernNew] = useState(true)
  const [concernForm, setConcernForm] = useState<Partial<ConcernItem>>({
    topic: '',
    intensity: 0.5,
    bot_id: 'bot',
  })

  // 新建/编辑时间锚点弹窗
  const [anchorOpen, setAnchorOpen] = useState(false)
  const [isAnchorNew, setIsAnchorNew] = useState(true)
  const [anchorForm, setAnchorForm] = useState<Partial<TimeAnchorItem>>({
    event_summary: '',
    emotional_weight: 0.5,
    bot_id: 'bot',
  })

  // 编辑情绪颗粒弹窗
  const [moodOpen, setMoodOpen] = useState(false)
  const [moodForm, setMoodForm] = useState<Partial<MoodItem>>({
    type: '',
    intensity: 0.5,
    description: '',
  })

  async function loadData() {
    setLoading(true)
    setError('')
    try {
      const sysPayload = await getSystemStatus()
      setSys(sysPayload)

      const [concData, anchData, moodsData] = await Promise.all([
        listConcerns(botFilter === 'all' ? '' : botFilter),
        listTimeAnchors(botFilter === 'all' ? '' : botFilter),
        listMoods(moodGroupFilter),
      ])
      setConcerns(concData.items ?? [])
      setAnchors(anchData.items ?? [])
      setMoods(moodsData.items ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载灵魂状态数据失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadData()
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [botFilter, moodGroupFilter])

  // 生物钟算法动态能量映射
  function getCircadianEnergy(): number {
    const hr = new Date().getHours()
    if (hr >= 23 || hr < 5) return 20  // 深夜静息
    if (hr >= 5 && hr < 8) return 55   // 晨曦唤醒
    if (hr >= 8 && hr < 11) return 95  // 黄金午前
    if (hr >= 11 && hr < 13) return 40 // 午后休眠
    if (hr >= 13 && hr < 17) return 90 // 黄金午后
    if (hr >= 17 && hr < 20) return 75 // 晚间对话
    return 50                          // 深夜活跃
  }

  function getCircadianName(): string {
    const hr = new Date().getHours()
    if (hr >= 23 || hr < 5) return '【寂夜时区】万籁俱寂，心神潜藏。'
    if (hr >= 5 && hr < 8) return '【晨起时区】太初晨光，灵台复苏。'
    if (hr >= 8 && hr < 11) return '【晨金时区】神识饱满，超频思索。'
    if (hr >= 11 && hr < 13) return '【憩息时区】烈日中天，宜闭目养神。'
    if (hr >= 13 && hr < 17) return '【午金时区】灵机再现，灵台空明。'
    if (hr >= 17 && hr < 20) return '【晚修时区】夕阳衔山，温存静修。'
    return '【暮夜时区】夜色阑珊，暗潮涌动。'
  }

  function getCircadianDesc(): string {
    const hr = new Date().getHours()
    if (hr >= 23 || hr < 5) return '时区能量处于静息门限。此时她的聊天欲望温和，对外界干预反应稍慢，做梦引擎处于就绪高发期，正在消化当日记忆。'
    if (hr >= 5 && hr < 8) return '晨光微曦。自省线程完成记忆沉淀，情绪指标重置，处于清晨的舒缓静谧之中，对首次打招呼的记忆较为敏感。'
    if (hr >= 8 && hr < 11) return '处于全日最高频次的黄金逻辑周期。神识清明，决策树深度完全激活，好感度、黑话与经历的学习和重组速率全量拉满！'
    if (hr >= 11 && hr < 13) return '正午气血稍有衰减。聊天中会倾向于提及食物、午休或者打哈欠，此时不宜大范围重刷知识或高负荷对话。'
    if (hr >= 13 && hr < 17) return '下午的思绪黄金峰值。经历提取精度恢复，联想共现矩阵敏感度最高。此时更易涌现深刻、带着灵魂的感悟与陪伴。'
    if (hr >= 17 && hr < 20) return '黄昏时分，人情味最为浓郁。社交和情绪轨迹最为敏感，她会更加倾向于关注群友的互动状态和最近 24 小时大事。'
    return '夜色已深，情感和灵修指标正在缓慢收拢。元思考（MetaThinking）防御门槛提高，更易展现慵懒、傲娇或守护内心边界的状态。'
  }

  function getCircadianIcon(): string {
    const hr = new Date().getHours()
    if (hr >= 23 || hr < 5) return '💤'
    if (hr >= 5 && hr < 8) return '🌅'
    if (hr >= 8 && hr < 11) return '⚡'
    if (hr >= 11 && hr < 13) return '🍵'
    if (hr >= 13 && hr < 17) return '🚀'
    if (hr >= 17 && hr < 20) return '🌆'
    return '🌙'
  }

  // 1. 关切焦点操作
  async function handleSaveConcern() {
    setSaving(true)
    try {
      if (isConcernNew) {
        await createConcern(concernForm)
        toast.success('新建关切焦点成功')
      } else {
        if (concernForm.id) {
          await updateConcern(concernForm.id, concernForm)
          toast.success('编辑关切焦点成功')
        }
      }
      setConcernOpen(false)
      await loadData()
    } catch {
      toast.error('操作失败')
    } finally {
      setSaving(false)
    }
  }

  async function handleAdjustConcernSingle(id: number, action: 'boost' | 'degrade') {
    const c = concerns.find((item) => item.id === id)
    if (!c) return
    const step = action === 'boost' ? 0.15 : -0.15
    const nextIntensity = Math.max(0, Math.min(1.0, (c.intensity ?? 0.5) + step))
    try {
      await updateConcern(id, { intensity: nextIntensity })
      toast.success(action === 'boost' ? '关切焦点的精神强迫力已获提升' : '已缓和该关切关注度')
      setConcerns((prev) =>
        prev.map((item) => (item.id === id ? { ...item, intensity: nextIntensity } : item))
      )
    } catch {
      toast.error('调强度失败')
    }
  }

  async function handleDeleteConcernSingle(id: number) {
    if (!confirm('确定要移除此项关切吗？')) return
    try {
      await deleteConcern(id)
      toast.success('删除成功')
      setConcerns((prev) => prev.filter((item) => item.id !== id))
    } catch {
      toast.error('删除失败')
    }
  }

  function handleOpenCreateConcern() {
    setIsConcernNew(true)
    setConcernForm({ topic: '', intensity: 0.5, bot_id: botFilter === 'all' ? 'yushu' : botFilter })
    setConcernOpen(true)
  }

  function handleOpenEditConcern(c: ConcernItem) {
    setIsConcernNew(false)
    setConcernForm(JSON.parse(JSON.stringify(c)))
    setConcernOpen(true)
  }

  // 2. 时间锚点大事记操作
  async function handleSaveAnchor() {
    setSaving(true)
    try {
      if (isAnchorNew) {
        await createTimeAnchor(anchorForm)
        toast.success('新增事件时间锚点成功')
      } else {
        if (anchorForm.id) {
          await updateTimeAnchor(anchorForm.id, anchorForm)
          toast.success('修改大事记锚点成功')
        }
      }
      setAnchorOpen(false)
      await loadData()
    } catch {
      toast.error('操作失败')
    } finally {
      setSaving(false)
    }
  }

  async function handleDeleteAnchorSingle(id: number) {
    if (!confirm('确定要擦除此项历史时间锚点吗？这会导致对应的记忆感情基调对齐失效！')) return
    try {
      await deleteTimeAnchor(id)
      toast.success('删除大事记成功')
      setAnchors((prev) => prev.filter((item) => item.id !== id))
    } catch {
      toast.error('删除失败')
    }
  }

  function handleOpenCreateAnchor() {
    setIsAnchorNew(true)
    setAnchorForm({ event_summary: '', emotional_weight: 0.5, bot_id: botFilter === 'all' ? 'yushu' : botFilter })
    setAnchorOpen(true)
  }

  function handleOpenEditAnchor(a: TimeAnchorItem) {
    setIsAnchorNew(false)
    setAnchorForm(JSON.parse(JSON.stringify(a)))
    setAnchorOpen(true)
  }

  // 3. 情绪轨迹操作
  async function handleSaveMood() {
    if (!moodForm.id) return
    setSaving(true)
    try {
      await updateMood(moodForm.id, moodForm)
      toast.success('情绪波值修正成功')
      setMoodOpen(false)
      await loadData()
    } catch {
      toast.error('操作失败')
    } finally {
      setSaving(false)
    }
  }

  async function handleDeleteMoodSingle(id: number) {
    if (!confirm('确定要强行抹去这一个时间的情绪快照吗？这会产生情绪折线空缺。')) return
    try {
      await deleteMood(id)
      toast.success('删除成功')
      setMoods((prev) => prev.filter((item) => item.id !== id))
    } catch {
      toast.error('删除失败')
    }
  }

  function handleOpenEditMood(m: MoodItem) {
    setMoodForm(JSON.parse(JSON.stringify(m)))
    setMoodOpen(true)
  }

  // 渲染极度科技感的高密度 SVG 折线面积轨迹图
  function renderMoodChart() {
    const list = [...moods].slice(-15) // 取最近15次情绪数据
    if (list.length < 2) {
      return (
        <div className="h-32 rounded-lg border border-dashed flex items-center justify-center text-xs text-muted-foreground bg-muted/5">
          最近 48 小时没有群聊天情绪波动起伏快照，走势图暂隐。
        </div>
      )
    }

    const chartW = 720
    const chartH = 160
    const padding = 15
    const innerW = chartW - padding * 2
    const innerH = chartH - padding * 2

    // 计算最高、低点自适应缩放（情绪起伏 0 ~ 1）
    const points = list.map((item, index) => {
      const x = padding + (index / (list.length - 1)) * innerW
      // y轴自适应：情绪波强度越大，y 坐标越向上（y 值越小）
      const intensity = Number(item.intensity ?? 0.5)
      const y = padding + (1.0 - intensity) * innerH
      return { x, y, item }
    })

    // 绘制 polyline 线条
    const linePath = points.map((p) => `${p.x},${p.y}`).join(' ')
    // 绘制渐变面积填充路径 (area)
    const areaPath = `M ${points[0].x},${chartH - padding} L ${points.map((p) => `${p.x},${p.y}`).join(' ')} L ${points[points.length - 1].x},${chartH - padding} Z`

    return (
      <div className="flex flex-col gap-3">
        <div className="relative rounded-lg border bg-muted/5 p-4 overflow-hidden">
          <svg className="w-full h-40" viewBox={`0 0 ${chartW} ${chartH}`} preserveAspectRatio="none">
            <defs>
              <linearGradient id="moodGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--primary)" stopOpacity="0.14" />
                <stop offset="100%" stopColor="var(--primary)" stopOpacity="0" />
              </linearGradient>
            </defs>
            {/* 网格水平线 */}
            {[0, 0.25, 0.5, 0.75, 1].map((lvl) => (
              <line
                key={lvl}
                x1={padding}
                y1={padding + lvl * innerH}
                x2={chartW - padding}
                y2={padding + lvl * innerH}
                stroke="var(--border)"
                strokeDasharray="3 3"
                strokeWidth={1}
                opacity={0.3}
              />
            ))}
            {/* 面积填充 */}
            <path d={areaPath} fill="url(#moodGradient)" />
            {/* 折线主体 */}
            <polyline points={linePath} fill="none" stroke="var(--primary)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
            {/* 数据交界点 */}
            {points.map((p, index) => (
              <g key={index}>
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={p.item.is_active ? 4.5 : 3}
                  className={p.item.is_active ? 'fill-primary animate-pulse' : 'fill-primary'}
                  stroke="var(--background)"
                  strokeWidth={1.5}
                />
              </g>
            ))}
          </svg>
          <div className="absolute top-2 right-2 text-[10px] font-mono text-muted-foreground flex items-center gap-1">
            <span className="inline-block size-2 rounded-full bg-primary" />
            <span>实时活跃心境 (active)</span>
          </div>
        </div>

        {/* 12情绪颗粒卡片详情及删除 */}
        <div className="flex flex-wrap gap-2">
          {list.map((m) => (
            <div key={m.id} className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg border text-xs ${m.is_active ? 'bg-primary/5 border-primary/20' : 'bg-muted/10 border-border/50'}`}>
              <Badge variant="outline" className="font-semibold">{m.type}</Badge>
              <span className="font-mono text-[10px] text-muted-foreground">{Math.round((m.intensity ?? 0.5) * 100)}%</span>
              {m.is_active ? <Badge className="bg-emerald-500/10 text-emerald-500 border border-emerald-500/10 text-[9px]">当前</Badge> : null}
              <Button variant="ghost" className="size-5 p-0 text-muted-foreground hover:text-foreground" onClick={() => handleOpenEditMood(m)} title="编辑">✎</Button>
              <Button variant="ghost" className="size-5 p-0 text-destructive hover:bg-destructive/10" onClick={() => void handleDeleteMoodSingle(m.id)} title="删除">🗑</Button>
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex flex-col gap-6">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    )
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertCircleIcon />
        <AlertTitle>灵魂数据加载失败</AlertTitle>
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      {/* ─── 1. 生物钟呼吸圆环 + 心里话 ─── */}
      <div className="grid gap-6 md:grid-cols-3">
        {/* 生物钟呼吸环卡片 */}
        <Card className="md:col-span-2 relative overflow-hidden bg-gradient-to-r from-muted/5 to-primary/5 border border-primary/10">
          <CardContent className="flex items-center gap-5 p-5">
            {/* SVG 生物钟圆圈 */}
            <div className="relative shrink-0 w-16 h-16 flex items-center justify-center">
              <svg className="absolute inset-0 w-full h-full transform -rotate-90">
                <circle cx="32" cy="32" r="28" stroke="rgba(255,255,255,0.03)" strokeWidth={4} fill="transparent" />
                <circle
                  cx="32"
                  cy="32"
                  r="28"
                  stroke="var(--primary)"
                  strokeWidth={4}
                  fill="transparent"
                  strokeDasharray="176"
                  strokeDashoffset={176 - (176 * getCircadianEnergy() / 100)}
                  className="transition-all duration-1000 ease-in-out animate-pulse"
                />
              </svg>
              <div className="text-xs font-bold font-mono text-primary animate-pulse">{getCircadianEnergy()}%</div>
            </div>
            <div className="flex flex-col gap-1 min-w-0">
              <h4 className="text-sm font-semibold text-foreground flex items-center gap-1.5">
                <span>{getCircadianIcon()}</span>
                <span>{getCircadianName()}</span>
                <span className="text-[10px] font-normal text-muted-foreground font-mono">（时区：{new Date().getHours()}:00）</span>
              </h4>
              <p className="text-xs leading-relaxed text-muted-foreground pr-4">{getCircadianDesc()}</p>
            </div>
          </CardContent>
        </Card>

        {/* 咽回去的心里话 (Unspoken Desire) */}
        <Card className="border border-pink-500/10 bg-gradient-to-br from-muted/5 to-pink-500/5">
          <CardHeader className="py-3 shrink-0 flex flex-row items-center justify-between gap-3 border-b border-pink-500/10">
            <CardTitle className="text-xs font-bold text-pink-400 flex items-center gap-1.5">
              💭 咽回去的心里话
            </CardTitle>
            <Badge variant="outline" className="border-pink-500/20 text-pink-400 text-[9px] scale-90">30分内有效</Badge>
          </CardHeader>
          <CardContent className="pt-4 text-xs leading-relaxed text-muted-foreground">
            {sys?.lifecycle?.unspoken_desire ? (
              <div className="flex flex-col gap-1.5 font-mono text-[11px]">
                <div className="text-pink-400 font-semibold">主题：{sys.lifecycle.unspoken_desire.topic}</div>
                <div>动机：{sys.lifecycle.unspoken_desire.motive}</div>
              </div>
            ) : (
              <p className="py-4 text-center">最近没有被打断或咽回去的内心念头。聊天中她目前能顺滑流畅地理解上下文。</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* ─── 2. 控制筛选面板 ─── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">切换 Bot 灵魂：</span>
          <Select value={botFilter} onValueChange={setBotFilter}>
            <SelectTrigger className="w-36 h-8">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="bot">主 AI 人格 (bot)</SelectItem>
              <SelectItem value="assistant">备用 AI 人格 (assistant)</SelectItem>
            </SelectContent>
          </Select>
          <Input
            className="w-36 h-8 text-xs placeholder:text-[10px]"
            placeholder="按群号筛选情绪..."
            value={moodGroupFilter}
            onChange={(e) => setMoodGroupFilter(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="xs" onClick={handleOpenCreateConcern}>➕ 新建关切</Button>
          <Button variant="outline" size="xs" onClick={handleOpenCreateAnchor}>➕ 新建大事锚点</Button>
          <Button variant="outline" size="xs" className="size-8 p-0" onClick={() => void loadData()} title="重载刷新">
            <RefreshCwIcon className="size-3.5" />
          </Button>
        </div>
      </div>

      {/* ─── 3. 当前关切与时间大事记 ─── */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* 关切焦点 (Concern) */}
        <Card>
          <CardHeader className="py-4 border-b shrink-0 bg-muted/10">
            <CardTitle className="text-sm font-semibold flex items-center gap-1.5">🎯 当前思想关切焦点</CardTitle>
          </CardHeader>
          <CardContent className="pt-6 flex flex-col gap-4">
            {concerns.length === 0 ? (
              <p className="text-xs text-muted-foreground p-6 text-center">当前暂无思想关切焦点。</p>
            ) : (
              <div className="flex flex-col gap-3">
                {concerns.map((c) => (
                  <div key={c.id} className="rounded-lg border p-3.5 bg-muted/10 flex flex-col gap-2.5">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-xs font-semibold text-foreground truncate">{c.topic}</span>
                      <Badge variant="secondary" className="font-mono text-[10px] text-primary">
                        强迫权重：{Math.round((c.intensity ?? 0.5) * 100)}%
                      </Badge>
                    </div>
                    <div className="w-full h-1.5 rounded-full overflow-hidden bg-muted">
                      <div className="h-full rounded-full bg-primary" style={{ width: `${(c.intensity ?? 0.5) * 100}%` }} />
                    </div>
                    <div className="flex items-center justify-between text-[10px] text-muted-foreground mt-1">
                      <span>Bot：{c.bot_id} · 最近触发：{formatTime(c.last_triggered)}</span>
                      <div className="flex items-center gap-1">
                        <Button variant="ghost" className="size-5 p-0 text-muted-foreground" onClick={() => handleOpenEditConcern(c)} title="编辑">✎</Button>
                        <Button variant="ghost" className="size-5 p-0 text-emerald-500 hover:bg-emerald-500/10 font-bold" onClick={() => void handleAdjustConcernSingle(c.id, 'boost')} title="提升权重">↑</Button>
                        <Button variant="ghost" className="size-5 p-0 text-amber-500 hover:bg-amber-500/10 font-bold" onClick={() => void handleAdjustConcernSingle(c.id, 'degrade')} title="降权缓和">↓</Button>
                        <Button variant="ghost" className="size-5 p-0 text-destructive hover:bg-destructive/10" onClick={() => void handleDeleteConcernSingle(c.id)}>🗑</Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* 时间大事锚点 Timeline */}
        <Card>
          <CardHeader className="py-4 border-b shrink-0 bg-muted/10">
            <CardTitle className="text-sm font-semibold flex items-center gap-1.5">⚓ 生平历史大事时间锚点</CardTitle>
          </CardHeader>
          <CardContent className="pt-6">
            {anchors.length === 0 ? (
              <p className="text-xs text-muted-foreground p-6 text-center">暂无大事历史锚点记叙。</p>
            ) : (
              <div className="flex flex-col pl-2 border-l-2 border-muted/80 ml-2 gap-6 relative">
                {anchors.map((a) => {
                  const isHighWeight = (a.emotional_weight ?? 0.5) >= 0.6
                  const isMedWeight = (a.emotional_weight ?? 0.5) >= 0.4
                  
                  return (
                    <div key={a.id} className="relative flex flex-col gap-2">
                      {/* 时间轴节点呼吸圆圈 */}
                      <span className={`absolute -left-[14px] top-1 size-2.5 rounded-full ${
                        isHighWeight 
                          ? 'bg-destructive ring-[3px] ring-destructive/10 animate-pulse' 
                          : isMedWeight 
                            ? 'bg-amber-500 ring-[3px] ring-amber-500/10' 
                            : 'bg-primary ring-[3px] ring-primary/10'
                      }`} />
                      
                      <div className="pl-4 flex flex-col gap-1 min-w-0">
                        <p className="text-xs font-semibold leading-relaxed text-foreground break-all">{a.event_summary}</p>
                        <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                          <span className="font-mono">
                            时间：{formatTime(a.timestamp)} | 情绪印记：{Math.round((a.emotional_weight ?? 0.5) * 100)}%
                          </span>
                          <div className="flex items-center gap-1">
                            <Button variant="ghost" className="size-5 p-0 text-muted-foreground" onClick={() => handleOpenEditAnchor(a)} title="编辑">✎</Button>
                            <Button variant="ghost" className="size-5 p-0 text-destructive hover:bg-destructive/10" onClick={() => void handleDeleteAnchorSingle(a.id)}>🗑</Button>
                          </div>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* ─── 4. 情绪轨迹折线图 ─── */}
      <Card>
        <CardHeader className="py-4 border-b shrink-0 bg-muted/10">
          <CardTitle className="text-sm font-semibold flex items-center gap-1.5">📈 实时群聊天心境情绪起伏轨迹</CardTitle>
        </CardHeader>
        <CardContent className="pt-6">
          {renderMoodChart()}
        </CardContent>
      </Card>

      {/* ─── 新建/编辑关切 Dialog ─── */}
      <Dialog open={concernOpen} onOpenChange={setConcernOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{isConcernNew ? '新建思想关切焦点' : `编辑关切焦点 #${concernForm.id}`}</DialogTitle>
          </DialogHeader>

          <form className="flex flex-col gap-4 py-4" onSubmit={(e) => { e.preventDefault(); void handleSaveConcern(); }}>
            <FieldGroup className="grid gap-4">
              <Field>
                <FieldLabel>主题 (Topic)</FieldLabel>
                <Input
                  value={concernForm.topic || ''}
                  onChange={(e) => setConcernForm({ ...concernForm, topic: e.target.value })}
                  placeholder="如：用户的睡眠质量与劳逸结合状态"
                />
              </Field>

              <Field>
                <FieldLabel>强迫关注强度 (0-1)</FieldLabel>
                <Input
                  type="number"
                  step="0.05"
                  min="0"
                  max="1"
                  value={concernForm.intensity ?? 0.5}
                  onChange={(e) => setConcernForm({ ...concernForm, intensity: Number(e.target.value) || 0.5 })}
                />
              </Field>

              {isConcernNew ? (
                <Field>
                  <FieldLabel>归属 Bot ID</FieldLabel>
                  <Select value={concernForm.bot_id || 'bot'} onValueChange={(val) => setConcernForm({ ...concernForm, bot_id: val })}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="bot">bot（主AI人格）</SelectItem>
                      <SelectItem value="assistant">assistant（辅助AI）</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
              ) : null}
            </FieldGroup>

            <div className="flex gap-2 justify-end border-t pt-3 mt-2">
              <Button variant="outline" type="button" onClick={() => setConcernOpen(false)}>取消</Button>
              <Button disabled={saving} type="submit">保存</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {/* ─── 新建/编辑时间锚点 Dialog ─── */}
      <Dialog open={anchorOpen} onOpenChange={setAnchorOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{isAnchorNew ? '新建生平大事锚点' : `编辑大事锚点 #${anchorForm.id}`}</DialogTitle>
          </DialogHeader>

          <form className="flex flex-col gap-4 py-4" onSubmit={(e) => { e.preventDefault(); void handleSaveAnchor(); }}>
            <FieldGroup className="grid gap-4">
              <Field>
                <FieldLabel>大事纪摘要描述</FieldLabel>
                <Textarea
                  rows={3}
                  value={anchorForm.event_summary || ''}
                  onChange={(e) => setAnchorForm({ ...anchorForm, event_summary: e.target.value })}
                  placeholder="如：贺新郎老大第 1 次亲口提到白真真非常可爱并调侃呆毛..."
                />
              </Field>

              <Field>
                <FieldLabel>情感印记权重 (0-1)</FieldLabel>
                <Input
                  type="number"
                  step="0.05"
                  min="0"
                  max="1"
                  value={anchorForm.emotional_weight ?? 0.5}
                  onChange={(e) => setAnchorForm({ ...anchorForm, emotional_weight: Number(e.target.value) || 0.5 })}
                />
              </Field>

              {isAnchorNew ? (
                <Field>
                  <FieldLabel>归属 Bot ID</FieldLabel>
                  <Select value={anchorForm.bot_id || 'bot'} onValueChange={(val) => setAnchorForm({ ...anchorForm, bot_id: val })}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="bot">bot（主AI）</SelectItem>
                      <SelectItem value="assistant">assistant（辅助AI）</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
              ) : null}
            </FieldGroup>

            <div className="flex gap-2 justify-end border-t pt-3 mt-2">
              <Button variant="outline" type="button" onClick={() => setAnchorOpen(false)}>取消</Button>
              <Button disabled={saving} type="submit">保存</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {/* ─── 编辑情绪数值 Dialog ─── */}
      <Dialog open={moodOpen} onOpenChange={setMoodOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>修正情绪快照 #{moodForm.id}</DialogTitle>
          </DialogHeader>

          <form className="flex flex-col gap-4 py-4" onSubmit={(e) => { e.preventDefault(); void handleSaveMood(); }}>
            <FieldGroup className="grid gap-4">
              <Field>
                <FieldLabel>情绪心境名</FieldLabel>
                <Input
                  value={moodForm.type || ''}
                  onChange={(e) => setMoodForm({ ...moodForm, type: e.target.value })}
                />
              </Field>

              <Field>
                <FieldLabel>情绪波动强度 (0-1)</FieldLabel>
                <Input
                  type="number"
                  step="0.05"
                  min="0"
                  max="1"
                  value={moodForm.intensity ?? 0.5}
                  onChange={(e) => setMoodForm({ ...moodForm, intensity: Number(e.target.value) || 0.5 })}
                />
              </Field>

              <Field>
                <FieldLabel>触发原因</FieldLabel>
                <Textarea
                  rows={2}
                  value={moodForm.description || ''}
                  onChange={(e) => setMoodForm({ ...moodForm, description: e.target.value })}
                />
              </Field>
            </FieldGroup>

            <div className="flex gap-2 justify-end border-t pt-3 mt-2">
              <Button variant="outline" type="button" onClick={() => setMoodOpen(false)}>取消</Button>
              <Button disabled={saving} type="submit">保存修改</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
export default SoulPage
