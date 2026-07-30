// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ChatArtifact from './ChatArtifact'

describe('Lecture chat artifact', () => {
  it('shows durable progress and emits lecture controls', () => {
    const onLectureAction = vi.fn()
    render(
      <ChatArtifact
        artifact={{
          type: 'lecture',
          title: 'Probability foundations',
          status: 'waiting_check',
          current_section: 1,
          completed_sections: 0,
          total_sections: 3,
          sections: [
            { index: 0, title: 'Events', status: 'current' },
            { index: 1, title: 'Conditioning', status: 'upcoming' },
            { index: 2, title: 'Independence', status: 'upcoming' },
          ],
          check_question: 'What is an event?',
          actions: [
            { action: 'pause', label: '暂停' },
            { action: 'stop', label: '结束讲课' },
          ],
        }}
        workspaceId="workspace-1"
        onChooseIntent={vi.fn()}
        onLectureAction={onLectureAction}
      />,
    )

    expect(screen.getByText('Probability foundations')).toBeTruthy()
    expect(screen.getByText('第 1 / 3 节')).toBeTruthy()
    expect(screen.getByText('What is an event?')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '暂停' }))
    expect(onLectureAction).toHaveBeenCalledWith('pause')
  })

  it('offers a retry instead of leaving an invalid response stuck', () => {
    const onLectureAction = vi.fn()
    render(
      <ChatArtifact
        artifact={{
          type: 'lecture',
          status: 'error',
          error: 'The model response could not be converted into a lecture.',
          actions: [{ action: 'retry', label: '重试生成' }],
        }}
        workspaceId="workspace-1"
        onChooseIntent={vi.fn()}
        onLectureAction={onLectureAction}
      />,
    )
    expect(screen.getByText(/could not be converted/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '重试生成' }))
    expect(onLectureAction).toHaveBeenCalledWith('retry')
  })

  it('retries grading with the preserved learner answer', () => {
    const onLectureAction = vi.fn()
    render(
      <ChatArtifact
        artifact={{
          type: 'lecture',
          status: 'waiting_check',
          error: '评分结果格式无效，checkpoint 已保留。',
          actions: [
            { action: 'retry_grade', label: '重试评分', message: 'the preserved answer' },
          ],
        }}
        workspaceId="workspace-1"
        onChooseIntent={vi.fn()}
        onLectureAction={onLectureAction}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: '重试评分' }))
    expect(onLectureAction).toHaveBeenCalledWith('retry_grade', 'the preserved answer')
  })
})
