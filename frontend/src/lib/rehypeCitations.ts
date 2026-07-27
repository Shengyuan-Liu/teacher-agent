import type { Element, Root, Text } from 'hast'
import { visit } from 'unist-util-visit'

const REF = /\[(\d+)\]/g
const SKIP = new Set(['cite', 'code', 'pre'])

/** Turns `[1]` in the answer prose into `<cite data-n="1">` so it can be hovered. */
export function rehypeCitations() {
  return (tree: Root) => {
    visit(tree, 'text', (node: Text, index, parent) => {
      if (!parent || index === undefined) return
      if (parent.type === 'element' && SKIP.has((parent as Element).tagName)) return
      if (!REF.test(node.value)) return
      REF.lastIndex = 0

      const children: (Text | Element)[] = []
      let last = 0
      for (const match of node.value.matchAll(REF)) {
        const start = match.index
        if (start > last) children.push({ type: 'text', value: node.value.slice(last, start) })
        children.push({
          type: 'element',
          tagName: 'cite',
          properties: { dataN: match[1] },
          children: [{ type: 'text', value: match[0] }],
        })
        last = start + match[0].length
      }
      if (last < node.value.length) children.push({ type: 'text', value: node.value.slice(last) })

      parent.children.splice(index, 1, ...children)
      return index + children.length
    })
  }
}
