export interface SseFrame {
  event: string
  data: string
}

/** Incremental SSE parser. The server emits CRLF line endings, so normalise them. */
export class SseParser {
  private buffer = ''

  push(text: string): SseFrame[] {
    this.buffer += text.replace(/\r\n/g, '\n')
    const frames: SseFrame[] = []

    let sep
    while ((sep = this.buffer.indexOf('\n\n')) !== -1) {
      const raw = this.buffer.slice(0, sep)
      this.buffer = this.buffer.slice(sep + 2)

      let event = 'message'
      let data = ''
      for (const line of raw.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      if (data) frames.push({ event, data })
    }
    return frames
  }
}
