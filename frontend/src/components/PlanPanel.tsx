import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import ActivityTrace, { type TraceStep } from '@/components/ActivityTrace'
import MarkdownBlock from '@/components/MarkdownBlock'
import { api, streamAgent } from '@/lib/api'

export default function PlanPanel({ workspaceId }: { workspaceId: string }) {
  const [goal, setGoal] = useState('')
  const [dailyMinutes, setDailyMinutes] = useState(60)
  const [deadline, setDeadline] = useState('')
  const [steps, setSteps] = useState<TraceStep[]>([])
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  const queryClient = useQueryClient()

  const plans = useQuery({
    queryKey: ['plans', workspaceId],
    queryFn: () => api.listPlans(workspaceId),
  })

  const toggleStage = useMutation({
    mutationFn: ({ planId, stageId, status }: { planId: string; stageId: string; status: 'pending' | 'done' }) =>
      api.updateStage(planId, stageId, status),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['plans', workspaceId] }),
  })

  const removePlan = useMutation({
    mutationFn: (planId: string) => api.deletePlan(planId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['plans', workspaceId] }),
  })

  async function generate(e: React.FormEvent) {
    e.preventDefault()
    if (!goal.trim() || generating) return
    setError('')
    setSteps([])
    setGenerating(true)
    try {
      await streamAgent(
        `/workspaces/${workspaceId}/plans/stream`,
        { goal: goal.trim(), daily_minutes: dailyMinutes, deadline: deadline || null },
        {
          onStage: (ev) =>
            setSteps((s) => [
              ...s,
              { key: ev.stage, agent: ev.agent, label: ev.label, result: null, done: false },
            ]),
          onStageResult: (ev) =>
            setSteps((s) =>
              s.map((step) =>
                step.key === ev.stage ? { ...step, result: ev.result, done: true } : step,
              ),
            ),
          onUsage: () => {},
          onDone: () => {
            setGoal('')
            queryClient.invalidateQueries({ queryKey: ['plans', workspaceId] })
          },
          onError: setError,
        },
      )
    } catch (err) {
      setError(String(err))
    } finally {
      setGenerating(false)
      setSteps((s) => s.map((step) => ({ ...step, done: true })))
    }
  }

  return (
    <section>
      <form onSubmit={generate} className="plan-form">
        <input
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="Your goal, e.g. pass the optimisation exam / understand this codebase"
        />
        <div className="row" style={{ marginTop: '0.5rem' }}>
          <label className="inline-label">
            min/day
            <input
              type="number"
              min={10}
              max={720}
              value={dailyMinutes}
              onChange={(e) => setDailyMinutes(Number(e.target.value))}
              style={{ width: '6rem' }}
            />
          </label>
          <label className="inline-label">
            deadline
            <input
              type="date"
              value={deadline}
              onChange={(e) => setDeadline(e.target.value)}
              style={{ width: '11rem' }}
            />
          </label>
          <button type="submit" disabled={generating || !goal.trim()}>
            {generating ? 'Planning…' : 'Generate plan'}
          </button>
        </div>
      </form>

      {steps.length > 0 && <ActivityTrace steps={steps} />}
      {error && <p className="error">{error}</p>}

      {plans.data?.length === 0 && !generating && (
        <div className="empty">No plan yet. State your goal above and generate one.</div>
      )}

      {plans.data?.map((plan) => (
        <div key={plan.id} className="plan">
          <div className="plan-head">
            <strong>{plan.goal}</strong>
            <span className="muted">
              {plan.daily_minutes} min/day
              {plan.deadline ? ` · until ${plan.deadline}` : ''} ·{' '}
              <button className="link-button danger" onClick={() => removePlan.mutate(plan.id)}>
                delete
              </button>
            </span>
          </div>
          {plan.stages.map((stage) => (
            <div key={stage.id} className={`stage ${stage.status}`}>
              <label className="stage-title">
                <input
                  type="checkbox"
                  checked={stage.status === 'done'}
                  onChange={(e) =>
                    toggleStage.mutate({
                      planId: plan.id,
                      stageId: stage.id,
                      status: e.target.checked ? 'done' : 'pending',
                    })
                  }
                />
                <span>
                  {stage.position + 1}. {stage.title}
                </span>
                <span className="muted">~{Math.round(stage.estimated_minutes / 60)}h</span>
              </label>
              <div className="stage-body">
                <MarkdownBlock content={stage.description} />
                <p className="muted">
                  {stage.topics.join(' · ')}
                  {stage.activities.length > 0 && ` — ${stage.activities.join(', ')}`}
                </p>
              </div>
            </div>
          ))}
        </div>
      ))}
    </section>
  )
}
