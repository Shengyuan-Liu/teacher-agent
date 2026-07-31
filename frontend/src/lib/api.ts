/**
 * Typed boundary for the REST API and the shared Agent SSE protocol.
 *
 * Stream event names and payloads are an application-level contract, not UI
 * implementation details: Chat, Replay and other Agent surfaces all dispatch
 * through this file so auth refresh and protocol handling cannot drift.
 */

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

export type MemoryKind = 'preference' | 'background' | 'goal'

export interface UserMemory {
  id: string
  kind: MemoryKind
  memory_key: string
  content: string
  confidence: number
  effective_confidence: number
  importance: number
  user_confirmed: boolean
  expires_at: string | null
  last_accessed_at: string | null
  access_count: number
  source_workspace_id: string | null
  created_at: string
  updated_at: string
}

type Provenance = 'user_upload' | 'user_url' | 'user_github' | 'web_search'

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

interface UsageCall {
  step: string
  model: string
  input_tokens: number
  output_tokens: number
  cost_usd: number | null
  prompt?: {
    step: string
    key: string
    version: number
    content_hash: string
    source: 'builtin' | 'workspace'
  } | null
}

export interface Usage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: number | null
  /** false when some call used a model with no configured price */
  priced: boolean
  calls: UsageCall[]
  resource_governance?: {
    policy_version: string
    workspace_scoped: boolean
    budget: {
      enabled: boolean
      limits: {
        max_model_calls: number
        max_tokens: number
        max_cost_usd: number
        soft_ratio: number
      }
      actual: {
        model_calls: number
        input_tokens: number
        output_tokens: number
        cost_usd: number
      }
      projected: {
        model_calls: number
        tokens: number
        cost_usd: number
      }
      reserved_model_calls: number
      cost_fully_enforced: boolean
      downgraded_calls: number
      hard_stop: boolean
      events: TraceResult[]
    }
    cache: {
      enabled: boolean
      hits: number
      misses: number
      bypasses: number
      errors: number
      events: TraceResult[]
    }
    circuit_breaker: {
      enabled: boolean
      events: TraceResult[]
    }
  }
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

interface TraceRecord {
  agent: string
  stage: string
  label: string
  result: TraceResult
  provider?: string
  model?: string
  tier?: 'fast' | 'smart'
  reasoning_effort?: string
}

