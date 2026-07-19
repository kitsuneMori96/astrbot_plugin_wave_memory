import { fetchJson } from './client'
import type { ScopeOption } from '@/components/shared/types'

export interface BotOptionDto {
  db_id: string
  name: string
  qq_id?: string
  aliases?: string[]
  status?: string
}

export interface SessionOptionDto {
  id: string
  bot_id: string
  platform_id: string
  kind: string
  conversation_id: string
  group_name?: string
  label: string
  source?: string
  sources?: string[]
  count?: number
  capabilities?: Record<string, number>
}

export interface ChannelOptionDto {
  id: string
  enabled: boolean
  source?: string
  trace_count?: number
}

export interface ScopeOptionsPayload {
  bots: BotOptionDto[]
  sessions: SessionOptionDto[]
  channels: ChannelOptionDto[]

  generated_at: number
  source: {
    health: 'healthy' | 'empty' | 'error'
    reason_code: string | null
    providers?: string[]
  }
}

export function getScopeOptions(): Promise<ScopeOptionsPayload> {
  return fetchJson<ScopeOptionsPayload>('/api/options/scopes')
}

export function scopeOptionsFor(payload: ScopeOptionsPayload, kinds: Array<ScopeOption['kind']>): ScopeOption[] {
  const items: ScopeOption[] = []
  if (kinds.includes('bot')) {
    items.push(...payload.bots.map((bot) => ({
      value: bot.db_id,
      label: bot.name || bot.db_id,
      kind: 'bot' as const,
      description: bot.qq_id ? `Bot ID ${bot.db_id} · QQ ${bot.qq_id}` : `Bot ID ${bot.db_id}`,
      disabled: bot.status !== undefined && bot.status !== 'active',
    })))
  }
  if (kinds.includes('session')) {
    items.push(...payload.sessions.map((session) => {
      const groupName = session.group_name?.trim()
      const label = session.kind === 'group' && groupName && groupName !== session.conversation_id
        ? `${groupName}（${session.conversation_id}）`
        : session.label || session.conversation_id
      return {
        value: session.id,
        label,
        kind: 'session' as const,
        description: `${session.bot_id} · ${session.kind} · ${session.source ?? 'runtime'}`,
      }
    }))
  }
  if (kinds.includes('channel')) {
    items.push(...payload.channels.map((channel) => ({
      value: channel.id,
      label: channel.id,
      kind: 'channel' as const,
      description: `${channel.enabled ? '已启用' : '已停用'} · ${channel.source ?? 'registry'}`,
      disabled: !channel.enabled,
    })))
  }
  return items
}
