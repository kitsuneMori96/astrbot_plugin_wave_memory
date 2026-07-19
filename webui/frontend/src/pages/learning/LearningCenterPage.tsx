import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangleIcon, CheckCircle2Icon, Loader2Icon, PlayIcon, RefreshCwIcon, RotateCcwIcon } from 'lucide-react'
import { toast } from 'sonner'

import { isRequestCancelled } from '@/api/client'
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
  type DedicatedReviewStatus,
  type LearningCandidateItem,
  type LearningExperiencesPayload,
  type LearningJobItem,
  type LearningListPayload,
  type LearningPromotionItem,
  type LearningSourceItem,
} from '@/api/learningCenter'
import { getScopeOptions, scopeOptionsFor } from '@/api/options'
import { ObjectDeepLink, PaginationControls, QueryState, ResponsiveTable, ScopeSelect } from '@/components/shared'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { usePaginationSearchParams } from '@/hooks/use-pagination-search-params'

type QueryStatus = 'loading' | 'success' | 'empty' | 'error'
type AsyncState<T> = { status: QueryStatus; data: T | null; error?: unknown }
const loadingState = <T,>(): AsyncState<T> => ({ status: 'loading', data: null })
const learningTabs = ['sources', 'jobs', 'candidates', 'fewshot', 'experiences', 'promotions'] as const
type LearningTab = typeof learningTabs[number]

const candidateTypeOptions = [
  ['all', '全部类型'],
  ['worldview_internalization', '世界观内化'],
  ['book_experience_episode', '书中经历'],
  ['interaction_experience', '互动经历'],
  ['few_shot_style', 'FewShot 风格'],
  ['fact', '事实'],
  ['relationship', '关系'],
  ['book_lore', '书设知识'],
  ['jargon_candidate', '术语候选'],
  ['belief_candidate', '信念候选'],
] as const

const reviewStatusOptions = [
  ['all', '全部审核状态'],
  ['pending', '待审核'],
  ['approved', '已批准'],
  ['rejected', '已拒绝'],
  ['ignored', '已忽略'],
  ['delegated', '专属审核'],
] as const

const promotionStatusOptions = [
  ['all', '全部晋升状态'],
  ['queued', '排队中'],
  ['running', '处理中'],
  ['succeeded', '已成功'],
  ['retryable_failed', '可重试失败'],
  ['terminal_failed', '终态失败'],
  ['waiting_dedicated_review', '等待专属审核'],
  ['partial', '部分成功'],
  ['mixed', '混合状态'],
] as const

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function textValue(value: unknown, fallback = '—'): string {
  if (value === undefined || value === null || value === '') return fallback
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.length ? value.map((item) => typeof item === 'object' ? compactObject(item) : String(item)).join('、') : fallback
  if (typeof value === 'object') return compactObject(value)
  return String(value)
}

function compactObject(value: unknown): string {
  try { return JSON.stringify(value) } catch { return String(value) }
}

function formatTime(value: unknown): string {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) return textValue(value)
  return new Date(numeric < 10_000_000_000 ? numeric * 1000 : numeric).toLocaleString('zh-CN')
}

function statusLabel(status: unknown): string {
  const value = String(status ?? 'unknown')
  const labels: Record<string, string> = {
    pending: '待审核', approved: '已批准', rejected: '已拒绝', ignored: '已忽略', delegated: '专属审核中',
    queued: '排队中', running: '处理中', committed: '已提交', succeeded: '已成功',
    retryable_failed: '失败（可重试）', terminal_failed: '失败（不可重试）', waiting_dedicated_review: '等待专属审核',
    partial: '部分成功', mixed: '混合状态', skipped: '已跳过', unknown: '未知',
  }
  return labels[value] ?? value
}

function statusVariant(status: unknown): 'default' | 'secondary' | 'destructive' | 'outline' {
  const value = String(status ?? '')
  if (value === 'succeeded' || value === 'approved' || value === 'committed') return 'default'
  if (value.includes('failed') || value === 'rejected') return 'destructive'
  if (['running', 'queued', 'pending', 'delegated'].includes(value)) return 'secondary'
  return 'outline'
}

function StatusBadge({ status }: { status: unknown }) {
  return <Badge variant={statusVariant(status)}>{statusLabel(status)}</Badge>
}

function candidateTypeLabel(type: unknown): string {
  return candidateTypeOptions.find(([value]) => value === String(type))?.[1] ?? textValue(type, '未知类型')
}

function totalText<T>(payload: LearningListPayload<T> | null): string {
  if (!payload) return '—'
  return payload.page.total_status === 'exact' && payload.page.total !== null ? String(payload.page.total) : '不可用'
}

function SummaryCard({ title, value, description }: { title: string; value: string; description?: string }) {
  return <Card><CardHeader className="gap-1 pb-3"><CardDescription>{title}</CardDescription><CardTitle className="font-mono text-2xl">{value}</CardTitle>{description ? <CardDescription className="text-xs">{description}</CardDescription> : null}</CardHeader></Card>
}

function EmptyState({ children }: { children: string }) {
  return <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">{children}</p>
}

function DetailGrid({ value, keys }: { value: unknown; keys?: Array<[string, string]> }) {
  const record = asRecord(value)
  if (!record || Object.keys(record).length === 0) return <p className="text-sm text-muted-foreground">未记录。</p>
  const entries = keys?.length
    ? [...keys.filter(([, key]) => key in record).map(([label, key]) => [label, record[key]] as const), ...Object.entries(record).filter(([key]) => !keys.some(([, preferred]) => preferred === key))]
    : Object.entries(record)
  return <dl className="grid gap-2 text-xs sm:grid-cols-2">{entries.map(([label, value], index) => <div key={`${label}-${index}`} className="min-w-0 rounded-md border bg-muted/20 p-2"><dt className="text-muted-foreground">{label}</dt><dd className="mt-1 whitespace-pre-wrap break-words">{textValue(value)}</dd></div>)}</dl>
}

