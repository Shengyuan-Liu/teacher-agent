import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type WebSearchCandidate } from '@/lib/api'

/**
 * Form B: search the web, then let the user pick which results to ingest.
 * Nothing is added until "Add" is pressed — that confirmation is the gate that
 * keeps search and ingestion from ever running as one unattended step.
 */
export default function WebSearchPanel({
  workspaceId,
  onIngested,
}: {
  workspaceId: string
  onIngested: () => void
}) {
  const [query, setQuery] = useState('')
  const [candidates, setCandidates] = useState<WebSearchCandidate[] | null>(null)
  const [queriesUsed, setQueriesUsed] = useState<string[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const search = useMutation({
    mutationFn: () => api.webSearch(workspaceId, { query: query.trim() }),
    onSuccess: (res) => {
      setCandidates(res.results)
      setQueriesUsed(res.queries_used)
      setSelected(new Set(res.results.filter((r) => r.recommended).map((r) => r.url)))
    },
  })

  const ingest = useMutation({
    mutationFn: () =>
      api.webSearchIngest(
        workspaceId,
        candidates!.filter((c) => selected.has(c.url)).map((c) => ({ url: c.url, title: c.title })),
        query.trim(),
      ),
    onSuccess: () => {
      setCandidates(null)
      setSelected(new Set())
      setQuery('')
      onIngested()
    },
  })

  const toggle = (url: string) =>
    setSelected((s) => {
      const next = new Set(s)
      if (next.has(url)) next.delete(url)
      else next.add(url)
      return next
    })

  return (
    <div className="web-search-panel">
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault()
          if (query.trim()) search.mutate()
        }}
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search the web for material to add…"
          autoFocus
        />
        <button type="submit" disabled={search.isPending || !query.trim()}>
          {search.isPending ? 'Searching…' : 'Search'}
        </button>
      </form>
      {search.isError && <p className="error small">{String(search.error)}</p>}

      {candidates && (
        <>
          {queriesUsed.length > 0 && (
            <p className="muted small">Searched: {queriesUsed.join(' · ')}</p>
          )}
          {candidates.length === 0 && <p className="muted">No results.</p>}
          <ul className="list web-candidates">
            {candidates.map((c) => (
              <li key={c.url} className="list-row">
                <input
                  type="checkbox"
                  checked={selected.has(c.url)}
                  onChange={() => toggle(c.url)}
                />
                <div style={{ flex: 1 }}>
                  <a href={c.url} target="_blank" rel="noreferrer" className="title">
                    {c.title}
                  </a>
                  <div className="muted small">
                    {c.domain}
                    {c.recommended && c.reason ? ` · ✓ ${c.reason}` : ''}
                  </div>
                </div>
              </li>
            ))}
          </ul>
          {candidates.length > 0 && (
            <button disabled={ingest.isPending || selected.size === 0} onClick={() => ingest.mutate()}>
              {ingest.isPending ? 'Adding…' : `Add ${selected.size} to workspace`}
            </button>
          )}
          {ingest.isError && <p className="error small">{String(ingest.error)}</p>}
        </>
      )}
    </div>
  )
}
