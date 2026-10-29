import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TopHeader } from '../components/layout/TopHeader'
import { Sidebar } from '../components/layout/Sidebar'
import { allFilters } from '../features/graph/graphAdapter'
import { claim, graph, status, summary } from './fixture'

describe('search and graph filters', () => {
  it('searches loaded nodes and focuses the selected result', () => {
    const onFocus = vi.fn()
    render(<TopHeader nodes={graph.nodes} onFocus={onFocus} onRefresh={vi.fn()} onFit={vi.fn()} loaded consistency="VALID" />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Search loaded graph' }), { target: { value: 'System A improves' } })
    fireEvent.click(screen.getByRole('option', { name: /System A improves recall/i }))
    expect(onFocus).toHaveBeenCalledWith(claim)
  })

  it('applies node, status, confidence, and edge visibility controls', () => {
    const onFilters = vi.fn()
    const { rerender } = render(<Sidebar filters={allFilters()} onFilters={onFilters} status={status} summary={summary} onContradictions={vi.fn()} onLoadAnother={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Claims/ }))
    expect(onFilters.mock.calls.at(-1)?.[0].nodeTypes.has('CLAIM')).toBe(true)
    rerender(<Sidebar filters={allFilters()} onFilters={onFilters} status={status} summary={summary} onContradictions={vi.fn()} onLoadAnother={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Settings & filters/i }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Filter claim status' }), { target: { value: 'CONTESTED' } })
    expect(onFilters.mock.calls.at(-1)?.[0].status).toBe('CONTESTED')
    fireEvent.change(screen.getByRole('combobox', { name: 'Filter confidence' }), { target: { value: 'HIGH' } })
    expect(onFilters.mock.calls.at(-1)?.[0].confidence).toBe('HIGH')
    fireEvent.click(screen.getByLabelText('Origin'))
    expect(onFilters.mock.calls.at(-1)?.[0].edgeTypes.has('ORIGINATED_FROM')).toBe(true)
  })
})
