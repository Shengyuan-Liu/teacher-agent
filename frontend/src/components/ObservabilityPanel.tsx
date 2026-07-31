import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, replayAgentRun, type AgentRun, type AgentSpan } from '@/lib/api'

function milliseconds(value: number | null | undefined): string {
  if (value == null) return '—'
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${value.toFixed(0)} ms`
}

function dollars(value: number | null | undefined): string {
  return value == null ? '—' : `$${value.toFixed(4)}`
}

function Waterfall({ run, spans }: { run: AgentRun; spans: AgentSpan[] }) {
  // Bars use wall-clock offsets for concurrency; ordinal remains the stable
  // display order when spans start within the same millisecond.
  const total = Math.max(run.latency_ms ?? 1, 1)
  return (
    <div className="span-waterfall">
      {spans.map((span) => {
        const offset = Math.max(
          0,
          new Date(span.started_at).getTime() - new Date(run.started_at).getTime(),
        )
        const left = Math.min(100, (offset / total) * 100)
        const width = Math.max(1.5, Math.min(100 - left, ((span.latency_ms ?? 0) / total) * 100))
        return (
          <details className={`span-row ${span.status}`} key={span.id}>
            <summary>
              <span className="span-label">
                <strong>{span.agent}</strong>
                <small>{span.stage}</small>
              </span>
              <span className="span-track">
                <i style={{ left: `${left}%`, width: `${width}%` }} />
              </span>
              <span className="span-duration">{milliseconds(span.latency_ms)}</span>
            </summary>
            <div className="span-details">
              <p>
                {span.model ? `${span.model} · ${span.tier ?? 'custom'}` : span.kind}
                {span.reasoning_effort ? ` · reasoning ${span.reasoning_effort}` : ''}
              </p>
              <p>
                {span.input_tokens + span.output_tokens} tokens · {dollars(span.cost_usd)}
              </p>
              {span.error && <p className="error">{span.error}</p>}
              {Object.keys(span.output).length > 0 && (
                <pre>{JSON.stringify(span.output, null, 2)}</pre>
              )}
            </div>
          </details>
        )
      })}
    </div>
  )
}

/** Durable trace browser and isolated replay launcher for one workspace. */
export default function ObservabilityPanel({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [replayStage, setReplayStage] = useState('')
  const [promptMode, setPromptMode] = useState<'current' | 'original'>('current')

  const summary = useQuery({
    queryKey: ['observability-summary', workspaceId],
    queryFn: () => api.observabilitySummary(workspaceId),
  })
  const runs = useQuery({
    queryKey: ['agent-runs', workspaceId],
    queryFn: () => api.listAgentRuns(workspaceId),
    refetchInterval: (query) =>
      query.state.data?.some((run) => run.status === 'running') ? 1500 : false,
  })
  const detail = useQuery({
    queryKey: ['agent-run', workspaceId, selectedId],
    queryFn: () => api.getAgentRun(workspaceId, selectedId!),
    enabled: Boolean(selectedId),
  })
  const replay = useMutation({
    mutationFn: (runId: string) =>
      replayAgentRun(
        workspaceId,
        runId,
        {
          onStage: (event) => setReplayStage(event.label),
          onStageResult: () => undefined,
          onUsage: () => undefined,
          onDone: () => setReplayStage('Replay completed'),
          onError: (message) => {
            throw new Error(message)
          },
        },
        promptMode,
      ),
    onSuccess: async (_data, sourceRunId) => {
      const fresh = await api.listAgentRuns(workspaceId)
      queryClient.setQueryData(['agent-runs', workspaceId], fresh)
      queryClient.invalidateQueries({ queryKey: ['observability-summary', workspaceId] })
      const replayed = fresh.find((run) => run.replay_of_id === sourceRunId)
      if (replayed) setSelectedId(replayed.id)
    },
  })

  const selected = detail.data
  return (
    <section className="observability-panel">
      <div className="observability-hero">
        <div>
          <p className="eyebrow">OpenTelemetry + durable traces</p>
          <h2>Agent Observability</h2>
          <p className="muted">
            Inspect latency, models, tokens, cost and failures, then replay the exact input.
          </p>
        </div>
        <a className="jaeger-link" href="http://localhost:16686" target="_blank" rel="noreferrer">
          Open Jaeger ↗
        </a>
      </div>

      <div className="observability-cards">
        <div>
          <span>Runs · 24h</span>
          <strong>{summary.data?.runs ?? 0}</strong>
        </div>
        <div>
          <span>Success rate</span>
          <strong>{((summary.data?.success_rate ?? 0) * 100).toFixed(1)}%</strong>
        </div>
        <div>
          <span>P95 latency</span>
          <strong>{milliseconds(summary.data?.p95_latency_ms)}</strong>
        </div>
        <div>
          <span>Total cost</span>
          <strong>{dollars(summary.data?.cost_usd)}</strong>
        </div>
      </div>

      <div className="observability-breakdowns">
        <div>
          <h3>By agent</h3>
          {summary.data?.by_agent.map((row) => (
            <div className="breakdown-row" key={row.name}>
              <strong>{row.name}</strong>
              <span>{row.calls} calls</span>
              <span>P95 {milliseconds(row.p95_latency_ms)}</span>
              <span>{dollars(row.cost_usd)}</span>
            </div>
          ))}
        </div>
        <div>
          <h3>By model</h3>
          {summary.data?.by_model.map((row) => (
            <div className="breakdown-row" key={row.name}>
              <strong>{row.name}</strong>
              <span>{row.calls} calls</span>
              <span>{row.input_tokens + row.output_tokens} tokens</span>
              <span>{dollars(row.cost_usd)}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="observability-runs">
        <div className="section-title">
          <h3>Recent traces</h3>
          <span className="muted">Trace IDs map directly to OTLP/Jaeger</span>
        </div>
        {!runs.data?.length && <div className="empty">Chat once to create the first trace.</div>}
        {runs.data?.map((run) => (
          <button
            type="button"
            className={`observability-run ${selectedId === run.id ? 'selected' : ''}`}
            key={run.id}
            onClick={() => setSelectedId(run.id)}
          >
            <span className={`run-dot ${run.status}`} />
            <span>
              <strong>{run.intent ?? 'routing'}</strong>
              <small>
                {run.kind} · {run.trace_id.slice(0, 12)}
              </small>
            </span>
            <span>{milliseconds(run.latency_ms)}</span>
            <span>{dollars('cost_usd' in run.usage ? run.usage.cost_usd : null)}</span>
            <time>{new Date(run.started_at).toLocaleTimeString()}</time>
          </button>
        ))}
      </div>

      {selected && (
        <div className="trace-detail">
          <div className="section-title">
            <div>
              <h3>{selected.intent ?? 'Agent turn'}</h3>
              <p className="muted">
                trace {selected.trace_id} · root {selected.root_span_id}
              </p>
            </div>
            <div className="replay-controls">
              <select
                value={promptMode}
                onChange={(event) =>
                  setPromptMode(event.target.value as 'current' | 'original')
                }
                aria-label="Replay prompt versions"
              >
                <option value="current">Current prompts</option>
                <option value="original">Original prompts</option>
              </select>
              <button
                type="button"
                disabled={replay.isPending || selected.status !== 'completed'}
                onClick={() => {
                  setReplayStage('Starting replay…')
                  replay.mutate(selected.id)
                }}
              >
                {replay.isPending ? replayStage || 'Replaying…' : 'Replay input'}
              </button>
            </div>
          </div>
          {selected.replay_comparison && (
            <div className="replay-comparison">
              <strong>Replay comparison</strong>
              <span>Latency Δ {milliseconds(selected.replay_comparison.latency_delta_ms)}</span>
              <span>Tokens Δ {selected.replay_comparison.output_tokens_delta}</span>
              <span>Cost Δ {dollars(selected.replay_comparison.cost_delta_usd)}</span>
              <span>
                Output {selected.replay_comparison.output_changed ? 'changed' : 'unchanged'}
              </span>
              <span>
                Prompts {selected.replay_comparison.prompts_changed ? 'changed' : 'unchanged'}
              </span>
            </div>
          )}
          {replay.error && (
            <p className="error">
              {replay.error instanceof Error ? replay.error.message : 'Replay failed'}
            </p>
          )}
          <Waterfall run={selected} spans={selected.spans ?? []} />
        </div>
      )}
    </section>
  )
}
