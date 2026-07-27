import { describe, expect, it } from 'vitest'
import { shouldStayOpen } from './safeTriangle'

// Card sits above the pointer, its bottom edge facing the cursor.
const card = { left: 100, right: 300, top: 20, bottom: 80 }
const origin = { x: 200, y: 120 }

describe('shouldStayOpen', () => {
  it('stays open inside the card', () => {
    expect(shouldStayOpen({ x: 200, y: 50 }, origin, card)).toBe(true)
  })

  it('stays open on a diagonal path towards a far corner', () => {
    expect(shouldStayOpen({ x: 150, y: 100 }, origin, card)).toBe(true)
    expect(shouldStayOpen({ x: 260, y: 95 }, origin, card)).toBe(true)
  })

  it('closes when the pointer veers away sideways', () => {
    expect(shouldStayOpen({ x: 40, y: 110 }, origin, card)).toBe(false)
    expect(shouldStayOpen({ x: 380, y: 110 }, origin, card)).toBe(false)
  })

  it('closes when the pointer moves away from the card', () => {
    expect(shouldStayOpen({ x: 200, y: 200 }, origin, card)).toBe(false)
  })

  it('works when the card is below the pointer', () => {
    const below = { left: 100, right: 300, top: 160, bottom: 220 }
    expect(shouldStayOpen({ x: 150, y: 140 }, origin, below)).toBe(true)
    expect(shouldStayOpen({ x: 150, y: 60 }, origin, below)).toBe(false)
  })
})
