import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { ApiError, api, type EvalDatasetInput, type EvalRun } from '@/lib/api'

const COORDINATION_VARIANTS = [
  'single_agent',
  'typed_dag',
  'sequential_dag',
  'no_synthesis',
] as const

function formatMetric(value: number | undefined): string {
  return value === undefined ? '—' : value.toFixed(3)
}

function statusLabel(run: EvalRun): string {
  if (run.status === 'pending') return 'Queued'
  if (run.status === 'running') return 'Running'
  if (run.status === 'failed') return 'Execution failed'
  return run.summary.gate_passed === false ? 'Regression' : 'Passed'
}

function JsonImport({
  workspaceId,
  onCreated,
}: {
  workspaceId: string
  onCreated: () => void
}) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [suite, setSuite] = useState('router_contract')
  const [cases, setCases] = useState(
    '[\n  {\n    "key": "case-1",\n    "input": {},\n    "expected": {},\n    "tags": ["golden"]\n  }\n]',
  )
  const [parseError, setParseError] = useState('')

  const create = useMutation({
    mutationFn: (body: EvalDatasetInput) => api.createEvalDataset(workspaceId, body),
    onSuccess: () => {
      setName('')
      setOpen(false)
      setParseError('')
      onCreated()
    },
  })

  function submit(e: React.FormEvent) {
    e.preventDefault()
    setParseError('')
    try {
      const parsed = JSON.parse(cases)
      if (!Array.isArray(parsed) || !parsed.length) throw new Error('Cases must be a non-empty array')
      create.mutate({
        name: name.trim(),
        suite,
        cases: parsed,
        thresholds: { min_scores: { contract_accuracy: 1 } },
        metadata: { source: 'dashboard-import' },
      })
    } catch (error) {
      setParseError(error instanceof Error ? error.message : 'Invalid JSON')
    }
  }

  if (!open) {
    return (
      <button className="ghost" type="button" onClick={() => setOpen(true)}>
        Import custom dataset
      </button>
    )
  }
  return (
    <form className="eval-import" onSubmit={submit}>
      <div className="eval-import-head">
        <strong>Import versioned golden set</strong>
        <button className="link-button" type="button" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
      <div className="eval-fields">
        <label>
          Name
          <input value={name} onChange={(event) => setName(event.target.value)} required />
        </label>
        <label>
          Suite
          <select value={suite} onChange={(event) => setSuite(event.target.value)}>
            <option value="router_contract">Router contract</option>
            <option value="structured_output">Structured output</option>
            <option value="rag_retrieval">RAG retrieval</option>
            <option value="multi_agent_coordination">Multi-agent coordination</option>
            <option value="agent_security">Agent security red team</option>
            <option value="resource_governance">Resource governance</option>
          </select>
        </label>
      </div>
      <label>
        Cases JSON
        <textarea rows={10} value={cases} onChange={(event) => setCases(event.target.value)} />
      </label>
      {(parseError || create.error) && (
        <p className="error">
          {parseError ||
            (create.error instanceof ApiError ? create.error.message : 'Could not import dataset')}
        </p>
      )}
      <button type="submit" disabled={!name.trim() || create.isPending}>
        {create.isPending ? 'Importing…' : 'Import dataset'}
      </button>
    </form>
  )
}

