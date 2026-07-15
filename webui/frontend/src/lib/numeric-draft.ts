export interface NumericConstraints {
  label: string
  integer?: boolean
  min?: number
  max?: number
}

export interface NumericDraftResult {
  value: number | null
  error: string | null
}

export function validateNumericDraft(raw: string, constraints: NumericConstraints): NumericDraftResult {
  const valueText = raw.trim()
  if (!valueText) return { value: null, error: `${constraints.label}不能为空` }

  const value = Number(valueText)
  if (!Number.isFinite(value)) return { value: null, error: `${constraints.label}必须是有限数值` }
  if (constraints.integer && !Number.isInteger(value)) return { value: null, error: `${constraints.label}必须是整数` }
  if (constraints.min !== undefined && value < constraints.min) return { value: null, error: `${constraints.label}不能小于 ${constraints.min}` }
  if (constraints.max !== undefined && value > constraints.max) return { value: null, error: `${constraints.label}不能大于 ${constraints.max}` }
  return { value, error: null }
}
