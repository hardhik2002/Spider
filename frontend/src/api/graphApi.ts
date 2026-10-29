import { apiRequest } from './client'
import type {
  ClaimViewDto, ConsistencyDto, GraphBuildDto, GraphStatusDto,
  NodeDetailDto, PathResponseDto, SubgraphDto, GraphSummaryDto,
} from '../types/graph'

const base = (researchId: string) => `/api/v1/research/${encodeURIComponent(researchId)}/graph`
const snapshot = (researchId: string, snapshotId: string) => `${base(researchId)}/${encodeURIComponent(snapshotId)}`

export const graphApi = {
  build: (researchId: string, evidenceId: string) => apiRequest<GraphBuildDto>(base(researchId), {
    method: 'POST', body: JSON.stringify({ evidence_job_id: evidenceId }),
  }),
  status: (researchId: string, snapshotId: string) => apiRequest<GraphStatusDto>(snapshot(researchId, snapshotId)),
  summary: (researchId: string, snapshotId: string) => apiRequest<GraphSummaryDto>(`${snapshot(researchId, snapshotId)}/summary`),
  consistency: (researchId: string, snapshotId: string) => apiRequest<ConsistencyDto>(`${snapshot(researchId, snapshotId)}/consistency`),
  subgraph: (researchId: string, snapshotId: string, rootNodeId: string, depth = 2, maxNodes = 100, direction = 'both') => {
    const params = new URLSearchParams({ root_node_id: rootNodeId, depth: String(depth), max_nodes: String(maxNodes), direction })
    return apiRequest<SubgraphDto>(`${snapshot(researchId, snapshotId)}/subgraph?${params}`)
  },
  node: (researchId: string, snapshotId: string, nodeId: string, includeText = false) =>
    apiRequest<NodeDetailDto>(`${snapshot(researchId, snapshotId)}/nodes/${encodeURIComponent(nodeId)}?include_text=${includeText}`),
  claim: (researchId: string, snapshotId: string, claimId: string) =>
    apiRequest<ClaimViewDto>(`${snapshot(researchId, snapshotId)}/claims/${encodeURIComponent(claimId)}`),
  contradictions: (researchId: string, snapshotId: string) =>
    apiRequest<{ claims: ClaimViewDto[] }>(`${snapshot(researchId, snapshotId)}/contradictions`),
  path: (researchId: string, snapshotId: string, from: string, to: string) => {
    const params = new URLSearchParams({ from_node_id: from, to_node_id: to })
    return apiRequest<PathResponseDto>(`${snapshot(researchId, snapshotId)}/path?${params}`)
  },
}
