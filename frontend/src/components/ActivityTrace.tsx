import { useState } from 'react'
import type { TraceResult } from '@/lib/api'

export interface TraceStep {
  key: string
  agent: string
  label: string
  result: TraceResult
  done: boolean
  provider?: string
  model?: string
  tier?: 'fast' | 'smart'
  reasoning_effort?: string
}

interface TaskDagNode {
  [key: string]: TraceResult
  id: string
  agent: string
  kind: string
  depends_on: string[]
  status: string
  attempts: number
}

interface TaskDag {
  [key: string]: TraceResult
  type: 'task_dag'
  layers: string[][]
  nodes: TaskDagNode[]
}

function isTaskDag(value: Exclude<TraceResult, null>): value is TaskDag {
  if (typeof value !== 'object' || Array.isArray(value)) return false
  return (
    value.type === 'task_dag' &&
    Array.isArray(value.layers) &&
    Array.isArray(value.nodes)
  )
}

function TaskDagResult({ dag }: { dag: TaskDag }) {
  const nodes = new Map(dag.nodes.map((node) => [node.id, node]))
  return (
    <div className="task-dag" aria-label="Task dependency graph">
      <div className="task-dag-layers">
        {dag.layers.map((layer, index) => (
          <div className="task-dag-layer-wrap" key={layer.join(':')}>
            {index > 0 && <span className="task-dag-arrow">→</span>}
            <div className="task-dag-layer" aria-label={`DAG layer ${index + 1}`}>
              {layer.map((taskId) => {
                const node = nodes.get(taskId)
                if (!node) return null
                return (
                  <div
                    className={`task-dag-node task-dag-node-${node.status}`}
                    key={node.id}
                    title={
                      node.depends_on.length
                        ? `depends on ${node.depends_on.join(', ')}`
                        : 'no dependencies'
                    }
                  >
                    <span>{node.agent}</span>
                    <code>{node.id}</code>
                    <small>
                      {node.status}
                      {node.attempts > 0 ? ` · attempt ${node.attempts}` : ''}
                    </small>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>
      <details className="task-dag-raw">
        <summary>Raw DAG result</summary>
        <pre className="trace-result">{JSON.stringify(dag, null, 2)}</pre>
      </details>
    </div>
  )
}

function Result({ value }: { value: Exclude<TraceResult, null> }) {
  if (isTaskDag(value)) return <TaskDagResult dag={value} />
  if (
    typeof value === 'object' &&
    !Array.isArray(value) &&
    value.dag !== null &&
    isTaskDag(value.dag)
  ) {
    return (
      <>
        <TaskDagResult dag={value.dag} />
        <pre className="trace-result">{JSON.stringify(value, null, 2)}</pre>
      </>
    )
  }
  const rendered = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
  return <pre className="trace-result">{rendered}</pre>
}

/**
 * The collapsible call chain: which agent ran, what each step returned.
 * Collapsed it is one line; expanded, each step shows its result.
 */
export default function ActivityTrace({ steps }: { steps: TraceStep[] }) {
  const [open, setOpen] = useState(false)
  if (steps.length === 0) return null

  const running = steps.find((s) => !s.done)
  const agents = [...new Set(steps.map((step) => step.agent))].join(' → ')
  const summary = running
    ? running.label
    : `${agents} · ${steps.length} step${steps.length > 1 ? 's' : ''}`

  return (
    <div className="trace">
      <button
        type="button"
        className="trace-summary"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className={`chevron ${open ? 'open' : ''}`}>▶</span>
        {running && <span className="spinner" />}
        {summary}
      </button>
      {open && (
        <div className="trace-steps">
          {steps.map((s) => (
            <div key={s.key} className="trace-step">
              <span className="trace-step-head">
                {s.done ? '✓' : <span className="spinner" />}
                <span className="trace-agent">{s.agent}</span>
                <span>{s.label}</span>
                {s.model && (
                  <span
                    className="trace-model"
                    title={`${s.provider ?? 'LLM'} · ${s.tier ?? 'custom tier'}${
                      s.reasoning_effort ? ` · reasoning ${s.reasoning_effort}` : ''
                    }`}
                  >
                    {s.model}
                    {s.tier && ` · ${s.tier}`}
                    {s.reasoning_effort && ` · reasoning ${s.reasoning_effort}`}
                  </span>
                )}
              </span>
              {s.result !== null && <Result value={s.result} />}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
