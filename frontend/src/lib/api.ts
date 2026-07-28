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

/**
 * Access tokens last an hour. Without this, a tab left open past that simply
 * stops working: every call 401s and mutations fail with nothing on screen.
 * Concurrent 401s share one refresh so we do not stampede the endpoint.
 */
let refreshing: Promise<boolean> | null = null

async function refreshSession(): Promise<boolean> {
  const { refreshToken, setToken, clear } = useAuth.getState()
  if (!refreshToken) {
    clear()
    return false
  }
  refreshing ??= (async () => {
    try {
      const res = await fetch(`${BASE_URL}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      })
      if (!res.ok) {
        clear()
        return false
      }
      const tokens: TokenResponse = await res.json()
      setToken(tokens.access_token, tokens.refresh_token)
      return true
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

async function request<T>(path: string, init?: RequestInit, allowRefresh = true): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...init?.headers },
  })
  if (res.status === 401 && allowRefresh && (await refreshSession())) {
    return request<T>(path, init, false)
  }
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
  type: 'pdf' | 'md' | 'docx' | 'pptx' | 'xlsx' | 'url' | 'github'
  title: string
  status: 'pending' | 'parsing' | 'embedding' | 'ready' | 'failed'
  origin: string | null
  error: string | null
  progress: number
  progress_detail: string | null
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

export interface UsageCall {
  step: string
  model: string
  input_tokens: number
  output_tokens: number
  cost_usd: number | null
}

export interface Usage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: number | null
  /** false when some call used a model with no configured price */
  priced: boolean
  calls: UsageCall[]
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: Citation[] | null
  usage: Usage | null
  trace: TraceRecord[] | null
  created_at: string
}

export interface TraceRecord {
  agent: string
  stage: string
  label: string
  result: string | null
}

export interface PlanStage {
  id: string
  position: number
  title: string
  description: string
  topics: string[]
  activities: string[]
  estimated_minutes: number
  status: 'pending' | 'done'
}

export interface StudyPlan {
  id: string
  goal: string
  daily_minutes: number
  deadline: string | null
  created_at: string
  stages: PlanStage[]
}

export interface Question {
  id: string
  type: 'single' | 'multi' | 'fill' | 'short'
  difficulty: string
  stem: string
  options: string[] | null
  answer: string | string[]
  explanation: string
  source: { chunk_id: string; title: string; heading: string | null } | null
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
  const url = `${BASE_URL}/workspaces/${workspaceId}/images/${sourceId}/${encodeURIComponent(imageId)}`
  let res = await fetch(url, { headers: authHeaders() })
  if (res.status === 401 && (await refreshSession())) {
    res = await fetch(url, { headers: authHeaders() })
  }
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
  addUrlSource: (workspaceId: string, url: string) =>
    json<Source>(`/workspaces/${workspaceId}/sources/url`, 'POST', { url }),
  addGithubSource: (workspaceId: string, repoUrl: string) =>
    json<Source>(`/workspaces/${workspaceId}/sources/github`, 'POST', { repo_url: repoUrl }),
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

  listPlans: (workspaceId: string) => request<StudyPlan[]>(`/workspaces/${workspaceId}/plans`),
  updateStage: (planId: string, stageId: string, status: 'pending' | 'done') =>
    json<StudyPlan>(`/plans/${planId}/stages/${stageId}`, 'PATCH', { status }),
  deletePlan: (planId: string) => request<void>(`/plans/${planId}`, { method: 'DELETE' }),

  listQuestions: (workspaceId: string) =>
    request<Question[]>(`/workspaces/${workspaceId}/questions`),
  deleteQuestion: (workspaceId: string, questionId: string) =>
    request<void>(`/workspaces/${workspaceId}/questions/${questionId}`, { method: 'DELETE' }),
}

export interface StageEvent {
  agent: string
  stage: string
  label: string
}

export interface StageResultEvent {
  stage: string
  result: string | null
}

export interface AgentStreamHandlers {
  onStage: (event: StageEvent) => void
  onStageResult: (event: StageResultEvent) => void
  onUsage: (usage: Usage) => void
  onDone: (payload: Record<string, unknown>) => void
  onError: (message: string) => void
}

export interface StreamHandlers extends AgentStreamHandlers {
  onCitations: (citations: Citation[]) => void
  onToken: (delta: string) => void
}

/** POST an SSE endpoint and dispatch its events; shared by every agent run. */
export async function streamAgent(
  path: string,
  body: unknown,
  handlers: AgentStreamHandlers & Partial<Pick<StreamHandlers, 'onCitations' | 'onToken'>>,
): Promise<void> {
  const open = () =>
    fetch(`${BASE_URL}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body),
    })

  let res = await open()
  if (res.status === 401 && (await refreshSession())) {
    res = await open()
  }
  if (!res.ok || !res.body) {
    const errorBody = await res.json().catch(() => null)
    throw new ApiError(errorBody?.detail ?? res.statusText, res.status)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  const parser = new SseParser()

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    for (const { event, data } of parser.push(decoder.decode(value, { stream: true }))) {
      if (event === 'stage') handlers.onStage(JSON.parse(data))
      else if (event === 'stage_result') handlers.onStageResult(JSON.parse(data))
      else if (event === 'citations') handlers.onCitations?.(JSON.parse(data))
      else if (event === 'token') handlers.onToken?.(JSON.parse(data).delta)
      else if (event === 'usage') handlers.onUsage(JSON.parse(data))
      else if (event === 'done') handlers.onDone(JSON.parse(data))
      else if (event === 'error') handlers.onError(JSON.parse(data).message)
    }
  }
}

export async function streamAnswer(
  sessionId: string,
  message: string,
  handlers: StreamHandlers,
): Promise<void> {
  const open = () =>
    fetch(`${BASE_URL}/chat/sessions/${sessionId}/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ message }),
    })

  let res = await open()
  if (res.status === 401 && (await refreshSession())) {
    res = await open()
  }
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
      else if (event === 'usage') handlers.onUsage(JSON.parse(data))
      else if (event === 'done') handlers.onDone(JSON.parse(data).grounded)
      else if (event === 'error') handlers.onError(JSON.parse(data).message)
    }
  }
}
