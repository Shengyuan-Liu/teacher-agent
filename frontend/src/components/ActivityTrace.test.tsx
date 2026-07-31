// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import ActivityTrace from './ActivityTrace'

test('expanded trace displays complete structured agent results', () => {
  render(
    <ActivityTrace
      steps={[
        {
          key: 'router',
          agent: 'router',
          label: 'Understanding request',
          result: {
            intent: 'quiz',
            routed_to: 'a practice quiz',
          },
          done: true,
          provider: 'openai',
          model: 'gpt-5.6-luna',
          tier: 'fast',
          reasoning_effort: 'none',
        },
        {
          key: 'validate',
          agent: 'quiz',
          label: 'Checking answers',
          result: {
            questions: [{ stem: 'What is convexity?', answer: 'A definition' }],
          },
          done: true,
          provider: 'openai',
          model: 'gpt-5.6-terra',
          tier: 'smart',
          reasoning_effort: 'medium',
        },
      ]}
    />,
  )

  expect(screen.queryByText(/"intent": "quiz"/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /router → quiz · 2 steps/i }))
  expect(screen.getByText('gpt-5.6-luna · fast · reasoning none')).toBeTruthy()
  expect(screen.getByText('gpt-5.6-terra · smart · reasoning medium')).toBeTruthy()
  expect(screen.getByText(/"intent": "quiz"/)).toBeTruthy()
  expect(screen.getByText(/"stem": "What is convexity\?"/)).toBeTruthy()
  expect(screen.getByText(/"answer": "A definition"/)).toBeTruthy()
})

test('typed task DAG shows parallel workers and dependent synthesis', () => {
  render(
    <ActivityTrace
      steps={[
        {
          key: 'task_dag',
          agent: 'orchestrator',
          label: 'Building execution graph',
          result: {
            type: 'task_dag',
            layers: [['web_1', 'qa_1'], ['answer_1']],
            nodes: [
              {
                id: 'web_1',
                agent: 'web',
                kind: 'knowledge',
                query: 'Find biography',
                depends_on: [],
                status: 'completed',
                attempts: 1,
              },
              {
                id: 'qa_1',
                agent: 'qa',
                kind: 'knowledge',
                query: 'Find theorem',
                depends_on: [],
                status: 'completed',
                attempts: 1,
              },
              {
                id: 'answer_1',
                agent: 'answer',
                kind: 'synthesis',
                query: 'Combine',
                depends_on: ['web_1', 'qa_1'],
                status: 'pending',
                attempts: 0,
              },
            ],
          },
          done: true,
        },
      ]}
    />,
  )

  fireEvent.click(
    screen.getByRole('button', { name: /orchestrator · 1 step/i }),
  )
  expect(screen.getByLabelText('Task dependency graph')).toBeTruthy()
  expect(screen.getByLabelText('DAG layer 1').textContent).toContain('web')
  expect(screen.getByLabelText('DAG layer 1').textContent).toContain('qa')
  expect(screen.getByLabelText('DAG layer 2').textContent).toContain('answer')
  fireEvent.click(screen.getByText('Raw DAG result'))
  expect(screen.getByText(/"depends_on":/)).toBeTruthy()
})
