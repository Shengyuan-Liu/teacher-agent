import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import { Link, useLocation, useParams } from 'react-router-dom'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import ActivityTrace, { type TraceStep } from '@/components/ActivityTrace'
import ChatArtifact from '@/components/ChatArtifact'
import CitationRef from '@/components/CitationRef'
import UsageNote from '@/components/UsageNote'
import {
  api,
  streamAnswer,
  type Citation,
  type ChatArtifact as Artifact,
  type ChatIntent,
  type Usage,
  type WebCitation,
  type WebSearchSuggestion,
} from '@/lib/api'
import { normaliseMath } from '@/lib/markdown'
import { rehypeCitations } from '@/lib/rehypeCitations'
import 'katex/dist/katex.min.css'

interface Msg {
  role: 'user' | 'assistant'
  content: string
  citations: Citation[] | null
  webCitations?: WebCitation[]
  usedWebSearch?: boolean
  suggestion?: WebSearchSuggestion | null
  usage?: Usage | null
  trace?: TraceStep[]
  streaming?: boolean
  artifact?: Artifact
}

function CitationChips({
  citations,
  workspaceId,
}: {
  citations: Citation[]
  workspaceId?: string
}) {
  return (
    <div className="citations">
      {citations.map((c) => (
        <span key={c.n} className="badge">
          <CitationRef n={c.n} citation={c} workspaceId={workspaceId} /> {c.source_title}
          {c.heading ? ` · ${c.heading}` : ''}
        </span>
      ))}
    </div>
  )
}

function WebCitationChips({ citations }: { citations: WebCitation[] }) {
  return (
    <div className="citations web-citations">
      <span className="badge web-badge">🌐 From the web</span>
      {citations.map((c) => (
        <a key={c.n} className="badge web-cite" href={c.url} target="_blank" rel="noreferrer">
          [{c.n}] {c.domain}
          {c.title ? ` · ${c.title}` : ''}
        </a>
      ))}
    </div>
  )
}

function Answer({
  content,
  citations,
  webCitations,
  workspaceId,
}: {
  content: string
  citations: Citation[] | null
  webCitations?: WebCitation[]
  workspaceId?: string
}) {
  return (
    <div className="md">
      <Markdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex, rehypeCitations]}
        components={{
          cite: ({ node }) => {
            const n = Number((node?.properties as { dataN?: string })?.dataN)
            const web = webCitations?.find((c) => c.n === n)
            if (web) {
              return (
                <a className="cite web" href={web.url} target="_blank" rel="noreferrer" title={web.title}>
                  [{n}]
                </a>
              )
            }
            return (
              <CitationRef
                n={n}
                citation={citations?.find((c) => c.n === n)}
                workspaceId={workspaceId}
              />
            )
          },
        }}
      >
        {normaliseMath(content)}
      </Markdown>
    </div>
  )
}

