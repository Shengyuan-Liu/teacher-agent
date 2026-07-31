import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { api, type PromptDefinition } from '@/lib/api'

function shortHash(value: string): string {
  return value.slice(0, 12)
}

export default function PromptRegistryPanel({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient()
  const [selectedKey, setSelectedKey] = useState('')
  const [template, setTemplate] = useState('')
  const [notes, setNotes] = useState('')
  const prompts = useQuery({
    queryKey: ['prompts', workspaceId],
    queryFn: () => api.listPrompts(workspaceId),
  })
  const selected = useMemo(
    () => prompts.data?.find((item) => item.key === selectedKey) ?? prompts.data?.[0],
    [prompts.data, selectedKey],
  )
  const active = selected?.versions.find(
    (version) =>
      version.version === selected.active_version && version.source === selected.active_source,
  )
  const selectedPromptKey = selected?.key
  const activeContentHash = selected?.active_content_hash
  const activeTemplate = active?.template

  useEffect(() => {
    if (!selectedPromptKey) return
    setSelectedKey(selectedPromptKey)
    setTemplate(activeTemplate ?? '')
    setNotes('')
  }, [selectedPromptKey, activeContentHash, activeTemplate])

  const refresh = async (definition?: PromptDefinition) => {
    if (definition) {
      queryClient.setQueryData<PromptDefinition[]>(['prompts', workspaceId], (current) =>
        current?.map((item) => (item.key === definition.key ? definition : item)),
      )
    } else {
      await queryClient.invalidateQueries({ queryKey: ['prompts', workspaceId] })
    }
  }
  const create = useMutation({
    mutationFn: () =>
      api.createPromptVersion(workspaceId, selected!.key, { template, notes: notes || undefined }),
    onSuccess: refresh,
  })
  const activate = useMutation({
    mutationFn: (version: number) =>
      api.activatePromptVersion(workspaceId, selected!.key, version),
    onSuccess: refresh,
  })
  const reset = useMutation({
    mutationFn: () => api.resetPromptToBuiltin(workspaceId, selected!.key),
    onSuccess: refresh,
  })
  const error = create.error ?? activate.error ?? reset.error

  if (prompts.isPending) return <p className="muted">Loading prompt registry…</p>
  if (!selected) return <div className="empty">No registered prompts.</div>

  return (
    <section className="prompt-panel">
      <div className="prompt-hero">
        <div>
          <p className="eyebrow">Immutable versions · runtime hashes · replay pins</p>
          <h2>Prompt Registry</h2>
          <p className="muted">
            Draft workspace overrides, activate them atomically, and roll back without changing
            historical runs.
          </p>
        </div>
        <div className="prompt-active">
          <span>Active</span>
          <strong>
            {selected.active_source} v{selected.active_version}
          </strong>
          <code>{shortHash(selected.active_content_hash)}</code>
        </div>
      </div>

      <div className="prompt-layout">
        <nav className="prompt-list" aria-label="Registered prompts">
          {prompts.data?.map((item) => (
            <button
              type="button"
              key={item.key}
              className={item.key === selected.key ? 'selected' : ''}
              onClick={() => setSelectedKey(item.key)}
            >
              <strong>{item.key}</strong>
              <small>
                {item.active_source} v{item.active_version}
              </small>
            </button>
          ))}
        </nav>

        <div className="prompt-editor">
          <div>
            <h3>{selected.key}</h3>
            <p className="muted">{selected.description}</p>
            <p className="prompt-contract">
              Variables:{' '}
              {selected.required_variables.length
                ? selected.required_variables.map((item) => `{${item}}`).join(', ')
                : 'none'}
            </p>
          </div>
          <label>
            New immutable version
            <textarea
              rows={18}
              value={template}
              onChange={(event) => setTemplate(event.target.value)}
              spellCheck={false}
            />
          </label>
          <label>
            Change note
            <input
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="What behavior should this version change?"
            />
          </label>
          <div className="row">
            <button
              type="button"
              disabled={!template.trim() || create.isPending}
              onClick={() => create.mutate()}
            >
              {create.isPending ? 'Saving…' : 'Create draft'}
            </button>
            <button
              type="button"
              className="ghost"
              disabled={selected.active_source === 'builtin' || reset.isPending}
              onClick={() => reset.mutate()}
            >
              Reset to builtin
            </button>
          </div>
          {error && <p className="error">{error instanceof Error ? error.message : 'Request failed'}</p>}

          <div className="prompt-versions">
            <h3>Version history</h3>
            {selected.versions.map((version) => (
              <div key={`${version.source}-${version.version}`} className="prompt-version">
                <div>
                  <strong>
                    {version.source} v{version.version}
                  </strong>
                  <span className={`prompt-status ${version.status}`}>{version.status}</span>
                  <code>{shortHash(version.content_hash)}</code>
                  {version.notes && <p>{version.notes}</p>}
                </div>
                {version.source === 'workspace' && version.status !== 'active' && (
                  <button
                    type="button"
                    className="ghost"
                    disabled={activate.isPending}
                    onClick={() => activate.mutate(version.version)}
                  >
                    Activate
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
