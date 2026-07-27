import { useAuth } from './auth'
import { SseParser } from './sse'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function authHeaders(): Record<string, string> {
  const token = useAuth.getState().token
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...init?.headers },
  })
  if (res.status === 401) {
    useAuth.getState().clear()
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new ApiError(body?.detail ?? res.statusText, res.status)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

function json<T>(path: string, method: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
}

export interface Workspace {
  id: string
  name: string
  description: string | null
  language: string
  status: string
  created_at: string
}

export interface Source {
  id: string
  type: 'pdf' | 'md'
  title: string
  status: 'pending' | 'parsing' | 'embedding' | 'ready' | 'failed'
  error: string | null
  created_at: string
}

export interface Citation {
  n: number
  chunk_id: string
  source_id: string
  source_title: string
  heading: string | null
  excerpt: string
  truncated: boolean
  images: string[]
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: Citation[] | null
  created_at: string
}

export interface ChatSession {
  id: string
  workspace_id: string
  title: string | null
  created_at: string
}

export async function fetchImage(
  workspaceId: string,
  sourceId: string,
  imageId: string,
): Promise<Blob> {
  const res = await fetch(
    `${BASE_URL}/workspaces/${workspaceId}/images/${sourceId}/${encodeURIComponent(imageId)}`,
    { headers: authHeaders() },
  )
  if (!res.ok) throw new ApiError(res.statusText, res.status)
  return res.blob()
}

export const api = {
  register: (email: string, password: string) =>
    json('/auth/register', 'POST', { email, password }),
  login: (email: string, password: string) =>
    json<TokenResponse>('/auth/login', 'POST', { email, password }),

  listWorkspaces: () => request<Workspace[]>('/workspaces'),
  createWorkspace: (name: string, description?: string) =>
    json<Workspace>('/workspaces', 'POST', { name, description }),
  deleteWorkspace: (id: string) => request<void>(`/workspaces/${id}`, { method: 'DELETE' }),

  listSources: (workspaceId: string) => request<Source[]>(`/workspaces/${workspaceId}/sources`),
  uploadSource: (workspaceId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Source>(`/workspaces/${workspaceId}/sources/upload`, {
      method: 'POST',
      body: form,
    })
  },
  retrySource: (workspaceId: string, sourceId: string) =>
    request<Source>(`/workspaces/${workspaceId}/sources/${sourceId}/retry`, { method: 'POST' }),
  deleteSource: (workspaceId: string, sourceId: string) =>
    request<void>(`/workspaces/${workspaceId}/sources/${sourceId}`, { method: 'DELETE' }),

  createSession: (workspaceId: string) =>
    request<ChatSession>(`/workspaces/${workspaceId}/chat/sessions`, { method: 'POST' }),
  listSessions: (workspaceId: string) =>
    request<ChatSession[]>(`/workspaces/${workspaceId}/chat/sessions`),
  deleteSession: (sessionId: string) =>
    request<void>(`/chat/sessions/${sessionId}`, { method: 'DELETE' }),
  listMessages: (sessionId: string) =>
    request<ChatMessage[]>(`/chat/sessions/${sessionId}/messages`),
}

export type Stage = 'retrieve' | 'grade' | 'generate' | 'decline'

export interface StageEvent {
  stage: Stage
  excerpts?: number
}

export interface StreamHandlers {
  onStage: (event: StageEvent) => void
  onCitations: (citations: Citation[]) => void
  onToken: (delta: string) => void
  onDone: (grounded: boolean) => void
  onError: (message: string) => void
}

export async function streamAnswer(
  sessionId: string,
  message: string,
  handlers: StreamHandlers,
): Promise<void> {
  const res = await fetch(`${BASE_URL}/chat/sessions/${sessionId}/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ message }),
  })
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => null)
    throw new ApiError(body?.detail ?? res.statusText, res.status)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  const parser = new SseParser()

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break

    for (const { event, data } of parser.push(decoder.decode(value, { stream: true }))) {
      if (event === 'stage') handlers.onStage(JSON.parse(data))
      else if (event === 'citations') handlers.onCitations(JSON.parse(data))
      else if (event === 'token') handlers.onToken(JSON.parse(data).delta)
      else if (event === 'done') handlers.onDone(JSON.parse(data).grounded)
      else if (event === 'error') handlers.onError(JSON.parse(data).message)
    }
  }
}
