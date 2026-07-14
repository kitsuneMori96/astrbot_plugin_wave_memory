import { useState, type ReactElement, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet'
import { useIsMobile } from '@/hooks/use-mobile'
import { cn } from '@/lib/utils'

export interface ResponsiveDetailProps {
  title: string
  description: string
  children: ReactNode
  trigger?: ReactElement
  triggerLabel?: string
  open?: boolean
  onOpenChange?: (open: boolean) => void
  className?: string
}

export function ResponsiveDetail({
  title,
  description,
  children,
  trigger,
  triggerLabel = '查看详情',
  open: controlledOpen,
  onOpenChange,
  className,
}: ResponsiveDetailProps) {
  const isMobile = useIsMobile()
  const [internalOpen, setInternalOpen] = useState(false)
  const open = controlledOpen ?? internalOpen
  const setOpen = (nextOpen: boolean) => {
    if (controlledOpen === undefined) setInternalOpen(nextOpen)
    onOpenChange?.(nextOpen)
  }
  const triggerNode = trigger ?? <Button type="button" variant="outline" size="sm">{triggerLabel}</Button>

  if (isMobile) {
    return (
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger asChild>{triggerNode}</SheetTrigger>
        <SheetContent side="right" className={cn('w-[min(92vw,32rem)] sm:max-w-lg', className)} data-responsive-detail="sheet">
          <SheetHeader className="border-b pr-12">
            <SheetTitle>{title}</SheetTitle>
            <SheetDescription>{description}</SheetDescription>
          </SheetHeader>
          <ScrollArea className="min-h-0 flex-1">
            <div className="p-4 text-sm leading-relaxed">{children}</div>
          </ScrollArea>
        </SheetContent>
      </Sheet>
    )
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{triggerNode}</DialogTrigger>
      <DialogContent className={cn('max-h-[min(85vh,48rem)] sm:max-w-2xl', className)} data-responsive-detail="dialog">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <ScrollArea className="min-h-0 max-h-[65vh]">
          <div className="pr-4 text-sm leading-relaxed">{children}</div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}