export default function Chat() {
  const { id: workspaceId, sid: sessionId } = useParams<{ id: string; sid: string }>()
  const initialQuestion = (useLocation().state as { initial?: string } | null)?.initial
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState('')
  const [webEnabled, setWebEnabled] = useState(false)
  const bottom = useRef<HTMLDivElement>(null)
  const sentInitial = useRef(false)

  useEffect(() => {
    api.capabilities().then((c) => setWebEnabled(c.web_search))
  }, [])

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    api.listMessages(sessionId).then((history) => {
      // A late response must not overwrite an answer already streaming in.
      if (cancelled || sentInitial.current) return
      setMessages(
        history.map((m) => ({
          role: m.role,
          content: m.content,
          citations: m.citations,
          webCitations: m.web_citations,
          usedWebSearch: m.used_web_search,
          usage: m.usage,
          trace: (m.trace ?? []).map((t, i) => ({
            key: `${t.stage}-${i}`,
            agent: t.agent,
            label: t.label,
            result: t.result,
            done: true,
            provider: t.provider,
            model: t.model,
            tier: t.tier,
            reasoning_effort: t.reasoning_effort,
          })),
          artifact: m.artifacts,
        })),
      )
      if (initialQuestion) {
        sentInitial.current = true
        send(initialQuestion)
      }
    })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const patchLast = (patch: (last: Msg) => Msg) =>
    setMessages((m) => [...m.slice(0, -1), patch(m[m.length - 1])])

  async function send(question: string, webSearch = false, intent?: ChatIntent) {
    if (!question.trim() || streaming || !sessionId) return
    setError('')
    setStreaming(true)
    setMessages((m) => [
      ...m,
      { role: 'user', content: question, citations: null },
      { role: 'assistant', content: '', citations: null, trace: [], streaming: true },
    ])

    let citations: Citation[] = []
    let webCitations: WebCitation[] = []
    let usedWeb = false
    let finished = false
    try {
      await streamAnswer(
        sessionId,
        question,
        {
        onStage: (e) =>
          patchLast((last) => ({
            ...last,
            trace: [
              ...(last.trace ?? []),
              {
                key: e.stage,
                agent: e.agent,
                label: e.label,
                result: null,
                done: false,
                provider: e.provider,
                model: e.model,
                tier: e.tier,
                reasoning_effort: e.reasoning_effort,
              },
            ],
          })),
        onStageResult: (e) =>
          patchLast((last) => ({
            ...last,
            trace: (last.trace ?? []).map((step) =>
              step.key === e.stage
                ? {
                    ...step,
                    result: e.result,
                    done: true,
                    provider: e.provider ?? step.provider,
                    model: e.model ?? step.model,
                    tier: e.tier ?? step.tier,
                    reasoning_effort: e.reasoning_effort ?? step.reasoning_effort,
                  }
                : step,
            ),
          })),
        onCitations: (c) => {
          citations = c
        },
        onToken: (delta) => patchLast((last) => ({ ...last, content: last.content + delta })),
        onWebCitation: (c) => {
          usedWeb = true
          webCitations = [...webCitations, c]
          patchLast((last) => ({ ...last, webCitations, usedWebSearch: true }))
        },
        onWebSearchSuggested: (s) =>
          patchLast((last) => ({ ...last, suggestion: s })),
        onArtifact: (artifact) => patchLast((last) => ({ ...last, artifact })),
        onUsage: (u) => patchLast((last) => ({ ...last, usage: u })),
        onDone: (payload) => {
          finished = true
          const grounded = Boolean(payload.grounded)
          patchLast((last) => ({
            ...last,
            streaming: false,
            citations: grounded ? citations : null,
            webCitations: usedWeb ? webCitations : [],
            usedWebSearch: usedWeb,
            trace: (last.trace ?? []).map((s) => ({ ...s, done: true })),
          }))
        },
        onError: (message) => {
          finished = true
          setError(message)
          patchLast((last) => ({ ...last, streaming: false }))
        },
        },
        webSearch,
        intent,
      )
      if (!finished) {
        // Stream ended without a done event: connection dropped mid-answer.
        setError('The answer was interrupted. Please try again.')
        patchLast((last) => ({ ...last, streaming: false }))
      }
    } catch (err) {
      setError(String(err))
      setMessages((m) => (m[m.length - 1].content === '' ? m.slice(0, -2) : m))
    } finally {
      setStreaming(false)
    }
  }

  return (
    <main className="chat-shell">
      <header className="topbar">
        <Link to={`/w/${workspaceId}`} className="muted">
          ← Back to workspace
        </Link>
      </header>

      <div className="chat-messages">
        {messages.map((m, i) =>
          m.role === 'user' ? (
            <div key={i} className="msg-user">
              {m.content}
            </div>
          ) : (
            <div key={i} className="msg-assistant">
              {m.trace && m.trace.length > 0 && <ActivityTrace steps={m.trace} />}
              <Answer
                content={m.content}
                citations={m.citations}
                webCitations={m.webCitations}
                workspaceId={workspaceId}
              />
              {m.artifact?.type && workspaceId && (
                <ChatArtifact
                  artifact={m.artifact}
                  workspaceId={workspaceId}
                  disabled={streaming}
                  onChooseIntent={(intent, label) => {
                    const original = messages[i - 1]?.content ?? ''
                    send(`按“${label}”继续处理这个请求：${original}`, intent === 'web', intent)
                  }}
                />
              )}
              {m.streaming && <span className="cursor" />}
              {m.citations && m.citations.length > 0 && (
                <CitationChips citations={m.citations} workspaceId={workspaceId} />
              )}
              {m.webCitations && m.webCitations.length > 0 && (
                <WebCitationChips citations={m.webCitations} />
              )}
              {m.suggestion && webEnabled && !m.streaming && (
                <button
                  type="button"
                  className="web-suggest"
                  disabled={streaming}
                  onClick={() => send(m.suggestion!.suggested_query, true)}
                >
                  🌐 Not in your material — search the web?
                </button>
              )}
              {m.usage && !m.streaming && <UsageNote usage={m.usage} />}
            </div>
          ),
        )}
        <div ref={bottom} />
      </div>

      {error && <p className="error">{error}</p>}
      <form
        className="chat-input row"
        onSubmit={(e) => {
          e.preventDefault()
          const q = input.trim()
          setInput('')
          send(q)
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a question about your material…"
          disabled={streaming}
        />
        <button type="submit" disabled={streaming || !input.trim()}>
          Send
        </button>
      </form>
    </main>
  )
}
