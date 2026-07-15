const SHARED_SCOPE_QUERY_KEYS = ['bot_id', 'session_id', 'visibility'] as const

export function sharedScopeSearch(search: string): string {
  const source = new URLSearchParams(search)
  const scoped = new URLSearchParams()

  for (const key of SHARED_SCOPE_QUERY_KEYS) {
    const value = source.get(key)
    if (value !== null && value !== '') {
      scoped.set(key, value)
    }
  }

  const query = scoped.toString()
  return query ? `?${query}` : ''
}
