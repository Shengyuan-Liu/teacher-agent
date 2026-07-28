import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { api, type Source } from '@/lib/api'

const ACTIVE = new Set(['pending', 'parsing', 'embedding'])

function StatusBadge({ status }: { status: Source['status'] }) {
  const kind = status === 'ready' ? 'ok' : status === 'failed' ? 'bad' : 'busy'
  return <span className={`badge ${kind}`}>{status}</span>
}

export default function SourcePanel({ workspaceId }: { workspaceId: string }) {
  const fileInput = useRef<HTMLInputElement>(null)
  const [adding, setAdding] = useState<'url' | 'github' | null>(null)
  const [location, setLocation] = useState('')
  const queryClient = useQueryClient()

  const sources = useQuery({
    queryKey: ['sources', workspaceId],
    queryFn: () => api.listSources(workspaceId),
    refetchInterval: (query) =>
      query.state.data?.some((s) => ACTIVE.has(s.status)) ? 1500 : false,
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['sources', workspaceId] })

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadSource(workspaceId, file),
    onSuccess: refresh,
  })
  const addRemote = useMutation({
    mutationFn: (value: string) =>
      adding === 'github'
        ? api.addGithubSource(workspaceId, value)
        : api.addUrlSource(workspaceId, value),
    onSuccess: () => {
      setAdding(null)
      setLocation('')
      refresh()
    },
  })
  const retry = useMutation({
    mutationFn: (id: string) => api.retrySource(workspaceId, id),
    onSuccess: refresh,
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteSource(workspaceId, id),
    onSuccess: refresh,
  })

  return (
    <section>
      <input
        ref={fileInput}
        type="file"
        accept=".pdf,.md,.markdown,.docx,.pptx,.xlsx"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) upload.mutate(file)
          e.target.value = ''
        }}
      />
      <div className="row" style={{ flexWrap: 'wrap', gap: '0.5rem' }}>
        <button
          className="ghost"
          onClick={() => fileInput.current?.click()}
          disabled={upload.isPending}
        >
          {upload.isPending ? 'Uploading…' : '+ Upload file'}
        </button>
        <button className="ghost" onClick={() => setAdding(adding === 'url' ? null : 'url')}>
          + Website
        </button>
        <button className="ghost" onClick={() => setAdding(adding === 'github' ? null : 'github')}>
          + GitHub repo
        </button>
      </div>
      {adding && (
        <form
          className="row"
          style={{ marginTop: '0.6rem' }}
          onSubmit={(e) => {
            e.preventDefault()
            if (location.trim()) addRemote.mutate(location.trim())
          }}
        >
          <input
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            placeholder={
              adding === 'github'
                ? 'https://github.com/owner/repo'
                : 'https://docs.example.com/guide/'
            }
            autoFocus
          />
          <button type="submit" disabled={addRemote.isPending || !location.trim()}>
            {addRemote.isPending ? 'Adding…' : 'Add'}
          </button>
        </form>
      )}
      {[upload, addRemote, retry, remove]
        .filter((m) => m.isError)
        .map((m, i) => (
          <p key={i} className="error">
            {String(m.error)}
          </p>
        ))}

      {sources.data?.length === 0 ? (
        <div className="empty" style={{ marginTop: '1rem' }}>
          Give the assistant something to teach from — upload a PDF or Markdown file.
        </div>
      ) : (
        <ul className="list" style={{ marginTop: '0.5rem' }}>
          {sources.data?.map((s) => (
            <li key={s.id}>
              <div className="list-row">
                <span className="title" style={{ flex: 1 }} title={s.title}>
                  {s.title}
                </span>
                <StatusBadge status={s.status} />
                {s.status !== 'ready' && (
                  <button className="link-button" onClick={() => retry.mutate(s.id)}>
                    Retry
                  </button>
                )}
                <button className="link-button danger" onClick={() => remove.mutate(s.id)}>
                  ×
                </button>
              </div>
              {ACTIVE.has(s.status) && (
                <div className="ingest">
                  <div className="ingest-track">
                    <div
                      className="ingest-bar"
                      style={{ width: `${Math.max(2, s.progress * 100)}%` }}
                    />
                  </div>
                  <span className="muted">
                    {s.progress_detail ?? 'Queued'} · {Math.round(s.progress * 100)}%
                  </span>
                </div>
              )}
              {s.error && <p className="error small">{s.error}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
