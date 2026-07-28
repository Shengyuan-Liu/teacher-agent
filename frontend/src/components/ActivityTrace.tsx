import { useState } from 'react'

export interface TraceStep {
  key: string
  agent: string
  label: string
  result: string | null
  done: boolean
}

/**
 * The collapsible call chain: which agent ran, what each step returned.
 * Collapsed it is one line; expanded, each step shows its result.
 */
export default function ActivityTrace({ steps }: { steps: TraceStep[] }) {
  const [open, setOpen] = useState(false)
  if (steps.length === 0) return null

  const running = steps.find((s) => !s.done)
  const summary = running
    ? running.label
    : `${steps[0].agent} · ${steps.length} step${steps.length > 1 ? 's' : ''}`

  return (
    <div className="trace">
      <button type="button" className="trace-summary" onClick={() => setOpen(!open)}>
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
                {s.label}
              </span>
              {s.result && <span className="trace-result">→ {s.result}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
