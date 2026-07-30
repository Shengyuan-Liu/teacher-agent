// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import type { Citation } from '@/lib/api'
import CitationRef from './CitationRef'

const citation: Citation = {
  n: 2,
  chunk_id: 'c1',
  source_id: '',
  source_title: 'LectureNotes.pdf',
  heading: 'Definitions',
  excerpt: 'Definition 5.3.1. The counting process associated to a sequence.',
  truncated: true,
  images: [],
}

test('hovering the marker reveals the source excerpt', () => {
  const { container } = render(<CitationRef n={2} citation={citation} />)
  const wrap = container.querySelector('.cite-wrap')
  expect(wrap).not.toBeNull()
  // hidden until hovered
  expect(screen.queryByText(/Definition 5.3.1/)).toBeNull()
  fireEvent.mouseEnter(wrap!)
  expect(screen.getByText(/Definition 5.3.1/)).toBeTruthy()
  expect(screen.getByText(/LectureNotes\.pdf/)).toBeTruthy()
})

test('a marker with no matching citation stays a plain [n]', () => {
  const { container } = render(<CitationRef n={5} />)
  expect(container.textContent).toBe('[5]')
  expect(container.querySelector('.cite-wrap')).toBeNull()
})
