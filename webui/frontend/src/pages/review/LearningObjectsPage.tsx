import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangleIcon } from 'lucide-react'
import { toast } from 'sonner'

import { getLearningObjectsReview, reviewCandidate, type LearningObjectItem, type LearningObjectReviewPayload, type ReviewCandidate } from '@/api/review'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldLabel } from '@/components/ui/field'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

const learningObjectFilterOptions = [
  { value: 'pending', label: 'pending · 待审候选' },
  { value: 'risky', label: 'risky · 风险候选' },
  { value: 'duplicate', label: 'duplicate · 重复项' },
]

const learningObjectLinks = [
  { label: 'BookLore', route: '/blackbox/book-lore' },
  { label: 'FewShot', route: '/blackbox/fewshot' },
  { label: 'Facts', route: '/blackbox/facts' },
  { label: 'People', route: '/blackbox/people' },
]

type CandidateBucket = 'pending' | 'risky' | 'duplicate'

function riskLabel(risk: unknown): string {
  const value = String(risk ?? 'unknown')
  if (value === 'high') return '高'
  if (value === 'medium') return '中'
  if (value === 'low') return '低'
  if (value === 'unknown') return '未知'
  return value
}

function modeLabel(enabled: unknown, disabledReason: unknown): string {
  if (enabled) return '已启用'
  const reason = String(disabledReason ?? '')
  return reason || '已关闭'
}

function fieldText(item: Record<string, unknown>, key: string, fallback = '-'): string {
  const value = item[key]
  return value === undefined || value === null || value === '' ? fallback : String(value)
}

function candidateId(item: ReviewCandidate | Record<string, unknown>): number | null {
  const value = Number(item.id)
  return Number.isFinite(value) && value > 0 ? value : null
}

function candidateTitle(item: ReviewCandidate | Record<string, unknown>): string {
  return String(item.object_key ?? item.key ?? item.candidate_type ?? item.type ?? item.id ?? 'candidate')
}

function candidatePreview(item: ReviewCandidate | Record<string, unknown>): string {
  return String(item.content ?? item.preview ?? item.reason ?? item.message ?? '暂无内容预览')
}

function SummaryCard({ title, value }: { title: string; value: unknown }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{title}</CardDescription>
        <CardTitle className="text-2xl">{String(value ?? 0)}</CardTitle>
      </CardHeader>
    </Card>
  )
}

function ObjectDetailCard({ item }: { item: LearningObjectItem }) {
  return (
    <div className="grid gap-2 rounded-lg border bg-muted/20 p-3 text-xs md:grid-cols-2">
      <div><span className="text-muted-foreground">写入路径：</span>{String(item.write_path ?? '-')}</div>
      <div><span className="text-muted-foreground">存储：</span>{String(item.storage ?? '-')}</div>
      <div><span className="text-muted-foreground">注入通道：</span>{String(item.injection_channel ?? '-')}</div>
      <div><span className="text-muted-foreground">风险：</span>{riskLabel(item.risk)}</div>
      <div className="md:col-span-2"><span className="text-muted-foreground">运行模式禁用原因：</span>{modeLabel(item.mode_enabled, item.mode_disabled_reason)}</div>
    </div>
  )
}

function CandidateCard({
  item,
  bucket,
  reviewingId,
  onReview,
}: {
  item: ReviewCandidate | Record<string, unknown>
  bucket: CandidateBucket
  reviewingId: number | null
  onReview: (item: ReviewCandidate | Record<string, unknown>, action: 'approve' | 'reject') => void
}) {
  const id = candidateId(item)
  return (
    <Card size="sm">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="truncate text-sm">{candidateTitle(item)}</CardTitle>
            <CardDescription>结构化候选卡片 · {bucket}</CardDescription>
          </div>
          <Badge variant={bucket === 'risky' ? 'destructive' : 'secondary'}>{bucket}</Badge>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">{candidatePreview(item)}</p>
        <div className="grid gap-2 text-xs md:grid-cols-2">
          <div><span className="text-muted-foreground">对象：</span>{fieldText(item, 'object_key', fieldText(item, 'key'))}</div>
          <div><span className="text-muted-foreground">类型：</span>{fieldText(item, 'candidate_type', fieldText(item, 'type'))}</div>
          <div><span className="text-muted-foreground">风险：</span>{fieldText(item, 'object_risk', fieldText(item, 'risk'))}</div>
          <div><span className="text-muted-foreground">状态：</span>{fieldText(item, 'review_status', fieldText(item, 'status'))}</div>
        </div>
        {bucket !== 'duplicate' && id ? (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={reviewingId === id} onClick={() => onReview(item, 'approve')}>批准</Button>
            <Button size="sm" variant="destructive" disabled={reviewingId === id} onClick={() => onReview(item, 'reject')}>拒绝</Button>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">重复项当前只读展示；候选可 approve/reject。</p>
        )}
      </CardContent>
    </Card>
  )
}

