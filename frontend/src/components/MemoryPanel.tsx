import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api, type MemoryKind, type UserMemory } from '@/lib/api'

const kindLabels: Record<MemoryKind, string> = {
  preference: 'Preference',
  background: 'Background',
  goal: 'Long-term goal',
}

function dateInput(value: string | null): string {
  return value ? value.slice(0, 10) : ''
}

function MemoryEditor({
  memory,
  onCancel,
  onSave,
  saving,
}: {
  memory: UserMemory
  onCancel: () => void
  onSave: (body: { kind: MemoryKind; content: string; expires_at: string | null }) => void
  saving: boolean
}) {
  const [kind, setKind] = useState(memory.kind)
  const [content, setContent] = useState(memory.content)
  const [expires, setExpires] = useState(dateInput(memory.expires_at))
  return (
    <form
      className="memory-editor"
      onSubmit={(event) => {
        event.preventDefault()
        onSave({
          kind,
          content: content.trim(),
          expires_at: expires ? new Date(`${expires}T23:59:59Z`).toISOString() : null,
        })
      }}
    >
      <select value={kind} onChange={(event) => setKind(event.target.value as MemoryKind)}>
        {Object.entries(kindLabels).map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
      <textarea rows={3} value={content} onChange={(event) => setContent(event.target.value)} />
      <label>
        Expires (optional)
        <input type="date" value={expires} onChange={(event) => setExpires(event.target.value)} />
      </label>
      <div className="memory-actions">
        <button type="submit" disabled={saving || !content.trim()}>
          Save
        </button>
        <button type="button" className="ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}

export default function MemoryPanel({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState<'all' | MemoryKind>('all')
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [newKind, setNewKind] = useState<MemoryKind>('preference')
  const [newContent, setNewContent] = useState('')

  const memories = useQuery({
    queryKey: ['memories', workspaceId],
    queryFn: () => api.listMemories(workspaceId),
  })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['memories', workspaceId] })
  const create = useMutation({
    mutationFn: () => api.createMemory(workspaceId, { kind: newKind, content: newContent.trim() }),
    onSuccess: () => {
      setNewContent('')
      setShowCreate(false)
      invalidate()
    },
  })
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof api.updateMemory>[1] }) =>
      api.updateMemory(id, body),
    onSuccess: () => {
      setEditing(null)
      invalidate()
    },
  })
  const remove = useMutation({
    mutationFn: api.deleteMemory,
    onSuccess: invalidate,
  })

  const visible = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase()
    return (memories.data ?? []).filter(
      (item) =>
        (filter === 'all' || item.kind === filter) &&
        (!needle || item.content.toLocaleLowerCase().includes(needle)),
    )
  }, [filter, memories.data, search])

  return (
    <section className="memory-panel">
      <div className="section-title">
        <div>
          <p className="eyebrow">Cross-session personalization</p>
          <h2>Agent Memory</h2>
          <p className="muted">
            Review everything the Agent may recall. Your edits are trusted over automatic updates.
          </p>
        </div>
        <button type="button" onClick={() => setShowCreate((value) => !value)}>
          Add memory
        </button>
      </div>

      {showCreate && (
        <form
          className="memory-create"
          onSubmit={(event) => {
            event.preventDefault()
            create.mutate()
          }}
        >
          <select value={newKind} onChange={(event) => setNewKind(event.target.value as MemoryKind)}>
            {Object.entries(kindLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <textarea
            rows={2}
            placeholder="What should the Agent remember?"
            value={newContent}
            onChange={(event) => setNewContent(event.target.value)}
          />
          <button type="submit" disabled={!newContent.trim() || create.isPending}>
            Remember
          </button>
        </form>
      )}

      <div className="memory-filters">
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Filter memories…"
        />
        <select value={filter} onChange={(event) => setFilter(event.target.value as typeof filter)}>
          <option value="all">All types</option>
          {Object.entries(kindLabels).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>

      {memories.isPending && <p className="muted">Loading memories…</p>}
      {memories.isError && <p className="error">Could not load Agent memory.</p>}
      {!memories.isPending && !visible.length && (
        <div className="empty">No matching memory. Durable preferences and goals will appear here.</div>
      )}
      <div className="memory-list">
        {visible.map((memory) =>
          editing === memory.id ? (
            <MemoryEditor
              key={memory.id}
              memory={memory}
              saving={update.isPending}
              onCancel={() => setEditing(null)}
              onSave={(body) => update.mutate({ id: memory.id, body })}
            />
          ) : (
            <article className="memory-card" key={memory.id}>
              <div className="memory-card-head">
                <span className={`memory-kind ${memory.kind}`}>{kindLabels[memory.kind]}</span>
                <span className="muted">
                  {Math.round(memory.effective_confidence * 100)}% confidence
                  {memory.user_confirmed ? ' · confirmed by you' : ' · extracted'}
                </span>
              </div>
              <p>{memory.content}</p>
              <div className="memory-card-foot">
                <span className="muted">
                  {memory.expires_at
                    ? `Expires ${new Date(memory.expires_at).toLocaleDateString()}`
                    : 'No expiry'}
                  {memory.access_count ? ` · recalled ${memory.access_count}×` : ''}
                </span>
                <span className="memory-actions">
                  <button type="button" className="link-button" onClick={() => setEditing(memory.id)}>
                    Edit
                  </button>
                  <button
                    type="button"
                    className="link-button danger"
                    onClick={() => {
                      if (confirm('Delete this memory permanently?')) remove.mutate(memory.id)
                    }}
                  >
                    Delete
                  </button>
                </span>
              </div>
            </article>
          ),
        )}
      </div>
    </section>
  )
}
