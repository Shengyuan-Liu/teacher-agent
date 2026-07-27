export interface Point {
  x: number
  y: number
}

export interface Rect {
  left: number
  right: number
  top: number
  bottom: number
}

function sign(a: Point, b: Point, c: Point): number {
  return (a.x - c.x) * (b.y - c.y) - (b.x - c.x) * (a.y - c.y)
}

export function inTriangle(p: Point, a: Point, b: Point, c: Point): boolean {
  const d1 = sign(p, a, b)
  const d2 = sign(p, b, c)
  const d3 = sign(p, c, a)
  const hasNeg = d1 < 0 || d2 < 0 || d3 < 0
  const hasPos = d1 > 0 || d2 > 0 || d3 > 0
  return !(hasNeg && hasPos)
}

export function inRect(p: Point, r: Rect): boolean {
  return p.x >= r.left && p.x <= r.right && p.y >= r.top && p.y <= r.bottom
}

/**
 * Keep a popover open while the pointer travels towards it.
 *
 * The corridor is the triangle from where the pointer left the trigger to the
 * two corners of the popover edge facing it, so a diagonal path that briefly
 * leaves both elements does not dismiss the card.
 */
export function shouldStayOpen(pointer: Point, origin: Point, card: Rect): boolean {
  if (inRect(pointer, card)) return true

  const facingTop = card.bottom <= origin.y
  const corners: [Point, Point] = facingTop
    ? [
        { x: card.left, y: card.bottom },
        { x: card.right, y: card.bottom },
      ]
    : [
        { x: card.left, y: card.top },
        { x: card.right, y: card.top },
      ]

  return inTriangle(pointer, origin, corners[0], corners[1])
}
