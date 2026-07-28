import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import ActivityTrace, { type TraceStep } from '@/components/ActivityTrace'
import MarkdownBlock from '@/components/MarkdownBlock'
import { api, streamAgent, type Question } from '@/lib/api'

function sameSet(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((x) => b.includes(x))
}

function QuestionCard({
  question,
  onDelete,
}: {
  question: Question
  onDelete: () => void
}) {
  const [picked, setPicked] = useState<string[]>([])
  const [typed, setTyped] = useState('')
  const [revealed, setRevealed] = useState(false)

  const answers = Array.isArray(question.answer) ? question.answer : [question.answer]
  const choice = question.type === 'single' || question.type === 'multi'
  const correct = choice
    ? sameSet(picked, answers)
    : typed.trim().toLowerCase() === String(question.answer).trim().toLowerCase()

  function pick(option: string) {
    if (revealed) return
    if (question.type === 'single') {
      setPicked([option])
    } else {
      setPicked((p) => (p.includes(option) ? p.filter((x) => x !== option) : [...p, option]))
    }
  }

  return (
    <div className="question">
      <div className="question-head">
        <span className="badge">{question.type}</span>
        <span className="badge">{question.difficulty}</span>
        {question.source && (
          <span className="muted" title={question.source.heading ?? undefined}>
            {question.source.title}
          </span>
        )}
        <button className="link-button danger" onClick={onDelete} style={{ marginLeft: 'auto' }}>
          ×
        </button>
      </div>

      <MarkdownBlock content={question.stem} />

      {choice && question.options && (
        <div className="options">
          {question.options.map((option) => {
            const isPicked = picked.includes(option)
            const isAnswer = answers.includes(option)
            const cls = revealed
              ? isAnswer
                ? 'option correct'
                : isPicked
                  ? 'option wrong'
                  : 'option'
              : isPicked
                ? 'option picked'
                : 'option'
            return (
              <button key={option} type="button" className={cls} onClick={() => pick(option)}>
                <MarkdownBlock content={option} />
              </button>
            )
          })}
        </div>
      )}

      {!choice && (
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={question.type === 'fill' ? 'Fill in the blank…' : 'Your answer…'}
          disabled={revealed}
          style={{ marginTop: '0.5rem' }}
        />
      )}

      {!revealed ? (
        <button
          className="ghost"
          style={{ marginTop: '0.6rem' }}
          onClick={() => setRevealed(true)}
          disabled={choice ? picked.length === 0 : !typed.trim()}
        >
          Check
        </button>
      ) : (
        <div className={`verdict ${correct ? 'ok' : 'bad'}`}>
          {choice ? (
            correct ? (
              'Correct'
            ) : (
              'Not quite'
            )
          ) : (
            <span>
              Reference answer: <MarkdownBlock content={String(question.answer)} />
            </span>
          )}
          <div className="muted" style={{ marginTop: '0.4rem' }}>
            <MarkdownBlock content={question.explanation} />
          </div>
        </div>
      )}
    </div>
  )
}

export default function QuizPanel({ workspaceId }: { workspaceId: string }) {
  const [count, setCount] = useState(5)
  const [topic, setTopic] = useState('')
  const [steps, setSteps] = useState<TraceStep[]>([])
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  const queryClient = useQueryClient()

  const questions = useQuery({
    queryKey: ['questions', workspaceId],
    queryFn: () => api.listQuestions(workspaceId),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteQuestion(workspaceId, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['questions', workspaceId] }),
  })

  async function generate(e: React.FormEvent) {
    e.preventDefault()
    if (generating) return
    setError('')
    setSteps([])
    setGenerating(true)
    try {
      await streamAgent(
        `/workspaces/${workspaceId}/quiz/stream`,
        { count, topic: topic.trim() || null },
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
          onDone: () => queryClient.invalidateQueries({ queryKey: ['questions', workspaceId] }),
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
      <form onSubmit={generate} className="row" style={{ flexWrap: 'wrap' }}>
        <input
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="Topic to focus on (optional, e.g. QR factorisation)"
          style={{ flex: 1, minWidth: '14rem' }}
        />
        <select value={count} onChange={(e) => setCount(Number(e.target.value))}>
          {[3, 5, 10].map((n) => (
            <option key={n} value={n}>
              {n} questions
            </option>
          ))}
        </select>
        <button type="submit" disabled={generating}>
          {generating ? 'Writing…' : 'Generate'}
        </button>
      </form>

      {steps.length > 0 && <ActivityTrace steps={steps} />}
      {error && <p className="error">{error}</p>}

      {questions.data?.length === 0 && !generating && (
        <div className="empty">No questions yet. Generate a few to practise with.</div>
      )}
      {questions.data?.map((question) => (
        <QuestionCard
          key={question.id}
          question={question}
          onDelete={() => remove.mutate(question.id)}
        />
      ))}
    </section>
  )
}
