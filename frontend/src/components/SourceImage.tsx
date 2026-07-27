import { useEffect, useState } from 'react'
import { fetchImage } from '@/lib/api'

/**
 * Figures live behind the authenticated API, so they cannot be loaded with a
 * plain `src` — the browser would send no Authorization header. Fetch the bytes
 * and hand the element an object URL instead.
 */
export default function SourceImage({
  workspaceId,
  sourceId,
  imageId,
}: {
  workspaceId: string
  sourceId: string
  imageId: string
}) {
  const [url, setUrl] = useState<string>()
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let objectUrl: string | undefined
    let cancelled = false

    fetchImage(workspaceId, sourceId, imageId)
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
      })
      .catch(() => !cancelled && setFailed(true))

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [workspaceId, sourceId, imageId])

  if (failed) return null
  if (!url) return <span className="figure-loading" />
  return <img className="figure" src={url} alt={`Figure ${imageId}`} />
}
