import { describe, expect, it } from 'vitest'
import { SseParser } from './sse'

describe('SseParser', () => {
  it('parses the CRLF framing the server actually sends', () => {
    const parser = new SseParser()
    const frames = parser.push(
      'event: stage\r\ndata: {"stage": "retrieve"}\r\n\r\n' +
        'event: token\r\ndata: {"delta": "hi"}\r\n\r\n',
    )
    expect(frames).toEqual([
      { event: 'stage', data: '{"stage": "retrieve"}' },
      { event: 'token', data: '{"delta": "hi"}' },
    ])
  })

  it('parses plain LF framing too', () => {
    const parser = new SseParser()
    expect(parser.push('event: done\ndata: {"grounded": true}\n\n')).toEqual([
      { event: 'done', data: '{"grounded": true}' },
    ])
  })

  it('holds back a frame split across chunk boundaries', () => {
    const parser = new SseParser()
    expect(parser.push('event: token\r\nda')).toEqual([])
    expect(parser.push('ta: {"delta": "a"}\r\n')).toEqual([])
    expect(parser.push('\r\nevent: token\r\ndata: {"delta": "b"}\r\n\r\n')).toEqual([
      { event: 'token', data: '{"delta": "a"}' },
      { event: 'token', data: '{"delta": "b"}' },
    ])
  })

  it('ignores comment-only keepalive frames', () => {
    const parser = new SseParser()
    expect(parser.push(': ping\r\n\r\n')).toEqual([])
  })
})