interface PlanStage {
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

export interface EvalSuite {
  name: string
  description: string
  metrics: string[]
  requires_workspace: boolean
  requires_model: boolean
}

interface EvalCase {
  id: string
  key: string
  position: number
  input: Record<string, unknown>
  expected: Record<string, unknown>
  tags: string[]
  metadata: Record<string, unknown>
  enabled: boolean
}

export interface EvalDataset {
  id: string
  workspace_id: string
  name: string
  description: string | null
  suite: string
  version: number
  default_config: Record<string, unknown>
  thresholds: Record<string, unknown>
  metadata: Record<string, unknown>
  case_count: number
  created_at: string
  cases: EvalCase[] | null
}

interface EvalResult {
  id: string
  case_id: string
  case_key: string
  status: string
  passed: boolean | null
  input: Record<string, unknown>
  expected: Record<string, unknown>
  output: Record<string, unknown>
  scores: Record<string, number>
  details: Record<string, unknown>
  latency_ms: number | null
  input_tokens: number
  output_tokens: number
  cost_usd: number | null
  error: string | null
}

export interface EvalRun {
  id: string
  dataset_id: string
  workspace_id: string
  baseline_run_id: string | null
  suite: string
  label: string
  variant: string | null
  status: 'pending' | 'running' | 'completed' | 'failed'
  config: Record<string, unknown>
  summary: {
    cases?: number
    passed?: number
    errors?: number
    pass_rate?: number
    gate_passed?: boolean
    metrics?: Record<string, number>
    cost_usd?: number | null
    latency_ms?: number
    input_tokens?: number
    output_tokens?: number
  }
  comparison: {
    baseline_run_id?: string | null
    regressions?: string[]
    gate_passed?: boolean
    metrics?: Record<
      string,
      {
        baseline: number | null
        current: number | null
        delta: number | null
        max_regression: number | null
        regressed: boolean
      }
    >
  }
  git_sha: string | null
  error: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
  dataset_name: string
  results: EvalResult[] | null
}

export interface EvalDatasetInput {
  name: string
  description?: string
  suite: string
  version?: number
  default_config?: Record<string, unknown>
  thresholds?: Record<string, unknown>
  metadata?: Record<string, unknown>
  cases: {
    key: string
    input: Record<string, unknown>
    expected: Record<string, unknown>
    tags?: string[]
    metadata?: Record<string, unknown>
    enabled?: boolean
  }[]
}

export interface AgentSpan {
  id: string
  trace_id: string
  span_id: string
  parent_span_id: string | null
  ordinal: number
  name: string
  agent: string
  stage: string
  kind: string
  status: string
  provider: string | null
  model: string | null
  tier: string | null
  reasoning_effort: string | null
  attributes: Record<string, unknown>
  input: Record<string, unknown>
  output: Record<string, unknown>
  input_tokens: number
  output_tokens: number
  cost_usd: number | null
  latency_ms: number | null
  error: string | null
  started_at: string
  completed_at: string | null
}

export interface AgentRun {
  id: string
  workspace_id: string
  session_id: string | null
  replay_of_id: string | null
  trace_id: string
  root_span_id: string
  kind: 'chat' | 'replay' | 'idempotency_replay'
  status: 'running' | 'completed' | 'error' | 'cancelled'
  intent: string | null
  input: Record<string, unknown>
  output: Record<string, unknown>
  model_config: Record<string, unknown>
  usage: Usage | Record<string, never>
  latency_ms: number | null
  error: string | null
  started_at: string
  completed_at: string | null
  created_at: string
  spans: AgentSpan[] | null
  replay_comparison: {
    source_run_id: string
    latency_delta_ms: number | null
    input_tokens_delta: number
    output_tokens_delta: number
    cost_delta_usd: number | null
    output_changed: boolean
    prompts_changed: boolean
  } | null
}

interface ObservabilityBreakdown {
  name: string
  calls: number
  errors: number
  p50_latency_ms: number
  p95_latency_ms: number
  input_tokens: number
  output_tokens: number
  cost_usd: number | null
}

export interface ObservabilitySummary {
  window_hours: number
  runs: number
  completed: number
  errors: number
  success_rate: number
  p50_latency_ms: number
  p95_latency_ms: number
  input_tokens: number
  output_tokens: number
  cost_usd: number | null
  by_agent: ObservabilityBreakdown[]
  by_model: ObservabilityBreakdown[]
}

interface PromptVersion {
  id: string | null
  version: number
  status: 'builtin' | 'draft' | 'active' | 'archived'
  template: string
  variables: string[]
  content_hash: string
  source: 'builtin' | 'workspace'
  notes: string | null
  metadata: Record<string, unknown>
  created_at: string | null
  activated_at: string | null
}

export interface PromptDefinition {
  key: string
  description: string
  required_variables: string[]
  active_version: number
  active_source: 'builtin' | 'workspace'
  active_content_hash: string
  versions: PromptVersion[]
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

  listMemories: (workspaceId: string) =>
    request<UserMemory[]>(`/workspaces/${workspaceId}/memories`),
  createMemory: (
    workspaceId: string,
    body: { kind: MemoryKind; content: string; expires_at?: string | null },
  ) => json<UserMemory>(`/workspaces/${workspaceId}/memories`, 'POST', body),
  updateMemory: (
    memoryId: string,
    body: { kind?: MemoryKind; content?: string; expires_at?: string | null },
  ) => json<UserMemory>(`/memories/${memoryId}`, 'PATCH', body),
  deleteMemory: (memoryId: string) =>
    request<void>(`/memories/${memoryId}`, { method: 'DELETE' }),

