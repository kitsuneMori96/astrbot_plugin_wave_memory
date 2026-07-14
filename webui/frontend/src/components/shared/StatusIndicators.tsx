import { AlertCircleIcon, CheckCircle2Icon, CircleEllipsisIcon, Clock3Icon, PauseCircleIcon, XCircleIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { OperationState, QualityDecision } from './types'

type BadgeVariant = 'default' | 'secondary' | 'destructive' | 'outline'

const QUALITY: Record<QualityDecision, { label: string; variant: BadgeVariant; icon: typeof CheckCircle2Icon }> = {
  allow: { label: '质量门：允许', variant: 'default', icon: CheckCircle2Icon },
  quarantine: { label: '质量门：隔离', variant: 'secondary', icon: PauseCircleIcon },
  reject: { label: '质量门：拒绝', variant: 'destructive', icon: XCircleIcon },
  defer: { label: '质量门：延后', variant: 'outline', icon: Clock3Icon },
  unknown: { label: '质量门：未知', variant: 'outline', icon: AlertCircleIcon },
}

const OPERATIONS: Record<OperationState, { label: string; variant: BadgeVariant; icon: typeof CheckCircle2Icon }> = {
  queued: { label: '操作：排队中', variant: 'secondary', icon: Clock3Icon },
  running: { label: '操作：执行中', variant: 'secondary', icon: CircleEllipsisIcon },
  committed: { label: '操作：已提交', variant: 'default', icon: CheckCircle2Icon },
  succeeded: { label: '操作：成功', variant: 'default', icon: CheckCircle2Icon },
  rolled_back: { label: '操作：已回滚', variant: 'destructive', icon: XCircleIcon },
  failed: { label: '操作：失败', variant: 'destructive', icon: XCircleIcon },
  cancelled: { label: '操作：已取消', variant: 'outline', icon: PauseCircleIcon },
  unknown: { label: '操作：未知', variant: 'outline', icon: AlertCircleIcon },
}

export interface QualityDecisionBadgeProps {
  decision?: QualityDecision | null
  reasonCode?: string | null
  className?: string
}

export function QualityDecisionBadge({ decision, reasonCode, className }: QualityDecisionBadgeProps) {
  const config = QUALITY[decision ?? 'unknown']
  const Icon = config.icon
  return (
    <Badge variant={config.variant} className={cn('h-auto min-h-5 whitespace-normal py-1', className)} title={reasonCode ?? undefined}>
      <Icon aria-hidden="true" />
      <span>{config.label}{reasonCode ? ` · ${reasonCode}` : ''}</span>
    </Badge>
  )
}

export interface OperationStatusProps {
  status?: OperationState | null
  operationId?: string | null
  revision?: string | null
  className?: string
}

export function OperationStatus({ status, operationId, revision, className }: OperationStatusProps) {
  const config = OPERATIONS[status ?? 'unknown']
  const Icon = config.icon
  const details = [operationId ? `ID ${operationId}` : null, revision ? `revision ${revision}` : null].filter(Boolean).join(' · ')
  return (
    <span data-slot="operation-status" className={cn('inline-flex flex-wrap items-center gap-2', className)}>
      <Badge variant={config.variant} className="h-auto min-h-5 whitespace-normal py-1">
        <Icon aria-hidden="true" />
        {config.label}
      </Badge>
      {details ? <span className="font-mono text-sm text-muted-foreground">{details}</span> : null}
    </span>
  )
}
