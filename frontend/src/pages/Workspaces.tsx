import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'

export default function Workspaces() {
  const [name, setName] = useState('')
  const queryClient = useQueryClient()
  const { email, clear } = useAuth()

  const workspaces = useQuery({ queryKey: ['workspaces'], queryFn: api.listWorkspaces })

  const create = useMutation({
    mutationFn: () => api.createWorkspace(name.trim()),
    onSuccess: () => {
      setName('')
      queryClient.invalidateQueries({ queryKey: ['workspaces'] })
    },
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteWorkspace(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
  })

  return (
    <main className="shell">
      <header className="topbar">
        <h1>Workspaces</h1>
        <span className="muted">
          {email} ·{' '}
          <button className="link-button" onClick={clear}>
            Log out
          </button>
        </span>
      </header>

      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault()
          if (name.trim()) create.mutate()
        }}
      >
        <input
          placeholder="New workspace, e.g. Operating Systems"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button type="submit" disabled={create.isPending || !name.trim()}>
          Create
        </button>
      </form>
      {create.isError && <p className="error">{String(create.error)}</p>}

      <div style={{ marginTop: '1.5rem' }}>
        {workspaces.isPending && <p className="muted">Loading…</p>}
        {workspaces.data?.length === 0 && (
          <div className="empty">
            No workspaces yet. Create one, upload a PDF or Markdown file, and start asking.
          </div>
        )}
        <ul className="list">
          {workspaces.data?.map((ws) => (
            <li key={ws.id} className="list-row">
              <Link to={`/w/${ws.id}`}>
                <div className="title">{ws.name}</div>
                {ws.description && <div className="muted">{ws.description}</div>}
              </Link>
              <button
                className="link-button danger"
                onClick={() => {
                  if (confirm(`Delete workspace "${ws.name}" and everything in it?`))
                    remove.mutate(ws.id)
                }}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      </div>
    </main>
  )
}
