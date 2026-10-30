import { beforeEach, describe, expect, it, vi } from 'vitest'
import { advanceRun, graphPath, progressPath } from '../features/research/researchRunMachine'
import { restoreRun, saveRun } from '../features/research/researchRunStorage'
import type { SpiderMindRun } from '../features/research/researchRunTypes'
import { consistency, evidenceId, researchId, snapshotId, status, summary } from './fixture'

const api = vi.hoisted(() => ({
  create: vi.fn(), status: vi.fn(), sources: vi.fn(), startIndex: vi.fn(), index: vi.fn(), startAgent: vi.fn(), agent: vi.fn(),
  startEvidence: vi.fn(), evidence: vi.fn(), graphBuild: vi.fn(), graphStatus: vi.fn(), graphSummary: vi.fn(), graphConsistency: vi.fn(), claims: vi.fn(),
}))
vi.mock('../features/research/researchApi', () => ({ researchApi: api }))

function initial(): SpiderMindRun { return { question: 'What is hybrid retrieval in RAG?', stage: 'CREATING_RESEARCH', startedAt: '2026-10-29', updatedAt: '2026-10-29' } }
beforeEach(() => { vi.clearAllMocks(); localStorage.clear(); api.sources.mockResolvedValue({ sources: [] }) })

describe('question-first research machine', () => {
  it('propagates every ID through the real endpoint sequence and records a grounded result', async () => {
    api.create.mockResolvedValue({ research_job_id: researchId })
    api.sources.mockResolvedValue({ sources: [{ url: 'https://example.com/source', title: 'Source', selected: true, crawl_status: 'COMPLETED', pages: [] }] })
    api.status.mockResolvedValueOnce({ status: 'running', stage: 'searching', total_search_results: 2, selected_seed_count: 0, pages_crawled: 0 }).mockResolvedValue({ status: 'completed', stage: 'completed', total_search_results: 4, selected_seed_count: 1, pages_crawled: 2 })
    api.startIndex.mockResolvedValue({ index_job_id: 'index-1' })
    api.index.mockResolvedValue({ status: 'COMPLETED', documents_indexed: 2, documents_skipped: 0, lexical_records: 4 })
    api.startAgent.mockResolvedValue({ agent_run_id: 'agent-1' })
    api.agent.mockResolvedValue({ status: 'COMPLETED', stop_reason: 'SUFFICIENT_EVIDENCE', current_node: 'FINALIZE', new_pages_crawled: 1 })
    api.startEvidence.mockResolvedValue({ evidence_job_id: evidenceId })
    api.evidence.mockResolvedValue({ status: 'COMPLETED', stage: 'COMPLETED', claims_extracted: 1, citations_validated: 2 })
    api.graphBuild.mockResolvedValue({ graph_snapshot_id: snapshotId })
    api.graphStatus.mockResolvedValue(status)
    api.graphSummary.mockResolvedValue(summary)
    api.graphConsistency.mockResolvedValue(consistency)
    api.claims.mockResolvedValue({ claims: [{ id: 'claim-1', claim: 'A grounded claim', status: 'SUPPORTED', confidence_tier: 'MEDIUM', support_count: 1, contradiction_count: 0, independent_support_groups: 1 }] })
    let run = initial()
    const commit = (value: SpiderMindRun) => { run = value; saveRun(value) }
    for (let i = 0; i < 12; i++) run = await advanceRun(run, commit)
    expect(run.stage).toBe('COMPLETED')
    expect(run.summary?.citations_total).toBe(2)
    expect(run.claims?.[0].claim).toBe('A grounded claim')
    expect(run.sources?.[0].title).toBe('Source')
    expect(run.pagesCrawled).toBe(3)
    expect(api.create).toHaveBeenCalledTimes(1)
    expect(api.startIndex).toHaveBeenCalledWith(researchId)
    expect(api.index).toHaveBeenCalledWith(researchId)
    expect(api.startAgent).toHaveBeenCalledWith(researchId)
    expect(api.agent).toHaveBeenCalledWith(researchId, 'agent-1')
    expect(api.startEvidence).toHaveBeenCalledWith(researchId, 'agent-1')
    expect(api.evidence).toHaveBeenCalledWith(researchId, evidenceId)
    expect(api.graphBuild).toHaveBeenCalledWith(researchId, evidenceId)
    expect(api.graphStatus).toHaveBeenCalledWith(researchId, snapshotId)
    expect(graphPath(run)).toBe(progressPath(researchId) + `/graph/${snapshotId}`)
    expect(restoreRun()?.graphSnapshotId).toBe(snapshotId)
  })

  it('resumes research polling without creating a second job', async () => {
    const saved = { ...initial(), stage: 'RESEARCHING' as const, researchJobId: researchId }
    saveRun(saved)
    api.status.mockResolvedValue({ status: 'running', stage: 'crawling', total_search_results: 3, selected_seed_count: 1, pages_crawled: 1 })
    const run = await advanceRun(restoreRun()!, saveRun)
    expect(run.stage).toBe('RESEARCHING')
    expect(api.create).not.toHaveBeenCalled()
    expect(api.status).toHaveBeenCalledWith(researchId)
  })

  it('stops after a real agent cancellation without starting evidence', async () => {
    api.agent.mockResolvedValue({ status: 'CANCELLED', stop_reason: 'CANCELLED', current_node: 'FINALIZE', new_pages_crawled: 0 })
    const run = await advanceRun({ ...initial(), stage: 'RUNNING_AGENT', researchJobId: researchId, agentRunId: 'agent-1' }, vi.fn())
    expect(run.stage).toBe('CANCELLED')
    expect(api.startEvidence).not.toHaveBeenCalled()
  })

  it('retains claims when graph validation is partial', async () => {
    api.graphStatus.mockResolvedValue({ ...status, status: 'PARTIAL', validation_status: 'PARTIAL' })
    api.claims.mockResolvedValue({ claims: [{ id: 'claim-1', claim: 'Recorded claim', status: 'SUPPORTED', confidence_tier: 'MEDIUM', support_count: 1, contradiction_count: 0, independent_support_groups: 1 }] })
    const run = await advanceRun({ ...initial(), stage: 'BUILDING_GRAPH', researchJobId: researchId, evidenceJobId: evidenceId, graphSnapshotId: snapshotId }, vi.fn())
    expect(run.stage).toBe('PARTIAL')
    expect(run.claims?.[0].claim).toBe('Recorded claim')
    expect(api.graphSummary).not.toHaveBeenCalled()
  })

  it('resumes each downstream stage using saved IDs', async () => {
    const base = { ...initial(), researchJobId: researchId, indexJobId: 'index-1', agentRunId: 'agent-1', evidenceJobId: evidenceId, graphSnapshotId: snapshotId }
    api.index.mockResolvedValue({ status: 'RUNNING', lexical_records: 1 })
    api.agent.mockResolvedValue({ status: 'RUNNING', stop_reason: null, current_node: 'RETRIEVING', new_pages_crawled: 0 })
    api.evidence.mockResolvedValue({ status: 'RUNNING', stage: 'EXTRACTING', claims_extracted: 0, citations_validated: 0 })
    api.graphStatus.mockResolvedValue({ ...status, status: 'RUNNING' })
    for (const stage of ['INDEXING', 'RUNNING_AGENT', 'VERIFYING_EVIDENCE', 'BUILDING_GRAPH'] as const) {
      saveRun({ ...base, stage })
      await advanceRun(restoreRun()!, saveRun)
    }
    expect(api.startIndex).not.toHaveBeenCalled()
    expect(api.startAgent).not.toHaveBeenCalled()
    expect(api.startEvidence).not.toHaveBeenCalled()
    expect(api.graphBuild).not.toHaveBeenCalled()
  })

  it('follows the latest index attempt returned by the backend', async () => {
    api.index.mockResolvedValue({ status: 'RUNNING', index_job_id: 'index-2', lexical_records: 2 })
    const run = await advanceRun({ ...initial(), stage: 'INDEXING', researchJobId: researchId, indexJobId: 'index-1' }, vi.fn())
    expect(run.indexJobId).toBe('index-2')
    expect(run.stage).toBe('INDEXING')
  })

  it('keeps partial research and rejects a zero-source research failure', async () => {
    api.status.mockResolvedValueOnce({ status: 'partial', stage: 'completed', total_search_results: 2, selected_seed_count: 1, pages_crawled: 1 }).mockResolvedValue({ status: 'failed', stage: 'failed', total_search_results: 0, selected_seed_count: 0, pages_crawled: 0, error_message: 'Search failed' })
    const first = await advanceRun({ ...initial(), stage: 'RESEARCHING', researchJobId: researchId }, vi.fn())
    expect(first.stage).toBe('INDEXING')
    expect(first.partialStages).toContain('RESEARCHING')
    const second = await advanceRun({ ...initial(), stage: 'RESEARCHING', researchJobId: researchId }, vi.fn())
    expect(second.stage).toBe('FAILED')
    expect(second.error).toBe('Search failed')
  })

  it('does not repeat an uncertain creation POST after refresh', () => {
    saveRun(initial())
    const restored = restoreRun()
    expect(restored?.stage).toBe('FAILED')
    expect(restored?.error).toMatch(/cannot be verified/)
  })
  it('persists job metadata without claim text or graph payload', () => {
    const run = { ...initial(), stage: 'COMPLETED' as const, researchJobId: researchId, claims: [{ id: 'c', claim: 'Recorded text', status: 'SUPPORTED', confidence_tier: 'MEDIUM', support_count: 1, contradiction_count: 0, independent_support_groups: 1 }], summary }
    saveRun(run)
    expect(restoreRun()?.researchJobId).toBe(researchId)
    expect(restoreRun()?.claims).toBeUndefined()
    expect(restoreRun()?.summary).toBeUndefined()
  })
})
