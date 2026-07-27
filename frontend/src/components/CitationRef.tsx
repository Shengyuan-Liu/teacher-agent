import { useEffect, useRef, useState } from 'react'
import SourceImage from '@/components/SourceImage'
import type { Citation } from '@/lib/api'
import { shouldStayOpen, type Point } from '@/lib/safeTriangle'

const GRACE_MS = 250

export default function CitationRef({
  n,
  citation,
  workspaceId,
}: {
  n: number
  citation?: Citation
  workspaceId?: string
}) {
  const [open, setOpen] = useState(false)
  const card = useRef<HTMLSpanElement>(null)
  const origin = useRef<Point>({ x: 0, y: 0 })
  const closeTimer = useRef<number | undefined>(undefined)

  useEffect(() => {
    if (!open) return

    const onMove = (e: MouseEvent) => {
      const box = card.current?.getBoundingClientRect()
      const pointer = { x: e.clientX, y: e.clientY }
      const safe = box ? shouldStayOpen(pointer, origin.current, box) : false

      if (safe) {
        window.clearTimeout(closeTimer.current)
        closeTimer.current = undefined
      } else if (closeTimer.current === undefined) {
        closeTimer.current = window.setTimeout(() => setOpen(false), GRACE_MS)
      }
    }

    document.addEventListener('mousemove', onMove)
    return () => {
      document.removeEventListener('mousemove', onMove)
      window.clearTimeout(closeTimer.current)
      closeTimer.current = undefined
    }
  }, [open])

  if (!citation) return <span>[{n}]</span>

  return (
    <span
      className="cite"
      onMouseEnter={(e) => {
        origin.current = { x: e.clientX, y: e.clientY }
        setOpen(true)
      }}
    >
      [{n}]
      {open && (
        <span className="cite-pop" ref={card}>
          <span className="cite-pop-head">
            {citation.source_title}
            {citation.heading ? ` · ${citation.heading}` : ''}
          </span>
          <span className="cite-pop-body">
            {citation.excerpt}
            {citation.truncated ? '…' : ''}
          </span>
          {workspaceId && citation.images.length > 0 && (
            <span className="cite-pop-figures">
              {citation.images.map((imageId) => (
                <SourceImage
                  key={imageId}
                  workspaceId={workspaceId}
                  sourceId={citation.source_id}
                  imageId={imageId}
                />
              ))}
            </span>
          )}
        </span>
      )}
    </span>
  )
}