export function LearningObjectsPage() {
  const [data, setData] = useState<LearningObjectReviewPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState<CandidateBucket>('pending')
  const [reviewingId, setReviewingId] = useState<number | null>(null)

  async function load() {
    setLoading(true)
    setError('')
    try {
      const payload = await getLearningObjectsReview()
      setData(payload)
    } catch (err) {
      setError(err instanceof Error ? err.message : '学习对象审查加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function handleReviewCandidate(item: ReviewCandidate | Record<string, unknown>, action: 'approve' | 'reject') {
    const id = candidateId(item)
    if (!id) {
      toast.error('候选缺少 id，无法审核')
      return
    }
    setReviewingId(id)
    try {
      await reviewCandidate(id, action)
      toast.success(action === 'approve' ? '候选已批准' : '候选已拒绝')
      await load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '候选审核失败')
    } finally {
      setReviewingId(null)
    }
  }

  if (loading) {
    return <Skeleton className="h-96 w-full" />
  }
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertTriangleIcon />
        <AlertTitle>加载失败</AlertTitle>
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    )
  }

  const summary = data?.summary ?? {}
  const objects = data?.objects ?? []
  const pending = data?.pending_candidates ?? []
  const risky = data?.risky_candidates ?? []
  const duplicates = data?.duplicate_entries ?? []
  const candidateGroups: Record<CandidateBucket, Array<ReviewCandidate | Record<string, unknown>>> = {
    pending,
    risky,
    duplicate: duplicates,
  }
  const selectedCandidates = candidateGroups[filter] ?? []

  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-4 md:grid-cols-4">
        <SummaryCard title="对象数" value={summary.objects} />
        <SummaryCard title="高风险对象" value={summary.high_risk_objects} />
        <SummaryCard title="待审候选" value={summary.pending_candidates} />
        <SummaryCard title="风险候选" value={summary.risky_candidates} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>学习对象结构化审查</CardTitle>
          <CardDescription>pending / risky / duplicate 可筛选；候选可 approve/reject，并和黑盒管理页互链。</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Field className="max-w-xs">
            <FieldLabel>候选筛选</FieldLabel>
            <Select value={filter} onValueChange={(value) => setFilter(value as CandidateBucket)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {learningObjectFilterOptions.map((option) => (
                    <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </Field>
          <div className="grid gap-3 md:grid-cols-4">
            {learningObjectLinks.map((link) => (
              <Button key={link.route} asChild variant="outline">
                <Link to={link.route}>{link.label}</Link>
              </Button>
            ))}
          </div>
          <div className="hidden">
            <Link to="/blackbox/book-lore">BookLore</Link>
            <Link to="/blackbox/fewshot">FewShot</Link>
            <Link to="/blackbox/facts">Facts</Link>
            <Link to="/blackbox/people">People</Link>
          </div>
        </CardContent>
      </Card>

      {duplicates.length > 0 ? (
        <Alert>
          <AlertTriangleIcon />
          <AlertTitle>发现重复登记提示</AlertTitle>
          <AlertDescription>{duplicates.length} 个学习对象存在运行时重复检测发现。</AlertDescription>
        </Alert>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>学习对象登记表</CardTitle>
          <CardDescription>写入路径、存储、风险、运行模式禁用原因和可注入通道</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {objects.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无学习对象。</p>
          ) : (
            <>
              <div className="overflow-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>对象键</TableHead>
                      <TableHead>风险</TableHead>
                      <TableHead>模式</TableHead>
                      <TableHead>写入路径</TableHead>
                      <TableHead>存储</TableHead>
                      <TableHead>注入通道</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {objects.map((item) => (
                      <TableRow key={item.key ?? item.name}>
                        <TableCell className="font-medium">{item.key ?? item.name}</TableCell>
                        <TableCell><Badge variant={item.risk === 'high' ? 'destructive' : 'secondary'}>{riskLabel(item.risk)}</Badge></TableCell>
                        <TableCell>{modeLabel(item.mode_enabled, item.mode_disabled_reason)}</TableCell>
                        <TableCell>{String(item.write_path ?? '-')}</TableCell>
                        <TableCell>{String(item.storage ?? '-')}</TableCell>
                        <TableCell>{String(item.injection_channel ?? '-')}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <div className="grid gap-3 lg:grid-cols-2">
                {objects.slice(0, 6).map((item) => (
                  <ObjectDetailCard key={item.key ?? item.name} item={item} />
                ))}
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>结构化候选卡片</CardTitle>
          <CardDescription>{filter} · {selectedCandidates.length} 条</CardDescription>
        </CardHeader>
        <CardContent>
          {selectedCandidates.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无候选。</p>
          ) : (
            <div className="grid gap-3 lg:grid-cols-2">
              {selectedCandidates.slice(0, 12).map((item, index) => (
                <CandidateCard key={`${filter}-${candidateId(item) ?? index}`} item={item} bucket={filter} reviewingId={reviewingId} onReview={handleReviewCandidate} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
