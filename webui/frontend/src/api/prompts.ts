import { fetchJson } from './client'

export interface PersonaItem {
  id: number
  name: string
  system_prompt: string
  begin_dialogs: unknown[]
  enabled: boolean
  built_in: boolean
  created_at?: number
  updated_at?: number
}

export interface PersonaBinding {
  id: number
  scope: 'global' | 'bot' | 'group'
  scope_id: string
  persona_id: number
  persona_name: string | null
}

export interface PromptTemplate {
  key: string
  name: string
  category: string
  content: string
  variables: string[]
  updated_at?: number
  is_custom: boolean
  built_in_content: string
}

export async function listPersonas(includeDisabled = true): Promise<PersonaItem[]> {
  const data = await fetchJson<{ items: PersonaItem[] }>(
    `/api/prompts/personas?include_disabled=${includeDisabled ? '1' : '0'}`,
  )
  return data.items ?? []
}

export async function createPersona(payload: {
  name: string
  system_prompt: string
  begin_dialogs?: unknown[]
  enabled?: boolean
}): Promise<{ id: number }> {
  return fetchJson('/api/prompts/personas', { method: 'POST', body: JSON.stringify(payload) })
}

export async function updatePersona(
  id: number,
  payload: Partial<Pick<PersonaItem, 'name' | 'system_prompt' | 'begin_dialogs' | 'enabled'>>,
): Promise<{ ok: boolean }> {
  return fetchJson(`/api/prompts/personas/${id}`, { method: 'PUT', body: JSON.stringify(payload) })
}

export async function deletePersona(id: number): Promise<{ ok: boolean }> {
  return fetchJson(`/api/prompts/personas/${id}`, { method: 'DELETE' })
}

export async function listBindings(): Promise<PersonaBinding[]> {
  const data = await fetchJson<{ items: PersonaBinding[] }>('/api/prompts/bindings')
  return data.items ?? []
}

export async function setBinding(scope: string, personaId: number, scopeId = ''): Promise<{ ok: boolean }> {
  return fetchJson('/api/prompts/bindings', {
    method: 'POST',
    body: JSON.stringify({ scope, scope_id: scopeId, persona_id: personaId }),
  })
}

export async function removeBinding(scope: string, scopeId = ''): Promise<{ ok: boolean }> {
  const params = scopeId ? `?scope_id=${encodeURIComponent(scopeId)}` : ''
  return fetchJson(`/api/prompts/bindings/${scope}${params}`, { method: 'DELETE' })
}

export async function listTemplates(): Promise<PromptTemplate[]> {
  const data = await fetchJson<{ items: PromptTemplate[] }>('/api/prompts/templates')
  return data.items ?? []
}

export async function saveTemplate(key: string, content: string): Promise<{ ok: boolean }> {
  return fetchJson(`/api/prompts/templates/${key}`, { method: 'PUT', body: JSON.stringify({ content }) })
}

export async function resetTemplate(key: string): Promise<{ content: string }> {
  return fetchJson(`/api/prompts/templates/${key}/reset`, { method: 'POST' })
}

export async function importFromAstrbot(): Promise<{ imported: number; skipped: string[] }> {
  return fetchJson('/api/prompts/import_astrbot', { method: 'POST' })
}
