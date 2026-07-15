import type { ConfigGroup } from '@/api/config'

export function changedPayload(current: ConfigGroup[], original: ConfigGroup[]): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  for (const group of current) {
    const previous = original.find((item) => item.key === group.key)
    if (group.kind === 'object') {
      const changed: Record<string, unknown> = {}
      for (const item of group.items ?? []) {
        const oldItem = previous?.items?.find((candidate) => candidate.key === item.key)
        if (!oldItem || !Object.is(item.value, oldItem.value)) changed[item.key] = item.value
      }
      if (Object.keys(changed).length) payload[group.key] = changed
    } else if (!previous || !Object.is(group.value, previous.value)) {
      payload[group.key] = group.value
    }
  }
  return payload
}
