import { useEffect, useState } from 'react'
import { AlertTriangleIcon, CheckCircle2Icon, ExternalLinkIcon, Loader2Icon, RefreshCwIcon, RotateCcwIcon } from 'lucide-react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'

import {
  getDedicatedReviewStatus,
  getLearningCandidate,
  getLearningExperiences,
  getLearningFewShot,
  listLearningCandidates,
  listLearningJobs,
  listLearningPromotions,
  listLearningSources,
  retryLearningPromotion,
  reviewLearningCandidate,
  runLearningJob,
  type ApprovedFewShotExample,
  type DedicatedReviewStatus,
  type LearningCandidateItem,
  type LearningExperiencesPayload,
  type LearningJobItem,
  type LearningListPayload,
  type LearningPromotionItem,
  type LearningSourceItem,
} from '@/api/learningCenter'
import { getSystemStatus, type RegistryBotItem } from '@/api/system'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

const fallbackBots: RegistryBotItem[] = [
  { db_id: 'yushu', name: '羽书', qq_id: '', aliases: [] },
  { db_id: 'baizz', name: '白真真', qq_id: '', aliases: [] },
]

const candidateTypeOptions = [
  ['all', '全部类型'],
  ['worldview_internalization', '世界观内化'],
  ['book_experience_episode', '书中经历'],
  ['few_shot_style', 'FewShot 风格'],
  ['fact', '事实'],
  ['relationship', '关系'],
  ['jargon_candidate', '黑话'],
  ['belief_candidate', '信念'],
]

const reviewStatusOptions = [
  ['all', '全部审核状态'],
  ['pending', '待审核'],
  ['approved', '已批准'],
  ['rejected', '已拒绝'],
  ['delegated', '专属审核'],
]

const promotionStatusOptions = [
  ['all', '全部晋升状态'],
  ['queued', '排队中'],
  ['running', '处理中'],
  ['succeeded', '已成功'],
  ['retryable_failed', '可重试失败'],
  ['terminal_failed', '终态失败'],
]

interface LearningCenterData {
  sources: LearningListPayload<LearningSourceItem>
  jobs: LearningListPayload<LearningJobItem>
  candidates: LearningListPayload<LearningCandidateItem>
  promotions: LearningListPayload<LearningPromotionItem>
  fewShot: Awaited<ReturnType<typeof getLearningFewShot>>
  experiences: LearningExperiencesPayload
}

function textValue(value: unknown, fallback = '—'): string {
  return value === undefined || value === null || value === '' ? fallback : String(value)
}

function formatTime(value: unknown): string {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) return textValue(value)
  return new Date(numeric < 10_000_000_000 ? numeric * 1000 : numeric).toLocaleString('zh-CN')
}

function statusLabel(status: unknown): string {
  const value = String(status ?? 'unknown')
  const labels: Record<string, string> = {
    pending: '待审核',
    approved: '已批准',
    rejected: '已拒绝',
    delegated: '专属审核中',
    queued: '排队中',
    running: '处理中',
    succeeded: '已成功',
    retryable_failed: '失败（可重试）',
    terminal_failed: '失败（不可重试）',
    waiting_dedicated_review: '等待专属审核',
    partial: '部分成功',
    mixed: '混合状态',
    skipped: '已跳过',
    unknown: '未知',
  }
  return labels[value] ?? value
}

function statusVariant(status: unknown): 'default' | 'secondary' | 'destructive' | 'outline' {
  const value = String(status ?? '')
  if (value === 'succeeded' || value === 'approved') return 'default'
  if (value.includes('failed') || value === 'rejected') return 'destructive'
  if (value === 'running' || value === 'queued' || value === 'pending' || value === 'delegated') return 'secondary'
  return 'outline'
}

function candidateTypeLabel(type: unknown): string {
  const found = candidateTypeOptions.find(([value]) => value === String(type))
  return found?.[1] ?? textValue(type, '未知类型')
}

