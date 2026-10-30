import { apiRequest } from '../../api/client'
import { graphApi } from '../../api/graphApi'
import type { ClaimSummary, ResearchSource } from './researchRunTypes'

const base = (id: string) => `/api/v1/research/${encodeURIComponent(id)}`
const json = (body: object) => ({ method: 'POST', body: JSON.stringify(body) })
export const researchApi = {
  create: (question: string) => apiRequest<{ research_job_id: string }>(`/api/v1/research`, json({ question })),
  status: (id: string) => apiRequest<{ status: string; stage: string; error_message: string | null; total_search_results: number; selected_seed_count: number; pages_crawled: number }>(base(id)),
  sources: (id: string) => apiRequest<{ sources: ResearchSource[] }>(`${base(id)}/sources?selected=true&limit=100`),
  startIndex: (id: string) => apiRequest<{ index_job_id: string }>(`${base(id)}/index`, { method: 'POST' }),
  index: (id: string) => apiRequest<{ index_job_id?: string; status: string; documents_indexed?: number; documents_skipped?: number; chunks_created?: number; lexical_records?: number; error_message?: string }>(`${base(id)}/index`),
  startAgent: (id: string) => apiRequest<{ agent_run_id: string }>(`${base(id)}/agent`, json({})),
  agent: (id: string, runId: string) => apiRequest<{ status: string; stop_reason: string | null; error_message: string | null; current_node: string; new_pages_crawled: number }>(`${base(id)}/agent/${encodeURIComponent(runId)}`),
  cancelAgent: (id: string, runId: string) => apiRequest<unknown>(`${base(id)}/agent/${encodeURIComponent(runId)}/cancel`, { method: 'POST' }),
  startEvidence: (id: string, agentRunId: string) => apiRequest<{ evidence_job_id: string }>(`${base(id)}/evidence`, json({ agent_run_id: agentRunId })),
  evidence: (id: string, evidenceId: string) => apiRequest<{ status: string; stage: string; claims_extracted: number; citations_validated: number; error: string | null }>(`${base(id)}/evidence/${encodeURIComponent(evidenceId)}`),
  claims: (id: string, evidenceId: string) => apiRequest<{ claims: ClaimSummary[] }>(`${base(id)}/evidence/${encodeURIComponent(evidenceId)}/claims`),
  graphBuild: graphApi.build,
  graphStatus: graphApi.status,
  graphSummary: graphApi.summary,
  graphConsistency: graphApi.consistency,
}
