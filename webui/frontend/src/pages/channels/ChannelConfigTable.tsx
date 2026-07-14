import { ExternalLinkIcon } from 'lucide-react'
import { Link } from 'react-router-dom'

import type { ChannelConfigData, ChannelDescriptor, ChannelSettings, FieldValueDto } from '@/api/channels'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

const numericFields = [
  ['priority', '优先级'],
  ['top_k', '检索数'],
  ['max_items', '最大条数'],
  ['token_budget', 'Token 预算'],
  ['timeout_ms', '超时 ms'],
  ['min_score', '最低分'],
] as const

type NumericField = (typeof numericFields)[number][0]

function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function fieldValue(channel: ChannelSettings | undefined, field: NumericField): string {
  const value = channel?.[field]
  return value === undefined || value === null ? '' : String(value)
}

function parseField(field: NumericField, value: string): number | null {
  if (value === '') return field === 'top_k' || field === 'max_items' || field === 'min_score' ? null : 0
  const parsed = field === 'min_score' ? Number.parseFloat(value) : Number.parseInt(value, 10)
  return Number.isFinite(parsed) ? parsed : null
}

function ValueState({ state }: { state?: FieldValueDto }) {
  if (!state) return null
  return (
    <span className="flex flex-wrap gap-x-2 text-xs text-muted-foreground" title={`生效方式：${state.apply_mode}`}>
      <span>默认 {display(state.default)}</span>
      <span>保存 {display(state.saved)}</span>
      <span className="font-medium text-foreground">生效 {display(state.effective)}</span>
    </span>
  )
}

export function ChannelConfigTable({
  draft,
  descriptors,
  onDraftChange,
}: {
  draft: ChannelConfigData
  descriptors: ChannelDescriptor[]
  onDraftChange: (draft: ChannelConfigData) => void
}) {
  const channels = draft.channels ?? {}
  const descriptorMap = new Map(descriptors.map((item) => [item.id, item]))

  function updateChannel(name: string, patch: Partial<ChannelSettings>) {
    onDraftChange({ ...draft, channels: { ...channels, [name]: { ...(channels[name] ?? {}), ...patch } } })
  }

  return (
    <div data-slot="channel-config-table" className="overflow-hidden rounded-xl border border-border/70">
      <Table>
        <TableHeader className="bg-muted/25">
          <TableRow>
            <TableHead className="w-[24%]">通道与用途</TableHead>
            <TableHead className="w-[18%]">运行状态</TableHead>
            <TableHead>参数</TableHead>
            <TableHead className="w-32 text-right">管理</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {Object.entries(channels).map(([name, channel]) => {
            const descriptor = descriptorMap.get(name)
            const safety = name === 'safety'
            return (
              <TableRow key={name} className="align-top">
                <TableCell>
                  <div className="flex flex-col gap-2 py-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold">{name}</span>
                      {safety ? <Badge variant="secondary">固定启用</Badge> : null}
                    </div>
                    <p className="text-sm leading-relaxed text-muted-foreground">{descriptor?.purpose ?? '后端未返回用途说明'}</p>
                    <div className="flex flex-wrap gap-1">
                      <Badge variant={descriptor?.risk === 'critical' || descriptor?.risk === 'high' ? 'destructive' : 'outline'}>
                        风险：{descriptor?.risk ?? 'unknown'}
                      </Badge>
                      {(descriptor?.dependencies ?? []).slice(0, 3).map((dependency) => <Badge key={dependency} variant="outline">{dependency}</Badge>)}
                    </div>
                  </div>
                </TableCell>
                <TableCell>
                  <div className="flex flex-col gap-3 py-1">
                    <div className="flex items-center gap-2">
                      <Switch aria-label={`${name} 启用状态`} checked={safety ? true : Boolean(channel.enabled)} disabled={safety} onCheckedChange={(checked) => updateChannel(name, { enabled: safety ? true : checked })} />
                      <span className="text-sm">{channel.enabled || safety ? '已启用' : '已停用'}</span>
                    </div>
                    <Badge variant={channel.status === 'error' || channel.status === 'timeout' ? 'destructive' : 'secondary'} className="w-fit">
                      {String(channel.status ?? 'unknown')}
                    </Badge>
                    <span className="text-xs text-muted-foreground">最近 {display(channel.last_hit_count)} 命中 · {display(channel.last_latency_ms)} ms</span>
                    <ValueState state={channel.field_states?.enabled} />
                  </div>
                </TableCell>
                <TableCell>
                  <div className="grid min-w-[360px] gap-3 py-1 sm:grid-cols-2 xl:grid-cols-3">
                    {numericFields.map(([field, label]) => (
                      <label key={field} className="flex min-w-0 flex-col gap-1.5 text-xs font-medium">
                        <span>{label}</span>
                        <Input
                          aria-label={`${name} ${label}`}
                          inputMode={field === 'min_score' ? 'decimal' : 'numeric'}
                          value={fieldValue(channel, field)}
                          onChange={(event) => updateChannel(name, { [field]: parseField(field, event.target.value) })}
                        />
                        <ValueState state={channel.field_states?.[field]} />
                      </label>
                    ))}
                  </div>
                </TableCell>
                <TableCell className="text-right">
                  {descriptor?.management_route ? (
                    <Button asChild variant="outline" size="sm">
                      <Link to={descriptor.management_route}>负责页面<ExternalLinkIcon data-icon="inline-end" aria-hidden="true" /></Link>
                    </Button>
                  ) : <span className="text-xs text-muted-foreground">当前页管理</span>}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
