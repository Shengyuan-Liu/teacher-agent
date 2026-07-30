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

export type Provenance = 'user_upload' | 'user_url' | 'user_github' | 'web_search'

export interface Source {
  id: string
  type: 'pdf' | 'md' | 'docx' | 'pptx' | 'xlsx' | 'url' | 'github'
  title: string
  status: 'pending' | 'parsing' | 'embedding' | 'ready' | 'failed'
  origin: string | null
  provenance: Provenance
  search_query: string | null
  fetched_at: string | null
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
  source_type?: Source['type'] | null
  source_origin?: string | null
  source_url?: string | null
  source_position?: number | null
  page_start?: number | null
  page_end?: number | null
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

export interface WebCitation {
  n: number
  url: string
  title: string
  domain: string
  fetched_at: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: Citation[] | null
  web_citations: WebCitation[]
  used_web_search: boolean
  usage: Usage | null
  trace: TraceRecord[] | null
  artifacts: ChatArtifact
  created_at: string
}

export interface LectureSummary {
  id: string
  workspace_id: string
  chat_session_id: string
  plan_stage_id: string | null
  title: string
  scope: string
  status: 'active' | 'waiting_check' | 'paused' | 'completed' | 'cancelled'
  current_section_index: number
  total_sections: number
  created_at: string
  updated_at: string
  completed_at: string | null
}

export type ChatIntent =
  | 'qa'
  | 'web'
  | 'quiz'
  | 'test'
  | 'review'
  | 'progress'
  | 'plan'
  | 'explain'
  | 'lecture'

export type ChatArtifact = Record<string, unknown> & { type?: string }

export interface Capabilities {
  web_search: boolean
  llm_provider: string
  llm_models: Record<'fast' | 'smart', string>
  embedding_provider: string
  limits: Record<string, number>
}

export interface WebSearchCandidate {
  url: string
  title: string
  snippet: string
  domain: string
  recommended: boolean
  reason: string | null
}

export interface WebSearchResult {
  queries_used: string[]
  results: WebSearchCandidate[]
}

export type TraceResult =
  | string
  | number
  | boolean
  | null
  | TraceResult[]
  | { [key: string]: TraceResult }

export interface TraceRecord {
  agent: string
  stage: string
  label: string
  result: TraceResult
  provider?: string
  model?: string
  tier?: 'fast' | 'smart'
  reasoning_effort?: string
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

export interface AssessmentQuestion {
  id: string
  position: number
  points: number
  type: Question['type']
  difficulty: string
  stem: string
  options: string[] | null
  source: Question['source']
  response?: unknown
  score_fraction?: number | null
  correct?: boolean | null
  feedback?: string | null
  grader?: string | null
  grader_model?: string | null
  answer?: string | string[] | null
  explanation?: string | null
}

export interface Assessment {
  id: string
  title: string
  status: 'in_progress' | 'submitted' | 'timed_out'
  time_limit_minutes: number
  started_at: string
  submitted_at: string | null
  score: number | null
  max_score: number
  created_at: string
  questions: AssessmentQuestion[]
}

export type AssessmentSummary = Omit<Assessment, 'questions'>

export interface ReviewItem {
  id: string
  topic: string
  due_at: string
  interval_days: number
  repetitions: number
  last_correct: boolean | null
  question: Omit<AssessmentQuestion, 'id' | 'position' | 'points'>
}

export interface ReviewResult {
  item: ReviewItem
  score_fraction: number
  correct: boolean
  feedback: string
  grader: string
  grader_model: string | null
}

export interface TopicMastery {
  topic: string
  score: number
  attempts: number
  correct_count: number
  last_evidence: number
  updated_at: string
}

export interface KnowledgeGraph {
  nodes: { id: string; title: string; mastery: number | null }[]
  edges: { from: string; to: string }[]
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

export async function fetchSourceFile(
  workspaceId: string,
  sourceId: string,
  allowRefresh = true,
): Promise<Blob> {
  const path = `/workspaces/${workspaceId}/sources/${sourceId}/content`
  const res = await fetch(`${BASE_URL}${path}`, { headers: authHeaders() })
  if (res.status === 401 && allowRefresh && (await refreshSession())) {
    return fetchSourceFile(workspaceId, sourceId, false)
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
  listLectures: (workspaceId: string) =>
    request<LectureSummary[]>(`/workspaces/${workspaceId}/lectures`),

  listPlans: (workspaceId: string) => request<StudyPlan[]>(`/workspaces/${workspaceId}/plans`),
  updateStage: (planId: string, stageId: string, status: 'pending' | 'done') =>
    json<StudyPlan>(`/plans/${planId}/stages/${stageId}`, 'PATCH', { status }),
  deletePlan: (planId: string) => request<void>(`/plans/${planId}`, { method: 'DELETE' }),

  listQuestions: (workspaceId: string) =>
    request<Question[]>(`/workspaces/${workspaceId}/questions`),
  deleteQuestion: (workspaceId: string, questionId: string) =>
    request<void>(`/workspaces/${workspaceId}/questions/${questionId}`, { method: 'DELETE' }),

  createAssessment: (
    workspaceId: string,
    body: { title: string; count: number; time_limit_minutes: number; topic?: string | null },
  ) => json<Assessment>(`/workspaces/${workspaceId}/assessments`, 'POST', body),
  listAssessments: (workspaceId: string) =>
    request<AssessmentSummary[]>(`/workspaces/${workspaceId}/assessments`),
  getAssessment: (workspaceId: string, assessmentId: string) =>
    request<Assessment>(`/workspaces/${workspaceId}/assessments/${assessmentId}`),
  submitAssessment: (
    workspaceId: string,
    assessmentId: string,
    answers: Record<string, unknown>,
  ) =>
    json<Assessment>(
      `/workspaces/${workspaceId}/assessments/${assessmentId}/submit`,
      'POST',
      { answers },
    ),
  listReviews: (workspaceId: string, dueOnly = true) =>
    request<ReviewItem[]>(`/workspaces/${workspaceId}/reviews?due_only=${dueOnly}`),
  answerReview: (workspaceId: string, reviewId: string, response: unknown) =>
    json<ReviewResult>(`/workspaces/${workspaceId}/reviews/${reviewId}/answer`, 'POST', {
      response,
    }),
  listMastery: (workspaceId: string) =>
    request<TopicMastery[]>(`/workspaces/${workspaceId}/mastery`),

  capabilities: () => request<Capabilities>('/capabilities'),
  webSearch: (workspaceId: string, body: { query?: string; from_question?: string }) =>
    json<WebSearchResult>(`/workspaces/${workspaceId}/web-search`, 'POST', body),
  webSearchIngest: (
    workspaceId: string,
    results: { url: string; title?: string }[],
    query?: string,
  ) =>
    json<{ source_ids: string[] }>(`/workspaces/${workspaceId}/web-search/ingest`, 'POST', {
      results,
      query,
    }),
}

export interface StageEvent {
  agent: string
  stage: string
  label: string
  provider?: string
  model?: string
  tier?: 'fast' | 'smart'
  reasoning_effort?: string
}

export interface StageResultEvent {
  stage: string
  result: TraceResult
  provider?: string
  model?: string
  tier?: 'fast' | 'smart'
  reasoning_effort?: string
}

export interface AgentStreamHandlers {
  onStage: (event: StageEvent) => void
  onStageResult: (event: StageResultEvent) => void
  onUsage: (usage: Usage) => void
  onDone: (payload: Record<string, unknown>) => void
  onError: (message: string) => void
}

export interface WebSearchSuggestion {
  reason: string
  suggested_query: string
}

export interface StreamHandlers extends AgentStreamHandlers {
  onCitations: (citations: Citation[]) => void
  onToken: (delta: string) => void
  onWebCitation: (citation: WebCitation) => void
  onWebSearchSuggested: (suggestion: WebSearchSuggestion) => void
  onArtifact?: (artifact: ChatArtifact) => void
}

type DispatchHandlers = AgentStreamHandlers &
  Partial<
    Pick<
      StreamHandlers,
      'onCitations' | 'onToken' | 'onWebCitation' | 'onWebSearchSuggested' | 'onArtifact'
    >
  >

export function dispatchStreamEvent(
  event: string,
  data: string,
  handlers: DispatchHandlers,
): void {
  const payload = JSON.parse(data)
  if (event === 'stage') handlers.onStage(payload)
  else if (event === 'stage_result') handlers.onStageResult(payload)
  else if (event === 'citations') handlers.onCitations?.(payload)
  else if (event === 'token') handlers.onToken?.(payload.delta)
  else if (event === 'web_citation') handlers.onWebCitation?.(payload)
  else if (event === 'web_search_suggested') handlers.onWebSearchSuggested?.(payload)
  else if (event === 'artifact') handlers.onArtifact?.(payload)
  else if (event === 'usage') handlers.onUsage(payload)
  else if (event === 'done') handlers.onDone(payload)
  else if (event === 'error') handlers.onError(payload.message)
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
      dispatchStreamEvent(event, data, handlers)
    }
  }
}

export async function streamAnswer(
  sessionId: string,
  message: string,
  handlers: StreamHandlers,
  webSearch = false,
  intent?: ChatIntent,
  requestId?: string,
): Promise<void> {
  const open = () =>
    fetch(`${BASE_URL}/chat/sessions/${sessionId}/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ message, web_search: webSearch, intent, request_id: requestId }),
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
      dispatchStreamEvent(event, data, handlers)
    }
  }
}
