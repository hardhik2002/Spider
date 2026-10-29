import type { ClaimStatus, GraphEdgeType, GraphNodeType } from '../../types/graph'

export const nodeNames: Record<GraphNodeType, string> = {
  RESEARCH: 'Research', SUBQUESTION: 'Subquestion', CLAIM: 'Claim', CITATION: 'Citation',
  EVIDENCE_CHUNK: 'Evidence', DOCUMENT: 'Document', SOURCE_GROUP: 'Source group',
}

export const edgeVisuals: Record<GraphEdgeType, { label: string; color: string; dash?: string }> = {
  HAS_SUBQUESTION: { label: 'Has subquestion', color: '#ad3543' },
  HAS_CLAIM: { label: 'Has claim', color: '#f34354' },
  ORIGINATED_FROM: { label: 'Origin', color: '#7b808a', dash: '4 6' },
  SUPPORTED_BY: { label: 'Support', color: '#52b894' },
  PARTIALLY_SUPPORTED_BY: { label: 'Partial support', color: '#d9a55b' },
  CONTRADICTED_BY: { label: 'Contradiction', color: '#f24b55', dash: '5 4' },
  HAS_CITATION: { label: 'Citation', color: '#bd647b' },
  CITES_CHUNK: { label: 'Cites chunk', color: '#a5a8af' },
  PART_OF_DOCUMENT: { label: 'In document', color: '#6f7781' },
  MEMBER_OF_SOURCE_GROUP: { label: 'Source group', color: '#a58bc4' },
}

export const statusVisuals: Record<ClaimStatus, { label: string; className: string }> = {
  CORROBORATED: { label: 'Corroborated', className: 'status-good' },
  SUPPORTED: { label: 'Supported', className: 'status-good' },
  PARTIALLY_SUPPORTED: { label: 'Partial support', className: 'status-warn' },
  CONTESTED: { label: 'Contested', className: 'status-contested' },
  CONTRADICTED: { label: 'Contradicted', className: 'status-danger' },
  INSUFFICIENT_EVIDENCE: { label: 'Insufficient evidence', className: 'status-muted' },
}
