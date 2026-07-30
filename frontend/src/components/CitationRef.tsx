import { useRef, useState } from 'react'
import SourceImage from '@/components/SourceImage'
import { fetchSourceFile, type Citation } from '@/lib/api'

const GRACE_MS = 200

/**
 * A `[n]` citation marker that reveals its source on hover.
 *
 * Open while the pointer is over the marker or the card; a short grace period
 * on leave lets the pointer cross the small gap between them without dismissing
 * it. (An earlier version tracked a "safe triangle" off a global mousemove,
 * which collapsed to a point at the entry position and dismissed the card on
 * the slightest movement — so it often never appeared at all.)
 */
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
  const closeTimer = useRef<number | undefined>(undefined)

  if (!citation) return <span>[{n}]</span>

  const show = () => {
    window.clearTimeout(closeTimer.current)
    setOpen(true)
  }
  const hide = () => {
    closeTimer.current = window.setTimeout(() => setOpen(false), GRACE_MS)
  }

  const openSource = async () => {
    const external = citation.source_url ?? citation.source_origin
    if (external) {
      window.open(external, '_blank', 'noopener,noreferrer')
      return
    }
    if (!workspaceId || !citation.source_id) return
    const blob = await fetchSourceFile(workspaceId, citation.source_id)
    const url = URL.createObjectURL(blob)
    window.open(url, '_blank', 'noopener,noreferrer')
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
  }

  return (
    <span className="cite-wrap" onMouseEnter={show} onMouseLeave={hide}>
      <button
        type="button"
        className="cite cite-button"
        onClick={openSource}
        disabled={!((citation.source_url ?? citation.source_origin) || (workspaceId && citation.source_id))}
        title="Open source"
      >
        [{n}]
      </button>
      {open && (
        <span className="cite-pop" onMouseEnter={show} onMouseLeave={hide}>
          <span className="cite-pop-head">
            {citation.source_title}
            {citation.heading ? ` · ${citation.heading}` : ''}
            {citation.source_position != null ? ` · section ${citation.source_position + 1}` : ''}
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