function jsonText(value: unknown): string {
  if (value === undefined || value === null) return '—'
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function PaginationControls({
  offset,
  limit,
  total,
  loading,
  onChange,
}: {
  offset: number
  limit: number
  total: number
  loading: boolean
  onChange: (offset: number) => void
}) {
  const page = Math.floor(offset / limit) + 1
  const totalPages = Math.max(1, Math.ceil(total / limit))
  return (
    <div className="flex items-center justify-between gap-3 border-t pt-3 text-xs text-muted-foreground">
      <span>第 {page} / {totalPages} 页 · 共 {total} 条</span>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" disabled={loading || offset <= 0} onClick={() => onChange(Math.max(0, offset - limit))}>上一页</Button>
        <Button size="sm" variant="outline" disabled={loading || offset + limit >= total} onClick={() => onChange(offset + limit)}>下一页</Button>
      </div>
    </div>
  )
}

function StatusBadge({ status }: { status: unknown }) {
  return <Badge variant={statusVariant(status)}>{statusLabel(status)}</Badge>
}

function SummaryCard({ title, value, description }: { title: string; value: unknown; description?: string }) {
  return (
    <Card>
      <CardHeader className="gap-1 pb-3">
        <CardDescription>{title}</CardDescription>
        <CardTitle className="text-2xl font-mono">{textValue(value, '0')}</CardTitle>
        {description ? <CardDescription className="text-[11px]">{description}</CardDescription> : null}
      </CardHeader>
    </Card>
  )
}

function EmptyState({ children }: { children: string }) {
  return <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">{children}</p>
}

function SourcesPanel({
  sources,
  jobs,
  botId,
  loading,
  onRunJob,
}: {
  sources: LearningListPayload<LearningSourceItem>
  jobs: LearningListPayload<LearningJobItem>
  botId: string
  loading: boolean
  onRunJob: (job: LearningJobItem) => void
}) {
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>来源</CardTitle>
          <CardDescription>按 BotProfile.db_id 查看学习输入来源及游标，不以 QQ 号代替 target ID。</CardDescription>
        </CardHeader>
        <CardContent>
          {sources.items.length === 0 ? <EmptyState>当前 Bot 暂无来源。</EmptyState> : (
            <div className="overflow-auto rounded-lg border">
              <Table>
                <TableHeader><TableRow><TableHead>名称</TableHead><TableHead>类型</TableHead><TableHead>状态</TableHead><TableHead>游标</TableHead></TableRow></TableHeader>
                <TableBody>
                  {sources.items.map((source) => (
                    <TableRow key={source.id}>
                      <TableCell className="font-medium">{textValue(source.name)}</TableCell>
                      <TableCell className="font-mono text-xs">{textValue(source.source_type)}</TableCell>
                      <TableCell><StatusBadge status={source.enabled ? 'approved' : 'rejected'} /></TableCell>
                      <TableCell className="max-w-[240px] truncate font-mono text-xs" title={jsonText(source.cursor)}>{jsonText(source.cursor)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
          <p className="mt-3 text-[11px] text-muted-foreground">当前作用域：<span className="font-mono">{botId}</span></p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>任务</CardTitle>
          <CardDescription>任务返回的 queued/running/skipped 状态直接来自 API；启动后不会提前显示成功。</CardDescription>
        </CardHeader>
        <CardContent>
          {jobs.items.length === 0 ? <EmptyState>当前 Bot 暂无任务。</EmptyState> : (
            <div className="overflow-auto rounded-lg border">
              <Table>
                <TableHeader><TableRow><TableHead>任务</TableHead><TableHead>候选类型</TableHead><TableHead>启用</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader>
                <TableBody>
                  {jobs.items.map((job) => (
                    <TableRow key={job.id}>
                      <TableCell><div className="font-medium">{textValue(job.name)}</div><div className="font-mono text-[10px] text-muted-foreground">job #{job.id}</div></TableCell>
                      <TableCell><Badge variant="outline">{candidateTypeLabel(job.candidate_type)}</Badge></TableCell>
                      <TableCell><StatusBadge status={job.enabled ? 'approved' : 'rejected'} /></TableCell>
                      <TableCell className="text-right"><Button size="sm" variant="outline" disabled={loading || !job.enabled} onClick={() => onRunJob(job)}><RefreshCwIcon data-icon="inline-start" />运行</Button></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function CandidateFilters({
  candidateType,
  reviewStatus,
  promotionStatus,
  source,
  onCandidateTypeChange,
  onReviewStatusChange,
  onPromotionStatusChange,
  onSourceChange,
}: {
  candidateType: string
  reviewStatus: string
  promotionStatus: string
  source: string
  onCandidateTypeChange: (value: string) => void
  onReviewStatusChange: (value: string) => void
  onPromotionStatusChange: (value: string) => void
  onSourceChange: (value: string) => void
}) {
  return (
    <FieldGroup className="grid gap-3 md:grid-cols-4">
      <Field><FieldLabel>候选类型</FieldLabel><Select value={candidateType} onValueChange={onCandidateTypeChange}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{candidateTypeOptions.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></Field>
      <Field><FieldLabel>审核状态</FieldLabel><Select value={reviewStatus} onValueChange={onReviewStatusChange}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{reviewStatusOptions.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></Field>
      <Field><FieldLabel>晋升状态</FieldLabel><Select value={promotionStatus} onValueChange={onPromotionStatusChange}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{promotionStatusOptions.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></Field>
      <Field><FieldLabel>来源名称/类型</FieldLabel><Input value={source} placeholder="按 source 筛选" onChange={(event) => onSourceChange(event.target.value)} /></Field>
    </FieldGroup>
  )
}

function CandidatesPanel({
  payload,
  loading,
  candidateType,
  reviewStatus,
  promotionStatus,
  source,
  onCandidateTypeChange,
  onReviewStatusChange,
  onPromotionStatusChange,
  onSourceChange,
  onOffsetChange,
  onOpen,
  onReview,
}: {
  payload: LearningListPayload<LearningCandidateItem>
  loading: boolean
  candidateType: string
  reviewStatus: string
  promotionStatus: string
  source: string
  onCandidateTypeChange: (value: string) => void
  onReviewStatusChange: (value: string) => void
  onPromotionStatusChange: (value: string) => void
  onSourceChange: (value: string) => void
  onOffsetChange: (offset: number) => void
  onOpen: (candidate: LearningCandidateItem) => void
  onReview: (candidate: LearningCandidateItem, action: 'approve' | 'reject') => void
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>候选</CardTitle>
        <CardDescription>候选详情、证据和晋升状态均以 API 返回为准；点击行查看审核操作与错误详情。</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <CandidateFilters
          candidateType={candidateType}
          reviewStatus={reviewStatus}
          promotionStatus={promotionStatus}
          source={source}
          onCandidateTypeChange={onCandidateTypeChange}
          onReviewStatusChange={onReviewStatusChange}
          onPromotionStatusChange={onPromotionStatusChange}
          onSourceChange={onSourceChange}
        />
        {payload.items.length === 0 ? <EmptyState>暂无符合筛选条件的候选。</EmptyState> : (
          <div className="overflow-auto rounded-lg border">
            <Table>
              <TableHeader><TableRow><TableHead>ID</TableHead><TableHead>类型/内容</TableHead><TableHead>审核状态</TableHead><TableHead>晋升状态</TableHead><TableHead>目标 ID</TableHead><TableHead className="text-right">审核</TableHead></TableRow></TableHeader>
              <TableBody>
                {payload.items.map((candidate) => {
                  const promotionStatusValue = candidate.promotion_status
                  const canReview = candidate.review_status === 'pending'
                  return (
                    <TableRow key={candidate.id} className="cursor-pointer" onClick={() => onOpen(candidate)}>
                      <TableCell className="font-mono text-xs">#{candidate.id}</TableCell>
                      <TableCell className="max-w-[300px]">
                        <div className="flex flex-wrap gap-1"><Badge variant="outline">{candidateTypeLabel(candidate.candidate_type)}</Badge><span className="font-medium truncate">{textValue(candidate.content, candidate.reason)}</span></div>
                      </TableCell>
                      <TableCell><StatusBadge status={candidate.review_status} /></TableCell>
                      <TableCell><StatusBadge status={promotionStatusValue} /></TableCell>
                      <TableCell className="font-mono text-xs">{candidate.target_ids?.length ? candidate.target_ids.join(', ') : '—'}</TableCell>
                      <TableCell className="text-right" onClick={(event) => event.stopPropagation()}>
                        {canReview ? <div className="flex justify-end gap-1"><Button size="xs" disabled={loading} onClick={() => onReview(candidate, 'approve')}>批准</Button><Button size="xs" variant="destructive" disabled={loading} onClick={() => onReview(candidate, 'reject')}>拒绝</Button></div> : <Button size="xs" variant="ghost" onClick={() => onOpen(candidate)}>详情</Button>}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </div>
        )}
        <PaginationControls offset={payload.offset} limit={payload.limit} total={payload.total} loading={loading} onChange={onOffsetChange} />
      </CardContent>
    </Card>
  )
}

function FewShotPanel({ payload }: { payload: LearningCenterData['fewShot'] }) {
  const approvedExamples = (payload.approved_examples ?? []).filter((example) => example.status === 'approved')
  const approvedCandidates = (payload.items ?? []).filter((candidate) => candidate.candidate_type === 'few_shot_style' && candidate.review_status === 'approved')
  return (
    <Card>
      <CardHeader><CardTitle>FewShot</CardTitle><CardDescription>仅展示 <code>few_shot_style</code> 且已批准的样例，不混入其他学习结果或待审核记录。</CardDescription></CardHeader>
      <CardContent className="flex flex-col gap-4">
        {approvedExamples.length === 0 && approvedCandidates.length === 0 ? <EmptyState>暂无已批准 FewShot 样例。</EmptyState> : (
          <div className="grid gap-3 lg:grid-cols-2">
            {approvedExamples.map((example, index) => <FewShotCard key={String(example.id ?? `example-${index}`)} example={example} />)}
            {approvedCandidates.map((candidate) => <Card key={`candidate-${candidate.id}`} className="bg-muted/10"><CardHeader className="pb-2"><div className="flex items-center justify-between gap-2"><CardTitle className="text-sm">学习中心候选 #{candidate.id}</CardTitle><StatusBadge status="approved" /></div></CardHeader><CardContent><p className="whitespace-pre-wrap text-sm">{textValue(candidate.content)}</p></CardContent></Card>)}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function FewShotCard({ example }: { example: ApprovedFewShotExample }) {
  return <Card className="bg-muted/10"><CardHeader className="pb-2"><div className="flex items-center justify-between gap-2"><CardTitle className="text-sm">批准样例 #{textValue(example.id)}</CardTitle><StatusBadge status="approved" /></div><CardDescription>Bot：{textValue(example.bot_id)} · 分数：{textValue(example.score)}</CardDescription></CardHeader><CardContent className="flex flex-col gap-2"><p className="whitespace-pre-wrap text-sm">{textValue(example.content)}</p><div className="flex flex-wrap gap-1">{(example.traits ?? []).map((trait) => <Badge key={trait} variant="outline">{trait}</Badge>)}</div></CardContent></Card>
}

function ExperienceItem({ item, label }: { item: Record<string, unknown>; label: string }) {
  return <Card className="bg-muted/10"><CardHeader className="pb-2"><div className="flex items-center justify-between gap-2"><CardTitle className="text-sm">{label}</CardTitle><Badge variant="outline">{textValue(item.id ?? item.created_at)}</Badge></div></CardHeader><CardContent className="text-sm"><p className="whitespace-pre-wrap leading-relaxed">{textValue(item.content ?? item.summary ?? item.event_summary ?? item.description)}</p><p className="mt-2 text-xs text-muted-foreground">时间：{formatTime(item.created_at ?? item.timestamp ?? item.updated_at)}</p></CardContent></Card>
}

function ExperiencesPanel({ payload }: { payload: LearningExperiencesPayload }) {
  const worldview = payload.worldview_internalization ?? []
  const book = payload.book_experience_episodes ?? []
  const interaction = payload.interaction_experiences ?? []
  const legacyEvolution = payload.legacy_history?.evolution ?? []
  const legacyExperience = payload.legacy_history?.experience ?? []
  return (
    <div className="flex flex-col gap-5">
      <Card className="border-amber-500/20 bg-amber-500/5"><CardHeader><CardTitle>经历 / 内化</CardTitle><CardDescription>按语义隔离世界观内化、书中经历、互动经历与 legacy 历史，不将内化内容伪装成真实经历。</CardDescription></CardHeader></Card>
      <Card>
        <CardHeader><CardTitle>世界观内化</CardTitle><CardDescription>候选类型：worldview_internalization</CardDescription></CardHeader>
        <CardContent className="flex flex-col gap-3"><Alert><AlertTriangleIcon /><AlertTitle>非书中真实经历</AlertTitle><AlertDescription>这些条目是世界观/书设知识的内化，不代表 Bot 在书中亲身经历过。</AlertDescription></Alert>{worldview.length === 0 ? <EmptyState>暂无世界观内化。</EmptyState> : <div className="grid gap-3 lg:grid-cols-2">{worldview.map((item) => <ExperienceItem key={item.id} item={item} label="世界观内化（非书中真实经历）" />)}</div>}</CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>书中经历</CardTitle><CardDescription>候选类型：book_experience_episode；只接受带完整证据的经历候选。</CardDescription></CardHeader>
        <CardContent className="flex flex-col gap-3">{book.length === 0 ? <EmptyState>暂无书中经历。</EmptyState> : <div className="grid gap-3 lg:grid-cols-2">{book.map((item) => <BookExperienceCard key={item.id} item={item} />)}</div>}</CardContent>
      </Card>
      <div className="grid gap-5 xl:grid-cols-2">
        <Card><CardHeader><CardTitle>互动经历</CardTitle><CardDescription>来自真实互动链路的 experience_episodes。</CardDescription></CardHeader><CardContent className="flex flex-col gap-3">{interaction.length === 0 ? <EmptyState>暂无互动经历。</EmptyState> : interaction.map((item, index) => <ExperienceItem key={String(item.id ?? index)} item={item} label="互动经历" />)}</CardContent></Card>
        <Card><CardHeader><CardTitle>legacy 历史经历</CardTitle><CardDescription>只读兼容投影：已生效历史与旧经历分开显示。</CardDescription></CardHeader><CardContent className="flex flex-col gap-3">{legacyEvolution.length === 0 && legacyExperience.length === 0 ? <EmptyState>暂无 legacy 历史。</EmptyState> : <><p className="text-xs font-semibold text-muted-foreground">legacy evolution / 已生效历史</p>{legacyEvolution.map((item, index) => <ExperienceItem key={`evolution-${String(item.id ?? index)}`} item={item} label="legacy 已生效历史" />)}<p className="pt-2 text-xs font-semibold text-muted-foreground">legacy experience / 历史经历</p>{legacyExperience.map((item, index) => <ExperienceItem key={`experience-${String(item.id ?? index)}`} item={item} label="legacy 历史经历" />)}</>}</CardContent></Card>
      </div>
    </div>
  )
}

function BookExperienceCard({ item }: { item: LearningCandidateItem }) {
  const evidence = item.evidence && !Array.isArray(item.evidence) ? item.evidence : {}
  const evidenceFields: Array<[string, string]> = [
    ['语料库/书版本', 'corpus'],
    ['章节引用', 'chapter_reference'],
    ['原文引用', 'original_quote'],
    ['参与者', 'participants'],
    ['目标 Bot 角色', 'target_role'],
    ['知情视角', 'knowledge_perspective'],
  ]
  return <Card className="bg-muted/10"><CardHeader className="pb-2"><div className="flex items-center justify-between gap-2"><CardTitle className="text-sm">书中经历候选 #{item.id}</CardTitle><StatusBadge status={item.review_status} /></div></CardHeader><CardContent className="flex flex-col gap-3"><p className="whitespace-pre-wrap text-sm">{textValue(item.content)}</p><div className="grid gap-2 rounded-lg border bg-background/50 p-3 text-xs md:grid-cols-2">{evidenceFields.map(([label, key]) => <div key={key} className={key === 'original_quote' ? 'md:col-span-2' : ''}><span className="text-muted-foreground">{label}：</span><span className="whitespace-pre-wrap">{jsonText(evidence[key])}</span></div>)}</div></CardContent></Card>
}

function PromotionsPanel({
  payload,
  loading,
  onOffsetChange,
  onRetry,
}: {
  payload: LearningListPayload<LearningPromotionItem>
  loading: boolean
  onOffsetChange: (offset: number) => void
  onRetry: (promotion: LearningPromotionItem) => void
}) {
  return <Card><CardHeader><CardTitle>晋升历史</CardTitle><CardDescription>显示目标 ID、API 返回的状态和错误详情；只有 retryable_failed 才显示安全重试。</CardDescription></CardHeader><CardContent className="flex flex-col gap-4">{payload.items.length === 0 ? <EmptyState>暂无晋升记录。</EmptyState> : <div className="overflow-auto rounded-lg border"><Table><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>候选/目标</TableHead><TableHead>状态</TableHead><TableHead>target ID</TableHead><TableHead>错误详情</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{payload.items.map((promotion) => { const retryable = promotion.promotion_status === 'retryable_failed' || promotion.retryable === true; return <TableRow key={promotion.id}><TableCell className="font-mono text-xs">#{promotion.id}</TableCell><TableCell><div>{candidateTypeLabel(promotion.candidate_type)}</div><div className="text-xs text-muted-foreground">candidate #{textValue(promotion.candidate_id)}</div></TableCell><TableCell><StatusBadge status={promotion.promotion_status} /></TableCell><TableCell className="font-mono text-xs">{textValue(promotion.target_id)}</TableCell><TableCell className="max-w-[280px] text-xs text-destructive"><span title={textValue(promotion.error_message)}>{textValue(promotion.error_code)}{promotion.error_message ? ` · ${promotion.error_message}` : ''}</span></TableCell><TableCell className="text-right">{retryable ? <Button size="sm" variant="outline" disabled={loading} onClick={() => onRetry(promotion)}><RotateCcwIcon data-icon="inline-start" />安全重试</Button> : <span className="text-xs text-muted-foreground">—</span>}</TableCell></TableRow> })}</TableBody></Table></div>}<PaginationControls offset={payload.offset} limit={payload.limit} total={payload.total} loading={loading} onChange={onOffsetChange} /></CardContent></Card>
}

function CandidateDetail({
  candidate,
  dedicated,
  dedicatedError,
  actionLoading,
  onReview,
  onRetry,
}: {
  candidate: LearningCandidateItem
  dedicated: DedicatedReviewStatus | null
  dedicatedError: string
  actionLoading: boolean
  onReview: (action: 'approve' | 'reject') => void
  onRetry: (promotion: LearningPromotionItem) => void
}) {
  const evidence = candidate.evidence
  const canReview = candidate.review_status === 'pending'
  const retryablePromotions = (candidate.promotions ?? []).filter((promotion) => promotion.promotion_status === 'retryable_failed' || promotion.retryable === true)
  const isDedicated = candidate.candidate_type === 'jargon_candidate' || candidate.candidate_type === 'belief_candidate'
  return <div className="flex flex-col gap-4 overflow-auto"><div className="flex flex-wrap items-center gap-2"><Badge variant="outline">#{candidate.id}</Badge><Badge variant="outline">{candidateTypeLabel(candidate.candidate_type)}</Badge><StatusBadge status={candidate.review_status} /><StatusBadge status={candidate.promotion_status} /></div><p className="whitespace-pre-wrap rounded-lg border bg-muted/20 p-3 text-sm">{textValue(candidate.content, candidate.reason)}</p><div className="grid gap-2 text-xs md:grid-cols-2"><div>Bot ID：<span className="font-mono">{candidate.bot_id}</span></div><div>来源 fingerprint：<span className="font-mono">{textValue(candidate.source_fingerprint)}</span></div><div>审核者：{textValue(candidate.reviewer)}</div><div>审核时间：{formatTime(candidate.reviewed_at)}</div><div className="md:col-span-2">目标 ID：<span className="font-mono">{candidate.target_ids?.length ? candidate.target_ids.join(', ') : '—'}</span></div></div><Card><CardHeader className="pb-2"><CardTitle className="text-sm">证据</CardTitle><CardDescription>原始证据只读展示，不在前端补造章节、原文或参与者。</CardDescription></CardHeader><CardContent><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted/30 p-3 text-xs">{jsonText(evidence)}</pre></CardContent></Card>{candidate.candidate_type === 'worldview_internalization' ? <Alert><AlertTriangleIcon /><AlertTitle>非书中真实经历</AlertTitle><AlertDescription>世界观内化仅是知识内化，不代表真实经历。</AlertDescription></Alert> : null}{isDedicated ? <Card><CardHeader className="pb-2"><CardTitle className="text-sm">专属审核</CardTitle><CardDescription>黑话/信念只能委派给原有专属审核，学习中心不会绕过专属服务直接生效。</CardDescription></CardHeader><CardContent className="flex flex-col gap-2 text-xs">{dedicatedError ? <Alert variant="destructive"><AlertTitle>专属审核状态不可用</AlertTitle><AlertDescription>{dedicatedError}</AlertDescription></Alert> : dedicated ? <><div className="flex flex-wrap items-center gap-2"><StatusBadge status={dedicated.status} /><span>target ID：<span className="font-mono">{textValue(dedicated.target_id)}</span></span></div>{dedicated.deep_link ? <DeepLink href={dedicated.deep_link} label="打开专属审核深链" /> : <span className="text-muted-foreground">暂无专属审核深链</span>}{dedicated.error ? <p className="text-destructive">错误：{dedicated.error}</p> : null}</> : <span className="text-muted-foreground">正在读取专属审核同步状态…</span>}</CardContent></Card> : null}<Card><CardHeader className="pb-2"><CardTitle className="text-sm">操作历史与晋升</CardTitle></CardHeader><CardContent className="flex flex-col gap-2">{(candidate.operations ?? []).length === 0 ? <span className="text-xs text-muted-foreground">暂无操作历史。</span> : candidate.operations?.map((operation, index) => <div key={`${String(operation.id ?? operation.kind)}-${index}`} className="rounded border p-2 text-xs"><div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{textValue(operation.kind)}</Badge><StatusBadge status={operation.status} />{operation.target_id !== undefined ? <span className="font-mono">target: {textValue(operation.target_id)}</span> : null}</div>{operation.error_message ? <p className="mt-1 text-destructive">{textValue(operation.error_code)} · {textValue(operation.error_message)}</p> : null}</div>)}</CardContent></Card>{candidate.failures?.length ? <Alert variant="destructive"><AlertTitle>晋升错误详情</AlertTitle><AlertDescription><div className="flex flex-col gap-1">{candidate.failures.map((failure, index) => <div key={`${failure.id ?? index}`}>{textValue(failure.code)} · {textValue(failure.message)} {failure.retryable ? '(可重试)' : '(不可重试)'}</div>)}</div></AlertDescription></Alert> : null}<div className="flex flex-wrap justify-end gap-2 border-t pt-3">{canReview ? <><Button disabled={actionLoading} onClick={() => onReview('approve')}><CheckCircle2Icon data-icon="inline-start" />批准</Button><Button variant="destructive" disabled={actionLoading} onClick={() => onReview('reject')}>拒绝</Button></> : null}{retryablePromotions.map((promotion) => <Button key={promotion.id} variant="outline" disabled={actionLoading} onClick={() => onRetry(promotion)}><RotateCcwIcon data-icon="inline-start" />安全重试 #{promotion.id}</Button>)}</div></div>
}

function DeepLink({ href, label }: { href: string; label: string }) {
  if (href.startsWith('/')) return <Link className="inline-flex items-center gap-1 text-primary underline underline-offset-2" to={href}>{label}<ExternalLinkIcon className="size-3" /></Link>
  return <a className="inline-flex items-center gap-1 text-primary underline underline-offset-2" href={href} target="_blank" rel="noreferrer">{label}<ExternalLinkIcon className="size-3" /></a>
}

export function LearningCenterPage() {
  const [bots, setBots] = useState<RegistryBotItem[]>(fallbackBots)
  const [botId, setBotId] = useState('')
  const [data, setData] = useState<LearningCenterData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [activeSection, setActiveSection] = useState('sources')
  const [candidateOffset, setCandidateOffset] = useState(0)
  const [promotionOffset, setPromotionOffset] = useState(0)
  const [candidateType, setCandidateType] = useState('all')
  const [reviewStatus, setReviewStatus] = useState('all')
  const [promotionStatus, setPromotionStatus] = useState('all')
  const [source, setSource] = useState('')
  const [selectedCandidate, setSelectedCandidate] = useState<LearningCandidateItem | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [dedicated, setDedicated] = useState<DedicatedReviewStatus | null>(null)
  const [dedicatedError, setDedicatedError] = useState('')
  const [actionLoading, setActionLoading] = useState(false)

  useEffect(() => {
    let alive = true
    getSystemStatus().then((payload) => {
      if (!alive) return
      const availableBots = payload.registry_bots?.length ? payload.registry_bots : fallbackBots
      setBots(availableBots)
      setBotId((current) => current || availableBots[0].db_id)
    }).catch(() => {
      if (alive) setBotId((current) => current || fallbackBots[0].db_id)
    })
    return () => { alive = false }
  }, [])

  async function loadData(requestedBotId = botId) {
    if (!requestedBotId) return
    setLoading(true)
    setError('')
    try {
      const query = { bot_id: requestedBotId, limit: 20 }
      const [sources, jobs, candidates, promotions, fewShot, experiences] = await Promise.all([
        listLearningSources(query),
        listLearningJobs(query),
        listLearningCandidates({ ...query, ...(candidateType !== 'all' ? { candidate_type: candidateType } : {}), ...(reviewStatus !== 'all' ? { review_status: reviewStatus } : {}), ...(promotionStatus !== 'all' ? { promotion_status: promotionStatus } : {}), ...(source.trim() ? { source: source.trim() } : {}), offset: candidateOffset }),
        listLearningPromotions({ ...query, ...(promotionStatus !== 'all' ? { promotion_status: promotionStatus } : {}), offset: promotionOffset }),
        getLearningFewShot({ ...query, offset: 0 }),
        getLearningExperiences({ ...query, offset: 0 }),
      ])
      setData({ sources, jobs, candidates, promotions, fewShot, experiences })
    } catch (err) {
      setError(err instanceof Error ? err.message : '学习中心数据加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (botId) void loadData(botId)
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [botId, candidateOffset, promotionOffset, candidateType, reviewStatus, promotionStatus, source])

  async function handleOpenCandidate(candidate: LearningCandidateItem) {
    setSelectedCandidate(candidate)
    setDetailLoading(true)
    setDedicated(null)
    setDedicatedError('')
    try {
      const detail = await getLearningCandidate(candidate.id, botId)
      setSelectedCandidate(detail.item)
      if (detail.item.candidate_type === 'jargon_candidate' || detail.item.candidate_type === 'belief_candidate') {
        try {
          const dedicatedPayload = await getDedicatedReviewStatus(candidate.id, botId)
          setDedicated(dedicatedPayload.item)
        } catch (err) {
          setDedicatedError(err instanceof Error ? err.message : '专属审核状态读取失败')
        }
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '候选详情读取失败')
    } finally {
      setDetailLoading(false)
    }
  }

  async function handleReview(candidate: LearningCandidateItem, action: 'approve' | 'reject') {
    setActionLoading(true)
    try {
      const result = await reviewLearningCandidate(candidate.id, botId, action, { idempotencyKey: `learning-review-${botId}-${candidate.id}-${action}` })
      await loadData(botId)
      const reviewedCandidateValue = result.item && 'candidate' in result.item ? result.item.candidate : undefined
      const reviewedCandidate = reviewedCandidateValue && typeof reviewedCandidateValue === 'object' ? reviewedCandidateValue as LearningCandidateItem : undefined
      if (reviewedCandidate?.review_status === 'delegated') toast.info('已委派专属审核，最终状态以专属审核 API 为准。')
      else if (reviewedCandidate?.review_status === 'approved') toast.info('审核状态已由 API 更新；晋升仍以晋升状态为准。')
      else toast.info('审核请求已返回，请以 API 状态确认结果。')
      setSelectedCandidate(reviewedCandidate ?? null)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '审核请求失败')
    } finally {
      setActionLoading(false)
    }
  }

  async function handleRetry(promotion: LearningPromotionItem) {
    setActionLoading(true)
    try {
      const result = await retryLearningPromotion(promotion.id, botId, `learning-retry-${botId}-${promotion.id}`)
      await loadData(botId)
      const item = result.item && !('candidate' in result.item) ? result.item as LearningPromotionItem : undefined
      if (item?.promotion_status === 'succeeded') toast.success('重试结果已由 API 确认为成功。')
      else if (item?.promotion_status === 'running' || item?.promotion_status === 'queued') toast.info(`重试已提交，当前状态：${statusLabel(item.promotion_status)}`)
      else toast.info('重试请求已返回，请以 API 状态确认结果。')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '安全重试失败')
    } finally {
      setActionLoading(false)
    }
  }

  async function handleRunJob(job: LearningJobItem) {
    setActionLoading(true)
    try {
      const result = await runLearningJob(job.id, botId, `learning-run-${botId}-${job.id}`)
      await loadData(botId)
      const item = result.item && !('candidate' in result.item) ? result.item as Record<string, unknown> : undefined
      toast.info(`任务 API 状态：${statusLabel(item?.status)}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '任务运行失败')
    } finally {
      setActionLoading(false)
    }
  }

  const candidatePayload = data?.candidates ?? { items: [], total: 0, limit: 20, offset: candidateOffset, has_more: false }
  const promotionPayload = data?.promotions ?? { items: [], total: 0, limit: 20, offset: promotionOffset, has_more: false }

  return <div className="flex flex-col gap-6"><div className="flex flex-wrap items-start justify-between gap-4"><div><h1 className="text-xl font-bold tracking-tight">通用学习中心</h1><p className="text-sm text-muted-foreground">统一查看学习来源、候选审核、FewShot、经历内化与晋升历史。</p></div><div className="flex items-center gap-2"><label className="text-xs text-muted-foreground" htmlFor="learning-bot">Bot</label><Select value={botId || undefined} onValueChange={(value) => { setCandidateOffset(0); setPromotionOffset(0); setBotId(value) }}><SelectTrigger id="learning-bot" className="w-44"><SelectValue placeholder="选择 BotProfile.db_id" /></SelectTrigger><SelectContent>{bots.map((bot) => <SelectItem key={bot.db_id} value={bot.db_id}>{bot.name} ({bot.db_id})</SelectItem>)}</SelectContent></Select><Button variant="outline" size="icon-sm" disabled={loading} onClick={() => void loadData()} title="刷新学习中心"><RefreshCwIcon className={loading ? 'animate-spin' : ''} /></Button></div></div><div className="grid gap-3 md:grid-cols-4"><SummaryCard title="来源" value={data?.sources.total} /><SummaryCard title="任务" value={data?.jobs.total} /><SummaryCard title="候选" value={data?.candidates.total} description="当前筛选" /><SummaryCard title="晋升历史" value={data?.promotions.total} description="当前 Bot 作用域" /></div>{error ? <Alert variant="destructive"><AlertTriangleIcon /><AlertTitle>学习中心加载失败</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-3"><span>{error}</span><Button size="sm" variant="outline" onClick={() => void loadData()}>重试加载</Button></AlertDescription></Alert> : null}{loading && !data ? <Skeleton className="h-96 w-full" /> : <Tabs value={activeSection} onValueChange={setActiveSection}><TabsList className="grid h-auto w-full grid-cols-2 gap-1 sm:grid-cols-3 lg:grid-cols-6"><TabsTrigger value="sources">来源</TabsTrigger><TabsTrigger value="jobs">任务</TabsTrigger><TabsTrigger value="candidates">候选</TabsTrigger><TabsTrigger value="fewshot">FewShot</TabsTrigger><TabsTrigger value="experiences">经历/内化</TabsTrigger><TabsTrigger value="promotions">晋升历史</TabsTrigger></TabsList><TabsContent value="sources"><SourcesPanel sources={data?.sources ?? { items: [], total: 0, limit: 20, offset: 0, has_more: false }} jobs={data?.jobs ?? { items: [], total: 0, limit: 20, offset: 0, has_more: false }} botId={botId} loading={actionLoading} onRunJob={(job) => void handleRunJob(job)} /></TabsContent><TabsContent value="jobs"><SourcesPanel sources={data?.sources ?? { items: [], total: 0, limit: 20, offset: 0, has_more: false }} jobs={data?.jobs ?? { items: [], total: 0, limit: 20, offset: 0, has_more: false }} botId={botId} loading={actionLoading} onRunJob={(job) => void handleRunJob(job)} /></TabsContent><TabsContent value="candidates"><CandidatesPanel payload={candidatePayload} loading={loading || actionLoading} candidateType={candidateType} reviewStatus={reviewStatus} promotionStatus={promotionStatus} source={source} onCandidateTypeChange={(value) => { setCandidateOffset(0); setCandidateType(value) }} onReviewStatusChange={(value) => { setCandidateOffset(0); setReviewStatus(value) }} onPromotionStatusChange={(value) => { setCandidateOffset(0); setPromotionStatus(value) }} onSourceChange={(value) => { setCandidateOffset(0); setSource(value) }} onOffsetChange={setCandidateOffset} onOpen={(candidate) => void handleOpenCandidate(candidate)} onReview={(candidate, action) => void handleReview(candidate, action)} /></TabsContent><TabsContent value="fewshot"><FewShotPanel payload={data?.fewShot ?? { approved_examples: [], items: [], total: 0, limit: 20, offset: 0, has_more: false }} /></TabsContent><TabsContent value="experiences"><ExperiencesPanel payload={data?.experiences ?? { worldview_internalization: [], book_experience_episodes: [], interaction_experiences: [], legacy_history: { evolution: [], experience: [] } }} /></TabsContent><TabsContent value="promotions"><PromotionsPanel payload={promotionPayload} loading={loading || actionLoading} onOffsetChange={setPromotionOffset} onRetry={(promotion) => void handleRetry(promotion)} /></TabsContent></Tabs>}{selectedCandidate ? <Dialog open onOpenChange={(open) => { if (!open) setSelectedCandidate(null) }}><DialogContent className="flex max-h-[88vh] flex-col sm:max-w-3xl"><DialogHeader><DialogTitle>候选详情与审核</DialogTitle><DialogDescription>{detailLoading ? '正在读取 API 详情…' : '证据、目标 ID、状态和错误均来自当前 Bot 的 API 响应。'}</DialogDescription></DialogHeader>{detailLoading ? <div className="flex items-center justify-center py-12 text-muted-foreground"><Loader2Icon className="mr-2 animate-spin" />读取详情</div> : <CandidateDetail candidate={selectedCandidate} dedicated={dedicated} dedicatedError={dedicatedError} actionLoading={actionLoading} onReview={(action) => void handleReview(selectedCandidate, action)} onRetry={(promotion) => void handleRetry(promotion)} />}</DialogContent></Dialog> : null}</div>
}

export default LearningCenterPage
