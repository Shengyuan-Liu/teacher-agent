import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import MarkdownBlock from '@/components/MarkdownBlock'
import { api } from '@/lib/api'

/**
 * Read-only view of the workspace's study plan as a to-do list. The plan is
 * created and edited in chat ("帮我制定学习计划" / "把第二阶段改成…"); here the learner
 * only ticks stages off, and that progress is what the planner reads next time.
 */
export default function PlanPanel({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient()

  const plans = useQuery({
    queryKey: ['plans', workspaceId],
    queryFn: () => api.listPlans(workspaceId),
  })

  const toggleStage = useMutation({
    mutationFn: ({
      planId,
      stageId,
      status,
    }: {
      planId: string
      stageId: string
      status: 'pending' | 'done'
    }) => api.updateStage(planId, stageId, status),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['plans', workspaceId] }),
  })

  if (plans.isPending) return <p className="muted">Loading…</p>
  if (!plans.data?.length)
    return (
      <div className="empty">
        No study plan yet. Ask in chat, e.g. “帮我制定一个学习计划” — then tick stages off here as
        you go.
      </div>
    )

  return (
    <section>
      {plans.data.slice(0, 1).map((plan) => (
        <div key={plan.id} className="plan">
          <div className="plan-head">
            <strong>{plan.goal}</strong>
          </div>
          {plan.stages.map((stage) => (
            <div key={stage.id} className={`stage ${stage.status}`}>
              <label className="stage-title">
                <span className="stage-title-text">
                  {stage.position + 1}. {stage.title}
                </span>
                <span className="muted">~{Math.round(stage.estimated_minutes / 60)}h</span>
                <input
                  type="checkbox"
                  aria-label={`Mark ${stage.title} as complete`}
                  checked={stage.status === 'done'}
                  onChange={(e) =>
                    toggleStage.mutate({
                      planId: plan.id,
                      stageId: stage.id,
                      status: e.target.checked ? 'done' : 'pending',
                    })
                  }
                />
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
