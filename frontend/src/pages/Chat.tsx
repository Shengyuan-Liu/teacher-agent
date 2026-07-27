import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import { Link, useLocation, useParams } from 'react-router-dom'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import ActivityTrace, { type TraceStep } from '@/components/ActivityTrace'
import CitationRef from '@/components/CitationRef'
import { api, streamAnswer, type Citation, type StageEvent } from '@/lib/api'
import { normaliseMath } from '@/lib/markdown'
import { rehypeCitations } from '@/lib/rehypeCitations'
import 'katex/dist/katex.min.css'

interface Msg {
  role: 'user' | 'assistant'
  content: string
  citations: Citation[] | null
  trace?: TraceStep[]
  streaming?: boolean
}

function stageLabel(e: StageEvent): string {
  switch (e.stage) {
    case 'retrieve':
      return 'Searching material'
    case 'grade':
      return `Checking coverage of ${e.excerpts} excerpts`
    case 'generate':
      return 'Writing answer from material'
    case 'decline':
      return "Material doesn't cover this"
  }
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

function Answer({
  content,
  citations,
  workspaceId,
}: {
  content: string
  citations: Citation[] | null
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
  const bottom = useRef<HTMLDivElement>(null)
  const sentInitial = useRef(false)

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    api.listMessages(sessionId).then((history) => {
      // A late response must not overwrite an answer already streaming in.
      if (cancelled || sentInitial.current) return
      setMessages(
        history.map((m) => ({ role: m.role, content: m.content, citations: m.citations })),
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

  async function send(question: string) {
    if (!question.trim() || streaming || !sessionId) return
    setError('')
    setStreaming(true)
    setMessages((m) => [
      ...m,
      { role: 'user', content: question, citations: null },
      { role: 'assistant', content: '', citations: null, trace: [], streaming: true },
    ])

    let citations: Citation[] = []
    let finished = false
    try {
      await streamAnswer(sessionId, question, {
        onStage: (e) =>
          patchLast((last) => ({
            ...last,
            trace: [
              ...(last.trace ?? []).map((s) => ({ ...s, done: true })),
              { label: stageLabel(e), done: false },
            ],
          })),
        onCitations: (c) => {
          citations = c
        },
        onToken: (delta) => patchLast((last) => ({ ...last, content: last.content + delta })),
        onDone: (grounded) => {
          finished = true
          patchLast((last) => ({
            ...last,
            streaming: false,
            citations: grounded ? citations : null,
            trace: (last.trace ?? []).map((s) => ({ ...s, done: true })),
          }))
        },
        onError: (message) => {
          finished = true
          setError(message)
          patchLast((last) => ({ ...last, streaming: false }))
        },
      })
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
              <Answer content={m.content} citations={m.citations} workspaceId={workspaceId} />
              {m.streaming && <span className="cursor" />}
              {m.citations && m.citations.length > 0 && (
                <CitationChips citations={m.citations} workspaceId={workspaceId} />
              )}
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
