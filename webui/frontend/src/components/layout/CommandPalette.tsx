import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CornerDownLeftIcon, Loader2, SearchIcon } from 'lucide-react'

import { appRoutes } from '@/app/routes'
import { listMemories, type MemoryItem } from '@/api/memories'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'

interface CommandPaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

type Entry =
  | { kind: 'page'; path: string; title: string; description: string }
  | { kind: 'memory'; id: number; title: string; description: string }

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [memoryHits, setMemoryHits] = useState<MemoryItem[]>([])
  const [searching, setSearching] = useState(false)
  const [activeIdx, setActiveIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const pageEntries = useMemo<Entry[]>(() => {
    const q = query.trim().toLowerCase()
    return appRoutes
      .filter((r) => !r.path.includes(':'))
      .filter((r) => !q || r.title.toLowerCase().includes(q) || r.description.toLowerCase().includes(q) || r.path.includes(q))
      .map((r) => ({ kind: 'page' as const, path: r.path, title: r.title, description: r.description }))
  }, [query])

  // 记忆搜索：≥2 字符 debounce 350ms
  useEffect(() => {
    if (!open) return
    const q = query.trim()
    if (q.length < 2) {
      setMemoryHits([])
      setSearching(false)
      return
    }
    setSearching(true)
    const timer = window.setTimeout(async () => {
      try {
        const resp = await listMemories({ search: q, size: 8 })
        setMemoryHits(resp.items ?? [])
      } catch {
        setMemoryHits([])
      } finally {
        setSearching(false)
      }
    }, 350)
    return () => window.clearTimeout(timer)
  }, [query, open])

  const memoryEntries = useMemo<Entry[]>(
    () =>
      memoryHits.map((m) => ({
        kind: 'memory' as const,
        id: m.id,
        title: (m.content ?? '').slice(0, 60),
        description: `记忆 #${m.id} · ${m.source ?? ''} ${m.sender_name ?? ''}`,
      })),
    [memoryHits],
  )

  const entries = useMemo(() => [...pageEntries, ...memoryEntries], [pageEntries, memoryEntries])

  useEffect(() => {
    setActiveIdx(0)
  }, [query])

  useEffect(() => {
    if (open) {
      setQuery('')
      setMemoryHits([])
      setActiveIdx(0)
      window.setTimeout(() => inputRef.current?.focus(), 30)
    }
  }, [open])

  const activate = useCallback(
    (entry: Entry | undefined) => {
      if (!entry) return
      onOpenChange(false)
      if (entry.kind === 'page') {
        navigate(entry.path)
      } else {
        navigate(`/memories?search=${encodeURIComponent(query.trim())}&highlight=${entry.id}`)
      }
    },
    [navigate, onOpenChange, query],
  )

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIdx((i) => Math.min(i + 1, entries.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIdx((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      activate(entries[activeIdx])
    }
  }

  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [activeIdx])

  let flatIdx = -1

  const renderRow = (entry: Entry) => {
    flatIdx += 1
    const idx = flatIdx
    const active = idx === activeIdx
    return (
      <button
        key={`${entry.kind}-${entry.kind === 'page' ? entry.path : entry.id}`}
        data-active={active}
        className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm ${
          active ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/50'
        }`}
        onMouseEnter={() => setActiveIdx(idx)}
        onClick={() => activate(entry)}
      >
        {entry.kind === 'page' ? (
          <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
        ) : (
          <span className="size-4 shrink-0 rounded-sm bg-primary/15 text-center text-[10px] leading-4 text-primary">M</span>
        )}
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{entry.title || '(无内容)'}</span>
          <span className="block truncate text-xs text-muted-foreground">{entry.description}</span>
        </span>
        {active && <CornerDownLeftIcon className="size-3.5 shrink-0 text-muted-foreground" />}
      </button>
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl gap-0 p-0" showCloseButton={false}>
        <DialogHeader className="px-4 pb-2 pt-4">
          <DialogTitle className="sr-only">命令面板</DialogTitle>
          <DialogDescription className="sr-only">搜索页面与记忆</DialogDescription>
          <div className="flex items-center gap-2 border-b pb-3">
            {searching ? (
              <Loader2 className="size-4 shrink-0 animate-spin text-muted-foreground" />
            ) : (
              <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
            )}
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="搜索页面、记忆…（↑↓ 选择，Enter 跳转）"
              className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
            <kbd className="rounded border px-1.5 py-0.5 text-[10px] text-muted-foreground">ESC</kbd>
          </div>
        </DialogHeader>
        <div ref={listRef} className="max-h-[50vh] overflow-y-auto p-2">
          {entries.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              {query.trim().length >= 2 ? '无匹配结果' : '输入关键词，或直接回车浏览页面'}
            </p>
          ) : (
            <>
              {pageEntries.length > 0 && (
                <p className="px-3 pb-1 pt-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">页面</p>
              )}
              {pageEntries.map(renderRow)}
              {memoryEntries.length > 0 && (
                <p className="px-3 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">记忆</p>
              )}
              {memoryEntries.map(renderRow)}
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
