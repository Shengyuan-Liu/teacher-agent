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
