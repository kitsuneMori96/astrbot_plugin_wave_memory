import type { ObjectRefDescriptor } from '@/components/shared/types'

export function buildObjectDeepLink(to: string, objectRef: ObjectRefDescriptor, tab?: string, traceId?: string): string {
  const [pathname, query = ''] = to.split('?', 2)
  const params = new URLSearchParams(query)
  params.set('ref', objectRef.ref)
  if (objectRef.locator !== undefined && objectRef.locator !== null && String(objectRef.locator).trim()) {
    params.set('object_id', String(objectRef.locator))
  }
  const scopeQuery = objectRef.scope_query
  if (scopeQuery?.bot_id) params.set('bot_id', scopeQuery.bot_id)
  if (scopeQuery?.session_id) params.set('session_id', scopeQuery.session_id)
  if (scopeQuery?.visibility) params.set('visibility', scopeQuery.visibility)
  if (scopeQuery?.subject_principal_id) params.set('subject_principal_id', scopeQuery.subject_principal_id)
  if (tab) params.set('tab', tab)
  if (traceId) params.set('trace_id', traceId)
  return `${pathname}?${params.toString()}`
}
