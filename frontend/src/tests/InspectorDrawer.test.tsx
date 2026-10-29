import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { InspectorDrawer } from '../components/inspector/InspectorDrawer'
import { claim, claimView, consistency, graph, summary, status, citation, document } from './fixture'

const base = {
  open: true, detail: null, graph, status, summary, consistency,
  onClose: vi.fn(), onFullPassage: vi.fn(), onNavigate: vi.fn(), onPath: vi.fn(),
}

describe('inspector', () => {
  it('shows support and contradiction together with exact citation text', () => {
    render(<InspectorDrawer {...base} node={claim} claim={claimView} />)
    expect(screen.getByText('Supporting')).toBeInTheDocument()
    expect(screen.getByText('Contradicting')).toBeInTheDocument()
    expect(screen.getAllByText(/Recall increased on Dataset X/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Recall decreased on Dataset X/).length).toBeGreaterThan(0)
    expect(screen.getByText('UNRESOLVED')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Citations' }))
    expect(screen.getAllByText('Offsets 0–30')).toHaveLength(2)
  })

  it('shows citation offsets and blocks unsafe source links', () => {
    const unsafe = { ...citation, properties: { ...citation.properties, source_url: 'javascript:alert(1)' } } as typeof citation
    render(<InspectorDrawer {...base} node={unsafe} claim={null} />)
    expect(screen.getAllByText(/Recall increased on Dataset X/).length).toBeGreaterThan(0)
    expect(screen.getByText('No public URL')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Open source' })).not.toBeInTheDocument()
  })

  it('renders document links safely and real summary counts', () => {
    const { rerender } = render(<InspectorDrawer {...base} node={document} claim={null} />)
    const link = screen.getByRole('link', { name: 'Open document' })
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    rerender(<InspectorDrawer {...base} node={null} claim={null} />)
    expect(screen.getByText('Research at a glance')).toBeInTheDocument()
    expect(within(screen.getByText('Claims').parentElement!).getByText('1')).toBeInTheDocument()
  })
})
