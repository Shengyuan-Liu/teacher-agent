import Markdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import { normaliseMath } from '@/lib/markdown'
import 'katex/dist/katex.min.css'

/** Markdown with maths, for content outside the chat (plans, quiz stems). */
export default function MarkdownBlock({ content }: { content: string }) {
  return (
    <div className="md">
      <Markdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
        {normaliseMath(content)}
      </Markdown>
    </div>
  )
}
