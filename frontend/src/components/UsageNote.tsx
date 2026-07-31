import { useState } from 'react'
import type { Usage } from '@/lib/api'

function formatCost(usd: number): string {
  // Sub-cent turns are the common case; two decimals would round them to $0.00.
  if (usd < 0.01) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
}

export default function UsageNote({ usage }: { usage: Usage }) {
  const [open, setOpen] = useState(false)
  const governance = usage.resource_governance

  const cost =
    usage.cost_usd === null
      ? 'cost unknown'
      : `${formatCost(usage.cost_usd)}${usage.priced ? '' : '+'}`

  return (
    <div className="usage">
      <button type="button" className="usage-summary" onClick={() => setOpen(!open)}>
        {usage.total_tokens.toLocaleString()} tokens · {cost}
      </button>
      {open && (
        <>
          {governance && (
            <div className="resource-governance" aria-label="Turn resource governance">
              <span>
                Budget {governance.budget.actual.model_calls}/
                {governance.budget.limits.max_model_calls} calls
              </span>
              <span>
                Cache {governance.cache.hits} hit / {governance.cache.misses} miss
              </span>
              <span>
                Breaker {governance.circuit_breaker.events.length} event
                {governance.circuit_breaker.events.length === 1 ? '' : 's'}
              </span>
              {governance.budget.downgraded_calls > 0 && (
                <span>{governance.budget.downgraded_calls} Smart → Fast</span>
              )}
              {governance.budget.hard_stop && <span className="resource-stop">Hard stop</span>}
            </div>
          )}
          <table className="usage-table">
            <tbody>
              {usage.calls.map((call, i) => (
                <tr key={i}>
                  <td>{call.step}</td>
                  <td className="usage-model">{call.model}</td>
                  <td>
                    {call.input_tokens.toLocaleString()} in
                    {call.output_tokens > 0 && ` / ${call.output_tokens.toLocaleString()} out`}
                  </td>
                  <td>{call.cost_usd === null ? '—' : formatCost(call.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}
