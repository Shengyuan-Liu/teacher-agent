import { useEffect, useMemo, useState } from 'react'
import ActivityTrace from '@/components/ActivityTrace'
import MarkdownBlock from '@/components/MarkdownBlock'
import {
  api,
  type Assessment,
  type AssessmentQuestion,
  type ChatArtifact as Artifact,
  type ChatIntent,
  type KnowledgeGraph,
  type Question,
  type ReviewItem,
  type ReviewResult,
} from '@/lib/api'

type ChooseIntent = (intent: ChatIntent, label: string) => void
type LectureAction = 'continue' | 'pause' | 'stop' | 'retry' | 'retry_grade'

function ResponseField({
  question,
  value,
  disabled,
  onChange,
}: {
  question: Pick<Question, 'type' | 'options'>
  value: unknown
  disabled?: boolean
  onChange: (value: unknown) => void
}) {
  const selected = Array.isArray(value) ? value : value ? [String(value)] : []
  if ((question.type === 'single' || question.type === 'multi') && question.options) {
    return (
      <div className="options">
        {question.options.map((option) => (
          <button
            type="button"
            className={selected.includes(option) ? 'option picked' : 'option'}
            disabled={disabled}
            key={option}
            onClick={() => {
              if (question.type === 'single') onChange(option)
              else if (selected.includes(option)) onChange(selected.filter((item) => item !== option))
              else onChange([...selected, option])
            }}
          >
            <MarkdownBlock content={option} />
          </button>
        ))}
      </div>
    )
  }
  return (
    <textarea
      rows={question.type === 'short' ? 4 : 2}
      value={String(value ?? '')}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      placeholder="在这里作答…"
    />
  )
}

function PracticeQuiz({ questions }: { questions: Question[] }) {
  const [answers, setAnswers] = useState<Record<string, unknown>>({})
  const [revealed, setRevealed] = useState<Record<string, boolean>>({})
  return (
    <div className="chat-artifact">
      {questions.map((question, index) => (
        <article className="question" key={question.id}>
          <div className="question-head">
            <strong>{index + 1}.</strong>
            <span className="badge">{question.type}</span>
            <span className="badge">{question.difficulty}</span>
          </div>
          <MarkdownBlock content={question.stem} />
          <ResponseField
            question={question}
            value={answers[question.id]}
            disabled={revealed[question.id]}
            onChange={(value) => setAnswers((current) => ({ ...current, [question.id]: value }))}
          />
          {!revealed[question.id] ? (
            <button
              type="button"
              onClick={() => setRevealed((current) => ({ ...current, [question.id]: true }))}
            >
              查看答案
            </button>
          ) : (
            <div className="verdict ok">
              <strong>参考答案：</strong>{' '}
              {Array.isArray(question.answer) ? question.answer.join(', ') : question.answer}
              <MarkdownBlock content={question.explanation} />
            </div>
          )}
        </article>
      ))}
    </div>
  )
}