function SourceTable({ payload }: { payload: LearningListPayload<LearningSourceItem> }) {
  if (!payload.items.length) return <EmptyState>当前 Bot 暂无来源。</EmptyState>
  return <ResponsiveTable label="学习来源清单" table={<Table><TableHeader><TableRow><TableHead>名称</TableHead><TableHead>类型</TableHead><TableHead>状态</TableHead><TableHead>游标</TableHead><TableHead>配置摘要</TableHead></TableRow></TableHeader><TableBody>{payload.items.map((source) => <TableRow key={source.id}><TableCell><div className="font-medium">{textValue(source.name)}</div><div className="font-mono text-xs text-muted-foreground">source #{source.id}</div></TableCell><TableCell className="font-mono text-xs">{textValue(source.source_type)}</TableCell><TableCell><StatusBadge status={source.enabled === false ? 'rejected' : 'approved'} /></TableCell><TableCell className="max-w-64 truncate font-mono text-xs" title={textValue(source.cursor)}>{textValue(source.cursor)}</TableCell><TableCell className="max-w-64 truncate text-xs" title={textValue(source.config)}>{textValue(source.config)}</TableCell></TableRow>)}</TableBody></Table>} cards={payload.items.map((source) => <article key={source.id} className="flex flex-col gap-3 rounded-lg border bg-card p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><p className="font-medium">{textValue(source.name)}</p><p className="font-mono text-xs text-muted-foreground">source #{source.id}</p></div><StatusBadge status={source.enabled === false ? 'rejected' : 'approved'} /></div><dl className="grid gap-2 text-sm"><div><dt className="text-muted-foreground">类型</dt><dd className="break-words font-mono text-xs">{textValue(source.source_type)}</dd></div><div><dt className="text-muted-foreground">游标</dt><dd className="break-words font-mono text-xs">{textValue(source.cursor)}</dd></div><div><dt className="text-muted-foreground">配置摘要</dt><dd className="whitespace-pre-wrap break-words text-xs">{textValue(source.config)}</dd></div></dl></article>)} />
}

function JobTable({ payload, actionLoading, onRun }: { payload: LearningListPayload<LearningJobItem>; actionLoading: string; onRun: (item: LearningJobItem) => void }) {
  if (!payload.items.length) return <EmptyState>当前 Bot 暂无任务。</EmptyState>
  return <ResponsiveTable label="学习任务清单" table={<Table><TableHeader><TableRow><TableHead>任务</TableHead><TableHead>来源</TableHead><TableHead>候选类型</TableHead><TableHead>状态</TableHead><TableHead>调度 / 策略</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{payload.items.map((job) => <TableRow key={job.id}><TableCell><div className="font-medium">{textValue(job.name)}</div><div className="font-mono text-xs text-muted-foreground">job #{job.id}</div></TableCell><TableCell className="font-mono text-xs">{textValue(job.source_id)}</TableCell><TableCell><Badge variant="outline">{candidateTypeLabel(job.candidate_type)}</Badge></TableCell><TableCell><StatusBadge status={job.enabled === false ? 'rejected' : 'approved'} /></TableCell><TableCell className="max-w-72 text-xs"><div className="truncate" title={textValue(job.schedule)}>调度：{textValue(job.schedule)}</div><div className="truncate text-muted-foreground" title={textValue(job.policy)}>策略：{textValue(job.policy)}</div></TableCell><TableCell className="text-right"><Button size="sm" variant="outline" disabled={Boolean(actionLoading) || job.enabled === false} onClick={() => onRun(job)}>{actionLoading === `job:${job.id}` ? <Loader2Icon className="animate-spin" /> : <PlayIcon data-icon="inline-start" />}运行</Button></TableCell></TableRow>)}</TableBody></Table>} cards={payload.items.map((job) => <article key={job.id} className="flex flex-col gap-3 rounded-lg border bg-card p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><p className="font-medium">{textValue(job.name)}</p><p className="font-mono text-xs text-muted-foreground">job #{job.id}</p></div><StatusBadge status={job.enabled === false ? 'rejected' : 'approved'} /></div><dl className="grid gap-2 text-sm sm:grid-cols-2"><div><dt className="text-muted-foreground">来源</dt><dd className="break-all font-mono text-xs">{textValue(job.source_id)}</dd></div><div><dt className="text-muted-foreground">候选类型</dt><dd><Badge variant="outline">{candidateTypeLabel(job.candidate_type)}</Badge></dd></div><div><dt className="text-muted-foreground">调度</dt><dd className="break-words text-xs">{textValue(job.schedule)}</dd></div><div><dt className="text-muted-foreground">策略</dt><dd className="break-words text-xs">{textValue(job.policy)}</dd></div></dl><Button type="button" className="w-fit" size="sm" variant="outline" disabled={Boolean(actionLoading) || job.enabled === false} onClick={() => onRun(job)}>{actionLoading === `job:${job.id}` ? <Loader2Icon className="animate-spin" /> : <PlayIcon data-icon="inline-start" />}运行</Button></article>)} />
}

function CandidateFilters({ candidateType, reviewStatus, promotionStatus, source, onChange }: { candidateType: string; reviewStatus: string; promotionStatus: string; source: string; onChange: (values: Record<string, string | null>) => void }) {
  return <FieldGroup className="grid gap-3 md:grid-cols-2 xl:grid-cols-4"><Field><FieldLabel>候选类型</FieldLabel><Select value={candidateType || 'all'} onValueChange={(value) => onChange({ candidate_type: value === 'all' ? null : value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{candidateTypeOptions.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></Field><Field><FieldLabel>审核状态</FieldLabel><Select value={reviewStatus || 'all'} onValueChange={(value) => onChange({ review_status: value === 'all' ? null : value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{reviewStatusOptions.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></Field><Field><FieldLabel>晋升状态</FieldLabel><Select value={promotionStatus || 'all'} onValueChange={(value) => onChange({ promotion_status: value === 'all' ? null : value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{promotionStatusOptions.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></Field><Field><FieldLabel htmlFor="learning-source-filter">来源名称 / 类型</FieldLabel><Input id="learning-source-filter" value={source} onChange={(event) => onChange({ source: event.target.value || null })} placeholder="按 API source 筛选" /></Field></FieldGroup>
}

function CandidateTable({ payload, actionLoading, onOpen, onReview }: { payload: LearningListPayload<LearningCandidateItem>; actionLoading: string; onOpen: (item: LearningCandidateItem) => void; onReview: (item: LearningCandidateItem, action: 'approve' | 'reject') => void }) {
  if (!payload.items.length) return <EmptyState>暂无符合筛选条件的候选。</EmptyState>
  return <ResponsiveTable label="学习候选清单" table={<Table><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>类型 / 内容</TableHead><TableHead>来源 / 任务</TableHead><TableHead>审核</TableHead><TableHead>晋升</TableHead><TableHead>质量边界</TableHead><TableHead>目标</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{payload.items.map((candidate) => {
    const blocked = Boolean(candidate.promotion_blocked || candidate.quarantined || candidate.garbled)
    const pending = candidate.review_status === 'pending'
    return <TableRow key={candidate.id} className="cursor-pointer" onClick={() => onOpen(candidate)}><TableCell className="font-mono text-xs">#{candidate.id}</TableCell><TableCell className="max-w-80"><Badge variant="outline">{candidateTypeLabel(candidate.candidate_type)}</Badge><p className="mt-1 line-clamp-2 whitespace-pre-wrap">{textValue(candidate.content, textValue(candidate.reason))}</p></TableCell><TableCell className="text-xs"><div>{candidate.source ? textValue(candidate.source.name ?? candidate.source.source_type) : `source #${textValue(candidate.source_id)}`}</div><div className="text-muted-foreground">{candidate.task ? textValue(candidate.task.name) : `job #${textValue(candidate.job_id)}`}</div></TableCell><TableCell><StatusBadge status={candidate.review_status} /></TableCell><TableCell><StatusBadge status={candidate.promotion_status} /></TableCell><TableCell>{blocked ? <div className="flex flex-wrap gap-1">{candidate.promotion_block_reasons?.map((reason) => <Badge key={reason} variant="destructive">{reason}</Badge>) ?? <Badge variant="destructive">只读</Badge>}</div> : <Badge variant="outline">可审核</Badge>}</TableCell><TableCell className="font-mono text-xs">{candidate.target_ids?.length ? candidate.target_ids.join(', ') : '—'}</TableCell><TableCell className="text-right" onClick={(event) => event.stopPropagation()}>{pending ? <div className="flex justify-end gap-1"><Button type="button" size="xs" disabled={Boolean(actionLoading) || blocked} onClick={() => onReview(candidate, 'approve')}>批准</Button><Button type="button" size="xs" variant="destructive" disabled={Boolean(actionLoading)} onClick={() => onReview(candidate, 'reject')}>拒绝</Button></div> : <Button type="button" size="xs" variant="ghost" onClick={() => onOpen(candidate)}>详情</Button>}</TableCell></TableRow>
  })}</TableBody></Table>} cards={payload.items.map((candidate) => {
    const blocked = Boolean(candidate.promotion_blocked || candidate.quarantined || candidate.garbled)
    const pending = candidate.review_status === 'pending'
    return <article key={candidate.id} className="flex flex-col gap-3 rounded-lg border bg-card p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><p className="font-mono text-xs text-muted-foreground">候选 #{candidate.id}</p><Badge variant="outline">{candidateTypeLabel(candidate.candidate_type)}</Badge></div><div className="flex flex-wrap gap-1"><StatusBadge status={candidate.review_status} /><StatusBadge status={candidate.promotion_status} /></div></div><p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{textValue(candidate.content, textValue(candidate.reason))}</p><dl className="grid gap-2 text-xs sm:grid-cols-2"><div><dt className="text-muted-foreground">来源 / 任务</dt><dd className="break-words">{candidate.source ? textValue(candidate.source.name ?? candidate.source.source_type) : `source #${textValue(candidate.source_id)}`}<span className="block text-muted-foreground">{candidate.task ? textValue(candidate.task.name) : `job #${textValue(candidate.job_id)}`}</span></dd></div><div><dt className="text-muted-foreground">质量边界</dt><dd>{blocked ? <div className="flex flex-wrap gap-1">{candidate.promotion_block_reasons?.map((reason) => <Badge key={reason} variant="destructive">{reason}</Badge>) ?? <Badge variant="destructive">只读</Badge>}</div> : <Badge variant="outline">可审核</Badge>}</dd></div><div><dt className="text-muted-foreground">目标</dt><dd className="break-all font-mono">{candidate.target_ids?.length ? candidate.target_ids.join(', ') : '—'}</dd></div></dl><div className="flex flex-wrap justify-end gap-2">{pending ? <><Button type="button" size="sm" disabled={Boolean(actionLoading) || blocked} onClick={() => onReview(candidate, 'approve')}>批准</Button><Button type="button" size="sm" variant="destructive" disabled={Boolean(actionLoading)} onClick={() => onReview(candidate, 'reject')}>拒绝</Button></> : <Button type="button" size="sm" variant="outline" onClick={() => onOpen(candidate)}>查看详情</Button>}</div></article>
  })} />
}

function CandidateCards({ payload, onOpen }: { payload: LearningListPayload<LearningCandidateItem>; onOpen: (item: LearningCandidateItem) => void }) {
  if (!payload.items.length) return <EmptyState>暂无 FewShot 学习过程记录。</EmptyState>
  return <div className="grid gap-3 lg:grid-cols-2">{payload.items.map((candidate) => <Card key={candidate.id} className="bg-muted/10"><CardHeader className="pb-2"><div className="flex flex-wrap items-center justify-between gap-2"><CardTitle className="text-sm">FewShot 候选 #{candidate.id}</CardTitle><div className="flex gap-1"><StatusBadge status={candidate.review_status} /><StatusBadge status={candidate.promotion_status} /></div></div><CardDescription>仅展示 few_shot_style 的候选、审核与晋升过程，不冒充正式样例。</CardDescription></CardHeader><CardContent className="flex flex-col gap-3"><p className="whitespace-pre-wrap text-sm">{textValue(candidate.content)}</p><Button className="w-fit" size="sm" variant="outline" onClick={() => onOpen(candidate)}>查看结构化详情</Button></CardContent></Card>)}</div>
}

function ExperienceItem({ item, label }: { item: Record<string, unknown>; label: string }) {
  return <Card className="bg-muted/10"><CardHeader className="pb-2"><div className="flex items-center justify-between gap-2"><CardTitle className="text-sm">{label}</CardTitle><Badge variant="outline">{textValue(item.id ?? item.created_at)}</Badge></div></CardHeader><CardContent><p className="whitespace-pre-wrap text-sm leading-relaxed">{textValue(item.content ?? item.summary ?? item.event_summary ?? item.description)}</p><dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2"><div><dt className="text-muted-foreground">时间</dt><dd>{formatTime(item.created_at ?? item.timestamp ?? item.updated_at)}</dd></div>{item.source !== undefined ? <div><dt className="text-muted-foreground">来源</dt><dd>{textValue(item.source)}</dd></div> : null}</dl></CardContent></Card>
}

function BookExperienceCard({ item }: { item: LearningCandidateItem }) {
  const evidence = asRecord(item.evidence) ?? {}
  const fields: Array<[string, string]> = [['语料库 / 书版本', 'corpus'], ['章节', 'chapter_reference'], ['原文', 'original_quote'], ['参与者', 'participants'], ['目标角色', 'target_role'], ['知情视角', 'knowledge_perspective']]
  return <Card className="bg-muted/10"><CardHeader className="pb-2"><div className="flex items-center justify-between gap-2"><CardTitle className="text-sm">书中经历候选 #{item.id}</CardTitle><StatusBadge status={item.review_status} /></div></CardHeader><CardContent className="flex flex-col gap-3"><p className="whitespace-pre-wrap text-sm">{textValue(item.content)}</p><DetailGrid value={evidence} keys={fields} /></CardContent></Card>
}

function ExperiencesPanel({ payload }: { payload: LearningExperiencesPayload }) {
  const worldview = payload.worldview_internalization ?? []
  const book = payload.book_experience_episodes ?? []
  const interaction = payload.interaction_experiences ?? []
  return <div className="flex flex-col gap-5"><Card className="border-amber-500/20 bg-amber-500/5"><CardHeader><CardTitle>经历 / 内化</CardTitle><CardDescription>只展示已经形成正式 Scope 投影的世界观内化、书中经历与互动经历。</CardDescription></CardHeader></Card><Card><CardHeader><CardTitle>世界观内化</CardTitle><CardDescription>候选类型 worldview_internalization；非亲历。</CardDescription></CardHeader><CardContent className="flex flex-col gap-3"><Alert><AlertTriangleIcon /><AlertTitle>非亲历</AlertTitle><AlertDescription>这些条目是世界观或书设知识的内化，不表示 Bot 在书中亲身经历过。</AlertDescription></Alert>{worldview.length ? <div className="grid gap-3 lg:grid-cols-2">{worldview.map((item) => <ExperienceItem key={item.id} item={item} label="世界观内化（非亲历）" />)}</div> : <EmptyState>暂无世界观内化。</EmptyState>}</CardContent></Card><Card><CardHeader><CardTitle>书中经历</CardTitle><CardDescription>结构化保留 corpus、章节、原文、参与者、目标角色与视角证据；缺失字段不会在前端补造。</CardDescription></CardHeader><CardContent>{book.length ? <div className="grid gap-3 lg:grid-cols-2">{book.map((item) => <BookExperienceCard key={item.id} item={item} />)}</div> : <EmptyState>暂无书中经历。</EmptyState>}</CardContent></Card><Card><CardHeader><CardTitle>互动经历</CardTitle><CardDescription>仅展示真实会话投影返回的互动锚点。</CardDescription></CardHeader><CardContent className="flex flex-col gap-3">{interaction.length ? interaction.map((item, index) => <ExperienceItem key={String(item.id ?? index)} item={item} label="互动经历" />) : <EmptyState>暂无互动经历。</EmptyState>}</CardContent></Card></div>
}

function PromotionsTable({ payload, actionLoading, onRetry }: { payload: LearningListPayload<LearningPromotionItem>; actionLoading: string; onRetry: (item: LearningPromotionItem) => void }) {
  if (!payload.items.length) return <EmptyState>暂无晋升记录。</EmptyState>
  return <ResponsiveTable label="学习晋升记录清单" table={<Table><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>候选 / 目标</TableHead><TableHead>状态</TableHead><TableHead>Target ID</TableHead><TableHead>错误详情</TableHead><TableHead>正式对象</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{payload.items.map((promotion) => {
    const retryable = promotion.promotion_status === 'retryable_failed' && promotion.retryable === true
    return <TableRow key={promotion.id}><TableCell className="font-mono text-xs">#{promotion.id}</TableCell><TableCell><div>{candidateTypeLabel(promotion.candidate_type)}</div><div className="text-xs text-muted-foreground">candidate #{textValue(promotion.candidate_id)} · {textValue(promotion.target_kind)}</div></TableCell><TableCell><StatusBadge status={promotion.promotion_status} /></TableCell><TableCell className="font-mono text-xs">{textValue(promotion.target_id)}</TableCell><TableCell className="max-w-72 text-xs text-destructive">{promotion.error_code || promotion.error_message ? `${textValue(promotion.error_code)} · ${textValue(promotion.error_message)}` : '—'}</TableCell><TableCell>{promotion.target_link ? <ObjectDeepLink to={promotion.target_link.path} objectRef={promotion.target_link.object_ref}>打开正式对象</ObjectDeepLink> : <span className="text-xs text-muted-foreground">无服务端 ObjectRef</span>}</TableCell><TableCell className="text-right">{retryable ? <Button type="button" size="sm" variant="outline" disabled={Boolean(actionLoading)} onClick={() => onRetry(promotion)}>{actionLoading === `promotion:${promotion.id}` ? <Loader2Icon className="animate-spin" /> : <RotateCcwIcon data-icon="inline-start" />}安全重试</Button> : '—'}</TableCell></TableRow>
  })}</TableBody></Table>} cards={payload.items.map((promotion) => {
    const retryable = promotion.promotion_status === 'retryable_failed' && promotion.retryable === true
    return <article key={promotion.id} className="flex flex-col gap-3 rounded-lg border bg-card p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><p className="font-mono text-xs text-muted-foreground">晋升 #{promotion.id}</p><p className="font-medium">{candidateTypeLabel(promotion.candidate_type)}</p><p className="text-xs text-muted-foreground">candidate #{textValue(promotion.candidate_id)} · {textValue(promotion.target_kind)}</p></div><StatusBadge status={promotion.promotion_status} /></div><dl className="grid gap-2 text-sm sm:grid-cols-2"><div><dt className="text-muted-foreground">Target ID</dt><dd className="break-all font-mono text-xs">{textValue(promotion.target_id)}</dd></div><div><dt className="text-muted-foreground">错误详情</dt><dd className="whitespace-pre-wrap break-words text-xs text-destructive">{promotion.error_code || promotion.error_message ? `${textValue(promotion.error_code)} · ${textValue(promotion.error_message)}` : '—'}</dd></div><div><dt className="text-muted-foreground">正式对象</dt><dd>{promotion.target_link ? <ObjectDeepLink to={promotion.target_link.path} objectRef={promotion.target_link.object_ref}>打开正式对象</ObjectDeepLink> : <span className="text-xs text-muted-foreground">无服务端 ObjectRef</span>}</dd></div></dl>{retryable ? <Button type="button" className="w-fit" size="sm" variant="outline" disabled={Boolean(actionLoading)} onClick={() => onRetry(promotion)}>{actionLoading === `promotion:${promotion.id}` ? <Loader2Icon className="animate-spin" /> : <RotateCcwIcon data-icon="inline-start" />}安全重试</Button> : null}</article>
  })} />
}

function CandidateDetail({ candidate, dedicated, dedicatedError, actionLoading, onReview, onRetry }: { candidate: LearningCandidateItem; dedicated: DedicatedReviewStatus | null; dedicatedError: string; actionLoading: string; onReview: (action: 'approve' | 'reject' | 'ignore') => void; onRetry: (item: LearningPromotionItem) => void }) {
  const blocked = Boolean(candidate.promotion_blocked || candidate.quarantined || candidate.garbled)
  const pending = candidate.review_status === 'pending'
  const dedicatedType = candidate.candidate_type === 'jargon_candidate' || candidate.candidate_type === 'belief_candidate'
  const retryable = (candidate.promotions ?? []).filter((item) => item.promotion_status === 'retryable_failed' && item.retryable === true)
  return <div className="flex flex-col gap-4 overflow-auto"><div className="flex flex-wrap items-center gap-2"><Badge variant="outline">#{candidate.id}</Badge><Badge variant="outline">{candidateTypeLabel(candidate.candidate_type)}</Badge><StatusBadge status={candidate.review_status} /><StatusBadge status={candidate.promotion_status} />{blocked ? <Badge variant="destructive">只读 / 不可晋升</Badge> : null}</div><p className="whitespace-pre-wrap rounded-lg border bg-muted/20 p-3 text-sm">{textValue(candidate.content, textValue(candidate.reason))}</p><DetailGrid value={{ bot_id: candidate.bot_id, source_fingerprint: candidate.source_fingerprint, source_id: candidate.source_id, job_id: candidate.job_id, reviewer: candidate.reviewer, reviewed_at: formatTime(candidate.reviewed_at), review_note: candidate.review_note, target_ids: candidate.target_ids }} keys={[["Bot ID", 'bot_id'], ['来源 fingerprint', 'source_fingerprint'], ['来源 ID', 'source_id'], ['任务 ID', 'job_id'], ['审核者', 'reviewer'], ['审核时间', 'reviewed_at'], ['审核备注', 'review_note'], ['目标 ID', 'target_ids']]} /><Card><CardHeader className="pb-2"><CardTitle className="text-sm">证据</CardTitle><CardDescription>只读结构化展示 API 返回证据，不补造章节、原文或参与者。</CardDescription></CardHeader><CardContent><DetailGrid value={candidate.evidence} /></CardContent></Card>{candidate.source ? <Card><CardHeader className="pb-2"><CardTitle className="text-sm">来源</CardTitle></CardHeader><CardContent><DetailGrid value={candidate.source} keys={[["名称", 'name'], ['类型', 'source_type'], ['状态', 'enabled'], ['游标', 'cursor']]} /></CardContent></Card> : null}{candidate.task ? <Card><CardHeader className="pb-2"><CardTitle className="text-sm">来源任务</CardTitle></CardHeader><CardContent><DetailGrid value={candidate.task} keys={[["名称", 'name'], ['候选类型', 'candidate_type'], ['启用', 'enabled'], ['调度', 'schedule'], ['策略', 'policy']]} /></CardContent></Card> : null}{candidate.candidate_type === 'worldview_internalization' ? <Alert><AlertTriangleIcon /><AlertTitle>非亲历</AlertTitle><AlertDescription>世界观内化仅代表知识内化，不代表真实经历。</AlertDescription></Alert> : null}{blocked ? <Alert variant="destructive"><AlertTitle>不可晋升</AlertTitle><AlertDescription>{candidate.promotion_block_reasons?.join('、') || '服务端标记为不可晋升、隔离或乱码。批准操作已禁用。'}</AlertDescription></Alert> : null}{dedicatedType ? <Card><CardHeader className="pb-2"><CardTitle className="text-sm">专属审核</CardTitle><CardDescription>术语与信念候选委派给既有专属审核；学习中心不会绕过专属服务直接生效。</CardDescription></CardHeader><CardContent>{dedicatedError ? <Alert variant="destructive"><AlertTitle>专属审核状态不可用</AlertTitle><AlertDescription>{dedicatedError}</AlertDescription></Alert> : dedicated ? <div className="flex flex-col gap-3"><div className="flex flex-wrap items-center gap-2"><StatusBadge status={dedicated.status} /><span className="font-mono text-xs">target {textValue(dedicated.target_id)}</span></div>{dedicated.deep_link && dedicated.object_ref ? <ObjectDeepLink to={dedicated.deep_link} objectRef={dedicated.object_ref}>打开专属审核对象</ObjectDeepLink> : <span className="text-sm text-muted-foreground">无服务端签发的 ObjectRef，不使用裸 ID 跳转。</span>}{dedicated.error ? <p className="text-sm text-destructive">{dedicated.error}</p> : null}</div> : <p className="text-sm text-muted-foreground">正在读取专属审核状态…</p>}</CardContent></Card> : null}<Card><CardHeader className="pb-2"><CardTitle className="text-sm">操作历史与晋升</CardTitle></CardHeader><CardContent className="flex flex-col gap-2">{candidate.operations?.length ? candidate.operations.map((operation, index) => <div key={`${String(operation.id ?? operation.kind)}-${index}`} className="rounded-md border p-2 text-xs"><div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{textValue(operation.kind)}</Badge><StatusBadge status={operation.status} />{operation.target_id !== undefined ? <span className="font-mono">target {textValue(operation.target_id)}</span> : null}</div>{operation.error_message ? <p className="mt-1 text-destructive">{textValue(operation.error_code)} · {textValue(operation.error_message)}</p> : null}</div>) : <p className="text-sm text-muted-foreground">暂无操作历史。</p>}{candidate.promotions?.map((promotion) => promotion.target_link ? <ObjectDeepLink key={promotion.id} to={promotion.target_link.path} objectRef={promotion.target_link.object_ref}>打开晋升对象 #{promotion.id}</ObjectDeepLink> : null)}</CardContent></Card>{candidate.failures?.length ? <Alert variant="destructive"><AlertTitle>晋升错误详情</AlertTitle><AlertDescription><ul className="list-disc pl-5">{candidate.failures.map((failure, index) => <li key={failure.id ?? index}>{textValue(failure.code)} · {textValue(failure.message)}{failure.retryable ? '（可重试）' : '（不可重试）'}</li>)}</ul></AlertDescription></Alert> : null}<div className="flex flex-wrap justify-end gap-2 border-t pt-3">{pending ? <><Button disabled={Boolean(actionLoading) || blocked} onClick={() => onReview('approve')}><CheckCircle2Icon data-icon="inline-start" />批准</Button><Button variant="destructive" disabled={Boolean(actionLoading)} onClick={() => onReview('reject')}>拒绝</Button><Button variant="secondary" disabled={Boolean(actionLoading)} onClick={() => onReview('ignore')}>忽略</Button></> : null}{retryable.map((promotion) => <Button key={promotion.id} variant="outline" disabled={Boolean(actionLoading)} onClick={() => onRetry(promotion)}><RotateCcwIcon data-icon="inline-start" />安全重试 #{promotion.id}</Button>)}</div></div>
}

export function LearningCenterPage() {
  const pagination = usePaginationSearchParams()
  const searchParams = pagination.searchParams
  const botId = searchParams.get('bot_id') ?? ''
  const rawTab = searchParams.get('tab') ?? 'sources'
  const tab: LearningTab = learningTabs.includes(rawTab as LearningTab) ? rawTab as LearningTab : 'sources'
  const candidateType = searchParams.get('candidate_type') ?? ''
  const reviewStatus = searchParams.get('review_status') ?? ''
  const promotionStatus = searchParams.get('promotion_status') ?? ''
  const sourceFilter = searchParams.get('source') ?? ''

  const [sources, setSources] = useState<AsyncState<LearningListPayload<LearningSourceItem>>>(loadingState)
  const [jobs, setJobs] = useState<AsyncState<LearningListPayload<LearningJobItem>>>(loadingState)
  const [candidates, setCandidates] = useState<AsyncState<LearningListPayload<LearningCandidateItem>>>(loadingState)
  const [fewShot, setFewShot] = useState<AsyncState<LearningListPayload<LearningCandidateItem>>>(loadingState)
  const [experiences, setExperiences] = useState<AsyncState<LearningExperiencesPayload>>(loadingState)
  const [promotions, setPromotions] = useState<AsyncState<LearningListPayload<LearningPromotionItem>>>(loadingState)
  const [selectedCandidate, setSelectedCandidate] = useState<LearningCandidateItem | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [detailCandidateId, setDetailCandidateId] = useState<number | null>(null)
  const [dedicated, setDedicated] = useState<DedicatedReviewStatus | null>(null)
  const [dedicatedError, setDedicatedError] = useState('')
  const [actionLoading, setActionLoading] = useState('')
  const requestRef = useRef<{ generation: number; controller: AbortController } | null>(null)
  const detailRequestRef = useRef<AbortController | null>(null)

  const loadBots = useCallback(async () => scopeOptionsFor(await getScopeOptions(), ['bot']), [])
  const baseQuery = useMemo(() => ({ bot_id: botId, limit: pagination.limit, offset: pagination.offset }), [botId, pagination.limit, pagination.offset])
  const candidateQueryFilters = useMemo(() => ({
    ...(candidateType ? { candidate_type: candidateType } : {}),
    ...(reviewStatus ? { review_status: reviewStatus } : {}),
    ...(promotionStatus ? { promotion_status: promotionStatus } : {}),
    ...(sourceFilter.trim() ? { source: sourceFilter.trim() } : {}),
  }), [candidateType, promotionStatus, reviewStatus, sourceFilter])
  const promotionQueryFilters = useMemo(() => promotionStatus ? { promotion_status: promotionStatus } : {}, [promotionStatus])
  const activeQueryFilters = tab === 'candidates' ? candidateQueryFilters : tab === 'promotions' ? promotionQueryFilters : null

  useEffect(() => {
    if (rawTab !== tab) pagination.setFilters({ tab }, true)
  }, [pagination, rawTab, tab])

  const loadCurrent = useCallback(async () => {
    requestRef.current?.controller.abort()
    const controller = new AbortController()
    const request = { generation: (requestRef.current?.generation ?? 0) + 1, controller }
    requestRef.current = request
    const isCurrent = () => requestRef.current === request && !controller.signal.aborted

    if (!botId) {
      const empty = { status: 'empty', data: null } as const
      if (tab === 'sources') setSources(empty)
      else if (tab === 'jobs') setJobs(empty)
      else if (tab === 'candidates') setCandidates(empty)
      else if (tab === 'fewshot') setFewShot(empty)
      else if (tab === 'experiences') setExperiences(empty)
      else setPromotions(empty)
      return
    }

    const run = async <T,>(setter: React.Dispatch<React.SetStateAction<AsyncState<T>>>, requestFn: () => Promise<T>, hasItems: (value: T) => boolean) => {
      setter(loadingState())
      try {
        const value = await requestFn()
        if (isCurrent()) setter({ status: hasItems(value) ? 'success' : 'empty', data: value })
      } catch (error) {
        if (isCurrent() && !isRequestCancelled(error)) setter({ status: 'error', data: null, error })
      }
    }

    if (tab === 'sources') await run(setSources, () => listLearningSources(baseQuery, controller.signal), (value) => value.items.length > 0)
    else if (tab === 'jobs') await run(setJobs, () => listLearningJobs(baseQuery, controller.signal), (value) => value.items.length > 0)
    else if (tab === 'candidates') await run(setCandidates, () => listLearningCandidates({ ...baseQuery, ...activeQueryFilters }, controller.signal), (value) => value.items.length > 0)
    else if (tab === 'fewshot') await run(setFewShot, () => getLearningFewShot({ ...baseQuery, candidate_type: 'few_shot_style' }, controller.signal), (value) => value.items.length > 0)
    else if (tab === 'experiences') await run(setExperiences, () => getLearningExperiences(baseQuery, controller.signal), (value) => Boolean(value.worldview_internalization?.length || value.book_experience_episodes?.length || value.interaction_experiences?.length))
    else await run(setPromotions, () => listLearningPromotions({ ...baseQuery, ...activeQueryFilters }, controller.signal), (value) => value.items.length > 0)
  }, [activeQueryFilters, baseQuery, botId, tab])

  useEffect(() => {
    void loadCurrent()
    return () => requestRef.current?.controller.abort()
  }, [loadCurrent])
  useEffect(() => () => detailRequestRef.current?.abort(), [])

  async function openCandidate(candidate: LearningCandidateItem) {
    detailRequestRef.current?.abort()
    const controller = new AbortController()
    detailRequestRef.current = controller
    setDetailCandidateId(candidate.id)
    setSelectedCandidate(null)
    setDetailLoading(true)
    setDetailError('')
    setDedicated(null)
    setDedicatedError('')
    try {
      const detail = await getLearningCandidate(candidate.id, botId, controller.signal)
      if (controller.signal.aborted || detailRequestRef.current !== controller) return
      setSelectedCandidate(detail.item)
      if (detail.item.candidate_type === 'jargon_candidate' || detail.item.candidate_type === 'belief_candidate') {
        try { setDedicated((await getDedicatedReviewStatus(candidate.id, botId, controller.signal)).item) }
        catch (reason) {
          if (!isRequestCancelled(reason) && detailRequestRef.current === controller) setDedicatedError(reason instanceof Error ? reason.message : '专属审核状态读取失败')
        }
      }
    } catch (reason) {
      if (!isRequestCancelled(reason) && detailRequestRef.current === controller) setDetailError(reason instanceof Error ? reason.message : '候选详情读取失败')
    } finally {
      if (detailRequestRef.current === controller) setDetailLoading(false)
    }
  }

  function closeCandidate() {
    detailRequestRef.current?.abort()
    setDetailCandidateId(null)
    setSelectedCandidate(null)
    setDetailError('')
    setDedicated(null)
    setDedicatedError('')
  }

  function retryCandidateDetail() {
    if (detailCandidateId === null) return
    void openCandidate({ id: detailCandidateId, bot_id: botId })
  }

  async function review(candidate: LearningCandidateItem, action: 'approve' | 'reject' | 'ignore') {
    setActionLoading(`candidate:${candidate.id}`)
    try {
      const result = await reviewLearningCandidate(candidate.id, botId, action, { idempotencyKey: `learning-review:${botId}:${candidate.id}:${action}` })
      if (!result.ok || !['committed', 'succeeded'].includes(result.operation.status)) throw new Error(`服务端未确认审核提交：${result.operation.status}`)
      toast.success(`审核操作：${result.operation.status}；后续晋升以 API 状态确认结果`)
      const nextCandidateValue = result.item && 'candidate' in result.item ? result.item.candidate : undefined
      const nextCandidate = asRecord(nextCandidateValue) ? nextCandidateValue as LearningCandidateItem : undefined
      if (nextCandidate) setSelectedCandidate(nextCandidate)
      else if (selectedCandidate?.id === candidate.id) setSelectedCandidate(null)
      await loadCurrent()
    } catch (reason) { toast.error(reason instanceof Error ? reason.message : '审核失败') }
    finally { setActionLoading('') }
  }

  async function runJob(item: LearningJobItem) {
    setActionLoading(`job:${item.id}`)
    try {
      const result = await runLearningJob(item.id, botId, `learning-job:${botId}:${item.id}`)
      if (!result.ok || !['queued', 'running', 'succeeded', 'committed'].includes(result.operation.status)) throw new Error(`任务未被服务端接受：${result.operation.status}`)
      toast.success(`任务 API 状态：${result.operation.status}`)
      await loadCurrent()
    } catch (reason) { toast.error(reason instanceof Error ? reason.message : '任务启动失败') }
    finally { setActionLoading('') }
  }

  async function retry(item: LearningPromotionItem) {
    setActionLoading(`promotion:${item.id}`)
    try {
      const result = await retryLearningPromotion(item.id, botId, `learning-promotion:${botId}:${item.id}`)
      if (!result.ok) throw new Error('晋升重试未被服务端接受')
      toast.success(`晋升操作：${result.operation.status}`)
      await loadCurrent()
      if (selectedCandidate) await openCandidate(selectedCandidate)
    } catch (reason) { toast.error(reason instanceof Error ? reason.message : '晋升重试失败') }
    finally { setActionLoading('') }
  }

  const pageForTab = tab === 'sources' ? sources.data?.page : tab === 'jobs' ? jobs.data?.page : tab === 'candidates' ? candidates.data?.page : tab === 'fewshot' ? fewShot.data?.page : tab === 'promotions' ? promotions.data?.page : null
  const botPrompt = !botId ? '请选择真实 Bot' : undefined

  return <div data-slot="learning-center-page" className="flex flex-col gap-6"><div className="flex flex-wrap items-start justify-between gap-4"><div><h1 className="text-xl font-bold tracking-tight">通用学习中心</h1><p className="text-sm text-muted-foreground">统一查看学习统计、来源任务、候选审核、FewShot 过程、经历内化与晋升历史。</p></div><div className="flex w-full min-w-0 items-end gap-2 sm:w-auto sm:min-w-72"><ScopeSelect className="flex-1" value={botId || undefined} loadOptions={loadBots} label="Bot" placeholder="选择真实 BotProfile.db_id" onValueChange={(value) => pagination.setFilters({ bot_id: value })} /><Button type="button" size="icon-sm" variant="outline" disabled={!botId} onClick={() => void loadCurrent()} title="刷新学习中心"><RefreshCwIcon className={sources.status === 'loading' ? 'animate-spin' : ''} /></Button></div></div><div className="grid gap-3 md:grid-cols-4"><SummaryCard title="来源" value={totalText(sources.data)} /><SummaryCard title="任务" value={totalText(jobs.data)} /><SummaryCard title="候选" value={totalText(candidates.data)} description="当前候选筛选" /><SummaryCard title="晋升历史" value={totalText(promotions.data)} description="当前 Bot 作用域" /></div><Tabs value={tab} onValueChange={(value) => pagination.setFilters({ tab: value })}><TabsList className="grid h-auto w-full grid-cols-2 gap-1 sm:grid-cols-3 lg:grid-cols-6"><TabsTrigger value="sources">来源</TabsTrigger><TabsTrigger value="jobs">任务</TabsTrigger><TabsTrigger value="candidates">候选</TabsTrigger><TabsTrigger value="fewshot">FewShot</TabsTrigger><TabsTrigger value="experiences">经历 / 内化</TabsTrigger><TabsTrigger value="promotions">晋升</TabsTrigger></TabsList><TabsContent value="sources"><Card><CardHeader><CardTitle>学习来源</CardTitle><CardDescription>按 BotProfile.db_id 展示真实输入来源与游标；不以 QQ 号或默认 Bot 代替作用域。</CardDescription></CardHeader><CardContent><QueryState status={sources.status} error={sources.error} title={botPrompt} onRetry={() => void loadCurrent()}>{sources.data ? <SourceTable payload={sources.data} /> : null}</QueryState></CardContent></Card></TabsContent><TabsContent value="jobs"><Card><CardHeader><CardTitle>来源任务</CardTitle><CardDescription>任务状态来自 API；运行操作只调用幂等任务端点，不提前显示成功。</CardDescription></CardHeader><CardContent><QueryState status={jobs.status} error={jobs.error} title={botPrompt} onRetry={() => void loadCurrent()}>{jobs.data ? <JobTable payload={jobs.data} actionLoading={actionLoading} onRun={(item) => void runJob(item)} /> : null}</QueryState></CardContent></Card></TabsContent><TabsContent value="candidates"><Card><CardHeader><CardTitle>候选</CardTitle><CardDescription>筛选写入 URL；点击行读取当前 Bot 下的结构化详情、证据、操作和晋升状态。</CardDescription></CardHeader><CardContent className="flex flex-col gap-4"><CandidateFilters candidateType={candidateType} reviewStatus={reviewStatus} promotionStatus={promotionStatus} source={sourceFilter} onChange={(values) => pagination.setFilters(values)} /><QueryState status={candidates.status} error={candidates.error} title={botPrompt} onRetry={() => void loadCurrent()}>{candidates.data ? <CandidateTable payload={candidates.data} actionLoading={actionLoading} onOpen={(item) => void openCandidate(item)} onReview={(item, action) => void review(item, action)} /> : null}</QueryState></CardContent></Card></TabsContent><TabsContent value="fewshot"><Card><CardHeader><CardTitle>FewShot 学习过程</CardTitle><CardDescription>只展示 few_shot_style 候选、审核与晋升过程；正式对象通过服务端 ObjectRef 进入管理页。</CardDescription></CardHeader><CardContent><QueryState status={fewShot.status} error={fewShot.error} title={botPrompt ?? 'FewShot 学习过程当前真实为空'} onRetry={() => void loadCurrent()}>{fewShot.data ? <CandidateCards payload={fewShot.data} onOpen={(item) => void openCandidate(item)} /> : null}</QueryState></CardContent></Card></TabsContent><TabsContent value="experiences"><QueryState status={experiences.status} error={experiences.error} title={botPrompt} onRetry={() => void loadCurrent()}>{experiences.data ? <ExperiencesPanel payload={experiences.data} /> : null}</QueryState></TabsContent><TabsContent value="promotions"><Card><CardHeader><CardTitle>晋升历史</CardTitle><CardDescription>显示 target、API 状态、错误和正式对象引用；仅 retryable_failed 允许幂等安全重试。</CardDescription></CardHeader><CardContent><QueryState status={promotions.status} error={promotions.error} title={botPrompt} onRetry={() => void loadCurrent()}>{promotions.data ? <PromotionsTable payload={promotions.data} actionLoading={actionLoading} onRetry={(item) => void retry(item)} /> : null}</QueryState></CardContent></Card></TabsContent></Tabs>{pageForTab ? <PaginationControls page={pageForTab} onOffsetChange={pagination.setOffset} onLimitChange={pagination.setLimit} disabled={[sources.status, jobs.status, candidates.status, fewShot.status, promotions.status].includes('loading')} /> : null}{detailCandidateId !== null ? <Dialog open onOpenChange={(open) => { if (!open) closeCandidate() }}><DialogContent className="flex max-h-[90vh] flex-col sm:max-w-3xl"><DialogHeader><DialogTitle>候选结构化详情</DialogTitle><DialogDescription>{detailLoading ? '正在读取当前 Bot 作用域下的详情…' : '证据、目标、状态、ObjectRef 和错误均来自新 API。'}</DialogDescription></DialogHeader>{detailLoading ? <div className="flex items-center justify-center py-12 text-muted-foreground"><Loader2Icon className="mr-2 animate-spin" />读取详情</div> : detailError ? <Alert variant="destructive"><AlertTriangleIcon /><AlertTitle>候选详情读取失败</AlertTitle><AlertDescription className="flex flex-col gap-3"><span>{detailError}</span><span className="flex gap-2"><Button type="button" size="sm" variant="outline" onClick={retryCandidateDetail}>重试</Button><Button type="button" size="sm" variant="ghost" onClick={closeCandidate}>关闭</Button></span></AlertDescription></Alert> : selectedCandidate ? <CandidateDetail candidate={selectedCandidate} dedicated={dedicated} dedicatedError={dedicatedError} actionLoading={actionLoading} onReview={(action) => void review(selectedCandidate, action)} onRetry={(item) => void retry(item)} /> : null}</DialogContent></Dialog> : null}</div>
}
