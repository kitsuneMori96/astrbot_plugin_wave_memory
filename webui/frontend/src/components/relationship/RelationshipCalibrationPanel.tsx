import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { listMemories, type MemoryItem } from '@/api/memories'
import { calibrateRelationship, type PeopleQuery, type RelationshipItem } from '@/api/people'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

const DIMENSIONS = [
  ['familiarity', '熟悉度'],
  ['trust', '信任'],
  ['fun', '趣味'],
  ['depth', '深度'],
] as const
const ACTIONS = [
  ['adjust', '相对调整'],
  ['override', '绝对覆盖'],
  ['clear_override', '取消覆盖'],
  ['restore_auto', '恢复自动推断'],
] as const

function displayValue(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : '未知 / 未记录'
}

type RelationshipCalibrationTarget = Pick<RelationshipItem, 'subject_principal_id' | 'revision' | 'values' | 'object_ref' | 'calibration'>

export function RelationshipCalibrationPanel({ item, query, onChanged }: { item: RelationshipCalibrationTarget; query: PeopleQuery; onChanged?: () => void }) {
  const [action, setAction] = useState<(typeof ACTIONS)[number][0]>('adjust')
  const [dimension, setDimension] = useState<(typeof DIMENSIONS)[number][0]>('trust')
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('')
  const [evidence, setEvidence] = useState('')
  const [evidenceMemories, setEvidenceMemories] = useState<MemoryItem[]>([])
  const [selectedEvidenceId, setSelectedEvidenceId] = useState('')
  const [busy, setBusy] = useState(false)
  const values = item.values ?? {}
  const available = Boolean(item.object_ref?.ref && item.revision !== null && item.calibration.available)

  useEffect(() => {
    if (!available) {
      setEvidenceMemories([])
      setSelectedEvidenceId('')
      return
    }
    let active = true
    void listMemories({ bot_id: query.bot_id, session_id: query.session_id, visibility: query.visibility, limit: 25, offset: 0 })
      .then((payload) => { if (active) setEvidenceMemories(payload.items) })
      .catch(() => { if (active) setEvidenceMemories([]) })
    return () => { active = false }
  }, [available, query.bot_id, query.session_id, query.visibility])

  async function submit() {
    if (!available || !item.object_ref || item.revision === null) return
    if (!reason.trim()) { toast.warning('请填写人工校准理由'); return }
    if (!selectedEvidenceId) { toast.warning('请选择当前 Scope 内的真实 Memory 证据'); return }
    setBusy(true)
    try {
      const numeric = amount.trim() ? Number(amount) : undefined
      if ((action === 'adjust' || action === 'override') && (numeric === undefined || !Number.isFinite(numeric))) {
        toast.warning('请输入有效的关系数值')
        return
      }
      const [platformId, , ...conversationParts] = query.session_id.split(':')
      const evidencePayload = [{
        kind: 'memory',
        id: selectedEvidenceId,
        summary: evidence.trim() || undefined,
        source_scope: { bot_id: query.bot_id, visibility: query.visibility, session: { id: query.session_id, platform_id: platformId, kind: 'group', conversation_id: conversationParts.join(':') }, subject_principal_id: item.subject_principal_id },
      }]
      await calibrateRelationship(query, {
        object_ref: item.object_ref.ref,
        revision: item.revision,
        action,
        dimension,
        ...(action === 'adjust' ? { delta: numeric } : action === 'override' ? { value: numeric } : {}),
        reason: reason.trim(),
        evidence: evidencePayload,
      })
      toast.success('关系人工校准已提交并记录审计')
      setReason('')
      setEvidence('')
      setSelectedEvidenceId('')
      setAmount('')
      onChanged?.()
    } catch (failure) {
      toast.error(failure instanceof Error ? failure.message : '关系人工校准失败')
    } finally {
      setBusy(false)
    }
  }

  return <Card className="border-pink-500/20">
    <CardHeader className="pb-3"><CardTitle className="text-sm">人工关系校准</CardTitle><CardDescription>自动值继续学习；人工 adjustment/override 默认不衰减。所有动作需要理由、证据和当前 revision。</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-4">
      <div className="grid gap-2 sm:grid-cols-2">{DIMENSIONS.map(([key, label]) => <div key={key} className="rounded-md border bg-muted/10 p-2 text-xs"><div className="font-medium">{label} <span className="font-mono text-muted-foreground">{key}</span></div><dl className="mt-2 grid grid-cols-2 gap-x-2 gap-y-1 text-muted-foreground"><dt>自动</dt><dd className="text-right font-mono">{displayValue(values[key]?.automatic_value)}</dd><dt>调整</dt><dd className="text-right font-mono">{displayValue(values[key]?.manual_adjustment)}</dd><dt>覆盖</dt><dd className="text-right font-mono">{displayValue(values[key]?.manual_override)}</dd><dt>生效</dt><dd className="text-right font-mono text-foreground">{displayValue(values[key]?.effective_value)}</dd></dl></div>)}</div>
      {!available ? <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">当前人物没有正式 scoped relationship projection，保持 unknown/null，不提供写入按钮。</p> : <>
        <div className="grid gap-3 sm:grid-cols-2"><Field><FieldLabel htmlFor="relationship-action">动作</FieldLabel><select id="relationship-action" className="h-8 rounded-md border bg-background px-2 text-sm" value={action} onChange={(event) => setAction(event.target.value as typeof action)}>{ACTIONS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field><Field><FieldLabel htmlFor="relationship-dimension">关系维度</FieldLabel><select id="relationship-dimension" className="h-8 rounded-md border bg-background px-2 text-sm" value={dimension} onChange={(event) => setDimension(event.target.value as typeof dimension)}>{DIMENSIONS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field></div>
        {action === 'adjust' || action === 'override' ? <Field><FieldLabel htmlFor="relationship-amount">数值</FieldLabel><Input id="relationship-amount" inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} placeholder={action === 'adjust' ? '相对变化，例如 2 或 -1' : '绝对值，必须在该维度范围内'} /></Field> : null}
        <Field><FieldLabel htmlFor="relationship-reason">理由</FieldLabel><Textarea id="relationship-reason" maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="说明为什么需要人工校准…" /></Field>
        <Field><FieldLabel htmlFor="relationship-evidence-memory">真实 Memory 证据</FieldLabel><select id="relationship-evidence-memory" className="h-8 rounded-md border bg-background px-2 text-sm" value={selectedEvidenceId} onChange={(event) => setSelectedEvidenceId(event.target.value)}><option value="">请选择当前 Scope 的 Memory…</option>{evidenceMemories.map((memory) => <option key={memory.id} value={String(memory.id)}>{memory.id} · {(memory.content || '').slice(0, 72)}</option>)}</select><FieldDescription>服务端会重新读取该 Memory、计算 content hash 并校验 Scope；前端不能伪造 available 状态。</FieldDescription></Field>
        <Field><FieldLabel htmlFor="relationship-evidence">补充证据说明（可选）</FieldLabel><Textarea id="relationship-evidence" value={evidence} onChange={(event) => setEvidence(event.target.value)} placeholder="补充说明这条真实消息为什么支持校准…" /></Field>
        <Button type="button" disabled={busy} onClick={() => void submit()}>{busy ? '提交中…' : '提交人工校准'}</Button>
      </>}
    </CardContent>
  </Card>
}
