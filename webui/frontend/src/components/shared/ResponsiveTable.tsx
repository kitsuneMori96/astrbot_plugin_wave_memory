import type { ReactNode } from 'react'

import { useIsMobile } from '@/hooks/use-mobile'
import { cn } from '@/lib/utils'

export interface ResponsiveTableProps {
  table: ReactNode
  cards: ReactNode
  label: string
  className?: string
}

/** 在窄屏用页面提供的语义卡片替代宽表，桌面保留原始表格结构。 */
export function ResponsiveTable({ table, cards, label, className }: ResponsiveTableProps) {
  const isMobile = useIsMobile()

  if (isMobile) {
    return <section aria-label={label} data-slot="responsive-table" data-responsive-table="cards" className={cn('flex min-w-0 flex-col gap-3', className)}>{cards}</section>
  }

  return <div role="region" aria-label={label} data-slot="responsive-table" data-responsive-table="table" className={cn('overflow-x-auto rounded-lg border', className)}>{table}</div>
}
