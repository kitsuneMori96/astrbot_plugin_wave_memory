import type { ChannelConfigData, ChannelSettings } from '@/api/channels'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

const numericFields = ['priority', 'top_k', 'max_items', 'token_budget', 'timeout_ms', 'min_score'] as const

type NumericField = (typeof numericFields)[number]

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
            <TableHead>启用</TableHead>
            <TableHead>priority</TableHead>
            <TableHead>top_k</TableHead>
            <TableHead>max_items</TableHead>
            <TableHead>token_budget</TableHead>
            <TableHead>timeout_ms</TableHead>
            <TableHead>min_score</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {Object.entries(channels).map(([name, channel]) => {
            const safety = name === 'safety'
            return (
              <TableRow key={name}>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{name}</span>
                    {safety ? <Badge variant="secondary">non-disableable</Badge> : null}
                  </div>
                </TableCell>
                <TableCell>
                  <Switch checked={safety ? true : Boolean(channel.enabled)} disabled={safety} onCheckedChange={(checked) => updateChannel(name, { enabled: safety ? true : checked })} />
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
