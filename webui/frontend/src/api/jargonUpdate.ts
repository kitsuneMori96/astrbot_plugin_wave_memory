export interface HolymanUpdateCheckPayload {
  ok: boolean
  asset_type?: string
  runtime_policy?: string
  local_version: string
  remote_version: string
  remote_commit_version?: string
  content_hash?: string
  local_content_hash?: string
  content_count?: number
  local_count?: number
  local_counts?: Record<string, number>
  asset_status: string
  has_update: boolean
  is_update_available?: boolean
  update_available?: boolean
  remote_reachable?: boolean
  checked_at: string
  cached: boolean
  cache_age_seconds?: number
  cache_ttl_seconds?: number
  source_url?: string
  warning?: string
}

export function holymanUpdateCheckPath(force = false): string {
  return force ? '/api/jargon/holyman/update/check?force=true' : '/api/jargon/holyman/update/check'
}
