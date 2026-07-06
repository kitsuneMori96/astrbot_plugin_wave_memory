import { Link } from 'react-router-dom'
import { ExternalLinkIcon } from 'lucide-react'

import type { ChannelConfigData, ChannelSettings } from '@/api/channels'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

const numericFields = ['priority', 'top_k', 'max_items', 'token_budget', 'timeout_ms', 'min_score'] as const

type NumericField = (typeof numericFields)[number]

type BadgeVariant = 'default' | 'secondary' | 'destructive' | 'outline'

interface ChannelMetadata {
  description: string
  risk: string
  managementLabel: string
  managementPath?: string
}

const channelMetadata: Record<string, ChannelMetadata> = {
  safety: {
    description: '身份污染、近期去重、安全兜底',
    risk: '固定启用，不允许关闭',
    managementLabel: '固定启用',
  },
  memory: {
    description: '群聊长期记忆召回',
    risk: '关闭 memory：长期记忆不注入',
    managementLabel: '记忆管理器',
    managementPath: '/memories',
  },
  timeline: {
    description: '时间线/近期重要事件',
    risk: '关闭 timeline：近期重要事件不注入',
    managementLabel: '灵魂与人物',
    managementPath: '/blackbox/people',
  },
  facts: {
    description: '事实关系注入',
    risk: '关闭 facts：稳定事实关系不注入',
    managementLabel: 'Facts 管理',
    managementPath: '/blackbox/facts',
  },
  persona: {
    description: '人格画像摘要',
    risk: '关闭 persona：人格画像摘要不注入',
    managementLabel: '灵魂与情绪',
    managementPath: '/soul',
  },
  belief: {
    description: '信念注入',
    risk: '关闭 belief：Bot 判断/认知不注入',
    managementLabel: '信念审核',
    managementPath: '/beliefs',
  },
  jargon: {
    description: '黑话理解参考',
    risk: '关闭 jargon：本地黑话不注入',
    managementLabel: '黑话与口癖',
    managementPath: '/jargon',
  },
  fewshot: {
    description: '风格范例注入',
    risk: '关闭 fewshot：风格示例不注入',
    managementLabel: 'FewShot 管理',
    managementPath: '/blackbox/fewshot',
  },
  book_lore: {
    description: '书设知识注入',
    risk: '关闭 book_lore：书设知识不注入',
    managementLabel: 'BookLore 管理',
    managementPath: '/blackbox/book-lore',
  },
  fts5: {
    description: '全文检索补召回',
    risk: '调低 token_budget：可能丢关键信息',
    managementLabel: '索引与 FTS5',
    managementPath: '/blackbox/indexes',
  },
  affinity: {
    description: '好感/关系信号',
    risk: '调低 timeout：可能导致慢通道被跳过',
    managementLabel: '人物与好感',
    managementPath: '/blackbox/people',
  },
}

function statusVariant(status: unknown): BadgeVariant {
  const value = String(status || 'unknown')
  if (value === 'hit') return 'default'
  if (value === 'error' || value === 'timeout') return 'destructive'
  if (value === 'disabled' || value === 'skipped') return 'secondary'
  return 'outline'
}

function runtimeNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function fieldValue(channel: ChannelSettings | undefined, field: NumericField): string {
  const value = channel?.[field]
  return value === undefined || value === null ? '' : String(value)
}

function parseField(field: NumericField, value: string): number | null {
  if (value === '') {
    return field === 'top_k' || field === 'max_items' || field === 'min_score' ? null : 0
  }
  const number = field === 'min_score' ? Number.parseFloat(value) : Number.parseInt(value, 10)
  return Number.isFinite(number) ? number : null
}

export function ChannelConfigTable({
  draft,
  onDraftChange,
}: {
  draft: ChannelConfigData
  onDraftChange: (draft: ChannelConfigData) => void
}) {
  const channels = draft.channels ?? {}

  function updateChannel(name: string, patch: Partial<ChannelSettings>) {
    onDraftChange({
      ...draft,
      channels: {
        ...channels,
        [name]: {
          ...(channels[name] ?? {}),
          ...patch,
        },
      },
    })
  }

  return (
    <div className="overflow-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>通道</TableHead>
            <TableHead>通道语义与风险</TableHead>
            <TableHead>管理入口</TableHead>
            <TableHead>启用</TableHead>
            <TableHead>运行状态</TableHead>
            <TableHead>最近延迟</TableHead>
            <TableHead>最近命中</TableHead>
            <TableHead>优先级</TableHead>
            <TableHead>检索数量</TableHead>
            <TableHead>最大条数</TableHead>
            <TableHead>Token 预算</TableHead>
            <TableHead>超时毫秒</TableHead>
            <TableHead>最低分</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {Object.entries(channels).map(([name, channel]) => {
            const safety = name === 'safety'
            const metadata = channelMetadata[name] ?? {
              description: '未登记通道说明',
              risk: '风险提示待补充',
              managementLabel: '暂无入口',
            }
            return (
              <TableRow key={name}>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{name}</span>
                    {safety ? <Badge variant="secondary">不可关闭</Badge> : null}
                  </div>
                </TableCell>
                <TableCell className="min-w-64">
                  <div className="flex flex-col gap-2">
                    <span className="text-sm">{metadata.description}</span>
                    <Badge variant={metadata.risk.includes('关闭') || metadata.risk.includes('调低') ? 'outline' : 'secondary'} className="w-fit">
                      风险提示：{metadata.risk}
                    </Badge>
                  </div>
                </TableCell>
                <TableCell>
                  {metadata.managementPath ? (
                    <Button asChild variant="outline" size="sm">
                      <Link to={metadata.managementPath}>
                        {metadata.managementLabel}
                        <ExternalLinkIcon data-icon="inline-end" />
                      </Link>
                    </Button>
                  ) : (
                    <Badge variant="secondary">{metadata.managementLabel}</Badge>
                  )}
                </TableCell>
                <TableCell>
                  <Switch checked={safety ? true : Boolean(channel.enabled)} disabled={safety} onCheckedChange={(checked) => updateChannel(name, { enabled: safety ? true : checked })} />
                </TableCell>
                <TableCell>
                  <Badge variant={statusVariant(channel.status)}>{String(channel.status || 'unknown')}</Badge>
                </TableCell>
                <TableCell>
                  <span className="text-muted-foreground">{runtimeNumber(channel.last_latency_ms)} ms</span>
                </TableCell>
                <TableCell>
                  <span className="text-muted-foreground">{runtimeNumber(channel.last_hit_count)}</span>
                </TableCell>
                {numericFields.map((field) => (
                  <TableCell key={field}>
                    <Input
                      className="min-w-24"
                      inputMode={field === 'min_score' ? 'decimal' : 'numeric'}
                      value={fieldValue(channel, field)}
                      onChange={(event) => updateChannel(name, { [field]: parseField(field, event.target.value) })}
                    />
                  </TableCell>
                ))}
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
