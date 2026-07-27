const FENCE = /^\s*(```|~~~)/
const OPEN = /^([ \t]*)\$\$(.*)$/

/**
 * remark-math only closes a `$$` block when the closing delimiter ends its line.
 * Models routinely write `$$a = b$$ [1]`, end a multi-line block with
 * `\end{pmatrix}$$ [1]`, or indent a block inside a list item. remark-math then
 * keeps scanning for a delimiter it accepts and swallows the prose in between,
 * handing the lot to KaTeX.
 *
 * Rewrite display blocks so both delimiters sit alone on their own lines at a
 * consistent indent, and push trailing text onto the next line.
 */
export function normaliseMath(markdown: string): string {
  const result = rewrite(markdown)
  return isBalanced(result, markdown) ? result : markdown
}

function rewrite(markdown: string): string {
  const lines = markdown.split('\n')
  const out: string[] = []
  let inFence = false

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]

    if (FENCE.test(line)) {
      inFence = !inFence
      out.push(line)
      continue
    }

    const open = inFence ? null : OPEN.exec(line)
    if (!open) {
      out.push(line)
      continue
    }

    const [, indent, firstRest] = open
    const body: string[] = []
    let rest = firstRest
    let trailing = ''
    let closed = false
    let j = i

    for (;;) {
      const close = rest.indexOf('$$')
      if (close !== -1) {
        body.push(rest.slice(0, close))
        trailing = rest.slice(close + 2).trim()
        closed = true
        break
      }
      body.push(rest)
      j += 1
      if (j >= lines.length) break
      rest = lines[j]
    }

    const content = reindent(body, indent)
    // An unclosed block is normal mid-stream; leave it until the rest arrives.
    if (!closed || !content) {
      out.push(line)
      continue
    }

    out.push(`${indent}$$`, content, `${indent}$$`)
    if (trailing) out.push('', `${indent}${trailing}`)
    i = j
  }

  return out.join('\n')
}

/** Re-base the body on `indent` so a block inside a list item stays inside it. */
function reindent(body: string[], indent: string): string {
  const lines = [...body]
  while (lines.length && !lines[0].trim()) lines.shift()
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop()
  if (!lines.length) return ''

  const widths = lines.filter((l) => l.trim()).map((l) => l.length - l.trimStart().length)
  const common = Math.min(...widths)
  return lines.map((l) => (l.trim() ? indent + l.slice(common) : l)).join('\n')
}

/**
 * Refuse a rewrite that changed how many delimiters there are or left one
 * sharing a line, since either means remark-math will pair them differently
 * from what we intended.
 */
function isBalanced(rewritten: string, original: string): boolean {
  const count = (s: string) => (s.match(/\$\$/g) ?? []).length
  if (count(rewritten) !== count(original)) return false

  let inFence = false
  for (const line of rewritten.split('\n')) {
    if (FENCE.test(line)) {
      inFence = !inFence
      continue
    }
    if (inFence) continue
    if (line.includes('$$') && line.trim() !== '$$') return false
  }
  return true
}