  listEvalSuites: (workspaceId: string) =>
    request<EvalSuite[]>(`/workspaces/${workspaceId}/evals/suites`),
  listEvalDatasets: (workspaceId: string) =>
    request<EvalDataset[]>(`/workspaces/${workspaceId}/evals/datasets`),
  createEvalDataset: (workspaceId: string, body: EvalDatasetInput) =>
    json<EvalDataset>(`/workspaces/${workspaceId}/evals/datasets`, 'POST', body),
  createEvalStarter: (workspaceId: string, suite: string) =>
    json<EvalDataset>(`/workspaces/${workspaceId}/evals/datasets/starter`, 'POST', { suite }),
  deleteEvalDataset: (workspaceId: string, datasetId: string) =>
    request<void>(`/workspaces/${workspaceId}/evals/datasets/${datasetId}`, {
      method: 'DELETE',
    }),
  listEvalRuns: (workspaceId: string) =>
    request<EvalRun[]>(`/workspaces/${workspaceId}/evals/runs`),
  getEvalRun: (workspaceId: string, runId: string) =>
    request<EvalRun>(`/workspaces/${workspaceId}/evals/runs/${runId}`),
  createEvalRun: (
    workspaceId: string,
    datasetId: string,
    body: {
      label?: string
      variant?: string
      baseline_run_id?: string
      config?: Record<string, unknown>
    },
  ) =>
    json<EvalRun>(
      `/workspaces/${workspaceId}/evals/datasets/${datasetId}/runs`,
      'POST',
      body,
    ),

  observabilitySummary: (workspaceId: string, hours = 24) =>
    request<ObservabilitySummary>(
      `/workspaces/${workspaceId}/observability/summary?hours=${hours}`,
    ),
  listAgentRuns: (workspaceId: string) =>
    request<AgentRun[]>(`/workspaces/${workspaceId}/observability/runs`),
  getAgentRun: (workspaceId: string, runId: string) =>
    request<AgentRun>(`/workspaces/${workspaceId}/observability/runs/${runId}`),

  listPrompts: (workspaceId: string) =>
    request<PromptDefinition[]>(`/workspaces/${workspaceId}/prompts`),
  createPromptVersion: (
    workspaceId: string,
    key: string,
    body: { template: string; notes?: string; metadata?: Record<string, unknown> },
  ) =>
    json<PromptDefinition>(
      `/workspaces/${workspaceId}/prompts/${encodeURIComponent(key)}/versions`,
      'POST',
      body,
    ),
  activatePromptVersion: (workspaceId: string, key: string, version: number) =>
    request<PromptDefinition>(
      `/workspaces/${workspaceId}/prompts/${encodeURIComponent(key)}/versions/${version}/activate`,
      { method: 'POST' },
    ),
  resetPromptToBuiltin: (workspaceId: string, key: string) =>
    request<PromptDefinition>(
      `/workspaces/${workspaceId}/prompts/${encodeURIComponent(key)}/reset-to-builtin`,
      { method: 'POST' },
    ),

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

interface StageEvent {
  agent: string
  stage: string
  label: string
  provider?: string
  model?: string
  tier?: 'fast' | 'smart'
  reasoning_effort?: string
}

interface StageResultEvent {
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
  // Unknown events are ignored for forward compatibility. Invalid JSON is not:
  // it rejects the stream and exposes a server protocol violation to the caller.
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
async function streamAgent(
  path: string,
  body: unknown,
  handlers: DispatchHandlers,
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
  // Keep requestId unchanged across auth retries. The backend uses it to replay
  // a committed turn or resume the original durable DAG after disconnection.
  return streamAgent(
    `/chat/sessions/${sessionId}/stream`,
    { message, web_search: webSearch, intent, request_id: requestId },
    handlers,
  )
}

export async function replayAgentRun(
  workspaceId: string,
  runId: string,
  handlers: AgentStreamHandlers,
  promptMode: 'current' | 'original' = 'current',
): Promise<void> {
  return streamAgent(
    `/workspaces/${workspaceId}/observability/runs/${runId}/replay/stream`,
    { prompt_mode: promptMode },
    handlers,
  )
}
