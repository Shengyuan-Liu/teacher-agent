// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { dispatchStreamEvent, type StreamHandlers } from './api'

function handlers(): StreamHandlers {
  return {
    onStage: vi.fn(),
    onStageResult: vi.fn(),
    onCitations: vi.fn(),
    onToken: vi.fn(),
    onWebCitation: vi.fn(),
    onWebSearchSuggested: vi.fn(),
    onUsage: vi.fn(),
    onDone: vi.fn(),
    onError: vi.fn(),
  }
}

describe('dispatchStreamEvent', () => {
  it('delivers structured stage results to chat handlers', () => {
    const target = handlers()
    const result = { context: [{ source_title: 'Notes.pdf', content: 'Full passage' }] }

    dispatchStreamEvent('stage_result', JSON.stringify({ stage: 'retrieve', result }), target)

    expect(target.onStageResult).toHaveBeenCalledWith({ stage: 'retrieve', result })
  })

  it('keeps the complete done payload', () => {
    const target = handlers()
    const payload = { message_id: 'message-1', grounded: true }

    dispatchStreamEvent('done', JSON.stringify(payload), target)

    expect(target.onDone).toHaveBeenCalledWith(payload)
  })
})
