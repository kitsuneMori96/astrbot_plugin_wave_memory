import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useIsMobile } from '@/hooks/use-mobile'
import { QueryState } from './QueryState'
import { ObjectDeepLink } from './ObjectDeepLink'
import { ResponsiveDetail } from './ResponsiveDetail'
import type { EvidenceRef } from './types'

export interface EvidenceListProps {
  evidence: EvidenceRef[]
  objectPath?: string | ((evidence: EvidenceRef) => string)
  emptyDescription?: string
}

const AVAILABILITY_LABELS: Record<NonNullable<EvidenceRef['availability']>, string> = {
  available: '可用',
  unavailable: '不可用',
  quarantined: '已隔离',
  unknown: '未知',
}

function Availability({ value }: { value?: EvidenceRef['availability'] }) {
  const status = value ?? 'unknown'
  return <Badge variant={status === 'available' ? 'default' : status === 'quarantined' ? 'secondary' : 'outline'}>{AVAILABILITY_LABELS[status]}</Badge>
}

function EvidenceDetails({ item, objectPath }: { item: EvidenceRef; objectPath?: EvidenceListProps['objectPath'] }) {
  const path = typeof objectPath === 'function' ? objectPath(item) : objectPath
  return (
    <div data-slot="evidence-details" className="flex flex-col gap-4">
      <dl className="grid gap-3 sm:grid-cols-2">
        <div><dt className="font-medium text-muted-foreground">类型</dt><dd>{item.type}</dd></div>
        <div><dt className="font-medium text-muted-foreground">稳定 ID</dt><dd className="break-all font-mono">{item.id}</dd></div>
        <div><dt className="font-medium text-muted-foreground">内容 hash</dt><dd className="break-all font-mono">{item.content_hash ?? '未知'}</dd></div>
        <div><dt className="font-medium text-muted-foreground">采集时间</dt><dd>{item.captured_at ?? '未知'}</dd></div>
        <div><dt className="font-medium text-muted-foreground">来源作用域</dt><dd className="break-all">{item.source_scope ?? '未知'}</dd></div>
        <div><dt className="font-medium text-muted-foreground">可用状态</dt><dd><Availability value={item.availability} /></dd></div>
      </dl>
      {item.summary ? <p className="text-sm text-muted-foreground">{item.summary}</p> : null}
      {path && item.object_ref ? <ObjectDeepLink to={path} objectRef={item.object_ref}>打开证据对象</ObjectDeepLink> : null}
    </div>
  )
}

export function EvidenceList({ evidence, objectPath, emptyDescription }: EvidenceListProps) {
  const isMobile = useIsMobile()
  if (evidence.length === 0) {
    return <QueryState status="empty" title="当前没有证据" description={emptyDescription ?? '服务端未返回可用证据，未使用相似文本或演示记录替代。'} />
  }

  if (isMobile) {
    return (
      <ul data-slot="evidence-list" className="flex flex-col gap-3" data-evidence-layout="mobile">
        {evidence.map((item) => (
          <li key={`${item.type}:${item.id}`} className="flex flex-col gap-3 rounded-lg border bg-card p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0"><p className="font-medium">{item.type}</p><p className="break-all font-mono text-sm text-muted-foreground">{item.id}</p></div>
              <Availability value={item.availability} />
            </div>
            <ResponsiveDetail title={`证据 ${item.id}`} description="完整证据引用与服务端签发的 scoped 对象入口">
              <EvidenceDetails item={item} objectPath={objectPath} />
            </ResponsiveDetail>
          </li>
        ))}
      </ul>
    )
  }

  return (
    <Table data-evidence-layout="table">
      <TableHeader>
        <TableRow>
          <TableHead>类型</TableHead>
          <TableHead>稳定 ID</TableHead>
          <TableHead>来源作用域</TableHead>
          <TableHead>状态</TableHead>
          <TableHead><span className="sr-only">操作</span></TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {evidence.map((item) => (
          <TableRow key={`${item.type}:${item.id}`}>
            <TableCell>{item.type}</TableCell>
            <TableCell className="max-w-56 truncate font-mono">{item.id}</TableCell>
            <TableCell>{item.source_scope ?? '未知'}</TableCell>
            <TableCell><Availability value={item.availability} /></TableCell>
            <TableCell className="text-right">
              <ResponsiveDetail title={`证据 ${item.id}`} description="完整证据引用与服务端签发的 scoped 对象入口">
                <EvidenceDetails item={item} objectPath={objectPath} />
              </ResponsiveDetail>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