function TimedAssessment({ workspaceId, assessmentId }: { workspaceId: string; assessmentId: string }) {
  const [assessment, setAssessment] = useState<Assessment | null>(null)
  const [answers, setAnswers] = useState<Record<string, unknown>>({})
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [now, setNow] = useState(Date.now())

  useEffect(() => {
    api.getAssessment(workspaceId, assessmentId).then(setAssessment).catch((cause) => setError(String(cause)))
  }, [assessmentId, workspaceId])
  useEffect(() => {
    if (assessment?.status !== 'in_progress') return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [assessment?.status])

  const seconds = assessment
    ? Math.max(
        0,
        Math.ceil(
          (new Date(assessment.started_at).getTime() + assessment.time_limit_minutes * 60_000 - now) /
            1000,
        ),
      )
    : 0
  const percent = useMemo(() => {
    if (!assessment?.max_score || assessment.score == null) return null
    return Math.round((assessment.score / assessment.max_score) * 100)
  }, [assessment])

  async function submit() {
    if (!assessment || submitting) return
    setSubmitting(true)
    try {
      setAssessment(await api.submitAssessment(workspaceId, assessment.id, answers))
    } catch (cause) {
      setError(String(cause))
    } finally {
      setSubmitting(false)
    }
  }

  useEffect(() => {
    if (assessment?.status === 'in_progress' && seconds === 0) void submit()
    // `seconds` changes only on the timer tick, so timeout submission fires once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assessment?.status, seconds])

  if (error) return <p className="error">{error}</p>
  if (!assessment) return <p className="muted">正在加载测试…</p>
  const submitted = assessment.status !== 'in_progress'
  const gradingSteps = submitted
    ? assessment.questions.map((question) => ({
        key: `grade-${question.id}`,
        agent: question.grader === 'llm' ? 'short_answer_grader' : 'objective_grader',
        label: `批改第 ${question.position + 1} 题`,
        result: {
          score_fraction: question.score_fraction ?? 0,
          correct: question.correct ?? false,
          feedback: question.feedback ?? '',
        },
        done: true,
        model: question.grader_model ?? undefined,
        tier: question.grader === 'llm' ? ('smart' as const) : undefined,
      }))
    : []
  return (
    <section className="chat-artifact">
      <div className="assessment-head">
        <strong>{assessment.title}</strong>
        {!submitted ? (
          <span className={`test-timer ${seconds < 60 ? 'urgent' : ''}`}>
            {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}
          </span>
        ) : (
          <strong className="score-pill">{percent}%</strong>
        )}
      </div>
      {gradingSteps.length > 0 && <ActivityTrace steps={gradingSteps} />}
      {assessment.questions.map((question: AssessmentQuestion) => (
        <article className={`question ${submitted ? (question.correct ? 'passed' : 'failed') : ''}`} key={question.id}>
          <div className="question-head">
            <strong>{question.position + 1}.</strong>
            <span className="badge">{question.type}</span>
          </div>
          <MarkdownBlock content={question.stem} />
          <ResponseField
            question={question}
            value={answers[question.id] ?? question.response}
            disabled={submitted}
            onChange={(value) => setAnswers((current) => ({ ...current, [question.id]: value }))}
          />
          {submitted && (
            <div className={`verdict ${question.correct ? 'ok' : 'bad'}`}>
              <strong>{question.correct ? '正确' : `得分 ${Math.round((question.score_fraction ?? 0) * 100)}%`}</strong>
              {question.feedback && <MarkdownBlock content={question.feedback} />}
            </div>
          )}
        </article>
      ))}
      {!submitted && (
        <button type="button" disabled={submitting} onClick={submit}>
          {submitting ? '评分中…' : seconds === 0 ? '时间到，提交测试' : '提交测试'}
        </button>
      )}
    </section>
  )
}

function ReviewCard({ workspaceId, ids }: { workspaceId: string; ids: string[] }) {
  const [items, setItems] = useState<ReviewItem[]>([])
  const [response, setResponse] = useState<unknown>('')
  const [result, setResult] = useState<ReviewResult | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    api.listReviews(workspaceId).then((rows) => setItems(rows.filter((row) => ids.includes(row.id))))
  }, [ids, workspaceId])
  const current = items[0]
  if (!current) return <div className="empty">这组错题已经复习完成。</div>
  return (
    <article className="question chat-artifact">
      <div className="question-head"><span className="badge">错题复习</span><span>{current.topic}</span></div>
      <MarkdownBlock content={current.question.stem} />
      <ResponseField question={current.question} value={response} disabled={Boolean(result)} onChange={setResponse} />
      {!result ? (
        <button type="button" disabled={busy} onClick={async () => {
          setBusy(true)
          try { setResult(await api.answerReview(workspaceId, current.id, response)) } finally { setBusy(false) }
        }}>{busy ? '检查中…' : '检查答案'}</button>
      ) : (
        <div className={`verdict ${result.correct ? 'ok' : 'bad'}`}>
          <ActivityTrace
            steps={[{
              key: `review-${current.id}`,
              agent: result.grader === 'llm' ? 'short_answer_grader' : 'objective_grader',
              label: '批改复习题',
              result: {
                score_fraction: result.score_fraction,
                correct: result.correct,
                feedback: result.feedback,
              },
              done: true,
              model: result.grader_model ?? undefined,
              tier: result.grader === 'llm' ? 'smart' : undefined,
            }]}
          />
          <strong>{result.correct ? '正确' : `得分 ${Math.round(result.score_fraction * 100)}%`}</strong>
          <MarkdownBlock content={result.feedback} />
          <button type="button" onClick={() => { setItems((rows) => rows.slice(1)); setResponse(''); setResult(null) }}>下一题</button>
        </div>
      )}
    </article>
  )
}

function LectureCard({
  artifact,
  disabled,
  onAction,
}: {
  artifact: Artifact
  disabled?: boolean
  onAction: (action: LectureAction, message?: string) => void
}) {
  const sections = (artifact.sections ?? []) as {
    index: number
    title: string
    status: 'done' | 'current' | 'upcoming'
  }[]
  const actions = (artifact.actions ?? []) as {
    action: LectureAction
    label: string
    message?: string
  }[]
  const total = Number(artifact.total_sections ?? 0)
  const current = Number(artifact.current_section ?? 0)
  const completed = Number(artifact.completed_sections ?? 0)
  const status = String(artifact.status ?? '')
  const progress = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0
  const statusLabels: Record<string, string> = {
    active: '等待继续',
    waiting_check: '等待回答',
    paused: '已暂停',
    completed: '已完成',
    cancelled: '已结束',
    missing: '尚未开始',
  }
  return (
    <section className="chat-artifact lecture-card">
      <div className="lecture-head">
        <div>
          <span className="badge">互动讲课</span>
          <strong>{String(artifact.title ?? 'Lecture')}</strong>
        </div>
        <span className={`lecture-status ${status}`}>{statusLabels[status] ?? status}</span>
      </div>
      {total > 0 && (
        <>
          <div className="lecture-progress-label">
            <span>第 {current} / {total} 节</span>
            <span>{progress}%</span>
          </div>
          <div className="lecture-progress" aria-label={`讲课进度 ${progress}%`}>
            <span style={{ width: `${progress}%` }} />
          </div>
          <ol className="lecture-sections">
            {sections.map((section) => (
              <li className={section.status} key={`${section.index}-${section.title}`}>
                <span>{section.status === 'done' ? '✓' : section.index + 1}</span>
                {section.title}
              </li>
            ))}
          </ol>
        </>
      )}
      {artifact.check_question ? (
        <div className="lecture-check">
          <strong>等待你的回答</strong>
          <p>{String(artifact.check_question)}</p>
          <small>直接在下方 Chat 输入框作答；也可以随时插入一个问题。</small>
        </div>
      ) : null}
      {artifact.error ? <p className="error">{String(artifact.error)}</p> : null}
      {actions.length > 0 && (
        <div className="lecture-actions">
          {actions.map((action) => (
            <button
              type="button"
              className={action.action === 'stop' ? 'secondary' : undefined}
              disabled={disabled}
              key={action.action}
              onClick={() => {
                if (action.message) onAction(action.action, action.message)
                else onAction(action.action)
              }}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}
    </section>
  )
}

export default function ChatArtifact({
  artifact,
  workspaceId,
  disabled,
  onChooseIntent,
  onLectureAction,
}: {
  artifact: Artifact
  workspaceId: string
  disabled?: boolean
  onChooseIntent: ChooseIntent
  onLectureAction: (action: LectureAction, message?: string) => void
}) {
  if (artifact.type === 'clarification') {
    const options = (artifact.options ?? []) as { intent: ChatIntent; label: string; description: string }[]
    return <div className="chat-artifact clarification-options">{options.map((option) => (
      <button type="button" disabled={disabled} key={option.intent} onClick={() => onChooseIntent(option.intent, option.label)}>
        <strong>{option.label}</strong><small>{option.description}</small>
      </button>
    ))}</div>
  }
  if (artifact.type === 'practice_quiz') return <PracticeQuiz questions={artifact.questions as Question[]} />
  if (artifact.type === 'assessment') return <TimedAssessment workspaceId={workspaceId} assessmentId={String(artifact.assessment_id)} />
  if (artifact.type === 'review') return <ReviewCard workspaceId={workspaceId} ids={(artifact.review_ids ?? []) as string[]} />
  if (artifact.type === 'mastery') {
    const items = (artifact.items ?? []) as { topic: string; score: number; attempts: number; correct_count: number }[]
    return <div className="chat-artifact mastery-list">{items.map((item) => (
      <div className="mastery-row" key={item.topic}><div className="mastery-label"><strong>{item.topic}</strong><span>{Math.round(item.score)}%</span></div><div className="mastery-track"><span style={{ width: `${item.score}%` }} /></div><small>{item.correct_count}/{item.attempts} 次掌握良好</small></div>
    ))}</div>
  }
  if (artifact.type === 'knowledge_graph') {
    const graph = artifact.graph as KnowledgeGraph
    const titles = new Map(graph?.nodes?.map((node) => [node.id, node.title]))
    return <div className="chat-artifact knowledge-graph"><strong>知识关系</strong><div className="knowledge-nodes">{graph?.nodes?.map((node) => <span className="badge" key={node.id}>{node.title}{node.mastery == null ? '' : ` · ${Math.round(node.mastery)}%`}</span>)}</div>{graph?.edges?.length > 0 && <div className="knowledge-edges">{graph.edges.map((edge, index) => <small key={`${edge.from}-${edge.to}-${index}`}>{titles.get(edge.from) ?? edge.from} → {titles.get(edge.to) ?? edge.to}</small>)}</div>}</div>
  }
  if (artifact.type === 'lecture') {
    return <LectureCard artifact={artifact} disabled={disabled} onAction={onLectureAction} />
  }
  return null
}
