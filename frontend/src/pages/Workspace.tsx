import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import PlanPanel from '@/components/PlanPanel'
import QuizPanel from '@/components/QuizPanel'
import SourcePanel from '@/components/SourcePanel'
import { api } from '@/lib/api'

function SessionList({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient()
  const sessions = useQuery({
    queryKey: ['sessions', workspaceId],
    queryFn: () => api.listSessions(workspaceId),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteSession(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sessions', workspaceId] }),
  })

  if (sessions.isPending) return <p className="muted">Loading…</p>
  if (!sessions.data?.length)
    return <div className="empty">No chats yet. Ask your first question above.</div>

  return (
    <ul className="list">
      {sessions.data.map((s) => (
        <li key={s.id} className="list-row">
          <Link to={`/w/${workspaceId}/c/${s.id}`}>
            <div className="title">{s.title ?? 'New chat'}</div>
          </Link>
          <span className="muted">{new Date(s.created_at).toLocaleDateString()}</span>
          <button
            className="link-button danger"
            title="Delete chat"
            onClick={() => {
              if (confirm(`Delete chat "${s.title ?? 'New chat'}"?`)) remove.mutate(s.id)
            }}
          >
            ×
          </button>
        </li>
      ))}
    </ul>
  )
}

export default function Workspace() {
  const { id } = useParams<{ id: string }>()
  const [tab, setTab] = useState<'chats' | 'plan' | 'quiz' | 'sources'>('chats')
  const [question, setQuestion] = useState('')
  const navigate = useNavigate()

  const workspaces = useQuery({ queryKey: ['workspaces'], queryFn: api.listWorkspaces })
  const workspace = workspaces.data?.find((w) => w.id === id)

  if (!id) return null

  async function startChat(e: React.FormEvent) {
    e.preventDefault()
    const q = question.trim()
    if (!q) return
    const session = await api.createSession(id!)
    navigate(`/w/${id}/c/${session.id}`, { state: { initial: q } })
  }

  return (
    <main className="shell shell-column">
      <header className="topbar">
        <h1>{workspace?.name ?? '…'}</h1>
        <Link to="/" className="muted">
          ← All workspaces
        </Link>
      </header>

      <div className="tabs">
        {(
          [
            ['chats', 'Chats'],
            ['plan', 'Plan'],
            ['quiz', 'Quiz'],
            ['sources', 'Sources'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={`tab ${tab === key ? 'active' : ''}`}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="shell-body">
        {tab === 'chats' && <SessionList workspaceId={id} />}
        {tab === 'plan' && <PlanPanel workspaceId={id} />}
        {tab === 'quiz' && <QuizPanel workspaceId={id} />}
        {tab === 'sources' && <SourcePanel workspaceId={id} />}
      </div>

      <form className="chat-input row" onSubmit={startChat}>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={`New chat in ${workspace?.name ?? 'this workspace'}…`}
        />
        <button type="submit" disabled={!question.trim()}>
          Ask
        </button>
      </form>
    </main>
  )
}
