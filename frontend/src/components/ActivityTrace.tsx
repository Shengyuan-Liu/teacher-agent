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

function Result({ value }: { value: Exclude<TraceResult, null> }) {
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
