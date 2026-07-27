import { describe, expect, it } from 'vitest'
import { normaliseMath } from './markdown'

describe('normaliseMath', () => {
  it('moves single-line $$ delimiters onto their own lines', () => {
    expect(normaliseMath('$$x^2 + y^2 = z^2$$')).toBe('$$\nx^2 + y^2 = z^2\n$$')
  })

  it('pushes a trailing citation out of the math body', () => {
    expect(normaliseMath('$$a = b$$ [1]')).toBe('$$\na = b\n$$\n\n[1]')
  })

  it('closes a multi-line block whose delimiter has trailing text', () => {
    const input = ['$$\\nabla f = \\begin{pmatrix}', 'a\\\\', 'b', '\\end{pmatrix}$$ [1]'].join('\n')
    expect(normaliseMath(input)).toBe(
      ['$$', '\\nabla f = \\begin{pmatrix}', 'a\\\\', 'b', '\\end{pmatrix}', '$$', '', '[1]'].join(
        '\n',
      ),
    )
  })

  it('leaves already-block math untouched', () => {
    const block = '$$\n\\sum_{i=1}^n i\n$$'
    expect(normaliseMath(block)).toBe(block)
  })

  it('leaves inline single-dollar math untouched', () => {
    const text = 'The value $x_i$ is positive.'
    expect(normaliseMath(text)).toBe(text)
  })

  it('does not touch $$ that starts mid-line', () => {
    const text = 'see $$a$$ here'
    expect(normaliseMath(text)).toBe(text)
  })

  it('never merges two equations separated by prose', () => {
    const input = ['$$a = 1$$ [1]', '', '## Heading', '', 'Some prose.', '', '$$b = 2$$'].join('\n')
    const out = normaliseMath(input)
    expect(out).toContain('## Heading')
    expect(out).toContain('Some prose.')
    expect(out.split('\n').filter((l) => l.trim() === '$$')).toHaveLength(4)
  })

  it('leaves an unclosed block alone while it is still streaming', () => {
    const partial = 'text\n\n$$\\frac{a}{b'
    expect(normaliseMath(partial)).toBe(partial)
  })

  it('keeps an indented block inside its list item', () => {
    const input = ['- **Exact stepsize:** ray:', '  $$t^k \\in \\operatorname{argmin} f(x)$$'].join(
      '\n',
    )
    expect(normaliseMath(input)).toBe(
      [
        '- **Exact stepsize:** ray:',
        '  $$',
        '  t^k \\in \\operatorname{argmin} f(x)',
        '  $$',
      ].join('\n'),
    )
  })

  it('never leaves a $$ sharing a line with other text', () => {
    const input = [
      '- item:',
      '  $$a = 1$$',
      '',
      '## Heading',
      '',
      '$$b = 2$$ [3]',
      '',
      'prose',
    ].join('\n')
    const out = normaliseMath(input)
    for (const line of out.split('\n')) {
      if (line.includes('$$')) expect(line.trim()).toBe('$$')
    }
    expect(out).toContain('## Heading')
    expect(out).toContain('prose')
  })

  it('ignores $$ inside fenced code', () => {
    const code = ['```', '$$not math$$ trailing', '```'].join('\n')
    expect(normaliseMath(code)).toBe(code)
  })
})
