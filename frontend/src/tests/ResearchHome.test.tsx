import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../app/App'

beforeEach(() => { localStorage.clear(); window.history.replaceState(null, '', '/') })
describe('normal landing and developer route', () => {
  it('asks for a question and validates before submission', () => {
    render(<App />)
    expect(screen.queryByRole('textbox', { name: 'Research Job ID' })).not.toBeInTheDocument()
    const question = screen.getByRole('textbox', { name: /YOUR RESEARCH QUESTION/i })
    fireEvent.click(screen.getByRole('button', { name: /^Research$/ }))
    expect(screen.getByRole('alert')).toHaveTextContent('at least two words')
    fireEvent.change(question, { target: { value: 'Compare GraphRAG and traditional RAG architectures' } })
    expect(question).toHaveValue('Compare GraphRAG and traditional RAG architectures')
  })
  it('submits one trimmed question and navigates using the returned internal ID', async () => {
    const response = { ok: true, json: async () => ({ research_job_id: '11111111-1111-4111-8111-111111111111', status: 'pending' }) } as Response
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response)
    render(<App />)
    fireEvent.change(screen.getByRole('textbox', { name: /YOUR RESEARCH QUESTION/i }), { target: { value: '  What is hybrid retrieval in RAG?  ' } })
    fireEvent.click(screen.getByRole('button', { name: /^Research$/ }))
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1))
    expect(JSON.parse(String(fetchSpy.mock.calls[0][1]?.body))).toEqual({ question: 'What is hybrid retrieval in RAG?' })
    await waitFor(() => expect(window.location.pathname).toBe('/research/11111111-1111-4111-8111-111111111111'))
    expect(screen.queryByRole('textbox', { name: 'Research Job ID' })).not.toBeInTheDocument()
    fetchSpy.mockRestore()
  })
  it('re-enables the question form when creation fails before an ID is returned', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
    render(<App />)
    fireEvent.change(screen.getByRole('textbox', { name: /YOUR RESEARCH QUESTION/i }), { target: { value: 'What is hybrid retrieval in RAG?' } })
    fireEvent.click(screen.getByRole('button', { name: /^Research$/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot reach SpiderMind')
    expect(screen.getByRole('button', { name: /^Research$/ })).toBeEnabled()
    fetchSpy.mockRestore()
  })
  it('keeps manual snapshot loading behind Developer Tools', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Developer Tools' }))
    expect(window.location.pathname).toBe('/developer/open-graph')
    expect(screen.getByRole('textbox', { name: 'Research Job ID' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Graph Snapshot ID' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Close graph loader' }))
    expect(window.location.pathname).toBe('/')
  })
  it('shows real saved result data and opens its graph route', () => {
    const run = { question: 'What is hybrid retrieval in RAG?', stage: 'COMPLETED', startedAt: '2026-10-29', updatedAt: '2026-10-29', researchJobId: '11111111-1111-4111-8111-111111111111', graphSnapshotId: '22222222-2222-4222-8222-222222222222', graphValid: true, summary: { claims_corroborated: 1, claims_supported: 2, claims_contested: 1, citations_total: 5, documents_total: 3, source_groups_total: 2 }, claims: [{ id: 'claim-1', claim: 'A supported finding', status: 'SUPPORTED', confidence_tier: 'MEDIUM', support_count: 1, contradiction_count: 0, independent_support_groups: 1 }], sources: [{ url: 'https://example.com/study', title: 'Source study', selected: true, crawl_status: 'COMPLETED', pages: [{ url: 'https://example.com/study', status: 'COMPLETED' }] }] }
    localStorage.setItem('spidermind.activeResearchRun', JSON.stringify(run))
    window.history.replaceState(null, '', `/research/${run.researchJobId}`)
    const spy = vi.spyOn(window.history, 'pushState')
    render(<App />)
    expect(screen.getByText('A supported finding')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Verified claims' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open public source/i })).toHaveAttribute('href', 'https://example.com/study')
    fireEvent.click(screen.getByRole('button', { name: /Explore Spider Graph/i }))
    expect(spy).toHaveBeenCalledWith(null, '', `/research/${run.researchJobId}/graph/${run.graphSnapshotId}`)
    spy.mockRestore()
  })
  it('reloads result details from saved IDs after refresh', async () => {
    const researchJobId = '11111111-1111-4111-8111-111111111111'
    const evidenceJobId = '33333333-3333-4333-8333-333333333333'
    const graphSnapshotId = '22222222-2222-4222-8222-222222222222'
    localStorage.setItem('spidermind.activeResearchRun', JSON.stringify({ question: 'What is hybrid retrieval in RAG?', stage: 'COMPLETED', startedAt: '2026-10-29', updatedAt: '2026-10-29', researchJobId, evidenceJobId, graphSnapshotId, graphValid: true }))
    window.history.replaceState(null, '', `/research/${researchJobId}`)
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      const data = url.includes('/sources') ? { sources: [] } : url.includes('/claims') ? { claims: [{ id: 'claim-1', claim: 'Persisted claim from API', status: 'SUPPORTED', confidence_tier: 'MEDIUM', support_count: 1, contradiction_count: 0, independent_support_groups: 1 }] } : { claims_corroborated: 0, claims_supported: 1, claims_contested: 0, citations_total: 1, documents_total: 1, source_groups_total: 1 }
      return { ok: true, json: async () => data } as Response
    })
    render(<App />)
    expect(await screen.findByText('Persisted claim from API')).toBeInTheDocument()
    expect(fetchSpy).toHaveBeenCalledTimes(3)
    expect(localStorage.getItem('spidermind.activeResearchRun')).not.toContain('Persisted claim from API')
    fetchSpy.mockRestore()
  })
})
