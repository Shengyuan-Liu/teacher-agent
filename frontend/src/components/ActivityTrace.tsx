import { useState } from 'react'

export interface TraceStep {
  label: string
  done: boolean
}

export default function ActivityTrace({ steps }: { steps: TraceStep[] }) {
  const [open, setOpen] = useState(false)
  if (steps.length === 0) return null

  const running = steps.find((s) => !s.done)
  const summary = running ? running.label : steps[steps.length - 1].label

  return (
    <div className="trace">
      <button type="button" className="trace-summary" onClick={() => setOpen(!open)}>
        <span className={`chevron ${open ? 'open' : ''}`}>▶</span>
        {running && <span className="spinner" />}
        {summary}
      </button>
      {open && (
        <div className="trace-steps">
          {steps.map((s, i) => (
            <span key={i} style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
              {s.done ? '✓' : <span className="spinner" />} {s.label}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
