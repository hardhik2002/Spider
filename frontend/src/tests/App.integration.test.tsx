import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { GraphNodeDto, SubgraphDto } from '../types/graph'
import { App } from '../app/App'
import { claim, claimView, consistency, evidenceId, graph, initialGraph, researchId, snapshotId, status, summary } from './fixture'

const api = vi.hoisted(() => ({
  build: vi.fn(), status: vi.fn(), summary: vi.fn(), consistency: vi.fn(),
  subgraph: vi.fn(), node: vi.fn(), claim: vi.fn(), contradictions: vi.fn(), path: vi.fn(),
}))
vi.mock('../api/graphApi', () => ({ graphApi: api }))
vi.mock('../components/graph/GraphWorkspace', async () => {
  const React = await import('react')
  return { GraphWorkspace: ({ graph: visible, onSelect, onExpand }: {
    graph: SubgraphDto; onSelect: (node: GraphNodeDto) => void; onExpand: (id: string) => void
  }) => React.createElement('div', { 'data-testid': 'graph-workspace' },
    visible.nodes.find((node) => node.type === 'CLAIM') && React.createElement('button', { onClick: () => onSelect(visible.nodes.find((node) => node.type === 'CLAIM')!) }, 'Select claim'),
    React.createElement('button', { onClick: () => onExpand('claim:claim-1') }, 'Expand claim'),
  ) }
})

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  window.history.replaceState(null, '', '/developer/open-graph')
  api.status.mockResolvedValue(status)
  api.summary.mockResolvedValue(summary)
  api.consistency.mockResolvedValue(consistency)
  api.subgraph.mockImplementation(async (_research: string, _snapshot: string, root: string) => root.startsWith('research:') ? initialGraph : graph)
  api.node.mockImplementation(async (_research: string, _snapshot: string, nodeId: string) => ({ node: graph.nodes.find((node) => node.id === nodeId), neighbors: [], incoming_edge_counts: {}, outgoing_edge_counts: {} }))
  api.claim.mockResolvedValue(claimView)
  api.contradictions.mockResolvedValue({ claims: [claimView] })
  api.path.mockResolvedValue({ count: 1, paths: [{ nodes: [graph.nodes[0], graph.nodes[1], claim], edges: graph.edges.slice(0, 2) }] })
})

describe('graph workspace integration', () => {
  it('loads a Phase 7 graph, inspects a claim, shows conflict/citation, and traces a path', async () => {
    render(<App />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Research Job ID' }), { target: { value: researchId } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Graph Snapshot ID' }), { target: { value: snapshotId } })
    fireEvent.click(screen.getByRole('button', { name: /Open graph/i }))
    await screen.findByRole('button', { name: 'Select claim' })
    expect(api.subgraph).toHaveBeenCalledWith(researchId, snapshotId, `research:${researchId}`, 2, 100, 'outgoing')
    fireEvent.click(screen.getByRole('button', { name: 'Select claim' }))
    await screen.findByText('Supporting')
    expect(screen.getByText('Contradicting')).toBeInTheDocument()
    expect(screen.getByText(/Recall increased on Dataset X/)).toBeInTheDocument()
    expect(screen.getByText(/Recall decreased on Dataset X/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Graph' }))
    fireEvent.click(screen.getByRole('button', { name: /Trace from research/i }))
    await waitFor(() => expect(api.path).toHaveBeenCalledWith(researchId, snapshotId, `research:${researchId}`, claim.id))
    expect(await screen.findByText(/Path highlighted/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Contradictions/i }))
    expect(await screen.findByText('1 supporting · 1 contradicting passages')).toBeInTheDocument()
  })

  it('presents an API error when a snapshot is missing', async () => {
    api.status.mockRejectedValueOnce(new Error('Graph snapshot not found'))
    render(<App />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Research Job ID' }), { target: { value: researchId } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Graph Snapshot ID' }), { target: { value: snapshotId } })
    fireEvent.click(screen.getByRole('button', { name: /Open graph/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Graph snapshot not found')
  })

  it('starts a graph build from a completed evidence job and opens its snapshot', async () => {
    api.build.mockResolvedValueOnce({ graph_job_id: 'build-1', graph_snapshot_id: snapshotId, status: 'pending' })
    render(<App />)
    fireEvent.click(screen.getByRole('tab', { name: /Build graph/i }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Research Job ID' }), { target: { value: researchId } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Evidence Job ID' }), { target: { value: evidenceId } })
    fireEvent.click(screen.getByRole('button', { name: /Build and open/i }))
    await screen.findByRole('button', { name: 'Select claim' })
    expect(api.build).toHaveBeenCalledWith(researchId, evidenceId)
    expect(api.status).toHaveBeenCalledWith(researchId, snapshotId)
  })
})
