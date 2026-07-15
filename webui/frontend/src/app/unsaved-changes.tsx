/* oxlint-disable react/only-export-components -- provider 与注册 hook 必须共享同一私有 context */
import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

type GuardedAction = () => void | Promise<void>
type RegisterGuard = (id: string, dirty: boolean, message: string) => () => void

interface ConfirmActionOptions {
  message?: string
  confirmLabel?: string
}

interface UnsavedChangesContextValue {
  register: RegisterGuard
  confirmAction: (action: GuardedAction, options?: ConfirmActionOptions) => void
}

const UnsavedChangesContext = createContext<UnsavedChangesContextValue>({
  register: () => () => undefined,
  confirmAction: (action) => { void action() },
})

interface PendingRequestBase {
  message: string
  confirmLabel: string
}

interface PendingAnchorNavigation extends PendingRequestBase {
  kind: 'anchor'
  anchor: HTMLAnchorElement
}

interface PendingLocationNavigation extends PendingRequestBase {
  kind: 'location'
  href: string
}

interface PendingAction extends PendingRequestBase {
  kind: 'action'
  action: GuardedAction
}

type PendingRequest = PendingAnchorNavigation | PendingLocationNavigation | PendingAction

function isGuardedNavigation(event: MouseEvent, anchor: HTMLAnchorElement): boolean {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false
  if (anchor.hasAttribute('download')) return false
  const target = anchor.getAttribute('target')?.toLowerCase()
  if (target && target !== '_self') return false

  const rawHref = anchor.getAttribute('href')
  if (!rawHref || rawHref.startsWith('mailto:') || rawHref.startsWith('tel:') || rawHref.startsWith('javascript:')) return false

  const url = new URL(anchor.href, window.location.href)
  if (url.origin !== window.location.origin || url.href === window.location.href) return false

  const sameDocument = url.pathname === window.location.pathname && url.search === window.location.search
  if (sameDocument && url.hash && !url.hash.startsWith('#/')) return false
  return true
}

export function UnsavedChangesProvider({ children }: { children: ReactNode }) {
  const location = useLocation()
  const guardsRef = useRef(new Map<string, string>())
  const acceptedHrefRef = useRef(window.location.href)
  const allowedHrefRef = useRef<string | null>(null)
  const bypassAnchorRef = useRef<HTMLAnchorElement | null>(null)
  const [pending, setPending] = useState<PendingRequest | null>(null)

  const guardMessage = useCallback(() => {
    const messages = Array.from(new Set(guardsRef.current.values()))
    return messages.length === 1 ? messages[0] : '当前页面有多处未保存修改，离开后这些修改将丢失。'
  }, [])

  const register = useCallback<RegisterGuard>((id, dirty, message) => {
    if (dirty) guardsRef.current.set(id, message)
    else guardsRef.current.delete(id)
    return () => { guardsRef.current.delete(id) }
  }, [])

  const confirmAction = useCallback((action: GuardedAction, options?: ConfirmActionOptions) => {
    if (!guardsRef.current.size) {
      void action()
      return
    }
    setPending({
      kind: 'action',
      action,
      message: options?.message ?? guardMessage(),
      confirmLabel: options?.confirmLabel ?? '放弃修改并继续',
    })
  }, [guardMessage])

  useEffect(() => {
    const href = window.location.href
    if (!guardsRef.current.size || allowedHrefRef.current === href) {
      acceptedHrefRef.current = href
      allowedHrefRef.current = null
    }
  }, [location.hash, location.key, location.pathname, location.search])

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!guardsRef.current.size) return
      event.preventDefault()
      event.returnValue = ''
    }
    const captureNavigation = (event: MouseEvent) => {
      if (!guardsRef.current.size) return
      const anchor = event.composedPath().find((node): node is HTMLAnchorElement => node instanceof HTMLAnchorElement)
      if (!anchor || !isGuardedNavigation(event, anchor)) return
      if (bypassAnchorRef.current === anchor) {
        bypassAnchorRef.current = null
        return
      }
      event.preventDefault()
      event.stopPropagation()
      setPending({ kind: 'anchor', anchor, message: guardMessage(), confirmLabel: '放弃修改并离开' })
    }
    const captureHistoryNavigation = () => {
      const targetHref = window.location.href
      if (!guardsRef.current.size) {
        acceptedHrefRef.current = targetHref
        return
      }
      if (targetHref === acceptedHrefRef.current) return
      if (allowedHrefRef.current === targetHref) {
        acceptedHrefRef.current = targetHref
        allowedHrefRef.current = null
        return
      }

      const returnHref = acceptedHrefRef.current
      window.history.replaceState(window.history.state, '', returnHref)
      window.dispatchEvent(new PopStateEvent('popstate', { state: window.history.state }))
      setPending({ kind: 'location', href: targetHref, message: guardMessage(), confirmLabel: '放弃修改并离开' })
    }

    window.addEventListener('beforeunload', beforeUnload)
    window.addEventListener('popstate', captureHistoryNavigation, true)
    window.addEventListener('hashchange', captureHistoryNavigation, true)
    document.addEventListener('click', captureNavigation, true)
    return () => {
      window.removeEventListener('beforeunload', beforeUnload)
      window.removeEventListener('popstate', captureHistoryNavigation, true)
      window.removeEventListener('hashchange', captureHistoryNavigation, true)
      document.removeEventListener('click', captureNavigation, true)
    }
  }, [guardMessage])

  const contextValue = useMemo<UnsavedChangesContextValue>(() => ({ register, confirmAction }), [confirmAction, register])

  const confirmPending = () => {
    const request = pending
    setPending(null)
    if (!request) return
    if (request.kind === 'action') {
      void request.action()
      return
    }
    if (request.kind === 'anchor') {
      const href = request.anchor.href
      allowedHrefRef.current = href
      bypassAnchorRef.current = request.anchor
      request.anchor.click()
      return
    }
    allowedHrefRef.current = request.href
    acceptedHrefRef.current = request.href
    window.history.pushState(window.history.state, '', request.href)
    window.dispatchEvent(new PopStateEvent('popstate', { state: window.history.state }))
  }

  return (
    <UnsavedChangesContext.Provider value={contextValue}>
      {children}
      <Dialog open={Boolean(pending)} onOpenChange={(open) => { if (!open) setPending(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>放弃未保存修改？</DialogTitle>
            <DialogDescription>{pending?.message} 确认后将无法恢复这些草稿。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setPending(null)}>继续编辑</Button>
            <Button type="button" variant="destructive" onClick={confirmPending}>{pending?.confirmLabel ?? '放弃修改并继续'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </UnsavedChangesContext.Provider>
  )
}

export function useUnsavedChangesGuard(dirty: boolean, message: string) {
  const { register } = useContext(UnsavedChangesContext)
  const id = useId()
  useEffect(() => register(id, dirty, message), [dirty, id, message, register])
}

export function useUnsavedChangesConfirm() {
  return useContext(UnsavedChangesContext).confirmAction
}
