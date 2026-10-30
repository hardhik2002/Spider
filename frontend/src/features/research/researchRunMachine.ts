import { researchApi } from './researchApi'
import type { ResearchStage, SpiderMindRun } from './researchRunTypes'

const terminal = new Set(['COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED', 'BUDGET_EXHAUSTED'])
const upper = (value: string) => value.toUpperCase()
const patch = (run: SpiderMindRun, values: Partial<SpiderMindRun>): SpiderMindRun => ({ ...run, ...values, updatedAt: new Date().toISOString() })
export const graphPath = (run: SpiderMindRun) => `/research/${encodeURIComponent(run.researchJobId!)}/graph/${encodeURIComponent(run.graphSnapshotId!)}`
export const progressPath = (id: string) => `/research/${encodeURIComponent(id)}`

export async function advanceRun(run: SpiderMindRun, commit: (run: SpiderMindRun) => void): Promise<SpiderMindRun> {
  const move = (values: Partial<SpiderMindRun>) => { run = patch(run, values); commit(run); return run }
  const id = run.researchJobId
  if (run.stage === 'CREATING_RESEARCH') {
    // This POST is deliberately never retried automatically after a reload.
    if (id) return move({ stage: 'RESEARCHING' })
    const result = await researchApi.create(run.question)
    return move({ researchJobId: result.research_job_id, stage: 'RESEARCHING' })
  }
  if (!id) throw new Error('The research job was not saved. Start a new research run.')
  if (run.stage === 'RESEARCHING') {
    const value = await researchApi.status(id)
    move({ researchStatus: value.status, researchStep: value.stage, searchResults: value.total_search_results, sourcesSelected: value.selected_seed_count, pagesCrawled: value.pages_crawled, initialPagesCrawled: value.pages_crawled })
    if (!terminal.has(upper(value.status))) return run
    try { move({ sources: (await researchApi.sources(id)).sources }) } catch { /* source detail is optional; status still drives the workflow */ }
    if (upper(value.status) === 'CANCELLED') return move({ stage: 'CANCELLED' })
    if (upper(value.status) === 'FAILED' || value.pages_crawled < 1) return move({ stage: 'FAILED', error: value.error_message || 'No usable public sources were crawled.' })
    return move({ stage: 'INDEXING', partialStages: upper(value.status) === 'PARTIAL' ? [...(run.partialStages ?? []), 'RESEARCHING'] : run.partialStages, warning: upper(value.status) === 'PARTIAL' ? 'Some searches or sources failed; continuing with available pages.' : undefined })
  }
  if (run.stage === 'INDEXING') {
    if (!run.indexJobId) {
      const value = await researchApi.startIndex(id)
      return move({ indexJobId: value.index_job_id })
    }
    const value = await researchApi.index(id)
    if (value.index_job_id && value.index_job_id !== run.indexJobId) move({ indexJobId: value.index_job_id, warning: 'A newer indexing attempt is active. Following its recorded status.' })
    move({ documentsIndexed: (value.documents_indexed ?? 0) + (value.documents_skipped ?? 0), chunksCreated: value.lexical_records ?? value.chunks_created ?? 0 })
    if (!terminal.has(upper(value.status))) return run
    if (upper(value.status) === 'FAILED' || (value.lexical_records ?? 0) < 1) return move({ stage: 'FAILED', error: value.error_message || 'No searchable evidence was indexed.' })
    return move({ stage: 'RUNNING_AGENT', partialStages: upper(value.status) === 'PARTIAL' ? [...(run.partialStages ?? []), 'INDEXING'] : run.partialStages, warning: upper(value.status) === 'PARTIAL' ? 'Some documents could not be indexed.' : run.warning })
  }
  if (run.stage === 'RUNNING_AGENT') {
    if (!run.agentRunId) {
      const value = await researchApi.startAgent(id)
      return move({ agentRunId: value.agent_run_id })
    }
    const value = await researchApi.agent(id, run.agentRunId)
    move({ agentStatus: value.status, agentStopReason: value.stop_reason ?? undefined, agentPagesCrawled: value.new_pages_crawled ?? 0, pagesCrawled: (run.initialPagesCrawled ?? 0) + (value.new_pages_crawled ?? 0) })
    if (!terminal.has(upper(value.status))) return run
    if (upper(value.status) === 'CANCELLED') return move({ stage: 'CANCELLED', warning: 'Research was stopped at your request.' })
    if (upper(value.status) === 'FAILED') return move({ stage: 'PARTIAL', partialStages: [...(run.partialStages ?? []), 'RUNNING_AGENT'], warning: value.error_message || 'The research agent stopped. Available indexed sources can still be inspected.' })
    return move({ stage: 'VERIFYING_EVIDENCE', partialStages: upper(value.status) !== 'COMPLETED' ? [...(run.partialStages ?? []), 'RUNNING_AGENT'] : run.partialStages, warning: upper(value.status) !== 'COMPLETED' ? `The research agent stopped with ${value.status.toLowerCase().replaceAll('_', ' ')}. Verifying available evidence.` : run.warning })
  }
  if (run.stage === 'VERIFYING_EVIDENCE') {
    if (!run.evidenceJobId) {
      const value = await researchApi.startEvidence(id, run.agentRunId!)
      return move({ evidenceJobId: value.evidence_job_id })
    }
    const value = await researchApi.evidence(id, run.evidenceJobId)
    move({ claimsExtracted: value.claims_extracted, citationsValidated: value.citations_validated, evidenceStep: value.stage })
    if (!terminal.has(upper(value.status))) return run
    if (upper(value.status) === 'FAILED' || (value.claims_extracted ?? 0) < 1) return move({ stage: 'PARTIAL', partialStages: [...(run.partialStages ?? []), 'VERIFYING_EVIDENCE'], warning: value.error || 'No verified claims are available from this run.' })
    return move({ stage: 'BUILDING_GRAPH', partialStages: upper(value.status) === 'PARTIAL' ? [...(run.partialStages ?? []), 'VERIFYING_EVIDENCE'] : run.partialStages, warning: upper(value.status) === 'PARTIAL' ? 'Claim verification completed with some failures.' : run.warning })
  }
  if (run.stage === 'BUILDING_GRAPH') {
    if (!run.graphSnapshotId) {
      const value = await researchApi.graphBuild(id, run.evidenceJobId!)
      return move({ graphSnapshotId: value.graph_snapshot_id })
    }
    const value = await researchApi.graphStatus(id, run.graphSnapshotId)
    if (!terminal.has(upper(value.status))) return run
    if (upper(value.status) !== 'COMPLETED') return move({ stage: 'PARTIAL', partialStages: [...(run.partialStages ?? []), 'BUILDING_GRAPH'], warning: 'Graph build or validation is incomplete. Verified claims remain available.', graphValid: false, claims: (await researchApi.claims(id, run.evidenceJobId!)).claims })
    const [summary, consistency, claims] = await Promise.all([
      researchApi.graphSummary(id, run.graphSnapshotId), researchApi.graphConsistency(id, run.graphSnapshotId), researchApi.claims(id, run.evidenceJobId!),
    ])
    const graphValid = consistency.status === 'VALID'
    return move({ summary, claims: claims.claims, graphValid, stage: graphValid ? 'COMPLETED' : 'PARTIAL', partialStages: graphValid ? run.partialStages : [...(run.partialStages ?? []), 'BUILDING_GRAPH'], warning: graphValid ? run.warning : 'Graph validation is incomplete. Verified claims are available below.' })
  }
  return run
}

export function stageLabel(stage: ResearchStage): string {
  return ({ CREATING_RESEARCH: 'Starting research', RESEARCHING: 'Searching and reading sources', INDEXING: 'Indexing evidence', RUNNING_AGENT: 'Checking for missing information', VERIFYING_EVIDENCE: 'Verifying claims and citations', BUILDING_GRAPH: 'Building research graph', COMPLETED: 'Research complete', PARTIAL: 'Partial research available', FAILED: 'Research stopped', CANCELLED: 'Research stopped' })[stage]
}