/** Evaluation control plane: versioned datasets, durable runs and baseline gates. */
export default function EvaluationPanel({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient()
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)

  const suites = useQuery({
    queryKey: ['eval-suites', workspaceId],
    queryFn: () => api.listEvalSuites(workspaceId),
  })
  const datasets = useQuery({
    queryKey: ['eval-datasets', workspaceId],
    queryFn: () => api.listEvalDatasets(workspaceId),
  })
  const runs = useQuery({
    queryKey: ['eval-runs', workspaceId],
    queryFn: () => api.listEvalRuns(workspaceId),
    refetchInterval: (query) =>
      query.state.data?.some((run) => run.status === 'pending' || run.status === 'running')
        ? 1500
        : false,
  })
  const selectedRun = useQuery({
    queryKey: ['eval-run', workspaceId, selectedRunId],
    queryFn: () => api.getEvalRun(workspaceId, selectedRunId!),
    enabled: Boolean(selectedRunId),
    refetchInterval: (query) => {
      const run = query.state.data
      return run?.status === 'pending' || run?.status === 'running' ? 1200 : false
    },
  })

  const completedByVariant = useMemo(() => {
    // A baseline is comparable only within the same dataset, strategy and
    // execution mode; mixing deterministic and live runs would create fake deltas.
    const result = new Map<string, EvalRun>()
    for (const run of runs.data ?? []) {
      const mode = String(run.config.execution_mode ?? 'deterministic')
      const key = `${run.dataset_id}:${run.variant ?? 'default'}:${mode}`
      if (run.status === 'completed' && !result.has(key)) {
        result.set(key, run)
      }
    }
    return result
  }, [runs.data])

  const benchmarkDataset = datasets.data?.find(
    (dataset) => dataset.suite === 'multi_agent_coordination',
  )
  const benchmarkRuns = useMemo(() => {
    if (!benchmarkDataset) return []
    const latest = new Map<string, EvalRun>()
    for (const run of runs.data ?? []) {
      if (
        run.dataset_id === benchmarkDataset.id &&
        run.status === 'completed' &&
        run.variant &&
        !latest.has(run.variant)
      ) {
        latest.set(run.variant, run)
      }
    }
    return COORDINATION_VARIANTS.flatMap((variant) => {
      const run = latest.get(variant)
      return run ? [run] : []
    })
  }, [benchmarkDataset, runs.data])

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['eval-datasets', workspaceId] })
    queryClient.invalidateQueries({ queryKey: ['eval-runs', workspaceId] })
  }

  const starter = useMutation({
    mutationFn: (suite: string) => api.createEvalStarter(workspaceId, suite),
    onSuccess: refresh,
  })
  const runDataset = useMutation({
    mutationFn: async (datasetId: string) => {
      const dataset = datasets.data?.find((item) => item.id === datasetId)
      const variant =
        dataset?.suite === 'rag_retrieval'
          ? 'hybrid_rrf_rerank'
          : dataset?.suite === 'multi_agent_coordination'
            ? 'typed_dag'
            : undefined
      const mode = String(dataset?.default_config.execution_mode ?? 'deterministic')
      const baseline = completedByVariant.get(
        `${datasetId}:${variant ?? 'default'}:${mode}`,
      )
      return api.createEvalRun(workspaceId, datasetId, {
        label: `Dashboard run ${new Date().toISOString()}`,
        baseline_run_id: baseline?.id,
        variant,
        config:
          dataset?.suite === 'multi_agent_coordination'
            ? { execution_mode: mode }
            : undefined,
      })
    },
    onSuccess: (run) => {
      setSelectedRunId(run.id)
      queryClient.invalidateQueries({ queryKey: ['eval-runs', workspaceId] })
    },
  })
  const runMatrix = useMutation({
    mutationFn: async ({
      datasetId,
      executionMode,
    }: {
      datasetId: string
      executionMode: 'deterministic' | 'live'
    }) => {
      const stamp = new Date().toISOString()
      // Launch the matrix from one dataset snapshot and timestamp so strategy
      // differences are not confounded with edited cases or labels.
      return Promise.all(
        COORDINATION_VARIANTS.map((variant) => {
          const baseline = completedByVariant.get(
            `${datasetId}:${variant}:${executionMode}`,
          )
          return api.createEvalRun(workspaceId, datasetId, {
            label: `${executionMode} ablation · ${variant} · ${stamp}`,
            variant,
            baseline_run_id: baseline?.id,
            config: { execution_mode: executionMode },
          })
        }),
      )
    },
    onSuccess: (matrix) => {
      const full = matrix.find((run) => run.variant === 'typed_dag')
      setSelectedRunId(full?.id ?? matrix[0]?.id ?? null)
      queryClient.invalidateQueries({ queryKey: ['eval-runs', workspaceId] })
    },
  })

  return (
    <section className="eval-panel">
      <div className="eval-hero">
        <div>
          <p className="eyebrow">AI quality control</p>
          <h2>Evaluation Platform</h2>
          <p className="muted">
            Version golden sets, compare every case, and gate releases against a baseline.
          </p>
        </div>
        <JsonImport workspaceId={workspaceId} onCreated={refresh} />
      </div>

      <div className="eval-starters">
        <p>
          {datasets.data?.length
            ? 'Add another model-free golden set or import a custom dataset.'
            : 'Create a model-free starter dataset, then run it as your first baseline.'}
        </p>
        <div className="row">
          {suites.data
            ?.filter((suite) =>
              [
                'router_contract',
                'structured_output',
                'agent_security',
                'resource_governance',
                'multi_agent_coordination',
              ].includes(suite.name),
            )
            .map((suite) => (
              <button
                className="ghost"
                key={suite.name}
                type="button"
                disabled={starter.isPending}
                onClick={() => starter.mutate(suite.name)}
              >
                Add {suite.name.replaceAll('_', ' ')}
              </button>
            ))}
        </div>
        {starter.error && (
          <p className="error">
            {starter.error instanceof ApiError ? starter.error.message : 'Could not add starter'}
          </p>
        )}
      </div>

      <div className="eval-section">
        <div className="section-title">
          <h3>Datasets</h3>
          <span className="muted">{datasets.data?.length ?? 0} versioned sets</span>
        </div>
        {datasets.isPending && <p className="muted">Loading datasets…</p>}
        <div className="eval-dataset-grid">
          {datasets.data?.map((dataset) => {
            const defaultVariant =
              dataset.suite === 'rag_retrieval'
                ? 'hybrid_rrf_rerank'
                : dataset.suite === 'multi_agent_coordination'
                  ? 'typed_dag'
                  : 'default'
            const mode = String(
              dataset.default_config.execution_mode ?? 'deterministic',
            )
            const baseline = completedByVariant.get(
              `${dataset.id}:${defaultVariant}:${mode}`,
            )
            return (
              <article className="eval-dataset-card" key={dataset.id}>
                <div>
                  <span className="eval-suite">{dataset.suite}</span>
                  <h3>{dataset.name}</h3>
                  <p className="muted">
                    v{dataset.version} · {dataset.case_count} cases
                    {baseline ? ` · baseline ${formatMetric(baseline.summary.pass_rate)}` : ''}
                  </p>
                </div>
                <div className="eval-dataset-actions">
                  <button
                    type="button"
                    disabled={runDataset.isPending || runMatrix.isPending}
                    onClick={() => runDataset.mutate(dataset.id)}
                  >
                    {dataset.suite === 'multi_agent_coordination'
                      ? 'Run full DAG'
                      : 'Run evaluation'}
                  </button>
                  {dataset.suite === 'multi_agent_coordination' && (
                    <>
                      <button
                        className="ghost"
                        type="button"
                        disabled={runMatrix.isPending}
                        onClick={() =>
                          runMatrix.mutate({
                            datasetId: dataset.id,
                            executionMode: 'deterministic',
                          })
                        }
                      >
                        Run ablation matrix
                      </button>
                      <button
                        className="ghost"
                        type="button"
                        disabled={runMatrix.isPending}
                        onClick={() =>
                          runMatrix.mutate({
                            datasetId: dataset.id,
                            executionMode: 'live',
                          })
                        }
                      >
                        Run live matrix · uses models
                      </button>
                    </>
                  )}
                </div>
              </article>
            )
          })}
        </div>
        {runDataset.error && (
          <p className="error">
            {runDataset.error instanceof ApiError
              ? runDataset.error.message
              : 'Could not start evaluation'}
          </p>
        )}
        {runMatrix.error && (
          <p className="error">
            {runMatrix.error instanceof ApiError
              ? runMatrix.error.message
              : 'Could not start ablation matrix'}
          </p>
        )}
      </div>

      {benchmarkDataset && (
        <div className="eval-section benchmark-comparison">
          <div className="section-title">
            <div>
              <h3>Multi-agent ablation</h3>
              <p className="muted">
                Latest comparable run per strategy · {benchmarkDataset.name}
              </p>
            </div>
            <span className="muted">{benchmarkRuns.length}/4 strategies</span>
          </div>
          {benchmarkRuns.length === 0 ? (
            <div className="empty">Run the ablation matrix to compare strategies.</div>
          ) : (
            <div className="benchmark-table-wrap">
              <table className="benchmark-table">
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th>Quality</th>
                    <th>Claim recall</th>
                    <th>Latency efficiency</th>
                    <th>Parallel speedup</th>
                    <th>Cost efficiency</th>
                    <th>Mode</th>
                  </tr>
                </thead>
                <tbody>
                  {benchmarkRuns.map((run) => (
                    <tr key={run.id}>
                      <td>
                        <button
                          className="link-button"
                          type="button"
                          onClick={() => setSelectedRunId(run.id)}
                        >
                          {run.variant}
                        </button>
                      </td>
                      <td>{formatMetric(run.summary.metrics?.answer_quality)}</td>
                      <td>{formatMetric(run.summary.metrics?.claim_recall)}</td>
                      <td>{formatMetric(run.summary.metrics?.latency_efficiency)}</td>
                      <td>{formatMetric(run.summary.metrics?.parallelism_efficiency)}</td>
                      <td>{formatMetric(run.summary.metrics?.cost_efficiency)}</td>
                      <td>{String(run.config.execution_mode ?? 'deterministic')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <div className="eval-section">
        <div className="section-title">
          <h3>Recent runs</h3>
          <span className="muted">Click a run for case-level evidence</span>
        </div>
        {!runs.data?.length && <div className="empty">No evaluation runs yet.</div>}
        <div className="eval-run-list">
          {runs.data?.map((run) => (
            <button
              className={`eval-run ${selectedRunId === run.id ? 'selected' : ''}`}
              key={run.id}
              type="button"
              onClick={() => setSelectedRunId(run.id)}
            >
              <span className={`eval-status ${run.status} ${run.summary.gate_passed === false ? 'regression' : ''}`}>
                {statusLabel(run)}
              </span>
              <span>
                <strong>{run.dataset_name}</strong>
                <small>{run.variant ?? run.suite}</small>
              </span>
              <span className="eval-score">
                {run.status === 'completed'
                  ? `${Math.round((run.summary.pass_rate ?? 0) * 100)}%`
                  : '…'}
              </span>
              <time>{new Date(run.created_at).toLocaleString()}</time>
            </button>
          ))}
        </div>
      </div>

      {selectedRun.data && (
        <div className="eval-detail">
          <div className="section-title">
            <div>
              <h3>{selectedRun.data.dataset_name}</h3>
              <p className="muted">
                {selectedRun.data.label}
                {selectedRun.data.git_sha ? ` · ${selectedRun.data.git_sha.slice(0, 8)}` : ''}
              </p>
            </div>
            <span className={`eval-status ${selectedRun.data.status}`}>
              {statusLabel(selectedRun.data)}
            </span>
          </div>
          <div className="eval-metrics">
            {Object.entries(selectedRun.data.summary.metrics ?? {}).map(([name, value]) => (
              <div key={name}>
                <span>{name}</span>
                <strong>{formatMetric(value)}</strong>
                {selectedRun.data.comparison.metrics?.[name]?.delta != null && (
                  <small
                    className={
                      selectedRun.data.comparison.metrics[name].regressed ? 'negative' : ''
                    }
                  >
                    Δ {selectedRun.data.comparison.metrics[name].delta! >= 0 ? '+' : ''}
                    {selectedRun.data.comparison.metrics[name].delta!.toFixed(3)}
                  </small>
                )}
              </div>
            ))}
          </div>
          <div className="eval-results">
            {selectedRun.data.results?.map((result) => (
              <details key={result.id} className={result.passed ? 'passed' : 'failed'}>
                <summary>
                  <span>{result.passed ? '✓' : '×'}</span>
                  <strong>{result.case_key}</strong>
                  <small>{result.latency_ms?.toFixed(1) ?? '—'} ms</small>
                </summary>
                {result.error && <p className="error">{result.error}</p>}
                <pre>{JSON.stringify({ scores: result.scores, output: result.output }, null, 2)}</pre>
              </details>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
