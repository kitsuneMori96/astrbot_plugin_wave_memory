import type { ChannelConfigData, ChannelPatch, ChannelSettings, ChannelValidationPayload } from '@/api/channels'
import { stableStringify } from '@/lib/stable-json'

const editableFields = ['enabled', 'priority', 'top_k', 'max_items', 'token_budget', 'timeout_ms', 'min_score'] as const

export function serializeChannelPatch(draft: ChannelConfigData): ChannelPatch {
  const channels: Record<string, Partial<ChannelSettings>> = {}
  Object.entries(draft.channels ?? {}).forEach(([name, channel]) => {
    channels[name] = {}
    editableFields.forEach((field) => {
      if (field in channel) channels[name][field] = channel[field] as never
    })
    if (name === 'safety') channels[name].enabled = true
  })
  return {
    ...(draft.recent_dedup_minutes === undefined ? {} : { recent_dedup_minutes: draft.recent_dedup_minutes }),
    trace_enabled: Boolean(draft.trace_enabled),
    channels,
  }
}

export function channelPatchFingerprint(patch: ChannelPatch): string {
  return stableStringify(patch)
}

export function hasFreshChannelPreflight(
  validation: ChannelValidationPayload | null,
  validatedFingerprint: string | null,
  patch: ChannelPatch,
): boolean {
  return Boolean(validation?.ok && validation.preflight_token && validatedFingerprint && validatedFingerprint === channelPatchFingerprint(patch))
}
